"""Unit tests for the SafetyEngine: loop prevention, spam scoring, rate limiting, and PII redaction."""

import unittest
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai.safety import SafetyEngine


class TestSafetyEngine(unittest.TestCase):
    def setUp(self):
        self.safety = SafetyEngine(
            max_replies_per_thread=3,
            max_sender_per_hour=3,
            max_global_per_hour=10,
        )

    def test_pii_redaction(self):
        text = "My SSN is 123-45-6789 and my card is 4111-2222-3333-4444. API key: sk-abcdef1234567890abcdef123456"
        redacted = SafetyEngine.redact_pii(text)
        self.assertNotIn("123-45-6789", redacted)
        self.assertNotIn("4111-2222-3333-4444", redacted)
        self.assertNotIn("sk-abcdef1234567890abcdef123456", redacted)
        self.assertIn("[REDACTED-SSN]", redacted)
        self.assertIn("[REDACTED-CARD]", redacted)
        self.assertIn("[REDACTED-KEY]", redacted)

    def test_automated_sender_detection(self):
        res = self.safety.check_incoming_email(
            sender_email="no-reply@company.com",
            subject="Important update",
            body="Do not reply to this email.",
            headers={},
        )
        self.assertFalse(res.is_safe)
        self.assertTrue(res.is_automated)
        self.assertEqual(res.action, "ARCHIVE_SPAM")

    def test_auto_reply_header_detection(self):
        res = self.safety.check_incoming_email(
            sender_email="colleague@domain.com",
            subject="Out of office",
            body="I will be away for the week.",
            headers={"Auto-Submitted": "auto-replied"},
        )
        self.assertFalse(res.is_safe)
        self.assertTrue(res.is_automated)

    def test_spam_keyword_scoring(self):
        spam_body = "Claim your lottery prize now! Casino winner crypto investment viagra guaranteed!"
        res = self.safety.check_incoming_email(
            sender_email="spammer@domain.com",
            subject="YOU WON LOTTERY",
            body=spam_body,
            headers={},
        )
        self.assertFalse(res.is_safe)
        self.assertTrue(res.is_spam)
        self.assertGreaterEqual(res.spam_score, 0.6)

    def test_per_sender_rate_limiting(self):
        sender = "frequent@example.com"
        # 1st, 2nd, 3rd messages ok
        for i in range(3):
            res = self.safety.check_incoming_email(
                sender_email=sender,
                subject=f"Message {i}",
                body="Hello support team",
                headers={},
            )
            self.assertTrue(res.is_safe)

        # 4th message exceeds limit of 3
        res4 = self.safety.check_incoming_email(
            sender_email=sender,
            subject="Message 4",
            body="Hello again",
            headers={},
        )
        self.assertFalse(res4.is_safe)
        self.assertTrue(res4.is_rate_limited)
        self.assertEqual(res4.action, "RATE_LIMIT_HOLD")

    def test_thread_loop_limit(self):
        thread = "thread-12345"
        # Record 3 replies to thread
        self.safety.record_dispatched_reply(thread)
        self.safety.record_dispatched_reply(thread)
        self.safety.record_dispatched_reply(thread)

        # Incoming email on same thread
        res = self.safety.check_incoming_email(
            sender_email="user@example.com",
            subject="Loop test",
            body="Still waiting",
            headers={},
            thread_id=thread,
        )
        self.assertFalse(res.is_safe)
        self.assertTrue(res.thread_loop_detected)
        self.assertEqual(res.action, "SUPPRESS_LOOP")


if __name__ == "__main__":
    unittest.main()
