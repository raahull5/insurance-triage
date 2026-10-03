import json
from types import SimpleNamespace

from src.ai.reasoning_engine import LLMReasoningEngine

email = SimpleNamespace(
    subject="Question about my policy",
    body_text=(
        "Hello, I would like to understand what information "
        "is available about my insurance policy. Please guide me."
    ),
    sender_email="synthetic.test@example.com",
    sender_name="Test Customer",
    received_date="2026-09-29",
    headers={}
)

engine = LLMReasoningEngine(
    model="groq/qwen/qwen3.8-27b",
    temperature=0.2
)

print("Sending one synthetic request to OmniRoute...")

decision = engine.analyze(email, {})

print("\nLIVE INFERENCE RESULT")
print(json.dumps(decision.to_dict(), indent=2, ensure_ascii=False))

assert decision.intent in {
    "NEW_CLAIM", "CLAIM_STATUS", "CLAIM_DELAY",
    "CLAIM_REJECTION_DISPUTE", "CLAIM_DOCUMENTS",
    "SETTLEMENT_INQUIRY", "ADJUSTER_REASSIGNMENT",
    "POLICY_DETAILS", "RENEWAL", "CANCELLATION",
    "POLICY_CHANGE", "ADD_VEHICLE_DRIVER",
    "CERTIFICATE_OF_INSURANCE", "DEDUCTIBLE_INQUIRY",
    "NETWORK_GARAGE", "CASHLESS_REPAIR",
    "ROADSIDE_ASSISTANCE", "TOWING_SERVICE",
    "REPAIR_STATUS", "REPAIR_ESTIMATE_DISPUTE",
    "PAYMENT_ISSUE", "BILLING_DISPUTE", "REFUND",
    "PORTAL_ACCESS", "COMPLAINT", "FRAUD_SUSPICION",
    "GENERAL_QUERY"
}
assert 1 <= decision.urgency_score <= 10
assert decision.sentiment in {
    "Calm", "Frustrated", "Angry", "Anxious", "Neutral"
}
assert 0 <= decision.confidence <= 1

print("\nPASS: Live inference returned a valid triage decision.")
