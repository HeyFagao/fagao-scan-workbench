# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Official trial/word-pack contract tests using isolated server responses."""
from copy import deepcopy
import unittest

from fagao.membership import MembershipClient
import test_membership as fixtures
from test_membership import decision, iso


class WorkbenchExperienceTests(unittest.TestCase):
    redirect = fixtures.MembershipTests.redirect
    setUp = fixtures.MembershipTests.setUp
    authorize = fixtures.MembershipTests.authorize
    read_session = fixtures.MembershipTests.read_session
    assert_denied = fixtures.MembershipTests.assert_denied

    def grant(self, basis="free_trial", seconds=7 * 86400, **changes):
        server = self.clock.time()
        return {**decision(self.clock), "access_basis": basis,
                "membership_expires_at": None, "access_expires_at": iso(server + seconds),
                "permit_expires_at": iso(server + min(seconds, 900)),
                "identity_expires_at": iso(server + 900),
                "history_eligible": False, "history_membership_expires_at": None,
                "history_access_expires_at": None, **changes}

    def expired(self, basis="free_trial", **changes):
        return self.grant(basis, -1, **{"allowed": False, "reason": "expired", "permit_expires_at": None, **changes})

    def test_seven_day_trial_and_fifteen_day_word_pack_keep_membership_truth(self):
        for basis, days in (("free_trial", 7), ("word_pack", 15)):
            with self.subTest(basis=basis):
                self.transport.override = self.grant(basis, days * 86400)
                result = self.authorize()
                self.assertTrue(result["allowed"])
                self.assertEqual(result["access_basis"], basis)
                self.assertEqual(result["access_expires_at"], iso(self.clock.time() + days * 86400))
                self.assertIsNone(result["membership_expires_at"])
                self.assertEqual(result["permit_expires_at"], iso(self.clock.time() + 900))
                self.assertNotIn("access_expires_at", self.read_session())
                self.client.logout()

    def test_trial_can_continue_after_real_membership_expiry_without_rewriting_it(self):
        membership_end = iso(self.clock.time() - 86400)
        self.transport.override = self.grant("word_pack", membership_expires_at=membership_end)
        result = self.authorize()
        self.assertTrue(result["allowed"])
        self.assertEqual(result["membership_expires_at"], membership_end)
        self.assertEqual(result["access_expires_at"], self.transport.override["access_expires_at"])

    def test_trial_and_word_pack_stop_at_exact_deadline_and_preserve_read_only_history(self):
        for basis in ("free_trial", "word_pack"):
            with self.subTest(basis=basis):
                self.transport.failure = None
                self.transport.override = self.grant(basis, 2)
                before = self.authorize()
                folder = self.client.account_directory(before["account_id"])
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "saved-report.txt").write_text("retained", encoding="utf-8")
                self.transport.failure = OSError("offline")
                self.clock.advance(1)
                self.assertTrue(self.client.require()["allowed"])
                self.clock.advance(1)
                expired = self.client.require(operation="history")
                self.assertEqual(expired["access_mode"], "read_only")
                self.assertEqual(expired["reason"], "expired")
                self.assertIsNone(expired["membership_expires_at"])
                for operation in ("update", "import", "translation", "collect", "analyze", "save_report"):
                    self.assert_denied("expired", operation=operation)
                self.assertEqual((folder / "saved-report.txt").read_text(encoding="utf-8"), "retained")
                self.clock.advance(898)
                self.assert_denied("verification_unavailable", operation="history")

    def test_expired_trial_reconnect_receives_only_bounded_account_history(self):
        self.transport.override = self.expired()
        status = self.authorize()
        self.assertFalse(status["allowed"])
        self.assertEqual(status["access_mode"], "read_only")
        self.assertTrue(self.client.require(operation="read")["can_read_history"])
        self.assert_denied("expired", operation="update")
        self.transport.failure = OSError("offline")
        fresh = MembershipClient(self.root, transport=self.transport, clock=self.clock)
        self.assert_denied("verification_unavailable", client=fresh, operation="history")

    def test_word_pack_renewals_refresh_same_account_and_never_reset_local_data(self):
        self.transport.override = self.expired("word_pack")
        before = self.authorize()
        folder = self.client.account_directory(before["account_id"])
        folder.mkdir(parents=True)
        report = folder / "report.txt"
        report.write_text("retain me", encoding="utf-8")
        for days in (15, 30):
            self.transport.override = self.grant("word_pack", days * 86400)
            renewed = self.client.status(refresh=True)
            self.assertTrue(renewed["allowed"])
            self.assertEqual(renewed["access_expires_at"], iso(self.clock.time() + days * 86400))
            self.assertEqual(before["generation"], renewed["generation"])
            self.assertEqual(folder, self.client.account_directory(renewed["account_id"]))
            self.assertEqual(report.read_text(encoding="utf-8"), "retain me")

    def test_member_neutral_deadline_does_not_relax_actual_membership_expiry(self):
        for basis in ("paid_membership", "experimental_membership"):
            with self.subTest(basis=basis):
                self.transport.override = self.grant(basis, 10, membership_expires_at=iso(self.clock.time() + 10))
                result = self.authorize()
                self.assertEqual(result["access_expires_at"], result["membership_expires_at"])
                self.transport.failure = OSError("offline")
                self.clock.advance(10)
                self.assert_denied("expired")
                self.assertTrue(self.client.require(operation="history")["can_read_history"])
                self.transport.failure = None
                for end in (None, iso(self.clock.time() - 1), iso(self.clock.time() + 1)):
                    self.transport.override = self.grant(basis, membership_expires_at=end)
                    status = self.client.status(refresh=True)
                    self.assertFalse(status["allowed"])
                self.transport.override = self.grant(basis, 86400,
                                                     membership_expires_at=iso(self.clock.time() + 3600))
                self.assertFalse(self.client.status(refresh=True)["allowed"])

    def test_missing_invalid_or_extended_experience_deadlines_fail_closed(self):
        self.transport.override = self.grant()
        self.authorize()
        for basis in ("free_trial", "word_pack"):
            invalid = [{"access_expires_at": None}, {"access_expires_at": "not-a-date"},
                       {"access_expires_at": "2026-01-01T00:00:00"},
                       {"access_expires_at": iso(self.clock.time())},
                       {"access_expires_at": iso(self.clock.time() + 899)},
                       {"permit_expires_at": iso(self.clock.time() + 901)},
                       {"identity_expires_at": iso(self.clock.time() + 901)},
                       {"membership_expires_at": "invalid"}, {"account": None},
                       {"account": {"id": "someone-else", "display_name": "other"}}]
            for change in invalid:
                with self.subTest(basis=basis, change=change):
                    self.transport.override = self.grant(basis)
                    self.assertTrue(self.client.status(refresh=True)["allowed"])
                    self.transport.override = self.grant(basis, **change)
                    status = self.client.status(refresh=True)
                    self.assertEqual(status["reason"], "verification_unavailable")
                    self.assertFalse(status["can_read_history"])
            self.transport.override = self.grant(basis)
            del self.transport.override["access_expires_at"]
            self.assertEqual(self.client.status(refresh=True)["reason"], "verification_unavailable")

    def test_expired_trial_needs_finite_elapsed_deadline_and_current_identity_lease(self):
        self.transport.override = self.grant()
        self.authorize()
        for change in ({"access_expires_at": None}, {"access_expires_at": iso(self.clock.time() + 1)},
                       {"identity_expires_at": None}, {"identity_expires_at": iso(self.clock.time())},
                       {"identity_expires_at": iso(self.clock.time() + 901)}, {"account": None}):
            with self.subTest(change=change):
                self.transport.override = self.expired(**change)
                status = self.client.status(refresh=True)
                self.assertEqual(status["reason"], "verification_unavailable")
                self.assertFalse(status["can_read_history"])

    def test_generic_history_evidence_does_not_require_fake_member_dates(self):
        history_end = iso(self.clock.time() - 86400)
        self.transport.override = self.expired("word_pack", reason="not_eligible", access_expires_at=None,
                                              history_eligible=True, history_access_expires_at=history_end)
        result = self.authorize()
        self.assertEqual(result["access_mode"], "read_only")
        self.assertIsNone(result["membership_expires_at"])
        self.assertIsNone(result["history_membership_expires_at"])
        self.assertEqual(result["history_access_expires_at"], history_end)
        self.assert_denied("not_eligible", operation="analyze")
        base = deepcopy(self.transport.override)
        for change in ({"history_eligible": False}, {"history_access_expires_at": None},
                       {"history_access_expires_at": iso(self.clock.time() + 1)}):
            self.transport.override = {**base, **change}
            self.assertFalse(self.client.status(refresh=True)["can_read_history"])

    def test_refund_suspension_device_revoke_and_logout_remove_trial_authority(self):
        self.transport.override = self.grant()
        self.authorize()
        for reason in ("suspended", "device_revoked", "not_eligible"):
            with self.subTest(reason=reason):
                self.transport.override = self.grant()
                self.assertTrue(self.client.status(refresh=True)["allowed"])
                self.transport.override = self.expired(reason=reason)
                result = self.client.status(refresh=True)
                self.assertFalse(result["can_read_history"])
                self.assert_denied(reason, operation="history")
        self.client.logout()
        self.assert_denied("not_authenticated")

    def test_sleeping_or_late_response_does_not_restart_trial_clock(self):
        def delayed(*args):
            response = {"device_token": self.transport.token, "scope": "workbench", "status": self.grant(seconds=2)}
            self.clock.wall += 901
            return response
        self.transport.token_hook = delayed
        result = self.authorize()
        self.assertFalse(result["allowed"])
        self.assertFalse(result["can_read_history"])

    def test_consumed_short_permit_does_not_claim_unexpired_trial_has_ended(self):
        def delayed(*args):
            response = {"device_token": self.transport.token, "scope": "workbench", "status": self.grant()}
            self.clock.wall += 901
            return response
        self.transport.token_hook = delayed
        result = self.authorize()
        self.assertFalse(result["allowed"])
        self.assertFalse(result["can_read_history"])
        self.assertEqual(result["reason"], "verification_unavailable")


if __name__ == "__main__":
    unittest.main()
