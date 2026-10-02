"""Unit tests for the Context-Aware Auto-Reply Generator."""

import unittest
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.autoreply.reply_generator import ReplyGenerator
from src.ai.reasoning_engine import TriageDecision
from src.ingestion.preprocessor import StructuredEmail


class TestReplyGenerator(unittest.TestCase):
    def setUp(self):
        self.generator = ReplyGenerator()
        self.email = StructuredEmail(
            message_id="201",
            sender_name="David Miller",
            sender_email="david.m@example.com",
            recipient="support@apexshield.com",
            subject="Question about my claim",
            received_date="2024-09-25T12:00:00Z",
            body_text="Can I get an update?",
        )
        self.context = {
            "customer": {"customer_id": 1, "name": "David Miller"},
            "policies": [
                {
                    "policy_number": "POL-2024-4412",
                    "policy_type": "Comprehensive Auto",
                    "status": "Active",
                    "start_date": "2024-01-01",
                    "end_date": "2025-01-01",
                    "deductible": 500.0,
                    "coverage_limit": 50000.0,
                }
            ],
            "claims": [
                {
                    "claim_number": "CLM-2024-09112",
                    "status": "In-Review",
                    "claimed_amount": 3400.0,
                    "approved_amount": 0.0,
                    "assigned_adjuster": "Sarah Connor",
                    "claim_type": "Front Bumper Damage",
                }
            ],
            "renewals": [
                {
                    "renewal_due_date": "2024-12-15",
                    "quoted_premium": 1250.0,
                    "ncb_discount_pct": 20,
                    "status": "Pending",
                }
            ],
            "garages": [
                {
                    "name": "Precision Auto Body",
                    "address": "456 Oak Ave",
                    "city": "Dallas",
                    "phone": "+1-555-0199",
                    "rating": 4.9,
                }
            ],
        }

    def test_markdown_stripping(self):
        raw_text = "**Dear David**, here is your *claim status*:\n## Details\n- Item 1\n- Item 2"
        clean = ReplyGenerator.clean_markdown_to_plaintext(raw_text)
        self.assertNotIn("**", clean)
        self.assertNotIn("##", clean)
        self.assertIn("Dear David", clean)

    def test_claim_status_generation(self):
        decision = TriageDecision(
            category="Claims",
            intent="CLAIM_STATUS",
            priority="High",
            urgency_score=7,
            sentiment="Neutral",
            escalation_needed=False,
            escalation_reason="",
            summary="Claim status request",
            policy_number="POL-2024-4412",
            claim_number="CLM-2024-09112",
            routed_to="Claims Desk",
            suggested_reply="",
        )
        reply = self.generator.generate_reply(self.email, decision, self.context)
        self.assertIsNotNone(reply)
        self.assertIn("CLM-2024-09112", reply)
        self.assertIn("In-Review", reply)
        self.assertIn("Sarah Connor", reply)
        self.assertIn("$3,400.00", reply)
        self.assertIn("Next Steps:", reply)

    def test_claim_rejection_dispute_generation(self):
        context_rejected = dict(self.context)
        context_rejected["claims"] = [
            {
                "claim_number": "CLM-2024-03110",
                "status": "Rejected",
                "rejection_reason": "Commercial rideshare exclusion clause §4.2",
            }
        ]
        decision = TriageDecision(
            category="Claims",
            intent="CLAIM_REJECTION_DISPUTE",
            priority="Critical",
            urgency_score=9,
            sentiment="Angry",
            escalation_needed=True,
            escalation_reason="Dispute",
            summary="Disputing rejection",
            policy_number=None,
            claim_number="CLM-2024-03110",
            routed_to="Appeals Desk",
            suggested_reply="",
        )
        reply = self.generator.generate_reply(self.email, decision, context_rejected)
        self.assertIn("CLM-2024-03110", reply)
        self.assertIn("Appeals", reply)
        self.assertIn("Commercial rideshare exclusion", reply)

    def test_emergency_roadside_generation(self):
        decision = TriageDecision(
            category="Garage & Roadside",
            intent="ROADSIDE_ASSISTANCE",
            priority="Critical",
            urgency_score=10,
            sentiment="Urgent",
            escalation_needed=True,
            escalation_reason="Roadside emergency",
            summary="Emergency roadside assistance",
            policy_number=None,
            claim_number=None,
            routed_to="Emergency Dispatch",
            suggested_reply="",
        )
        reply = self.generator.generate_reply(self.email, decision, self.context)
        self.assertIn("1-800-555-ROAD", reply)
        self.assertIn("Safety Instructions", reply)

    def test_spam_suppression(self):
        decision = TriageDecision(
            category="System & Spam",
            intent="SPAM_OR_AUTOMATED",
            priority="Low",
            urgency_score=1,
            sentiment="Neutral",
            escalation_needed=False,
            escalation_reason="",
            summary="Spam message",
            policy_number=None,
            claim_number=None,
            routed_to="#archive",
            suggested_reply="",
        )
        reply = self.generator.generate_reply(self.email, decision, self.context)
        self.assertIsNone(reply)


if __name__ == "__main__":
    unittest.main()
