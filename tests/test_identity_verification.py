"""Tests for out-of-band identity verification.

The contract under test: a sender who quotes someone else's claim number gets
no record data, but is not dead-ended either. They receive a single-use,
time-limited code -- addressed to the contact on file, not to them -- and once
they redeem it their address becomes trusted for that one record.
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai.identity_verification import (
    IdentityVerifier,
    build_challenge_body,
    extract_verification_code,
)


class TestCodeExtraction(unittest.TestCase):
    """Only genuine code phrasings are recognised, so an unrelated 6-digit
    number in a claim narrative never burns one of the sender's attempts."""

    def test_bare_code_on_its_own_line(self):
        self.assertEqual(extract_verification_code("Hello\n\n    123456\n\nThanks"), "123456")

    def test_code_in_a_sentence(self):
        self.assertEqual(extract_verification_code("my verification code is 482019"), "482019")
        self.assertEqual(extract_verification_code("Code: 999111"), "999111")
        self.assertEqual(extract_verification_code("here is my OTP 000123"), "000123")

    def test_claim_number_is_not_a_code(self):
        text = "My claim is CLM-2024-04190 and the amount is 482019 dollars."
        self.assertIsNone(extract_verification_code(text))

    def test_wrong_length_is_ignored(self):
        self.assertIsNone(extract_verification_code("code 12345"))
        self.assertIsNone(extract_verification_code("code 1234567"))

    def test_empty_input(self):
        self.assertIsNone(extract_verification_code(None))
        self.assertIsNone(extract_verification_code(""))


class TestChallengeIssuance(unittest.TestCase):
    def setUp(self):
        self.v = IdentityVerifier()

    def test_issue_returns_a_six_digit_code(self):
        ok, reason, ch = self.v.issue("new@example.com", "CUST-1")
        self.assertTrue(ok, reason)
        self.assertEqual(len(ch.code), 6)
        self.assertTrue(ch.code.isdigit())

    def test_codes_are_unique_per_issue(self):
        # A fresh verifier per issue, so the per-sender live cap and the resend
        # cooldown do not mask the randomness being asserted here.
        codes = set()
        for _ in range(12):
            v = IdentityVerifier()
            _, _, ch = v.issue("new@example.com", "CUST-1")
            codes.add(ch.code)
        self.assertGreater(len(codes), 1, "codes must not be constant")

    def test_resend_is_cooldown_limited(self):
        self.v.issue("new@example.com", "CUST-1")
        ok, reason, _ = self.v.issue("new@example.com", "CUST-1")
        self.assertFalse(ok)
        self.assertIn("wait", reason.lower())

    def test_max_live_challenges_per_sender(self):
        v = IdentityVerifier(resend_cooldown_seconds=0)
        for _ in range(3):
            ok, _, _ = v.issue("new@example.com", "CUST-1")
            self.assertTrue(ok)
        ok, reason, _ = v.issue("new@example.com", "CUST-1")
        self.assertFalse(ok)
        self.assertIn("too many", reason.lower())


class TestChallengeRedemption(unittest.TestCase):
    def setUp(self):
        self.v = IdentityVerifier()

    def test_correct_code_verifies(self):
        _, _, ch = self.v.issue("new@example.com", "CUST-1")
        r = self.v.redeem("new@example.com", ch.code)
        self.assertTrue(r.verified, r.reason)
        self.assertEqual(r.customer_id, "CUST-1")
        self.assertTrue(self.v.is_verified("new@example.com", "CUST-1"))

    def test_code_is_single_use(self):
        _, _, ch = self.v.issue("new@example.com", "CUST-1")
        self.assertTrue(self.v.redeem("new@example.com", ch.code).verified)
        r2 = self.v.redeem("new@example.com", ch.code)
        self.assertFalse(r2.verified)

    def test_wrong_code_does_not_verify(self):
        _, _, ch = self.v.issue("new@example.com", "CUST-1")
        r = self.v.redeem("new@example.com", "000000" if ch.code != "000000" else "111111")
        self.assertFalse(r.verified)
        self.assertIn("not correct", r.reason.lower())

    def test_attempts_are_capped(self):
        _, _, ch = self.v.issue("new@example.com", "CUST-1")
        for _ in range(5):
            self.v.redeem("new@example.com", "999999")
        r = self.v.redeem("new@example.com", ch.code)
        self.assertFalse(r.verified, "code must not work after max attempts")
        self.assertIn("too many", r.reason.lower())

    def test_code_is_scoped_to_the_issuing_address(self):
        """A code leaked to a third party must not verify them."""
        _, _, ch = self.v.issue("new@example.com", "CUST-1")
        r = self.v.redeem("attacker@example.com", ch.code)
        self.assertFalse(r.verified)
        self.assertFalse(self.v.is_verified("attacker@example.com", "CUST-1"))

    def test_expired_code_is_rejected(self):
        # TTL 0 means the code is born expired: issue() records it, and
        # redeem() must reject it as expired rather than as unknown.
        v = IdentityVerifier(ttl_seconds=0)
        _, _, ch = v.issue("new@example.com", "CUST-1")
        r = v.redeem("new@example.com", ch.code)
        self.assertFalse(r.verified)
        self.assertIn("expired", r.reason.lower())

    def test_no_pending_challenge(self):
        r = self.v.redeem("new@example.com", "123456")
        self.assertFalse(r.verified)
        self.assertIn("no verification is pending", r.reason.lower())

    def test_address_comparison_is_case_insensitive(self):
        _, _, ch = self.v.issue("New@Example.com", "CUST-1")
        r = self.v.redeem("new@example.com", ch.code)
        self.assertTrue(r.verified, r.reason)


class TestVerificationScoping(unittest.TestCase):
    def setUp(self):
        self.v = IdentityVerifier()

    def test_verification_does_not_transfer_to_another_record(self):
        _, _, ch = self.v.issue("new@example.com", "CUST-1")
        self.v.redeem("new@example.com", ch.code)
        # Verified for CUST-1, but NOT for a different customer's record.
        self.assertTrue(self.v.is_verified("new@example.com", "CUST-1"))
        self.assertFalse(self.v.is_verified("new@example.com", "CUST-2"))

    def test_verification_lapses(self):
        v = IdentityVerifier(verified_ttl_seconds=0)
        _, _, ch = v.issue("new@example.com", "CUST-1")
        v.redeem("new@example.com", ch.code)
        self.assertFalse(v.is_verified("new@example.com", "CUST-1"))

    def test_stats(self):
        _, _, ch = self.v.issue("new@example.com", "CUST-1")
        self.v.redeem("new@example.com", ch.code)
        s = self.v.stats()
        self.assertEqual(s["used"], 1)
        self.assertEqual(s["verified_senders"], 1)


class TestChallengeBody(unittest.TestCase):
    def test_body_contains_code_and_no_record_data(self):
        body = build_challenge_body("Marcus", "482019")
        self.assertIn("482019", body)
        self.assertIn("Marcus", body)
        self.assertIn("30 minutes", body)
        # It must not leak status, amounts, adjuster, or the claim number.
        for leak in ("CLM-", "adjuster", "under review", "payout", "premium"):
            self.assertNotIn(leak, body)

    def test_body_omits_signature_when_blank(self):
        self.assertNotIn("Best regards", build_challenge_body("Marcus", "1", signature=""))

    def test_body_includes_signature_when_given(self):
        self.assertIn("Best regards", build_challenge_body("Marcus", "1", signature="Best regards,\nTeam"))


if __name__ == "__main__":
    unittest.main()
