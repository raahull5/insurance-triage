"""Unit tests for email parsing, HTML cleaning, entity decoding, and normalization."""

import unittest
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion.preprocessor import EmailPreprocessor, StructuredEmail


class TestEmailPreprocessor(unittest.TestCase):
    def test_clean_html_with_entities_scripts_styles(self):
        dirty_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <style>body { color: red; }</style>
            <script>alert("hack");</script>
        </head>
        <body>
            <!-- This is an internal comment -->
            <h1>Claim Notification &amp; Inquiry</h1>
            <p>Dear Support Team,&nbsp;My vehicle (VIN: 1HGCR2F83HA001) was damaged &lt;severely&gt; in &quot;Chicago&quot;.</p>
            <p>Please review my policy &#35;POL-IL-2024-8819.</p>
        </body>
        </html>
        """
        cleaned = EmailPreprocessor.clean_html(dirty_html)
        
        # Verify dangerous elements removed
        self.assertNotIn("alert", cleaned)
        self.assertNotIn("<style>", cleaned)
        self.assertNotIn("internal comment", cleaned)
        
        # Verify entities decoded
        self.assertIn("Claim Notification & Inquiry", cleaned)
        self.assertIn("Dear Support Team, My vehicle (VIN: 1HGCR2F83HA001) was damaged <severely> in \"Chicago\".", cleaned)
        self.assertIn("Please review my policy #POL-IL-2024-8819.", cleaned)

    def test_from_himalaya_dict(self):
        sample_dict = {
            "id": "104",
            "from": {"name": "Marcus Vance", "addr": "Marcus.Vance@Example.Com"},
            "to": [{"addr": "claims@apexshield.com"}],
            "subject": "Urgent: Vehicle Accident Report",
            "date": "2026-09-25T14:30:00Z",
            "html_body": "<p>I was in an accident today at 5th and Main.</p>",
            "in_reply_to": "<ref-12345@apexshield.com>",
            "headers": {"X-Priority": "1"}
        }

        email_obj = EmailPreprocessor.from_himalaya_dict(sample_dict)
        self.assertEqual(email_obj.message_id, "104")
        self.assertEqual(email_obj.sender_email, "marcus.vance@example.com")
        self.assertEqual(email_obj.sender_name, "Marcus Vance")
        self.assertEqual(email_obj.recipient, "claims@apexshield.com")
        self.assertEqual(email_obj.subject, "Urgent: Vehicle Accident Report")
        self.assertEqual(email_obj.body_text, "I was in an accident today at 5th and Main.")
        self.assertEqual(email_obj.in_reply_to, "<ref-12345@apexshield.com>")
        self.assertTrue(email_obj.is_html)

    def test_from_raw_rfc822(self):
        raw_email = (
            "From: \"Elena Rostova\" <elena.rostova@example.com>\r\n"
            "To: support@apexshield.com\r\n"
            "Subject: Proof of payment\r\n"
            "Date: Fri, 25 Sep 2026 10:00:00 -0500\r\n"
            "In-Reply-To: <orig-999@apexshield.com>\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "\r\n"
            "Attached is my payment confirmation for policy POL-OH-2024-1029.\r\n"
        )
        email_obj = EmailPreprocessor.from_raw_rfc822(raw_email, message_id="RFC-99")
        self.assertEqual(email_obj.message_id, "RFC-99")
        self.assertEqual(email_obj.sender_email, "elena.rostova@example.com")
        self.assertEqual(email_obj.sender_name, "Elena Rostova")
        self.assertEqual(email_obj.subject, "Proof of payment")
        self.assertEqual(email_obj.in_reply_to, "<orig-999@apexshield.com>")
        self.assertIn("Attached is my payment confirmation", email_obj.body_text)


if __name__ == "__main__":
    unittest.main()
