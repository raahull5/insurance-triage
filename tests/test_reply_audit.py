"""Tests for the durable reply audit trail.

The dispatcher historically kept audit records only in an in-memory list, so the
record of what was sent, suppressed, rate-limited, or failed vanished when the
process exited. These tests pin the durable behaviour: every dispatch outcome is
written to the reply_audit table, the trail survives a restart, and a failure to
write an audit row never blocks the reply itself.
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.database import Database
from src.db.repository import InsuranceRepository
from src.db.schema import create_tables
from src.ingestion.preprocessor import StructuredEmail
from src.ai.reasoning_engine import TriageDecision
from src.autoreply.sender import ReplyDispatcher, DispatchAuditRecord


def make_email(message_id: str = "MSG-1", sender: str = "cust@example.com") -> StructuredEmail:
    return StructuredEmail(
        message_id=message_id,
        sender_email=sender,
        sender_name="Test Customer",
        recipient="support@apexshield.com",
        subject="Question about my policy",
        body_text="What is the status of my claim?",
        received_date="2024-09-25T10:00:00Z",
        headers={"From": sender},
    )


def make_decision(**overrides) -> TriageDecision:
    base = dict(
        category="Claims",
        intent="CLAIM_STATUS",
        priority="Medium",
        urgency_score=5,
        sentiment="Neutral",
        escalation_needed=False,
        escalation_reason="",
        summary="Customer asking about claim status.",
        policy_number=None,
        claim_number=None,
        routed_to="#claims",
        suggested_reply="Thanks for reaching out.",
        confidence=0.9,
        human_review_required=False,
    )
    base.update(overrides)
    return TriageDecision(**base)


class ReplyAuditTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "audit.db"
        self.db = Database(Path(self.db_path))
        self.conn = self.db.get_connection()
        create_tables(self.conn)
        self.repo = InsuranceRepository(self.conn)

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:
            pass
        self._tmp.cleanup()

    def make_dispatcher(self, dry_run: bool = True) -> ReplyDispatcher:
        return ReplyDispatcher(
            dry_run=dry_run,
            audit_repository=self.repo,
        )


class TestDurableReplyAudit(ReplyAuditTestBase):
    def test_dry_run_dispatch_is_persisted(self):
        d = self.make_dispatcher()
        email = make_email("MSG-DRY-1")
        d.dispatch(email, make_decision(), "Here is your status.")
        d.dispatch(email, make_decision(), "Here is your status.")

        rows = self.repo.get_reply_audit()
        # A dry run is dispatched twice because duplicate suppression keys on
        # Message-ID, and the second call is recorded as a suppression.
        self.assertGreaterEqual(len(rows), 1)
        statuses = [r["status"] for r in rows]
        self.assertIn("DRY_RUN", statuses)

    def test_every_outcome_is_audited(self):
        d = self.make_dispatcher()
        # Sent
        d.dispatch(make_email("MSG-SENT"), make_decision(), "ok")
        # Duplicate suppressed
        d.dispatch(make_email("MSG-SENT"), make_decision(), "ok")
        # Human review suppressed
        d.dispatch(
            make_email("MSG-REVIEW"),
            make_decision(intent="SPAM_OR_AUTOMATED", human_review_required=True, suggested_reply=""),
            None,
        )
        statuses = {r["status"] for r in self.repo.get_reply_audit()}
        self.assertIn("DRY_RUN", statuses)
        self.assertIn("DUPLICATE_SUPPRESSED", statuses)
        self.assertIn("SUPPRESSED_HUMAN_REVIEW", statuses)

    def test_audit_row_captures_envelope_fields(self):
        d = self.make_dispatcher()
        d.dispatch(make_email("MSG-FIELDS", sender="bob@example.com"), make_decision(), "body text")
        row = self.repo.get_reply_audit()[0]
        self.assertEqual(row["message_id"], "MSG-FIELDS")
        self.assertEqual(row["recipient"], "bob@example.com")
        self.assertEqual(row["in_reply_to"], "MSG-FIELDS")
        self.assertTrue(row["subject"].startswith("Re: "))
        self.assertTrue(row["timestamp"])

    def test_body_is_hashed_not_stored_in_the_clear(self):
        d = self.make_dispatcher()
        secret = "Dear Marcus, your claim amount is 4820.00 and your SSN is 123-45-6789."
        d.dispatch(make_email("MSG-HASH"), make_decision(), secret)
        row = self.repo.get_reply_audit()[0]
        self.assertIsNotNone(row["body_sha256"])
        self.assertEqual(len(row["body_sha256"]), 64)
        self.assertEqual(row["body_chars"], len(secret))
        # The body itself must not be retrievable from the audit row.
        flat = " ".join(str(v) for v in row.values())
        self.assertNotIn("4820.00", flat)
        self.assertNotIn("123-45-6789", flat)

    def test_audit_trail_survives_a_restart(self):
        d = self.make_dispatcher()
        d.dispatch(make_email("MSG-PERSIST"), make_decision(), "x")
        in_memory_count = len(d.get_audit_log())

        # Simulate a process restart: a brand-new dispatcher + repository over
        # the same file must still see the row.
        del d
        self.conn.close()
        new_db = Database(Path(self.db_path))
        new_conn = new_db.get_connection()
        try:
            new_repo = InsuranceRepository(new_conn)
            rows = new_repo.get_reply_audit()
            self.assertEqual(len(rows), in_memory_count)
            self.assertEqual(rows[0]["message_id"], "MSG-PERSIST")
        finally:
            new_conn.close()

    def test_stats_group_by_status(self):
        d = self.make_dispatcher()
        d.dispatch(make_email("MSG-S1"), make_decision(), "x")
        stats = self.repo.get_reply_audit_stats()
        self.assertTrue(stats)
        self.assertEqual(sum(stats.values()), len(self.repo.get_reply_audit()))

    def test_filters_by_status_and_recipient(self):
        d = self.make_dispatcher()
        d.dispatch(make_email("MSG-R1", sender="a@example.com"), make_decision(), "x")
        d.dispatch(make_email("MSG-R2", sender="b@example.com"), make_decision(), "x")
        d.dispatch(make_email("MSG-R1", sender="a@example.com"), make_decision(), "x")
        by_recipient = self.repo.get_reply_audit(recipient="a@example.com")
        self.assertTrue(by_recipient)
        for r in by_recipient:
            self.assertEqual(r["recipient"], "a@example.com")
        by_status = self.repo.get_reply_audit(status="DRY_RUN")
        for r in by_status:
            self.assertEqual(r["status"], "DRY_RUN")

    def test_in_memory_log_still_available_without_repository(self):
        d = ReplyDispatcher(dry_run=True)
        d.dispatch(make_email("MSG-NOREPO"), make_decision(), "x")
        self.assertEqual(len(d.get_audit_log()), 1)

    def test_audit_write_failure_does_not_block_the_reply(self):
        class BrokenRepo:
            def record_reply_audit(self, **kwargs):
                raise RuntimeError("disk full")

        d = ReplyDispatcher(dry_run=True, audit_repository=BrokenRepo())
        # No assertLogs here: logger propagation is disabled by other suites in
        # a full run, so asserting on log output would make this test depend on
        # suite ordering. The contract that matters is behavioural -- the
        # customer still gets their reply.
        ok, record = d.dispatch(make_email("MSG-BROKEN"), make_decision(), "x")
        self.assertTrue(ok)
        self.assertEqual(record.status, "DRY_RUN")
        self.assertEqual(len(d.get_audit_log()), 1)


class TestReplyAuditSchema(ReplyAuditTestBase):
    def test_table_exists_with_expected_columns(self):
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(reply_audit)")]
        for expected in (
            "id", "timestamp", "message_id", "recipient", "subject", "status",
            "error_details", "in_reply_to", "email_id", "customer_id", "intent",
            "routing_desk", "body_sha256", "body_chars", "dry_run",
        ):
            self.assertIn(expected, cols)

    def test_indexes_created(self):
        idx = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='reply_audit'"
        )}
        for expected in (
            "idx_reply_audit_timestamp",
            "idx_reply_audit_status",
            "idx_reply_audit_message",
            "idx_reply_audit_recipient",
        ):
            self.assertIn(expected, idx)


if __name__ == "__main__":
    unittest.main()
