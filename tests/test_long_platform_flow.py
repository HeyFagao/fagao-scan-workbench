# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""The additional domestic lists use the same account-scoped CLI and task entry points."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fagao.__main__ import main
from fagao import long_rankings
from fagao.runtime import WorkbenchRuntime
from fagao.server import collection_scope
from fagao.store import AppError
from membership_fixtures import SyntheticMembership


class LongPlatformFlowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.membership = SyntheticMembership(self.root)
        self.runtime = WorkbenchRuntime(self.root, self.membership)
        self.assertTrue(long_rankings.COLLECTORS, 'No additional domestic ranking source registered')

    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['--data-dir', str(self.root), *args], runtime_factory=lambda _: self.runtime)
        return code, json.loads(output.getvalue())

    @staticmethod
    def source_fixture(platform, options):
        # Same numeric book ID on different sites is intentional. The source
        # explicitly shows a rank of 9 and says nothing about the ranking date.
        return dict(options, platform=platform, source_url='https://example.org/ranking',
                    books=[{'id': '71', 'title': '隔离作品', 'author': '测试作者', 'rank': 9}])

    def test_cli_collection_favorite_and_analysis_stay_with_the_source_and_account(self):
        store = self.runtime.capture(operation='use').store
        info = {p['id']: p for p in store.state()['platforms']}
        for platform, module in long_rankings.COLLECTORS.items():
            with self.subTest(platform=platform):
                received = []

                def collect(options, progress=None):
                    received.append(dict(options))
                    return self.source_fixture(platform, options)

                with patch.object(module, 'collect', side_effect=collect):
                    code, snap = self.cli('collect', platform)
                    self.assertEqual(code, 0, snap)
                    for dim in ('channel', 'category', 'board'):
                        self.assertEqual(received[0][dim], info[platform]['default_options'][dim])
                    self.assertEqual(received[0]['limit'], 100)
                self.assertEqual((snap['mode'], snap['data_date'], snap['books'][0]['rank']),
                                 ('long', None, 9))
                store.toggle_favorite(snap['id'], '71', True)
                with self.assertRaisesRegex(AppError, '尚未接入|暂未接入'):
                    self.runtime.capture(operation='use').jobs.start_text(snap['id'], '71')

        self.assertEqual({f['platform'] for f in store.state()['favorites']}, set(long_rankings.COLLECTORS))
        for platform in long_rankings.COLLECTORS:
            with self.subTest(favorite=platform):
                code, task = self.cli('prepare', f'分析我收藏的{info[platform]["name"]}《隔离作品》')
                self.assertEqual(code, 0, task)
                self.assertEqual(task['platform'], platform)
                self.assertIsNone(task['text_id'])
        self.membership.account = 'member-b'
        self.membership.generation = 'session-b'
        self.assertEqual(self.cli('state')[1]['favorites'], [])
        self.assertEqual(self.cli('state')[1]['snapshots'], [])

    def test_inactive_cli_and_invalid_board_cannot_reach_the_adapter(self):
        for platform, module in long_rankings.COLLECTORS.items():
            with self.subTest(platform=platform), patch.object(module, 'collect') as collect:
                self.membership.allowed, self.membership.reason = True, 'active'
                code, _ = self.cli('collect', platform, '--options', '{"board":"不存在的榜型"}')
                self.assertEqual(code, 1)
                self.membership.allowed, self.membership.reason = False, 'expired'
                code, body = self.cli('collect', platform)
                self.assertEqual((code, body['error']['code']), (1, 'membership_expired'))
                collect.assert_not_called()

    def test_cli_reports_source_failure_and_keeps_previous_edition(self):
        from fagao.collectors import CollectorError

        store = self.runtime.capture(operation='use').store
        module = long_rankings.COLLECTORS['faloo']
        raw = self.source_fixture('faloo', long_rankings.collection_options('faloo', {}))
        saved = store.save_snapshot(raw)
        with patch.object(module, 'collect', side_effect=CollectorError('平台官网返回 HTTP 403。', 'forbidden')):
            code, response = self.cli('collect', 'faloo')
        self.assertEqual((code, response['error']['code']), (1, 'forbidden'))
        self.assertIn('403', response['error']['message'])
        self.assertEqual([item['id'] for item in store.state()['snapshots']], [saved['id']])

    def test_rank_reference_distinguishes_daily_and_monthly_editions(self):
        store = self.runtime.capture(operation='use').store
        spec = long_rankings.PLATFORMS['qimao']
        board = spec['default_options']['board']
        snapshots = {}
        for period in ('月榜', '日榜'):
            options = {**spec['default_options'], 'period': period}
            snapshots[period] = store.save_snapshot(self.source_fixture('qimao', options))['id']
        # A saved daily view must not shadow a request for the monthly ranking.
        store.set_view({'mode': 'long', 'platform': 'qimao', 'snapshot_id': snapshots['日榜']})
        for period, sid in snapshots.items():
            code, result = self.cli('resolve', f'七猫{board}{period}第9名')
            self.assertEqual(code, 0, result)
            self.assertEqual(result['snapshot_id'], sid)
        store.set_view({'mode': 'long', 'platform': 'fanqie'})
        code, error = self.cli('resolve', f'七猫{board}第9名')
        self.assertEqual((code, error['error']['code']), (1, 'ambiguous_scope'))
        self.assertEqual({c['snapshot_id'] for c in error['error']['candidates']}, set(snapshots.values()))

    def test_qimao_list_position_resolves_without_claiming_an_official_rank(self):
        store = self.runtime.capture(operation='use').store
        raw = self.source_fixture('qimao', long_rankings.PLATFORMS['qimao']['default_options'])
        raw['books'][0].update(rank=1, rank_semantics='source_list_position')
        sid = store.save_snapshot(raw)['id']
        code, book = self.cli('resolve', '七猫大热榜第1位')
        self.assertEqual(code, 0, book)
        self.assertEqual((book['snapshot_id'], book['book_id']), (sid, '71'))
        self.assertEqual(book['book']['rank_semantics'], 'source_list_position')
        self.assertIn('来源列表第 1 位', book['scope_description'])
        code, error = self.cli('resolve', '七猫大热榜原榜第1名')
        self.assertEqual((code, error['error']['code']), (1, 'rank_not_found'))
        self.assertIn('列表位置', error['error']['message'])

    def test_equivalent_default_filters_reuse_one_job_and_offline_cache(self):
        manager = self.runtime.capture(operation='use').jobs
        for platform, module in long_rankings.COLLECTORS.items():
            with self.subTest(platform=platform):
                scope, effective = collection_scope(platform, {})
                explicit = dict(module.PLATFORM['default_options'], limit=100)
                self.assertEqual(scope, collection_scope(platform, explicit)[0])
                # Existing clients may still send the legacy 10,000 ceiling.
                self.assertEqual(scope, collection_scope(platform, {**explicit, 'limit': 10000})[0])
                entered, release = threading.Event(), threading.Event()

                def holding_collect(options, progress=None):
                    entered.set()
                    self.assertTrue(release.wait(4))
                    return self.source_fixture(platform, options)

                with patch.object(module, 'collect', side_effect=holding_collect) as collect:
                    start = manager.start(platform, {}, automatic=True)
                    try:
                        self.assertTrue(entered.wait(3))
                        again = manager.start(platform, explicit, automatic=True)
                        self.assertEqual((again['status'], again['job_id']), ('busy', start['job_id']))
                    finally:
                        release.set()
                    end = time.monotonic() + 3
                    while time.monotonic() < end:
                        with manager.lock:
                            if scope not in manager.active:
                                break
                        time.sleep(.01)
                    saved = manager.start(platform, explicit, automatic=True)
                    self.assertEqual(saved['status'], 'fresh')
                    self.assertEqual(manager.store.snapshot(saved['snapshot_id'])['board'], effective['board'])
                    self.assertEqual(collect.call_count, 1)
