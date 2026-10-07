import sqlite3

from src.ai.claim_workflow import ClaimWorkflow
from src.db.repository import InsuranceRepository


def make_repository():
    """
    Create a minimal in-memory database that mirrors the
    columns required by the claim workflow and repository.
    """

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    conn.executescript(
        """
        CREATE TABLE customers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL
        );

        CREATE TABLE vehicles (
            id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            vin TEXT UNIQUE NOT NULL,
            make TEXT NOT NULL,
            model TEXT NOT NULL,
            year INTEGER NOT NULL,
            FOREIGN KEY(customer_id)
                REFERENCES customers(id)
        );

        CREATE TABLE policies (
            id TEXT PRIMARY KEY,
            policy_number TEXT UNIQUE NOT NULL,
            customer_id TEXT NOT NULL,
            vehicle_id TEXT NOT NULL,
            type TEXT NOT NULL,
            policy_type TEXT,
            status TEXT NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            premium REAL NOT NULL,
            deductible REAL NOT NULL,
            FOREIGN KEY(customer_id)
                REFERENCES customers(id),
            FOREIGN KEY(vehicle_id)
                REFERENCES vehicles(id)
        );

        CREATE TABLE claims (
            id TEXT PRIMARY KEY,
            claim_number TEXT UNIQUE NOT NULL,
            policy_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            incident_date DATE NOT NULL,
            reported_date DATE,
            status TEXT NOT NULL,
            description TEXT,
            damage_description TEXT,
            claimed_amount REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(policy_id)
                REFERENCES policies(id),
            FOREIGN KEY(customer_id)
                REFERENCES customers(id)
        );

        INSERT INTO customers (
            id,
            name,
            email
        )
        VALUES (
            'CUST-001',
            'Rahul Test',
            'rahul@example.com'
        );

        INSERT INTO vehicles (
            id,
            customer_id,
            vin,
            make,
            model,
            year
        )
        VALUES (
            'VEH-001',
            'CUST-001',
            '1HGCM82633A123456',
            'Honda',
            'City',
            2023
        );

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
            deductible
        )
        VALUES (
            'POL-ID-001',
            'POL-ABC123',
            'CUST-001',
            'VEH-001',
            'Motor',
            'Comprehensive',
            'ACTIVE',
            '2026-01-01',
            '2026-12-31',
            20000.0,
            5000.0
        );
        """
    )

    return InsuranceRepository(conn)


# ==========================================================================
# TEST 1
# Complete FNOL + valid customer + valid policy
# should create an official claim.
# ==========================================================================

def test_complete_verified_claim_is_created():

    repo = make_repository()
    workflow = ClaimWorkflow(repo)

    result = workflow.process(
        subject="New motor insurance claim POL-ABC123",
        body=(
            "Policy: POL-ABC123\n"
            "Incident Date: 2026-06-15\n"
            "Accident: Rear-end collision\n"
            "Damage: Rear bumper damaged\n"
            "INR 75000"
        ),
        customer_id="CUST-001",
    )

    assert result.status == ClaimWorkflow.CREATED

    assert result.claim is not None

    assert result.claim["policy_id"] == "POL-ID-001"

    assert result.claim["customer_id"] == "CUST-001"

    assert result.claim["incident_date"] == "2026-06-15"

    assert result.claim["description"] == "Rear-end collision"

    assert result.claim["damage_description"] == (
        "Rear bumper damaged"
    )

    assert result.claim["claimed_amount"] == 75000.0


# ==========================================================================
# TEST 2
# Complete FNOL but customer identity is unavailable.
#
# No claim must be created.
# ==========================================================================

def test_missing_customer_requires_human_review():

    repo = make_repository()
    workflow = ClaimWorkflow(repo)

    result = workflow.process(
        subject="New claim POL-ABC123",
        body=(
            "Policy: POL-ABC123\n"
            "Incident Date: 2026-06-15\n"
            "Accident: Rear-end collision"
        ),
        customer_id=None,
    )

    assert result.status == ClaimWorkflow.HUMAN_REVIEW

    assert result.human_review_required is True

    assert result.claim is None

    count = repo.conn.execute(
        "SELECT COUNT(*) FROM claims"
    ).fetchone()[0]

    assert count == 0


# ==========================================================================
# TEST 3
# FNOL is incomplete.
#
# No policy verification or claim creation should occur.
# ==========================================================================

def test_incomplete_fnol_requires_human_review():

    repo = make_repository()
    workflow = ClaimWorkflow(repo)

    result = workflow.process(
        subject="New claim POL-ABC123",
        body="Policy: POL-ABC123",
        customer_id="CUST-001",
    )

    assert result.status == ClaimWorkflow.HUMAN_REVIEW

    assert result.human_review_required is True

    assert "incident_date" in result.intake.missing_fields

    assert (
        "incident_description"
        in result.intake.missing_fields
    )

    assert result.claim is None

    count = repo.conn.execute(
        "SELECT COUNT(*) FROM claims"
    ).fetchone()[0]

    assert count == 0


# ==========================================================================
# TEST 4
# Policy number does not exist.
#
# No claim should be created.
# ==========================================================================

def test_policy_failure_requires_human_review():

    repo = make_repository()
    workflow = ClaimWorkflow(repo)

    result = workflow.process(
        subject="New claim POL-NOTFOUND",
        body=(
            "Policy: POL-NOTFOUND\n"
            "Incident Date: 2026-06-15\n"
            "Accident: Rear-end collision"
        ),
        customer_id="CUST-001",
    )

    assert result.status == ClaimWorkflow.HUMAN_REVIEW

    assert result.verification.status == (
        "POLICY_NOT_FOUND"
    )

    assert result.claim is None

    count = repo.conn.execute(
        "SELECT COUNT(*) FROM claims"
    ).fetchone()[0]

    assert count == 0


# ==========================================================================
# TEST 5
# Same FNOL submitted twice.
#
# First request creates a claim.
# Second request must detect the duplicate.
# ==========================================================================

def test_duplicate_claim_is_not_created_twice():

    repo = make_repository()
    workflow = ClaimWorkflow(repo)

    kwargs = {
        "subject": "New claim POL-ABC123",
        "body": (
            "Policy: POL-ABC123\n"
            "Incident Date: 2026-06-15\n"
            "Accident: Rear-end collision"
        ),
        "customer_id": "CUST-001",
    }

    first = workflow.process(**kwargs)

    second = workflow.process(**kwargs)

    assert first.status == ClaimWorkflow.CREATED

    assert second.status == (
        ClaimWorkflow.DUPLICATE_CLAIM
    )

    assert second.claim is not None

    count = repo.conn.execute(
        "SELECT COUNT(*) FROM claims"
    ).fetchone()[0]

    assert count == 1