# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import concurrent.futures
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fagao.server import JobManager, OVERVIEW_SCOPE
from fagao.store import AppError, Store


def source(platform, options, count=10, complete=True):
    categories=['玄幻脑洞','都市脑洞'] if options['channel']=='男频' else ['古言脑洞','宫斗宅斗']
    return {'platform':platform,'mode':'long','channel':options['channel'],'category':options['category'],
            'board':options['board'],'period':options.get('period',''),'categories':categories if platform=='fanqie' else [],
            'data_date':'2026-11-09T00:00:00Z','complete':complete,
            'books':[{'id':f'{platform}-{options["channel"]}-{i}','title':f'测试作品{i}','rank':i+1} for i in range(count)]}


class OverviewJobsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(self.tmp.name); self.manager=JobManager(self.store)
        self.public_patch=patch('fagao.public_rankings.sync_public_rankings',return_value={'snapshots':[]})
        self.public_fetch=self.public_patch.start(); self.addCleanup(self.public_patch.stop)
        self.options={'channel':'女频','category':'古言脑洞','board':'新书榜','max_pages':100,'limit':10000}

    def tearDown(self):
        for jid in list(self.manager.active.values()): self.manager.cancel(jid)
        deadline=time.monotonic()+3
        while self.manager.active and time.monotonic()<deadline: time.sleep(.01)
        self.tmp.cleanup()

    def wait(self,jid,predicate=lambda job:job['status'] in ('completed','failed','cancelled')):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            job=self.store.job(jid)
            if predicate(job): return job
            time.sleep(.01)
        self.fail(f'job did not reach expected state: {job}')

    def test_first_batch_is_saved_before_later_network_and_scroll_resumes(self):
        later=threading.Event()
        def collector(platform,options,directory,progress):
            progress('首批已读',snapshot=source(platform,options),has_more=True)
            later.set()
            return source(platform,options,20)
        before=self.store.state()['views']
        with patch('fagao.collectors.collect',side_effect=collector):
            reply=self.manager.start('fanqie',self.options,interactive=True)
            partial=self.wait(reply['job_id'],lambda job:job.get('waiting_for_more'))
            self.assertFalse(later.is_set())
            saved=self.store.snapshot(partial['partial_snapshot_id'])
            self.assertEqual(saved['books_count'],10); self.assertFalse(saved['complete'])
            self.assertEqual(self.store.state()['views'],before)
            resumed=self.manager.more(reply['job_id'],20)
            self.assertEqual(resumed['status'],'continued')
            final=self.wait(reply['job_id'])
        self.assertTrue(later.is_set()); self.assertEqual(final['status'],'completed')
        self.assertNotEqual(final['snapshot_id'],saved['id'])
        self.assertEqual(self.store.snapshot(saved['id'])['books_count'],10)
        self.assertEqual(self.store.snapshot(final['snapshot_id'])['books_count'],20)

    def test_background_overview_can_promote_paused_interactive_job(self):
        def collector(platform,options,directory,progress):
            progress('首批',snapshot=source(platform,options),has_more=True)
            return source(platform,options,20)
        with patch('fagao.collectors.collect',side_effect=collector) as fetch:
            first=self.manager.start('fanqie',self.options,interactive=True)
            self.wait(first['job_id'],lambda job:job.get('waiting_for_more'))
            second=self.manager.start('fanqie',self.options,True)
            self.assertEqual(second['status'],'busy'); self.assertEqual(first['job_id'],second['job_id'])
            final=self.wait(first['job_id'])
        self.assertFalse(final['interactive']); self.assertFalse(final['waiting_for_more'])
        self.assertEqual(fetch.call_count,1)

    def test_idle_gate_stops_and_more_restarts_without_losing_preview(self):
        def collector(platform,options,directory,progress):
            progress('首批',snapshot=source(platform,options),has_more=True)
            return source(platform,options,20)
        with patch('fagao.collectors.collect',side_effect=collector), patch('fagao.server.INTERACTIVE_IDLE_SECONDS',.05):
            first=self.manager.start('fanqie',self.options,interactive=True)
            paused=self.wait(first['job_id'])
            self.assertTrue(paused['paused_for_demand']); self.assertTrue(paused['partial'])
            self.assertFalse(self.store.snapshot(paused['snapshot_id'])['complete'])
            next_reply=self.manager.more(first['job_id'],20)
            self.assertEqual(next_reply['status'],'started'); self.assertNotEqual(first['job_id'],next_reply['job_id'])
            final=self.wait(next_reply['job_id'])
        self.assertEqual(self.store.snapshot(final['snapshot_id'])['books_count'],20)
        self.assertEqual(self.store.snapshot(paused['snapshot_id'])['books_count'],10)

    def test_cancel_unblocks_gate_and_keeps_first_batch(self):
        later=threading.Event()
        def collector(platform,options,directory,progress):
            progress('首批',snapshot=source(platform,options),has_more=True)
            later.set(); return source(platform,options,20)
        with patch('fagao.collectors.collect',side_effect=collector):
            started=self.manager.start('fanqie',self.options,interactive=True)
            job=self.wait(started['job_id'],lambda job:job.get('waiting_for_more'))
            self.manager.cancel(started['job_id'])
            deadline=time.monotonic()+1
            while self.manager.active and time.monotonic()<deadline: time.sleep(.01)
        self.assertFalse(self.manager.active); self.assertFalse(later.is_set())
        self.assertEqual(self.store.snapshot(job['partial_snapshot_id'])['books_count'],10)

    def test_bad_partial_scope_fails_without_saving_wrong_book(self):
        def collector(platform,options,directory,progress):
            raw=source(platform,options); raw['board']='阅读榜'
            progress('混合数据',snapshot=raw,has_more=True)
        with patch('fagao.collectors.collect',side_effect=collector):
            final=self.wait(self.manager.start('fanqie',self.options,interactive=True)['job_id'])
        self.assertEqual(final['status'],'failed'); self.assertEqual(final['error_code'],'partial_scope_mismatch')
        self.assertEqual(self.store.state()['snapshots'],[])

    def test_partial_period_cannot_mix_daily_and_monthly_cat_rank(self):
        def collector(platform,options,directory,progress):
            raw=source(platform,options); raw['period']='日榜'
            progress('周期不一致',snapshot=raw,has_more=True)
        with patch('fagao.collectors.collect',side_effect=collector):
            final=self.wait(self.manager.start('ciweimao',{'board':'月票榜','period':'月榜'},interactive=True)['job_id'])
        self.assertEqual(final['error_code'],'partial_scope_mismatch')
        self.assertEqual(self.store.state()['snapshots'],[])

    def test_overview_seeds_all_platforms_then_only_new_fanqie_categories(self):
        calls=[]
        def collector(platform,options,*args):
            calls.append((platform,options['channel'],options['board'],options['category'],options['max_pages']))
            return source(platform,options)
        before=self.store.state()['views']
        with patch('fagao.collectors.collect',side_effect=collector):
            final=self.wait(self.manager.start_overview(True)['job_id'])
        self.assertEqual([call[0] for call in calls[:4]],['fanqie','fanqie','qidian','ciweimao'])
        self.assertTrue(all(call[4]==1 for call in calls[:4]))
        self.assertTrue(all(call[2]=='新书榜' for call in calls if call[0]=='fanqie'))
        self.assertEqual(final['result']['snapshots_count'],6)
        self.assertEqual(final['progress']['completed_categories'],6)
        self.assertEqual(final['progress']['total_categories'],6)
        self.assertEqual(final['status'],'completed'); self.assertFalse(final['partial'])
        self.assertEqual(self.store.state()['views'],before)
        self.assertEqual(self.manager.start_overview(True)['status'],'fresh')

    def test_partial_overview_retries_only_failed_scopes_after_cooldown(self):
        calls=[]
        def collector(platform,options,*args):
            calls.append((platform,options['category']))
            if options['category']=='都市脑洞': raise AppError('network_failed','一个分类暂时失败')
            return source(platform,options)
        with patch('fagao.collectors.collect',side_effect=collector):
            final=self.wait(self.manager.start_overview(True)['job_id'])
            self.assertEqual(self.manager.start_overview(True)['status'],'cooldown')
            self.assertTrue(final['partial']); self.assertEqual(final['status'],'completed')
            self.assertEqual(final['progress']['failed_categories'],1)
            self.assertEqual(final['result']['failed_categories'][0]['category'],'都市脑洞')
            # Expire only failure cooldown; successful exact scopes remain fresh.
            with patch('fagao.server.AUTO_FAILURE_COOLDOWN_SECONDS',0):
                second=self.wait(self.manager.start_overview(True)['job_id'])
        self.assertEqual(second['progress']['skipped_categories'],5)
        self.assertEqual(len(calls),11)  # four previews + six full scopes + one retry

    def test_concurrent_overview_requests_share_one_parent_job(self):
        release=threading.Event(); entered=threading.Event()
        def collector(platform,options,*args):
            entered.set(); release.wait(2); return source(platform,options)
        with patch('fagao.collectors.collect',side_effect=collector):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                replies=list(pool.map(lambda _:self.manager.start_overview(True),range(8)))
            self.assertTrue(entered.wait(1)); self.assertEqual(len({r['job_id'] for r in replies}),1)
            self.assertEqual(sum(r['status']=='started' for r in replies),1)
            release.set(); self.wait(replies[0]['job_id'])

    def test_cancelling_overview_does_not_cancel_a_shared_browse_job(self):
        release=threading.Event()
        def collector(platform,options,*args):
            release.wait(2); return source(platform,options)
        with patch('fagao.collectors.collect',side_effect=collector):
            parent=self.manager.start_overview(True)
            job=self.wait(parent['job_id'],lambda value:bool(value.get('active_child_id')))
            child=self.store.job(job['active_child_id'])
            joined=self.manager.start(child['platform'],child['options'],True,True)
            self.assertEqual(joined['job_id'],child['id']); self.assertTrue(joined['job']['shared'])
            self.manager.cancel(parent['job_id'])
            self.assertNotEqual(self.store.job(child['id'])['status'],'cancelled')
            release.set(); self.assertEqual(self.wait(child['id'])['status'],'completed')

    def test_cancel_between_child_start_and_registration_never_resurrects_parent(self):
        release=threading.Event(); started=[]
        original=self.manager.start
        def start_and_cancel(*args,**kwargs):
            reply=original(*args,**kwargs); started.append(reply['job_id'])
            parent=self.store.scope_jobs(OVERVIEW_SCOPE)[0]
            self.manager.cancel(parent['id'])
            return reply
        def collector(platform,options,*args):
            release.wait(2); return source(platform,options)
        with patch('fagao.collectors.collect',side_effect=collector), patch.object(self.manager,'start',side_effect=start_and_cancel):
            parent=self.manager.start_overview(True)
            self.wait(parent['job_id'],lambda value:value['status']=='cancelled')
            release.set()
            deadline=time.monotonic()+2
            while self.manager.active and time.monotonic()<deadline: time.sleep(.01)
        self.assertEqual(self.store.job(parent['job_id'])['status'],'cancelled')
        self.assertEqual(len(started),1)
        self.assertEqual(self.store.job(started[0])['status'],'cancelled')

    def test_overview_cache_only_rebuilds_on_snapshot_revision(self):
        with patch('fagao.overview.build_overview',return_value={'platforms':[]}) as build:
            self.manager.overview(); self.manager.overview(); self.assertEqual(build.call_count,1)
            self.store.save_snapshot(source('fanqie',self.options))
            self.manager.overview(); self.assertEqual(build.call_count,2)
            # Parent remains discoverable even after thirty newer child jobs.
            self.store.save_job({'id':'overview-old','created_at':'2000-01-01','scope_key':OVERVIEW_SCOPE,'status':'running'})
            for index in range(35): self.store.save_job({'id':f'other-{index}','created_at':'2026-11-09','status':'completed'})
            self.assertEqual(self.manager.overview()['update']['id'],'overview-old')

    def test_demand_validation(self):
        for bad in (True,0,-1,10001,'10'):
            with self.subTest(bad=bad), self.assertRaises(AppError): self.manager.start('fanqie',self.options,interactive=True,demand_count=bad)
        for bad in (True,0,1001,'10'):
            with self.subTest(bad=bad), self.assertRaises(AppError): self.manager.more('missing',bad)


if __name__=='__main__': unittest.main()
