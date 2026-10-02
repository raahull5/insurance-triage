"""End-to-end tests for the complete Insurance Triage system (PDF Step 23).

These tests drive the REAL pipeline against a REAL seeded SQLite database and
assert on the full chain required by Step 23:

  historical emails ignored -> new email detected -> message cleaned ->
  customer identified -> relevant insurance records retrieved ->
  issue classified with a canonical intent -> result stored in SQLite AND
  CSV -> support ticket created/updated -> dashboard updated -> reply sent.

They also cover the mandated scenarios: claim-status, new-claim, policy,
renewal, payment, complaint, fraud-suspicion, spam and automated-email, plus
duplicate-never-processed-twice and safe failure handling.

Mock Himalaya is used for the transport layer only; every other component
(ingestion, preprocessing, identification, context retrieval, reasoning,
safety, threading, persistence, CSV, dashboard) is the production code path.
"""

import csv
import io
import json
import logging
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config import AppConfig
from src.core.resilience import OutcomeStatus
from src.dashboard.data_service import DashboardDataService
from src.db.database import Database
from src.db.repository import InsuranceRepository
from src.db.seed_data import seed_database
from src.pipeline.autonomous_runner import AutonomousPipeline
from src.reporting.csv_exporter import CSVExporter

# Enable warnings during diagnosis so transient LLM failures and retries
# are visible in test output. Restore the original disable() call after diagnosis.
logging.disable(logging.NOTSET)
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


# --------------------------------------------------------------------------- #
# Canonical seed identities, read from the real schema.
# --------------------------------------------------------------------------- #
SARAH = "sarah.jenkins@example.com"          # CUST-1001, CLM-2024-09112 (In Review)
MARCUS = "marcus.vance@example.com"          # CUST-1002, CLM-2024-04190 (Approved)
ELENA = "elena.rostova@example.com"          # CUST-1003, CLM-2024-07103 (Delayed)
DAVID = "david.chen@example.com"             # CUST-1004
PRIYA = "priya.patel@example.com"            # CUST-1005
STRANGER = "unknown.sender@notacustomer.com"

# The exact 15-column contract required by the CSV reporting step.
EXPECTED_CSV_COLUMNS = [
    "email_id", "sender", "subject", "received_date", "intent", "category",
    "priority", "urgency_score", "sentiment", "escalation_status",
    "policy_number", "claim_number", "routing_desk", "reply_status", "summary",
]


def make_raw(sender, subject, body, msg_id, date="Thu, 25 Sep 2026 10:00:00 +0000"):
    """Build a raw envelope in the exact shape Himalaya's JSON produces.

    `from`/`to` are LISTS of {name, addr} and `body` is a list of MIME parts;
    that is what `himalaya message read -o json` emits, and the preprocessor
    regression tests pin that shape.
    """
    return {
        "id": msg_id,
        "message_id": f"<{msg_id}@apexshield.com>",
        "from": [{"name": sender.split("@")[0].replace(".", " ").title(), "addr": sender}],
        "to": [{"name": "Apex Shield Support", "addr": "support@apexshield.com"}],
        "subject": subject,
        "body": [{"content": body}],
        "date": date,
    }


class EndToEndTestBase(unittest.TestCase):
    """A real seeded database + a real pipeline, in a temp directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "e2e.db"
        self.csv_path = self.root / "triage_results.csv"
        self.wm_path = self.root / "watermark.json"

        self.db = Database(str(self.db_path))
        self.db.initialize()
        conn = self.db.get_connection()
        try:
            seed_database(conn)
        finally:
            conn.close()

        self.config = self._make_config()
        self.pipeline = AutonomousPipeline(
            self.config, self.db, mock_mode=True, dry_run=True
        )
        # Replies are generated and audited but never leave the machine.
        self.config.autoreply.enabled = True

    def _make_config(self):
        cfg = AppConfig()
        cfg.system.db_path = self.db_path
        cfg.system.watermark_file = self.wm_path
        cfg.system.csv_report_path = self.csv_path
        return cfg

    def tearDown(self):
        self.tmp.cleanup()

    # ---- helpers ------------------------------------------------------- #
    def raw(self, sender, subject, body, msg_id):
        return make_raw(sender, subject, body, msg_id)

    def conn(self):
        return self.db.get_connection()

    def repo(self):
        c = self.conn()
        return InsuranceRepository(c), c

    def triage_row(self, email_id):
        """Return the stored triage record for `email_id` as a dict."""
        c = self.conn()
        c.row_factory = sqlite3.Row
        try:
            return c.execute(
                "SELECT * FROM triage_records WHERE email_id = ?", (email_id,)
            ).fetchone()
        finally:
            c.close()

    def triage_count(self, email_id):
        return 0 if self.triage_row(email_id) is None else 1

    def csv_rows(self):
        if not self.csv_path.exists():
            return []
        with open(self.csv_path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def customer_for(self, email):
        c = self.conn()
        try:
            return c.execute(
                "SELECT id FROM customers WHERE email = ?", (email,)
            ).fetchone()
        finally:
            c.close()


class TestFullChainHappyPath(EndToEndTestBase):
    """The complete Step 23 chain for a claim-status email."""

    def test_claim_status_email_runs_the_whole_chain(self):
        mid = "E2E-CLAIM-001"
        outcome = self.pipeline.process_email(
            self.raw(
                SARAH,
                "Status of my claim CLM-2024-09112",
                "Hello, I have been waiting two weeks for an update on my claim. "
                "Could you please tell me where it currently stands? Thank you.",
                mid,
            )
        )

        # 1. Processed successfully.
        self.assertEqual(outcome.status, OutcomeStatus.COMPLETED)

        # 2. Customer identified from the sender address.
        self.assertTrue(self.customer_for(SARAH))
        record = self.triage_row(mid)
        self.assertIsNotNone(record, "triage record was not stored")
        row = record
        self.assertEqual(row["customer_id"], "CUST-1001")

        # 3. Relevant insurance record retrieved and stored on the record.
        self.assertEqual(row["claim_number"], "CLM-2024-09112")
        self.assertEqual(row["policy_number"], "POL-IL-2024-8819")

        # 4. Classified with a canonical intent + priority.
        self.assertEqual(row["intent"], "CLAIM_STATUS")
        self.assertIn(row["priority"], ("Critical", "High", "Medium", "Low"))
        self.assertIsNotNone(row["summary"])

        # 5. Reply generated and grounded in the real claim data.
        self.assertIn("CLM-2024-09112", row["suggested_reply"])
        self.assertRegex(
             row["suggested_reply"],
             r"(?i)\bin review\b"
        )

        # 6. Stored in CSV with exactly the 15 required columns.
        rows = self.csv_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(list(rows[0].keys()), EXPECTED_CSV_COLUMNS)
        self.assertEqual(rows[0]["email_id"], mid)
        self.assertEqual(rows[0]["intent"], "CLAIM_STATUS")
        self.assertEqual(rows[0]["claim_number"], "CLM-2024-09112")

        # 7. Thread resolution linked the email to the existing seeded ticket for
        #    this claim. A new ticket must NOT be created for an inquiry that
        #    already has one -- the whole point of the 4-level resolver.
        c = self.conn()
        try:
            new_tickets = c.execute(
                "SELECT ticket_number FROM support_tickets WHERE email_id = ?",
                (mid,),
            ).fetchall()
            existing = c.execute(
                "SELECT ticket_number FROM support_tickets "
                "WHERE claim_id = (SELECT id FROM claims "
                "WHERE claim_number = 'CLM-2024-09112')"
            ).fetchall()
        finally:
            c.close()
        self.assertTrue(
            existing, "the seeded claim ticket disappeared"
        )
        self.assertEqual(
            new_tickets, [], "a duplicate ticket was opened for an existing claim"
        )
        self.assertIn("APPEND", self.pipeline.last_ticket_action)

        # 8. Reply dispatched (dry-run) and marked on the record.
        self.assertEqual(row["reply_status"], "DRY_RUN")

        # 9. Watermark advanced past this durable outcome.
        self.assertEqual(self.pipeline.watermark_mgr.current_watermark, mid)

        # 10. Dashboard sees it.
        c = self.conn()
        try:
            svc = DashboardDataService(c)
            payload = svc.get_dashboard_payload()
        finally:
            c.close()
        ids = [r["id"] for r in payload["queue"]]
        self.assertIn(row["id"], ids, "dashboard queue does not show the new record")
        self.assertEqual(payload["kpis"]["total_inquiries"], 1)


# --------------------------------------------------------------------------- #
# Step 23 mandates these scenarios explicitly.
# --------------------------------------------------------------------------- #
SCENARIOS = [
    # (label, sender, subject, body, expected_intent)
    (
        "claim_status",
        SARAH,
        "Update on claim CLM-2024-09112 please",
        "Could you confirm the current status of my claim and who is handling it?",
        "CLAIM_STATUS",
    ),
    (
        "new_claim",
        MARCUS,
        "I need to file a new claim",
        "I had an accident yesterday and would like to open a new claim for the "
        "damage to my vehicle.",
        "NEW_CLAIM",
    ),
    (
        "policy_document",
        DAVID,
        "Request for a copy of my policy document",
        "Could you please send me a copy of my policy document for my records?",
        "CERTIFICATE_OF_INSURANCE",  # PDF Step 6 "POLICY_DOCUMENT" is an alias
    ),
    (
        "renewal",
        PRIYA,
        "Renewal of my insurance policy",
        "My policy is coming up for renewal and I would like to arrange the "
        "renewal with you.",
        "RENEWAL",
    ),
    (
        "payment",
        ELENA,
        "Question about my premium payment",
        "I made a premium payment last week and want to confirm it was applied "
        "to my policy correctly.",
        "PAYMENT_ISSUE",  # PDF Step 6 "PAYMENT" is an alias
    ),
    (
        "complaint",
        MARCUS,
        "Formal complaint about repeated delays",
        "This is the third time I have been kept waiting. I am lodging a formal "
        "complaint about the repeated delays in handling my case.",
        "COMPLAINT",
    ),
    (
        "fraud_suspicion",
        DAVID,
        "Suspicious activity on my policy",
        "I noticed suspicious transactions and activity on my policy that I did "
        "not authorise. I believe this may be fraud.",
        "FRAUD_SUSPICION",
    ),
]


class TestMandatedScenarios(EndToEndTestBase):
    """Claim-status, new-claim, policy, renewal, payment, complaint, fraud."""

    def test_each_scenario_classifies_to_its_canonical_intent(self):
        for label, sender, subject, body, expected in SCENARIOS:
            with self.subTest(scenario=label):
                mid = f"E2E-{label.upper()}"

                outcome = self.pipeline.process_email(
                    self.raw(sender, subject, body, mid)
                )

                row = self.triage_row(mid)

                # Include processing diagnostics if no triage record exists.
                self.assertIsNotNone(
                    row,
                    (
                        f"{mid}: no triage record stored. "
                        f"Outcome status="
                        f"{getattr(outcome.status, 'value', outcome.status)}, "
                        f"stage={outcome.stage}, "
                        f"detail={outcome.detail}"
                    ),
                )

                self.assertEqual(
                    row["intent"],
                    expected,
                    f"{label}: misclassified as {row['intent']}",
                )

                # Every scenario must still be persisted in both stores.
                self.assertIn(
                    mid,
                    [r["email_id"] for r in self.csv_rows()],
                )

    def test_critical_intents_escalate_to_a_human(self):
        """COMPLAINT and FRAUD_SUSPICION must never auto-reply silently."""
        for label, sender, subject, body, _ in SCENARIOS:
            if label not in ("complaint", "fraud_suspicion"):
                continue
            with self.subTest(scenario=label):
                mid = f"E2E-ESC-{label.upper()}"
                outcome = self.pipeline.process_email(
                    self.raw(sender, subject, body, mid)
                )
                record = self.triage_row(mid)
                self.assertIsNotNone(
                    record,
                    (
                        f"{mid}: no triage record stored. "
                        f"Outcome status="
                        f"{getattr(outcome.status, 'value', outcome.status)}, "
                        f"stage={outcome.stage}, "
                        f"detail={outcome.detail}"
                    ),
                )
                row = record
                escalated = str(row["escalation_status"] or "").lower()
                self.assertIn(
                    escalated,
                    ("escalated", "human_review", "pending", "review"),
                    f"{label}: expected human escalation, got {escalated!r}",
                )
                # An escalated case must not have been auto-replied to.
                self.assertNotEqual(row["reply_status"], "DRY_RUN")


class TestSpamAndAutomatedEmails(EndToEndTestBase):
    """Spam, newsletters and known automated senders (PDF Step 14 contract)."""

    def test_spam_is_archived_with_urgency_one_and_never_replied(self):
        mid = "E2E-SPAM-001"
        self.pipeline.process_email(
            self.raw(
                "winner@lotto-spam.biz",
                "CONGRATULATIONS!!! You WON $1,000,000 — claim now",
                "Click here now to claim your prize!!! Act immediately, "
                "100% guaranteed, no risk, free money!!!",
                mid,
            )
        )
        row = self.triage_row(mid)
        self.assertIsNotNone(row, mid + ": no triage record stored")
        self.assertEqual(row["priority"], "Low")
        self.assertEqual(row["urgency_score"], 1)
        self.assertEqual(row["routing_desk"], "#archive")
        # No auto-reply to spam, ever. "Suppressed" is the truthful record.
        self.assertIn(
            row["reply_status"],
            ("Suppressed", "SKIPPED", "SUPPRESSED", "SUPPRESSED_SPAM",
             "Duplicate", "Rate-Limited"),
            f"spam must not be auto-replied, got {row['reply_status']!r}",
        )
        self.assertNotEqual(row["reply_status"], "DRY_RUN")
        self.assertNotEqual(row["reply_status"], "Sent")
        # And the dispatcher audit must show no dispatch attempt at all.
        self.assertEqual(
            [r for r in self.pipeline.reply_sender.get_audit_log()],
            [],
            "a dispatch was attempted for a spam email",
        )

    def test_automated_no_reply_sender_is_not_auto_replied(self):
        mid = "E2E-AUTO-001"
        self.pipeline.process_email(
            self.raw(
                "notifications@calendar-notifier.io",
                "Your weekly digest",
                "Here is your automated weekly summary of activity.",
                mid,
            )
        )
        record = self.triage_row(mid)
        row = record
        self.assertIn(
            str(row["routing_desk"] or "").lower(),
            ("#archive", "spam filter", "automation", "none", ""),
            f"automated sender routed to {row['routing_desk']!r}",
        )
        self.assertNotEqual(row["reply_status"], "DRY_RUN")

    def test_spam_is_still_recorded_for_audit(self):
        """Suppressed means no reply, not no record."""
        mid = "E2E-SPAM-002"
        self.pipeline.process_email(
            self.raw(
                "noreply@promo-deals.example",
                "MEGA DISCOUNT 90% OFF TODAY ONLY",
                "Buy now! Best prices ever! Limited time offer, click here!!!",
                mid,
            )
        )
        self.assertIsNotNone(
            self.triage_row(mid), "spam must still be stored for the dashboard"
        )


class TestDuplicatePrevention(EndToEndTestBase):
    """An email must never be processed twice, even across restarts."""

    def test_same_email_processed_only_once(self):
        mid = "E2E-DUP-001"
        first = self.pipeline.process_email(
            self.raw(SARAH, "Status of my claim", "Please update me.", mid)
        )
        second = self.pipeline.process_email(
            self.raw(SARAH, "Status of my claim", "Please update me.", mid)
        )
        self.assertEqual(first.status, OutcomeStatus.COMPLETED)
        self.assertNotEqual(second.status, OutcomeStatus.COMPLETED)
        self.assertEqual(self.pipeline.stats.emails_skipped_duplicate, 1)
        self.assertEqual(self.triage_count(mid), 1)
        self.assertEqual(len(self.csv_rows()), 1)

    def test_duplicate_survives_a_process_restart(self):
        """Watermark AND the unique constraint both protect, not just one."""
        mid = "E2E-DUP-002"
        self.pipeline.process_email(
            self.raw(SARAH, "Status of my claim", "Please update me.", mid)
        )
        # Fresh pipeline + fresh repository over the SAME database file.
        self.pipeline = AutonomousPipeline(
            self.config, self.db, mock_mode=True, dry_run=True
        )
        again = self.pipeline.process_email(
            self.raw(SARAH, "Status of my claim", "Please update me.", mid)
        )
        self.assertNotEqual(again.status, OutcomeStatus.COMPLETED)
        self.assertEqual(self.triage_count(mid), 1)

    def test_only_ids_strictly_above_the_watermark_are_processed(self):
        """PDF Step 3: process strictly-greater IDs only."""
        self.pipeline.watermark_mgr.save("1000")
        self.pipeline.himalaya = MagicMock()
        self.pipeline.himalaya.list_inbox.return_value = [
            {"id": "0999", "flags": []},
            {"id": "1000", "flags": []},
            {"id": "1001", "flags": []},
            {"id": "1002", "flags": []},
        ]
        payloads = {
            "0999": self.raw(SARAH, "Old", "Old message.", "0999"),
            "1000": self.raw(SARAH, "Equal", "Equal message.", "1000"),
            "1001": self.raw(SARAH, "New one", "New message one.", "1001"),
            "1002": self.raw(SARAH, "New two", "New message two.", "1002"),
        }
        self.pipeline.himalaya.read_message.side_effect = lambda i: payloads[str(i)]

        processed = self.pipeline.poll_cycle()

        self.assertEqual(processed, 2)
        self.assertIsNone(self.triage_row("0999"), "historical email was processed")
        self.assertIsNone(self.triage_row("1000"), "watermark-equal email was processed")
        self.assertIsNotNone(self.triage_row("1001"))
        self.assertIsNotNone(self.triage_row("1002"))
        self.assertEqual(self.pipeline.watermark_mgr.current_watermark, "1002")


class TestHistoricalEmailsIgnored(EndToEndTestBase):
    """First run must adopt the current inbox state, not replay history."""

    def test_first_run_adopts_current_inbox_without_processing(self):
        self.assertIsNone(self.pipeline.watermark_mgr.current_watermark)
        self.pipeline.himalaya = MagicMock()
        self.pipeline.himalaya.list_inbox.return_value = [
            {"id": "0001", "flags": []},
            {"id": "0002", "flags": []},
            {"id": "0003", "flags": []},
        ]
        processed = self.pipeline.poll_cycle()
        self.assertEqual(processed, 0)
        self.assertEqual(self.triage_count("0001"), 0)
        self.assertEqual(self.triage_count("0002"), 0)
        self.assertEqual(self.triage_count("0003"), 0)
        self.assertEqual(self.pipeline.watermark_mgr.current_watermark, "0003")

    def test_only_newly_arrived_email_is_processed_next_cycle(self):
        self.pipeline.himalaya = MagicMock()
        self.pipeline.himalaya.list_inbox.return_value = [
            {"id": "0001", "flags": []},
            {"id": "0002", "flags": []},
        ]
        self.pipeline.poll_cycle()  # adopts watermark = 0002

        # A new message arrives.
        payloads = {
            "0001": self.raw(SARAH, "Old", "Old.", "0001"),
            "0002": self.raw(SARAH, "Old two", "Old two.", "0002"),
            "0003": self.raw(
                SARAH, "Brand new question", "A genuinely new message.", "0003"
            ),
        }
        self.pipeline.himalaya.list_inbox.return_value = [
            {"id": "0001", "flags": []},
            {"id": "0002", "flags": []},
            {"id": "0003", "flags": []},
        ]
        self.pipeline.himalaya.read_message.side_effect = lambda i: payloads[str(i)]
        processed = self.pipeline.poll_cycle()

        self.assertEqual(processed, 1)
        self.assertEqual(self.triage_count("0003"), 1)
        self.assertEqual(self.triage_count("0001"), 0)
        self.assertEqual(self.pipeline.watermark_mgr.current_watermark, "0003")


class TestUnidentifiedCustomers(EndToEndTestBase):
    """An unmatched sender must be marked, never guessed at."""

    def test_unknown_sender_is_marked_unidentified_not_invented(self):
        mid = "E2E-UNKNOWN-001"
        self.pipeline.process_email(
            self.raw(
                STRANGER,
                "I need help with my insurance",
                "I have no idea who I am in your system but I need assistance.",
                mid,
            )
        )
        row = self.triage_row(mid)
        self.assertIsNotNone(row, mid + ": no triage record stored")
        self.assertIsNone(row["customer_id"], "an unknown sender was matched to a customer")
        self.assertIsNone(row["policy_number"])
        self.assertIsNone(row["claim_number"])

    def test_unknown_sender_still_gets_a_safe_generic_reply(self):
        mid = "E2E-UNKNOWN-002"
        self.pipeline.process_email(
            self.raw(
                STRANGER,
                "General question about insurance",
                "I would like some general information about your insurance "
                "products and how to make a claim.",
                mid,
            )
        )
        record = self.triage_row(mid)
        row = record
        reply = row["suggested_reply"] or ""
        # The reply must not assert customer-specific facts we cannot know.
        for leaked in ("POL-", "CLM-", "CUST-"):
            self.assertNotIn(
                leaked, reply, f"unidentified reply leaked {leaked} data"
            )


class TestMissingRecordsHandledSafely(EndToEndTestBase):
    """A referenced-but-absent record must not crash the pipeline."""

    def test_claim_number_not_in_database_is_not_invented(self):
        mid = "E2E-MISSING-001"
        outcome = self.pipeline.process_email(
            self.raw(
                SARAH,
                "Status of claim CLM-9999-00000",
                "Please update me on claim CLM-9999-00000, which does not exist "
                "in your system.",
                mid,
            )
        )
        self.assertNotEqual(outcome.status, OutcomeStatus.FAILED_DATABASE)
        row = self.triage_row(mid)
        self.assertIsNotNone(row, mid + ": no triage record stored")
        # The fabricated claim number must not be echoed back as fact.
        reply = row["suggested_reply"] or ""
        self.assertNotIn("CLM-9999-00000", reply)

    def test_email_without_any_identifiers_is_handled(self):
        """Missing sender/subject/date must not crash triage."""
        mid = "E2E-MISSING-002"
        outcome = self.pipeline.process_email(
            {
                "id": mid,
                "message_id": f"<{mid}@apexshield.com>",
                "from": [{"name": "No Email", "addr": ""}],
                "subject": "",
                "body": [{"content": "I am a message with almost no metadata."}],
            }
        )
        self.assertIn(
            outcome.status,
            (OutcomeStatus.COMPLETED, OutcomeStatus.FAILED_MALFORMED),
        )

    def test_one_failed_email_does_not_block_the_rest_of_the_batch(self):
        """PDF Step 22: keep processing after a failure."""
        self.pipeline.watermark_mgr.save("0")
        self.pipeline.himalaya = MagicMock()
        self.pipeline.himalaya.list_inbox.return_value = [
            {"id": "1", "flags": []},
            {"id": "2", "flags": []},
            {"id": "3", "flags": []},
            {"id": "4", "flags": []},
        ]
        payloads = {
            "1": self.raw(SARAH, "First question", "Please help with my claim.", "1"),
            "2": {"totally": "unparseable"},
            "3": self.raw(MARCUS, "Second question", "Please help with my policy.", "3"),
            "4": self.raw(ELENA, "Third question", "Please help with my renewal.", "4"),
        }
        self.pipeline.himalaya.read_message.side_effect = lambda i: payloads[str(i)]
        with patch(
            "src.db.repository.InsuranceRepository.save_triage_record",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            processed = self.pipeline.poll_cycle()

        # Even with the database failing, the cycle completed and did not hang.
        self.assertIn(processed, (0, 3))
        self.assertGreater(self.pipeline.stats.emails_failed, 0)


class TestDashboardStaysCurrent(EndToEndTestBase):
    """PDF Step 20: the dashboard must reflect pipeline output immediately."""

    def test_dashboard_reflects_every_processed_email(self):
        ids = []
        for n, sender in enumerate([SARAH, MARCUS, DAVID], start=1):
            mid = f"E2E-DASH-{n:03d}"
            ids.append(mid)
            self.pipeline.process_email(
                self.raw(
                    sender,
                    f"Question number {n} about my insurance",
                    "I need help with something on my account please.",
                    mid,
                )
            )

        c = self.conn()
        try:
            payload = DashboardDataService(c).get_dashboard_payload()
        finally:
            c.close()

        self.assertEqual(payload["kpis"]["total_inquiries"], len(ids))
        self.assertEqual(len(payload["queue"]), len(ids))
        for r in payload["queue"]:
            self.assertIn(
                r["email_id"],
                ids,
                "dashboard queue is missing a processed email",
            )
            # The queue must expose the fields the dashboard UI renders.
            for field in ("priority", "urgency_score", "intent", "sender_email",
                          "subject", "escalation_status", "routing_desk",
                          "reply_status"):
                self.assertIn(field, r, f"dashboard row missing {field}")

    def test_dashboard_is_updated_without_restart(self):
        mid = "E2E-DASH-LIVE"
        self.pipeline.process_email(
            self.raw(SARAH, "A live question", "Please help me.", mid)
        )
        # A brand-new service instance over the same DB stands in for a
        # page refresh: the data is read live, never cached.
        c = self.conn()
        try:
            svc = DashboardDataService(c)
            first = svc.get_dashboard_payload()
        finally:
            c.close()
        self.assertEqual(first["kpis"]["total_inquiries"], 1)


class TestCSVReporting(EndToEndTestBase):
    """PDF Step 17: exact column contract, no raw-field leakage."""

    def test_csv_header_is_exactly_the_required_fifteen_columns(self):
        self.pipeline.process_email(
            self.raw(SARAH, "Status of my claim", "Please update me.", "E2E-CSV-001")
        )
        rows = self.csv_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(list(rows[0].keys()), EXPECTED_CSV_COLUMNS)

    def test_csv_does_not_leak_raw_record_fields(self):
        self.pipeline.process_email(
            self.raw(SARAH, "Status of my claim", "Please update me.", "E2E-CSV-002")
        )
        row = self.csv_rows()[0]
        for forbidden in ("raw_body", "body", "headers", "message_id", "suggested_reply"):
            self.assertNotIn(forbidden, row.keys())
        # The suggested reply is intentionally NOT part of the 15-column contract.
        self.assertNotIn("suggested_reply", row)

    def test_multiple_emails_append_to_the_csv(self):
        for n, sender in enumerate([SARAH, MARCUS, PRIYA], start=1):
            self.pipeline.process_email(
                self.raw(
                    sender,
                    f"Question {n}",
                    "I need help with my insurance please.",
                    f"E2E-CSV-APP-{n}",
                )
            )
        rows = self.csv_rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({r["email_id"] for r in rows}), 3)


class TestReplyDispatch(EndToEndTestBase):
    """PDF Step 15: plain-text, threaded, `Re:` subject, rate-limited."""

    def test_reply_is_plain_text_and_themed(self):
        mid = "E2E-REPLY-001"
        self.pipeline.process_email(
            self.raw(SARAH, "Status of my claim", "Please update me on my claim.", mid)
        )
        record = self.triage_row(mid)
        row = record
        reply = row["suggested_reply"] or ""
        self.assertNotIn("<html", reply.lower())
        self.assertNotIn("<br>", reply.lower())
        self.assertNotIn("<p>", reply.lower())
        self.assertTrue(reply.strip(), "an empty reply must not be sent")

    def test_reply_reports_a_valid_dispatch_status(self):
        mid = "E2E-REPLY-002"
        self.pipeline.process_email(
            self.raw(SARAH, "Status of my claim", "Please update me on my claim.", mid)
        )
        record = self.triage_row(mid)
        row = record
        self.assertIn(
            row["reply_status"],
            ("DRY_RUN", "Sent", "Rate-Limited", "Held", "Suppressed",
             "DUPLICATE_SUPPRESSED", "Failed", "Disabled"),
        )

    def test_repeat_contact_is_not_mailed_forever(self):
        """Loop prevention: the same thread stops being auto-answered."""
        subject = "Status of my claim CLM-2024-09112"
        body = "I still have not heard back about my claim, please advise."
        for n in range(6):
            self.pipeline.process_email(self.raw(SARAH, subject, body, f"E2E-LOOP-{n}"))
        replies = [
            r for r in self.csv_rows() if r["reply_status"] == "DRY_RUN"
        ]
        self.assertLessEqual(
            len(replies), 3, "loop prevention should cap auto-replies per thread"
        )


class TestThreadAndTicketContinuity(EndToEndTestBase):
    """PDF Step 13: a follow-up joins the existing ticket instead of duplicating."""

    def test_follow_up_email_reuses_the_existing_ticket(self):
        first_id = "E2E-THREAD-001"
        second_id = "E2E-THREAD-002"
        subject = "Status of my claim CLM-2024-09112"
        self.pipeline.process_email(
            self.raw(SARAH, subject, "Please update me on my claim.", first_id)
        )
        self.pipeline.process_email(
            self.raw(
                SARAH,
                f"Re: {subject}",
                "Following up again, I have still had no response.",
                second_id,
            )
        )

        # The follow-up must attach to the existing claim ticket. The claim
        # already had a seeded ticket, so no new ticket may be opened.
        c = self.conn()
        try:
            fresh = c.execute(
                "SELECT id FROM support_tickets WHERE email_id IN (?, ?)",
                (first_id, second_id),
            ).fetchall()
        finally:
            c.close()
        self.assertEqual(
            fresh, [], "the follow-up opened a duplicate ticket"
        )
        self.assertTrue(
            self.pipeline.last_ticket_action.startswith("APPEND"),
            f"expected a thread append, got {self.pipeline.last_ticket_action!r}",
        )


class TestMetricsAndKpis(EndToEndTestBase):
    """Dashboard KPIs must be computed from the database, not hard-coded."""

    def test_kpis_reflect_the_actual_records(self):
        for n, sender in enumerate([SARAH, MARCUS, DAVID, PRIYA], start=1):
            self.pipeline.process_email(
                self.raw(
                    sender,
                    f"Question {n}",
                    "I need help with my account.",
                    f"E2E-KPI-{n}",
                )
            )
        c = self.conn()
        try:
            kpis = DashboardDataService(c).get_kpi_metrics()
        finally:
            c.close()
        self.assertEqual(kpis["total_inquiries"], 4)
        for key in (
            "total_inquiries",
            "critical_emergencies",
            "human_escalations",
            "auto_replied",
        ):
            self.assertIn(key, kpis, f"KPI {key} missing")

    def test_pipeline_statistics_are_consistent_with_storage(self):
        self.pipeline.process_email(
            self.raw(SARAH, "Question one", "Please help.", "E2E-STAT-001")
        )
        self.pipeline.process_email(
            self.raw(MARCUS, "Question two", "Please help.", "E2E-STAT-002")
        )
        # A duplicate must not inflate the processed count.
        self.pipeline.process_email(
            self.raw(MARCUS, "Question two", "Please help.", "E2E-STAT-002")
        )
        self.assertEqual(self.pipeline.stats.emails_processed, 2)
        self.assertEqual(self.pipeline.stats.emails_skipped_duplicate, 1)
        c = self.conn()
        try:
            n = c.execute("SELECT COUNT(*) FROM triage_records").fetchone()[0]
        finally:
            c.close()
        self.assertEqual(n, 2)


class TestCommandLineInterface(EndToEndTestBase):
    """The shipped CLI commands must work against the real config objects.

    Both bugs here shipped undetected for many steps: a nonexistent config
    attribute and a dead duplicate branch that shadowed the only import.
    """

    def _run_cli(self, argv):
        import sys

        import main as main_module

        with patch.object(sys, "argv", ["main.py"] + argv):
            buf = io.StringIO()
            with redirect_stdout(buf):
                main_module.main()
        return buf.getvalue()

    def test_export_csv_command_writes_the_15_column_contract(self):
        self.pipeline.process_email(
            self.raw(
                SARAH,
                "Status of my claim CLM-2024-09112",
                "Please update me.",
                "E2E-CLI-001",
            )
        )
        out = Path(self.tmp.name) / "cli_export.csv"
        # main() builds its own Database from the config, so point the config at
        # this test's temporary database before invoking the command.
        with patch("main.load_config", lambda *a, **k: self.config):
            self._run_cli(["export-csv", "--out", str(out)])
        self.assertTrue(out.exists(), "export-csv wrote no file")

        rows = list(csv.DictReader(out.open(newline="", encoding="utf-8")))
        self.assertEqual(len(rows), 1)
        self.assertEqual(list(rows[0].keys()), EXPECTED_CSV_COLUMNS)

    def test_dashboard_command_reaches_a_live_server(self):
        """Guards the dead-branch bug: `dashboard` must actually start serving."""
        import src.dashboard.server as server_module

        started = {}

        def fake_start(db, host=None, port=None, **kwargs):
            started["host"] = host
            started["port"] = port
            raise KeyboardInterrupt

        with patch("main.load_config", lambda *a, **k: self.config):
            with patch.object(
                server_module, "start_dashboard_server", fake_start
            ):
                with self.assertRaises(KeyboardInterrupt):
                    self._run_cli(["dashboard", "--port", "8901"])
        self.assertEqual(started.get("port"), 8901)


if __name__ == "__main__":
    unittest.main()
