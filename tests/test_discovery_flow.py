# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""New sources keep the existing CLI, account and immutable selection contracts."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fagao import daily_feeds, discovery
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

    def test_zhihu_story_validates_board_specific_filters_before_contacting_source(self):
        module = discovery.COLLECTORS['zhihu_story']
        for board, category in [('热度榜', '甜宠'), ('口碑榜', '家庭'), ('推荐榜', '治愈')]:
            with self.subTest(board=board):
                key, options = collection_scope('zhihu_story', {'board': board, 'category': category, 'max_pages': 100})
                self.assertEqual((options['mode'], options['channel'], options['board'], options['category'], options['max_pages']),
                                 ('short', '女频', board, category, 1))
                self.assertEqual(key, collection_scope('zhihu_story', options)[0])
        with patch.object(module, 'collect') as source:
            for options in ({'board': '热度榜', 'category': '家庭'},
                            {'board': '推荐榜', 'category': '悬疑'},
                            {'channel': '男频'}, {'period': '日榜'}):
                with self.subTest(options=options):
                    code, response = self.cli('collect', 'zhihu_story', '--options', json.dumps(options))
                    self.assertEqual((code, response['error']['code']), (1, 'invalid_options'))
            source.assert_not_called()

    def test_zhihu_story_preserves_licensed_feed_cache_and_source_specific_favorites(self):
        store = self.runtime.capture(operation='use').store
        # A previously saved licensed edition is an independent history, never a
        # standard book snapshot or a substitute for the new anonymous source.
        revision = 'a' * 64
        legacy = {'board': 'zhihu', 'columns': ['排名', '书名'], 'rows': [['2', '旧知乎作品']]}
        with store.connect() as connection:
            store._set(connection, 'daily-board:zhihu:' + revision, legacy)
            store._set(connection, 'daily-board:zhihu:history', [{'revision': revision}])
        old_cache = daily_feeds.read_cache(store, 'zhihu')
        other = store.save_snapshot(self.fixture('hongguo_official', discovery.collection_options('hongguo_official', {})))
        store.toggle_favorite(other['id'], '71', True)
        module = discovery.COLLECTORS['zhihu_story']
        data = self.fixture('zhihu_story', discovery.collection_options('zhihu_story', {}))
        data['source_id'] = 'zhihu:story-billboard:hot'
        data['rank_semantics'] = 'source_list_position'
        data['books'][0].update(id='18/71', rank=2, source_position=2, rank_semantics='source_list_position')
        data['books'].append({**data['books'][0], 'id': '18/72', 'title': '第二个短篇', 'rank': 4, 'source_position': 4})
        with patch.object(module, 'collect', return_value=data):
            code, saved = self.cli('collect', 'zhihu_story')
        self.assertEqual(code, 0, saved)
        self.assertIsNone(saved['data_date'])
        self.assertEqual([book['rank'] for book in saved['books']], [2, 4])
        self.assertEqual(saved['source_id'], 'zhihu:story-billboard:hot')
        self.assertEqual(daily_feeds.read_cache(store, 'zhihu'), old_cache)
        store.toggle_favorite(saved['id'], '18/71', True)
        for alias in ('知乎盐选', '盐选短篇', '盐言故事', '盐言', 'zhihu_story'):
            code, task = self.cli('prepare', f'分析我收藏的{alias}《隔离作品》')
            self.assertEqual(code, 0, task)
            self.assertEqual((task['snapshot_id'], task['book_id']), (saved['id'], '18/71'))
        code, task = self.cli('prepare', '分析我收藏的红果《隔离作品》')
        self.assertEqual((code, task['snapshot_id'], task['book_id']), (0, other['id'], '71'))
        self.member.account, self.member.generation = 'member-b', 'session-b'
        other_store = self.runtime.capture(operation='read').store
        self.assertIsNone(daily_feeds.read_cache(other_store, 'zhihu')['snapshot'])
        self.assertEqual(self.cli('state')[1]['favorites'], [])

    def test_zhihu_story_expiry_keeps_saved_metadata_readable_without_new_requests(self):
        store = self.runtime.capture(operation='use').store
        saved = store.save_snapshot(self.fixture('zhihu_story', discovery.collection_options('zhihu_story', {})))
        store.toggle_favorite(saved['id'], '71', True)
        self.member.allowed, self.member.reason = False, 'expired'
        with patch.object(discovery.COLLECTORS['zhihu_story'], 'collect') as source:
            code, state = self.cli('state')
            self.assertEqual(code, 0, state)
            self.assertEqual(state['favorites'][0]['snapshot_id'], saved['id'])
            code, reference = self.cli('resolve', '分析我收藏的知乎盐选《隔离作品》')
            self.assertEqual((code, reference['snapshot_id']), (0, saved['id']))
            for arguments in (('collect', 'zhihu_story'), ('prepare', '分析我收藏的知乎盐选《隔离作品》')):
                code, response = self.cli(*arguments)
                self.assertEqual((code, response['error']['code']), (1, 'membership_expired'))
            source.assert_not_called()

    def test_zhihu_story_cli_never_uses_detail_links_to_fetch_paid_content(self):
        store = self.runtime.capture(operation='use').store
        data = self.fixture('zhihu_story', discovery.collection_options('zhihu_story', {}))
        data['books'][0].update(id='18/71', url='https://www.zhihu.com/market/paid_column/18/section/71')
        saved = store.save_snapshot(data)
        store.toggle_favorite(saved['id'], '18/71', True)
        code, task = self.cli('prepare', '分析我收藏的知乎盐选《隔离作品》')
        self.assertEqual(code, 0, task)
        with patch('fagao.network.build_opener') as opener:
            for command in ('catalog', 'fetch-text'):
                code, response = self.cli(command, '--task', task['id'])
                self.assertEqual(code, 1, response)
                self.assertIn('error', response)
            opener.assert_not_called()


if __name__ == '__main__':
    unittest.main()
