import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.ai.reasoning_engine import LLMReasoningEngine


def fake_email():
    return SimpleNamespace(
        subject="Question about my policy",
        body_text="Please provide details about my policy.",
        sender_email="test@example.com",
        sender_name="Test Customer",
        received_date="2026-09-29",
        headers={}
    )


def fake_response(data):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(data)
                )
            )
        ]
    )


def valid_data():
    return {
        "intent": "GENERAL_QUERY",
        "urgency_score": 3,
        "sentiment": "Neutral",
        "summary": "Customer is asking a general policy question.",
        "escalation_needed": False,
        "escalation_reason": "",
        "suggested_reply": "Please let us know what information you need.",
        "confidence": 0.90,
        "policy_number": None,
        "claim_number": None
    }


def fake_escalation():
    return SimpleNamespace(
        human_review_required=False,
        priority="Low",
        reasons=[],
        routed_to="General Customer Support",
        send_acknowledgement_only=False
    )


class TestOmniRouteIntegration(unittest.TestCase):

    def setUp(self):
        self.engine = LLMReasoningEngine(
            model="groq/qwen/qwen3.8-27b",
            temperature=0.2
        )
        self.email = fake_email()
        self.context = {}

    def run_mocked(self, data):
        client = Mock()
        client.chat.completions.create.return_value = fake_response(data)

        with patch.object(self.engine, "_get_client", return_value=client), \
             patch(
                 "src.ai.reasoning_engine.EscalationEngine.evaluate",
                 return_value=fake_escalation()
             ):
            result = self.engine._reason_over_email(
                self.email, self.context, "Test prompt"
            )

        return result, client

    def test_valid_response(self):
        result, client = self.run_mocked(valid_data())

        self.assertEqual(result.intent, "GENERAL_QUERY")
        self.assertEqual(result.urgency_score, 3)
        self.assertEqual(result.sentiment, "Neutral")
        self.assertEqual(result.confidence, 0.90)
        self.assertEqual(result.priority, "Low")
        client.chat.completions.create.assert_called_once()
        print("PASS: Valid response")

    def test_malformed_json(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="{invalid json")
                )
            ]
        )

        with patch.object(self.engine, "_get_client", return_value=client):
            with self.assertRaises(ValueError):
                self.engine._reason_over_email(
                    self.email, self.context, "Test prompt"
                )

        print("PASS: Malformed JSON rejected")

    def test_unknown_intent(self):
        data = valid_data()
        data["intent"] = "MADE_UP_INTENT"

        with self.assertRaisesRegex(ValueError, "Unknown canonical intent"):
            self.run_mocked(data)

        print("PASS: Unknown intent rejected")

    def test_invalid_sentiment(self):
        data = valid_data()
        data["sentiment"] = "Urgent"

        with self.assertRaisesRegex(ValueError, "Invalid sentiment"):
            self.run_mocked(data)

        print("PASS: Invalid sentiment rejected")

    def test_api_timeout_propagates(self):
        client = Mock()
        client.chat.completions.create.side_effect = TimeoutError(
            "Simulated timeout"
        )

        with patch.object(self.engine, "_get_client", return_value=client):
            with self.assertRaises(TimeoutError):
                self.engine._reason_over_email(
                    self.email, self.context, "Test prompt"
                )

        print("PASS: API timeout propagates")

    def test_spam_bypasses_llm(self):
        with patch(
            "src.ai.reasoning_engine.SafetyEngine.is_spam_or_automated",
            return_value=(True, "Automated message")
        ), patch.object(self.engine, "_get_client") as client:
            result = self.engine.analyze(self.email, self.context)

        self.assertEqual(result.intent, "SPAM_OR_AUTOMATED")
        self.assertEqual(result.routed_to, "#archive")
        client.assert_not_called()

        print("PASS: Spam bypasses LLM")


if __name__ == "__main__":
    unittest.main(verbosity=2)
