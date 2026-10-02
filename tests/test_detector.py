"""Unit tests for new email detection, watermark management, and duplicate prevention."""

import unittest
import tempfile
import json
from pathlib import Path
import sys

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion.detector import WatermarkManager, EmailDetector, is_id_greater


class TestEmailDetector(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.watermark_file = Path(self.temp_dir.name) / "watermark.json"
        self.wm = WatermarkManager(self.watermark_file)
        self.detector = EmailDetector(self.wm)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_id_comparison(self):
        # Numeric comparison
        self.assertTrue(is_id_greater("10", "9"))
        self.assertTrue(is_id_greater("100", "99"))
        self.assertFalse(is_id_greater("9", "10"))
        self.assertFalse(is_id_greater("50", "50"))
        # String comparison
        self.assertTrue(is_id_greater("MSG-002", "MSG-001"))
        self.assertFalse(is_id_greater("MSG-001", "MSG-002"))

    def test_initial_watermark_ignores_history(self):
        historical_envelopes = [
            {"id": "1", "subject": "Old email 1"},
            {"id": "5", "subject": "Old email 5"},
            {"id": "3", "subject": "Old email 3"},
        ]
        self.assertIsNone(self.wm.current_watermark)
        
        # Initialize starting watermark
        self.detector.initialize_starting_watermark(historical_envelopes)
        self.assertEqual(self.wm.current_watermark, "5")

        # Ensure no historical emails are returned as new
        new_emails = self.detector.filter_new_emails(historical_envelopes)
        self.assertEqual(len(new_emails), 0)

    def test_subsequent_new_email_detection_order(self):
        # Established watermark at 5
        self.wm.save("5")

        inbox = [
            {"id": "2", "subject": "Old"},
            {"id": "5", "subject": "Old"},
            {"id": "8", "subject": "Newer 2"},
            {"id": "6", "subject": "Newer 1"},
            {"id": "12", "subject": "Newest"},
        ]

        new_emails = self.detector.filter_new_emails(inbox)
        # Should contain IDs 6, 8, 12 in ascending order
        self.assertEqual(len(new_emails), 3)
        self.assertEqual([e["id"] for e in new_emails], ["6", "8", "12"])

    def test_watermark_persistence_and_reload(self):
        self.wm.save("42")
        self.assertEqual(self.wm.current_watermark, "42")
        self.assertTrue(self.watermark_file.exists())

        # Create a new manager pointing at the same file
        wm2 = WatermarkManager(self.watermark_file)
        self.assertEqual(wm2.current_watermark, "42")
        self.assertEqual(wm2.processed_count, 1)


if __name__ == "__main__":
    unittest.main()
