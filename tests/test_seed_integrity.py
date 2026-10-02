"""Unit tests verifying seed data integrity and relational foreign key constraints."""

import unittest
import sqlite3
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.schema import create_tables
from src.db.seed_data import seed_database, verify_seed_integrity


class TestSeedIntegrity(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        create_tables(self.conn)
        seed_database(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_foreign_key_integrity(self):
        result = verify_seed_integrity(self.conn)
        self.assertEqual(result["foreign_key_violations"], 0)
        self.assertEqual(result["status"], "VALID")

    def test_entity_counts(self):
        result = verify_seed_integrity(self.conn)
        counts = result["counts"]
        self.assertGreaterEqual(counts["customers"], 5)
        self.assertGreaterEqual(counts["vehicles"], 5)
        self.assertGreaterEqual(counts["policies"], 5)
        self.assertGreaterEqual(counts["claims"], 4)
        self.assertGreaterEqual(counts["claim_documents"], 4)
        self.assertGreaterEqual(counts["payments"], 4)
        self.assertGreaterEqual(counts["renewals"], 2)
        self.assertGreaterEqual(counts["network_garages"], 4)
        self.assertGreaterEqual(counts["policy_coverage"], 4)
        self.assertGreaterEqual(counts["support_tickets"], 2)

    def test_claim_scenarios_exist(self):
        cur = self.conn.cursor()
        
        # Scenario 1: Approved claim with paid amount
        cur.execute("SELECT * FROM claims WHERE status = 'Approved' AND actual_payout > 0")
        self.assertIsNotNone(cur.fetchone())

        # Scenario 2: In Review claim awaiting documents
        cur.execute("SELECT * FROM claims WHERE status = 'In Review'")
        self.assertIsNotNone(cur.fetchone())

        # Scenario 3: Delayed claim with delay reason
        cur.execute("SELECT * FROM claims WHERE status = 'Delayed' AND delay_reason IS NOT NULL")
        self.assertIsNotNone(cur.fetchone())

        # Scenario 4: Rejected claim with policy-based rationale
        cur.execute("SELECT * FROM claims WHERE status = 'Rejected' AND rejection_reason IS NOT NULL")
        self.assertIsNotNone(cur.fetchone())


if __name__ == "__main__":
    unittest.main()
