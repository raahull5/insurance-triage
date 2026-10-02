"""Standard library unittest suite for insurance triage foundation."""

import unittest
import sqlite3
from pathlib import Path
import sys

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.schema import create_tables
from src.db.seed_data import seed_database
from src.db.repository import InsuranceRepository
from src.ingestion.preprocessor import EmailPreprocessor, StructuredEmail
from src.ai.intents import INTENT_PRIORITY_MAP, Priority
from src.ai.safety import SafetyEngine
from src.ai.reasoning_engine import LLMReasoningEngine
from src.autoreply.reply_generator import ReplyGenerator


class TestInsuranceFoundation(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        create_tables(self.conn)
        seed_database(self.conn)
        self.repo = InsuranceRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_customer_identification(self):
        cust = self.repo.find_customer_by_email("sarah.jenkins@example.com")
        self.assertIsNotNone(cust)
        self.assertEqual(cust["id"], "CUST-1001")
        self.assertEqual(cust["name"], "Sarah Jenkins")

    def test_full_context_retrieval(self):
        ctx = self.repo.get_full_insurance_context("sarah.jenkins@example.com")
        self.assertTrue(ctx["identified"])
        self.assertGreaterEqual(len(ctx["policies"]), 1)
        self.assertGreaterEqual(len(ctx["claims"]), 1)
        self.assertGreaterEqual(len(ctx["vehicles"]), 1)

    def test_unidentified_customer(self):
        ctx = self.repo.get_full_insurance_context("unknown.person@example.com")
        self.assertFalse(ctx["identified"])
        self.assertIsNone(ctx["customer"])

    def test_email_html_stripping(self):
        html_raw = "<html><body><h3>Help with claim</h3><p>My car was hit.<script>alert('xss')</script></p></body></html>"
        cleaned = EmailPreprocessor.clean_html(html_raw)
        self.assertNotIn("alert", cleaned)
        self.assertIn("Help with claim", cleaned)
        self.assertIn("My car was hit.", cleaned)

    def test_spam_detection(self):
        is_spam, reason = SafetyEngine.is_spam_or_automated(
            "noreply@marketing.com", "You won the lottery!", "Claim your prize now!", {}
        )
        self.assertTrue(is_spam)

    def test_claim_status_reasoning(self):
        engine = LLMReasoningEngine()
        ctx = self.repo.get_full_insurance_context("sarah.jenkins@example.com")
        email = StructuredEmail(
            message_id="T1",
            sender_email="sarah.jenkins@example.com",
            sender_name="Sarah Jenkins",
            recipient="support@apexshield.com",
            subject="Status of claim CLM-2024-09112",
            received_date="2024-09-25",
            body_text="Hi, can you tell me what is happening with my claim?",
        )
        decision = engine.analyze(email, ctx)
        self.assertEqual(decision.intent, "CLAIM_STATUS")
        self.assertEqual(decision.priority, Priority.HIGH.value)
        self.assertIn("CLM-2024-09112", decision.suggested_reply)


if __name__ == "__main__":
    unittest.main()
