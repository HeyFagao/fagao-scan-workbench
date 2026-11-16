# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Prepare the optional browser used to read a public short-story recommendation page."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit, urlunsplit

from . import network
from .collectors import CollectorError, _notify


ROOT = Path(__file__).resolve().parents[1]
PLAYWRIGHT_VERSION = '1.62.1'
RETRY_SECONDS = 30 * 60


def tools_directory(data_dir):
    directory = Path(data_dir).expanduser().resolve()
    if directory.parent.name == 'accounts':
        directory = directory.parent.parent
    return directory / 'tools' / 'recommendation-browser'


def _stop_process(process):
    """Stop the dependency installer and its children without exposing diagnostics."""
    try:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGTERM)
        elif sys.platform == 'win32':
            # npm and Playwright can both leave Node children behind when only the parent is killed.
            system = Path(os.environ.get('SYSTEMROOT', r'C:\Windows')) / 'System32/taskkill.exe'
            subprocess.run([str(system), '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            process.terminate()
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.communicate(timeout=2)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except OSError:
        pass
    try:
        process.communicate(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        # A broken pipe must not turn cancellation into an unbounded wait.
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _run(arguments, environment, timeout=12):
    network.check_authorization()
    process = None
    completed = False
    options = {'start_new_session': True} if os.name == 'posix' else {}
    if sys.platform == 'win32':
        options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        process = subprocess.Popen(arguments, env=environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                                   **options)
        deadline = time.monotonic() + timeout
        while True:
            network.check_authorization()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(arguments, timeout)
            try:
                output, _ = process.communicate(timeout=min(0.25, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        network.check_authorization()
        completed = True
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CollectorError('推荐页面的浏览器环境准备未完成，请让 WorkBuddy 检查后重试。',
                             'browser_setup_required') from exc
    finally:
        if process is not None and not completed:
            _stop_process(process)
    if process.returncode:
        # Dependency diagnostics can contain local proxy configuration. Keep them out of responses.
        raise CollectorError('推荐页面的浏览器环境准备未完成，请稍后重试，或让 WorkBuddy 检查网络。',
                             'browser_setup_required')
    return output.strip()


def _environment():
    environment = network.sanitize_environment(os.environ)
    # Discard npm's inherited session-specific overrides, including TLS exceptions.
    for key in tuple(environment):
        if key.lower().startswith(('npm_config_', 'playwright_download_', 'playwright_skip_')):
            environment.pop(key, None)
    environment.pop('NODE_TLS_REJECT_UNAUTHORIZED', None)
    environment.pop('NODE_EXTRA_CA_CERTS', None)
    environment.pop('NODE_OPTIONS', None)
    settings = network._settings()
    mode = settings['mode']
    if mode == 'proxy':
        proxies = {'http': settings['proxy_url'], 'https': settings['proxy_url']}
    elif mode == 'system':
        proxies = network._system_proxies()
    else:
        proxies = {}
    for scheme in ('http', 'https'):
        proxy = proxies.get(scheme)
        if proxy:
            environment[scheme.upper() + '_PROXY'] = network._proxy_url(proxy)
    environment['FAGAO_BROWSER_PROXY_MODE'] = mode
    if mode == 'proxy':
        environment['FAGAO_BROWSER_PROXY'] = settings['proxy_url']
    else:
        environment.pop('FAGAO_BROWSER_PROXY', None)
    return environment


def browser_proxy(environment):
    """Translate the application's explicit proxy into Playwright's normal launch setting."""
    value = environment.get('FAGAO_BROWSER_PROXY')
    if not value:
        return None
    # Normal app configuration currently forbids credentials. Keep the launch
    # contract explicit so a future credential-capable provider cannot put them in server.
    try:
        if not isinstance(value, str) or len(value) > 2048 or re.search(r'[\s\\\x00-\x1f\x7f]', value):
            raise ValueError('invalid proxy')
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if ':' in host:
            host = '[' + host + ']'
        address = host + (':' + str(parsed.port) if parsed.port else '')
        server = network._proxy_url(urlunsplit((parsed.scheme, address, parsed.path, parsed.query, parsed.fragment)))
        result = {'server': server}
        if parsed.username is not None:
            result['username'] = unquote(parsed.username)
        if parsed.password is not None:
            result['password'] = unquote(parsed.password)
        return result
    except (ValueError, TypeError):
        raise network.NetworkError('代理地址无效，请让 WorkBuddy 检查应用网络配置。') from None


def _find_node(environment):
    candidates = [shutil.which('node'), environment.get('CODEX_PRIMARY_RUNTIME_NODE')]
    versions = Path.home() / '.workbuddy' / 'binaries' / 'node' / 'versions'
    if versions.is_dir():
        directories = sorted((p for p in versions.iterdir() if p.is_dir()),
                             key=lambda p: tuple(int(n) for n in re.findall(r'\d+', p.name)), reverse=True)
        for directory in directories:
            candidates.extend((directory / 'bin/node', directory / 'node.exe', directory / 'bin/node.exe'))
    for value in candidates:
        if not value:
            continue
        path = Path(value).expanduser()
        if not path.is_file():
            continue
        try:
            version = _run([str(path), '--version'], environment)
        except CollectorError:
            continue
        match = re.fullmatch(r'v(\d+)\.\d+\.\d+', version)
        if match and int(match[1]) >= 20:
            return path.resolve()
    raise CollectorError('番茄短篇推荐需要浏览器环境，请让 WorkBuddy 自动准备 Node.js 20 或更新版本后继续。',
                         'browser_setup_required')


def _module_ready(module, node, environment):
    try:
        package = json.loads((module / 'package.json').read_text(encoding='utf-8'))
        if package.get('name') != 'playwright' or package.get('version') != PLAYWRIGHT_VERSION:
            return False
        output = _run([str(node), '-e', 'const p=require(process.argv[1]);process.stdout.write(typeof p.chromium.launch)',
                       str(module)], environment)
        return output == 'function'
    except (OSError, ValueError, CollectorError):
        return False


def _npm_cli(node):
    candidates = (node.parent / 'node_modules/npm/bin/npm-cli.js',
                  node.parent.parent / 'lib/node_modules/npm/bin/npm-cli.js',
                  node.parent.parent / 'node_modules/npm/bin/npm-cli.js')
    for path in candidates:
        if path.is_file():
            return path
    raise CollectorError('当前 Node.js 缺少官方 npm，请让 WorkBuddy 修复运行环境后继续。',
                         'browser_setup_required')


@contextmanager
def _setup_lock(base, timeout=45):
    lock = base / '.prepare.lock'
    with lock.open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        deadline = time.monotonic() + timeout
        held = False
        while not held:
            network.check_authorization()
            try:
                stream.seek(0)
                if sys.platform == 'win32':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise CollectorError('推荐浏览器正在准备，请稍后再打开此题材。', 'browser_setup_required')
                time.sleep(0.25)
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _system_browser():
    if sys.platform == 'darwin':
        paths = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                 '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge']
    elif sys.platform == 'win32':
        paths = []
        for name in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
            folder = os.environ.get(name)
            if folder:
                paths.extend((str(Path(folder) / 'Google/Chrome/Application/chrome.exe'),
                              str(Path(folder) / 'Microsoft/Edge/Application/msedge.exe')))
    else:
        paths = [shutil.which(name) for name in ('google-chrome', 'microsoft-edge', 'chromium', 'chromium-browser')]
    return next((str(Path(path)) for path in paths if path and Path(path).is_file()), None)


def _bundled_browser(module, node, environment):
    output = _run([str(node), '-e', 'process.stdout.write(require(process.argv[1]).chromium.executablePath())',
                   str(module)], environment)
    return output if output and Path(output).is_file() else None


def ensure_runtime(data_dir, progress=None):
    """Return a verified local runtime, preparing only the optional fixed browser dependency."""
    network.check_authorization()
    base = tools_directory(data_dir)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        environment = _environment()
    except network.NetworkError as exc:
        raise CollectorError(str(exc), 'network_error') from exc
    environment['PLAYWRIGHT_BROWSERS_PATH'] = str(base / 'browsers')
    node = _find_node(environment)
    installed = base / ('playwright-' + PLAYWRIGHT_VERSION)
    candidates = [ROOT / 'node_modules/playwright', installed / 'node_modules/playwright']
    with _setup_lock(base):
        module = next((path for path in candidates if _module_ready(path, node, environment)), None)
        executable = _system_browser()
        if module and not executable:
            executable = _bundled_browser(module, node, environment)
        if module and executable:
            return {'node': str(node), 'module_path': str(module), 'executable_path': executable,
                    'env': environment, 'proxy': browser_proxy(environment)}
        failed = base / 'last-failure.json'
        try:
            retry_at = json.loads(failed.read_text(encoding='utf-8')).get('retry_after', 0)
        except (OSError, ValueError, AttributeError):
            retry_at = 0
        if isinstance(retry_at, (int, float)) and retry_at > time.time():
            raise CollectorError('推荐浏览器上次准备未完成，请稍后重试或让 WorkBuddy 检查网络后继续。',
                                 'browser_setup_required')
        try:
            if module is None:
                _notify(progress, '正在准备短篇推荐的浏览器环境…')
                npm = _npm_cli(node)
                with tempfile.TemporaryDirectory(prefix='.install-', dir=base) as temporary:
                    stage = Path(temporary)
                    user_config = stage / '.npm-userconfig'
                    global_config = stage / '.npm-globalconfig'
                    user_config.write_text('', encoding='utf-8')
                    global_config.write_text('', encoding='utf-8')
                    npm_environment = dict(environment, npm_config_cache=str(stage / '.npm-cache'),
                                           npm_config_userconfig=str(user_config), npm_config_globalconfig=str(global_config),
                                           PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD='1')
                    _run([str(node), str(npm), 'install', '--prefix', str(stage), '--registry',
                          'https://registry.npmjs.org', '--ignore-scripts', '--no-audit', '--no-fund',
                          '--no-save', '--no-package-lock', 'playwright@' + PLAYWRIGHT_VERSION],
                         npm_environment, timeout=300)
                    staged_module = stage / 'node_modules/playwright'
                    if not _module_ready(staged_module, node, environment):
                        raise CollectorError('推荐浏览器组件校验未通过，请让 WorkBuddy 检查后继续。',
                                             'browser_setup_required')
                    shutil.rmtree(stage / '.npm-cache', ignore_errors=True)
                    user_config.unlink()
                    global_config.unlink()
                    if installed.exists():
                        shutil.rmtree(installed)
                    stage.rename(installed)
                module = installed / 'node_modules/playwright'
            if executable is None:
                _notify(progress, '正在准备短篇推荐所需的浏览器，首次使用可能需要几分钟…')
                _run([str(node), str(module / 'cli.js'), 'install', 'chromium'], environment, timeout=420)
                executable = _bundled_browser(module, node, environment)
                if not executable:
                    raise CollectorError('推荐浏览器下载未完成，请让 WorkBuddy 检查后继续。',
                                         'browser_setup_required')
            failed.unlink(missing_ok=True)
        except CollectorError as exc:
            if network.is_authorization_error(exc):
                raise
            failed.write_text(json.dumps({'retry_after': time.time() + RETRY_SECONDS}), encoding='utf-8')
            raise
    return {'node': str(node), 'module_path': str(module), 'executable_path': executable,
            'env': environment, 'proxy': browser_proxy(environment)}
