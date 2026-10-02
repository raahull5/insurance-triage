
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from src.config import load_config
from src.core.resilience import OutcomeStatus
from src.db.database import Database
from src.pipeline.autonomous_runner import (
    AutonomousPipeline,
    _sender_addresses,
)
from src.ai.reasoning_engine import TriageDecision
from src.ai.intents import get_intent_info
from src.ingestion.himalaya_client import HimalayaClient


def main():
    # Create a new isolated directory for every run.
    test_dir = Path(
        tempfile.mkdtemp(
            prefix="retry_gmail_e2e_",
            dir="data",
        )
    )

    # Snapshot the production watermark to verify that it
    # remains unchanged throughout the test.
    production_watermark = Path("data/watermark.json")
    original_watermark = (
        production_watermark.read_bytes()
        if production_watermark.exists()
        else None
    )

    print("Test directory:", test_dir.resolve())

    # Load configuration and redirect all test outputs
    # to the isolated test directory.
    config = load_config()
    config = replace(
        config,
        system=replace(
            config.system,
            db_path=test_dir / "test.db",
            csv_report_path=test_dir / "test.csv",
            watermark_file=test_dir / "watermark.json",
        ),
        autoreply=replace(
            config.autoreply,
            enabled=False,
            dry_run=True,
        ),
    )

    assert not config.autoreply.enabled
    assert config.autoreply.dry_run

    # STEP 1: Retrieve the intended Gmail message.
    print("\nSTEP 1: Retrieving Gmail message 8")

    client = HimalayaClient(
        account=config.gmail.account_name,
        mock_mode=False,
        config_path="data/config.toml",
    )

    message = client.read_message("8")

    assert message, "Gmail returned no message."
    assert str(message.get("id")) == "8", (
        f"Unexpected message ID: {message.get('id')}"
    )

    print("PASS: Gmail message retrieved.")

    # Retrieve the corresponding Gmail envelope.
    # The message-read response does not include sender
    # metadata, so we retrieve it separately.
    envelopes = client.list_inbox(
        page_size=50,
        page=1,
    )

    matching_envelopes = [
        item
        for item in envelopes
        if str(item.get("id")) == "8"
    ]

    assert len(matching_envelopes) == 1, (
        "Could not uniquely locate Gmail envelope "
        "for message 8."
    )

    envelope = matching_envelopes[0]

    sender_addresses = _sender_addresses(
        envelope.get("from")
    )

    assert sender_addresses, (
        "Gmail envelope has no usable sender address."
    )

    expected_sender = "rahul.kulkarni87@zohomail.in"

    assert expected_sender in sender_addresses, (
        f"Unexpected sender: {sender_addresses}"
    )

    print("PASS: Gmail envelope retrieved.")
    print("PASS: Sender verified:", expected_sender)

    # STEP 2: Create an isolated database and pipeline.
    print("\nSTEP 2: Creating isolated pipeline")

    database = Database(config.system.db_path)

    pipeline = AutonomousPipeline(
        config=config,
        database=database,
        mock_mode=False,
        dry_run=True,
    )

    pipeline.allowed_senders = sender_addresses

    assert not config.autoreply.enabled
    assert config.autoreply.dry_run

    print("PASS: Isolated pipeline created.")
    print("PASS: Automatic replies disabled.")

    # STEP 3: Force a transient AI timeout.
    print("\nSTEP 3: Simulating transient AI failure")

    with patch.object(
        pipeline.ai_engine,
        "analyze",
        side_effect=TimeoutError(
            "Simulated temporary AI timeout"
        ),
    ):
        first = pipeline._process_one(
            "8",
            defer_on_failure=True,
            envelope=envelope,
        )

    assert first is not None, (
        "First attempt returned no outcome."
    )

    assert first.status == OutcomeStatus.FAILED_LLM, (
        f"Expected FAILED_LLM, got {first.status}"
    )

    assert first.email_id == "8", (
        f"Unexpected email ID: {first.email_id}"
    )

    assert pipeline.retry_queue.pending == ["8"], (
        f"Unexpected retry queue: "
        f"{pipeline.retry_queue.pending}"
    )

    print("PASS: Transient failure classified as FAILED_LLM.")
    print("PASS: Message persisted in retry queue.")

    # STEP 4: Simulate an application restart.
    print("\nSTEP 4: Simulating application restart")

    restarted_database = Database(
        config.system.db_path
    )

    restarted_pipeline = AutonomousPipeline(
        config=config,
        database=restarted_database,
        mock_mode=False,
        dry_run=True,
    )

    restarted_pipeline.allowed_senders = sender_addresses

    assert restarted_pipeline.retry_queue.pending == ["8"], (
        "Pending message was not restored after restart."
    )

    print("PASS: Retry queue restored after restart.")

    # Prepare a deterministic AI decision.
    # No live AI inference is required for recovery.
    intent_info = get_intent_info("GENERAL_QUERY")

    decision = TriageDecision(
        category=intent_info.category,
        intent="GENERAL_QUERY",
        priority="Low",
        urgency_score=1,
        sentiment="Neutral",
        escalation_needed=False,
        escalation_reason="",
        summary="Test-only triage decision.",
        policy_number=None,
        claim_number=None,
        routed_to=intent_info.routing_destination,
        suggested_reply="",
        confidence=0.99,
        human_review_required=False,
        raw_response="Mocked decision; no live AI call.",
    )

    # STEP 5: Retry using mocked AI and real processing.
    print("\nSTEP 5: Retrying with mocked AI decision")

    with patch.object(
        restarted_pipeline.ai_engine,
        "analyze",
        return_value=decision,
    ) as mock_analyze:
        recovered = restarted_pipeline._process_one(
            "8",
            defer_on_failure=True,
            envelope=envelope,
        )

    assert mock_analyze.call_count == 1, (
        "Expected exactly one mocked AI analysis."
    )

    assert recovered is not None, (
        "Recovery returned no outcome."
    )

    assert recovered.status == OutcomeStatus.COMPLETED, (
        f"Recovery failed: {recovered.status}: "
        f"{recovered.detail}"
    )

    assert recovered.email_id == "8", (
        f"Unexpected recovered email ID: "
        f"{recovered.email_id}"
    )

    assert restarted_pipeline.retry_queue.pending == [], (
        f"Retry queue not cleared: "
        f"{restarted_pipeline.retry_queue.pending}"
    )

    assert restarted_pipeline._deferred == [], (
        f"In-memory queue not cleared: "
        f"{restarted_pipeline._deferred}"
    )

    print("PASS: Real processing completed with mocked AI.")
    print("PASS: Persistent retry queue cleared.")
    print("PASS: In-memory retry queue cleared.")

    # STEP 6: Verify database idempotency and isolation.
    print("\nSTEP 6: Verifying database and isolation")

    conn = restarted_database.get_connection()

    try:
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM triage_records
            WHERE email_id = ?
            """,
            ("8",),
        ).fetchone()[0]
    finally:
        conn.close()

    assert count == 1, (
        f"Expected one triage record for message 8; "
        f"found {count}."
    )

    assert not config.autoreply.enabled
    assert config.autoreply.dry_run

    current_watermark = (
        production_watermark.read_bytes()
        if production_watermark.exists()
        else None
    )

    assert current_watermark == original_watermark, (
        "Production watermark changed during the test."
    )

    print("PASS: Exactly one triage record exists.")
    print("PASS: Automatic replies remain disabled.")
    print("PASS: Production watermark is unchanged.")

    print("\nALL ASSERTIONS PASSED")
    print(
        "Gmail read + retry recovery + isolated DB "
        "test succeeded."
    )
    print("AI inference was mocked; no live AI call was made.")
    print("Test artifacts:", test_dir.resolve())


if __name__ == "__main__":
    main()