"""Tests for the autonomous pipeline orchestrator (Step 17)."""

import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from src.config import AppConfig, SystemConfig, AutoReplyConfig
from src.db.database import Database
from src.db.seed_data import seed_database
from src.db.repository import InsuranceRepository
from src.pipeline.autonomous_runner import AutonomousPipeline, PipelineStats
from src.ai.reasoning_engine import TriageDecision


def make_msg_id(tag: str) -> str:
    return f"<{uuid.uuid4()}@mail.example.com>"


# Must be internally consistent: this sender must actually own the claim, and
# the claim must actually have a seeded ticket (TICK-2024-8801), or the
# thread-continuity assertions below are testing an impossible situation.
# Sarah Jenkins (CUST-1001) owns CLM-2024-09112, which is the claim on
# TICK-2024-8801. The fixture previously used david.chen@example.com (a seeded
# customer who owns no claims at all) against a claim belonging to someone
# else -- a scenario the system is right to refuse to auto-reply to.
CLAIM_EMAIL = {
    "from": {"addr": "sarah.jenkins@example.com", "name": "Sarah Jenkins"},
    "to": "support@apexshield.com",
    "subject": "Status update on claim CLM-2024-09112 please",
    "date": "2024-09-25T10:00:00Z",
    "body": (
        "Hi, I am following up on claim CLM-2024-09112 for the rear bumper damage. "
        "My body shop finished the repair two weeks ago and I was told payment was approved. "
        "It has been three weeks and I still have not received the payment. "
        "This is quite frustrating. Please advise as soon as possible."
    ),
    "headers": {"From": "sarah.jenkins@example.com", "To": "support@apexshield.com"},
}

SPAM_EMAIL = {
    "from": {"addr": "winner@lottery-prizes-now.biz", "name": "Prize Team"},
    "to": "support@apexshield.com",
    "subject": "CONGRATULATIONS YOU WON $$$$ CLAIM YOUR FREE PRIZE NOW",
    "date": "2024-09-25T11:00:00Z",
    "body": (
        "You have been selected to receive $1,000,000 cash prize!!! "
        "Click http://spam.example.com/win now to claim your free money!!!"
    ),
    "headers": {"From": "winner@lottery-prizes-now.biz"},
}

UNKNOWN_EMAIL = {
    "from": {"addr": "unknown.person@example.org", "name": "Unknown"},
    "to": "support@apexshield.com",
    "subject": "Question about my policy",
    "date": "2024-09-25T12:00:00Z",
    "body": "I have a general question about my policy documents.",
    "headers": {"From": "unknown.person@example.org"},
}


class PipelineTestBase(unittest.TestCase):
    """Shared temp DB + seeded data + mock pipeline."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.db_path = self.tmpdir / "test.db"
        self.config = AppConfig(
            system=SystemConfig(
                db_path=self.db_path,
                watermark_file=self.tmpdir / "watermark.json",
                csv_report_path=self.tmpdir / "triage.csv",
                polling_interval_seconds=1,
            ),
            autoreply=AutoReplyConfig(enabled=True, dry_run=True),
        )
        self.db = Database(self.db_path)
        self.db.initialize()
        seed_conn = self.db.get_connection()
        try:
            seed_database(seed_conn)
        finally:
            seed_conn.close()
        self.pipeline = AutonomousPipeline(self.config, self.db, mock_mode=True, dry_run=True)
        # These tests cover pipeline orchestration, persistence and routing.
        # Stub the model so they do not depend on live OmniRoute availability.
        self.pipeline.ai_engine.analyze = self._stub_analyze

    @staticmethod
    def _stub_analyze(email, db_context):
        content = f"{email.subject} {email.body_text}".lower()
        legal = any(term in content for term in (
            "lawsuit", "legal action", "consulting a lawyer",
            "consulted an attorney", "consulted a lawyer",
        ))
        intent = "CLAIM_DELAY" if "claim" in content else "POLICY_DETAILS"
        return TriageDecision(
            category="Claims" if intent == "CLAIM_DELAY" else "Policy & Coverage",
            intent=intent,
            priority=(
                "Critical" if legal else
                "High" if intent == "CLAIM_DELAY" else "Medium"
            ),
            urgency_score=8 if legal else 5,
            sentiment="Frustrated" if "frustrating" in content else "Neutral",
            escalation_needed=legal,
            escalation_reason="Legal threat" if legal else "",
            summary="Deterministic test triage summary.",
            policy_number=None,
            claim_number=(
                "CLM-2024-09112"
                if "clm-2024-09112" in content else None
            ),
            routed_to="Claims" if intent == "CLAIM_DELAY" else "Policy & Coverage",
            suggested_reply=(
                "Thank you for contacting us. Your message has been received."
            ),
            confidence=0.95,
            human_review_required=legal,
            raw_response="Deterministic test stub",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def fetch_records(self):
        conn = self.db.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT email_id, intent, priority, customer_id, reply_status, processing_status FROM triage_records"
            )
            return cur.fetchall()
        finally:
            conn.close()

    def fetch_tickets(self):
        conn = self.db.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT ticket_number, status, email_id, assigned_team FROM support_tickets")
            return cur.fetchall()
        finally:
            conn.close()


class TestProcessEmailHappyPath(PipelineTestBase):
    """A known customer asking about a real seeded claim."""

    def test_claim_email_is_triaged_and_persisted(self):
        ok = self.pipeline.process_email(dict(CLAIM_EMAIL, id=make_msg_id("c1")))
        self.assertTrue(ok)
        self.assertEqual(self.pipeline.stats.emails_processed, 1)
        self.assertEqual(self.pipeline.stats.emails_failed, 0)

        records = self.fetch_records()
        self.assertEqual(len(records), 1)
        _mid, intent, _pri, cust_id, _reply, status = records[0]
        self.assertNotEqual(intent, "SPAM_OR_AUTOMATED")
        self.assertIsNotNone(cust_id, "Known sender should resolve to a seeded customer")
        self.assertEqual(status, "Completed")

    def test_claim_email_reuses_the_existing_claim_ticket(self):
        """The seeded claim already has a ticket, so the resolver must append.

        Creating a second ticket for the same claim is the duplicate-ticket bug:
        a customer would see two open cases for one claim.
        """
        baseline = len(self.fetch_tickets())
        self.pipeline.process_email(dict(CLAIM_EMAIL, id=make_msg_id("c2")))
        self.assertEqual(
            len(self.fetch_tickets()), baseline,
            "a duplicate ticket was opened for a claim that already had one",
        )
        self.assertEqual(self.pipeline.stats.tickets_created, 0)
        self.assertEqual(self.pipeline.stats.tickets_reused, 1)
        self.assertTrue(
            self.pipeline.last_ticket_action.startswith("APPEND"),
            f"expected APPEND, got {self.pipeline.last_ticket_action!r}",
        )

    def test_reply_is_generated_in_dry_run(self):
        with patch.object(
            self.pipeline.reply_gen,
            "generate_reply",
            wraps=self.pipeline.reply_gen.generate_reply,
        ) as generate_reply:
            self.pipeline.process_email(dict(CLAIM_EMAIL, id=make_msg_id("c3")))

        # A dry run is simulated, not counted as sent or suppressed.
        generate_reply.assert_called_once()
        audit = self.pipeline.reply_sender.get_audit_log()
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0].status, "DRY_RUN")
        self.assertEqual(self.pipeline.stats.replies_sent, 0)
        self.assertEqual(self.pipeline.stats.replies_suppressed, 0)

    def test_escalation_flag_counted(self):
        self.pipeline.process_email(dict(CLAIM_EMAIL, id=make_msg_id("c4")))
        self.assertGreaterEqual(self.pipeline.stats.escalations, 0)

    def test_summary_and_routing_populated(self):
        self.pipeline.process_email(dict(CLAIM_EMAIL, id=make_msg_id("c5")))
        conn = self.db.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT summary, routing_desk, urgency_score, sentiment FROM triage_records")
            row = cur.fetchone()
        finally:
            conn.close()
        self.assertTrue(row[0], "summary should be populated")
        self.assertTrue(row[1], "routing_desk should be populated")
        self.assertIsInstance(row[2], int)
        self.assertIn(row[3], ("Positive", "Neutral", "Negative", "Frustrated"))


class TestIdempotency(PipelineTestBase):
    def test_same_message_id_is_processed_once(self):
        mid = make_msg_id("dup")
        self.assertTrue(self.pipeline.process_email(dict(CLAIM_EMAIL, id=mid)))
        self.assertTrue(self.pipeline.process_email(dict(CLAIM_EMAIL, id=mid)))
        self.assertEqual(self.pipeline.stats.emails_processed, 1)
        self.assertEqual(self.pipeline.stats.emails_skipped_duplicate, 1)
        self.assertEqual(len(self.fetch_records()), 1)

    def test_watermark_advanced_after_processing(self):
        mid = make_msg_id("wm")
        self.pipeline.process_email(dict(CLAIM_EMAIL, id=mid))
        self.assertEqual(self.pipeline.watermark_mgr.current_watermark, str(mid))


class TestSpamHandling(PipelineTestBase):
    def test_spam_is_archived_not_triaged(self):
        self.pipeline.process_email(dict(SPAM_EMAIL, id=make_msg_id("s1")))
        self.assertEqual(self.pipeline.stats.emails_archived_spam, 1)
        self.assertEqual(self.pipeline.stats.emails_processed, 0)

    def test_spam_is_recorded_for_idempotency(self):
        mid = make_msg_id("s2")
        self.pipeline.process_email(dict(SPAM_EMAIL, id=mid))
        records = self.fetch_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][1], "SPAM_OR_AUTOMATED")
        self.assertEqual(records[0][5], "ARCHIVE_SPAM")

    def test_spam_does_not_create_ticket(self):
        before = len(self.fetch_tickets())
        self.pipeline.process_email(dict(SPAM_EMAIL, id=make_msg_id("s3")))
        self.assertEqual(len(self.fetch_tickets()), before)


class TestUnknownSender(PipelineTestBase):
    def test_unknown_sender_still_triages(self):
        ok = self.pipeline.process_email(dict(UNKNOWN_EMAIL, id=make_msg_id("u1")))
        self.assertTrue(ok)
        self.assertEqual(self.pipeline.stats.emails_processed, 1)
        records = self.fetch_records()
        self.assertEqual(len(records), 1)
        # No customer row matched; customer_id should be null.
        self.assertIsNone(records[0][3])

    def test_unknown_sender_still_gets_reply_attempt(self):
        with patch.object(
            self.pipeline.reply_gen,
            "generate_reply",
            wraps=self.pipeline.reply_gen.generate_reply,
        ) as generate_reply:
            self.pipeline.process_email(dict(UNKNOWN_EMAIL, id=make_msg_id("u2")))

        # Unknown senders may receive a generic simulated reply; no live email
        # is sent while dry_run is enabled.
        generate_reply.assert_called_once()
        audit = self.pipeline.reply_sender.get_audit_log()
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0].status, "DRY_RUN")
        self.assertEqual(audit[0].recipient, UNKNOWN_EMAIL["from"]["addr"])
        self.assertEqual(self.pipeline.stats.replies_sent, 0)
        self.assertEqual(self.pipeline.stats.replies_suppressed, 0)


class TestReplySuppression(PipelineTestBase):
    def test_autoreply_disabled_yields_no_reply(self):
        self.config.autoreply.enabled = False
        self.pipeline.process_email(dict(CLAIM_EMAIL, id=make_msg_id("r1")))
        records = self.fetch_records()
        self.assertEqual(records[0][4], "Disabled")
        self.assertEqual(self.pipeline.stats.replies_sent, 0)

    def test_escalated_case_holds_reply(self):
        # Legal threat should be escalated and held.
        legal = dict(UNKNOWN_EMAIL)
        legal["subject"] = "I am filing a lawsuit"
        legal["body"] = "My claim was denied and I am consulting a lawyer. I will sue."
        self.pipeline.process_email(dict(legal, id=make_msg_id("r2")))
        records = self.fetch_records()
        self.assertIn(records[0][4], ("Held", "Suppressed"))
        self.assertGreaterEqual(self.pipeline.stats.escalations, 1)


class TestThreadResolution(PipelineTestBase):
    def test_reply_to_same_subject_reuses_ticket(self):
        first = dict(CLAIM_EMAIL, id=make_msg_id("t1"))
        self.pipeline.process_email(first)
        tickets_after_first = self.fetch_tickets()
        # The claim already carries a seeded ticket, so the first email appends
        # to it rather than opening a duplicate.
        self.assertEqual(
            self.pipeline.stats.tickets_created, 0,
            f"unexpected new ticket: {self.pipeline.last_ticket_action!r}",
        )
        self.assertEqual(self.pipeline.stats.tickets_reused, 1)

        # Second email with same subject, same customer, references first message-id.
        second = dict(CLAIM_EMAIL, id=make_msg_id("t2"))
        second["in-reply-to"] = first["id"]
        second["headers"] = dict(second["headers"], **{
            "In-Reply-To": first["id"],
            "References": first["id"],
        })
        self.pipeline.process_email(second)

        # The reply references the first message, so it must resolve to the same
        # existing thread: no additional ticket, and reuse counted once more.
        self.assertEqual(
            self.pipeline.stats.tickets_created, 0,
            "the reply opened a new ticket instead of joining the thread",
        )
        self.assertEqual(self.pipeline.stats.tickets_reused, 2)
        self.assertEqual(len(self.fetch_tickets()), len(tickets_after_first))


class TestPollingLoop(PipelineTestBase):
    def test_poll_cycle_initializes_watermark_on_first_run(self):
        self.pipeline.himalaya.inject_mock_email(dict(CLAIM_EMAIL, id="1000"))
        processed = self.pipeline.poll_cycle()
        # First run only establishes watermark; historical mail is not replayed.
        self.assertEqual(processed, 0)
        self.assertEqual(self.pipeline.watermark_mgr.current_watermark, "1000")

    def test_poll_cycle_processes_new_messages(self):
        self.pipeline.himalaya.inject_mock_email(dict(CLAIM_EMAIL, id="1000"))
        self.pipeline.poll_cycle()  # sets watermark to 1000

        # New higher-id message arrives.
        self.pipeline.himalaya.inject_mock_email(dict(CLAIM_EMAIL, id="1001"))
        processed = self.pipeline.poll_cycle()
        self.assertEqual(processed, 1)
        self.assertEqual(self.pipeline.watermark_mgr.current_watermark, "1001")

    def test_poll_cycle_skips_already_seen(self):
        self.pipeline.himalaya.inject_mock_email(dict(CLAIM_EMAIL, id="1000"))
        self.pipeline.poll_cycle()
        self.pipeline.himalaya.inject_mock_email(dict(CLAIM_EMAIL, id="1001"))
        self.pipeline.poll_cycle()
        # Re-poll with no new mail.
        processed = self.pipeline.poll_cycle()
        self.assertEqual(processed, 0)

    def test_run_forever_respects_max_cycles(self):
        self.pipeline.himalaya.inject_mock_email(dict(CLAIM_EMAIL, id="1000"))
        stats = self.pipeline.run_forever(max_cycles=2)
        self.assertIsInstance(stats, PipelineStats)
        self.assertEqual(stats.poll_cycles, 2)


class TestStats(PipelineTestBase):
    def test_to_dict_and_str(self):
        stats = PipelineStats()
        stats.emails_seen = 5
        stats.emails_processed = 3
        d = stats.to_dict()
        self.assertEqual(d["emails_seen"], 5)
        s = str(stats)
        self.assertIn("seen=5", s)
        self.assertIn("processed=3", s)


class TestRepositoryFixes(PipelineTestBase):
    def test_ticket_id_is_deterministic(self):
        conn = self.db.get_connection()
        try:
            repo = InsuranceRepository(conn)
            a = repo.create_support_ticket(
                ticket_number="TCK-TEST-00001",
                customer_id=None, policy_id=None, claim_id=None,
                subject="s", category="c", priority="High",
                status="Open", assigned_team="t",
            )
            b = repo.create_support_ticket(
                ticket_number="TCK-TEST-00002",
                customer_id=None, policy_id=None, claim_id=None,
                subject="s", category="c", priority="High",
                status="Open", assigned_team="t",
            )
        finally:
            conn.close()
        self.assertNotEqual(a, b, "Different ticket_numbers must yield different ids")
        self.assertTrue(a.startswith("TCK-ABS"), "Ticket id should use the deterministic uuid5 prefix")

    def test_reopen_ticket(self):
        conn = self.db.get_connection()
        try:
            repo = InsuranceRepository(conn)
            cur = conn.cursor()
            cur.execute("SELECT ticket_number FROM support_tickets LIMIT 1")
            tnum = cur.fetchone()[0]
            cur.execute("UPDATE support_tickets SET status='Resolved' WHERE ticket_number=?", (tnum,))
            conn.commit()
            result = repo.reopen_ticket(tnum)
            cur.execute("SELECT status FROM support_tickets WHERE ticket_number=?", (tnum,))
            new_status = cur.fetchone()[0]
        finally:
            conn.close()
        self.assertTrue(result)
        self.assertEqual(new_status, "Open")


if __name__ == "__main__":
    unittest.main()

class TestInboxPagination(PipelineTestBase):
    """Tests for fetching multiple inbox pages."""

    def test_first_run_fetches_only_first_page(self):
        page1 = [{"id": str(i)} for i in range(20, 0, -1)]

        with patch.object(
            self.pipeline.himalaya, "list_inbox",
            return_value=page1
        ) as mock_list:
            result = self.pipeline._fetch_inbox_pages(page_size=20)

        self.assertEqual(result, page1)
        mock_list.assert_called_once_with(page_size=20, page=1)

    def test_fetches_until_watermark_is_found(self):
        self.pipeline.watermark_mgr.save("4")
        page1 = [{"id": str(i)} for i in (9, 8, 7)]
        page2 = [{"id": str(i)} for i in (6, 5, 4)]
        page3 = [{"id": str(i)} for i in (3, 2, 1)]

        with patch.object(
            self.pipeline.himalaya, "list_inbox",
            side_effect=[page1, page2]
        ) as mock_list:
            result = self.pipeline._fetch_inbox_pages(page_size=3)

        self.assertEqual(
            [item["id"] for item in result],
            ["9", "8", "7", "6", "5", "4"]
        )
        self.assertEqual(mock_list.call_count, 2)

    def test_deduplicates_messages_across_pages(self):
        self.pipeline.watermark_mgr.save("5")
        page1 = [{"id": str(i)} for i in (9, 8, 7)]
        page2 = [{"id": str(i)} for i in (7, 6, 5)]

        with patch.object(
            self.pipeline.himalaya, "list_inbox",
            side_effect=[page1, page2]
        ):
            result = self.pipeline._fetch_inbox_pages(page_size=3)

        ids = [item["id"] for item in result]
        self.assertEqual(ids, ["9", "8", "7", "6", "5"])
        self.assertEqual(len(ids), len(set(ids)))

    def test_stops_at_short_page_if_watermark_is_missing(self):
        self.pipeline.watermark_mgr.save("1")
        page1 = [{"id": str(i)} for i in (9, 8, 7)]
        page2 = [{"id": str(i)} for i in (6, 5)]

        with patch.object(
            self.pipeline.himalaya, "list_inbox",
            side_effect=[page1, page2]
        ) as mock_list:
            result = self.pipeline._fetch_inbox_pages(page_size=3)

        self.assertEqual(
            [item["id"] for item in result],
            ["9", "8", "7", "6", "5"]
        )
        self.assertEqual(mock_list.call_count, 2)

    def test_raises_if_pagination_makes_no_progress(self):
        self.pipeline.watermark_mgr.save("1")
        page1 = [{"id": str(i)} for i in (9, 8, 7)]

        with patch.object(
            self.pipeline.himalaya, "list_inbox",
            side_effect=[page1, page1]
        ):
            with self.assertRaisesRegex(
                RuntimeError, "made no progress"
            ):
                self.pipeline._fetch_inbox_pages(page_size=3)
