"""Unit tests for the CSV Triage Export Pipeline."""

import csv
import unittest
import sys
import tempfile
import os
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.reporting.csv_exporter import CSVExporter, CSV_COLUMNS


class TestCSVExporter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.csv_path = Path(self.temp_dir.name) / "triage_results.csv"
        self.exporter = CSVExporter(self.csv_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_header_creation(self):
        self.assertTrue(self.csv_path.exists())
        records = self.exporter.read_records()
        self.assertEqual(len(records), 0)

    def test_append_record_and_pii_redaction(self):
        sample_record = {
            "email_id": "MSG-9901",
            "sender": "david@example.com",
            "subject": "Payment issue with SSN 123-45-6789",
            "received_date": "2024-09-25T10:00:00Z",
            "intent": "PAYMENT_ISSUE",
            "category": "Billing & Payments",
            "priority": "High",
            "urgency_score": 8,
            "sentiment": "Frustrated",
            "escalation_status": "Escalated",
            "policy_number": "POL-2024-8831",
            "claim_number": "",
            "routing_desk": "Billing & Payments",
            "reply_status": "Suppressed",
            "summary": "Customer card 4111-2222-3333-4444 was declined.",
        }
        self.exporter.append_record(sample_record)

        records = self.exporter.read_records()
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r["email_id"], "MSG-9901")
        self.assertEqual(r["intent"], "PAYMENT_ISSUE")
        self.assertNotIn("123-45-6789", r["subject"])
        self.assertIn("[REDACTED-SSN]", r["subject"])
        self.assertNotIn("4111-2222-3333-4444", r["summary"])
        self.assertIn("[REDACTED-CARD]", r["summary"])

    def test_filter_records(self):
        records = [
            {
                "email_id": "M1",
                "sender": "a@ex.com",
                "subject": "Sub 1",
                "received_date": "2024-09-20T10:00:00Z",
                "intent": "CLAIM_STATUS",
                "category": "Claims",
                "priority": "High",
                "urgency_score": 7,
                "sentiment": "Neutral",
                "escalation_status": "Auto-Resolved",
            },
            {
                "email_id": "M2",
                "sender": "b@ex.com",
                "subject": "Sub 2",
                "received_date": "2024-09-24T10:00:00Z",
                "intent": "COMPLAINT",
                "category": "General Support",
                "priority": "Critical",
                "urgency_score": 10,
                "sentiment": "Angry",
                "escalation_status": "Escalated",
            },
        ]
        self.exporter.export_all(records)

        # Filter by priority
        crit = self.exporter.filter_records(priority="Critical")
        self.assertEqual(len(crit), 1)
        self.assertEqual(crit[0]["email_id"], "M2")

        # Filter by intent
        claims = self.exporter.filter_records(intent="CLAIM_STATUS")
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["email_id"], "M1")


class TestReplyStatusVocabulary(unittest.TestCase):
    """Regression: a real 'SENT' reply must not be labelled Held-for-Review.

    The dispatcher writes the literal uppercase status, but the exporter's
    escalation inference matched only 'Sent'/'Dry-Run'/'DRY_RUN' exactly, so
    an actually-sent reply was reported as held for human review in the CSV.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "out.csv"
        self.exporter = CSVExporter(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def _escalation_for(self, reply_status):
        record = {
            "email_id": "M1",
            "sender_email": "a@example.com",
            "subject": "Claim status",
            "received_date": "2024-09-24T10:00:00Z",
            "intent": "CLAIM_STATUS",
            "category": "Claims",
            "priority": "High",
            "urgency_score": 5,
            "sentiment": "Neutral",
            "reply_status": reply_status,
            "escalation_needed": 0,
            "human_review_required": 0,
        }
        self.exporter.export_all([record])
        with open(self.path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))[0]

    def test_uppercase_sent_is_auto_resolved(self):
        self.assertEqual(self._escalation_for("SENT")["escalation_status"], "Auto-Resolved")

    def test_titlecase_sent_still_auto_resolves(self):
        self.assertEqual(self._escalation_for("Sent")["escalation_status"], "Auto-Resolved")

    def test_dry_run_is_auto_resolved(self):
        self.assertEqual(self._escalation_for("DRY_RUN")["escalation_status"], "Auto-Resolved")

    def test_suppressed_is_held_for_review(self):
        self.assertEqual(
            self._escalation_for("SUPPRESSED_HUMAN_REVIEW")["escalation_status"],
            "Held-for-Review",
        )

    def test_failed_send_is_held_for_review(self):
        self.assertEqual(self._escalation_for("FAILED")["escalation_status"], "Held-for-Review")


if __name__ == "__main__":
    unittest.main()
