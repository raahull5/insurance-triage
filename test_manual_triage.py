import argparse
from datetime import datetime, timezone

from src.config import load_config
from src.ingestion.preprocessor import StructuredEmail
from src.ai.reasoning_engine import LLMReasoningEngine


def main():
    parser = argparse.ArgumentParser(
        description="Read-only insurance email triage test"
    )
    parser.add_argument("--from", dest="sender",
                        default="customer@example.com")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", required=True)
    args = parser.parse_args()

    config = load_config()

    email = StructuredEmail(
        message_id=f"MANUAL-TEST-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        sender_email=args.sender,
        sender_name="Test Customer",
        recipient="support@example.com",
        subject=args.subject,
        received_date=datetime.now(timezone.utc).isoformat(),
        body_text=args.body,
        raw_body=args.body,
        is_html=False,
        headers={},
    )

    engine = LLMReasoningEngine(
        model=config.ai.model,
        temperature=config.ai.temperature,
    )

    print("\nRunning read-only triage...")
    print("Model:", config.ai.model)

    decision = engine.analyze(email, {})

    if decision is None:
        raise RuntimeError("The reasoning engine returned no decision.")

    print("\n========== TRIAGE RESULT ==========")
    for field in (
        "intent",
        "category",
        "priority",
        "urgency_score",
        "escalation_needed",
        "human_review_required",
        "summary",
        "routed_to",
        "suggested_reply",
    ):
        print(f"\n{field.upper()}:")
        print(getattr(decision, field, "Not available"))

    print("\n===================================")
    print("READ-ONLY TEST COMPLETE")
    print("No Gmail access or database writes were performed.")


if __name__ == "__main__":
    main()
