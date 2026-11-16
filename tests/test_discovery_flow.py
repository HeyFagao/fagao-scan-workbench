# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""New sources keep the existing CLI, account and immutable selection contracts."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fagao import discovery
from fagao.__main__ import main
from fagao.collectors import CollectorError
from fagao.runtime import WorkbenchRuntime
from fagao.server import collection_scope
from membership_fixtures import SyntheticMembership


class DiscoveryFlowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.member = SyntheticMembership(self.root)
        self.runtime = WorkbenchRuntime(self.root, self.member)

    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['--data-dir', str(self.root), *args], runtime_factory=lambda _: self.runtime)
        return code, json.loads(output.getvalue())

    def fixture(self, platform, options):
        return dict(options, platform=platform, source_id='fixture:' + platform,
                    source_url=discovery.PLATFORMS[platform]['source_url'], data_date=None,
                    source_label_date='官网标签，非数据结算日', source_rows_seen=12,
                    books=[{'id': '71', 'title': '隔离作品', 'author': '测试作者',
                            'summary': '来源简介', 'rank': 9, 'rank_semantics': 'original_rank',
                            'cover_url': 'https://example.org/poster.jpg'}])

    def test_sources_use_registered_modes_and_preserve_metadata_without_claiming_a_date(self):
        store = self.runtime.capture(operation='use').store
        for platform, module in discovery.COLLECTORS.items():
            with self.subTest(platform=platform):
                spec = discovery.PLATFORMS[platform]
                with patch.object(module, 'collect', side_effect=lambda options, *a, **kw: self.fixture(platform, options)):
                    code, snap = self.cli('collect', platform)
                self.assertEqual(code, 0, snap)
                self.assertEqual(snap['mode'], spec['mode'])
                self.assertIsNone(snap['data_date'])
                self.assertEqual(snap['source_label_date'], '官网标签，非数据结算日')
                self.assertEqual(snap['source_rows_seen'], 12)
                self.assertEqual(snap['books'][0]['summary'], '来源简介')
                self.assertEqual(snap['books'][0]['cover_url'], 'https://example.org/poster.jpg')
                store.toggle_favorite(snap['id'], '71', True)
                code, task = self.cli('prepare', f'分析我收藏的{spec["name"]}《隔离作品》')
                self.assertEqual(code, 0, task)
                self.assertEqual((task['platform'], task['book_id']), (platform, '71'))
        self.member.account = 'member-b'
        self.member.generation = 'session-b'
        self.assertEqual(self.cli('state')[1]['snapshots'], [])
        self.assertEqual(self.cli('state')[1]['favorites'], [])

    def test_invalid_scope_and_expired_account_never_reach_a_source(self):
        for platform, module in discovery.COLLECTORS.items():
            with self.subTest(platform=platform), patch.object(module, 'collect') as source:
                self.member.allowed, self.member.reason = True, 'active'
                code, _ = self.cli('collect', platform, '--options', '{"board":"无此榜"}')
                self.assertEqual(code, 1)
                self.member.allowed, self.member.reason = False, 'expired'
                code, response = self.cli('collect', platform)
                self.assertEqual((code, response['error']['code']), (1, 'membership_expired'))
                source.assert_not_called()

    def test_canonical_defaults_and_legacy_limits_share_the_same_job_scope(self):
        for platform, spec in discovery.PLATFORMS.items():
            with self.subTest(platform=platform):
                key, options = collection_scope(platform, {})
                self.assertEqual(options['mode'], spec['mode'])
                self.assertEqual(key, collection_scope(platform, spec['default_options'])[0])
                self.assertEqual(key, collection_scope(platform, {**spec['default_options'], 'limit': 10000})[0])
                with self.assertRaisesRegex(Exception, '类型'):
                    collection_scope(platform, {'mode': 'overseas'})

    def test_failed_source_keeps_saved_previous_edition(self):
        module = discovery.COLLECTORS['hongguo_official']
        store = self.runtime.capture(operation='use').store
        saved = store.save_snapshot(self.fixture('hongguo_official', discovery.collection_options('hongguo_official', {})))
        with patch.object(module, 'collect', side_effect=CollectorError('平台维护中', 'network_error')):
            code, response = self.cli('collect', 'hongguo_official')
        self.assertEqual((code, response['error']['code']), (1, 'network_error'))
        self.assertEqual([s['id'] for s in store.state()['snapshots']], [saved['id']])

    def test_recommendation_browser_receives_the_captured_account_directory(self):
        module = discovery.COLLECTORS['fanqie_recommend']
        directory = self.runtime.capture(operation='use').store.directory
        with patch.object(module, 'collect', side_effect=lambda options, *a, **kw:
                          self.fixture('fanqie_recommend', options)) as source:
            code, response = self.cli('collect', 'fanqie_recommend')
        self.assertEqual(code, 0, response)
        self.assertEqual(source.call_args.kwargs['data_dir'], directory)


if __name__ == '__main__':
    unittest.main()
