import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.autoreply.sender import ReplyDispatcher
from src.ingestion.himalaya_client import HimalayaClient
from src.ingestion.preprocessor import StructuredEmail
from src.ai.reasoning_engine import TriageDecision


class TestSenderSafetyRegression(unittest.TestCase):
    def setUp(self):
        self.client = Mock(spec=HimalayaClient)
        self.dispatcher = ReplyDispatcher(
            himalaya_client=self.client,
            dry_run=True,
        )
        self.email = StructuredEmail(
            message_id="safety-test-001",
            sender_name="Test Customer",
            sender_email="customer@example.com",
            recipient="support@example.com",
            subject="Test inquiry",
            received_date="2026-09-29T10:00:00Z",
            body_text="Please provide an update.",
        )

    def make_decision(self, **overrides):
        fields = dict(
            category="Policy & Coverage",
            intent="POLICY_DETAILS",
            priority="Medium",
            urgency_score=5,
            sentiment="Neutral",
            escalation_needed=False,
            escalation_reason="",
            summary="Test inquiry",
            policy_number=None,
            claim_number=None,
            routed_to="Policy Servicing",
            suggested_reply="Here is your requested update.",
            human_review_required=False,
        )
        fields.update(overrides)
        return TriageDecision(**fields)

    def test_escalation_blocked_even_with_reply(self):
        decision = self.make_decision(escalation_needed=True)

        sent, audit = self.dispatcher.dispatch(
            self.email, decision, "A reply exists."
        )

        self.assertFalse(sent)
        self.assertEqual(audit.status, "SUPPRESSED_HUMAN_REVIEW")
        self.client.send_email.assert_not_called()

    def test_human_review_blocked_even_with_reply(self):
        decision = self.make_decision(human_review_required=True)

        sent, audit = self.dispatcher.dispatch(
            self.email, decision, "A reply exists."
        )

        self.assertFalse(sent)
        self.assertEqual(audit.status, "SUPPRESSED_HUMAN_REVIEW")
        self.client.send_email.assert_not_called()

    def test_spam_blocked(self):
        decision = self.make_decision(intent="SPAM_OR_AUTOMATED")

        sent, audit = self.dispatcher.dispatch(
            self.email, decision, "A reply exists."
        )

        self.assertFalse(sent)
        self.assertEqual(audit.status, "SUPPRESSED_SPAM")
        self.client.send_email.assert_not_called()

    def test_identity_challenge_blocked_without_authorization(self):
        decision = self.make_decision(intent="IDENTITY_VERIFICATION")

        sent, audit = self.dispatcher.dispatch(
            self.email,
            decision,
            "Verification code: 123456",
            send_to="onfile@example.com",
        )

        self.assertFalse(sent)
        self.assertEqual(audit.status, "SUPPRESSED_IDENTITY_CHALLENGE")
        self.client.send_email.assert_not_called()

    def test_authorized_identity_challenge_dry_run(self):
        decision = self.make_decision(intent="IDENTITY_VERIFICATION")

        sent, audit = self.dispatcher.dispatch(
            self.email,
            decision,
            "Verification code: 123456",
            send_to="onfile@example.com",
            allow_identity_challenge=True,
        )

        self.assertTrue(sent)
        self.assertEqual(audit.status, "DRY_RUN")
        self.assertEqual(audit.recipient, "onfile@example.com")
        self.assertEqual(audit.subject, "Identity verification required")
        self.assertIsNone(audit.in_reply_to)
        self.client.send_email.assert_not_called()


if __name__ == "__main__":
    unittest.main()
