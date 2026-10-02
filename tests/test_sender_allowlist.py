import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.config import AppConfig, SystemConfig, AutoReplyConfig
from src.db.database import Database
from src.pipeline.autonomous_runner import AutonomousPipeline


AUTHORIZED = "rahul.kulkarni87@zohomail.in"
UNAUTHORIZED = "someone@example.com"


class TestSenderAllowlist(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)

        self.config = AppConfig(
            system=SystemConfig(
                db_path=root / "test.db",
                watermark_file=root / "watermark.json",
                csv_report_path=root / "triage.csv",
                polling_interval_seconds=1,
            ),
            autoreply=AutoReplyConfig(enabled=False, dry_run=True),
        )
        self.db = Database(root / "test.db")
        self.db.initialize()

    def tearDown(self):
        self.tmpdir.cleanup()

    def make_pipeline(self, mock_mode=False, allowlist=AUTHORIZED):
        with patch.dict(
            "os.environ",
            {"INSURANCE_TRIAGE_ALLOWED_SENDERS": allowlist},
            clear=False,
        ):
            return AutonomousPipeline(
                self.config,
                self.db,
                mock_mode=mock_mode,
                dry_run=True,
            )

    def prepare_poll(self, pipeline, envelopes):
        pipeline.watermark_mgr = SimpleNamespace(
            current_watermark="0"
        )
        pipeline.detector = SimpleNamespace(
            filter_new_emails=lambda items: items
        )
        pipeline.gmail_breaker = SimpleNamespace(
            call=lambda fn: envelopes
        )
        pipeline.health = SimpleNamespace(
            record_success=Mock(),
            record_failure=Mock(),
        )
        pipeline._process_one = Mock(return_value=None)

    def test_authorized_sender_reaches_processing(self):
        pipeline = self.make_pipeline()
        envelopes = [{"id": "101", "from": AUTHORIZED}]
        self.prepare_poll(pipeline, envelopes)

        pipeline.poll_cycle()

        pipeline._process_one.assert_called_once()
        self.assertEqual(
            pipeline._process_one.call_args.args[0], "101"
        )

    def test_unauthorized_sender_is_rejected_before_read(self):
        pipeline = self.make_pipeline()
        envelopes = [{"id": "102", "from": UNAUTHORIZED}]
        self.prepare_poll(pipeline, envelopes)

        pipeline.poll_cycle()

        pipeline._process_one.assert_not_called()

    def test_missing_sender_is_rejected(self):
        pipeline = self.make_pipeline()
        envelopes = [{"id": "103"}]
        self.prepare_poll(pipeline, envelopes)

        pipeline.poll_cycle()

        pipeline._process_one.assert_not_called()

    def test_empty_allowlist_rejects_all_senders(self):
        pipeline = self.make_pipeline(allowlist="")
        envelopes = [{"id": "104", "from": AUTHORIZED}]
        self.prepare_poll(pipeline, envelopes)

        pipeline.poll_cycle()

        pipeline._process_one.assert_not_called()

    def test_mock_mode_bypasses_allowlist(self):
        pipeline = self.make_pipeline(
            mock_mode=True,
            allowlist="",
        )
        envelopes = [{"id": "105", "from": UNAUTHORIZED}]
        self.prepare_poll(pipeline, envelopes)

        pipeline.poll_cycle()

        pipeline._process_one.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class TestDirectSenderAllowlist(unittest.TestCase):
    """Test the second allowlist check inside _process_one."""

    def setUp(self):
        TestSenderAllowlist.setUp(self)

    def tearDown(self):
        TestSenderAllowlist.tearDown(self)

    def make_live_pipeline(self):
        return TestSenderAllowlist.make_pipeline(self, mock_mode=False)

    def test_authorized_sender_reaches_email_processing(self):
        pipeline = self.make_live_pipeline()
        pipeline.himalaya.read_message = Mock(
            return_value={"from": {"addr": AUTHORIZED}, "body": "Test"}
        )
        outcome = SimpleNamespace(
            status=SimpleNamespace(is_retryable=False),
            email_id="201",
        )
        pipeline.process_email = Mock(return_value=outcome)

        result = pipeline._process_one("201", defer_on_failure=False)

        self.assertIs(result, outcome)
        pipeline.himalaya.read_message.assert_called_once_with("201")
        pipeline.process_email.assert_called_once()

    def test_unauthorized_sender_is_rejected_after_read(self):
        pipeline = self.make_live_pipeline()
        pipeline.himalaya.read_message = Mock(
            return_value={"from": {"addr": UNAUTHORIZED}, "body": "Test"}
        )
        pipeline.process_email = Mock()

        result = pipeline._process_one("202", defer_on_failure=False)

        self.assertIsNone(result)
        pipeline.himalaya.read_message.assert_called_once_with("202")
        pipeline.process_email.assert_not_called()

    def test_missing_sender_is_rejected_after_read(self):
        pipeline = self.make_live_pipeline()
        pipeline.himalaya.read_message = Mock(
            return_value={"body": "Test"}
        )
        pipeline.process_email = Mock()

        result = pipeline._process_one("203", defer_on_failure=False)

        self.assertIsNone(result)
        pipeline.himalaya.read_message.assert_called_once_with("203")
        pipeline.process_email.assert_not_called()

class TestAllowlistWatermark(unittest.TestCase):
    """Verify watermark behavior when emails are rejected."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)

        self.config = AppConfig(
            system=SystemConfig(
                db_path=root / "test.db",
                watermark_file=root / "watermark.json",
                csv_report_path=root / "triage.csv",
                polling_interval_seconds=1,
            ),
            autoreply=AutoReplyConfig(enabled=False, dry_run=True),
        )
        self.db = Database(root / "test.db")
        self.db.initialize()

        with patch.dict(
            "os.environ",
            {"INSURANCE_TRIAGE_ALLOWED_SENDERS": AUTHORIZED},
            clear=False,
        ):
            self.pipeline = AutonomousPipeline(
                self.config, self.db, mock_mode=False, dry_run=True
            )

        self.pipeline.watermark_mgr.save("100")
        self.pipeline.gmail_breaker = SimpleNamespace(
            call=Mock()
        )
        self.pipeline.health = SimpleNamespace(
            record_success=Mock(),
            record_failure=Mock(),
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_rejected_email_does_not_advance_watermark(self):
        self.pipeline.gmail_breaker.call.return_value = [
            {"id": "101", "from": UNAUTHORIZED}
        ]
        self.pipeline._process_one = Mock()

        self.pipeline.poll_cycle()

        self.assertEqual(
            self.pipeline.watermark_mgr.current_watermark, "100"
        )
        self.pipeline._process_one.assert_not_called()

    def test_authorized_email_advances_past_rejected_email(self):
        def process_authorized(msg_id, defer_on_failure, envelope=None):
            self.pipeline.watermark_mgr.save(msg_id)
            self.pipeline.retry_queue.remove(msg_id)
            self.pipeline._deferred = [
                item for item in self.pipeline._deferred
                if item != msg_id
            ]
            return SimpleNamespace(
                status=SimpleNamespace(is_success=True),
                email_id=msg_id,
                should_advance_watermark=True,
            )

        self.pipeline._process_one = Mock(
            side_effect=process_authorized
        )

        # First cycle: process 101, reject 102.
        self.pipeline.gmail_breaker.call.return_value = [
            {"id": "102", "from": UNAUTHORIZED},
            {"id": "101", "from": AUTHORIZED},
        ]

        self.pipeline.poll_cycle()

        self.assertEqual(
            self.pipeline.watermark_mgr.current_watermark, "101"
        )
        self.pipeline._process_one.assert_called_once()
        self.assertEqual(
            self.pipeline._process_one.call_args.args[0], "101"
        )

        # Second cycle: 102 remains rejected; 103 is processed.
        self.pipeline._process_one.reset_mock()
        self.pipeline.gmail_breaker.call.return_value = [
            {"id": "101", "from": AUTHORIZED},
            {"id": "102", "from": UNAUTHORIZED},
            {"id": "103", "from": AUTHORIZED},
        ]

        self.pipeline.poll_cycle()

        self.assertEqual(
            self.pipeline.watermark_mgr.current_watermark, "103"
        )
        self.pipeline._process_one.assert_called_once()
        self.assertEqual(
            self.pipeline._process_one.call_args.args[0], "103"
        )


if __name__ == "__main__":
    unittest.main()
