from dataclasses import replace
from pathlib import Path
from datetime import date, timedelta

from src.config import load_config
from src.db.database import Database
from src.pipeline.autonomous_runner import AutonomousPipeline
from src.ingestion.himalaya_client import HimalayaClient


def seed_claim_test_data(database: Database):
    conn = database.get_connection()
    customer_id = "CUST-E2E-003"
    vehicle_id = "VEH-E2E-003"
    policy_id = "POL-E2E-003"

    conn.execute(
        "INSERT INTO customers (id, name, email, phone) VALUES (?, ?, ?, ?)",
        (customer_id, "Rahul Kulkarni", "rahul.kulkarni87@zohomail.in", "9999999999"),
    )
    conn.execute(
        "INSERT INTO vehicles (id, customer_id, vin, make, model, year) VALUES (?, ?, ?, ?, ?, ?)",
        (vehicle_id, customer_id, "TESTVIN00312345678", "Test", "Motor", 2022),
    )

    today = date.today()
    conn.execute(
        """
        INSERT INTO policies (
            id, policy_number, customer_id, vehicle_id, type, status,
            start_date, end_date, premium, deductible
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            policy_id, "POL-TEST-003", customer_id, vehicle_id,
            "Motor Insurance", "ACTIVE",
            (today - timedelta(days=30)).isoformat(),
            (today + timedelta(days=335)).isoformat(),
            25000.0, 5000.0,
        ),
    )
    conn.commit()
    conn.close()
    return customer_id, vehicle_id, policy_id


def main():
    test_dir = Path("data/e2e_claim_success_20261007")
    if test_dir.exists():
        raise RuntimeError(
            f"{test_dir} already exists. Inspect it before attempting another run."
        )
    test_dir.mkdir(parents=True)

    config = load_config()
    config = replace(
        config,
        system=replace(
            config.system,
            db_path=test_dir / "test.db",
            csv_report_path=test_dir / "test.csv",
            watermark_file=test_dir / "watermark.json",
        ),
        autoreply=replace(config.autoreply, enabled=False, dry_run=True),
    )

    if config.autoreply.enabled or not config.autoreply.dry_run:
        raise RuntimeError(
            "SAFETY FAILURE: automatic replies must be disabled and dry-run enabled."
        )

    print("Safety checks passed.")
    print("  Automatic replies: DISABLED")
    print("  Dry-run: ENABLED")
    print("  Watermark advancement: DISABLED")

    database = Database(config.system.db_path)
    customer_id, vehicle_id, policy_id = seed_claim_test_data(database)

    print("Seeded:")
    print("  Customer:", customer_id)
    print("  Vehicle:", vehicle_id)
    print("  Policy ID:", policy_id)
    print("  Policy number: POL-TEST-003")

    message_id = "13"
    print(f"\nReading Gmail message {message_id}...")

    client = HimalayaClient(
        account="gmail",
        mock_mode=False,
        config_path="data/config.toml",
    )
    message = client.read_message(message_id)

    if message is None or str(message.get("id")) != message_id:
        raise RuntimeError(f"Could not retrieve expected Gmail message {message_id}.")

    print("Message retrieved.")

    pipeline = AutonomousPipeline(
        config=config,
        database=database,
        mock_mode=False,
        dry_run=True,
    )

    print("\n========================================")
    print("FIRST PROCESSING PASS")
    print("========================================")

    first = pipeline.process_email(message, advance_watermark=False)

    print("Status:", getattr(first.status, "value", first.status))
    print("Email ID:", first.email_id)
    print("Stage:", first.stage)
    print("Retryable:", first.retryable)
    print("Should advance watermark:", first.should_advance_watermark)
    print("Detail:", first.detail)

    conn = database.get_connection()
    try:
        claims = conn.execute(
            "SELECT * FROM claims ORDER BY created_at ASC"
        ).fetchall()
        triage_records = conn.execute(
            "SELECT * FROM triage_records"
        ).fetchall()
        support_tickets = conn.execute(
            "SELECT * FROM support_tickets"
        ).fetchall()
    finally:
        conn.close()

    print("\nDATABASE RESULTS — FIRST PASS")
    print("Claims:", len(claims))
    print("Triage records:", len(triage_records))
    print("Support tickets:", len(support_tickets))

    assert first.email_id == message_id
    assert len(claims) == 1, f"Expected 1 claim, got {len(claims)}"

    claim = claims[0]

    assert claim["claim_number"].startswith("CLM-2026-")
    assert claim["policy_id"] == policy_id
    assert claim["customer_id"] == customer_id
    assert claim["incident_date"] == "2026-09-27"
    assert claim["status"] == "FNOL Received"
    assert claim["description"] == "Minor road accident involving my vehicle."
    assert claim["damage_description"] == "Front bumper and headlight damaged."
    assert float(claim["claimed_amount"]) == 45000.0

    assert len(triage_records) == 1
    assert len(support_tickets) == 1
    assert config.autoreply.enabled is False
    assert config.autoreply.dry_run is True
    assert not config.system.watermark_file.exists()

    print("PASS: official claim created:", claim["claim_number"])
    print("PASS: policy and customer matched.")
    print("PASS: incident/date/damage/amount extracted.")
    print("PASS: triage record created.")
    print("PASS: support ticket created.")
    print("PASS: automatic replies disabled.")
    print("PASS: watermark untouched.")

    print("\n========================================")
    print("SECOND PROCESSING PASS")
    print("========================================")

    second = pipeline.process_email(message, advance_watermark=False)

    print("Status:", getattr(second.status, "value", second.status))
    print("Email ID:", second.email_id)
    print("Stage:", second.stage)
    print("Detail:", second.detail)

    conn = database.get_connection()
    try:
        claims_after = conn.execute(
            "SELECT * FROM claims ORDER BY created_at ASC"
        ).fetchall()
        triage_after = conn.execute(
            "SELECT * FROM triage_records"
        ).fetchall()
        tickets_after = conn.execute(
            "SELECT * FROM support_tickets"
        ).fetchall()
    finally:
        conn.close()

    assert (
        getattr(second.status, "value", second.status)
        == "SKIPPED_DUPLICATE"
    ), (
        f"Expected SKIPPED_DUPLICATE, got "
        f"{getattr(second.status, 'value', second.status)}"
    )
    assert len(claims_after) == 1
    assert claims_after[0]["claim_number"] == claim["claim_number"]
    assert len(triage_after) == 1
    assert len(tickets_after) == 1
    assert not config.system.watermark_file.exists()

    print("PASS: duplicate email skipped.")
    print("PASS: no duplicate claim created.")
    print("PASS: no duplicate triage record created.")
    print("PASS: no duplicate support ticket created.")

    print("\n========================================")
    print("SUCCESSFUL CLAIM CREATION E2E PASSED")
    print("========================================")
    print("Test artifacts:", test_dir.resolve())


if __name__ == "__main__":
    main()
