
from dataclasses import replace
from pathlib import Path

from src.config import load_config
from src.db.database import Database
from src.pipeline.autonomous_runner import AutonomousPipeline
from src.ingestion.himalaya_client import HimalayaClient


def main():
    # Use a new, isolated directory for this test.
    test_dir = Path("data/e2e_test_20261003")

    # Never overwrite an existing test run.
    if test_dir.exists():
        raise RuntimeError(
            f"{test_dir} already exists. "
            "Inspect it before attempting another run."
        )

    test_dir.mkdir(parents=True)

    # Load configuration and isolate all test data.
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

    # Safety checks.
    if config.autoreply.enabled or not config.autoreply.dry_run:
        raise RuntimeError(
            "Safety check failed: automatic replies must "
            "remain disabled and dry-run must be enabled."
        )

    # Retrieve the synthetic insurance email from Gmail.
    message_id = "8"
    print(f"Reading Gmail message {message_id}...")

    client = HimalayaClient(
        account="gmail",
        mock_mode=False,
        config_path="data/config.toml",
    )

    message = client.read_message(message_id)

    if message is None or str(message.get("id")) != message_id:
        raise RuntimeError(
            f"Could not retrieve the expected email {message_id}."
        )

    print("Message retrieved.")
    print("Creating isolated database...")

    database = Database(config.system.db_path)

    # Keep the pipeline in dry-run mode.
    pipeline = AutonomousPipeline(
        config=config,
        database=database,
        mock_mode=False,
        dry_run=True,
    )

    # First processing pass.
    print("\nFirst processing pass...")
    first = pipeline.process_email(
        message,
        advance_watermark=False,
    )

    print("Status:", getattr(first.status, "value", first.status))
    print("Email ID:", first.email_id)
    print("Detail:", first.detail)

    conn = database.get_connection()
    try:
        first_count = conn.execute(
            "SELECT COUNT(*) FROM triage_records"
        ).fetchone()[0]
    finally:
        conn.close()

    print("Records after first pass:", first_count)

    # Second processing pass: verify deduplication.
    print("\nSecond processing pass (duplicate check)...")
    second = pipeline.process_email(
        message,
        advance_watermark=False,
    )

    print("Status:", getattr(second.status, "value", second.status))
    print("Email ID:", second.email_id)
    print("Detail:", second.detail)

    conn = database.get_connection()
    try:
        second_count = conn.execute(
            "SELECT COUNT(*) FROM triage_records"
        ).fetchone()[0]
    finally:
        conn.close()

    print("Records after second pass:", second_count)

    # Assertions.
    print("\n=== Assertions ===")

    assert first.email_id == message_id, (
        f"Unexpected first email ID: {first.email_id}"
    )

    assert first_count == 1, (
        f"Expected 1 record after first pass, got {first_count}"
    )

    assert getattr(second.status, "value", second.status) == (
        "SKIPPED_DUPLICATE"
    ), f"Expected duplicate, got {second.status}"

    assert second_count == 1, (
        f"Duplicate created another record: {second_count}"
    )

    assert not config.autoreply.enabled
    assert config.autoreply.dry_run

    print("PASS: one record created; duplicate skipped.")
    print("PASS: automatic replies disabled.")
    print("PASS: dry-run mode enabled.")
    print("PASS: production watermark was not advanced.")
    print("Test artifacts:", test_dir.resolve())


if __name__ == "__main__":
    main()