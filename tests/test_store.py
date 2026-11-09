import concurrent.futures
import copy
import tempfile
import unittest
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
        result=subprocess.run([sys.executable,'-m','fagao','--data-dir',self.tmp.name,'resume',old['id']],capture_output=True,text=True,check=True)
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
    def test_non_http_links_removed(self):
        s=snapshot(); s['books'][0]['url']='javascript:alert(1)'
        self.assertEqual(self.store.save_snapshot(s)['books'][0]['url'],'')
        with self.assertRaises(AppError): self.store.update_config({'points_site':'javascript:alert(1)'})

if __name__=='__main__': unittest.main()
