"""Unit tests for Himalaya Gmail integration and Keychain configuration."""

import unittest
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion.himalaya_client import HimalayaClient
from src.ingestion.gmail_config import generate_himalaya_gmail_toml


class TestGmailIntegration(unittest.TestCase):
    def test_keychain_toml_generation(self):
        toml_content = generate_himalaya_gmail_toml(
            email="support@apexshield.com",
            keychain_service="himalaya_gmail",
            keychain_account="support@apexshield.com",
        )
        self.assertIn("[accounts.default]", toml_content)
        self.assertIn("imap.gmail.com", toml_content)
        self.assertIn("smtp.gmail.com", toml_content)
        self.assertIn("security find-generic-password", toml_content)
        self.assertIn("himalaya_gmail", toml_content)
        self.assertIn("support@apexshield.com", toml_content)
        self.assertIn('folder.aliases.inbox = "INBOX"', toml_content)
        self.assertIn('folder.aliases.sent = "[Gmail]/Sent Mail"', toml_content)
        # Ensure password is NEVER plaintext
        self.assertNotIn("password =", toml_content)
        self.assertNotIn("raw =", toml_content)

    def test_mock_client_operations(self):
        client = HimalayaClient(account="default", mock_mode=True)
        conn_res = client.test_connection()
        self.assertTrue(conn_res["connected"])
        self.assertEqual(conn_res["mode"], "mock")

        # Test injecting and listing envelopes
        client.inject_mock_email({
            "id": "101",
            "subject": "Need help with claim",
            "from": {"addr": "sarah.jenkins@example.com", "name": "Sarah Jenkins"},
            "body": "Hello, here is my claim update request.",
        })
        inbox = client.list_inbox()
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["id"], "101")

        # Test reading individual message
        msg = client.read_message("101")
        self.assertIsNotNone(msg)
        self.assertEqual(msg["subject"], "Need help with claim")

        # Test sending reply
        sent_ok = client.send_email(
            to="sarah.jenkins@example.com",
            subject="Re: Need help with claim",
            body="We have received your request.",
            in_reply_to="101",
        )
        self.assertTrue(sent_ok)
        sent = client.get_sent_emails()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["to"], "sarah.jenkins@example.com")
        self.assertEqual(sent[0]["in_reply_to"], "101")


if __name__ == "__main__":
    unittest.main()
