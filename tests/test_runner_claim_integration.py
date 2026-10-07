import sqlite3

from src.db.database import Database
from src.db.repository import InsuranceRepository
from src.ai.claim_workflow import ClaimWorkflow


def build_test_db(tmp_path):
    db_path = tmp_path / "runner_claim_test.db"

    database = Database(db_path)
    conn = database.get_connection()
    repo = InsuranceRepository(conn)

    conn.execute(
        """
        INSERT INTO customers (id, name, email)
        VALUES (?, ?, ?)
        """,
        ("CUST-TEST-001", "Test Customer", "test@example.com"),
    )

    conn.execute(
        """
        INSERT INTO vehicles (
            id, customer_id, vin, make, model, year, license_plate, color
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "VEH-001",
            "CUST-TEST-001",
            "1HGBH41JXMN109186",
            "Honda",
            "City",
            2024,
            "TEST-001",
            "White",
        ),
    )

    conn.execute(
        """
        INSERT INTO policies (
            id, policy_number, customer_id, vehicle_id,
            type, policy_type, status,
            start_date, end_date,
            premium, premium_amount,
            deductible, coverage_limit,
            no_claim_bonus_pct
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "POLICY-ID-001",
            "POL-TEST-001",
            "CUST-TEST-001",
            "VEH-001",
            "AUTO",
            "Comprehensive",
            "ACTIVE",
            "2026-01-01",
            "2026-12-31",
            10000.0,
            10000.0,
            5000.0,
            500000.0,
            0.0,
        ),
    )

    conn.commit()
    return database, conn, repo


def test_new_claim_workflow_creates_claim(tmp_path):
    database, conn, repo = build_test_db(tmp_path)

    workflow = ClaimWorkflow(repo)

    result = workflow.process(
        subject="New motor insurance claim - POL-TEST-001",
        body=(
            "Incident: Vehicle was damaged in an accident.\n"
            "Damage: Front bumper and headlamp damaged.\n"
            "Incident Date: 2026-09-30\n"
            "Policy: POL-TEST-001"
        ),
        customer_id="CUST-TEST-001",
    )

    assert result.status == "CREATED"
    assert result.claim is not None
    assert result.claim["policy_id"] == "POLICY-ID-001"
    assert result.claim["customer_id"] == "CUST-TEST-001"

    claims = conn.execute("SELECT * FROM claims").fetchall()

    assert len(claims) == 1

    conn.close()


def test_duplicate_new_claim_does_not_create_second_claim(tmp_path):
    database, conn, repo = build_test_db(tmp_path)

    workflow = ClaimWorkflow(repo)

    kwargs = {
        "subject": "New motor insurance claim - POL-TEST-001",
        "body": (
            "Incident: Vehicle was damaged in an accident.\n"
            "Damage: Front bumper damaged.\n"
            "Incident Date: 2026-09-30\n"
            "Policy: POL-TEST-001"
        ),
        "customer_id": "CUST-TEST-001",
    }

    first = workflow.process(**kwargs)
    second = workflow.process(**kwargs)

    assert first.status == "CREATED"
    assert second.status == "DUPLICATE_CLAIM"

    claims = conn.execute("SELECT * FROM claims").fetchall()

    assert len(claims) == 1

    conn.close()


def test_unverified_customer_never_creates_claim(tmp_path):
    database, conn, repo = build_test_db(tmp_path)

    workflow = ClaimWorkflow(repo)

    result = workflow.process(
        subject="New motor insurance claim - POL-TEST-001",
        body=(
            "Incident: Vehicle was damaged in an accident.\n"
            "Incident Date: 2026-09-30\n"
            "Policy: POL-TEST-001"
        ),
        customer_id="CUST-ATTACKER",
    )

    assert result.status == "HUMAN_REVIEW"

    claims = conn.execute("SELECT * FROM claims").fetchall()

    assert len(claims) == 0

    conn.close()
