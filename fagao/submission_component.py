# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Launch the installed personal submission editor with a private, single-use draft."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import threading
import time
from xml.parsers.expat import ExpatError

from .store import AppError
from .submission_lock import component_lock


BUNDLE_ID = 'com.fagao.workbench.submission'
MAC_APP = Path('FagaoSubmission.app')
WIN_APP = Path('FagaoSubmission')
RECEIPT_FIELDS = {'version', 'platform', 'edition', 'executable_sha256', 'asar_sha256'}
APPEARANCES = {'light', 'dark', 'eye-care', 'system'}
FIELD_LIMITS = {'work_title': 300, 'pen_name': 100, 'subject': 500, 'body': 200_000}
HANDOFF_TTL = 300
# First launch may include OS package scanning or translation on macOS.
HANDOFF_ACK_TIMEOUT = 60
CHILD_ENV = ('HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE',
             '__CF_USER_TEXT_ENCODING', 'SYSTEMROOT', 'WINDIR', 'USERPROFILE', 'USERNAME',
             'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'HOMEDRIVE', 'HOMEPATH')


def _platform():
    return sys.platform if sys.platform in ('darwin', 'win32') else 'unsupported'


def _unavailable(message):
    return AppError('submission_component_unavailable', message, 503)


@lru_cache(maxsize=1)
def _windows_owner_reader():
    # TokenUser identifies the account; TokenOwner also covers the Windows
    # policy that assigns new files to the logged-in administrator's group.
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    system = ctypes.WinDLL('kernel32', use_last_error=True)
    pointer = ctypes.c_void_p
    ptr_out = ctypes.POINTER(pointer)
    system.GetCurrentProcess.restype = wintypes.HANDLE
    system.CloseHandle.argtypes = [wintypes.HANDLE]
    system.LocalFree.argtypes = [pointer]
    system.LocalFree.restype = pointer
    api.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    api.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, pointer,
                                        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.GetLengthSid.argtypes = [pointer]
    api.GetLengthSid.restype = wintypes.DWORD
    api.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
                                          ptr_out, ptr_out, ptr_out, ptr_out, ptr_out]
    api.GetNamedSecurityInfoW.restype = wintypes.DWORD

    def sid_bytes(sid):
        if not sid:
            raise ValueError('missing Windows owner')
        return ctypes.string_at(sid, api.GetLengthSid(sid))

    token = wintypes.HANDLE()
    if not api.OpenProcessToken(system.GetCurrentProcess(), 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
        raise ctypes.WinError(ctypes.get_last_error())
    allowed = set()
    try:
        for information_class in (1, 4):
            size = wintypes.DWORD()
            api.GetTokenInformation(token, information_class, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if not api.GetTokenInformation(token, information_class, buffer, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            allowed.add(sid_bytes(pointer.from_buffer(buffer).value))
    finally:
        system.CloseHandle(token)

    def is_owned(path):
        owner, descriptor = pointer(), pointer()
        # SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION.
        status = api.GetNamedSecurityInfoW(str(path), 1, 1, ctypes.byref(owner), None, None,
                                           None, ctypes.byref(descriptor))
        if status:
            raise ctypes.WinError(status)
        try:
            return sid_bytes(owner) in allowed
        finally:
            system.LocalFree(descriptor)
    return is_owned


def _checked(path, *, directory=False, private=False):
    """Reject links, junctions and publicly writable files on the trusted path."""
    info = path.lstat()
    reparse = getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if reparse or not correct_type:
        raise ValueError('unexpected file type')
    if os.name == 'posix':
        if info.st_uid != os.geteuid() or info.st_mode & (0o077 if private else 0o022):
            raise ValueError('unexpected ownership or permissions')
    elif os.name == 'nt' and not _windows_owner_reader()(path):
        raise ValueError('unexpected Windows file owner')
    return info


def _trusted_file(root, relative):
    parent = root
    for part in relative.parts[:-1]:
        parent = parent / part
        _checked(parent, directory=True)
    file_path = root / relative
    _checked(file_path)
    return file_path


def _digest(file_path):
    digest = hashlib.sha256()
    with file_path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_component(root, platform, *, check_signature=False):
    if platform == 'unsupported':
        raise _unavailable('桌面投稿目前支持 macOS 和 Windows。')
    if platform == 'win32':
        from scripts import install_submission_component as installer
        if sys.platform == 'win32' and installer.host_architecture() != 'x64':
            raise _unavailable('这台电脑的 Windows 架构暂不支持桌面投稿助手。可在工作台保存草稿，或在 Windows x64 电脑上打开助手。')
    try:
        root = Path(root).absolute()
        _checked(root, directory=True)
        components = root / 'components'
        _checked(components, directory=True)
        component = components / 'submission'
        _checked(component, directory=True)
        receipt_file = _trusted_file(component, Path('receipt.json'))
        if receipt_file.stat().st_size > 4096:
            raise ValueError('oversized receipt')
        receipt = json.loads(receipt_file.read_bytes())
        if (not isinstance(receipt, dict) or receipt.keys() != RECEIPT_FIELDS
                or type(receipt['version']) is not int or receipt['version'] != 1
                or (receipt['platform'], receipt['edition']) != (platform, 'personal')):
            raise ValueError('unrecognized receipt')
        if platform == 'darwin':
            executable = _trusted_file(component, MAC_APP / 'Contents/MacOS/FagaoSubmission')
            asar = _trusted_file(component, MAC_APP / 'Contents/Resources/app.asar')
            plist = _trusted_file(component, MAC_APP / 'Contents/Info.plist')
            if plist.stat().st_size > 128 * 1024:
                raise ValueError('oversized property list')
            metadata = plistlib.loads(plist.read_bytes())
            if (not isinstance(metadata, dict) or metadata.get('CFBundleIdentifier') != BUNDLE_ID
                    or metadata.get('CFBundleExecutable') != 'FagaoSubmission'
                    or not os.access(executable, os.X_OK)):
                raise ValueError('wrong macOS app')
        else:
            executable = _trusted_file(component, WIN_APP / 'FagaoSubmission.exe')
            asar = _trusted_file(component, WIN_APP / 'resources/app.asar')
            # Electron also loads DLLs, ICU data and other resources. The same
            # release pin used by the installer covers all these files.
            builds = installer.read_pins()
            matching = [pin['tree_sha256'] for pin in builds if pin['platform'] == 'win32'
                        and pin['arch'] == 'x64' and installer.receipt_for(pin) == receipt]
            if not matching or installer.tree_sha256(component / WIN_APP, 'win32') not in matching:
                raise ValueError('installed Windows component differs from its release')
        if platform == 'darwin':
            for key, file_path in (('executable_sha256', executable), ('asar_sha256', asar)):
                expected = receipt[key]
                if not isinstance(expected, str) or not re.fullmatch(r'[a-f0-9]{64}', expected) or _digest(file_path) != expected:
                    raise ValueError('installed files differ')
        if platform == 'darwin' and check_signature:
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(component / MAC_APP)],
                           check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30, env={'PATH': '/usr/bin:/bin'})
        return executable
    except FileNotFoundError as exc:
        raise _unavailable('投稿功能尚未就绪。请让 WorkBuddy 用原安装说明更新工作台，再刷新此页。') from exc
    except (ValueError, OSError, TypeError, ExpatError, plistlib.InvalidFileException, subprocess.SubprocessError) as exc:
        raise _unavailable('投稿程序文件校验失败。请让 WorkBuddy 用原安装说明修复工作台，再刷新此页。') from exc


def component_status(root):
    platform = _platform()
    try:
        _verified_component(root, platform)
    except AppError as exc:
        return {'available': False, 'platform': platform, 'message': exc.message}
    return {'available': True, 'platform': platform, 'message': '本机投稿组件已就绪。'}


def _encode_handoff(appearance, draft):
    if not isinstance(appearance, str) or appearance not in APPEARANCES:
        raise AppError('submission_invalid_appearance', '请选择工作台外观。')
    fields = None
    if draft is not None:
        fields = {}
        for key, limit in FIELD_LIMITS.items():
            value = draft.get(key) if isinstance(draft, dict) else None
            try:
                if not isinstance(value, str) or len(value.encode('utf-16-le')) // 2 > limit:
                    raise ValueError
            except ValueError as exc:
                raise AppError('submission_invalid_draft', '草稿内容超过投稿组件支持的长度或包含无效字符。') from exc
            fields['title' if key == 'work_title' else key] = value
    payload = json.dumps({'version': 1, 'appearance': appearance, 'draft': fields},
                         ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > 1024 * 1024:
        raise AppError('submission_invalid_draft', '草稿内容过长。')
    return payload


def _private_directory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    _checked(path, directory=True, private=True)
    return path


def _clear_stale(directory):
    cutoff = time.time() - HANDOFF_TTL
    for path in directory.iterdir():
        if re.fullmatch(r'[0-9a-f]{32}\.(?:json|accepted|claim-[0-9a-f-]{36})|\.prepare-[a-zA-Z0-9_-]+', path.name):
            try:
                info = path.lstat()
                if stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                continue


def _handoff_file(directory, payload):
    nonce = secrets.token_hex(16)
    handoff = directory / (nonce + '.json')
    fd, temporary = tempfile.mkstemp(prefix='.prepare-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
        os.replace(temporary, handoff)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return handoff, nonce


def _environment(handoff_dir, profile_dir):
    env = {key: os.environ[key] for key in CHILD_ENV if key in os.environ}
    if os.name == 'nt':
        system_root = Path(os.environ['SYSTEMROOT'])
        env['PATH'] = os.pathsep.join(map(str, (system_root / 'System32', system_root)))
    else:
        env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
    env['FAGAO_SUBMISSION_HANDOFF_DIR'] = str(handoff_dir)
    env['FAGAO_SUBMISSION_PROFILE_DIR'] = str(profile_dir)
    return env


def open_component(root, appearance, draft):
    payload = _encode_handoff(appearance, draft)
    root = Path(root).absolute()
    try:
        _checked(root, directory=True)
        _checked(root / 'components', directory=True)
        with component_lock(root / 'components', shared=True):
            return _open_component_locked(root, payload)
    except FileNotFoundError as exc:
        raise _unavailable('请先安装本机投稿组件，草稿仍保存在工作台。') from exc
    except (OSError, ValueError) as exc:
        raise _unavailable('投稿组件正在更新或暂时无法打开，请稍后再试。') from exc


def _process_start_error(exc):
    number = getattr(exc, 'winerror', None)
    if number in (2, 3, 126, 193, 216):
        return _unavailable(f'Windows 无法加载投稿程序（错误 {number}）。请让 WorkBuddy 用原安装说明检查并修复投稿组件。')
    if number in (5, 225, 226, 577, 1260):
        return AppError('submission_launch_blocked',
                        f'Windows 阻止了投稿程序启动（错误 {number}）。请让 WorkBuddy 检查系统提示和应用运行权限。', 503)
    detail = f'（Windows 错误 {number}）' if isinstance(number, int) else ''
    return AppError('submission_handoff_failed', f'无法启动本机投稿程序{detail}。请让 WorkBuddy 检查启动错误。', 503)


def _open_component_locked(root, payload):
    executable = _verified_component(root, _platform(), check_signature=True)
    try:
        directory = _private_directory(root / 'submission-handoff')
        profile = _private_directory(root / 'submission-native-profile')
        _clear_stale(directory)
        handoff, nonce = _handoff_file(directory, payload)
    except (OSError, ValueError) as exc:
        raise AppError('submission_handoff_failed', '无法准备本机投稿目录，请检查资料目录权限。', 503) from exc

    accepted = directory / (nonce + '.accepted')
    try:
        try:
            process = subprocess.Popen([str(executable), '--fagao-handoff=' + nonce],
                                       env=_environment(directory, profile), cwd=executable.parent,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, close_fds=True, start_new_session=os.name == 'posix')
        except OSError as exc:
            raise _process_start_error(exc) from exc
        threading.Thread(target=process.wait, name='submission-editor-wait', daemon=True).start()
        # Also covers the second-instance case: the newly started process may exit
        # normally before the existing window consumes the handoff.
        deadline = time.monotonic() + HANDOFF_ACK_TIMEOUT
        while not accepted.exists():
            exit_code = process.poll()
            if exit_code not in (None, 0):
                if _platform() == 'win32' and (exit_code & 0xffffffff) in (0xc0000135, 0xc000007b):
                    return_code = f'0x{exit_code & 0xffffffff:08X}'
                    raise _unavailable(f'Windows 无法加载投稿程序的运行文件（{return_code}）。请让 WorkBuddy 检查并修复投稿组件。')
                raise AppError('submission_handoff_failed',
                               f'投稿程序在窗口就绪前退出（退出码 {exit_code}）。请让 WorkBuddy 检查启动错误。', 503)
            if time.monotonic() >= deadline:
                raise AppError('submission_handoff_failed', '等待投稿窗口响应超时。请查看「发糕投稿助手」窗口或系统提示，然后重试。', 503)
            time.sleep(.05)
        if _checked(accepted, private=True).st_size != 0:
            raise ValueError('invalid acknowledgement')
        return {'opened': True}
    except (OSError, ValueError) as exc:
        raise AppError('submission_handoff_failed', '未能启动本机投稿窗口，请重试。', 503) from exc
    finally:
        handoff.unlink(missing_ok=True)
        accepted.unlink(missing_ok=True)
