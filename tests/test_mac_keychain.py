# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Exact, non-interactive keychain scope and ownership without touching a keychain."""
import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fagao import _mac_keychain as storage


class MemoryNative:
    def __init__(self):
        self.items = {}
        self.calls = []
        self.failure = None

    def perform(self, operation, query):
        self.calls.append((operation, dict(query)))
        if self.failure is not None:
            return self.failure, None
        account = query['kSecAttrAccount']
        if operation == 'add':
            if account in self.items:
                return -25299, None
            self.items[account] = query['kSecValueData']
            return 0, None
        if account not in self.items:
            return -25300, None
        if operation == 'delete':
            self.items.pop(account)
            return 0, None
        return 0, self.items[account]


class MacKeychainTests(unittest.TestCase):
    def setUp(self):
        self.native = MemoryNative()
        self.keychain = storage.MacKeychain(_native=self.native)

    def test_framework_load_is_lazy_and_an_explicit_backend_never_loads_it(self):
        with patch.object(storage, '_NativeKeychain', side_effect=AssertionError('No native access')):
            storage.MacKeychain()
            self.keychain.put('credential-1', b'synthetic-device-token')
            self.assertEqual(self.keychain.get('credential-1'), b'synthetic-device-token')

    def test_write_readback_and_get_use_only_the_pinned_service_and_account(self):
        self.keychain.put('credential-1', b'new-token')
        self.assertEqual([operation for operation, _ in self.native.calls], ['add', 'get'])
        for operation, query in self.native.calls:
            self.assertEqual(query['kSecClass'], 'kSecClassGenericPassword')
            self.assertEqual(query['kSecAttrService'], storage.SERVICE)
            self.assertEqual(query['kSecAttrAccount'], 'credential-1')
            self.assertEqual(query['kSecUseAuthenticationUI'], 'kSecUseAuthenticationUIFail')
            self.assertNotIn('kSecAttrAccessGroup', query)
            self.assertNotIn('kSecUseDataProtectionKeychain', query)
        addition = self.native.calls[0][1]
        self.assertIs(addition['kSecAttrSynchronizable'], False)
        read = self.native.calls[1][1]
        self.assertEqual(read['kSecMatchLimit'], 'kSecMatchLimitOne')
        self.assertIs(read['kSecReturnData'], True)

    def test_identical_duplicate_is_verified_but_existing_different_secret_is_never_overwritten(self):
        self.keychain.put('credential-1', b'original-token')
        self.keychain.put('credential-1', b'original-token')
        with self.assertRaises(storage.KeychainError) as caught:
            self.keychain.put('credential-1', b'replacement-token')
        self.assertEqual(caught.exception.reason, 'conflict')
        self.assertEqual(self.native.items['credential-1'], b'original-token')
        self.assertFalse(any(operation == 'update' for operation, _ in self.native.calls))

    def test_delete_is_exact_and_missing_is_idempotent(self):
        self.native.items.update(first=b'first-secret', second=b'second-secret')
        self.keychain.delete('first')
        self.keychain.delete('first')
        self.assertEqual(self.native.items, {'second': b'second-secret'})
        for operation, query in self.native.calls:
            self.assertEqual(operation, 'delete')
            self.assertEqual(query['kSecAttrService'], storage.SERVICE)
            self.assertEqual(query['kSecAttrAccount'], 'first')
            self.assertNotIn('kSecMatchLimit', query)

    def test_locked_missing_and_unavailable_have_sanitized_distinct_errors(self):
        for status, reason in ((-25300, 'missing'), (-25308, 'locked'),
                               (-25293, 'locked'), (-128, 'locked'),
                               (-25291, 'unavailable'), (-34018, 'unavailable')):
            with self.subTest(status=status):
                self.native.failure = status
                with self.assertRaises(storage.KeychainError) as caught:
                    self.keychain.get('secret-record')
                self.assertEqual(caught.exception.reason, reason)
                self.assertNotIn('secret-record', str(caught.exception))

    def test_native_diagnostic_is_not_exposed_and_locked_write_does_not_fallback(self):
        for error in (OSError('sensitive user diagnostic with token'), RuntimeError('sensitive token')):
            self.native.perform = Mock(side_effect=error)
            with self.subTest(error_type=type(error)), self.assertRaises(storage.KeychainError) as caught:
                self.keychain.put('credential-1', b'sensitive-token')
            self.assertEqual(caught.exception.reason, 'unavailable')
            self.assertNotIn('sensitive', str(caught.exception))
            self.native.perform.assert_called_once()

    def test_invalid_account_or_secret_cannot_create_a_broad_or_unbounded_query(self):
        for account in ('', '*', 'bad\nname', 'x' * 201, None):
            with self.subTest(account=account), self.assertRaises(storage.KeychainError):
                self.keychain.get(account)
        for secret in ('token', b'', b'x' * (storage.MAX_SECRET_BYTES + 1), None):
            with self.subTest(secret_type=type(secret)), self.assertRaises(storage.KeychainError):
                self.keychain.put('credential-1', secret)
        self.assertEqual(self.native.calls, [])

    def test_success_without_expected_data_is_refused(self):
        for data in (None, '', b'', b'x' * (storage.MAX_SECRET_BYTES + 1)):
            self.native.perform = Mock(return_value=(0, data))
            with self.subTest(data_type=type(data)), self.assertRaises(storage.KeychainError):
                self.keychain.get('credential-1')


class NativeOwnershipTests(unittest.TestCase):
    def native(self, status=0, data=b'native-token', type_id=9, size=None):
        native = storage._NativeKeychain.__new__(storage._NativeKeychain)
        native._dictionary = Mock(return_value=(101, [201, 202]))
        buffer = ctypes.create_string_buffer(data)
        def read(dictionary, output):
            self.assertEqual(dictionary, 101)
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = 301
            return status
        native.security = SimpleNamespace(SecItemCopyMatching=Mock(side_effect=read),
                                          SecItemAdd=Mock(return_value=status),
                                          SecItemDelete=Mock(return_value=status))
        native.cf = SimpleNamespace(CFGetTypeID=Mock(return_value=type_id),
                                    CFDataGetTypeID=Mock(return_value=9),
                                    CFDataGetLength=Mock(return_value=len(data) if size is None else size),
                                    CFDataGetBytePtr=Mock(return_value=ctypes.addressof(buffer)),
                                    CFRelease=Mock())
        native._test_buffer = buffer
        return native

    def assert_released(self, native, with_result=True):
        released = [call.args[0] for call in native.cf.CFRelease.call_args_list]
        expected = [101, 202, 201]
        if with_result:
            self.assertIsInstance(released[0], ctypes.c_void_p)
            self.assertEqual(released.pop(0).value, 301)
        self.assertEqual(released, expected)

    def test_success_copies_bytes_then_releases_result_dictionary_and_owned_values(self):
        native = self.native()
        self.assertEqual(native.perform('get', {}), (0, b'native-token'))
        self.assert_released(native)

    def test_failure_releases_any_returned_object_without_reading_its_bytes(self):
        native = self.native(status=-25308)
        self.assertEqual(native.perform('get', {}), (-25308, None))
        native.cf.CFDataGetBytePtr.assert_not_called()
        self.assert_released(native)

    def test_unexpected_type_or_unbounded_data_is_not_dereferenced_and_still_released(self):
        for arguments in ({'type_id': 4}, {'size': storage.MAX_SECRET_BYTES + 1}, {'size': -1}):
            native = self.native(**arguments)
            with self.subTest(arguments=arguments), self.assertRaises(storage.KeychainError):
                native.perform('get', {})
            native.cf.CFDataGetBytePtr.assert_not_called()
            self.assert_released(native)

    def test_add_and_delete_release_owned_cf_values_and_do_not_request_secret_results(self):
        for operation in ('add', 'delete'):
            native = self.native()
            self.assertEqual(native.perform(operation, {}), (0, None))
            self.assert_released(native, with_result=False)
            if operation == 'add':
                native.security.SecItemAdd.assert_called_once_with(101, None)
            else:
                native.security.SecItemDelete.assert_called_once_with(101)


if __name__ == '__main__':
    unittest.main()
