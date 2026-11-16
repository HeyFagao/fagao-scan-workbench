# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Local secret protection: standard format, OS boundary, and non-destructive errors."""
import base64
import ctypes
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from fagao.credential_store import (CredentialStoreError, SecretProtector, _WindowsDPAPI,
                                     fernet_decrypt, fernet_encrypt)


class LocalSecretTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_fernet_matches_the_official_public_specification_vector(self):
        # Public interoperability fixture from github.com/fernet/spec/generate.json.
        key = base64.urlsafe_b64decode('cw_0x689RpI-jtRR7oE8h_eQsKImvJapLeSbXpwF4e4=')
        token = b'gAAAAAAdwJ6wAAECAwQFBgcICQoLDA0ODy021cpGVWKZ_eEwCGM4BLLF_5CV9dOPmrhuVUPgJobwOz7JcbmrR64jVmpU4IwqDA=='
        self.assertEqual(fernet_decrypt(key, token), b'hello')
        timestamp = int.from_bytes(base64.urlsafe_b64decode(token)[1:9], 'big')
        with patch('fagao.credential_store.secrets.token_bytes', return_value=bytes(range(16))), \
                patch('fagao.credential_store.time.time', return_value=timestamp):
            self.assertEqual(fernet_encrypt(key, b'hello'), token)

    def test_fernet_rejects_tampered_mac_before_decryption(self):
        key = bytes(range(32))
        token = bytearray(base64.urlsafe_b64decode(fernet_encrypt(key, b'fixture')))
        token[30] ^= 1
        with patch('fagao.credential_store.AESModeOfOperationCBC', side_effect=AssertionError('must not decrypt')):
            with self.assertRaises(CredentialStoreError):
                fernet_decrypt(key, base64.urlsafe_b64encode(token))

    def test_private_file_provider_roundtrip_without_token_plaintext(self):
        first = SecretProtector(self.root, purpose='membership-device-v1', native=False)
        secret = b'fixture-secret-for-local-storage'
        envelope = first.seal(secret)
        self.assertEqual(envelope['provider'], 'file-private')
        self.assertNotIn(secret.decode(), str(envelope))
        second = SecretProtector(self.root, purpose='membership-device-v1', native=False)
        self.assertEqual(second.open(envelope), secret)
        for path in (self.root / '.membership/protected-keys').iterdir():
            self.assertEqual(path.stat().st_size, 32)
            if os.name != 'nt': self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_wrong_root_purpose_or_provider_never_creates_replacement_key(self):
        source = SecretProtector(self.root, purpose='membership-device-v1', native=False)
        envelope = source.seal(b'fixture')
        other_root = self.root / 'other'
        for target in (SecretProtector(other_root, purpose='membership-device-v1', native=False),
                       SecretProtector(self.root, purpose='code-seal-v1', native=False)):
            with self.assertRaises(CredentialStoreError): target.open(envelope)
        self.assertFalse(other_root.exists())

    def test_missing_private_key_fails_without_regeneration(self):
        protector = SecretProtector(self.root, purpose='fixture-v1', native=False)
        envelope = protector.seal(b'fixture')
        key, = (self.root / '.membership/protected-keys').glob('*.key')
        key.unlink()
        with self.assertRaises(CredentialStoreError) as caught:
            protector.open(envelope)
        self.assertEqual(caught.exception.reason, 'missing')
        self.assertFalse(key.exists())

    def test_native_macos_uses_only_opaque_own_reference_and_precise_delete(self):
        backend = Mock()
        rows = {}
        backend.put.side_effect = lambda reference, payload: rows.setdefault(reference, payload)
        backend.get.side_effect = rows.__getitem__
        with patch('fagao.credential_store.sys.platform', 'darwin'):
            protector = SecretProtector(self.root, purpose='fixture-v1', backend=backend)
            envelope = protector.seal(b'fixture')
            self.assertEqual(envelope['provider'], 'macos-keychain')
            self.assertNotIn('blob', envelope)
            self.assertEqual(protector.open(envelope), b'fixture')
            protector.discard({**envelope, 'reference': 'foreign.reference'})
            backend.delete.assert_not_called()
            protector.discard(envelope)
            backend.delete.assert_called_once_with(envelope['reference'])
        self.assertFalse((self.root / '.membership').exists())

    def test_native_failure_is_sanitized_and_has_no_private_file_fallback(self):
        backend = Mock()
        backend.put.side_effect = RuntimeError('password=fixture-secret')
        with patch('fagao.credential_store.sys.platform', 'darwin'):
            protector = SecretProtector(self.root, purpose='fixture-v1', backend=backend)
            with self.assertRaises(CredentialStoreError) as caught: protector.seal(b'fixture')
            self.assertNotIn('fixture-secret', str(caught.exception))
        self.assertFalse((self.root / '.membership').exists())

    def test_strict_discard_reports_lock_and_keeps_an_explicit_retry_reference(self):
        from fagao._mac_keychain import KeychainError
        backend = Mock()
        backend.get.return_value = b'unused'
        with patch('fagao.credential_store.sys.platform', 'darwin'):
            protector = SecretProtector(self.root, purpose='fixture-v1', backend=backend)
            envelope = {'version': 1, 'provider': 'macos-keychain', 'purpose': protector.purpose,
                        'binding': protector.binding, 'reference': protector.binding + '.' + 'a' * 48}
            backend.delete.side_effect = KeychainError('locked')
            with self.assertRaises(CredentialStoreError) as caught:
                protector.discard(envelope, strict=True)
            self.assertEqual(caught.exception.reason, 'locked')
            self.assertNotIn(envelope['reference'], str(caught.exception))
            # Original best-effort callers remain non-blocking.
            protector.discard(envelope)
            backend.delete.side_effect = KeychainError('missing')
            protector.discard(envelope, strict=True)

    def test_strict_discard_rejects_foreign_scope_without_touching_backend(self):
        backend = Mock()
        with patch('fagao.credential_store.sys.platform', 'darwin'):
            protector = SecretProtector(self.root, purpose='fixture-v1', backend=backend)
            envelope = {'version': 1, 'provider': 'macos-keychain', 'purpose': protector.purpose,
                        'binding': protector.binding, 'reference': 'foreign.reference'}
            with self.assertRaises(CredentialStoreError): protector.discard(envelope, strict=True)
            with self.assertRaises(CredentialStoreError): protector.discard({**envelope, 'binding': 'other'}, strict=True)
            backend.delete.assert_not_called()

    def test_strict_discard_of_private_envelope_keeps_the_shared_purpose_key(self):
        protector = SecretProtector(self.root, purpose='fixture-v1', native=False)
        first = protector.seal(b'first')
        second = protector.seal(b'second')
        protector.discard(first, strict=True)
        self.assertEqual(protector.open(second), b'second')

    def test_mac_reference_conflict_never_deletes_the_previous_writers_record(self):
        from fagao._mac_keychain import MacKeychain
        from tests.test_mac_keychain import MemoryNative
        native = MemoryNative()
        backend = MacKeychain(_native=native)
        with patch('fagao.credential_store.sys.platform', 'darwin'), \
                patch('fagao.credential_store.secrets.token_hex', return_value='a' * 48):
            protector = SecretProtector(self.root, purpose='fixture-v1', backend=backend)
            first = protector.seal(b'first')
            with self.assertRaises(CredentialStoreError) as caught:
                protector.seal(b'second')
            self.assertEqual(caught.exception.reason, 'conflict')
            self.assertEqual(protector.open(first), b'first')

    def test_dpapi_envelope_binds_current_user_to_local_root_and_purpose(self):
        backend = Mock()
        rows = {}
        def protect(payload, entropy):
            rows[b'opaque'] = (payload, entropy)
            return b'opaque'
        backend.protect.side_effect = protect
        backend.unprotect.side_effect = lambda blob, entropy: rows[blob][0] if rows[blob][1] == entropy else b'bad'
        with patch('fagao.credential_store.sys.platform', 'win32'):
            protector = SecretProtector(self.root, purpose='fixture-v1', backend=backend)
            envelope = protector.seal(b'fixture')
            self.assertEqual(envelope['provider'], 'windows-dpapi')
            self.assertEqual(protector.open(envelope), b'fixture')
            self.assertEqual(backend.protect.call_args.args[1], protector.binding.encode('ascii'))
        self.assertFalse((self.root / '.membership').exists())

    def test_dpapi_native_bridge_forbids_ui_and_does_not_use_machine_scope(self):
        native = _WindowsDPAPI.__new__(_WindowsDPAPI)
        held = []
        def protect(source, description, entropy, reserved, prompt, flags, output):
            self.assertEqual(flags, 1)
            self.assertIsNone(reserved); self.assertIsNone(prompt)
            self.assertEqual(ctypes.string_at(source._obj.pbData, source._obj.cbData), b'fixture')
            self.assertEqual(ctypes.string_at(entropy._obj.pbData, entropy._obj.cbData), b'context')
            buffer = ctypes.create_string_buffer(b'opaque'); held.append(buffer)
            output._obj.cbData = 6
            output._obj.pbData = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
            return 1
        native.crypt = Mock(); native.kernel = Mock()
        native.crypt.CryptProtectData.side_effect = protect
        self.assertEqual(native.protect(b'fixture', b'context'), b'opaque')
        native.kernel.LocalFree.assert_called_once()
        self.assertEqual(ctypes.sizeof(_WindowsDPAPI.Blob._fields_[0][1]), 4)

    @unittest.skipUnless(sys.platform == 'win32' and os.environ.get('FAGAO_SYNTHETIC_NATIVE_CREDENTIALS') == '1',
                         'explicit Windows synthetic credential verification')
    def test_native_windows_dpapi_roundtrip_uses_only_synthetic_bytes(self):
        protector = SecretProtector(self.root, purpose='native-fixture-v1')
        secret = b'synthetic-native-storage-test-only'
        envelope = protector.seal(secret)
        self.assertEqual(envelope['provider'], 'windows-dpapi')
        self.assertNotIn(secret.decode(), str(envelope))
        second = SecretProtector(self.root, purpose='native-fixture-v1')
        self.assertEqual(second.open(envelope), secret)
        encoded = bytearray(base64.urlsafe_b64decode(envelope['blob']))
        encoded[-1] ^= 1
        with self.assertRaises(CredentialStoreError):
            second.open({**envelope, 'blob': base64.urlsafe_b64encode(encoded).decode('ascii')})


if __name__ == '__main__':
    unittest.main()
