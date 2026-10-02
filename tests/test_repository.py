"""Unit tests for repository context retrieval and customer identification."""

import unittest
import sqlite3
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.schema import create_tables
from src.db.seed_data import seed_database
from src.db.repository import InsuranceRepository


class TestInsuranceRepository(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        create_tables(self.conn)
        seed_database(self.conn)
        self.repo = InsuranceRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_identify_by_email(self):
        ctx = self.repo.get_full_insurance_context("marcus.vance@example.com")
        self.assertTrue(ctx["identified"])
        self.assertEqual(ctx["matched_via"], "email")
        self.assertEqual(ctx["customer"]["name"], "Marcus Vance")
        self.assertEqual(len(ctx["policies"]), 1)
        self.assertEqual(ctx["policies"][0]["policy_number"], "POL-TX-2024-3401")
        self.assertGreaterEqual(len(ctx["garages"]), 1)

    def test_identify_unmatched_email_via_policy_number_in_body(self):
        # Sender email is not in DB, but mentions their policy number
        ctx = self.repo.get_full_insurance_context(
            email="alternate-address@personal.org",
            subject="Question regarding my coverage",
            body="Hello, I am writing regarding policy POL-TX-2024-3401."
        )
        self.assertTrue(ctx["identified"])
        self.assertIn("policy_number", ctx["matched_via"])
        self.assertEqual(ctx["customer"]["name"], "Marcus Vance")

    def test_identify_unmatched_email_via_claim_number_in_subject(self):
        # Sender email is not in DB, but mentions claim number
        ctx = self.repo.get_full_insurance_context(
            email="lawyer-office@legal.com",
            subject="Inquiry on CLM-2024-09112",
            body="Please provide claim file status."
        )
        self.assertTrue(ctx["identified"])
        self.assertIn("claim_number", ctx["matched_via"])
        self.assertEqual(ctx["customer"]["name"], "Sarah Jenkins")

    def test_unidentified_customer_with_no_matches(self):
        ctx = self.repo.get_full_insurance_context(
            email="stranger@nowhere.com",
            subject="General question",
            body="Do you sell insurance for boats?"
        )
        self.assertFalse(ctx["identified"])
        self.assertIsNone(ctx["customer"])
        self.assertEqual(len(ctx["policies"]), 0)
        self.assertEqual(len(ctx["claims"]), 0)


if __name__ == "__main__":
    unittest.main()
