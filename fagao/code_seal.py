# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Device-bound packaging for three local application modules.

The original public source remains available. This layer protects the installed
copy at rest and increases the cost of copying or scanning that copy; it is not
an authorization decision or a promise that local code cannot be analysed.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading

if __package__:
    from .credential_store import CredentialStoreError, SecretProtector, fernet_decrypt, fernet_encrypt
else:
    # The installer invokes only this script from a fully verified staging tree.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fagao.credential_store import CredentialStoreError, SecretProtector, fernet_decrypt, fernet_encrypt


APP = 'fagao-scan-workbench'
PACK_FILE = '.fagao-code-seal.json'
RECEIPT_FILE = '.fagao-release.json'
PURPOSE = 'code-seal-v1'
PROTECTED_MODULES = {
    'fagao/membership.py': 'fagao.membership',
    'fagao/runtime.py': 'fagao.runtime',
    'fagao/network.py': 'fagao.network',
}
MAX_SOURCE_BYTES = 1024 * 1024
MAX_PACK_BYTES = 5 * 1024 * 1024
MAX_RECEIPT_BYTES = 5 * 1024 * 1024
HEX_DIGEST = re.compile(r'[a-f0-9]{64}')
NOTICE_MARKER = '# 发糕团队维护声明。'
_KEYS = {}
_KEY_LOCK = threading.RLock()


class CodeSealError(RuntimeError):
    code = 'installation_integrity_failed'
    repair_required = True

    def __init__(self, reason='integrity'):
        self.reason = reason
        self.deferred = reason in ('key_locked', 'key_unavailable')
        self.repair_required = not self.deferred
        self.recovery = ('unlock_keychain' if reason == 'key_locked' else
                         'retry_secure_storage' if reason == 'key_unavailable' else 'repair_installation')
        message = ('系统钥匙串尚未解锁，请先在系统中解锁后重试；程序和已有资料仍保留，无需重装。'
                   if reason == 'key_locked' else
                   '系统安全存储暂时不可用，请让 WorkBuddy 检查后重试；程序和已有资料仍保留，无需重装。'
                   if reason == 'key_unavailable' else
                   '本机程序保护校验未完成，请让 WorkBuddy 从已发布版本修复；已有资料仍保留。')
        super().__init__(message)

    def diagnostic(self):
        return {'code': self.code, 'reason': self.reason, 'repair_required': self.repair_required,
                'deferred': self.deferred, 'recovery': self.recovery,
                'data_preserved': True, 'message': str(self)}


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _root(directory):
    try:
        path = Path(directory).expanduser().absolute()
        if not path.is_dir() or path.is_symlink():
            raise CodeSealError('invalid_release')
        return path.resolve()
    except (OSError, TypeError, ValueError):
        raise CodeSealError('invalid_release') from None


def _file(root, relative):
    # Only fixed package paths and the two fixed receipt files may be opened.
    if relative not in (*PROTECTED_MODULES, PACK_FILE, RECEIPT_FILE):
        raise CodeSealError('invalid_path')
    path = root / relative
    for component in (path, *path.parents):
        if component.is_symlink():
            raise CodeSealError('invalid_path')
        if component == root:
            break
    if not path.is_file():
        raise CodeSealError('missing_file')
    return path


def _read(path, maximum):
    try:
        with path.open('rb') as handle:
            content = handle.read(maximum + 1)
        if len(content) > maximum:
            raise CodeSealError('oversized_file')
        return content
    except CodeSealError:
        raise
    except OSError:
        raise CodeSealError('unreadable_file') from None


def _json(content):
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise CodeSealError('duplicate_metadata')
                result[key] = value
            return result
        result = json.loads(content.decode('utf-8'), object_pairs_hook=unique)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise CodeSealError('invalid_metadata') from None


def _data_root(directory):
    try:
        path = Path(directory).expanduser().absolute()
        if path.is_symlink():
            raise CodeSealError('invalid_data_root')
        return path.resolve()
    except (OSError, TypeError, ValueError):
        raise CodeSealError('invalid_data_root') from None


def _check_originals(original_files):
    if (not isinstance(original_files, dict) or not 1 <= len(original_files) <= 10000
            or not all(isinstance(name, str) and 0 < len(name) <= 1024
                       and isinstance(digest, str) and HEX_DIGEST.fullmatch(digest)
                       for name, digest in original_files.items())):
        raise CodeSealError('invalid_inventory')
    for relative in PROTECTED_MODULES:
        if not isinstance(original_files.get(relative), str) or not HEX_DIGEST.fullmatch(original_files[relative]):
            raise CodeSealError('invalid_inventory')


def _key_payload(key, stage_name, original_files):
    # Stage ownership and the trusted source inventory are authenticated by the
    # OS-protected envelope, rather than only by editable local JSON hashes.
    return _encoded({'app': APP, 'format': 1, 'stage_name': stage_name,
                     'original_inventory_sha256': _digest(_encoded(original_files)),
                     'key_b64': base64.urlsafe_b64encode(key).decode('ascii')})


def _read_key_payload(payload, pack, original_files):
    protected = _json(payload)
    if (set(protected) != {'app', 'format', 'stage_name', 'original_inventory_sha256', 'key_b64'}
            or protected.get('app') != APP or type(protected.get('format')) is not int
            or protected['format'] != 1 or protected.get('stage_name') != pack['stage_name']
            or protected.get('original_inventory_sha256') != _digest(_encoded(original_files))
            or not isinstance(protected.get('key_b64'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{43}=', protected['key_b64'])):
        raise CodeSealError('protected_origin_mismatch')
    try:
        encoded = protected['key_b64'].encode('ascii')
        key = base64.b64decode(encoded, altchars=b'-_', validate=True)
        if len(key) != 32 or base64.urlsafe_b64encode(key) != encoded:
            raise ValueError
        return key
    except (ValueError, binascii.Error):
        raise CodeSealError('invalid_key') from None


def _stub(source):
    try:
        lines = source.decode('utf-8').splitlines()
    except UnicodeError:
        raise CodeSealError('invalid_source') from None
    notices = [line for line in lines[:5] if line.startswith(NOTICE_MARKER)]
    if len(notices) != 1:
        raise CodeSealError('missing_notice')
    return (notices[0] + '\n'
            '"""Local protected module; repair it through the official installer."""\n'
            'from .code_seal import load_module as _fagao_load_module\n'
            '_fagao_load_module(__name__, globals())\n'
            'del _fagao_load_module\n').encode('utf-8')


def _payload(module, relative, source):
    return module.encode('ascii') + b'\0' + relative.encode('ascii') + b'\0' + source


def _source(module, relative, content):
    prefix = module.encode('ascii') + b'\0' + relative.encode('ascii') + b'\0'
    if not content.startswith(prefix):
        raise CodeSealError('module_binding')
    return content[len(prefix):]


def _atomic(path, content, mode=0o600):
    descriptor, temporary = tempfile.mkstemp(prefix='.code-seal-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def seal_release(directory, original_files, data_root, *, protector=None):
    """Transform an already verified new staging tree; never reseal an existing pack.

    ``original_files`` must come from the installer's independently verified
    archive inventory. The installer remains responsible for checking the full
    tree and only promoting it after this transaction succeeds.
    """
    root = _root(directory)
    data = _data_root(data_root)
    _check_originals(original_files)
    if (root / PACK_FILE).exists() or (root / PACK_FILE).is_symlink():
        raise CodeSealError('already_sealed')
    sources, stubs, modes = {}, {}, {}
    for relative, module in PROTECTED_MODULES.items():
        path = _file(root, relative)
        source = _read(path, MAX_SOURCE_BYTES)
        if not hmac.compare_digest(_digest(source), original_files[relative]):
            raise CodeSealError('source_modified')
        try:
            compile(source, str(path), 'exec', dont_inherit=True)
        except (SyntaxError, ValueError):
            raise CodeSealError('invalid_source') from None
        sources[relative] = source
        stubs[relative] = _stub(source)
        modes[relative] = path.stat().st_mode & 0o777
    protector = protector or SecretProtector(data, purpose=PURPOSE)
    key = secrets.token_bytes(32)
    envelope = None
    changed = []
    try:
        protected_key = _key_payload(key, root.name, original_files)
        envelope = protector.seal(protected_key)
        if not hmac.compare_digest(protector.open(envelope), protected_key):
            raise CodeSealError('key_roundtrip')
        modules, sealed_modules = {}, {}
        for relative, module in PROTECTED_MODULES.items():
            token = fernet_encrypt(key, _payload(module, relative, sources[relative]))
            modules[relative] = {'module': module, 'source_sha256': original_files[relative],
                                 'token': token.decode('ascii')}
            sealed_modules[relative] = {'module': module, 'source_sha256': original_files[relative],
                                        'stub_sha256': _digest(stubs[relative]), 'token_sha256': _digest(token)}
        pack = {'app': APP, 'format': 1, 'stage_name': root.name, 'data_root': str(data),
                'key_envelope': envelope, 'modules': modules}
        packed = _encoded(pack)
        metadata = {'format': 1, 'stage_name': root.name, 'pack_file': PACK_FILE, 'pack_sha256': _digest(packed),
                    'data_root': str(data), 'modules': sealed_modules}
        _atomic(root / PACK_FILE, packed)
        for relative in PROTECTED_MODULES:
            _atomic(root / relative, stubs[relative], modes[relative])
            changed.append(relative)
        verify_seal(root, metadata, original_files, data, protector=protector)
        return metadata
    except (CredentialStoreError, CodeSealError, OSError, TypeError, ValueError) as exc:
        rollback_failed = False
        for relative in changed:
            try:
                _atomic(root / relative, sources[relative], modes[relative])
            except OSError:
                rollback_failed = True
        try:
            (root / PACK_FILE).unlink(missing_ok=True)
        except OSError:
            rollback_failed = True
        if envelope is not None:
            try:
                protector.discard(envelope)
            except CredentialStoreError:
                pass
        reason = ('key_' + exc.reason if isinstance(exc, CredentialStoreError)
                  else exc.reason if isinstance(exc, CodeSealError) else 'sealing_failed')
        raise CodeSealError('staging_rollback_failed' if rollback_failed else reason) from None


def _validated_pack(directory, metadata, original_files, data_root):
    root, data = _root(directory), _data_root(data_root)
    _check_originals(original_files)
    if (not isinstance(metadata, dict)
            or set(metadata) != {'format', 'stage_name', 'pack_file', 'pack_sha256', 'data_root', 'modules'}
            or type(metadata.get('format')) is not int or metadata['format'] != 1
            or not isinstance(metadata.get('stage_name'), str)
            or not re.fullmatch(r'[A-Za-z0-9._-]{1,200}', metadata['stage_name'])
            or metadata.get('pack_file') != PACK_FILE or metadata.get('data_root') != str(data)
            or not isinstance(metadata.get('pack_sha256'), str)
            or not HEX_DIGEST.fullmatch(metadata['pack_sha256'])
            or not isinstance(metadata.get('modules'), dict)
            or set(metadata['modules']) != set(PROTECTED_MODULES)):
        raise CodeSealError('invalid_metadata')
    packed = _read(_file(root, PACK_FILE), MAX_PACK_BYTES)
    if not hmac.compare_digest(_digest(packed), metadata['pack_sha256']):
        raise CodeSealError('pack_modified')
    pack = _json(packed)
    if (set(pack) != {'app', 'format', 'stage_name', 'data_root', 'key_envelope', 'modules'}
            or pack.get('app') != APP or type(pack.get('format')) is not int or pack['format'] != 1
            or pack.get('stage_name') != metadata['stage_name']
            or pack.get('data_root') != str(data) or not isinstance(pack.get('key_envelope'), dict)
            or not isinstance(pack.get('modules'), dict) or set(pack['modules']) != set(PROTECTED_MODULES)):
        raise CodeSealError('invalid_pack')
    for relative, module in PROTECTED_MODULES.items():
        row, sealed = pack['modules'][relative], metadata['modules'][relative]
        if (not isinstance(row, dict) or set(row) != {'module', 'source_sha256', 'token'}
                or row.get('module') != module or row.get('source_sha256') != original_files[relative]
                or not isinstance(row.get('token'), str) or not 100 <= len(row['token']) <= MAX_PACK_BYTES
                or not isinstance(sealed, dict)
                or set(sealed) != {'module', 'source_sha256', 'stub_sha256', 'token_sha256'}
                or sealed.get('module') != module or sealed.get('source_sha256') != original_files[relative]
                or not all(isinstance(sealed.get(field), str) and HEX_DIGEST.fullmatch(sealed[field])
                           for field in ('stub_sha256', 'token_sha256'))):
            raise CodeSealError('invalid_module')
        try:
            token = row['token'].encode('ascii')
        except UnicodeError:
            raise CodeSealError('invalid_module') from None
        stub = _read(_file(root, relative), MAX_SOURCE_BYTES)
        if (not hmac.compare_digest(_digest(stub), sealed['stub_sha256'])
                or not hmac.compare_digest(_digest(token), sealed['token_sha256'])):
            raise CodeSealError('module_modified')
    return root, pack


def _key(pack, data_root, protector, original_files, *, cache=False):
    protector = protector or SecretProtector(data_root, purpose=PURPOSE)
    cache_id = (os.getpid(), str(data_root), _digest(_encoded(pack['key_envelope'])),
                pack['stage_name'], _digest(_encoded(original_files)))
    try:
        with _KEY_LOCK:
            if cache and cache_id in _KEYS:
                return _KEYS[cache_id]
            key = _read_key_payload(protector.open(pack['key_envelope']), pack, original_files)
            if cache:
                # No source plaintext or key material is written outside the OS store.
                _KEYS[cache_id] = key
            return key
    except CredentialStoreError as exc:
        raise CodeSealError('key_' + exc.reason) from None


def _decrypt_source(pack, relative, key):
    row = pack['modules'][relative]
    try:
        source = _source(PROTECTED_MODULES[relative], relative,
                         fernet_decrypt(key, row['token'].encode('ascii')))
    except (CredentialStoreError, UnicodeError):
        raise CodeSealError('ciphertext_integrity') from None
    if len(source) > MAX_SOURCE_BYTES or not hmac.compare_digest(_digest(source), row['source_sha256']):
        raise CodeSealError('source_integrity')
    return source


def verify_seal(directory, metadata, original_files, data_root, *, protector=None, cache=False):
    """Read-only validation; missing keys are never replaced or regenerated."""
    root, pack = _validated_pack(directory, metadata, original_files, data_root)
    key = _key(pack, _data_root(data_root), protector, original_files, cache=cache)
    for relative in PROTECTED_MODULES:
        source = _decrypt_source(pack, relative, key)
        if _digest(_stub(source)) != metadata['modules'][relative]['stub_sha256']:
            raise CodeSealError('stub_binding')
        try:
            compile(source, str(root / relative), 'exec', dont_inherit=True)
        except (SyntaxError, ValueError):
            raise CodeSealError('invalid_source') from None
    return True


def verify_metadata(directory, metadata, original_files, data_root):
    """Check fixed scope and file hashes without opening OS secrets or importing code."""
    _validated_pack(directory, metadata, original_files, data_root)
    return True


def discard_seal(directory, metadata, original_files, data_root, *, protector=None):
    """Discard only an unpromoted staging pack's own OS secret.

    The installer must retain this pack and its metadata when deletion is
    deferred. Promoted releases, healthy repeats and quarantine rollback copies
    are intentionally outside this API's path scope.
    """
    root = _root(directory)
    if not re.fullmatch(r'\.staging-[a-f0-9]{32}', root.name):
        raise CodeSealError('not_unpromoted_stage')
    _, pack = _validated_pack(root, metadata, original_files, data_root)
    if metadata['stage_name'] != root.name:
        raise CodeSealError('stage_binding')
    data = _data_root(data_root)
    protector = protector or SecretProtector(data, purpose=PURPOSE)
    try:
        # Authenticate this program key's origin before deleting anything. A
        # copied old pack cannot obtain cleanup authority by rewriting hashes.
        _key(pack, data, protector, original_files, cache=False)
        # Strict mode reports a locked/unavailable Keychain instead of losing
        # the last envelope reference while pretending cleanup succeeded.
        protector.discard(pack['key_envelope'], strict=True)
    except CodeSealError as exc:
        if exc.reason != 'key_missing':
            raise
        # Already absent: there is no native item to remove. This is an
        # idempotent completion, not authority to delete a different item.
    except CredentialStoreError as exc:
        raise CodeSealError('key_' + exc.reason) from None
    envelope_id = _digest(_encoded(pack['key_envelope']))
    with _KEY_LOCK:
        for identity in tuple(_KEYS):
            if identity[1:3] == (str(data), envelope_id):
                del _KEYS[identity]


def load_module(name, namespace):
    """Load an authenticated module into its original local module namespace."""
    relative = next((path for path, module in PROTECTED_MODULES.items() if module == name), None)
    if (relative is None or not isinstance(namespace, dict) or namespace.get('__name__') != name
            or not isinstance(namespace.get('__file__'), str)):
        raise CodeSealError('invalid_loader')
    try:
        module_file = Path(namespace['__file__']).absolute()
        if module_file.is_symlink() or module_file.parent.is_symlink():
            raise CodeSealError('invalid_loader')
        canonical_file = module_file.resolve()
    except (OSError, TypeError, ValueError):
        raise CodeSealError('invalid_loader') from None
    root = _root(canonical_file.parent.parent)
    if canonical_file != root / relative:
        raise CodeSealError('invalid_loader')
    receipt = _json(_read(_file(root, RECEIPT_FILE), MAX_RECEIPT_BYTES))
    metadata, originals = receipt.get('code_seal'), receipt.get('original_files')
    if (receipt.get('app') != APP or not isinstance(metadata, dict)
            or not isinstance(metadata.get('data_root'), str)):
        raise CodeSealError('missing_receipt')
    _, pack = _validated_pack(root, metadata, originals, metadata['data_root'])
    key = _key(pack, _data_root(metadata['data_root']), None, originals, cache=True)
    source = _decrypt_source(pack, relative, key)
    if _digest(_stub(source)) != metadata['modules'][relative]['stub_sha256']:
        raise CodeSealError('stub_binding')
    try:
        code = compile(source, str(module_file), 'exec', dont_inherit=True)
    except (SyntaxError, ValueError):
        raise CodeSealError('invalid_source') from None
    exec(code, namespace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('seal-release', 'verify-release', 'discard-release'))
    parser.add_argument('--release', required=True, type=Path)
    parser.add_argument('--data-root', required=True, type=Path)
    parser.add_argument('--inventory-file', required=True, type=Path)
    parser.add_argument('--metadata-file', type=Path)
    args = parser.parse_args()
    try:
        original = _json(_read(args.inventory_file, MAX_RECEIPT_BYTES))
        if args.operation == 'seal-release':
            result = seal_release(args.release, original, args.data_root)
            print(json.dumps({'ok': True, 'code_seal': result}, ensure_ascii=False))
        else:
            if args.metadata_file is None:
                raise CodeSealError('missing_metadata')
            metadata = _json(_read(args.metadata_file, MAX_RECEIPT_BYTES))
            if args.operation == 'verify-release':
                verify_seal(args.release, metadata, original, args.data_root)
                print(json.dumps({'ok': True, 'verified': True}))
            else:
                discard_seal(args.release, metadata, original, args.data_root)
                print(json.dumps({'ok': True, 'discarded': True}))
        return 0
    except CodeSealError as exc:
        print(json.dumps({'ok': False, 'error': exc.diagnostic()}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
