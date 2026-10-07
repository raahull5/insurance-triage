import sqlite3

from src.ai.claim_creator import ClaimCreationService
from src.ai.claim_intake import ClaimIntake
from src.ai.policy_verifier import (
    PolicyVerificationResult,
    PolicyVerifier,
)
from src.db.repository import InsuranceRepository
from src.db.schema import create_tables


def build_test_database():
    conn = sqlite3.connect(":memory:")

    conn.row_factory = sqlite3.Row

    create_tables(conn)

    conn.execute(
        """
        INSERT INTO customers (
            id,
            name,
            email
        )
        VALUES (?, ?, ?)
        """,
        (
            "CUST-1001",
            "Test Customer",
            "customer@example.com",
        ),
    )

    conn.execute(
        """
        INSERT INTO vehicles (
            id,
            customer_id,
            vin,
            make,
            model,
            year
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "VEH-1001",
            "CUST-1001",
            "1HGCM82633A123456",
            "Honda",
            "City",
            2024,
        ),
    )

    conn.execute(
        """
        INSERT INTO policies (
            id,
            policy_number,
            customer_id,
            vehicle_id,
            type,
            status,
            start_date,
            end_date,
            premium,
            deductible
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "POL-ID-9001",
            "POL-9001",
            "CUST-1001",
            "VEH-1001",
            "Motor",
            "ACTIVE",
            "2026-01-01",
            "2026-12-31",
            25000.0,
            5000.0,
        ),
    )

    conn.commit()

    return conn


def build_services():
    conn = build_test_database()

    repo = InsuranceRepository(conn)

    verifier = PolicyVerifier(repo)

    creator = ClaimCreationService(repo)

    return (
        conn,
        repo,
        verifier,
        creator,
    )


def build_intake(
    description="Vehicle collision at traffic signal.",
):
    return ClaimIntake(
        policy_number="POL-9001",
        incident_date="2026-10-03",
        incident_description=description,
        damage_description="Rear bumper damaged.",
        claimed_amount=45000.0,
    )


def verify_valid_intake(
    verifier,
    intake,
):
    return verifier.verify(
        intake=intake,
        customer_id="CUST-1001",
    )


def test_creates_claim_after_successful_verification():
    (
        conn,
        repo,
        verifier,
        creator,
    ) = build_services()

    intake = build_intake()

    verification = verify_valid_intake(
        verifier,
        intake,
    )

    assert (
        verification.status
        == PolicyVerifier.VERIFIED
    )

    result = creator.create(
        intake=intake,
        verification=verification,
        customer_id="CUST-1001",
    )

    assert (
        result.status
        == ClaimCreationService.CREATED
    )

    assert result.created is True

    assert result.claim is not None

    assert (
        result.claim["policy_id"]
        == "POL-ID-9001"
    )

    assert (
        result.claim["customer_id"]
        == "CUST-1001"
    )

    assert (
        result.claim["incident_date"]
        == "2026-10-03"
    )

    assert (
        result.claim["description"]
        == "Vehicle collision at traffic signal."
    )

    assert (
        result.claim["damage_description"]
        == "Rear bumper damaged."
    )

    assert (
        result.claim["claimed_amount"]
        == 45000.0
    )

    assert (
        result.claim["status"]
        == "FNOL Received"
    )

    assert result.claim[
        "claim_number"
    ].startswith("CLM-")

    conn.close()


def test_does_not_create_claim_when_policy_verification_failed():
    (
        conn,
        repo,
        verifier,
        creator,
    ) = build_services()

    intake = build_intake()

    verification = verifier.verify(
        intake=intake,
        customer_id="CUST-9999",
    )

    assert (
        verification.status
        == PolicyVerifier.CUSTOMER_MISMATCH
    )

    result = creator.create(
        intake=intake,
        verification=verification,
        customer_id="CUST-9999",
    )

    assert (
        result.status
        == ClaimCreationService.VERIFICATION_FAILED
    )

    assert result.created is False

    count = conn.execute(
        "SELECT COUNT(*) FROM claims"
    ).fetchone()[0]

    assert count == 0

    conn.close()


def test_does_not_create_claim_when_intake_is_incomplete():
    (
        conn,
        repo,
        verifier,
        creator,
    ) = build_services()

    intake = ClaimIntake(
        policy_number="POL-9001",
        incident_date="2026-10-03",
        incident_description=None,
    )

    verification = PolicyVerificationResult(
        status=PolicyVerifier.VERIFIED,
        policy=repo.find_policy_by_number(
            "POL-9001"
        ),
        reason="Test verification.",
    )

    result = creator.create(
        intake=intake,
        verification=verification,
        customer_id="CUST-1001",
    )

    assert (
        result.status
        == ClaimCreationService.INCOMPLETE_INTAKE
    )

    assert result.created is False

    count = conn.execute(
        "SELECT COUNT(*) FROM claims"
    ).fetchone()[0]

    assert count == 0

    conn.close()


def test_does_not_create_claim_when_verified_policy_has_no_authoritative_record():
    (
        conn,
        repo,
        verifier,
        creator,
    ) = build_services()

    intake = build_intake()

    verification = PolicyVerificationResult(
        status=PolicyVerifier.VERIFIED,
        policy=None,
        reason="Invalid test verification.",
    )

    result = creator.create(
        intake=intake,
        verification=verification,
        customer_id="CUST-1001",
    )

    assert (
        result.status
        == ClaimCreationService.VERIFICATION_FAILED
    )

    assert result.created is False

    conn.close()


def test_does_not_create_claim_for_customer_mismatch_even_if_verification_object_is_tampered():
    (
        conn,
        repo,
        verifier,
        creator,
    ) = build_services()

    intake = build_intake()

    policy = repo.find_policy_by_number(
        "POL-9001"
    )

    verification = PolicyVerificationResult(
        status=PolicyVerifier.VERIFIED,
        policy=policy,
        reason="Tampered test result.",
    )

    result = creator.create(
        intake=intake,
        verification=verification,
        customer_id="CUST-9999",
    )

    assert (
        result.status
        == ClaimCreationService.VERIFICATION_FAILED
    )

    assert result.created is False

    conn.close()


def test_duplicate_claim_is_not_created():
    (
        conn,
        repo,
        verifier,
        creator,
    ) = build_services()

    intake = build_intake()

    verification = verify_valid_intake(
        verifier,
        intake,
    )

    first = creator.create(
        intake=intake,
        verification=verification,
        customer_id="CUST-1001",
    )

    assert (
        first.status
        == ClaimCreationService.CREATED
    )

    second = creator.create(
        intake=intake,
        verification=verification,
        customer_id="CUST-1001",
    )

    assert (
        second.status
        == ClaimCreationService.DUPLICATE_CLAIM
    )

    assert second.created is False

    assert second.claim is not None

    count = conn.execute(
        "SELECT COUNT(*) FROM claims"
    ).fetchone()[0]

    assert count == 1

    conn.close()


def test_different_incident_description_can_create_separate_claim():
    (
        conn,
        repo,
        verifier,
        creator,
    ) = build_services()

    first_intake = build_intake(
        "Vehicle collision at traffic signal."
    )

    verification = verify_valid_intake(
        verifier,
        first_intake,
    )

    first = creator.create(
        intake=first_intake,
        verification=verification,
        customer_id="CUST-1001",
    )

    assert (
        first.status
        == ClaimCreationService.CREATED
    )

    second_intake = build_intake(
        "Vehicle damaged while parked outside the office."
    )

    second = creator.create(
        intake=second_intake,
        verification=verification,
        customer_id="CUST-1001",
    )

    assert (
        second.status
        == ClaimCreationService.CREATED
    )

    count = conn.execute(
        "SELECT COUNT(*) FROM claims"
    ).fetchone()[0]

    assert count == 2

    conn.close()