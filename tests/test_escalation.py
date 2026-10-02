"""Unit tests for the Human Review Escalation Engine."""

import unittest
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai.escalation import EscalationEngine


class TestEscalationEngine(unittest.TestCase):
    def setUp(self):
        self.context_with_customer = {
            "customer": {"customer_id": 1, "name": "Elena Rostova", "email": "elena.r@example.com"},
            "policies": [{"policy_number": "POL-2024-9901", "status": "Active"}],
            "claims": [],
            "tickets": [],
        }

    def test_legal_threat_escalation(self):
        decision = EscalationEngine.evaluate(
            subject="Terrible experience",
            body="My lawyer is preparing a lawsuit against your company for breach of contract.",
            intent="COMPLAINT",
            sentiment="Angry",
            confidence=0.95,
            db_context=self.context_with_customer,
        )
        self.assertTrue(decision.human_review_required)
        self.assertEqual(decision.priority, "Critical")
        self.assertIn("Legal", decision.routed_to)
        self.assertTrue(decision.suppress_auto_reply)
        self.assertTrue(any("Legal threat" in r for r in decision.reasons))

    def test_regulatory_complaint_escalation(self):
        decision = EscalationEngine.evaluate(
            subject="Reporting you to the state",
            body="I am filing a formal complaint with the Insurance Commissioner and the Ombudsman.",
            intent="COMPLAINT",
            sentiment="Angry",
            confidence=0.95,
            db_context=self.context_with_customer,
        )
        self.assertTrue(decision.human_review_required)
        self.assertEqual(decision.priority, "Critical")
        self.assertIn("Regulatory Compliance", decision.routed_to)
        self.assertTrue(any("Regulatory complaint" in r for r in decision.reasons))

    def test_fraud_suspicion_escalation(self):
        decision = EscalationEngine.evaluate(
            subject="Unauthorized policy on my name",
            body="Someone created a fake claim using stolen identity. This is fraud!",
            intent="FRAUD_SUSPICION",
            sentiment="Anxious",
            confidence=0.95,
            db_context=self.context_with_customer,
        )
        self.assertTrue(decision.human_review_required)
        self.assertEqual(decision.priority, "Critical")
        self.assertIn("Special Investigations Unit", decision.routed_to)

    def test_high_value_claim_dispute_escalation(self):
        context_with_high_claim = {
            "customer": {"customer_id": 1, "name": "Elena Rostova"},
            "claims": [
                {
                    "claim_number": "CLM-2024-07103",
                    "claimed_amount": 14500.0,
                    "status": "Delayed",
                }
            ],
            "tickets": [],
        }
        decision = EscalationEngine.evaluate(
            subject="Dispute delay on my transmission repair",
            body="Why is my $14,500 repair taking 3 weeks?",
            intent="CLAIM_DELAY",
            sentiment="Frustrated",
            confidence=0.95,
            db_context=context_with_high_claim,
            high_value_threshold=5000.0,
        )
        self.assertTrue(decision.human_review_required)
        self.assertEqual(decision.priority, "High")
        self.assertTrue(any("High-value claim dispute" in r for r in decision.reasons))

    def test_policy_cancellation_escalation(self):
        decision = EscalationEngine.evaluate(
            subject="Cancel my auto policy",
            body="Please cancel my insurance immediately as I sold the car.",
            intent="CANCELLATION",
            sentiment="Neutral",
            confidence=0.95,
            db_context=self.context_with_customer,
        )
        self.assertTrue(decision.human_review_required)
        self.assertIn("Retention", decision.routed_to)

    def test_low_confidence_escalation(self):
        decision = EscalationEngine.evaluate(
            subject="Unclear message",
            body="Random words gibberish 123",
            intent="GENERAL_QUERY",
            sentiment="Neutral",
            confidence=0.55,  # < 0.75
            db_context=self.context_with_customer,
        )
        self.assertTrue(decision.human_review_required)
        self.assertIn("Human Triage Review Queue", decision.routed_to)
        self.assertTrue(any("Low AI classification confidence" in r for r in decision.reasons))


if __name__ == "__main__":
    unittest.main()
