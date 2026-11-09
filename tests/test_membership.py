"""Synthetic authorization tests: no production website or customer data."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

from fagao import membership, network
from fagao.membership import MembershipClient
from fagao.store import AppError


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


class Clock:
    def __init__(self):
        self.wall = 1800000000.0
        self.mono = 10.0

    def time(self):
        return self.wall

    def monotonic(self):
        return self.mono

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds


def decision(clock, account_id="account-a", allowed=True, reason=None, member_seconds=3600, permit_seconds=900):
    return {"allowed": allowed, "reason": reason or ("active" if allowed else "expired"),
            "account": {"id": account_id, "display_name": "合成会员"},
            "membership_expires_at": iso(clock.time() + member_seconds),
            "permit_expires_at": iso(clock.time() + min(permit_seconds, member_seconds)) if allowed else None,
            "server_time": iso(clock.time()), "policy_version": "synthetic-unapproved-v1"}


def experimental_decision(clock, membership_end=None, permit_seconds=900):
    return {**decision(clock), "access_basis": "experimental_account",
            "membership_expires_at": membership_end,
            "permit_expires_at": iso(clock.time() + permit_seconds),
            "identity_expires_at": iso(clock.time() + 3600)}


class Transport:
    def __init__(self, clock):
        self.clock = clock
        self.account_id = "account-a"
        self.token = "synthetic-device-token-" + "a" * 32
        self.calls = []
        self.failure = None
        self.override = None
        self.permit_hook = None
        self.token_hook = None
        self.permit_seconds = 900
        self.member_seconds = 3600

    def __call__(self, method, url, payload, token):
        name = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
        self.calls.append({"method": method, "name": name, "payload": deepcopy(payload), "token": token})
        if self.failure:
            raise self.failure
        if name == "revoke":
            return {"revoked": True}
        if name == "permit" and self.permit_hook:
            return self.permit_hook(method, url, payload, token)
        if name == "token" and self.token_hook:
            return self.token_hook(method, url, payload, token)
        result = deepcopy(self.override) if self.override is not None else decision(
            self.clock, self.account_id, member_seconds=self.member_seconds, permit_seconds=self.permit_seconds)
        if name == "token":
            return {"device_token": self.token, "scope": "workbench", "status": result}
        return result


def rotation_worker(root, queue):
    """Two real processes share a synthetic rotating credential endpoint."""
    clock = Clock()
    remote_file = Path(root) / "synthetic-remote-counter"

    def transport(method, url, payload, token):
        count = int(remote_file.read_text())
        expected = "synthetic-rotating-token-" + str(count).zfill(32)
        time.sleep(0.08)
        if token != expected or int(remote_file.read_text()) != count:
            return decision(clock, allowed=False, reason="device_revoked")
        remote_file.write_text(str(count + 1))
        result = decision(clock)
        result.update(device_token="synthetic-rotating-token-" + str(count + 1).zfill(32), scope="workbench")
        return result

    try:
        client = MembershipClient(root, transport=transport, clock=clock)
        queue.put(client.status(refresh=True)["allowed"])
    except Exception:
        queue.put(False)


class MembershipTests(unittest.TestCase):
    redirect = "http://127.0.0.1:13876/auth/callback"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="membership-中文-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = Clock()
        self.transport = Transport(self.clock)
        self.client = MembershipClient(self.root, transport=self.transport, clock=self.clock)

    def authorize(self, client=None):
        client = client or self.client
        start = client.begin_authorization(self.redirect)
        return client.complete_authorization("one-use-code", start["state"], self.redirect)

    def read_session(self):
        return json.loads((self.root / ".membership" / "session.json").read_text())

    def assert_denied(self, reason, client=None, **kwargs):
        with self.assertRaises(AppError) as caught:
            (client or self.client).require(**kwargs)
        self.assertEqual(caught.exception.code, "membership_" + reason)
        return caught.exception

    def test_fresh_install_is_locked_without_network_or_account_data(self):
        status = self.client.status()
        self.assertFalse(status["allowed"])
        self.assertEqual(status["reason"], "not_authenticated")
        self.assertIsNone(status["account_id"])
        self.assertEqual(self.transport.calls, [])
        self.assertFalse((self.root / "accounts").exists())
        self.assert_denied("not_authenticated")

    def test_authorization_pkce_and_sanitized_response(self):
        start = self.client.begin_authorization(self.redirect)
        pending = self.read_session()["pending"]
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(start["authorization_url"]).query)
        import base64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(pending["verifier"].encode()).digest()).decode().rstrip("=")
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertNotIn(pending["verifier"], json.dumps(start))
        status = self.client.complete_authorization("one-use-code", start["state"], self.redirect)
        self.assertTrue(status["allowed"])
        self.assertEqual(status["account_id"], "account-a")
        self.assertEqual(status["account"]["id"], "account-a")
        self.assertEqual(self.transport.calls[0]["payload"]["code_verifier"], pending["verifier"])
        for forbidden in (self.transport.token, pending["verifier"], "one-use-code"):
            self.assertNotIn(forbidden, json.dumps(status))
        self.assertIsNone(self.read_session()["pending"])

    def test_state_is_single_use_and_wrong_state_does_not_exchange(self):
        start = self.client.begin_authorization(self.redirect)
        with self.assertRaises(AppError) as caught:
            self.client.complete_authorization("code", "x" * 43, self.redirect)
        self.assertEqual(caught.exception.code, "membership_invalid_callback")
        self.assertEqual(self.transport.calls, [])
        self.client.complete_authorization("code", start["state"], self.redirect)
        with self.assertRaises(AppError):
            self.client.complete_authorization("code", start["state"], self.redirect)
        self.assertEqual(len(self.transport.calls), 1)

    def test_correct_state_with_changed_callback_is_rejected(self):
        start = self.client.begin_authorization(self.redirect)
        with self.assertRaises(AppError):
            self.client.complete_authorization("code", start["state"], "http://127.0.0.1:13877/auth/callback")
        self.assertEqual(self.transport.calls, [])

    def test_authorization_expires_and_cannot_be_reused(self):
        start = self.client.begin_authorization(self.redirect)
        self.clock.advance(300)
        with self.assertRaises(AppError) as caught:
            self.client.complete_authorization("code", start["state"], self.redirect)
        self.assertEqual(caught.exception.code, "membership_authorization_expired")
        self.assertEqual(self.transport.calls, [])
        self.assertIsNone(self.read_session()["pending"])

    def test_cancelled_authorization_clears_request_preserves_existing_account(self):
        authenticated = self.authorize()
        before = self.read_session()
        start = self.client.begin_authorization(self.redirect)
        result = self.client.cancel_authorization(start["state"], self.redirect,
                                                  expected_account=authenticated["account_id"],
                                                  expected_generation=authenticated["generation"])
        self.assertTrue(result["authorization_cancelled"])
        self.assertEqual(result["authorization_status"], "authorization_cancelled")
        self.assertTrue(result["allowed"])
        self.assertIsNone(self.read_session()["pending"])
        for key in ("account", "generation", "device_token"):
            self.assertEqual(self.read_session()[key], before[key])
        with self.assertRaises(AppError):
            self.client.complete_authorization("code", start["state"], self.redirect)
        self.assertEqual(len(self.transport.calls), 1)

    def test_cancel_callback_rejects_wrong_state_redirect_replay_and_expiry(self):
        start = self.client.begin_authorization(self.redirect)
        for state, redirect in (("x" * 43, self.redirect),
                                (start["state"], "http://127.0.0.1:13877/auth/callback")):
            with self.subTest(state=state, redirect=redirect), self.assertRaises(AppError):
                self.client.cancel_authorization(state, redirect)
        self.assertIsNotNone(self.read_session()["pending"])
        result = self.client.cancel_authorization(start["state"], self.redirect)
        self.assertEqual(result["reason"], "not_authenticated")
        self.assertEqual(self.transport.calls, [])
        with self.assertRaises(AppError):
            self.client.cancel_authorization(start["state"], self.redirect)
        start = self.client.begin_authorization(self.redirect)
        self.clock.advance(300)
        with self.assertRaises(AppError) as caught:
            self.client.cancel_authorization(start["state"], self.redirect)
        self.assertEqual(caught.exception.code, "membership_authorization_expired")

    def test_authorization_clock_rollback_rejected(self):
        start = self.client.begin_authorization(self.redirect)
        self.clock.wall -= 60
        with self.assertRaises(AppError) as caught:
            self.client.complete_authorization("code", start["state"], self.redirect)
        self.assertEqual(caught.exception.code, "membership_authorization_expired")

    def test_callback_requires_numeric_canonical_loopback_and_exact_path(self):
        bad = ["http://localhost:8000/auth/callback", "http://127.1:8000/auth/callback",
               "https://127.0.0.1:8000/auth/callback", "http://127.0.0.1/auth/callback",
               "http://127.0.0.1:8000/auth/callback?x=1", "http://127.0.0.1:8000/auth/callback#x",
               "http://user@127.0.0.1:8000/auth/callback", "http://127.0.0.1:8000/other",
               "http://127.0.0.1:08000/auth/callback", "http://127.0.0.1:0/auth/callback",
               "http://127.0.0.1:65536/auth/callback", "http://example.com:8000/auth/callback"]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(AppError):
                self.client.begin_authorization(value)
        self.assertIn("authorization_url", self.client.begin_authorization("http://[::1]:8000/auth/callback"))

    def test_new_authorization_supersedes_previous_attempt(self):
        old = self.client.begin_authorization(self.redirect)
        new = self.client.begin_authorization(self.redirect)
        self.assertNotEqual(old["state"], new["state"])
        with self.assertRaises(AppError):
            self.client.complete_authorization("code", old["state"], self.redirect)
        self.assertTrue(self.client.complete_authorization("code", new["state"], self.redirect)["allowed"])

    def test_token_scope_must_be_exact_and_not_website_scope(self):
        self.transport.token_hook = lambda *args: {"device_token": self.transport.token,
                                                  "scope": "workbench website:admin", "status": decision(self.clock)}
        with self.assertRaises(AppError) as caught:
            self.authorize()
        self.assertEqual(caught.exception.code, "membership_verification_unavailable")
        self.assertIsNone(self.read_session()["device_token"])

    def test_failed_token_exchange_consumes_pending_and_redacts_exception(self):
        self.transport.failure = RuntimeError("secret-device-token secret-verifier private-url")
        with self.assertRaises(AppError) as caught:
            self.authorize()
        self.assertNotIn("secret", str(caught.exception))
        self.assertIsNone(self.read_session()["pending"])

    def test_cache_server_time_progresses_and_no_repeated_network(self):
        first = self.authorize()
        self.clock.advance(123)
        second = self.client.status()
        self.assertEqual(second["server_time"], iso(1800000123))
        self.assertEqual(second["permit_expires_at"], first["permit_expires_at"])
        self.assertEqual(len(self.transport.calls), 1)

    def test_offline_only_in_current_process_before_exact_deadline(self):
        self.authorize()
        self.transport.failure = OSError("offline")
        self.clock.advance(899)
        status = self.client.status(refresh=True)
        self.assertTrue(status["allowed"])
        self.assertTrue(status["verification_pending"])
        self.clock.advance(1)
        self.assert_denied("verification_unavailable")
        self.assertEqual(self.client.status()["account_id"], "account-a")

    def test_new_client_cannot_trust_saved_permit_or_offline_file(self):
        self.authorize()
        persisted = self.read_session()
        self.assertNotIn("permit_expires_at", persisted)
        self.transport.failure = OSError("offline")
        fresh = MembershipClient(self.root, transport=self.transport, clock=self.clock)
        self.assert_denied("verification_unavailable", fresh)
        self.assertEqual(fresh.status()["account_id"], "account-a")

    def test_forked_client_does_not_reuse_parent_permit(self):
        self.authorize()
        self.transport.failure = OSError("offline")
        with patch("fagao.membership.os.getpid", return_value=os.getpid() + 1):
            self.assert_denied("verification_unavailable")

    def test_local_clock_rollback_does_not_extend_lease(self):
        self.authorize()
        self.clock.wall -= 60
        self.transport.failure = OSError("offline")
        self.assert_denied("verification_unavailable")

    def test_sleep_wall_time_counts_when_monotonic_does_not_advance(self):
        self.authorize()
        self.clock.wall += 901
        self.transport.failure = OSError("offline")
        self.assert_denied("verification_unavailable")

    def test_membership_deadline_caps_short_permit(self):
        self.transport.member_seconds = 3
        self.authorize()
        self.clock.advance(2)
        self.assertTrue(self.client.require()["allowed"])
        self.clock.advance(1)
        self.transport.override = decision(self.clock, allowed=False, reason="expired", member_seconds=0)
        self.assert_denied("expired")

    def test_server_account_experiment_keeps_real_missing_or_past_membership_date(self):
        for membership_end in (None, iso(self.clock.time() - 86400), iso(self.clock.time() + 1)):
            with self.subTest(membership_end=membership_end):
                self.transport.override = experimental_decision(self.clock, membership_end)
                result = self.authorize()
                self.assertTrue(result['allowed'])
                self.assertEqual(result['access_mode'], 'active')
                self.assertEqual(result['access_basis'], 'experimental_account')
                self.assertEqual(result['membership_expires_at'], membership_end)
                self.assertEqual(result['permit_expires_at'], iso(self.clock.time() + 900))
                self.assertNotIn('access_basis', self.read_session())
                self.assertNotIn('permit_expires_at', self.read_session())
        self.clock.advance(2)
        self.assertTrue(self.client.require()['allowed'])

    def test_paid_and_experimental_memberships_still_require_real_member_deadline(self):
        for basis in ('paid_membership', 'experimental_membership'):
            for membership_end in (None, iso(self.clock.time() - 1), iso(self.clock.time() + 1)):
                with self.subTest(basis=basis, membership_end=membership_end):
                    self.transport.override = {**experimental_decision(self.clock, membership_end), 'access_basis': basis}
                    with self.assertRaises(AppError): self.authorize()
                    self.assertIsNone(self.read_session()['device_token'])
            self.transport.override = {**decision(self.clock, member_seconds=30), 'access_basis': basis}
            status = self.authorize()
            self.assertTrue(status['allowed'])
            self.assertEqual(status['access_basis'], basis)
            self.assertEqual(status['permit_expires_at'], status['membership_expires_at'])
            self.client.logout()

    def test_legacy_response_defaults_to_paid_and_unknown_basis_clears_cached_grant(self):
        self.assertEqual(self.authorize()['access_basis'], 'paid_membership')
        for basis in ('membership', 'admin_override', '', None, True, {}, 1):
            with self.subTest(basis=basis):
                self.transport.override = experimental_decision(self.clock)
                self.assertTrue(self.client.status(refresh=True)['allowed'])
                self.transport.override = {**experimental_decision(self.clock), 'access_basis': basis}
                result = self.client.status(refresh=True)
                self.assertFalse(result['allowed'])
                self.assertIsNone(result['access_basis'])
                self.assert_denied('verification_unavailable')

    def test_all_access_bases_reject_permits_longer_than_fifteen_minutes(self):
        for basis in ('paid_membership', 'experimental_membership', 'experimental_account'):
            for seconds in (0, -1, 900.001, 901, 86400):
                with self.subTest(basis=basis, seconds=seconds):
                    self.transport.override = {**decision(self.clock), 'access_basis': basis,
                                               'permit_expires_at': iso(self.clock.time() + seconds)}
                    with self.assertRaises(AppError): self.authorize()
                    self.assertIsNone(self.read_session()['device_token'])

    def test_account_experiment_never_turns_identity_lease_into_extra_use_or_history(self):
        self.transport.override = experimental_decision(self.clock, iso(self.clock.time() - 3600))
        first = self.authorize()
        self.transport.failure = OSError('offline')
        self.clock.advance(899)
        self.assertEqual(self.client.require()['permit_expires_at'], first['permit_expires_at'])
        self.clock.advance(1)
        self.assert_denied('verification_unavailable')
        self.assert_denied('verification_unavailable', operation='history')

    def test_account_experiment_cannot_survive_new_process_without_server_proof(self):
        self.transport.override = experimental_decision(self.clock)
        self.authorize()
        self.transport.failure = OSError('offline')
        fresh = MembershipClient(self.root, transport=self.transport, clock=self.clock)
        self.assert_denied('verification_unavailable', fresh)
        self.assertIsNone(fresh.status()['access_basis'])

    def test_explicit_experiment_close_or_device_revoke_clears_remaining_permit(self):
        self.transport.override = experimental_decision(self.clock)
        self.authorize()
        for reason in ('not_eligible', 'suspended', 'device_revoked'):
            with self.subTest(reason=reason):
                self.transport.override = experimental_decision(self.clock)
                self.assertTrue(self.client.status(refresh=True)['allowed'])
                self.transport.override = {**decision(self.clock, allowed=False, reason=reason),
                                           'access_basis': 'experimental_account', 'membership_expires_at': None}
                self.assertFalse(self.client.status(refresh=True)['allowed'])
                self.assert_denied(reason)
                self.assert_denied(reason, operation='history')

    def test_experiment_unavailable_cannot_extend_previously_verified_permit(self):
        self.transport.override = experimental_decision(self.clock)
        first = self.authorize()
        self.transport.failure = membership._Unavailable()
        self.clock.advance(899)
        status = self.client.status(refresh=True)
        self.assertTrue(status['allowed'])
        self.assertTrue(status['verification_pending'])
        self.assertEqual(status['permit_expires_at'], first['permit_expires_at'])
        self.clock.advance(1)
        self.assert_denied('verification_unavailable')

    def test_experiment_switch_back_to_membership_reapplies_real_deadline(self):
        self.transport.override = experimental_decision(self.clock)
        self.authorize()
        self.transport.override = {**decision(self.clock, member_seconds=3), 'access_basis': 'paid_membership'}
        result = self.client.status(refresh=True)
        self.assertEqual(result['access_basis'], 'paid_membership')
        self.assertEqual(result['permit_expires_at'], result['membership_expires_at'])
        self.transport.failure = OSError('offline')
        self.clock.advance(3)
        self.assert_denied('verification_unavailable')

    def test_experiment_cannot_remove_account_or_send_malformed_real_membership_date(self):
        self.transport.override = experimental_decision(self.clock)
        self.authorize()
        for changes in ({'account': None}, {'account': {'id': 'someone-else', 'display_name': 'other'}},
                        {'membership_expires_at': 'unknown'}, {'permit_expires_at': None}):
            with self.subTest(changes=changes):
                self.transport.override = {**experimental_decision(self.clock), **changes}
                result = self.client.status(refresh=True)
                self.assertFalse(result['allowed'])
                self.assert_denied('verification_unavailable')

    def test_sleep_during_experiment_exchange_consumes_permit_with_frozen_monotonic(self):
        def delayed(*args):
            response = {'device_token': self.transport.token, 'scope': 'workbench',
                        'status': experimental_decision(self.clock)}
            self.clock.wall += 3600
            return response
        self.transport.token_hook = delayed
        status = self.authorize()
        self.assertFalse(status['allowed'])
        self.assertEqual(status['reason'], 'verification_unavailable')
        self.assertFalse(status['can_read_history'])

    def test_sleep_during_experiment_refresh_consumes_permit_with_frozen_monotonic(self):
        self.transport.override = experimental_decision(self.clock)
        self.authorize()
        def delayed(*args):
            response = experimental_decision(self.clock)
            self.clock.wall += 900
            return response
        self.transport.permit_hook = delayed
        status = self.client.status(refresh=True)
        self.assertFalse(status['allowed'])
        self.assertEqual(status['reason'], 'verification_unavailable')

    def test_clock_rollback_during_experiment_response_cannot_create_new_permit(self):
        self.transport.override = experimental_decision(self.clock)
        self.authorize()
        def delayed(*args):
            response = experimental_decision(self.clock)
            self.clock.wall -= 60
            return response
        self.transport.permit_hook = delayed
        status = self.client.status(refresh=True)
        self.assertFalse(status['allowed'])
        self.assertEqual(status['reason'], 'verification_unavailable')

    def test_invalid_request_clock_observation_fails_closed(self):
        for field, value in (('mono', -1), ('mono', float('nan')), ('mono', float('inf')),
                             ('wall', float('nan')), ('wall', float('inf'))):
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as directory:
                clock = Clock()
                transport = Transport(clock)
                client = MembershipClient(directory, transport=transport, clock=clock)
                def delayed(*args):
                    response = {'device_token': transport.token, 'scope': 'workbench',
                                'status': experimental_decision(clock)}
                    setattr(clock, field, value)
                    return response
                transport.token_hook = delayed
                status = self.authorize(client)
                self.assertFalse(status['allowed'])
                self.assertFalse(status['can_read_history'])
                self.assertEqual(status['reason'], 'verification_unavailable')

    def test_expired_and_refunded_status_overrides_previous_valid_cache(self):
        self.authorize()
        for reason in ("expired", "not_eligible", "suspended", "device_revoked", "device_limit"):
            with self.subTest(reason=reason):
                self.transport.override = decision(self.clock, allowed=False, reason=reason)
                self.assertEqual(self.client.status(refresh=True)["reason"], reason)
                self.assert_denied(reason)

    def test_revoked_token_response_can_omit_unresolvable_account(self):
        self.authorize()
        self.transport.override = {**decision(self.clock, allowed=False, reason="device_revoked"), "account": None}
        status = self.client.status(refresh=True)
        self.assertFalse(status["allowed"])
        self.assertEqual(status["reason"], "device_revoked")
        self.assertEqual(status["account_id"], "account-a")
        self.assert_denied("device_revoked")

    def test_expired_member_can_read_verified_history_but_cannot_start_work(self):
        self.authorize()
        self.transport.override = {**decision(self.clock, allowed=False, reason="expired", member_seconds=-10),
                                   "identity_expires_at": iso(self.clock.time() + 60)}
        status = self.client.status(refresh=True)
        self.assertEqual(status["access_mode"], "read_only")
        self.assertTrue(status["can_read_history"])
        self.assertFalse(status["allowed"])
        self.assertTrue(self.client.require(operation="read")["can_read_history"])
        self.assertTrue(self.client.require(operation="history")["can_read_history"])
        for operation in ("use", "collect", "import", "translation", "export", "update"):
            with self.subTest(operation=operation):
                self.assert_denied("expired", operation=operation)

    def test_expired_member_can_reconnect_only_to_read_existing_history(self):
        self.transport.override = {**decision(self.clock, allowed=False, reason="expired", member_seconds=-10),
                                   "identity_expires_at": iso(self.clock.time() + 60)}
        status = self.authorize()
        self.assertFalse(status["allowed"])
        self.assertEqual(status["access_mode"], "read_only")
        self.assertTrue(self.client.require(operation="history")["can_read_history"])
        self.assert_denied("expired")

    def test_expired_history_without_identity_lease_reverifies_every_read(self):
        self.authorize()
        self.transport.override = decision(self.clock, allowed=False, reason="expired", member_seconds=-10)
        self.client.status(refresh=True)
        before = len(self.transport.calls)
        self.assertTrue(self.client.require(operation="read")["can_read_history"])
        self.assertTrue(self.client.require(operation="read")["can_read_history"])
        self.assertEqual(len(self.transport.calls), before + 2)
        self.transport.failure = OSError("offline")
        self.assert_denied("verification_unavailable", operation="history")

    def test_history_identity_lease_expires_and_is_not_trusted_after_restart(self):
        self.authorize()
        self.transport.override = {**decision(self.clock, allowed=False, reason="expired", member_seconds=-10),
                                   "identity_expires_at": iso(self.clock.time() + 60)}
        self.client.status(refresh=True)
        self.transport.failure = OSError("offline")
        self.clock.advance(59)
        self.assertTrue(self.client.require(operation="history")["can_read_history"])
        fresh = MembershipClient(self.root, transport=self.transport, clock=self.clock)
        self.assert_denied("verification_unavailable", fresh, operation="history")
        self.clock.advance(1)
        self.assert_denied("verification_unavailable", operation="history")

    def test_active_deadline_transition_requires_still_valid_identity_lease(self):
        self.transport.override = {**decision(self.clock, member_seconds=3),
                                   "identity_expires_at": iso(self.clock.time() + 60)}
        self.authorize()
        self.transport.failure = OSError("offline")
        self.clock.advance(3)
        status = self.client.require(operation="history")
        self.assertEqual(status["access_mode"], "read_only")
        self.assertFalse(status["allowed"])
        self.assert_denied("expired", operation="use")
        self.assertLessEqual(status["permit_expires_at"], status["membership_expires_at"])

    def test_expired_history_never_follows_suspension_revoke_or_ineligible(self):
        self.authorize()
        for reason in ("suspended", "device_revoked", "not_eligible", "not_authenticated"):
            with self.subTest(reason=reason):
                self.transport.override = {**decision(self.clock, allowed=False, reason=reason, member_seconds=-10),
                                           "identity_expires_at": iso(self.clock.time() + 60)}
                status = self.client.status(refresh=True)
                self.assertEqual(status["access_mode"], "locked")
                self.assertFalse(status["can_read_history"])
                self.assert_denied(reason, operation="history")

    def test_expired_history_needs_server_identity_and_valid_optional_lease(self):
        self.authorize()
        for changes in ({"account": None}, {"identity_expires_at": iso(self.clock.time())},
                        {"identity_expires_at": "invalid"}):
            with self.subTest(changes=changes):
                self.transport.override = {**decision(self.clock, allowed=False, reason="expired", member_seconds=-10), **changes}
                self.assertFalse(self.client.status(refresh=True)["can_read_history"])
                self.assert_denied("verification_unavailable", operation="history")

    def historical_paid_decision(self, reason="not_eligible"):
        return {**decision(self.clock, allowed=False, reason=reason, member_seconds=3600),
                "history_eligible": True, "history_membership_expires_at": iso(self.clock.time() - 86400),
                "identity_expires_at": iso(self.clock.time() + 60)}

    def test_current_gift_after_paid_segment_can_read_history_only(self):
        self.authorize()
        self.transport.override = self.historical_paid_decision()
        status = self.client.status(refresh=True)
        self.assertFalse(status["allowed"])
        self.assertEqual(status["reason"], "not_eligible")
        self.assertEqual(status["access_mode"], "read_only")
        self.assertTrue(status["history_eligible"])
        self.assertEqual(status["membership_expires_at"], iso(self.clock.time() + 3600))
        self.assertEqual(status["history_membership_expires_at"], iso(self.clock.time() - 86400))
        self.assertTrue(self.client.require(operation="read")["can_read_history"])
        self.assert_denied("not_eligible", operation="update")
        self.assert_denied("not_eligible", operation="translation")

    def test_prior_paid_history_requires_exact_authoritative_evidence_and_lease(self):
        self.authorize()
        invalid = [{"history_eligible": "true"}, {"history_eligible": 1},
                   {"history_membership_expires_at": None},
                   {"history_membership_expires_at": iso(self.clock.time() + 1)},
                   {"history_membership_expires_at": "2026-01-01T00:00:00"},
                   {"identity_expires_at": None}, {"identity_expires_at": iso(self.clock.time())},
                   {"account": None}, {"account": {"id": "other-account", "display_name": "其他"}}]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.transport.override = {**self.historical_paid_decision(), **changes}
                status = self.client.status(refresh=True)
                self.assertFalse(status["can_read_history"])
                self.assertEqual(status["access_mode"], "locked")
                self.assert_denied("verification_unavailable", operation="history")

    def test_ineligible_without_explicit_history_evidence_remains_locked(self):
        self.authorize()
        raw = self.historical_paid_decision()
        raw.pop("history_eligible")
        self.transport.override = raw
        self.assertFalse(self.client.status(refresh=True)["can_read_history"])
        self.assert_denied("not_eligible", operation="history")
        raw["history_eligible"] = False
        self.assertFalse(self.client.status(refresh=True)["can_read_history"])

    def test_prior_paid_history_lease_is_bounded_and_new_process_must_verify(self):
        self.transport.override = self.historical_paid_decision()
        status = self.authorize()
        self.assertEqual(status["access_mode"], "read_only")
        self.transport.failure = OSError("offline")
        self.clock.advance(59)
        status = self.client.require(operation="history")
        self.assertEqual(status["reason"], "not_eligible")
        self.assertTrue(status["can_read_history"])
        fresh = MembershipClient(self.root, transport=self.transport, clock=self.clock)
        self.assert_denied("verification_unavailable", fresh, operation="history")
        self.clock.advance(1)
        self.assert_denied("verification_unavailable", operation="history")

    def test_prior_paid_history_never_overrides_suspension_or_revocation(self):
        self.authorize()
        for reason in ("suspended", "device_revoked", "not_authenticated", "refunded"):
            with self.subTest(reason=reason):
                self.transport.override = self.historical_paid_decision(reason)
                status = self.client.status(refresh=True)
                self.assertFalse(status["can_read_history"])
                self.assertEqual(status["access_mode"], "locked")
                with self.assertRaises(AppError):
                    self.client.require(operation="history")

    def test_renewal_restores_identity_and_same_data_directory(self):
        first = self.authorize()
        directory = self.client.account_directory(first["account_id"])
        self.transport.override = decision(self.clock, allowed=False)
        self.client.status(refresh=True)
        self.transport.override = None
        renewed = self.client.status(refresh=True)
        self.assertTrue(renewed["allowed"])
        self.assertEqual(directory, self.client.account_directory(renewed["account_id"]))
        self.assertEqual(first["generation"], renewed["generation"])

    def test_malformed_response_revokes_cached_allowance(self):
        changes = [{"allowed": "true"}, {"reason": "unknown"}, {"account": None},
                   {"server_time": "2026-01-01T00:00:00"}, {"policy_version": None},
                   {"permit_expires_at": iso(self.clock.time() + 99999)},
                   {"membership_expires_at": "not-a-date"}, {"reason": "expired"},
                   {"account": {"id": "another-account", "display_name": "wrong"}}]
        for changeset in changes:
            with self.subTest(changes=changeset):
                self.transport.override = None
                self.authorize()
                self.transport.override = decision(self.clock)
                self.transport.override.update(changeset)
                result = self.client.status(refresh=True)
                self.assertFalse(result["allowed"])
                self.assertEqual(result["reason"], "verification_unavailable")

    def test_rotation_keeps_generation_and_is_seen_by_other_client(self):
        self.authorize()
        old_generation = self.client.active_generation
        rotated = "rotated-synthetic-token-" + "b" * 32
        self.transport.override = {**decision(self.clock), "device_token": rotated, "scope": "workbench"}
        self.client.status(refresh=True)
        self.assertEqual(self.read_session()["device_token"], rotated)
        self.assertEqual(old_generation, self.client.active_generation)
        self.transport.override = None
        fresh = MembershipClient(self.root, transport=self.transport, clock=self.clock)
        self.assertTrue(fresh.status()["allowed"])
        self.assertEqual(self.transport.calls[-1]["token"], rotated)

    def test_logout_invalidates_all_clients_and_keeps_data(self):
        self.authorize()
        other = MembershipClient(self.root, transport=self.transport, clock=self.clock)
        other.status()
        directory = self.client.account_directory("account-a")
        directory.mkdir(parents=True)
        (directory / "personal.txt").write_text("preserved")
        result = self.client.logout()
        self.assertIsNone(result["account_id"])
        self.assertIsNone(self.read_session()["device_token"])
        self.assert_denied("not_authenticated", other)
        self.assertEqual((directory / "personal.txt").read_text(), "preserved")

    def test_offline_logout_clears_secrets_and_reports_revocation_pending(self):
        self.authorize()
        self.transport.failure = OSError("offline secret")
        result = self.client.logout()
        self.assertTrue(result["revocation_pending"])
        self.assertNotIn(self.transport.token, (self.root / ".membership" / "session.json").read_text())
        self.assert_denied("not_authenticated")

    def test_logout_during_refresh_cannot_be_resurrected(self):
        self.authorize()
        arrived, release = threading.Event(), threading.Event()

        def delayed(*args):
            arrived.set()
            release.wait(3)
            return {**decision(self.clock), "device_token": "late-rotated-token-" + "c" * 32, "scope": "workbench"}

        self.transport.permit_hook = delayed
        results = []
        worker = threading.Thread(target=lambda: results.append(self.client.status(refresh=True)))
        worker.start()
        self.assertTrue(arrived.wait(2))
        logged_out = self.client.logout()
        self.assertIsNone(logged_out["account_id"])
        release.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(results[0]["allowed"])
        self.assertIsNone(self.read_session()["device_token"])
        revocations = [call["token"] for call in self.transport.calls if call["name"] == "revoke"]
        self.assertIn("late-rotated-token-" + "c" * 32, revocations)

    def test_logout_during_exchange_discards_new_token(self):
        start = self.client.begin_authorization(self.redirect)
        arrived, release = threading.Event(), threading.Event()

        def delayed(*args):
            arrived.set()
            release.wait(3)
            return {"status": decision(self.clock), "device_token": self.transport.token, "scope": "workbench"}

        self.transport.token_hook = delayed
        results = []

        def complete():
            try:
                self.client.complete_authorization("code", start["state"], self.redirect)
            except AppError as exc:
                results.append(exc.code)

        worker = threading.Thread(target=complete)
        worker.start()
        self.assertTrue(arrived.wait(2))
        self.client.logout()
        release.set()
        worker.join(3)
        self.assertEqual(results, ["membership_account_changed"])
        self.assertIsNone(self.read_session()["device_token"])
        self.assertEqual(self.transport.calls[-1]["name"], "revoke")

    def test_account_switch_cannot_continue_previous_account_or_generation(self):
        first = self.authorize()
        self.client.logout()
        self.transport.account_id = "account-b"
        second = self.authorize()
        self.assertNotEqual(first["generation"], second["generation"])
        self.assertNotEqual(self.client.account_directory("account-a"), self.client.account_directory("account-b"))
        self.assert_denied("account_changed", account_id="account-a")
        self.assert_denied("account_changed", generation=first["generation"])
        self.assertTrue(self.client.require(account_id="account-b", generation=second["generation"])["allowed"])

    def test_same_account_relogin_still_invalidates_old_task_generation(self):
        first = self.authorize()
        self.client.logout()
        self.authorize()
        self.assert_denied("account_changed", account_id="account-a", generation=first["generation"])

    def test_stale_logout_cannot_clear_a_new_account_inside_atomic_lock(self):
        first = self.authorize()
        self.client.logout()
        self.transport.account_id = "account-b"
        second = self.authorize()
        before = self.read_session()
        with self.assertRaises(AppError) as caught:
            self.client.logout(expected_account=first["account_id"], expected_generation=first["generation"])
        self.assertEqual(caught.exception.code, "membership_account_changed")
        self.assertEqual(self.read_session(), before)
        self.assertEqual(self.client.current_account_id, "account-b")
        self.client.logout(expected_account=second["account_id"], expected_generation=second["generation"])
        self.assertIsNone(self.client.current_account_id)

    def test_stale_authorization_start_cannot_replace_new_account_pending(self):
        anonymous = self.client.status()
        current = self.authorize()
        before = self.read_session()
        with self.assertRaises(AppError):
            self.client.begin_authorization(self.redirect, expected_account=None, expected_generation=anonymous["generation"])
        self.assertEqual(self.read_session(), before)
        start = self.client.begin_authorization(self.redirect, expected_account=current["account_id"],
                                               expected_generation=current["generation"])
        self.assertIn("authorization_url", start)

    def test_safe_namespace_never_uses_account_as_path(self):
        path = self.client.account_directory("../../someone/else")
        self.assertEqual(path.parent, self.root.resolve() / "accounts")
        self.assertEqual(len(path.name), 64)
        self.assertFalse(path.exists())

    @unittest.skipIf(os.name == "nt", "POSIX modes and symlinks; Windows uses inherited user-profile ACLs")
    def test_private_file_permissions_and_symlink_rejection(self):
        self.authorize()
        directory = self.root / ".membership"
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((directory / "session.json").stat().st_mode & 0o777, 0o600)
        accounts = self.root / "accounts"
        accounts.symlink_to(self.root / "elsewhere")
        with self.assertRaises(AppError):
            self.client.account_directory("account-a")

    def test_corrupted_session_invalidates_previous_cache(self):
        self.authorize()
        (self.root / ".membership" / "session.json").write_text("{corrupt")
        self.assert_denied("verification_unavailable")

    def test_real_cross_process_rotation_is_serialized(self):
        self.transport.token = "synthetic-rotating-token-" + "0" * 32
        self.authorize()
        (self.root / "synthetic-remote-counter").write_text("0")
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        workers = [context.Process(target=rotation_worker, args=(str(self.root), queue)) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            if worker.is_alive():
                worker.terminate()
                worker.join(2)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual([queue.get(timeout=2) for _ in workers], [True, True])
        queue.close()
        self.assertEqual((self.root / "synthetic-remote-counter").read_text(), "2")
        self.assertEqual(self.read_session()["device_token"], "synthetic-rotating-token-" + "2".zfill(32))

    def test_transport_runs_without_outer_task_guard_recursion(self):
        calls = []

        def guarded_transport(*args):
            network.check_authorization()
            return self.transport(*args)

        self.client.transport = guarded_transport
        with network.authorized_requests(lambda: calls.append("outer")):
            self.authorize()
            self.client.status(refresh=True)
        self.assertEqual(calls, [])

    def test_origin_and_endpoint_overrides_do_not_allow_http_or_external_paths(self):
        for origin in ("http://localhost:8000", "https://user:pass@example.com", "https://example.com/path", "https://example.com?x=1"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                MembershipClient(self.root, origin=origin)
        with self.assertRaises(ValueError):
            MembershipClient(self.root, endpoints={"token": "//evil.example/token"})

    def test_network_transport_uses_bearer_and_rejects_redirects(self):
        raw = json.dumps(decision(self.clock)).encode()

        class Response(io.BytesIO):
            headers = {"Content-Length": str(len(raw))}

        seen = []

        class Opener:
            def open(self, request, timeout):
                seen.append((request, timeout))
                return Response(raw)

        def opener_factory(*handlers):
            self.assertIsInstance(handlers[0], membership._NoRedirect)
            with self.assertRaises(membership._InvalidResponse):
                handlers[0].redirect_request(None, None, 302, "", {}, "https://other.example")
            return Opener()

        with patch.object(network, "build_opener", side_effect=opener_factory):
            result = self.client._http_transport("POST", "https://example.com/permit", {"safe": True}, self.transport.token)
        self.assertTrue(result["allowed"])
        request, timeout = seen[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer " + self.transport.token)
        self.assertNotIn(self.transport.token, request.full_url)
        self.assertEqual(timeout, membership.REQUEST_TIMEOUT)

    def test_http_error_cannot_grant_permission_even_with_success_json(self):
        original_transport = self.client.transport
        self.authorize()
        success = decision(self.clock)

        class Opener:
            def open(inner_self, request, timeout):
                raw = json.dumps(success).encode()
                raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(raw))

        self.client.transport = self.client._http_transport
        with patch.object(network, "build_opener", return_value=Opener()):
            self.assertFalse(self.client.status(refresh=True)["allowed"])
            self.assert_denied("verification_unavailable")
        self.client.transport = original_transport
        self.client.logout()
        success = {"device_token": self.transport.token, "scope": "workbench", "status": decision(self.clock)}
        self.client.transport = self.client._http_transport
        with patch.object(network, "build_opener", return_value=Opener()), self.assertRaises(AppError):
            self.authorize()
        self.assertIsNone(self.read_session()["device_token"])
        success = {"device_token": self.transport.token, "scope": "workbench",
                   "status": {**decision(self.clock, allowed=False, reason="expired", member_seconds=-10),
                              "identity_expires_at": iso(self.clock.time() + 60)}}
        with patch.object(network, "build_opener", return_value=Opener()), self.assertRaises(AppError):
            self.authorize()
        self.assertIsNone(self.read_session()["device_token"])
        success = {"device_token": self.transport.token, "scope": "workbench", "status": self.historical_paid_decision()}
        with patch.object(network, "build_opener", return_value=Opener()), self.assertRaises(AppError):
            self.authorize()
        self.assertIsNone(self.read_session()["device_token"])
        success = {"device_token": self.transport.token, "scope": "workbench", "status": experimental_decision(self.clock)}
        with patch.object(network, "build_opener", return_value=Opener()), self.assertRaises(AppError):
            self.authorize()
        self.assertIsNone(self.read_session()["device_token"])

    def test_transport_response_size_is_bounded(self):
        self.authorize()
        self.transport.override = {**decision(self.clock), "extra": "x" * membership.MAX_RESPONSE_BYTES}
        self.assertFalse(self.client.status(refresh=True)["allowed"])


if __name__ == "__main__":
    unittest.main()
