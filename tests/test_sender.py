"""Unit tests for the Automated Email Reply Dispatcher."""

import unittest
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.autoreply.sender import ReplyDispatcher, DispatchAuditRecord
from src.ai.reasoning_engine import TriageDecision
from src.ingestion.preprocessor import StructuredEmail
from src.ingestion.himalaya_client import HimalayaClient


class TestReplyDispatcher(unittest.TestCase):
    def setUp(self):
        self.mock_client = HimalayaClient(mock_mode=True)
        self.dispatcher = ReplyDispatcher(himalaya_client=self.mock_client, dry_run=True, rate_limit_per_minute=2)
        self.email = StructuredEmail(
            message_id="msg-1001",
            sender_name="Alice Wang",
            sender_email="alice.w@example.com",
            recipient="support@apexshield.com",
            subject="Policy inquiry",
            received_date="2024-09-25T12:00:00Z",
            body_text="What is my policy number?",
        )
        self.decision = TriageDecision(
            category="Policy & Coverage",
            intent="POLICY_DETAILS",
            priority="Medium",
            urgency_score=5,
            sentiment="Neutral",
            escalation_needed=False,
            escalation_reason="",
            summary="Policy query",
            policy_number="POL-2024-8831",
            claim_number=None,
            routed_to="Policy Servicing",
            suggested_reply="Dear Alice, your policy is POL-2024-8831.",
        )

    def test_successful_dry_run_dispatch(self):
        sent, record = self.dispatcher.dispatch(
            self.email, self.decision, "Dear Alice, your policy is POL-2024-8831."
        )
        self.assertTrue(sent)
        self.assertEqual(record.status, "DRY_RUN")
        self.assertEqual(record.recipient, "alice.w@example.com")
        self.assertEqual(record.in_reply_to, "msg-1001")
        self.assertEqual(len(self.dispatcher.get_audit_log()), 1)

    def test_duplicate_prevention(self):
        sent1, record1 = self.dispatcher.dispatch(
            self.email, self.decision, "First reply"
        )
        self.assertTrue(sent1)

        # Attempt same message_id again
        sent2, record2 = self.dispatcher.dispatch(
            self.email, self.decision, "Second reply"
        )
        self.assertFalse(sent2)
        self.assertEqual(record2.status, "DUPLICATE_SUPPRESSED")

    def test_rate_limiting(self):
        # Dispatch 2 messages (allowed)
        email1 = StructuredEmail("m1", "u1@ex.com", "U1", "s@ex.com", "Sub 1", "2024", "Body")
        email2 = StructuredEmail("m2", "u2@ex.com", "U2", "s@ex.com", "Sub 2", "2024", "Body")
        email3 = StructuredEmail("m3", "u3@ex.com", "U3", "s@ex.com", "Sub 3", "2024", "Body")

        self.dispatcher.dispatch(email1, self.decision, "Reply 1")
        self.dispatcher.dispatch(email2, self.decision, "Reply 2")
        
        # 3rd message should be rate limited (limit is 2)
        sent3, record3 = self.dispatcher.dispatch(email3, self.decision, "Reply 3")
        self.assertFalse(sent3)
        self.assertEqual(record3.status, "RATE_LIMITED")

    def test_empty_body_suppression(self):
        sent, record = self.dispatcher.dispatch(self.email, self.decision, "")
        self.assertFalse(sent)
        self.assertEqual(record.status, "SUPPRESSED_EMPTY_BODY")


if __name__ == "__main__":
    unittest.main()
