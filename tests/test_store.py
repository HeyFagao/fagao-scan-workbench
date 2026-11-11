# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import concurrent.futures
import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path
from fagao.store import AppError, Store
from fagao.resolver import resolve_reference


def snapshot(platform='fanqie', **overrides):
    return dict(platform=platform, channel='女频', category='古言脑洞', board='新书榜', source_url='https://fanqienovel.com/rank/0_1_253', books=[
        {'id':'a','title':'甲书','author':'作者一','rank':7,'summary':'关于家族与成长的书目简介。'},
        {'id':'b','title':'乙书','author':'作者二','rank':14,'summary':'关于权力与选择的书目简介。'},
        {'id':'c','title':'丙书','author':'作者三','rank':21,'summary':'关于江湖与复仇的书目简介。'},
    ], **overrides)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(self.tmp.name)
        self.s=self.store.save_snapshot(snapshot())
        self.store.set_view({'mode':'long','platform':'fanqie','snapshot_id':self.s['id'],'selected_id':'b'})
    def tearDown(self): self.tmp.cleanup()
    def test_snapshot_is_immutable_after_input_or_output_mutation(self):
        s=copy.deepcopy(self.s); s['books'][0]['title']='改名'
        self.assertEqual(self.store.snapshot(s['id'])['books'][0]['title'],'甲书')
        t=snapshot(); new=self.store.save_snapshot(t); t['books'][0]['title']='外部变更'
        self.assertEqual(self.store.snapshot(new['id'])['books'][0]['title'],'甲书')
        self.assertNotEqual(self.s['id'],new['id'])
    def test_sort_filter_ordinal_and_original_rank_are_separate(self):
        self.store.set_view({'mode':'long','filters':{'sort':'rank_desc','query':''}})
        self.assertEqual(resolve_reference('分析第二本',self.store.context())['book_id'],'b')
        self.store.set_view({'mode':'long','filters':{'sort':'rank_desc','query':'甲'}})
        self.assertEqual(resolve_reference('分析第一本',self.store.context())['book_id'],'a')
        self.assertEqual(resolve_reference('番茄新书榜第14名',self.store.context())['book_id'],'b')
    def test_new_day_snapshot_does_not_change_selection_or_task(self):
        t=self.store.prepare_task({'snapshot_id':self.s['id'],'book_id':'b'},'分析这本')
        incoming=snapshot(); incoming['books'][1]['id']='different'
        self.store.save_snapshot(incoming)
        resolved=resolve_reference('分析这本',self.store.context())
        self.assertEqual((resolved['snapshot_id'],resolved['book_id']),(self.s['id'],'b'))
        self.assertEqual(self.store.task(t['id'])['snapshot_id'],self.s['id'])
    def test_history_survives_restart_without_analysis(self):
        other=Store(self.tmp.name)
        self.assertEqual(other.state()['history'][0]['book_id'],'b')
        self.assertEqual(other.state()['views']['long']['selected_id'],'b')
    def test_state_polling_does_not_decode_unreferenced_book_bodies(self):
        import json
        raw=snapshot(); raw['books'][0]['title']='UNREFERENCED_BODY_SENTINEL'
        extra=self.store.save_snapshot(raw)
        original=json.loads
        def checked(value,*args,**kwargs):
            self.assertNotIn('UNREFERENCED_BODY_SENTINEL',value)
            return original(value,*args,**kwargs)
        with patch('fagao.store.json.loads',side_effect=checked):
            result=self.store.state()
        self.assertIn(extra['id'],[s['id'] for s in result['snapshots']])
        self.assertEqual(result['history'][0]['book']['title'],'乙书')
        self.assertNotIn('books',result['snapshots'][0])
    def test_old_snapshot_metadata_backfills_without_losing_records(self):
        with self.store.connect() as c:
            c.execute('DELETE FROM snapshot_metadata')
        after=Store(self.tmp.name).state()
        self.assertEqual(after['snapshots'][0]['id'],self.s['id'])
        self.assertEqual(after['history'][0]['book_id'],'b')
        self.assertEqual(after['snapshots'][0]['books_count'],3)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM snapshot_metadata').fetchone()[0],1)
    def test_snapshot_revision_changes_only_with_new_ranking_record(self):
        before=self.store.snapshot_revision()
        self.store.toggle_favorite(self.s['id'],'a',True)
        self.assertEqual(self.store.snapshot_revision(),before)
        fresh=self.store.save_snapshot(snapshot())
        self.assertNotEqual(self.store.snapshot_revision(),before)
        self.assertEqual({s['id'] for s in self.store.snapshot_history()},{self.s['id'],fresh['id']})
    def test_duplicate_rank_or_id_rejected(self):
        for field in ('rank','id'):
            s=snapshot(); s['books'][1][field]=s['books'][0][field]
            with self.assertRaises(AppError): self.store.save_snapshot(s)
        self.assertEqual(len(self.store.state()['snapshots']),1)
    def test_null_or_structured_identity_rejected(self):
        for field, invalid in [('id',None),('id',True),('id',{}),('id',[]),('title',None),('title','   ')]:
            value=snapshot(); value['books'][0][field]=invalid
            with self.subTest(field=field,invalid=invalid):
                with self.assertRaises(AppError): self.store.save_snapshot(value)
    def test_remove_favorite_clamps_filtered_page(self):
        for bid in ('a','b','c'): self.store.toggle_favorite(self.s['id'],bid,True)
        self.store.set_view({'mode':'long','filters':{'favorites':True},'page':3,'page_size':1})
        self.store.toggle_favorite(self.s['id'],'c',False)
        view=self.store.state()['views']['long']
        self.assertEqual(view['ordered_ids'],['a','b']); self.assertEqual(view['page'],2)
    def test_analysis_sessions_do_not_cross_reference(self):
        a=self.store.prepare_task({'snapshot_id':self.s['id'],'book_id':'a'},'分析这本','chat-a')
        b=self.store.prepare_task({'snapshot_id':self.s['id'],'book_id':'b'},'分析这本','chat-b')
        self.assertEqual(self.store.context('chat-a')['analysis_target']['book_id'],'a')
        self.assertEqual(self.store.context('chat-b')['analysis_target']['book_id'],'b')
        self.assertIsNone(self.store.context('chat-new')['analysis_target'])
        self.assertIsNone(self.store.context()['analysis_target'])
        self.assertEqual(resolve_reference('它的开头呢',self.store.context('chat-a'))['book_id'],'a')
        self.assertEqual(resolve_reference('它的开头呢',self.store.context('chat-b'))['book_id'],'b')
    def test_resume_after_import_uses_pinned_work(self):
        import json, subprocess, sys
        old=self.store.prepare_task({'snapshot_id':self.s['id'],'book_id':'b'},'分析这本','chat-a')
        self.store.set_view({'mode':'long','selected_id':'a'})
        self.store.import_text(self.s['id'],'b','这是一段恢复任务用的原始正文。'*10,'前两章')
        from cli_fixtures import invoke_store_cli
        result=invoke_store_cli(self.store,['--data-dir',self.tmp.name,'resume',old['id']])
        self.assertEqual(result.returncode,0,result.stdout)
        resumed=json.loads(result.stdout)
        self.assertEqual(resumed['book_id'],'b'); self.assertEqual(resumed['snapshot_id'],self.s['id'])
        self.assertEqual(resumed['scope'],'前两章'); self.assertEqual(resumed['session'],'chat-a')
        self.assertEqual(self.store.task(old['id'])['scope'],'仅简介（未取得正文）')
        self.assertNotEqual(resumed['id'],old['id'])
    def test_heiyan_has_no_rank(self):
        h=self.store.save_snapshot(snapshot('heiyan'))
        self.assertTrue(all(b['rank'] is None for b in h['books']))
    def test_favorites_same_source_id_cross_platform(self):
        q=self.store.save_snapshot(snapshot('qidian'))
        self.store.toggle_favorite(self.s['id'],'b',True)
        self.store.toggle_favorite(q['id'],'b',True)
        self.assertEqual(len(self.store.state()['favorites']),2)
        self.store.toggle_favorite(q['id'],'b',False)
        self.assertEqual(self.store.state()['favorites'][0]['platform'],'fanqie')
    def test_text_same_id_cross_platform_not_reused(self):
        q=self.store.save_snapshot(snapshot('qidian'))
        self.store.import_text(q['id'],'b','这是一段用户提供的正文。'*10,'前一章')
        t=self.store.prepare_task({'snapshot_id':self.s['id'],'book_id':'b'},'分析这本')
        self.assertIsNone(t['text_id'])
        t2=self.store.prepare_task({'snapshot_id':q['id'],'book_id':'b'},'分析这本')
        self.assertIsNotNone(t2['text_id'])
    def test_same_work_text_reused_across_snapshots(self):
        tx=self.store.import_text(self.s['id'],'b','这是一段用户提供的正文。'*10,'前一章')
        n=self.store.save_snapshot(snapshot())
        t=self.store.prepare_task({'snapshot_id':n['id'],'book_id':'b'},'分析这本')
        self.assertEqual(t['text_id'],tx['id'])
    def fetched(self,**overrides):
        content='这是从公开页面取得的第一章实际测试正文。'*5
        actual=len(content)
        return {**dict(platform='fanqie',book_id='b',title='乙书',text=content,
                    scope='第 1 章（共取得 1 章）',complete=False,coverage_complete=True,cancelled=False,
                    source='https://fanqienovel.com/page/b',status='连载中',catalog_chapters=3,catalog_complete=True,requested_start=1,requested_chapters=1,
                    chapters=[{'id':'chapter-b1','item_id':'chapter-b1','index':1,'title':'第一章','url':'https://fanqienovel.com/reader/chapter-b1','characters':actual,
                               'integrity':{'version':1,'status':'verified','declared_word_count':actual,'actual_characters':actual,'reason':'已核对来源字数'}}],
                    integrity={'version':1,'status':'verified','declared_book_word_count':None,'actual_characters':actual,'reason':'所列章节字数核验通过'},
                    missing=[],collected_at='2026-11-09T12:00:00+08:00'),**overrides}

    def test_fetched_text_uses_existing_assets_and_does_not_change_selection(self):
        raw=self.fetched(); meta=self.store.save_fetched_text(self.s['id'],'b',raw)
        self.assertEqual(meta['acquisition'],'public_download')
        self.assertEqual(meta['scope'],raw['scope']); self.assertTrue(meta['coverage_complete']); self.assertFalse(meta['complete'])
        self.assertEqual(meta['chapters'][0]['item_id'],'chapter-b1')
        self.assertEqual(meta['chapters'][0]['url'],raw['chapters'][0]['url'])
        self.assertEqual(meta['chapters'][0]['integrity'],raw['chapters'][0]['integrity'])
        self.assertEqual(meta['integrity'],raw['integrity'])
        self.assertEqual(meta['source'],raw['source'])
        self.assertEqual(meta['collected_at'],raw['collected_at'])
        self.assertEqual(self.store.text_record(meta['id'])['content'],raw['text'])
        self.assertEqual(self.store.state()['texts'][0]['id'],meta['id'])
        self.assertNotIn('content',self.store.state()['texts'][0])
        self.assertNotIn('text',self.store.state()['texts'][0])
        task=self.store.prepare_task({'snapshot_id':self.s['id'],'book_id':'b'},'分析第一章')
        self.assertEqual(task['text_id'],meta['id'])
        self.assertEqual(self.store.state()['views']['long']['selected_id'],'b')
        duplicate=self.store.save_fetched_text(self.s['id'],'b',raw)
        self.assertEqual(duplicate['id'],meta['id'])

    def test_partial_or_cancelled_text_retains_actual_missing_range(self):
        raw=self.fetched(); raw.update(coverage_complete=False,cancelled=True,requested_chapters=3,
            missing=[{'index':2,'id':'chapter-b2','chapter':'第二章','reason':'下载暂停'}])
        meta=self.store.save_fetched_text(self.s['id'],'b',raw)
        self.assertTrue(meta['cancelled']); self.assertFalse(meta['complete']); self.assertFalse(meta['coverage_complete'])
        self.assertEqual(meta['missing'][0]['reason'],'下载暂停')
        self.assertEqual(len(meta['chapters']),1)

    def test_downloaded_complete_book_requires_finished_status_and_whole_catalog(self):
        raw=self.fetched(); raw.update(complete=True,catalog_chapters=1,status='已完结')
        meta=self.store.save_fetched_text(self.s['id'],'b',raw)
        self.assertTrue(meta['complete'])
        self.assertIn('已完结',meta['completeness_basis'])
        for changed in ({'status':'连载中'},{'catalog_chapters':2},{'coverage_complete':False},{'status':False}):
            wrong={**raw,**changed}
            with self.subTest(changed=changed):
                with self.assertRaises(AppError): self.store.save_fetched_text(self.s['id'],'b',wrong)

    def test_download_identity_empty_error_and_range_mismatches_are_rejected(self):
        bad=[{'book_id':'a'},{'platform':'qidian'},{'text':''},{'text':'403 Forbidden'+'wrong'*20},
             {'chapters':[]},{'catalog_chapters':0},{'requested_chapters':False},
             {'missing':[{'index':1,'id':'chapter-b1','reason':'not actually missing'}]},
             {'missing':[{'index':2,'id':'chapter-b2','reason':'locked'}]},
             {'chapters':[{'id':'x','item_id':'y','index':1,'characters':100}]},
             {'chapters':[{'id':'x','item_id':'x','index':1,'characters':0}]}]
        for changed in bad:
            with self.subTest(changed=changed):
                with self.assertRaises(AppError): self.store.save_fetched_text(self.s['id'],'b',{**self.fetched(),**changed})
        self.assertEqual(self.store.state()['texts'],[])

    def test_download_integrity_rejects_contradictory_counts_and_claims(self):
        base=self.fetched(complete=True,catalog_chapters=1,status='已完结')
        variants=[]
        for key,value in [('version',True),('status','partial'),('actual_characters',100000),('declared_book_word_count',13325)]:
            raw=copy.deepcopy(base); raw['integrity'][key]=value; variants.append(raw)
        for key,value in [('version',True),('declared_word_count',13325),('declared_word_count',None),('actual_characters',1),('status','unverified')]:
            raw=copy.deepcopy(base); raw['chapters'][0]['integrity'][key]=value; variants.append(raw)
        raw=copy.deepcopy(base); del raw['chapters'][0]['integrity']; variants.append(raw)
        raw=copy.deepcopy(base); del raw['integrity']; variants.append(raw)
        for raw in variants:
            with self.subTest(raw=raw):
                with self.assertRaises(AppError) as raised: self.store.save_fetched_text(self.s['id'],'b',raw)
                self.assertEqual(raised.exception.code,'invalid_text_integrity')
        self.assertEqual(self.store.state()['texts'],[])

    def test_public_fragment_retains_evidence_and_cannot_be_labelled_complete(self):
        raw=self.fetched(catalog_chapters=1,status='已完结',scope='全书完整',coverage_complete=False)
        raw['chapters'][0]['integrity'].update(status='partial',declared_word_count=13325,reason='来源字数大于已取得正文')
        raw['integrity'].update(status='partial',declared_book_word_count=13325,reason='来源字数大于已取得正文')
        meta=self.store.save_fetched_text(self.s['id'],'b',raw)
        self.assertFalse(meta['complete']); self.assertFalse(meta['coverage_complete'])
        self.assertEqual(meta['integrity'],raw['integrity'])
        self.assertEqual(meta['chapters'][0]['integrity'],raw['chapters'][0]['integrity'])
        self.assertEqual(self.store.text_record(meta['id'])['content'],raw['text'])
        self.assertIn('公开片段',meta['scope']); self.assertNotIn('全书完整',meta['scope'])
        for claims in ({'coverage_complete':True},{'coverage_complete':True,'complete':True}):
            with self.subTest(claims=claims):
                with self.assertRaises(AppError): self.store.save_fetched_text(self.s['id'],'b',{**raw,**claims})

    def test_unknown_chapter_word_count_is_saved_only_as_unverified(self):
        raw=self.fetched(coverage_complete=False)
        raw['chapters'][0]['integrity'].update(status='unverified',declared_word_count=None,reason='来源未提供章节字数')
        raw['integrity'].update(status='unverified',reason='来源未提供章节字数')
        meta=self.store.save_fetched_text(self.s['id'],'b',raw)
        self.assertIn('完整性未确认',meta['scope'])
        self.assertFalse(meta['coverage_complete'])
        with self.assertRaises(AppError): self.store.save_fetched_text(self.s['id'],'b',{**raw,'coverage_complete':True})

    def test_whole_book_total_can_reject_full_claim_when_each_chapter_passes(self):
        raw=self.fetched(catalog_chapters=1,status='已完结',coverage_complete=False)
        raw['integrity'].update(status='partial',declared_book_word_count=13325,reason='全书字数未取得完整')
        meta=self.store.save_fetched_text(self.s['id'],'b',raw)
        self.assertEqual(meta['chapters'][0]['integrity']['status'],'verified')
        self.assertEqual(meta['integrity']['status'],'partial')
        with self.assertRaises(AppError): self.store.save_fetched_text(self.s['id'],'b',{**raw,'complete':True,'coverage_complete':True})

    def test_legacy_download_repair_preserves_content_and_corrects_tasks_and_reports(self):
        raw=self.fetched(complete=True,catalog_chapters=1,status='已完结',scope='全书完整')
        downloaded=self.store.save_fetched_text(self.s['id'],'b',raw)
        task=self.store.prepare_task({'snapshot_id':self.s['id'],'book_id':'b'},'完整分析','legacy-session')
        report=self.store.save_report(task['id'],'原报告分析正文及当时的完整性结论。'*10)
        original_report=self.store.report(report['id'])['content']
        imported=self.store.import_text(self.s['id'],'a','用户确认的完整正文。'*10,'用户提供完整正文',True)
        legacy=copy.deepcopy(downloaded)
        del legacy['integrity']
        del legacy['chapters'][0]['integrity']
        job={'id':'legacy-text-job','kind':'text','status':'completed','created_at':legacy['created_at'],
             'text_id':legacy['id'],'text':legacy,'partial':False,'message':'全书完整，已保存正文',
             'download_url':f"/api/texts/{legacy['id']}/download",'snapshot_id':self.s['id'],'book_id':'b'}
        self.store.save_job(job)
        with self.store.connect() as c:
            c.execute('UPDATE texts SET body=? WHERE id=?',(json.dumps(legacy),downloaded['id']))
            c.execute("DELETE FROM kv WHERE key='download_integrity_v1'")
        upgraded=Store(self.tmp.name)
        saved=upgraded.text_record(downloaded['id'])
        self.assertEqual(saved['content'],raw['text']); self.assertEqual(saved['source'],raw['source'])
        self.assertEqual(saved['sha256'],downloaded['sha256']); self.assertEqual(saved['chapters'],legacy['chapters'])
        self.assertEqual(saved['integrity']['status'],'unverified')
        self.assertFalse(saved['complete']); self.assertFalse(saved['coverage_complete'])
        self.assertEqual(saved['previous_scope'],'全书完整')
        self.assertEqual(saved['integrity']['actual_characters'],sum(not char.isspace() for char in raw['text']))
        self.assertEqual(upgraded.task(task['id'])['scope'],saved['scope'])
        self.assertFalse(upgraded.task(task['id'])['text']['complete'])
        repaired_job=upgraded.job(job['id'])
        self.assertEqual(repaired_job['status'],'completed'); self.assertTrue(repaired_job['partial'])
        self.assertEqual(repaired_job['text_id'],saved['id']); self.assertFalse(repaired_job['text']['complete'])
        self.assertEqual(repaired_job['text']['scope'],saved['scope'])
        self.assertIn(saved['scope'],repaired_job['message']); self.assertNotIn('全书完整',repaired_job['message'])
        self.assertEqual(repaired_job['download_url'],job['download_url'])
        self.assertEqual(repaired_job['snapshot_id'],job['snapshot_id']); self.assertEqual(repaired_job['book_id'],'b')
        self.assertEqual(upgraded.state()['jobs'][0],repaired_job)
        new_task=upgraded.prepare_task({'snapshot_id':self.s['id'],'book_id':'b'},'继续')
        self.assertEqual(new_task['text_id'],saved['id']); self.assertEqual(new_task['scope'],saved['scope'])
        corrected=upgraded.report(report['id'])
        self.assertEqual(corrected['scope'],saved['scope']); self.assertTrue(corrected['scope_corrected'])
        self.assertTrue(corrected['content'].endswith(original_report))
        self.assertTrue(corrected['content'].startswith('## 阅读范围更正'))
        self.assertEqual(Store(self.tmp.name).report(report['id'])['content'],corrected['content'])
        self.assertTrue(upgraded.text_record(imported['id'])['complete'])
        self.assertEqual(upgraded.text_record(imported['id'])['scope'],'用户提供完整正文')
        context=upgraded.context('legacy-session')
        self.assertEqual(context['analysis_target']['task_id'],task['id'])
        self.assertFalse(next(tx for tx in context['texts'] if tx['id']==saved['id'])['complete'])
        import subprocess,sys
        from cli_fixtures import invoke_store_cli
        result=invoke_store_cli(upgraded,['--data-dir',self.tmp.name,'resume',task['id']])
        self.assertEqual(result.returncode,0,result.stdout)
        resumed=json.loads(result.stdout)
        self.assertEqual(resumed['text_id'],saved['id']); self.assertEqual(resumed['scope'],saved['scope'])
        self.assertFalse(resumed['text']['complete']); self.assertFalse(resumed['text']['coverage_complete'])
        # A stale task copy written after migration is refreshed from its same
        # text asset before the next report is persisted.
        task['text']=legacy; task['scope']='全书完整'
        with upgraded.connect() as c:
            c.execute('UPDATE tasks SET body=? WHERE id=?',(json.dumps(task),task['id']))
        next_report=upgraded.save_report(task['id'],'按实际取得的片段分析人物冲突。'*10)
        self.assertEqual(next_report['scope'],saved['scope'])
        self.assertEqual(next_report['integrity']['status'],'unverified')

    def test_text_error_pages_and_empty_rejected(self):
        for body in ('','tiny','<html><body>'+'error'*20,'403 Forbidden'+'error'*20,'<!doctype html>'+'error'*20):
            with self.assertRaises(AppError): self.store.import_text(self.s['id'],'b',body,'全文',True)
    def test_report_pins_reading_scope_even_after_text_changed(self):
        tx=self.store.import_text(self.s['id'],'b','这是一段用户提供的正文。'*10,'前三章')
        t=self.store.prepare_task({'snapshot_id':self.s['id'],'book_id':'b'},'分析这本')
        self.store.import_text(self.s['id'],'b','这是一段较新的用户完整正文。'*10,'完整正文',True)
        r=self.store.save_report(t['id'],'人物的首次决定改变了矛盾的方向。'*10)
        saved=self.store.report(r['id'])
        self.assertEqual(saved['text_id'],tx['id']); self.assertEqual(saved['scope'],'前三章')
        self.assertIn('阅读范围：前三章',saved['content'])
    def test_report_versions_keep_previous_result(self):
        t=self.store.prepare_task({'snapshot_id':self.s['id'],'book_id':'b'},'分析这本')
        one=self.store.save_report(t['id'],'第一个有效分析。'*10)
        two=self.store.save_report(t['id'],'第二个有效分析。'*10)
        self.assertNotEqual(one['id'],two['id'])
        self.assertIn('第一个有效分析',self.store.report(one['id'])['content'])
    def test_many_concurrent_inserts_are_complete(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as p:
            ids=list(p.map(lambda _:self.store.save_snapshot(snapshot())['id'],range(24)))
        self.assertEqual(len(set(ids)),24)
        self.assertEqual(len(self.store.state()['snapshots']),25)
    def test_recover_interrupted_jobs_preserves_finished(self):
        for name,status in [('one','running'),('two','completed')]:
            self.store.save_job({'id':name,'created_at':'2026-11-09T00:00:00Z','status':status})
        self.store.recover_jobs()
        self.assertEqual(self.store.job('one')['status'],'interrupted')
        self.assertEqual(self.store.job('two')['status'],'completed')
    def test_public_cover_url_and_pagination_are_preserved(self):
        value=snapshot()
        value['books'][0]['cover_url']='https://cdn.example.com/books/a.jpg?size=240'
        value['pagination']={'pages_fetched':8,'max_pages':100,'stop_reason':'source_end','source_total':77,'untrusted':'ignored'}
        saved=self.store.save_snapshot(value)
        self.assertEqual(saved['books'][0]['cover_url'],value['books'][0]['cover_url'])
        self.assertEqual(saved['pagination'],{'pages_fetched':8,'max_pages':100,'stop_reason':'source_end','source_total':77})
        self.assertEqual(self.store.snapshot(saved['id'])['pagination'],saved['pagination'])
        metadata=next(s for s in self.store.state()['snapshots'] if s['id']==saved['id'])
        self.assertEqual(metadata['pagination']['pages_fetched'],8)
        # Inserting newer metadata must not move the selected snapshot or book.
        self.assertEqual(self.store.state()['views']['long']['snapshot_id'],self.s['id'])
        self.assertEqual(self.store.state()['views']['long']['selected_id'],'b')

    def test_official_categories_keep_order_with_bounded_clean_strings(self):
        value=snapshot()
        value['categories']=[' 古言脑洞 ','现代言情','古言脑洞',None,42,{},'', '分'*100]+[f'分类{i}' for i in range(150)]
        saved=self.store.save_snapshot(value)
        self.assertEqual(saved['categories'][:3],['古言脑洞','现代言情','分'*80])
        self.assertEqual(len(saved['categories']),100)
        self.assertTrue(all(isinstance(category,str) and 0<len(category)<=80 for category in saved['categories']))
        self.assertEqual(self.store.snapshot(saved['id'])['categories'],saved['categories'])
        metadata=next(s for s in self.store.state()['snapshots'] if s['id']==saved['id'])
        self.assertNotIn('categories',metadata)
        context=next(s for s in self.store.context()['snapshots'] if s['id']==saved['id'])
        self.assertEqual(context['categories'],saved['categories'])

    def test_book_publication_status_is_preserved_and_bounded(self):
        value=snapshot()
        value['books'][0]['status']='连载中'
        value['books'][1]['status']='已完结'
        value['books'][2]['status']='状态说明'*50
        saved=self.store.save_snapshot(value)
        stored=self.store.snapshot(saved['id'])['books']
        self.assertEqual(stored[0]['status'],'连载中')
        self.assertEqual(stored[1]['status'],'已完结')
        self.assertEqual(len(stored[2]['status']),60)
        value['books'][0]['status']={'untrusted':'not a status string'}
        self.assertEqual(self.store.save_snapshot(value)['books'][0]['status'],'')

    def test_unsafe_cover_urls_are_removed_without_discarding_books(self):
        urls=['javascript:alert(1)','data:image/svg+xml;base64,abc','file:///a.png',
              '//cdn.example.com/a.jpg','https://user:secret@cdn.example.com/a.jpg',
              'https://localhost/a.jpg','https://demo.localhost/a.jpg','http://127.0.0.1/a.jpg',
              'http://10.2.3.4/a.jpg','http://192.168.1.1/a.jpg','http://172.16.3.4/a.jpg',
              'http://169.254.169.254/a.jpg','http://[::1]/a.jpg','http://[fc00::1]/a.jpg',
              'http://[::ffff:127.0.0.1]/a.jpg','http://2130706433/a.jpg','http://0x7f000001/a.jpg',
              'http://0x7f.0.0.1/a.jpg','http://0177.0.0.1/a.jpg','https://router.local/a.jpg',
              'http://singlehost/a.jpg','https://cdn.example.com:999999/a.jpg',
              'https://cdn.example.com\\@localhost/a.jpg','https://cdn.example.com/\nimage.jpg']
        for url in urls:
            with self.subTest(url=url):
                value=snapshot(); value['books'][0]['cover_url']=url
                saved=self.store.save_snapshot(value)
                self.assertEqual(saved['books'][0]['cover_url'],'')
                self.assertEqual(len(saved['books']),3)

    def test_invalid_pagination_is_rejected_transactionally(self):
        for pagination in ([],{'pages_fetched':-1},{'max_pages':True},{'source_total':'100'}):
            with self.subTest(pagination=pagination):
                value=snapshot(); value['pagination']=pagination
                with self.assertRaises(AppError): self.store.save_snapshot(value)
        self.assertEqual(len(self.store.state()['snapshots']),1)

    def test_jobs_are_read_by_exact_collection_scope(self):
        for jid,scope in [('a','scope-one'),('b','scope-two'),('c','scope-one')]:
            self.store.save_job({'id':jid,'scope_key':scope,'created_at':f'2026-11-09T00:00:0{ord(jid)-97}Z','status':'failed'})
        self.assertEqual([j['id'] for j in self.store.scope_jobs('scope-one')],['c','a'])
        self.assertEqual(self.store.scope_jobs('unknown'),[])

    def test_non_http_links_removed(self):
        s=snapshot(); s['books'][0]['url']='javascript:alert(1)'
        self.assertEqual(self.store.save_snapshot(s)['books'][0]['url'],'')
        with self.assertRaises(AppError): self.store.update_config({'points_site':'javascript:alert(1)'})

if __name__=='__main__': unittest.main()
