# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Archive/direct coexistence, true dated history, and nonblocking import."""
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fagao.insights import build_insights
from fagao.overview import build_overview, relevant_snapshot_ids
from fagao.server import JobManager, OVERVIEW_SCOPE
from fagao.store import Store
from tests.test_dashboard import book, snapshot, view

SOURCE='github:scoful/FanqieRankTracker'


def public(ident='public', day='2026-11-09', reads=40000, count=20):
    return snapshot(ident, data_date=day, complete=False, source_id=SOURCE,
        source_revision='git-blob-sha1:'+day, rank_version='public:'+day,
        collected_at='2026-11-09T23:00:00Z',
        provenance={'source_id':SOURCE,'source_date':day,'source_revision':'git-blob-sha1:'+day,
                    'repo_url':'https://github.com/scoful/FanqieRankTracker','sample_limit':20,
                    'metric_precision':'source_display_rounded','rank_semantics':'source_list_position'},
        books=[book(str(i),reads=reads,words=None,rank=None,source_position=i,
                    rank_semantics='source_list_position') for i in range(1,count+1)])


class PublicDataSemanticsTest(unittest.TestCase):
    def test_same_day_public_sample_does_not_replace_full_direct_data(self):
        direct=snapshot('direct',books=[book(str(i)) for i in range(1,31)])
        data=build_overview([direct,public()])['platforms'][0]
        self.assertEqual(data['dashboard']['views']['全部']['kpis']['books'],30)
        self.assertEqual(len(data['dashboard']['views']['全部']['categories']),1)
        self.assertEqual(data['dashboard']['views']['全部']['kpis']['valid_words_count'],30)

    def test_public_history_is_independent_of_direct_index(self):
        direct=snapshot('direct',books=[book(str(i)) for i in range(1,31)])
        report=view([direct,public(),public('old','2026-11-02',20000)])
        self.assertEqual(report['kpis']['books'],30)
        self.assertTrue(all(row['score'] is None for row in report['blackhorse']['items']))
        changes=report['growth']
        self.assertEqual(changes['compared_count'],20)
        row=changes['items'][0]
        self.assertEqual((row['delta'],row['interval_days'],row['delta_rate']),(20000,7,100))
        self.assertEqual((row['source_id'],row['baseline_source_id']),(SOURCE,SOURCE))
        self.assertEqual(row['baseline_snapshot_id'],'old')
        self.assertIsNone(row['rank'])
        self.assertIn('非全榜',next(item for item in report['sources'] if item['source_id']==SOURCE)['sample_scope'])

    def test_direct_history_wins_for_same_date_but_never_mixes_sources(self):
        direct=snapshot('direct',books=[book('1',reads=20000)])
        old=snapshot('direct-old',data_date='2026-11-07',books=[book('1',reads=15000)])
        report=view([direct,old,public(count=1),public('public-old','2026-11-02',10000,1)])
        row=report['growth']['items'][0]
        self.assertEqual((row['source_id'],row['delta'],row['interval_days']),('direct',5000,2))
        self.assertEqual(report['blackhorse']['items'][0]['score'],1205)

    def test_exact_week_baseline_survives_metadata_reduction_per_source(self):
        records=[snapshot('direct'),public(),public('week','2026-11-02',10000,1),
                 public('yesterday','2026-11-08',30000,1),public('unused','2026-11-01',1,1)]
        ids=relevant_snapshot_ids(records)
        self.assertEqual(set(ids),{'direct','public','week','yesterday'})
        self.assertEqual(build_overview(records),build_overview([r for r in records if r['id'] in ids]))
        self.assertEqual(view(records)['growth']['items'][0]['interval_days'],7)

    def test_single_board_insights_do_not_compare_different_sources(self):
        direct=snapshot('direct',books=[book('1')])
        report=build_insights(direct,[public('older','2026-11-02',10000,1)])
        self.assertFalse(report['changes']['available'])

    def test_baseline_zero_keeps_delta_and_null_rate(self):
        row=view([public(count=1),public('old','2026-11-02',0,1)])['growth']['items'][0]
        self.assertEqual(row['delta'],40000)
        self.assertIsNone(row['delta_rate'])

    def test_real_store_retains_source_identity_positions_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory)
            store.save_snapshot(public('old','2026-11-02',20000))
            saved=store.save_snapshot(public())
            self.assertEqual(saved['source_id'],SOURCE)
            self.assertEqual(saved['provenance']['sample_limit'],20)
            self.assertEqual(saved['books'][0]['source_position'],1)
            self.assertEqual(saved['books'][0]['rank_semantics'],'source_list_position')
            self.assertIsNone(saved['books'][0]['rank'])
            self.assertEqual(view(store.overview_history())['growth']['compared_count'],20)


class PublicSyncJobsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name);self.manager=JobManager(self.store)
        self.job={'id':'parent','kind':'overview','status':'running','scope_key':OVERVIEW_SCOPE,
                  'created_at':'2026-11-09T12:00:00Z'}
        self.store.save_job(self.job)

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_sorts_history_before_current_and_is_idempotent(self):
        payload={'snapshots':[public(),public('old','2026-11-02')],'coverage':[{'categories':1}]}
        with patch('fagao.public_rankings.sync_public_rankings',return_value=payload):
            self.assertTrue(self.manager._start_public_sync(self.job).wait(2))
            self.assertEqual(self.store.job('parent')['public_source']['snapshot_count'],2)
            records=self.store.snapshot_history()
            self.assertEqual(len(records),2)
            self.assertEqual(records[0]['data_date'],'2026-11-09')
            self.assertTrue(self.manager._start_public_sync(self.job).wait(2))
        self.assertEqual(len(self.store.snapshot_history()),2)
        self.assertEqual(self.store.job('parent')['public_source']['snapshot_count'],0)

    def test_actual_adapter_output_is_idempotent_even_without_optional_mode(self):
        from tests.test_public_rankings import fixture, normalize
        records=normalize(fixture())
        with patch('fagao.public_rankings.sync_public_rankings',return_value={'snapshots':records}):
            self.assertTrue(self.manager._start_public_sync(self.job).wait(2))
            # Also cover the old adapter shape that lets Store supply long mode.
            for item in records: item.pop('mode',None)
            self.assertTrue(self.manager._start_public_sync(self.job).wait(2))
        self.assertEqual(len(self.store.snapshot_history()),1)
        self.assertEqual(self.store.job('parent')['public_source']['snapshot_count'],0)

    def test_restart_clears_running_public_marker_after_direct_completed(self):
        self.store.save_job({**self.job,'status':'completed','public_source':{'status':'running','snapshot_count':4}})
        self.store.recover_jobs()
        recovered=self.store.job('parent')
        self.assertEqual(recovered['status'],'completed')
        self.assertEqual(recovered['public_source']['status'],'interrupted')
        self.assertEqual(recovered['public_source']['snapshot_count'],4)

    def test_source_failure_preserves_job_and_existing_direct_snapshot(self):
        saved=self.store.save_snapshot(snapshot())
        with patch('fagao.public_rankings.sync_public_rankings',side_effect=RuntimeError('source unavailable')):
            self.assertTrue(self.manager._start_public_sync(self.job).wait(2))
        self.assertEqual(self.store.job('parent')['status'],'running')
        self.assertEqual(self.store.job('parent')['public_source']['status'],'unavailable')
        self.assertEqual(self.store.snapshot_history()[0]['id'],saved['id'])

    def test_already_cancelled_parent_is_not_resurrected(self):
        self.manager.cancel('parent')
        with patch('fagao.public_rankings.sync_public_rankings') as fetch:
            done=self.manager._start_public_sync(self.job)
            self.assertTrue(done.is_set());fetch.assert_not_called()
        self.assertEqual(self.store.job('parent')['status'],'cancelled')

    def test_cancel_prevents_late_public_import(self):
        release=threading.Event(); entered=threading.Event()
        def fetch(**kwargs):
            entered.set(); release.wait(2);return {'snapshots':[public()]}
        with patch('fagao.public_rankings.sync_public_rankings',side_effect=fetch):
            done=self.manager._start_public_sync(self.job)
            self.assertTrue(entered.wait(1))
            self.manager.cancel('parent');release.set();self.assertTrue(done.wait(2))
        self.assertEqual(self.store.snapshot_history(),[])
        self.assertEqual(self.store.job('parent')['public_source']['status'],'cancelled')

    def test_cancel_still_stops_public_when_direct_parent_has_completed(self):
        release=threading.Event(); entered=threading.Event()
        def fetch(**kwargs):
            entered.set(); release.wait(2);return {'snapshots':[public()]}
        with patch('fagao.public_rankings.sync_public_rankings',side_effect=fetch):
            done=self.manager._start_public_sync(self.job)
            self.assertTrue(entered.wait(1))
            current=self.store.job('parent');current['status']='completed';self.store.save_job(current)
            cancelled=self.manager.cancel('parent')
            self.assertEqual(cancelled['public_source']['status'],'cancelled')
            release.set();self.assertTrue(done.wait(2))
        self.assertEqual(self.store.snapshot_history(),[])

    def test_public_timeout_is_bounded_and_cannot_write_late(self):
        release=threading.Event(); returned=threading.Event()
        def fetch(**kwargs):
            release.wait(2);returned.set();return {'snapshots':[public()]}
        with patch('fagao.public_rankings.sync_public_rankings',side_effect=fetch),patch('fagao.server.PUBLIC_SYNC_DEADLINE_SECONDS',.03):
            done=self.manager._start_public_sync(self.job)
            self.assertTrue(done.wait(.5));self.assertEqual(self.store.job('parent')['public_source']['status'],'unavailable')
            release.set();self.assertTrue(returned.wait(1));time.sleep(.03)
        self.assertEqual(self.store.snapshot_history(),[])

    def test_slow_archive_does_not_block_direct_first_batch(self):
        release=threading.Event(); first=threading.Event(); archive_done=threading.Event()
        def fetch(**kwargs):
            release.wait(3);archive_done.set();return {'snapshots':[]}
        def collect(platform,options,*args):
            first.set()
            return {'platform':platform,'mode':'long','channel':options['channel'],'category':options['category'],
                    'board':options['board'],'period':options.get('period'),
                    'categories':[options['category']], 'data_date':'2026-11-09',
                    'complete':True,'books':[book('1')]}
        with patch('fagao.public_rankings.sync_public_rankings',side_effect=fetch),patch('fagao.collectors.collect',side_effect=collect):
            started=self.manager.start_overview()
            self.assertTrue(first.wait(1));self.assertFalse(archive_done.is_set())
            release.set()
            deadline=time.monotonic()+3
            while self.manager.active and time.monotonic()<deadline: time.sleep(.01)
            self.assertFalse(self.manager.active)
            self.assertEqual(self.store.job(started['job_id'])['status'],'completed')


if __name__=='__main__':unittest.main()
