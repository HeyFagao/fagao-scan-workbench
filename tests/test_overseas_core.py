"""Overseas selection, original language and deterministic WorkBuddy handoff."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from fagao.resolver import ResolutionError, resolve_reference
from fagao.store import AppError, Store


def source(platform='goodnovel', *, board='分类榜', rank=None):
    return {'platform':platform,'channel':'全部','category':'狼人' if platform=='goodnovel' else '全部',
            'board':board,'source_url':'https://www.goodnovel.com/rankings','books':[
                {'id':'a','title':"The Alpha’s Promise — A New Beginning",'author':'Émilie',
                 'summary':"She said, ‘I choose my own fate.’\nA promise becomes a choice.",
                 'tags':['狼人','爱情'],'source_tags':['Werewolf','Romance'],'rank':rank,
                 'url':'https://www.goodnovel.com/book/a','cover_url':'https://img.goodnovel.com/cover/a.jpg'},
                {'id':'b','title':'A Different Book','summary':'A second original English synopsis.',
                 'rank':None if rank is None else rank+1}]}


class OverseasCoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(self.tmp.name)
        self.snap=self.store.save_snapshot(source())
        self.store.set_view({'mode':'overseas','platform':'goodnovel','snapshot_id':self.snap['id'],'selected_id':'a'})

    def tearDown(self): self.tmp.cleanup()

    def test_default_view_and_originals_survive_restart(self):
        other=Store(self.tmp.name)
        state=other.state()
        self.assertEqual(state['active_mode'],'overseas')
        self.assertEqual(state['views']['overseas']['selected_id'],'a')
        self.assertEqual(state['history'][0]['mode'],'overseas')
        saved=other.snapshot(self.snap['id'])
        self.assertEqual(saved['books'][0]['title'],source()['books'][0]['title'])
        self.assertEqual(saved['books'][0]['summary'],source()['books'][0]['summary'])
        self.assertEqual(saved['books'][0]['source_tags'],['Werewolf','Romance'])
        self.assertEqual(saved['language'],'en')
        self.assertEqual(saved['content_type'],'long')
        self.assertEqual({p['id'] for p in state['platforms'] if p['mode']=='overseas'},
                         {'goodnovel','goodnovel_short','meganovel','dreame','webnovel','royalroad','scribblehub'})

    def test_favorites_update_overseas_filtered_view(self):
        self.store.toggle_favorite(self.snap['id'],'a',True)
        self.store.set_view({'mode':'overseas','filters':{'favorites':True}})
        self.assertEqual(self.store.state()['views']['overseas']['ordered_ids'],['a'])
        self.assertEqual(self.store.state()['favorites'][0]['mode'],'overseas')
        self.store.toggle_favorite(self.snap['id'],'a',False)
        self.assertEqual(self.store.state()['views']['overseas']['ordered_ids'],[])

    def test_overseas_explicit_reference_and_case_insensitive_platform(self):
        for query in ('翻译海外网文第二本','分析 GOODNOVEL 第二本','分析海外长篇第二本'):
            with self.subTest(query=query):
                self.assertEqual(resolve_reference(query,self.store.context())['book_id'],'b')
        result=resolve_reference('翻译这本',self.store.context())
        self.assertIn('海外网文',result['scope_description'])
        with self.assertRaises(ResolutionError) as caught:
            resolve_reference('GoodNovel 第1名',self.store.context())
        self.assertEqual(caught.exception.code,'rank_not_found')

    def test_english_title_words_are_not_scope_instructions(self):
        raw=source(); raw['books'][0]['title']='The Long Road of a New Girl'
        snap=self.store.save_snapshot(raw)
        self.store.set_view({'mode':'overseas','snapshot_id':snap['id']})
        result=resolve_reference('翻译 GoodNovel 《The Long Road of a New Girl》',self.store.context())
        self.assertEqual((result['snapshot_id'],result['book_id']),(snap['id'],'a'))

    def test_short_and_long_overseas_selection_do_not_mix(self):
        short=self.store.save_snapshot(source('goodnovel_short',board='热门短篇'))
        self.store.set_view({'mode':'overseas','platform':'goodnovel_short','snapshot_id':short['id'],'selected_id':'a'})
        for query in ('翻译 GoodNovel 短篇第二本','海外短篇第二本','GoodNovel短篇第二本'):
            with self.subTest(query=query):
                result=resolve_reference(query,self.store.context())
                self.assertEqual((result['snapshot_id'],result['book_id']),(short['id'],'b'))
        self.assertEqual(short['content_type'],'short')
        with self.assertRaises(ResolutionError):
            resolve_reference('GoodNovel 短篇长篇第二本',self.store.context())

    def test_platform_aliases_and_original_rank(self):
        for ident,label in [('dreame','DREAME'),('webnovel','Webnovel'),('royalroad','Royal Road'),('scribblehub','Scribble Hub'),('meganovel','MegaNovel')]:
            with self.subTest(platform=ident):
                snap=self.store.save_snapshot(source(ident,board='新书榜',rank=14))
                self.store.set_view({'mode':'overseas','platform':ident,'snapshot_id':snap['id']})
                result=resolve_reference(label+' 新书榜第14名',self.store.context())
                self.assertEqual((result['snapshot_id'],result['book_id']),(snap['id'],'a'))

    def test_pinned_translation_survives_selection_and_snapshot_changes(self):
        translated=self.store.translation_context('翻译这本','chat-a')
        self.store.save_snapshot(source())
        self.store.set_view({'mode':'overseas','selected_id':'b'})
        followup=self.store.translation_context('继续翻译它','chat-a')
        self.assertEqual((followup['snapshot_id'],followup['book_id']),(self.snap['id'],'a'))
        self.assertEqual(translated['original']['summary'],source()['books'][0]['summary'])
        explicit=self.store.translation_context('翻译这本','chat-a')
        self.assertEqual(explicit['book_id'],'b')
        pinned=self.store.translation_context(session='chat-a',snapshot_id=self.snap['id'],book_id='a')
        self.assertEqual(pinned['book_id'],'a')
        self.assertIn(self.snap['id'],pinned['prompt'])
        self.assertIn('translation-context',pinned['prompt'])
        self.assertEqual(pinned['scope'],'书名与简介')
        self.assertEqual(self.store.snapshot(self.snap['id'])['books'][0]['title'],source()['books'][0]['title'])

    def test_translation_sessions_and_analysis_are_independent(self):
        task=self.store.prepare_task({'snapshot_id':self.snap['id'],'book_id':'b'},'分析这本','chat-a')
        self.store.translation_context('翻译这本','chat-a')
        self.store.translation_context(session='chat-b',snapshot_id=self.snap['id'],book_id='b')
        self.assertEqual(self.store.translation_context('继续翻译它','chat-a')['book_id'],'a')
        self.assertEqual(self.store.translation_context('继续翻译它','chat-b')['book_id'],'b')
        self.assertEqual(self.store.context('chat-a')['analysis_target']['task_id'],task['id'])

    def test_text_metadata_is_platform_scoped_and_never_embedded(self):
        text=self.store.import_text(self.snap['id'],'a','Original English chapter. '*5,'Chapter 1')
        other=self.store.save_snapshot(source('webnovel'))
        self.store.import_text(other['id'],'a','Different platform content. '*5,'Chapter 9')
        handoff=self.store.translation_context()
        self.assertEqual([x['id'] for x in handoff['available_texts']],[text['id']])
        self.assertNotIn('content',handoff['available_texts'][0])
        self.assertNotIn('Original English chapter',json.dumps(handoff))
        self.assertEqual(handoff['original']['tags'],['Werewolf','Romance'])

    def test_cli_explicit_pair_and_incomplete_pair(self):
        from cli_fixtures import invoke_store_cli
        command=['--data-dir',self.tmp.name,'--session','cli-test','translation-context','--snapshot',self.snap['id'],'--book','a']
        result=invoke_store_cli(self.store,command)
        self.assertEqual(result.returncode,0,result.stdout)
        self.assertEqual(json.loads(result.stdout)['book_id'],'a')
        bad=invoke_store_cli(self.store,command[:-2])
        self.assertEqual(bad.returncode,1)
        self.assertEqual(json.loads(bad.stdout)['error']['code'],'incomplete_selection')

    def test_domestic_view_preserved_after_overseas_use(self):
        raw=source('fanqie',board='新书榜',rank=14)
        raw.update(category='古言脑洞',channel='女频')
        domestic=self.store.save_snapshot(raw)
        self.store.set_view({'mode':'long','platform':'fanqie','snapshot_id':domestic['id'],'selected_id':'b'})
        self.assertEqual(resolve_reference('番茄新书榜第14名',self.store.context())['book_id'],'a')
        self.assertEqual(self.store.state()['views']['overseas']['selected_id'],'a')
        self.store.set_view({'mode':'overseas'})
        self.assertEqual(resolve_reference('翻译这本',self.store.context())['snapshot_id'],self.snap['id'])
        self.assertEqual(resolve_reference('长篇第二本',self.store.context())['snapshot_id'],domestic['id'])


if __name__=='__main__': unittest.main()
