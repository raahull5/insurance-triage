"""Unit tests for the LLM Reasoning Engine and Structured Triage Prompt."""

import unittest
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai.reasoning_engine import LLMReasoningEngine, TriageDecision
from src.ingestion.preprocessor import StructuredEmail


class TestReasoningEngine(unittest.TestCase):
    def setUp(self):
        self.engine = LLMReasoningEngine()
        self.sample_context = {
            "customer": {
                "customer_id": 1,
                "name": "Sarah Jenkins",
                "email": "sarah.j@example.com",
                "phone": "+1-555-0192",
            },
            "policies": [
                {
                    "policy_number": "POL-2024-8831",
                    "policy_type": "Comprehensive Auto",
                    "status": "Active",
                    "deductible": 500.0,
                    "coverage_limit": 100000.0,
                    "start_date": "2024-01-01",
                    "end_date": "2025-01-01",
                }
            ],
            "claims": [
                {
                    "claim_number": "CLM-2024-04190",
                    "status": "Approved",
                    "claimed_amount": 1200.0,
                    "approved_amount": 1150.0,
                    "assigned_adjuster": "Michael Chang",
                    "claim_type": "Windshield Replacement",
                    "damage_description": "Crack across driver side windshield",
                }
            ],
            "renewals": [],
            "garages": [
                {
                    "name": "Apex Auto Center",
                    "address": "123 Main St",
                    "city": "Austin",
                    "phone": "+1-555-0101",
                    "rating": 4.8,
                }
            ],
            "extracted_entities": {
                "policy_numbers": ["POL-2024-8831"],
                "claim_numbers": ["CLM-2024-04190"],
            },
        }

    def test_claim_status_triage(self):
        email = StructuredEmail(
            message_id="101",
            sender_name="Sarah Jenkins",
            sender_email="sarah.j@example.com",
            recipient="claims@apexshield.com",
            subject="Update on Claim CLM-2024-04190",
            body_text="Hi, can you tell me the current status of my claim CLM-2024-04190?",
            received_date="2024-09-25T10:00:00Z",
            headers={},
        )
        decision = self.engine.analyze(email, self.sample_context)
        self.assertEqual(decision.intent, "CLAIM_STATUS")
        self.assertEqual(decision.category, "Claims")
        self.assertEqual(decision.priority, "High")
        self.assertEqual(decision.claim_number, "CLM-2024-04190")
        self.assertIn("CLM-2024-04190", decision.suggested_reply)
        self.assertIn("$1,150.00", decision.suggested_reply)
        self.assertFalse(decision.escalation_needed)

    def test_claim_delay_escalation(self):
        email = StructuredEmail(
            message_id="102",
            sender_name="Sarah Jenkins",
            sender_email="sarah.j@example.com",
            recipient="claims@apexshield.com",
            subject="Delayed Claim CLM-2024-04190",
            body_text="I have been waiting for weeks and my claim is delayed! Why is it taking too long?",
            received_date="2024-09-25T10:00:00Z",
            headers={},
        )
        decision = self.engine.analyze(email, self.sample_context)
        self.assertEqual(decision.intent, "CLAIM_DELAY")
        self.assertEqual(decision.priority, "Critical")
        self.assertEqual(decision.sentiment, "Frustrated")
        self.assertTrue(decision.escalation_needed)
        self.assertIn("Supervisor", decision.routed_to)

    def test_complaint_and_legal_threat(self):
        email = StructuredEmail(
            message_id="103",
            sender_name="Sarah Jenkins",
            sender_email="sarah.j@example.com",
            recipient="claims@apexshield.com",
            subject="Unacceptable service - speaking with my lawyer",
            body_text="This is terrible service. If this is not resolved today I will sue and contact the insurance commissioner!",
            received_date="2024-09-25T10:00:00Z",
            headers={},
        )
        decision = self.engine.analyze(email, self.sample_context)
        self.assertEqual(decision.intent, "COMPLAINT")
        self.assertEqual(decision.priority, "Critical")
        self.assertEqual(decision.sentiment, "Angry")
        self.assertTrue(decision.escalation_needed)
        self.assertIn("Executive", decision.routed_to)

    def test_emergency_roadside_triage(self):
        email = StructuredEmail(
            message_id="104",
            sender_name="Sarah Jenkins",
            sender_email="sarah.j@example.com",
            recipient="claims@apexshield.com",
            subject="Need tow truck stranded with flat tire",
            body_text="I am stranded on highway 101 with a flat tire, need emergency tow truck immediately!",
            received_date="2024-09-25T10:00:00Z",
            headers={},
        )
        decision = self.engine.analyze(email, self.sample_context)
        self.assertIn(decision.intent, ["TOWING_SERVICE", "ROADSIDE_ASSISTANCE"])
        self.assertEqual(decision.priority, "Critical")
        self.assertTrue(decision.escalation_needed)

    def test_spam_detection(self):
        email = StructuredEmail(
            message_id="105",
            sender_name="Mailer Daemon",
            sender_email="no-reply@external.com",
            recipient="claims@apexshield.com",
            subject="Automatic reply: Out of office",
            body_text="I am out of the office until Monday.",
            received_date="2024-09-25T10:00:00Z",
            headers={"Auto-Submitted": "auto-replied"},
        )
        decision = self.engine.analyze(email, self.sample_context)
        self.assertEqual(decision.intent, "SPAM_OR_AUTOMATED")
        self.assertEqual(decision.priority, "Low")
        self.assertEqual(decision.routed_to, "#archive")
        self.assertEqual(decision.suggested_reply, "")


if __name__ == "__main__":
    unittest.main()
