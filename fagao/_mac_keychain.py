# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Store only this installation's credential records in the user's Mac keychain.

No framework is loaded and no keychain is consulted until an operation is made.
The legacy, current-user keychain API is intentional: command-line Python does
not carry the entitlements required by the data-protection keychain.  Items are
not synchronizable, and authentication UI is forbidden for every operation.
"""
from __future__ import annotations

import ctypes
import re
import secrets
import sys


SERVICE = 'com.fagao.workbench.device-credentials.v1'
MAX_SECRET_BYTES = 16 * 1024
_ACCOUNT = re.compile(r'[A-Za-z0-9._~\-]{1,200}')
_SUCCESS = 0
_DUPLICATE = -25299
_MISSING = -25300
_LOCKED = {-128, -25293, -25308}


class KeychainError(Exception):
    """A stable diagnostic which never incorporates credentials or native text."""

    def __init__(self, reason='unavailable'):
        self.reason = reason if reason in ('locked', 'unavailable', 'missing', 'conflict') else 'unavailable'
        super().__init__('Mac credential storage is ' + self.reason)


def _account(value):
    if not isinstance(value, str) or not _ACCOUNT.fullmatch(value):
        raise KeychainError('unavailable')
    return value


def _failure(status):
    if status == _MISSING:
        return KeychainError('missing')
    if status == _DUPLICATE:
        return KeychainError('conflict')
    if status in _LOCKED:
        return KeychainError('locked')
    return KeychainError('unavailable')


class MacKeychain:
    """Exact service/account operations; existing records are never overwritten."""

    def __init__(self, *, _native=None):
        self._native = _native

    def _query(self, account):
        return {'kSecClass': 'kSecClassGenericPassword',
                'kSecAttrService': SERVICE, 'kSecAttrAccount': _account(account),
                'kSecUseAuthenticationUI': 'kSecUseAuthenticationUIFail'}

    def _call(self, operation, query):
        try:
            if self._native is None:
                self._native = _NativeKeychain()
            return self._native.perform(operation, query)
        except KeychainError:
            raise
        except Exception:
            raise KeychainError('unavailable') from None

    def get(self, account):
        query = self._query(account)
        # The default query excludes synchronizable items.  Explicitly adding
        # the synchronization attribute narrows which search keys macOS permits.
        query.update(kSecReturnData=True, kSecMatchLimit='kSecMatchLimitOne')
        status, data = self._call('get', query)
        if status != _SUCCESS:
            raise _failure(status)
        if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_SECRET_BYTES:
            raise KeychainError('unavailable')
        return data

    def put(self, account, secret):
        query = self._query(account)
        if not isinstance(secret, bytes) or not 1 <= len(secret) <= MAX_SECRET_BYTES:
            raise KeychainError('unavailable')
        query.update(kSecAttrSynchronizable=False, kSecValueData=secret)
        status, _ = self._call('add', query)
        if status not in (_SUCCESS, _DUPLICATE):
            raise _failure(status)
        # SecItemAdd is insert-only.  Even a duplicate is accepted only after
        # confirming that the exact target record already contains these bytes.
        try:
            if not secrets.compare_digest(self.get(account), secret):
                raise KeychainError('conflict')
        except KeychainError:
            if status == _SUCCESS:
                # Only the record just inserted by this call belongs to its
                # rollback. A duplicate belongs to the original writer.
                try:
                    self.delete(account)
                except KeychainError:
                    pass
            raise
        return status == _SUCCESS

    def delete(self, account):
        status, _ = self._call('delete', self._query(account))
        if status not in (_SUCCESS, _MISSING):
            raise _failure(status)


class _NativeKeychain:
    """Minimal ctypes bridge to system Security/CoreFoundation frameworks."""

    def __init__(self):
        if sys.platform != 'darwin':
            raise OSError('Mac keychain is unavailable')
        self.security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        pointer = ctypes.c_void_p
        self.security.SecItemAdd.argtypes = [pointer, ctypes.POINTER(pointer)]
        self.security.SecItemAdd.restype = ctypes.c_int32
        self.security.SecItemCopyMatching.argtypes = [pointer, ctypes.POINTER(pointer)]
        self.security.SecItemCopyMatching.restype = ctypes.c_int32
        self.security.SecItemDelete.argtypes = [pointer]
        self.security.SecItemDelete.restype = ctypes.c_int32
        self.cf.CFStringCreateWithBytes.argtypes = [pointer, pointer, ctypes.c_long, ctypes.c_uint32, ctypes.c_ubyte]
        self.cf.CFStringCreateWithBytes.restype = pointer
        self.cf.CFDataCreate.argtypes = [pointer, pointer, ctypes.c_long]
        self.cf.CFDataCreate.restype = pointer
        self.cf.CFDictionaryCreate.argtypes = [pointer, ctypes.POINTER(pointer), ctypes.POINTER(pointer),
                                              ctypes.c_long, pointer, pointer]
        self.cf.CFDictionaryCreate.restype = pointer
        self.cf.CFDataGetLength.argtypes = [pointer]
        self.cf.CFDataGetLength.restype = ctypes.c_long
        self.cf.CFDataGetBytePtr.argtypes = [pointer]
        self.cf.CFDataGetBytePtr.restype = pointer
        self.cf.CFGetTypeID.argtypes = [pointer]
        self.cf.CFGetTypeID.restype = ctypes.c_ulong
        self.cf.CFDataGetTypeID.argtypes = []
        self.cf.CFDataGetTypeID.restype = ctypes.c_ulong
        self.cf.CFRelease.argtypes = [pointer]
        self.cf.CFRelease.restype = None

    def _symbol(self, library, name):
        value = ctypes.c_void_p.in_dll(library, name).value
        if not value:
            raise KeychainError('unavailable')
        return value

    def _dictionary(self, query):
        owned = []
        try:
            keys, values = [], []
            constant_values = {'kSecClass', 'kSecUseAuthenticationUI', 'kSecMatchLimit'}
            for name, value in query.items():
                keys.append(self._symbol(self.security, name))
                if name in constant_values:
                    reference = self._symbol(self.security, value)
                elif isinstance(value, bool):
                    reference = self._symbol(self.cf, 'kCFBooleanTrue' if value else 'kCFBooleanFalse')
                else:
                    data = value if isinstance(value, bytes) else value.encode('utf-8')
                    buffer = ctypes.create_string_buffer(data)
                    if isinstance(value, bytes):
                        reference = self.cf.CFDataCreate(None, buffer, len(data))
                    else:
                        reference = self.cf.CFStringCreateWithBytes(None, buffer, len(data), 0x08000100, False)
                    if not reference:
                        raise KeychainError('unavailable')
                    owned.append(reference)
                values.append(reference)
            key_callbacks = ctypes.addressof(ctypes.c_byte.in_dll(self.cf, 'kCFTypeDictionaryKeyCallBacks'))
            value_callbacks = ctypes.addressof(ctypes.c_byte.in_dll(self.cf, 'kCFTypeDictionaryValueCallBacks'))
            references = ctypes.c_void_p * len(keys)
            dictionary = self.cf.CFDictionaryCreate(None, references(*keys), references(*values), len(keys),
                                                     key_callbacks, value_callbacks)
            if not dictionary:
                raise KeychainError('unavailable')
            return dictionary, owned
        except Exception:
            for reference in reversed(owned):
                self.cf.CFRelease(reference)
            raise

    def perform(self, operation, query):
        dictionary, owned = self._dictionary(query)
        result = ctypes.c_void_p()
        try:
            data = None
            if operation == 'get':
                status = self.security.SecItemCopyMatching(dictionary, ctypes.byref(result))
                if status == _SUCCESS:
                    if not result.value or self.cf.CFGetTypeID(result) != self.cf.CFDataGetTypeID():
                        raise KeychainError('unavailable')
                    size = self.cf.CFDataGetLength(result)
                    if not 1 <= size <= MAX_SECRET_BYTES:
                        raise KeychainError('unavailable')
                    pointer = self.cf.CFDataGetBytePtr(result)
                    if not pointer:
                        raise KeychainError('unavailable')
                    data = ctypes.string_at(pointer, size)
            elif operation == 'add':
                status = self.security.SecItemAdd(dictionary, None)
            elif operation == 'delete':
                status = self.security.SecItemDelete(dictionary)
            else:
                raise KeychainError('unavailable')
            return int(status), data
        finally:
            if result.value:
                self.cf.CFRelease(result)
            self.cf.CFRelease(dictionary)
            for reference in reversed(owned):
                self.cf.CFRelease(reference)
