"""Unit tests for the Ticket Deduplication and Thread Resolution Engine."""

import unittest
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.threading_engine import ThreadResolutionEngine, ThreadMatchResult
from src.ai.reasoning_engine import TriageDecision
from src.ingestion.preprocessor import StructuredEmail


class TestThreadingEngine(unittest.TestCase):
    def setUp(self):
        self.engine = ThreadResolutionEngine(reopen_window_days=7, intent_window_hours=48)
        self.decision = TriageDecision(
            category="Claims",
            intent="CLAIM_STATUS",
            priority="High",
            urgency_score=7,
            sentiment="Neutral",
            escalation_needed=False,
            escalation_reason="",
            summary="Claim status query",
            policy_number="POL-2024-8831",
            claim_number="CLM-2024-04190",
            routed_to="Claims Operations",
            suggested_reply="",
        )
        self.db_context = {
            "customer": {"customer_id": "cust-001", "name": "Sarah Jenkins"},
        }
        self.existing_tickets = [
            {
                "id": "tck-uuid-1",
                "ticket_number": "TCK-2024-00101",
                "customer_id": "cust-001",
                "email_id": "orig-msg-123",
                "policy_id": "POL-2024-8831",
                "claim_id": "CLM-2024-04190",
                "subject": "Claim question",
                "category": "Claims",
                "intent": "CLAIM_STATUS",
                "status": "In-Progress",
                "created_at": (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
                "updated_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            }
        ]

    def test_level_1_in_reply_to_match(self):
        email = StructuredEmail(
            message_id="reply-456",
            sender_name="Sarah",
            sender_email="sarah@example.com",
            recipient="support@apexshield.com",
            subject="Re: Claim question",
            received_date="2024-09-25",
            body_text="Any updates?",
            in_reply_to="orig-msg-123",
        )
        res = self.engine.resolve_thread(email, self.decision, self.db_context, self.existing_tickets)
        self.assertTrue(res.matched)
        self.assertEqual(res.match_level, "LEVEL_1_IN_REPLY_TO")
        self.assertEqual(res.action, "APPEND_TO_THREAD")
        self.assertEqual(res.ticket_number, "TCK-2024-00101")

    def test_level_2_references_match(self):
        email = StructuredEmail(
            message_id="reply-789",
            sender_name="Sarah",
            sender_email="sarah@example.com",
            recipient="support@apexshield.com",
            subject="Re: Claim question",
            received_date="2024-09-25",
            body_text="Following up",
            references="<root-msg> <orig-msg-123>",
        )
        res = self.engine.resolve_thread(email, self.decision, self.db_context, self.existing_tickets)
        self.assertTrue(res.matched)
        self.assertEqual(res.match_level, "LEVEL_2_REFERENCES")
        self.assertEqual(res.action, "APPEND_TO_THREAD")

    def test_level_3_subject_entity_match(self):
        email = StructuredEmail(
            message_id="new-msg-999",
            sender_name="Sarah",
            sender_email="sarah@example.com",
            recipient="support@apexshield.com",
            subject="Fwd: Claim question",
            received_date="2024-09-25",
            body_text="Checking in on CLM-2024-04190",
        )
        res = self.engine.resolve_thread(email, self.decision, self.db_context, self.existing_tickets)
        self.assertTrue(res.matched)
        self.assertEqual(res.match_level, "LEVEL_3_SUBJECT_ENTITIES")
        self.assertEqual(res.action, "APPEND_TO_THREAD")

    def test_reopen_recently_resolved_ticket(self):
        resolved_tickets = [
            {
                "id": "tck-uuid-2",
                "ticket_number": "TCK-2024-00102",
                "customer_id": "cust-001",
                "email_id": "msg-resolved-old",
                "subject": "Settlement payment",
                "status": "Resolved",
                "updated_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),  # 2 days ago < 7d
            }
        ]
        email = StructuredEmail(
            message_id="msg-followup",
            sender_name="Sarah",
            sender_email="sarah@example.com",
            recipient="support@apexshield.com",
            subject="Re: Settlement payment",
            received_date="2024-09-25",
            body_text="Re-opening question",
            in_reply_to="msg-resolved-old",
        )
        res = self.engine.resolve_thread(email, self.decision, self.db_context, resolved_tickets)
        self.assertTrue(res.matched)
        self.assertEqual(res.action, "REOPEN_TICKET")
        self.assertEqual(res.ticket_number, "TCK-2024-00102")

    def test_new_ticket_for_old_resolved(self):
        old_resolved_tickets = [
            {
                "id": "tck-uuid-3",
                "ticket_number": "TCK-2024-00050",
                "customer_id": "cust-001",
                "email_id": "ancient-msg",
                "subject": "Old question",
                "status": "Resolved",
                "updated_at": (datetime.now(timezone.utc) - timedelta(days=20)).isoformat(),  # 20 days ago > 7d
            }
        ]
        email = StructuredEmail(
            message_id="msg-very-new",
            sender_name="Sarah",
            sender_email="sarah@example.com",
            recipient="support@apexshield.com",
            subject="Re: Old question",
            received_date="2024-09-25",
            body_text="New issue on old thread",
            in_reply_to="ancient-msg",
        )
        res = self.engine.resolve_thread(email, self.decision, self.db_context, old_resolved_tickets)
        self.assertTrue(res.matched)
        self.assertEqual(res.action, "CREATE_NEW_LINKED_TICKET")
        self.assertEqual(res.linked_ticket_number, "TCK-2024-00050")


if __name__ == "__main__":
    unittest.main()
