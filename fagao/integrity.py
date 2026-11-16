# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Bounded local installation checks, not a signature or a hostile-user boundary."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import marshal
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import types

APP = 'fagao-scan-workbench'
REPOSITORY = 'HeyFagao/fagao-scan-workbench'
MAX_RECEIPT = 4 * 1024 * 1024
MAX_FILES = 10000
MAX_FILE = 32 * 1024 * 1024
MAX_EXPANDED = 200 * 1024 * 1024
_HEX = re.compile(r'[a-f0-9]{64}')
_RELEASE = re.compile(r'(?:[a-f0-9]{40}|local-[a-f0-9]{20})')


class IntegrityError(RuntimeError):
    code = 'installation_integrity_failed'

    def __init__(self, reason='invalid_installation', diagnostic=None):
        self.reason = reason
        self.details = {key:value for key,value in (diagnostic or {}).items()
                        if key in ('repair_required','deferred','recovery','message')}
        super().__init__(self.details.get('message') or '工作台安装文件未通过完整性检查。请让 WorkBuddy 用原安装说明自动修复；本机资料无需删除。')

    def diagnostic(self):
        return {'code': self.code, 'reason': self.reason, 'message': str(self),
                'repair_required': True, 'data_preserved': True, **self.details}


def _fail(reason):
    raise IntegrityError(reason)


def _is_link(path):
    if path.is_symlink():
        return True
    return bool(getattr(path, 'is_junction', lambda: False)())


def _relative(name):
    if (not isinstance(name, str) or not name or len(name) > 1024
            or any(c in name for c in ('\\', '\x00', ':'))
            or any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in name.split('/'))):
        _fail('invalid_inventory_path')
    try:
        name.encode('utf-8')
    except UnicodeError:
        _fail('invalid_inventory_path')
    path = PurePosixPath(name)
    if path.is_absolute():
        _fail('invalid_inventory_path')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *['COM' + str(i) for i in range(1, 10)],
                *['LPT' + str(i) for i in range(1, 10)]}
    if any(part.split('.')[0].upper() in reserved for part in path.parts):
        _fail('invalid_inventory_path')
    return path


def inventory_map(value):
    if not isinstance(value, dict) or not 1 <= len(value) <= MAX_FILES:
        _fail('invalid_inventory')
    folded = set()
    for name, digest in value.items():
        _relative(name)
        if not isinstance(digest, str) or not _HEX.fullmatch(digest):
            _fail('invalid_inventory')
        if name.casefold() in folded:
            _fail('ambiguous_inventory_path')
        folded.add(name.casefold())
    return value


def _json_file(path, maximum=MAX_RECEIPT):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            _fail('invalid_receipt_file')
        with path.open('rb') as stream:
            raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            _fail('invalid_receipt_file')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    _fail('duplicate_receipt_field')
                result[key] = value
            return result
        return json.loads(raw, object_pairs_hook=unique)
    except (OSError, ValueError, RecursionError, UnicodeError) as exc:
        raise IntegrityError('unreadable_receipt') from exc


def installed_layout(root):
    root = Path(root).expanduser().absolute()
    return root.parent.name == 'releases' and bool(_RELEASE.fullmatch(root.name))


def read_receipt(root):
    root = Path(root).expanduser().absolute()
    if _is_link(root) or not root.is_dir():
        _fail('invalid_release_directory')
    receipt_path = root / '.fagao-release.json'
    if not receipt_path.exists() and not _is_link(receipt_path):
        if installed_layout(root):
            _fail('missing_release_receipt')
        return None
    value = _json_file(receipt_path)
    allowed = {'app', 'release_id', 'source', 'files', 'runtime_integrity', 'original_files', 'code_seal'}
    if (not isinstance(value, dict) or set(value) - allowed
            or not {'app', 'release_id', 'source', 'files'} <= set(value)
            or value['app'] != APP or value['release_id'] != root.name
            or not installed_layout(root) or _is_link(root.parent) or _is_link(root.parent.parent)):
        _fail('invalid_release_receipt')
    source = value['source']
    if not isinstance(source, dict):
        _fail('invalid_release_source')
    if source.get('kind') == 'github':
        if (set(source) != {'kind', 'repo', 'commit', 'archive_sha256'}
                or source['repo'] != REPOSITORY or source['commit'] != root.name
                or not re.fullmatch(r'[a-f0-9]{40}', root.name)
                or not isinstance(source['archive_sha256'], str) or not _HEX.fullmatch(source['archive_sha256'])):
            _fail('invalid_release_source')
    elif source.get('kind') == 'local':
        if (set(source) != {'kind', 'source_dir'} or not root.name.startswith('local-')
                or not isinstance(source['source_dir'], str) or not Path(source['source_dir']).is_absolute()):
            _fail('invalid_release_source')
    else:
        _fail('invalid_release_source')
    inventory_map(value['files'])
    if 'runtime_integrity' in value and (type(value['runtime_integrity']) is not int or value['runtime_integrity'] != 1):
        _fail('unsupported_integrity_format')
    if 'original_files' in value:
        inventory_map(value['original_files'])
    if 'code_seal' in value and (not isinstance(value['code_seal'], dict) or 'original_files' not in value):
        _fail('invalid_code_seal_receipt')
    if 'code_seal' in value:
        seal = value['code_seal']
        protected = {'fagao/membership.py':'fagao.membership', 'fagao/runtime.py':'fagao.runtime',
                     'fagao/network.py':'fagao.network'}
        if (set(seal) != {'format','stage_name','pack_file','pack_sha256','data_root','modules'}
                or type(seal['format']) is not int or seal['format'] != 1
                or not isinstance(seal['stage_name'],str) or not re.fullmatch(r'\.staging-[a-f0-9]{32}',seal['stage_name'])
                or seal['pack_file'] != '.fagao-code-seal.json'
                or not isinstance(seal['pack_sha256'], str) or not _HEX.fullmatch(seal['pack_sha256'])
                or value['files'].get(seal['pack_file']) != seal['pack_sha256']
                or not isinstance(seal['data_root'], str) or '\x00' in seal['data_root']
                or not Path(seal['data_root']).is_absolute()
                or not isinstance(seal['modules'], dict) or set(seal['modules']) != set(protected)):
            _fail('invalid_code_seal_receipt')
        for path, module in protected.items():
            row = seal['modules'][path]
            if (not isinstance(row, dict) or set(row) != {'module','source_sha256','stub_sha256','token_sha256'}
                    or row['module'] != module or row['source_sha256'] != value['original_files'].get(path)
                    or row['stub_sha256'] != value['files'].get(path)
                    or not all(isinstance(row[field],str) and _HEX.fullmatch(row[field])
                               for field in ('source_sha256','stub_sha256','token_sha256'))):
                _fail('invalid_code_seal_receipt')
    return value


def _safe_python_cache(path, root, files):
    """Accept generated caches only when their executable code equals checked source."""
    if path.parent.name != '__pycache__' or path.suffix != '.pyc':
        return False
    match = re.fullmatch(r'(.+)\.([^.]+)(?:\.opt-([012]))?\.pyc', path.name)
    if not match:
        return False
    source = path.parent.parent / (match[1] + '.py')
    if source.relative_to(root).as_posix() not in files:
        return False
    # Other interpreter tags are not executable by this interpreter. Keep them
    # without parsing an incompatible marshal format or deleting user files.
    if match[2] != sys.implementation.cache_tag:
        return True
    try:
        raw = path.read_bytes()
        if len(raw) < 16 or len(raw) > MAX_FILE:
            return False
        cached = marshal.loads(raw[16:])
        if not isinstance(cached, types.CodeType) or Path(cached.co_filename).resolve() != source.resolve():
            return False
        expected = compile(source.read_bytes(), cached.co_filename, 'exec',
                           dont_inherit=True, optimize=int(match[3] or 0))
        return marshal.dumps(cached) == marshal.dumps(expected)
    except (OSError, ValueError, TypeError, EOFError, OverflowError, RecursionError, SystemError):
        return False


def verify_installation(root=None):
    root = Path(root or Path(__file__).resolve().parents[1]).expanduser().absolute()
    receipt = read_receipt(root)
    if receipt is None:
        return {'status': 'source_directory', 'official': False, 'checked': False}
    files = receipt['files']
    actual, total, count = set(), 0, 0
    try:
        for directory, directories, names in os.walk(root, followlinks=False):
            parent = Path(directory)
            for name in directories:
                if _is_link(parent / name):
                    _fail('unexpected_symbolic_link')
            for name in names:
                path = parent / name
                relative = path.relative_to(root).as_posix()
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    _fail('unexpected_symbolic_link')
                count += 1
                total += info.st_size
                if count > MAX_FILES + 1 or info.st_size > MAX_FILE or total > MAX_EXPANDED:
                    _fail('installation_size_limit')
                if relative == '.fagao-release.json':
                    continue
                if relative not in files and name in ('.DS_Store','Thumbs.db','desktop.ini') and info.st_size <= 1024*1024:
                    # File-manager metadata is not imported or executed by this
                    # application. Do not mistake normal Finder use for piracy.
                    continue
                if relative not in files and _safe_python_cache(path, root, files):
                    continue
                actual.add(relative)
                if relative in files and hashlib.sha256(path.read_bytes()).hexdigest() != files[relative]:
                    _fail('file_digest_mismatch')
        if actual != set(files):
            _fail('unexpected_or_missing_file')
    except OSError as exc:
        raise IntegrityError('unreadable_installation_file') from exc
    return {'status': 'verified', 'official': receipt['source']['kind'] == 'github',
            'checked': True, 'release_id': receipt['release_id'], 'files': len(files)}


class ReleaseLease:
    """A shared OS lock keeps repairs out while this exact release is executing."""
    def __init__(self, root, *, exclusive=False):
        self.root = Path(root).absolute()
        self.exclusive = exclusive
        self.stream = None
        self._windows = None

    def __enter__(self):
        if not installed_layout(self.root):
            return self
        directory = self.root.parent.parent / '.release-leases'
        if _is_link(directory):
            _fail('invalid_release_lease')
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / (self.root.name + '.lock')
        if _is_link(path):
            _fail('invalid_release_lease')
        self.stream = path.open('a+b')
        try:
            if os.name == 'nt':
                import ctypes
                from ctypes import wintypes
                import msvcrt
                class Overlapped(ctypes.Structure):
                    _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                                ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD),
                                ('hEvent', wintypes.HANDLE)]
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                              wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
                kernel.LockFileEx.restype = wintypes.BOOL
                kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                                wintypes.DWORD, ctypes.POINTER(Overlapped)]
                kernel.UnlockFileEx.restype = wintypes.BOOL
                handle = msvcrt.get_osfhandle(self.stream.fileno())
                overlap = Overlapped()
                if not kernel.LockFileEx(handle, 1 | (2 if self.exclusive else 0), 0, 1, 0, ctypes.byref(overlap)):
                    raise OSError(ctypes.get_last_error(), 'release lock unavailable')
                self._windows = (kernel, handle, overlap, ctypes)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), (fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise IntegrityError('release_in_use' if self.exclusive else 'release_repair_in_progress') from exc
        return self

    def __exit__(self, *args):
        if self.stream is not None:
            if self._windows:
                kernel, handle, overlap, ctypes = self._windows
                kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlap))
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None


@contextmanager
def checked_release(root=None):
    root = Path(root or Path(__file__).resolve().parents[1])
    with ReleaseLease(root):
        result = verify_installation(root)
        yield result
