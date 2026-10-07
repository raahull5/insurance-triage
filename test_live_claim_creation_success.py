from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from shutil import rmtree
from typing import Iterator

from src.config import load_config
from src.db.database import Database
from src.db.repository import InsuranceRepository
from src.ingestion.himalaya_client import HimalayaClient
from src.pipeline.autonomous_runner import AutonomousPipeline


TEST_DIR = Path("data/e2e_claim_success_20261004")

MESSAGE_ID = "13"

CUSTOMER_ID = "CUST-E2E-003"
VEHICLE_ID = "VEH-E2E-003"
POLICY_ID = "POL-E2E-003"
POLICY_NUMBER = "POL-TEST-003"

CUSTOMER_EMAIL = "rahul.kulkarni87@zohomail.in"
VIN = "TESTVIN00312345678"


@contextmanager
def repository_session(
    database: Database,
) -> Iterator[InsuranceRepository]:
    """
    Provide an InsuranceRepository backed by a managed SQLite connection.

    The repository expects a sqlite3.Connection rather than the Database
    wrapper, so this helper creates and closes the connection explicitly.
    """
    connection = database.get_connection()

    try:
        yield InsuranceRepository(connection)
    finally:
        connection.close()


def seed_authoritative_data(database: Database) -> None:
    """
    Seed the authoritative customer, vehicle and policy records.

    The official claim itself is NOT seeded here. It must be created by
    the real claim workflow.
    """

    today = date.today()

    start_date = today - timedelta(days=30)
    end_date = today + timedelta(days=335)

    connection = database.get_connection()

    try:
        connection.execute(
            """
            INSERT INTO customers (
                id,
                name,
                email,
                phone,
                address,
                city,
                state,
                postal_code
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                CUSTOMER_ID,
                "Rahul Kulkarni",
                CUSTOMER_EMAIL,
                "+919999999999",
                "E2E Test Address",
                "Pune",
                "Maharashtra",
                "411001",
            ),
        )

        connection.execute(
            """
            INSERT INTO vehicles (
                id,
                customer_id,
                vin,
                make,
                model,
                year,
                license_plate,
                color
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                VEHICLE_ID,
                CUSTOMER_ID,
                VIN,
                "Hyundai",
                "i20",
                2010,
                "MH12E2E003",
                "Test",
            ),
        )

        connection.execute(
            """
            INSERT INTO policies (
                id,
                policy_number,
                customer_id,
                vehicle_id,
                type,
                policy_type,
                status,
                start_date,
                end_date,
                premium,
                premium_amount,
                deductible,
                coverage_limit,
                no_claim_bonus_pct
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                POLICY_ID,
                POLICY_NUMBER,
                CUSTOMER_ID,
                VEHICLE_ID,
                "Motor Insurance",
                "Motor Insurance",
                "ACTIVE",
                start_date.isoformat(),
                end_date.isoformat(),
                25000.0,
                25000.0,
                5000.0,
                500000.0,
                0.0,
            ),
        )

        connection.commit()

    finally:
        connection.close()


def query_counts(database: Database):
    """Return claim, triage and support-ticket counts."""

    connection = database.get_connection()

    try:
        claims = connection.execute(
            "SELECT COUNT(*) FROM claims"
        ).fetchone()[0]

        triage_records = connection.execute(
            "SELECT COUNT(*) FROM triage_records"
        ).fetchone()[0]

        support_tickets = connection.execute(
            "SELECT COUNT(*) FROM support_tickets"
        ).fetchone()[0]

        return (
            claims,
            triage_records,
            support_tickets,
        )

    finally:
        connection.close()


def query_claim(database: Database):
    """Return the first official claim created by this E2E test."""

    connection = database.get_connection()

    try:
        return connection.execute(
            """
            SELECT
                id,
                claim_number,
                policy_id,
                customer_id,
                incident_date,
                reported_date,
                status,
                description,
                damage_description,
                claimed_amount
            FROM claims
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()

    finally:
        connection.close()


def main():
    print("=" * 70)
    print("LIVE CLAIM CREATION E2E TEST")
    print("=" * 70)

    # ---------------------------------------------------------------
    # SAFETY: completely isolated test directory
    # ---------------------------------------------------------------

    if TEST_DIR.exists():
        print(
            f"Removing previous isolated test directory: "
            f"{TEST_DIR}"
        )
        rmtree(TEST_DIR)

    TEST_DIR.mkdir(parents=True)

    db_path = TEST_DIR / "insurance_triage.db"
    csv_path = TEST_DIR / "triage_results.csv"
    watermark_path = TEST_DIR / "watermark.json"

    # ---------------------------------------------------------------
    # APPLICATION CONFIGURATION
    # ---------------------------------------------------------------

    config = load_config()

    # Redirect persistent application artifacts to the isolated
    # E2E directory.
    config.system.db_path = db_path
    config.system.csv_report_path = csv_path
    config.system.watermark_file = watermark_path

    # Critical safety controls.
    config.autoreply.enabled = False
    config.autoreply.dry_run = True

    print()
    print("Safety configuration:")
    print(f"  Database:   {db_path}")
    print(f"  CSV:        {csv_path}")
    print(f"  Watermark:  {watermark_path}")
    print(f"  Auto-reply: {config.autoreply.enabled}")
    print(f"  Dry-run:    {config.autoreply.dry_run}")

    # ---------------------------------------------------------------
    # ISOLATED DATABASE
    # ---------------------------------------------------------------

    database = Database(db_path)

    # Database.__init__() already initializes the schema.
    # No production database is touched.
    print()
    print("Isolated database initialized.")

    # ---------------------------------------------------------------
    # SEED AUTHORITATIVE CUSTOMER / VEHICLE / POLICY
    # ---------------------------------------------------------------

    print()
    print("Seeding authoritative customer / vehicle / policy...")

    seed_authoritative_data(database)

    # Verify the records using the SAME repository lookup methods
    # that the production claim workflow uses.
    with repository_session(database) as repository:

        customer = repository.find_customer_by_email(
            CUSTOMER_EMAIL
        )

        assert customer is not None, (
            "Seeded customer could not be found by email."
        )

        assert str(customer["id"]) == CUSTOMER_ID

        policy = repository.find_policy_by_number(
            POLICY_NUMBER
        )

        assert policy is not None, (
            "Seeded policy could not be found by policy number."
        )

        assert str(policy["id"]) == POLICY_ID
        assert str(policy["customer_id"]) == CUSTOMER_ID
        assert str(policy["policy_number"]) == POLICY_NUMBER

        vehicle = repository.find_vehicle_by_vin(
            VIN
        )

        assert vehicle is not None, (
            "Seeded vehicle could not be found by VIN."
        )

        assert str(vehicle["id"]) == VEHICLE_ID
        assert str(vehicle["customer_id"]) == CUSTOMER_ID

    print("PASS: authoritative customer exists.")
    print("PASS: authoritative vehicle exists.")
    print("PASS: authoritative policy exists.")

    print(f"  Customer:      {CUSTOMER_ID}")
    print(f"  Vehicle:       {VEHICLE_ID}")
    print(f"  Policy ID:     {POLICY_ID}")
    print(f"  Policy number: {POLICY_NUMBER}")

    # ---------------------------------------------------------------
    # READ REAL GMAIL MESSAGE
    # ---------------------------------------------------------------

    print()
    print(f"Reading Gmail message {MESSAGE_ID}...")

    gmail = HimalayaClient(
        account="gmail",
        mock_mode=False,
        config_path="data/config.toml",
    )

    raw_email = gmail.read_message(MESSAGE_ID)

    assert raw_email is not None

    print("Message retrieved.")

    # ---------------------------------------------------------------
    # CREATE REAL PIPELINE
    # ---------------------------------------------------------------

    pipeline = AutonomousPipeline(
        config=config,
        database=database,
        mock_mode=False,
        dry_run=True,
    )

    # ---------------------------------------------------------------
    # FIRST PROCESSING PASS
    # ---------------------------------------------------------------

    print()
    print("=" * 70)
    print("FIRST PROCESSING PASS")
    print("=" * 70)

    outcome = pipeline.process_email(
        raw_email,
        advance_watermark=False,
    )

    print()
    print(f"Status:   {outcome.status}")
    print(f"Email ID: {outcome.email_id}")
    print(f"Detail:   {outcome.detail}")
    print(f"Stage:    {outcome.stage}")

    # ---------------------------------------------------------------
    # DATABASE COUNTS
    # ---------------------------------------------------------------

    claims, triage_records, support_tickets = query_counts(
        database
    )

    print()
    print("Database after first pass:")
    print(f"  Claims:          {claims}")
    print(f"  Triage records:  {triage_records}")
    print(f"  Support tickets: {support_tickets}")

    # ---------------------------------------------------------------
    # OFFICIAL CLAIM MUST EXIST
    # ---------------------------------------------------------------

    assert claims == 1, (
        f"Expected exactly 1 official claim, found {claims}"
    )

    assert triage_records == 1, (
        f"Expected exactly 1 triage record, found "
        f"{triage_records}"
    )

    assert support_tickets == 1, (
        f"Expected exactly 1 support ticket, found "
        f"{support_tickets}"
    )

    claim = query_claim(database)

    assert claim is not None, (
        "Expected an official claim record."
    )

    (
        claim_id,
        claim_number,
        claim_policy_id,
        claim_customer_id,
        incident_date,
        reported_date,
        status,
        description,
        damage_description,
        claimed_amount,
    ) = claim

    print()
    print("Created official claim:")
    print(f"  Claim ID:          {claim_id}")
    print(f"  Claim number:      {claim_number}")
    print(f"  Policy ID:         {claim_policy_id}")
    print(f"  Customer ID:       {claim_customer_id}")
    print(f"  Incident date:     {incident_date}")
    print(f"  Reported date:     {reported_date}")
    print(f"  Status:             {status}")
    print(f"  Description:       {description}")
    print(f"  Damage:             {damage_description}")
    print(f"  Claimed amount:     {claimed_amount}")

    # ---------------------------------------------------------------
    # CLAIM FIELD ASSERTIONS
    # ---------------------------------------------------------------

    expected_claim_prefix = (
        f"CLM-{date.today().year}-"
    )

    assert claim_number.startswith(
        expected_claim_prefix
    ), (
        f"Unexpected claim number: {claim_number}"
    )

    assert claim_policy_id == POLICY_ID
    assert claim_customer_id == CUSTOMER_ID

    assert incident_date == "2026-09-27"

    assert status == "FNOL Received"

    assert description == (
        "Minor road accident involving my vehicle."
    )

    assert damage_description == (
        "Front bumper and headlight damaged."
    )

    assert float(claimed_amount) == 45000.0

    print()
    print("PASS: official claim was created correctly.")

    # ---------------------------------------------------------------
    # SAFETY CHECKS
    # ---------------------------------------------------------------

    assert config.autoreply.enabled is False
    assert config.autoreply.dry_run is True

    assert not watermark_path.exists(), (
        "Watermark must not be advanced by this test."
    )

    print("PASS: automatic replies remain disabled.")
    print("PASS: dry-run remains enabled.")
    print("PASS: production watermark was not advanced.")

    # ---------------------------------------------------------------
    # SECOND PROCESSING PASS
    # ---------------------------------------------------------------

    print()
    print("=" * 70)
    print("SECOND PROCESSING PASS")
    print("=" * 70)

    outcome2 = pipeline.process_email(
        raw_email,
        advance_watermark=False,
    )

    print()
    print(f"Status:   {outcome2.status}")
    print(f"Email ID: {outcome2.email_id}")
    print(f"Detail:   {outcome2.detail}")
    print(f"Stage:    {outcome2.stage}")

    claims2, triage_records2, support_tickets2 = query_counts(
        database
    )

    print()
    print("Database after second pass:")
    print(f"  Claims:          {claims2}")
    print(f"  Triage records:  {triage_records2}")
    print(f"  Support tickets: {support_tickets2}")

    # ---------------------------------------------------------------
    # DUPLICATE PROTECTION
    # ---------------------------------------------------------------

    assert outcome2.status == "SKIPPED_DUPLICATE", (
        f"Expected SKIPPED_DUPLICATE, got {outcome2.status}"
    )

    assert claims2 == 1, (
        f"Duplicate processing created another claim: "
        f"{claims2}"
    )

    assert triage_records2 == 1, (
        f"Duplicate processing created another triage record: "
        f"{triage_records2}"
    )

    assert support_tickets2 == 1, (
        f"Duplicate processing created another support ticket: "
        f"{support_tickets2}"
    )

    print()
    print("PASS: duplicate email was skipped.")
    print("PASS: no duplicate claim was created.")
    print("PASS: no duplicate triage record was created.")
    print("PASS: no duplicate support ticket was created.")

    # ---------------------------------------------------------------
    # FINAL SAFETY CHECKS
    # ---------------------------------------------------------------

    assert not watermark_path.exists()
    assert config.autoreply.enabled is False
    assert config.autoreply.dry_run is True

    print()
    print("=" * 70)
    print("LIVE CLAIM CREATION E2E TEST PASSED")
    print("=" * 70)

    print()
    print(f"Test artifacts: {TEST_DIR.resolve()}")
    print(f"Created claim:  {claim_number}")


if __name__ == "__main__":
    main()