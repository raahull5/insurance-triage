"""Tests for Step 22: error handling, resilience, and failure isolation."""

import logging
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config import AppConfig
from src.core.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    DEFAULT_DEGRADED_THRESHOLD,
    HealthMonitor,
    is_transient,
    OutcomeStatus,
    ProcessingOutcome,
    RetryPolicy,
)
from src.db.database import Database
from src.db.repository import InsuranceRepository
from src.db.seed_data import seed_database
from src.ingestion.himalaya_client import (
    HimalayaClient,
    HimalayaError,
    HimalayaPermanentError,
    HimalayaTransientError,
)
from src.ingestion.preprocessor import EmailPreprocessor
from src.pipeline.autonomous_runner import AutonomousPipeline


# --------------------------------------------------------------------- #
# Resilience primitives
# --------------------------------------------------------------------- #
class TestTransienceClassification(unittest.TestCase):
    def test_transient_classification(self):
        for exc in (
            HimalayaTransientError("timeout"),
            TimeoutError("read timed out"),
            ConnectionError("connection reset by peer"),
            sqlite3.OperationalError("database is locked"),
        ):
            self.assertTrue(is_transient(exc), f"{type(exc).__name__} should be transient")

    def test_permanent_classification(self):
        for exc in (
            HimalayaPermanentError("bad token"),
            ValueError("malformed email body"),
            TypeError("bad argument type"),
        ):
            self.assertFalse(is_transient(exc), f"{type(exc).__name__} should be permanent")

    def test_typed_errors_beat_text_sniffing(self):
        """A permanent error mentioning 'timeout' must stay permanent."""
        exc = HimalayaPermanentError("bad credentials; connection timeout noted")
        self.assertFalse(is_transient(exc))


    def test_himalaya_error_hierarchy(self):
        self.assertTrue(issubclass(HimalayaTransientError, HimalayaError))
        self.assertTrue(issubclass(HimalayaPermanentError, HimalayaError))
        self.assertTrue(issubclass(HimalayaError, Exception))


class TestCircuitBreaker(unittest.TestCase):
    def test_starts_closed(self):
        cb = CircuitBreaker("svc", failure_threshold=3)
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.allow_request())

    def test_opens_after_threshold_and_blocks(self):
        cb = CircuitBreaker("svc", failure_threshold=3, recovery_timeout=300)
        for _ in range(3):
            with self.assertRaises(HimalayaTransientError):
                cb.call(lambda: (_ for _ in ()).throw(HimalayaTransientError("down")))
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertFalse(cb.allow_request())
        with self.assertRaises(CircuitOpenError):
            cb.call(lambda: "should not run")

    def test_failure_counter_not_incremented_when_already_open(self):
        cb = CircuitBreaker("svc", failure_threshold=2, recovery_timeout=300)
        for _ in range(2):
            with self.assertRaises(HimalayaTransientError):
                cb.call(lambda: (_ for _ in ()).throw(HimalayaTransientError("x")))
        for _ in range(5):
            with self.assertRaises(CircuitOpenError):
                cb.call(lambda: "no")
        self.assertEqual(cb.failure_count, 2)

    def test_half_open_recovers_after_timeout(self):
        cb = CircuitBreaker("svc", failure_threshold=2, recovery_timeout=0.05)
        for _ in range(2):
            with self.assertRaises(HimalayaTransientError):
                cb.call(lambda: (_ for _ in ()).throw(HimalayaTransientError("x")))
        self.assertEqual(cb.state, CircuitState.OPEN)
        time.sleep(0.08)
        # First call after the timeout probes the dependency.
        self.assertEqual(cb.call(lambda: "recovered"), "recovered")
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.failure_count, 0)

    def test_success_returns_value_and_resets(self):
        cb = CircuitBreaker("svc", failure_threshold=3)
        with self.assertRaises(HimalayaTransientError):
            cb.call(lambda: (_ for _ in ()).throw(HimalayaTransientError("x")))
        self.assertEqual(cb.failure_count, 1)
        self.assertEqual(cb.call(lambda: 42), 42)
        self.assertEqual(cb.failure_count, 0)

    def test_permanent_error_trips_immediately(self):
        cb = CircuitBreaker("svc", failure_threshold=5)
        with self.assertRaises(HimalayaPermanentError):
            cb.call(lambda: (_ for _ in ()).throw(HimalayaPermanentError("bad token")))
        self.assertEqual(cb.state, CircuitState.OPEN)


class TestHealthMonitor(unittest.TestCase):
    def test_records_success_and_failure(self):
        h = HealthMonitor()
        h.record_success("gmail")
        h.record_failure("gmail", "timeout")
        h.record_failure("gmail", "timeout")
        dep = h.get("gmail")
        self.assertEqual(dep.name, "gmail")
        self.assertEqual(dep.successes, 1)
        self.assertEqual(dep.failures, 2)
        self.assertEqual(dep.total_calls, 3)
        # A success reset the streak, so two trailing failures is still healthy.
        self.assertTrue(dep.healthy)

    def test_success_resets_failure_streak(self):
        h = HealthMonitor()
        h.record_failure("gmail", "timeout")
        h.record_success("gmail")
        self.assertEqual(h.get("gmail").consecutive_failures, 0)
        self.assertTrue(h.get("gmail").healthy)

    def test_unhealthy_after_threshold(self):
        h = HealthMonitor()
        for _ in range(DEFAULT_DEGRADED_THRESHOLD):
            h.record_failure("db", "locked")
        self.assertFalse(h.get("db").healthy)
        h.record_success("db")
        self.assertTrue(h.get("db").healthy)

    def test_success_rate(self):
        h = HealthMonitor()
        for _ in range(3):
            h.record_success("db")
        h.record_failure("db", "err")
        self.assertAlmostEqual(h.get("db").success_rate, 0.75)

    def test_snapshot_serialises(self):
        h = HealthMonitor()
        h.record_failure("gmail", "boom")
        snap = h.snapshot()
        self.assertIn("gmail", snap)
        self.assertEqual(snap["gmail"]["failures"], 1)
        self.assertEqual(snap["gmail"]["last_error"], "boom")

    def test_empty_snapshot_is_safe(self):
        self.assertEqual(HealthMonitor().snapshot(), {})

    def test_never_recorded_dependency_is_healthy(self):
        self.assertTrue(HealthMonitor().get("unseen").healthy)



class TestProcessingOutcome(unittest.TestCase):
    def test_durable_statuses_advance_watermark(self):
        for status in (
            OutcomeStatus.COMPLETED,
            OutcomeStatus.ARCHIVED_SPAM,
            OutcomeStatus.HELD_FOR_REVIEW,
            OutcomeStatus.SKIPPED_DUPLICATE,
            OutcomeStatus.FAILED_MALFORMED,
        ):
            self.assertTrue(status.is_durable, status.value)

    def test_non_durable_statuses_do_not_advance_watermark(self):
        for status in (
            OutcomeStatus.FAILED_GMAIL,
            OutcomeStatus.FAILED_DATABASE,
            OutcomeStatus.FAILED_LLM,
            OutcomeStatus.FAILED_REPLY,
        ):
            self.assertFalse(status.is_durable, status.value)

    def test_retryable_flags(self):
        self.assertTrue(OutcomeStatus.FAILED_DATABASE.is_retryable)
        self.assertTrue(OutcomeStatus.FAILED_GMAIL.is_retryable)
        self.assertFalse(OutcomeStatus.FAILED_MALFORMED.is_retryable)
        self.assertFalse(OutcomeStatus.COMPLETED.is_retryable)

    def test_success_flag(self):
        self.assertTrue(OutcomeStatus.COMPLETED.is_success)
        self.assertFalse(OutcomeStatus.SKIPPED_DUPLICATE.is_success)

    def test_durable_outcome_sets_watermark_flag(self):
        o = ProcessingOutcome(status=OutcomeStatus.COMPLETED, email_id="x")
        self.assertTrue(o.should_advance_watermark)
        self.assertTrue(o.retryable is False)

    def test_non_durable_outcome_leaves_watermark(self):
        o = ProcessingOutcome(status=OutcomeStatus.FAILED_DATABASE, email_id="x")
        self.assertFalse(o.should_advance_watermark)
        self.assertTrue(o.retryable)

    def test_to_dict(self):
        o = ProcessingOutcome(status=OutcomeStatus.COMPLETED, email_id="x", stage="complete")
        d = o.to_dict()
        self.assertEqual(d["status"], "COMPLETED")
        self.assertEqual(d["email_id"], "x")
        self.assertTrue(d["is_durable"])



# --------------------------------------------------------------------- #
# Himalaya client error classification
# --------------------------------------------------------------------- #
class TestHimalayaErrorHandling(unittest.TestCase):
    def setUp(self):
        self.client = HimalayaClient(mock_mode=False)
        self.client.himalaya_path = "/usr/bin/himalaya"
        # Keep the real retry logic but make backoff instant. RETRY_POLICY is a
        # class attribute, so patch it at the class level for this test class.
        patcher = patch.object(
            HimalayaClient, "RETRY_POLICY",
            RetryPolicy(max_attempts=3, base_delay=0.0, jitter=False),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # Pretend the CLI is installed so the mock branch is not taken.
        self.client.is_cli_available = lambda: True

    @staticmethod
    def _ok(stdout="[]"):
        p = MagicMock()
        p.returncode = 0
        p.stdout = stdout
        p.stderr = ""
        return p

    @staticmethod
    def _fail(stderr):
        return subprocess.CalledProcessError(1, "himalaya", output="", stderr=stderr)

    def test_auth_error_is_permanent(self):
        with patch("subprocess.run", side_effect=self._fail("authentication failed: bad credentials")):
            with self.assertRaises(HimalayaPermanentError) as ctx:
                self.client.list_inbox()
        self.assertIn("authentication", str(ctx.exception).lower())

    def test_auth_error_is_not_retried(self):
        """Permanent failures must fail fast, not spin on backoff."""
        calls = {"n": 0}

        def counting(*a, **kw):
            calls["n"] += 1
            raise self._fail("authentication failed")

        with patch("subprocess.run", side_effect=counting):
            with self.assertRaises(HimalayaPermanentError):
                self.client.list_inbox()
        self.assertEqual(calls["n"], 1)

    def test_network_error_is_transient(self):
        with patch("subprocess.run", side_effect=self._fail("Network is unreachable: connection timed out")):
            with self.assertRaises(HimalayaTransientError):
                self.client.list_inbox()

    def test_rate_limited_is_transient(self):
        with patch("subprocess.run", side_effect=self._fail("429 Too Many Requests, try again later")):
            with self.assertRaises(HimalayaTransientError):
                self.client.list_inbox()

    def test_transient_error_is_retried(self):
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise self._fail("connection timed out")
            return self._ok("[]")

        with patch("subprocess.run", side_effect=flaky):
            result = self.client.list_inbox()
        self.assertEqual(result, [])
        self.assertGreaterEqual(calls["n"], 3)

    def test_timeout_is_transient(self):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="himalaya", timeout=30),
        ):
            with self.assertRaises(HimalayaTransientError):
                self.client.list_inbox()

    def test_missing_cli_is_permanent(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("no such file")):
            with self.assertRaises(HimalayaPermanentError):
                self.client.list_inbox()

    def test_list_inbox_never_silently_returns_empty_on_error(self):
        """The pipeline must be able to distinguish 'no mail' from 'auth failed'."""
        with patch("subprocess.run", side_effect=self._fail("authentication failed")):
            with self.assertRaises(HimalayaError):
                self.client.list_inbox()

    def test_read_message_raises_instead_of_returning_none(self):
        with patch("subprocess.run", side_effect=self._fail("Network is unreachable")):
            with self.assertRaises(HimalayaError):
                self.client.read_message("123")

    def test_malformed_json_is_transient(self):
        with patch("subprocess.run", return_value=self._ok("not json at all")):
            with self.assertRaises(HimalayaTransientError):
                self.client.list_inbox()



# --------------------------------------------------------------------- #
# Pipeline failure isolation
# --------------------------------------------------------------------- #
class TestPipelineResilience(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db = Database(str(self.db_path))
        self.db.initialize()
        conn = self.db.get_connection()
        try:
            seed_database(conn)
        finally:
            conn.close()

        self.wm_path = Path(self.tmp.name) / "watermark.json"
        # Redirect every writable path into the temp dir. Leaving csv_report_path
        # on the production default made this suite write triage_results.csv into
        # the real data/ directory on every run.
        self.csv_path = Path(self.tmp.name) / "triage_results.csv"
        cfg = AppConfig()
        cfg.system.db_path = Path(self.db_path)
        cfg.system.watermark_file = Path(self.wm_path)
        cfg.system.csv_report_path = self.csv_path
        cfg.autoreply.enabled = False
        self.config = cfg
        self.pipeline = AutonomousPipeline(cfg, self.db, mock_mode=True, dry_run=True)

    def tearDown(self):
        self.tmp.cleanup()

    # ---- watermark safety ----
    def test_database_failure_does_not_advance_watermark(self):
        with patch(
            "src.db.repository.InsuranceRepository.save_triage_record",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            outcome = self.pipeline.process_email(self._raw("wm-fail@test.com"))
        self.assertEqual(outcome.status, OutcomeStatus.FAILED_DATABASE)
        self.assertEqual(outcome.stage, "database_write")
        self.assertFalse(outcome.should_advance_watermark)
        self.assertTrue(outcome.status.is_retryable)

    def test_successful_processing_advances_watermark(self):
        outcome = self.pipeline.process_email(self._raw("wm-ok@test.com"))
        self.assertEqual(outcome.status, OutcomeStatus.COMPLETED)
        self.assertTrue(outcome.should_advance_watermark)

    def test_malformed_email_is_terminal_not_retried(self):
        outcome = self.pipeline.process_email({"total garbage": True})
        self.assertEqual(outcome.status, OutcomeStatus.FAILED_MALFORMED)
        self.assertFalse(outcome.status.is_retryable)
        self.assertTrue(outcome.should_advance_watermark)

    def test_duplicate_email_is_skipped(self):
        first = self.pipeline.process_email(self._raw("dup@test.com"))
        self.assertEqual(first.status, OutcomeStatus.COMPLETED)
        second = self.pipeline.process_email(self._raw("dup@test.com"))
        self.assertEqual(second.status, OutcomeStatus.SKIPPED_DUPLICATE)
        self.assertTrue(second.should_advance_watermark)

    # ---- circuit breaker integration ----
    def test_gmail_permanent_failure_does_not_crash_cycle(self):
        self.pipeline.himalaya = MagicMock()
        self.pipeline.himalaya.list_inbox.side_effect = HimalayaPermanentError(
            "list_inbox: authentication failed"
        )
        processed = self.pipeline.poll_cycle()
        self.assertEqual(processed, 0)
        self.assertFalse(self.pipeline.gmail_breaker.allow_request())

    def test_gmail_transient_failure_opens_breaker(self):
        self.pipeline.himalaya = MagicMock()
        self.pipeline.himalaya.list_inbox.side_effect = HimalayaTransientError(
            "list_inbox: connection timed out"
        )
        for _ in range(3):
            self.pipeline.poll_cycle()
        self.assertEqual(self.pipeline.gmail_breaker.state, CircuitState.OPEN)

    def test_open_breaker_short_circuits_without_calling_gmail(self):
        self.pipeline.himalaya = MagicMock()
        self.pipeline.himalaya.list_inbox.side_effect = HimalayaTransientError(
            "list_inbox: connection timed out"
        )
        for _ in range(3):
            self.pipeline.poll_cycle()
        calls_before = self.pipeline.himalaya.list_inbox.call_count
        self.pipeline.poll_cycle()
        # The breaker must stop us before another network round-trip.
        self.assertEqual(self.pipeline.himalaya.list_inbox.call_count, calls_before)


    def test_gmail_recovers_when_service_returns(self):
        self.pipeline.himalaya = MagicMock()
        self.pipeline.himalaya.list_inbox.side_effect = HimalayaTransientError(
            "list_inbox: connection timed out"
        )
        for _ in range(3):
            self.pipeline.poll_cycle()
        self.assertEqual(self.pipeline.gmail_breaker.state, CircuitState.OPEN)
        self.pipeline.gmail_breaker.recovery_timeout = 0.01
        time.sleep(0.02)
        self.pipeline.himalaya.list_inbox.side_effect = None
        self.pipeline.himalaya.list_inbox.return_value = []
        self.pipeline.poll_cycle()
        self.assertEqual(self.pipeline.gmail_breaker.state, CircuitState.CLOSED)

    # ---- batch isolation ----
    def test_one_bad_email_does_not_abort_the_batch(self):
        """A failure in the middle of a batch must not stop later emails.

        The second message fails at the database stage; the first and third must
        still be triaged and counted.
        """
        self.pipeline.himalaya = MagicMock()
        self.pipeline.himalaya.list_inbox.return_value = [
            {"id": "1", "flags": []},
            {"id": "2", "flags": []},
            {"id": "3", "flags": []},
        ]
        self.pipeline.watermark_mgr.save("0")

        messages = {
            "1": self._raw("batch-a@test.com", msg_id="m1"),
            "2": self._raw("batch-b@test.com", msg_id="m2"),
            "3": self._raw("batch-c@test.com", msg_id="m3"),
        }
        self.pipeline.himalaya.read_message.side_effect = lambda mid: messages[str(mid)]

        real_save = InsuranceRepository.save_triage_record

        def flaky_save(repo_self, record):
            if "batch-b@test.com" in (record.get("sender_email") or ""):
                raise sqlite3.OperationalError("database is locked")
            return real_save(repo_self, record)

        with patch(
            "src.db.repository.InsuranceRepository.save_triage_record",
            new=flaky_save,
        ):
            processed = self.pipeline.poll_cycle()

        # Two of the three were durably handled; the bad one did not stop the batch.
        self.assertEqual(processed, 2)
        self.assertEqual(self.pipeline.stats.emails_failed, 1)
        # The failed message is queued for a later retry.
        self.assertIn("m2", self.pipeline._deferred)

    def test_stats_track_failures(self):
        before = self.pipeline.stats.emails_failed
        self.pipeline.process_email({"nope": 1})
        self.assertEqual(self.pipeline.stats.emails_failed, before + 1)

    def test_health_monitor_reflects_failure(self):
        for _ in range(DEFAULT_DEGRADED_THRESHOLD):
            self.pipeline.process_email({"nope": 1})
        self.assertFalse(self.pipeline.health.get("preprocessing").healthy)

    def test_csv_export_failure_does_not_fail_the_email(self):
        """CSV is a side artifact; its failure must not lose the triage record."""
        with patch.object(
            self.pipeline.csv_exporter, "append_record", side_effect=OSError("disk full")
        ):
            outcome = self.pipeline.process_email(self._raw("csv-fail@test.com"))
        self.assertEqual(outcome.status, OutcomeStatus.COMPLETED)
        self.assertTrue(outcome.should_advance_watermark)

    # ---- helpers ----
    def _raw(self, sender, msg_id=None):
        return {
            "id": msg_id or sender,
            "message_id": msg_id or f"<{sender}>",
            "from": [{"name": "Test User", "addr": sender}],
            "to": [{"name": "Support", "addr": "support@insurance.example.com"}],
            "subject": "Question about my claim status",
            "body": [{"content": "Hi, can you please update me on my claim? Thank you."}],
            "date": "Thu, 25 Sep 2026 10:00:00 +0000",
        }


class TestHimalayaJsonShape(unittest.TestCase):
    """Regression tests for the real Himalaya JSON envelope shape.

    Himalaya's `message read -o json` emits `from`/`to` as a LIST of
    {name, addr} objects and `body` as a list of MIME parts. A parser that only
    understands the dict form silently produces an empty sender for every
    message, which quietly breaks customer matching, spam scoring, rate limits
    and CSV export. These tests pin the shape so it cannot regress.
    """

    def test_list_form_from_is_parsed(self):
        email = EmailPreprocessor.from_himalaya_dict(
            {
                "id": "1",
                "from": [{"name": "Priya Raman", "addr": "priya.raman@example.com"}],
                "to": [{"name": "Support", "addr": "support@insureco.com"}],
                "subject": "Claim status update please",
                "body": [{"content": "My claim CLM-2024-04190 has been in review for two weeks."}],
                "date": "Thu, 25 Sep 2026 10:00:00 +0000",
            }
        )
        self.assertEqual(email.sender_email, "priya.raman@example.com")
        self.assertEqual(email.sender_name, "Priya Raman")

    def test_dict_form_from_still_works(self):
        email = EmailPreprocessor.from_himalaya_dict(
            {
                "id": "2",
                "from": {"name": "Priya Raman", "addr": "priya.raman@example.com"},
                "subject": "s",
                "body": "hello",
                "date": "Thu, 25 Sep 2026 10:00:00 +0000",
            }
        )
        self.assertEqual(email.sender_email, "priya.raman@example.com")

    def test_string_form_from_still_works(self):
        email = EmailPreprocessor.from_himalaya_dict(
            {
                "id": "3",
                "from": '"Priya Raman" <priya.raman@example.com>',
                "subject": "s",
                "body": "hello",
                "date": "Thu, 25 Sep 2026 10:00:00 +0000",
            }
        )
        self.assertEqual(email.sender_email, "priya.raman@example.com")
        self.assertEqual(email.sender_name, "Priya Raman")

    def test_multipart_body_is_concatenated_not_truncated(self):
        email = EmailPreprocessor.from_himalaya_dict(
            {
                "id": "4",
                "from": [{"name": "P", "addr": "p@example.com"}],
                "subject": "s",
                "body": [
                    {"content": "First part of the message."},
                    {"content": "Second part of the message."},
                ],
                "date": "Thu, 25 Sep 2026 10:00:00 +0000",
            }
        )
        self.assertIn("First part", email.body_text)
        self.assertIn("Second part", email.body_text)

    def test_empty_from_list_yields_empty_sender_not_a_crash(self):
        email = EmailPreprocessor.from_himalaya_dict(
            {
                "id": "5",
                "from": [],
                "subject": "s",
                "body": "hello",
                "date": "Thu, 25 Sep 2026 10:00:00 +0000",
            }
        )
        self.assertEqual(email.sender_email, "")

if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main()
