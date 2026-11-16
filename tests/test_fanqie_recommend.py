# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
from html import escape
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from fagao import fanqie_recommend as recommend
from fagao import network
from fagao.collectors import CollectorError


def sample(count=2, category='男生生活'):
    return {'category': category, 'source_url': recommend.PAGE + '?' + urlencode({'tag': category}),
            'items': [{'position': str(i).zfill(2), 'title': f'测试故事{i}', 'author': '测试作者',
                       'summary': '来源展示的故事简介。', 'url': f'https://fanqienovel.com/page/{7600000000000000000+i}',
                       'cover_url': 'https://p3-reading-sign.fqnovelpic.com/example.jpeg?x-signature=example',
                       'topics': ['职场', category], 'reading_meta': '3.2万人在读/12分钟读完/1.2万字',
                       'metrics': [{'name': '浏览', 'value': '2.5万'}, {'name': '点赞', 'value': '0'},
                                   {'name': '评论', 'value': '1,234'}]}
                      for i in range(1, count+1)]}


class RecommendationTests(unittest.TestCase):
    def test_registered_real_topics_and_default(self):
        self.assertEqual(len(recommend.TAGS), 99)
        self.assertEqual(len(set(recommend.TAGS)), 99)
        self.assertEqual(recommend.PLATFORM['default_options']['category'], '男生生活')
        self.assertIn('规则怪谈', recommend.TAGS)

    def test_visible_metadata_uses_recommendation_position_and_unknown_date(self):
        result = recommend.parse_result(sample(), '男生生活', 100)
        self.assertEqual(len(result['books']), 2)
        self.assertEqual(result['books'][0]['title'], '测试故事1')
        self.assertEqual(result['books'][0]['rank'], 1)
        self.assertEqual(result['books'][0]['rank_semantics'], 'source_list_position')
        self.assertEqual(result['rank_semantics'], 'source_list_position')
        self.assertIsNone(result['data_date'])
        self.assertIsNone(result['source_total'])
        self.assertEqual(result['pagination']['stop_reason'], 'recommendation_feed')
        self.assertEqual(result['books'][0]['tags'], ['男生生活', '职场'])
        self.assertTrue(result['books'][0]['cover_url'].startswith('https://p3-reading-sign.fqnovelpic.com/'))
        self.assertEqual(result['books'][0]['metrics'], [
            {'name': '浏览', 'value': '2.5', 'unit': '万'},
            {'name': '点赞', 'value': '0', 'unit': ''},
            {'name': '评论', 'value': '1234', 'unit': ''},
            {'name': '在读', 'value': '3.2', 'unit': '万人'},
            {'name': '阅读时长', 'value': '12', 'unit': '分钟'},
            {'name': '字数', 'value': '1.2', 'unit': '万字'},
        ])

    def test_actual_source_positions_are_preserved_and_limit_is_bounded(self):
        data = sample(3)
        for row, position in zip(data['items'], (4, 9, 12)):
            row['position'] = str(position)
        result = recommend.parse_result(data, '男生生活', 2)
        self.assertEqual([book['rank'] for book in result['books']], [4, 9])
        self.assertEqual(result['requested_count'], 2)

    def test_wrong_topic_origin_duplicates_and_invalid_cards_are_rejected(self):
        cases = []
        cases.append(dict(sample(), category='职场'))
        cases.append(dict(sample(), source_url='https://example.invalid/'))
        for field, value in (('url', 'https://example.invalid/page/12345'), ('title', ''), ('position', '未知'),
                             ('position', '0'), ('topics', '职场'), ('metrics', '热度')):
            data = sample()
            data['items'][0][field] = value
            cases.append(data)
        duplicated = sample()
        duplicated['items'][1]['url'] = duplicated['items'][0]['url']
        cases.append(duplicated)
        reversed_positions = sample()
        reversed_positions['items'][1]['position'] = '01'
        cases.append(reversed_positions)
        cases.append(sample(101))
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(CollectorError):
                recommend.parse_result(payload, '男生生活', 100)

    def test_empty_source_and_unsafe_cover(self):
        with self.assertRaises(CollectorError) as caught:
            recommend.parse_result(sample(0), '男生生活', 100)
        self.assertEqual(caught.exception.state, 'empty_source')
        for cover in ('javascript:alert(1)', 'https://p3-reading-sign.fqnovelpic.com.evil.invalid/x',
                      'https://user:password@p3-reading-sign.fqnovelpic.com/x', 'http://p3-reading-sign.fqnovelpic.com/x'):
            payload = sample(1)
            payload['items'][0]['cover_url'] = cover
            self.assertEqual(recommend.parse_result(payload, '男生生活', 100)['books'][0]['cover_url'], '')

    def test_collect_forwards_account_directory_and_does_not_visit_worker_api(self):
        progress = lambda message, **kwargs: None
        with patch.object(recommend, '_read_page', return_value=sample()) as read:
            result = recommend.collect({'category': '男生生活', 'limit': 1000}, progress, Path('/private/account'))
        read.assert_called_once_with('男生生活', 100, Path('/private/account'), progress)
        self.assertEqual(result['requested_count'], 100)
        self.assertIn('wen1701.github.io', result['source_url'])

    def test_invalid_options_are_rejected_before_starting_browser(self):
        for options in ({'category': '不存在的分类'}, {'channel': '男频'}, {'board': '日榜'}, {'limit': True}, {'limit': 0}):
            with self.subTest(options=options), patch.object(recommend, '_read_page') as read:
                with self.assertRaises(CollectorError):
                    recommend.collect(options)
                read.assert_not_called()

    def test_source_errors_are_propagated_to_keep_existing_snapshot(self):
        with patch.object(recommend, '_read_page', side_effect=CollectorError('推荐源暂时不可用', 'network_error')):
            with self.assertRaises(CollectorError) as caught:
                recommend.collect({})
        self.assertEqual(caught.exception.state, 'network_error')

    def test_authorization_revocation_terminates_an_existing_child(self):
        from fagao.store import AppError
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is needed for the process lifecycle check')
        children = []
        cleanup_commands = []
        original_popen = subprocess.Popen
        def start(*args, **kwargs):
            child = original_popen(*args, **kwargs)
            command = args[0] if args else kwargs.get('args')
            if isinstance(command, (list, tuple)):
                if len(command) > 1 and str(command[0]) == node and Path(command[1]) == worker:
                    children.append(child)
                elif Path(command[0]).name.lower() == 'taskkill.exe':
                    cleanup_commands.append(list(command))
            return child
        checks = []
        def authorization():
            checks.append(True)
            if len(checks) > 1:
                raise AppError('membership_expired', '使用资格已到期')
        with tempfile.TemporaryDirectory() as directory:
            worker = Path(directory) / 'waiting.mjs'
            worker.write_text('setInterval(()=>{},1000);\n')
            runtime = {'node': node, 'module_path': str(Path(directory)/'unused'), 'env': {}}
            began = time.monotonic()
            with patch('fagao.recommendation_runtime.ensure_runtime', return_value=runtime), \
                    patch.object(recommend, 'WORKER', worker), \
                    patch.object(recommend.subprocess, 'Popen', side_effect=start), \
                    network.authorized_requests(authorization):
                with self.assertRaises(AppError) as caught:
                    recommend.collect({}, data_dir=directory)
            self.assertEqual(caught.exception.code, 'membership_expired')
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())
            if os.name == 'nt':
                self.assertTrue(cleanup_commands)
                for command in cleanup_commands:
                    self.assertEqual(command[1:4], ['/PID', str(children[0].pid), '/T'])
                    self.assertIn(command[4:], ([], ['/F']))
                # Windows grants the worker up to eight seconds before forcing its tree closed.
                self.assertLess(time.monotonic() - began, 15)
            else:
                self.assertLess(time.monotonic() - began, 5)

    def test_windows_cleanup_targets_only_own_tree_and_uses_force_after_timeout(self):
        child = Mock(pid=48261)
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('fixture worker', 8), 0]
        with patch.object(recommend.sys, 'platform', 'win32'), \
                patch.object(recommend.subprocess, 'run') as kill:
            recommend._stop_browser_process(child)
        arguments = [call.args[0] for call in kill.call_args_list]
        self.assertEqual(len(arguments), 2)
        self.assertTrue(arguments[0][0].endswith('taskkill.exe'))
        self.assertEqual(arguments[0][1:], ['/PID', '48261', '/T'])
        self.assertEqual(arguments[1][1:], ['/PID', '48261', '/T', '/F'])
        self.assertNotIn('/IM', arguments[0])
        self.assertNotIn('chrome.exe', ' '.join(arguments[0]))
        self.assertNotIn('shell', kill.call_args.kwargs)
        child.terminate.assert_not_called()
        child.kill.assert_not_called()

    def test_successful_windows_tree_close_does_not_force_kill(self):
        child = Mock(pid=48261)
        child.poll.return_value = None
        child.wait.return_value = 0
        with patch.object(recommend.sys, 'platform', 'win32'), \
                patch.object(recommend.subprocess, 'run') as kill:
            recommend._stop_browser_process(child)
        self.assertEqual(kill.call_count, 1)
        self.assertEqual(kill.call_args.args[0][1:], ['/PID', '48261', '/T'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX session cleanup')
    def test_posix_worker_closes_normally_before_its_private_group_is_killed(self):
        child = Mock(pid=48261)
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('fixture worker', 8), 0]
        with patch.object(recommend.os, 'killpg') as kill_group:
            recommend._stop_browser_process(child)
        child.terminate.assert_called_once()
        kill_group.assert_called_once_with(48261, recommend.signal.SIGKILL)


@unittest.skipUnless(os.environ.get('FAGAO_SYNTHETIC_BROWSER_TEST') == '1', 'opt in to an isolated browser fixture')
class RecommendationBrowserTests(unittest.TestCase):
    def test_normal_page_dom_worker_has_no_profile_and_extracts_cards(self):
        from fagao.recommendation_runtime import _system_browser
        root = Path(__file__).resolve().parents[1]
        node = shutil.which('node')
        self.assertTrue(node)
        module = Path(os.environ.get('FAGAO_QA_NODE_MODULES') or root / 'node_modules') / 'playwright'
        self.assertTrue(module.is_dir())
        row = sample(1)['items'][0]
        html = ('<!doctype html><meta charset="utf-8"><h2 id="short-feed-title">男生生活</h2>'
                '<section id="short-feed" aria-busy="false"><article class="short-card">'
                '<div class="short-cover"><img src="' + escape(row['cover_url'], quote=True) + '">'
                '<span class="short-position">01</span></div><h3>测试故事1</h3><p class="short-author">测试作者</p>'
                '<p class="short-excerpt">来源展示的故事简介。</p><div class="short-topic-row"><span>职场</span></div>'
                '<p class="short-reading-meta">3.2万人在读/12分钟读完/1.2万字</p><div class="short-card-footer">'
                '<dl class="short-metrics"><div><dt>浏览</dt><dd>2.5万</dd></div></dl>'
                '<a href="' + row['url'] + '">打开短篇</a></div></article></section>')
        with tempfile.TemporaryDirectory() as directory:
            fixture_module = Path(directory) / 'fixture.cjs'
            fixture_module.write_text('const {chromium}=require(' + json.dumps(str(module)) + ');\n'
                'module.exports={chromium:{launch:async settings=>{\n'
                'if(settings.userDataDir)throw Error("private profile used");\n'
                'if(JSON.stringify(settings.proxy)!==JSON.stringify({server:"http://127.0.0.1:32199",username:"fixture-user",password:"fixture-password"}))throw Error("proxy fields not preserved");\n'
                'delete settings.proxy;\n'
                'const browser=await chromium.launch(settings);const create=browser.newContext.bind(browser);\n'
                'browser.newContext=async options=>{const context=await create(options);\n'
                'await context.route("**/*",route=>{\n'
                'if(route.request().url().startsWith(' + json.dumps(recommend.PAGE) + '))\n'
                'return route.fulfill({status:200,contentType:"text/html; charset=utf-8",body:' + json.dumps(html) + '});\n'
                'return route.abort();});return context;};return browser;}}};\n')
            runtime = {'node': node, 'module_path': str(fixture_module), 'env': {},
                       'proxy': {'server': 'http://127.0.0.1:32199', 'username': 'fixture-user',
                                 'password': 'fixture-password'}}
            browser = _system_browser()
            if browser:
                runtime['executable_path'] = browser
            with patch('fagao.recommendation_runtime.ensure_runtime', return_value=runtime):
                snapshot = recommend.collect({'category': '男生生活', 'limit': 100}, data_dir=directory)
            self.assertEqual(snapshot['books'][0]['title'], '测试故事1')
            self.assertEqual(snapshot['books'][0]['summary'], '来源展示的故事简介。')
            self.assertEqual(snapshot['books'][0]['rank'], 1)
            self.assertEqual(snapshot['books'][0]['cover_url'], row['cover_url'])


if __name__ == '__main__':
    unittest.main()
