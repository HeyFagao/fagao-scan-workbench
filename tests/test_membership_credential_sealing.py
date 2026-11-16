# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Account lifecycle uses OS-sealed credentials without altering account data."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fagao.credential_store import CredentialStoreError
from fagao.membership import MembershipClient
from fagao.store import AppError
from tests.test_membership import Clock, Transport, decision


class SyntheticProtector:
    """In-memory protected references, never a native keychain or valid account."""
    def __init__(self):
        self.items = {}
        self.counter = 0
        self.locked = False
        self.deleted = []

    def seal(self, secret):
        if self.locked: raise CredentialStoreError('locked')
        self.counter += 1
        reference = str(self.counter)
        self.items[reference] = bytes(secret)
        return {'version': 1, 'provider': 'synthetic', 'reference': reference}

    def open(self, envelope):
        if self.locked: raise CredentialStoreError('locked')
        try: return self.items[envelope['reference']]
        except KeyError: raise CredentialStoreError('missing') from None

    def discard(self, envelope):
        self.deleted.append(envelope['reference'])
        self.items.pop(envelope['reference'], None)


class CredentialSessionTests(unittest.TestCase):
    redirect = 'http://127.0.0.1:18376/auth/callback'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = Clock()
        self.transport = Transport(self.clock)
        self.protector = SyntheticProtector()
        self.client = MembershipClient(self.root, self.transport, self.clock, credential_store=self.protector)
        self.path = self.root / '.membership/session.json'

    def authorize(self):
        start = self.client.begin_authorization(self.redirect)
        return self.client.complete_authorization('one-use-code', start['state'], self.redirect)

    def raw(self):
        return json.loads(self.path.read_text())

    def test_new_authorization_is_sealed_and_another_client_recovers_same_token(self):
        status = self.authorize()
        self.assertTrue(status['allowed'])
        self.assertEqual(status['credential_storage'],
                         {'protected': True, 'provider': 'synthetic', 'legacy_plaintext': False})
        self.assertNotIn('reference', json.dumps(status['credential_storage']))
        raw = self.raw()
        self.assertIsNone(raw['device_token'])
        self.assertIn('protected_device_token', raw)
        self.assertNotIn(self.transport.token, self.path.read_text())
        peer = MembershipClient(self.root, self.transport, self.clock, credential_store=self.protector)
        self.assertTrue(peer.status()['allowed'])
        self.assertEqual(self.transport.calls[-1]['token'], self.transport.token)
        self.assertEqual(len(self.protector.items), 1)

    def test_pending_authorization_and_same_token_writes_reuse_the_reference(self):
        self.authorize()
        reference = self.raw()['protected_device_token']
        start = self.client.begin_authorization(self.redirect)
        self.assertEqual(self.raw()['protected_device_token'], reference)
        self.client.cancel_authorization(start['state'], self.redirect)
        self.assertEqual(self.raw()['protected_device_token'], reference)
        self.assertEqual(len(self.protector.items), 1)

    def test_rotation_commits_new_reference_before_deleting_only_old_one(self):
        self.authorize()
        previous = self.raw()['protected_device_token']
        replacement = 'synthetic-rotated-' + 'a' * 40
        self.transport.override = {**decision(self.clock), 'scope': 'workbench', 'device_token': replacement}
        self.assertTrue(self.client.status(refresh=True)['allowed'])
        current = self.raw()['protected_device_token']
        self.assertNotEqual(current, previous)
        self.assertNotIn(previous['reference'], self.protector.items)
        self.assertEqual(self.protector.open(current), replacement.encode())
        self.assertEqual(len(self.protector.items), 1)

    def test_atomic_write_failure_preserves_old_state_and_old_reference(self):
        self.authorize()
        before = self.path.read_bytes()
        previous = self.raw()['protected_device_token']
        with self.client._state_lock():
            value = self.client._read()
            value['device_token'] = 'synthetic-replacement-' + 'b' * 40
            with patch('fagao.membership.os.replace', side_effect=OSError('fixture failure')):
                with self.assertRaises(AppError): self.client._save(value)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.protector.open(previous), self.transport.token.encode())
        self.assertEqual(len(self.protector.items), 1)
        self.assertFalse(list((self.root / '.membership').glob('.session-*')))

    def test_legacy_plaintext_is_sealed_before_renewal_can_rotate_it(self):
        state = self.raw()
        state.update(account={'id': 'account-a', 'display_name': 'Synthetic Member'}, device_token=self.transport.token)
        self.path.write_text(json.dumps(state))
        self.assertTrue(self.client.status(refresh=True)['allowed'])
        self.assertNotIn(self.transport.token, self.path.read_text())
        self.assertEqual(self.transport.calls[-1]['token'], self.transport.token)
        self.assertEqual(self.raw()['account']['id'], 'account-a')

    def test_failed_legacy_sealing_does_not_rewrite_original_or_contact_website(self):
        state = self.raw()
        state.update(account={'id': 'account-a', 'display_name': 'Synthetic Member'}, device_token=self.transport.token)
        self.path.write_text(json.dumps(state))
        before = self.path.read_bytes()
        self.protector.locked = True
        result = self.client.status(refresh=True)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['reason'], 'verification_unavailable')
        self.assertEqual(result['credential_storage_issue'], 'membership_credential_locked')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.transport.calls, [])

    def test_missing_protected_record_keeps_state_and_does_not_create_new_secret(self):
        self.authorize()
        before = self.path.read_bytes()
        self.protector.items.clear()
        calls = len(self.transport.calls)
        result = self.client.status(refresh=True)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['credential_storage_issue'], 'membership_credential_missing')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual(self.protector.items, {})

    def test_locked_store_preflight_preserves_pending_code_before_any_exchange(self):
        start = self.client.begin_authorization(self.redirect)
        before = self.path.read_bytes()
        self.protector.locked = True
        with self.assertRaises(AppError) as caught:
            self.client.complete_authorization('one-use-code', start['state'], self.redirect)
        self.assertEqual(caught.exception.code, 'membership_credential_locked')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.transport.calls, [])

    def test_logout_deletes_only_current_reference_after_local_identity_is_cleared(self):
        self.authorize()
        reference = self.raw()['protected_device_token']['reference']
        self.protector.items['foreign'] = b'not-a-workbench-secret'
        self.assertFalse(self.client.logout()['allowed'])
        self.assertIsNone(self.raw()['account'])
        self.assertNotIn('protected_device_token', self.raw())
        self.assertNotIn(reference, self.protector.items)
        self.assertEqual(self.protector.items['foreign'], b'not-a-workbench-secret')
        self.assertEqual(self.transport.calls[-1]['name'], 'revoke')

    def test_storage_lock_after_exchange_revokes_uncommitted_website_token(self):
        start = self.client.begin_authorization(self.redirect)
        def lock_store(*args):
            self.protector.locked = True
            return {'device_token': self.transport.token, 'scope': 'workbench',
                    'status': decision(self.clock)}
        self.transport.token_hook = lock_store
        with self.assertRaises(AppError) as caught:
            self.client.complete_authorization('one-use-code', start['state'], self.redirect)
        self.assertEqual(caught.exception.code, 'membership_credential_locked')
        self.assertIsNone(self.raw()['account'])
        self.assertIsNone(self.raw()['device_token'])
        self.assertNotIn(self.transport.token, self.path.read_text())
        self.assertEqual(self.transport.calls[-1]['name'], 'revoke')

    def test_storage_lock_during_rotation_is_not_reported_as_membership_expiry(self):
        self.authorize()
        before = self.path.read_bytes()
        def locked_response(*args):
            self.protector.locked = True
            return {**decision(self.clock), 'device_token': 'synthetic-rotated-' + 'c' * 40,
                    'scope': 'workbench'}
        self.transport.permit_hook = locked_response
        status = self.client.status(refresh=True)
        self.assertFalse(status['allowed'])
        self.assertEqual(status['reason'], 'verification_unavailable')
        self.assertEqual(status['credential_storage_issue'], 'membership_credential_locked')
        self.assertEqual(self.path.read_bytes(), before)

    def test_new_client_can_start_with_missing_record_but_cannot_read_history(self):
        self.authorize()
        self.protector.items.clear()
        peer = MembershipClient(self.root, self.transport, self.clock, credential_store=self.protector)
        self.assertEqual(peer.current_account_id, 'account-a')
        self.assertFalse(peer.status()['allowed'])
        with self.assertRaises(AppError): peer.require(operation='history')

    def test_explicit_reconnect_keeps_old_sealed_backup_and_uses_new_website_grant(self):
        status = self.authorize()
        before = self.path.read_bytes()
        retained = self.root / 'accounts' / 'existing-account-data' / 'report.txt'
        retained.parent.mkdir(parents=True)
        retained.write_text('original account data')
        self.protector.items.clear()
        peer = MembershipClient(self.root, self.transport, self.clock, credential_store=self.protector)
        calls = len(self.transport.calls)
        start = peer.begin_authorization(self.redirect, expected_account='account-a',
                                         expected_generation=status['generation'])
        self.assertFalse(peer.status()['allowed'])
        self.assertEqual(len(self.transport.calls), calls)
        backup, = (self.root / '.membership/credential-recovery').glob('*.json')
        self.assertEqual(backup.read_bytes(), before)
        self.assertNotIn(self.transport.token, backup.read_text())
        result = peer.complete_authorization('new-one-use-code', start['state'], self.redirect)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['account_id'], 'account-a')
        self.assertEqual(retained.read_text(), 'original account data')
        self.assertEqual(self.transport.calls[-1]['name'], 'token')

    def test_locked_new_client_does_not_silently_reset_on_connect(self):
        self.authorize()
        before = self.path.read_bytes()
        self.protector.locked = True
        peer = MembershipClient(self.root, self.transport, self.clock, credential_store=self.protector)
        self.assertEqual(peer.status()['credential_storage_issue'], 'membership_credential_locked')
        with self.assertRaises(AppError) as caught: peer.begin_authorization(self.redirect)
        self.assertEqual(caught.exception.code, 'membership_credential_locked')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root / '.membership/credential-recovery').exists())

    def test_explicit_logout_can_clear_lost_sealed_identity_without_deleting_data(self):
        self.authorize()
        before = self.path.read_bytes()
        self.protector.items.clear()
        peer = MembershipClient(self.root, self.transport, self.clock, credential_store=self.protector)
        result = peer.logout()
        self.assertFalse(result['allowed'])
        self.assertTrue(result['revocation_pending'])
        self.assertIsNone(self.raw()['account'])
        self.assertNotIn('protected_device_token', self.raw())
        backup, = (self.root / '.membership/credential-recovery').glob('*.json')
        self.assertEqual(backup.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
