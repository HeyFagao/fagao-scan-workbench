# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Seal only this application's secrets with the current operating-system user.

This is local at-rest protection, not an authorization decision. No website
permit, membership expiry, or account data is created by this module.
"""
from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import struct
import sys
import time

from ._vendor.pyaes import AESModeOfOperationCBC

MAX_SECRET_BYTES = 8192
MAX_FERNET_BYTES = 32 * 1024 * 1024


class CredentialStoreError(Exception):
    def __init__(self, reason='unavailable'):
        self.reason = reason
        messages = {
            'locked': '系统钥匙串尚未解锁，请解锁后重试；已有资料仍保留。',
            'missing': '本机保护凭据已丢失，请让维护助手恢复原系统凭据或重新连接账号；已有资料仍保留。',
            'wrong_device': '当前系统用户无法解封这份本机凭据，请使用原系统账号或重新连接；已有资料仍保留。',
            'integrity': '本机保护凭据校验失败，请让维护助手检查；已有资料仍保留。',
            'conflict': '本机保护凭据发生冲突，请让维护助手检查；已有资料仍保留。',
        }
        super().__init__(messages.get(reason, '系统安全存储暂时不可用，请让维护助手检查后重试；已有资料仍保留。'))


def _decode64(value):
    try:
        if not isinstance(value, bytes):
            raise ValueError
        decoded = base64.b64decode(value, altchars=b'-_', validate=True)
        if base64.urlsafe_b64encode(decoded) != value:
            raise ValueError
        return decoded
    except (ValueError, binascii.Error):
        raise CredentialStoreError('integrity') from None


def fernet_encrypt(key32, plaintext):
    """Standard Fernet v0x80: AES-128-CBC and HMAC-SHA256, with random IV."""
    if not isinstance(key32, bytes) or len(key32) != 32 or not isinstance(plaintext, bytes) or len(plaintext) > MAX_FERNET_BYTES:
        raise CredentialStoreError('integrity')
    iv = secrets.token_bytes(16)
    pad = 16 - len(plaintext) % 16
    padded = plaintext + bytes([pad]) * pad
    encryptor = AESModeOfOperationCBC(key32[16:], iv=iv)
    encrypted = b''.join(encryptor.encrypt(padded[pos:pos + 16]) for pos in range(0, len(padded), 16))
    content = b'\x80' + struct.pack('>Q', int(time.time())) + iv + encrypted
    return base64.urlsafe_b64encode(content + hmac.digest(key32[:16], content, 'sha256'))


def fernet_decrypt(key32, token):
    if not isinstance(key32, bytes) or len(key32) != 32 or not isinstance(token, bytes) or not 100 <= len(token) <= (MAX_FERNET_BYTES + 100) * 2:
        raise CredentialStoreError('integrity')
    decoded = _decode64(token)
    if len(decoded) < 73 or decoded[0] != 0x80 or (len(decoded) - 57) % 16:
        raise CredentialStoreError('integrity')
    if not hmac.compare_digest(hmac.digest(key32[:16], decoded[:-32], 'sha256'), decoded[-32:]):
        raise CredentialStoreError('integrity')
    decryptor = AESModeOfOperationCBC(key32[16:], iv=decoded[9:25])
    result = b''.join(decryptor.decrypt(decoded[pos:pos + 16]) for pos in range(25, len(decoded) - 32, 16))
    pad = result[-1]
    if not 1 <= pad <= 16 or not hmac.compare_digest(result[-pad:], bytes([pad]) * pad):
        raise CredentialStoreError('integrity')
    return result[:-pad]


class _WindowsDPAPI:
    UI_FORBIDDEN = 0x1

    class Blob(ctypes.Structure):
        _fields_ = [('cbData', ctypes.c_uint32), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

    def __init__(self):
        if sys.platform != 'win32':
            raise CredentialStoreError('unavailable')
        try:
            self.crypt = ctypes.WinDLL('crypt32', use_last_error=True)
            self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            blob = ctypes.POINTER(self.Blob)
            self.crypt.CryptProtectData.argtypes = [blob, ctypes.c_wchar_p, blob, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, blob]
            self.crypt.CryptProtectData.restype = ctypes.c_int
            self.crypt.CryptUnprotectData.argtypes = [blob, ctypes.c_void_p, blob, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, blob]
            self.crypt.CryptUnprotectData.restype = ctypes.c_int
            self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
            self.kernel.LocalFree.restype = ctypes.c_void_p
        except (AttributeError, OSError):
            raise CredentialStoreError('unavailable') from None

    def _blob(self, data):
        backing = ctypes.create_string_buffer(data)
        return self.Blob(len(data), ctypes.cast(backing, ctypes.POINTER(ctypes.c_ubyte))), backing

    def _apply(self, data, entropy, decrypt=False):
        source, keep_source = self._blob(data)
        context, keep_context = self._blob(entropy)
        result = self.Blob()
        try:
            operation = self.crypt.CryptUnprotectData if decrypt else self.crypt.CryptProtectData
            description = None if decrypt else 'Fagao Workbench local secret'
            success = operation(ctypes.byref(source), description, ctypes.byref(context), None, None,
                                self.UI_FORBIDDEN, ctypes.byref(result))
            if not success:
                raise CredentialStoreError('wrong_device' if decrypt else 'unavailable')
            if not 1 <= result.cbData <= MAX_SECRET_BYTES * 4:
                raise CredentialStoreError('integrity')
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            if result.pbData:
                self.kernel.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))

    def protect(self, secret, context):
        return self._apply(secret, context)

    def unprotect(self, secret, context):
        return self._apply(secret, context, decrypt=True)


class SecretProtector:
    """Use a stable data root and distinct purpose, never a changing release path.

    ``native=False`` is a constructor-only dependency for isolated transports and
    test fixtures. It never disables website authorization. macOS/Windows native
    failures do not silently fall back to a local key file.
    """
    def __init__(self, root_dir, *, purpose, native=True, backend=None):
        self.root = Path(root_dir).expanduser().resolve()
        if not isinstance(purpose, str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,79}', purpose):
            raise ValueError('Invalid local secret purpose')
        self.purpose = purpose
        scope = os.path.normcase(str(self.root))
        self.binding = hashlib.sha256(('fagao-secret-v1\0' + scope + '\0' + purpose).encode('utf-8')).hexdigest()
        self.provider = ('macos-keychain' if sys.platform == 'darwin' else 'windows-dpapi' if sys.platform == 'win32' else 'file-private') if native else 'file-private'
        self._backend = backend

    def _native(self):
        if self._backend is None:
            if self.provider == 'windows-dpapi':
                self._backend = _WindowsDPAPI()
            elif self.provider == 'macos-keychain':
                from ._mac_keychain import MacKeychain
                self._backend = MacKeychain()
        return self._backend

    def _payload(self, secret):
        return json.dumps({'purpose': self.purpose, 'binding': self.binding,
                           'secret': base64.urlsafe_b64encode(secret).decode('ascii')},
                          sort_keys=True, separators=(',', ':')).encode('utf-8')

    def _unpack(self, payload):
        try:
            result = json.loads(payload.decode('utf-8'))
            if (not isinstance(result, dict) or set(result) != {'purpose', 'binding', 'secret'}
                    or result['purpose'] != self.purpose or result['binding'] != self.binding):
                raise ValueError
            secret = _decode64(result['secret'].encode('ascii'))
            if not 1 <= len(secret) <= MAX_SECRET_BYTES:
                raise ValueError
            return secret
        except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
            raise CredentialStoreError('integrity') from None

    def _private_key(self, create):
        directory = self.root / '.membership' / 'protected-keys'
        path = directory / (hashlib.sha256(self.purpose.encode()).hexdigest() + '.key')
        try:
            if self.root.is_symlink() or (self.root / '.membership').is_symlink() or directory.is_symlink() or path.is_symlink():
                raise OSError
            if create:
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(directory, 0o700)
            try:
                fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            except FileNotFoundError:
                if not create:
                    raise CredentialStoreError('missing') from None
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                except FileExistsError:
                    return self._private_key(False)
                with os.fdopen(fd, 'wb') as output:
                    output.write(secrets.token_bytes(32)); output.flush(); os.fsync(output.fileno())
                fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != 32 or (os.name != 'nt' and info.st_mode & 0o077):
                    raise OSError
                key = handle.read(33)
            if len(key) != 32:
                raise OSError
            return key
        except CredentialStoreError:
            raise
        except OSError:
            raise CredentialStoreError('unavailable') from None

    def seal(self, secret):
        if not isinstance(secret, bytes) or not 1 <= len(secret) <= MAX_SECRET_BYTES:
            raise CredentialStoreError('integrity')
        envelope = {'version': 1, 'provider': self.provider, 'purpose': self.purpose, 'binding': self.binding}
        payload = self._payload(secret)
        created_reference = False
        try:
            if self.provider == 'macos-keychain':
                reference = self.binding + '.' + secrets.token_hex(24)
                envelope['reference'] = reference
                created = self._native().put(reference, payload)
                if created is False:
                    raise CredentialStoreError('conflict')
                created_reference = created is True
            elif self.provider == 'windows-dpapi':
                envelope['blob'] = base64.urlsafe_b64encode(self._native().protect(payload, self.binding.encode('ascii'))).decode('ascii')
            else:
                envelope['blob'] = fernet_encrypt(self._private_key(True), payload).decode('ascii')
            if not hmac.compare_digest(self.open(envelope), secret):
                raise CredentialStoreError('integrity')
            return envelope
        except CredentialStoreError:
            if created_reference:
                self.discard(envelope)
            raise
        except Exception as exc:
            if created_reference:
                self.discard(envelope)
            reason = getattr(exc, 'reason', 'unavailable')
            raise CredentialStoreError(reason if reason in {'locked', 'missing', 'conflict'} else 'unavailable') from None

    def open(self, envelope):
        expected = {'version', 'provider', 'purpose', 'binding', 'reference' if self.provider == 'macos-keychain' else 'blob'}
        if (not isinstance(envelope, dict) or set(envelope) != expected
                or type(envelope.get('version')) is not int or envelope.get('version') != 1
                or envelope.get('provider') != self.provider or envelope.get('purpose') != self.purpose
                or envelope.get('binding') != self.binding):
            raise CredentialStoreError('wrong_device')
        try:
            if self.provider == 'macos-keychain':
                reference = envelope.get('reference')
                if not isinstance(reference, str) or not re.fullmatch(self.binding + r'\.[a-f0-9]{48}', reference):
                    raise CredentialStoreError('integrity')
                payload = self._native().get(reference)
            else:
                blob = envelope.get('blob')
                if not isinstance(blob, str) or not 1 <= len(blob) <= MAX_SECRET_BYTES * 6:
                    raise CredentialStoreError('integrity')
                if self.provider == 'windows-dpapi':
                    payload = self._native().unprotect(_decode64(blob.encode('ascii')), self.binding.encode('ascii'))
                else:
                    payload = fernet_decrypt(self._private_key(False), blob.encode('ascii'))
            return self._unpack(payload)
        except CredentialStoreError:
            raise
        except Exception as exc:
            reason = getattr(exc, 'reason', 'unavailable')
            raise CredentialStoreError(reason if reason in {'locked', 'missing', 'conflict'} else 'unavailable') from None

    def discard(self, envelope, *, strict=False):
        # Native keychain items are immutable per sealed secret; delete only the
        # exact namespace/reference produced by this protector. Disk blobs have
        # no additional secret record, and shared file keys must remain intact.
        expected = {'version', 'provider', 'purpose', 'binding', 'reference' if self.provider == 'macos-keychain' else 'blob'}
        if (not isinstance(envelope, dict)
                or envelope.get('provider') != self.provider or envelope.get('purpose') != self.purpose
                or envelope.get('binding') != self.binding):
            if strict:
                raise CredentialStoreError('wrong_device')
            return
        if strict and (set(envelope) != expected or type(envelope.get('version')) is not int
                       or envelope.get('version') != 1):
            raise CredentialStoreError('integrity')
        if self.provider != 'macos-keychain':
            # DPAPI ciphertext is self-contained. File-private callers share a
            # purpose key, so discarding one envelope must never remove it.
            return
        reference = envelope.get('reference')
        if not isinstance(reference, str) or not re.fullmatch(self.binding + r'\.[a-f0-9]{48}', reference):
            if strict:
                raise CredentialStoreError('integrity')
            return
        try:
            self._native().delete(reference)
        except Exception as exc:
            if strict:
                reason = getattr(exc, 'reason', 'unavailable')
                if reason == 'missing':
                    return
                raise CredentialStoreError(reason if reason in {'locked', 'conflict'} else 'unavailable') from None
