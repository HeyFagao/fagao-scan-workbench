# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""The optional browser prepares once, keeps caches private, and preserves failure state."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fagao import recommendation_runtime as runtime
from fagao.collectors import CollectorError


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.data = self.directory / 'data'
        self.source = self.directory / 'source'
        self.node = self.directory / 'bin/node'
        self.node.parent.mkdir()
        self.node.touch()
        self.browser = self.directory / 'chrome'
        self.browser.touch()
        self.environment = {'PATH': '/fixture'}
        self.addCleanup(patch.stopall)
        patch.object(runtime, 'ROOT', self.source).start()
        patch.object(runtime, '_environment', return_value=self.environment.copy()).start()
        patch.object(runtime, '_find_node', return_value=self.node).start()
        patch.object(runtime, '_system_browser', return_value=str(self.browser)).start()
        self.commands = []
        patch.object(runtime, '_run', side_effect=self.command).start()

    def module(self, path, version=None):
        path.mkdir(parents=True, exist_ok=True)
        (path / 'package.json').write_text(json.dumps({'name': 'playwright',
            'version': version or runtime.PLAYWRIGHT_VERSION}), encoding='utf-8')
        (path / 'cli.js').write_text('fixture', encoding='utf-8')

    def command(self, arguments, environment, timeout=12):
        self.commands.append((arguments, environment.copy()))
        if '-e' in arguments:
            return 'function'
        prefix = Path(arguments[arguments.index('--prefix') + 1])
        self.module(prefix / 'node_modules/playwright')
        (prefix / '.npm-cache').mkdir()
        return ''

    def test_existing_project_module_and_system_browser_need_no_download(self):
        module = self.source / 'node_modules/playwright'
        self.module(module)
        result = runtime.ensure_runtime(self.data)
        self.assertEqual(result['module_path'], str(module))
        self.assertEqual(result['executable_path'], str(self.browser))
        self.assertIsNone(result['proxy'])
        self.assertEqual(len(self.commands), 1)
        self.assertNotIn('install', self.commands[0][0])

    def test_fixed_registry_install_is_reused_and_temporary_npm_cache_is_removed(self):
        npm = self.directory / 'npm-cli.js'
        npm.touch()
        with patch.object(runtime, '_npm_cli', return_value=npm):
            first = runtime.ensure_runtime(self.data)
            second = runtime.ensure_runtime(self.data)
        self.assertEqual(first['module_path'], second['module_path'])
        self.assertIsNone(first['proxy'])
        installs = [entry for entry in self.commands if 'install' in entry[0]]
        self.assertEqual(len(installs), 1)
        arguments, environment = installs[0]
        self.assertIn('playwright@1.62.1', arguments)
        self.assertIn('--ignore-scripts', arguments)
        self.assertIn('https://registry.npmjs.org', arguments)
        self.assertEqual(environment['PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD'], '1')
        self.assertNotEqual(environment['npm_config_userconfig'], environment['npm_config_globalconfig'])
        base = runtime.tools_directory(self.data)
        self.assertFalse(list(base.glob('.install-*')))
        self.assertFalse(list(base.rglob('.npm-cache')))
        self.assertFalse(list(base.rglob('.npm-userconfig')))

    def test_failed_install_does_not_promote_partial_package_or_repeat_each_refresh(self):
        calls = []
        def failed(arguments, environment, timeout=12):
            calls.append(arguments)
            raise CollectorError('fixture failure', 'browser_setup_required')
        with patch.object(runtime, '_npm_cli', return_value=self.directory / 'npm-cli.js'), \
                patch.object(runtime, '_run', side_effect=failed):
            for _ in range(2):
                with self.assertRaises(CollectorError):
                    runtime.ensure_runtime(self.data)
        self.assertEqual(len(calls), 1)
        base = runtime.tools_directory(self.data)
        self.assertFalse((base / 'playwright-1.62.1').exists())
        self.assertFalse(list(base.glob('.install-*')))
        self.assertTrue((base / 'last-failure.json').exists())

    def test_account_directories_share_tools_but_not_a_browser_profile(self):
        first = runtime.tools_directory(self.data / 'accounts/account-a')
        second = runtime.tools_directory(self.data / 'accounts/account-b')
        self.assertEqual(first, second)
        self.assertEqual(first, (self.data / 'tools/recommendation-browser').resolve())
        self.assertNotIn('profile', str(first))

    def test_install_lock_prevents_two_prepare_processes(self):
        base = self.directory / 'tools'
        base.mkdir()
        with runtime._setup_lock(base):
            with self.assertRaisesRegex(CollectorError, '正在准备'):
                with runtime._setup_lock(base, timeout=0):
                    self.fail('second prepare acquired the lock')

    def test_wrong_version_project_module_is_not_used(self):
        self.module(self.source / 'node_modules/playwright', version='1.1.0')
        npm = self.directory / 'npm-cli.js'
        with patch.object(runtime, '_npm_cli', return_value=npm):
            result = runtime.ensure_runtime(self.data)
        self.assertIn('playwright-1.62.1', result['module_path'])
        self.assertEqual(len([entry for entry in self.commands if 'install' in entry[0]]), 1)


class EnvironmentTests(unittest.TestCase):
    def test_shell_proxy_tls_and_npm_overrides_do_not_reach_installation(self):
        values = {'PATH': '/fixture', 'HTTPS_PROXY': 'https://unwanted.invalid',
                  'npm_config_registry': 'https://unwanted.invalid',
                  'NODE_OPTIONS': '--require unwanted.js', 'NODE_TLS_REJECT_UNAUTHORIZED': '0'}
        with patch.dict(os.environ, values, clear=True), \
                patch.object(runtime.network, '_settings', return_value={'mode': 'direct', 'proxy_url': None}):
            result = runtime._environment()
        self.assertEqual(result, {'PATH': '/fixture', 'FAGAO_BROWSER_PROXY_MODE': 'direct'})

    def test_only_application_selected_proxy_is_used(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(runtime.network, '_settings',
                return_value={'mode': 'proxy', 'proxy_url': 'http://127.0.0.1:8899'}):
            result = runtime._environment()
        self.assertEqual(result['HTTPS_PROXY'], 'http://127.0.0.1:8899')
        self.assertEqual(runtime.browser_proxy(result),
                         {'server': 'http://127.0.0.1:8899'})

    def test_launch_proxy_separates_credentials_from_server(self):
        result = runtime.browser_proxy({'FAGAO_BROWSER_PROXY':
            'http://fixture%40user:fixture%3Apassword@[::1]:8899'})
        self.assertEqual(result, {'server': 'http://[::1]:8899',
                                 'username': 'fixture@user', 'password': 'fixture:password'})
        self.assertNotIn('fixture', result['server'])

    def test_invalid_proxy_diagnostic_does_not_expose_credentials(self):
        with self.assertRaises(runtime.network.NetworkError) as caught:
            runtime.browser_proxy({'FAGAO_BROWSER_PROXY':
                'https://fixture-user:fixture-secret@proxy.invalid:8899/path'})
        self.assertNotIn('fixture-secret', str(caught.exception))

    def test_subprocess_failure_exposes_only_a_safe_setup_message(self):
        with self.assertRaises(CollectorError) as caught:
            runtime._run([sys.executable, '-c',
                'import sys;sys.stderr.write("https://name:secret@proxy.invalid");sys.exit(1)'], {})
        self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(caught.exception.state, 'browser_setup_required')


class AuthorizationTests(unittest.TestCase):
    def test_windows_cleanup_targets_only_the_own_process_tree(self):
        process = Mock(pid=43210)
        process.communicate.return_value = ('', '')
        windows = SimpleNamespace(name='nt', environ={'SYSTEMROOT': '/fixture/windows'})
        with patch.object(runtime, 'os', windows), patch.object(runtime.sys, 'platform', 'win32'), \
                patch.object(runtime.subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
                patch.object(runtime.subprocess, 'run') as terminate:
            runtime._stop_process(process)
        arguments = terminate.call_args.args[0]
        self.assertEqual(arguments, [str(Path('/fixture/windows') / 'System32' / 'taskkill.exe'),
                                     '/PID', '43210', '/T', '/F'])
        self.assertEqual(terminate.call_args.kwargs['timeout'], 3)
        process.communicate.assert_called_once_with(timeout=2)

    def test_install_revocation_stops_running_process_and_cleans_partial_stage(self):
        from fagao.store import AppError
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            installer = directory / 'npm-cli.js'
            leftover = directory / 'orphan-child'
            ready = directory / 'child-ready'
            probe = directory / 'child-probe'
            child_code = ('import pathlib,time\n'
                'ready=pathlib.Path(' + repr(str(ready)) + ')\n'
                'probe=pathlib.Path(' + repr(str(probe)) + ')\n'
                'ready.write_text("ready")\n'
                'while not probe.exists(): time.sleep(0.02)\n'
                'pathlib.Path(' + repr(str(leftover)) + ').write_text("leftover")\n')
            installer.write_text('import pathlib,subprocess,sys,time\n'
                'stage=pathlib.Path(sys.argv[sys.argv.index("--prefix")+1])\n'
                '(stage/"partial").write_text("fixture")\n'
                'subprocess.Popen([sys.executable,"-c",' + repr(child_code) + '])\n'
                'time.sleep(60)\n', encoding='utf-8')
            processes = []
            original_popen = subprocess.Popen
            def start(*args, **kwargs):
                process = original_popen(*args, **kwargs)
                arguments = args[0]
                if (len(arguments) > 1 and Path(arguments[0]) == Path(sys.executable)
                        and Path(arguments[1]) == installer):
                    processes.append(process)
                return process
            started = time.monotonic()
            revoked = []
            def authorization():
                if ready.exists():
                    if not revoked:
                        revoked.append(time.monotonic())
                    raise AppError('membership_expired', '使用资格已到期')
                self.assertLess(time.monotonic() - started, 5, 'installer child did not become ready')
            with patch.object(runtime, 'ROOT', directory / 'source'), \
                    patch.object(runtime, '_environment', return_value={}), \
                    patch.object(runtime, '_find_node', return_value=Path(sys.executable)), \
                    patch.object(runtime, '_system_browser', return_value='/fixture/chrome'), \
                    patch.object(runtime, '_npm_cli', return_value=installer), \
                    patch.object(runtime.subprocess, 'Popen', side_effect=start), \
                    runtime.network.authorized_requests(authorization):
                with self.assertRaises(AppError) as caught:
                    runtime.ensure_runtime(directory / 'data')
            self.assertEqual(caught.exception.code, 'membership_expired')
            self.assertLess(revoked[0] - started, 5)
            # Windows may use taskkill's 3s budget followed by two 2s pipe waits.
            self.assertLess(time.monotonic() - revoked[0], 8 if sys.platform == 'win32' else 5)
            self.assertTrue(ready.exists(), 'the installer child was not exercised')
            self.assertEqual(len(processes), 1)
            self.assertIsNotNone(processes[0].poll())
            probe.write_text('probe', encoding='utf-8')
            time.sleep(0.6)
            self.assertFalse(leftover.exists(), 'the installer child survived cancellation')
            base = runtime.tools_directory(directory / 'data')
            self.assertFalse(list(base.glob('.install-*')))
            self.assertFalse((base / 'last-failure.json').exists())
            self.assertFalse((base / 'playwright-1.62.1').exists())
            with runtime._setup_lock(base, timeout=0):
                pass

    def test_installer_timeout_is_bounded_and_reaps_process(self):
        processes = []
        target = [sys.executable, '-c', 'import time;time.sleep(60)']
        original_popen = subprocess.Popen
        def start(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            if args[0] == target:
                processes.append(process)
            return process
        with patch.object(runtime.subprocess, 'Popen', side_effect=start):
            with self.assertRaises(CollectorError):
                runtime._run(target, {}, timeout=0.15)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())


if __name__ == '__main__':
    unittest.main()
