from pathlib import Path

from src.ai.claim_intake import ClaimIntake
from src.ai.policy_verifier import PolicyVerifier
from src.db.database import Database
from src.db.repository import InsuranceRepository


def make_repository(tmp_path: Path):
    db_path = tmp_path / "test.db"

    # Database initializes the schema automatically.
    database = Database(db_path)

    # Repository operates on an existing database connection.
    connection = database.get_connection()

    return InsuranceRepository(connection)


def insert_customer(repository, customer_id="CUST-001"):
    repository.conn.execute(
        """
        INSERT INTO customers (
            id,
            name,
            email,
            phone
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            customer_id,
            "Test Customer",
            "test@example.com",
            "9999999999",
        ),
    )

    repository.conn.commit()


def insert_vehicle(
    repository,
    vehicle_id="VEH-001",
    customer_id="CUST-001",
):
    repository.conn.execute(
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
            vehicle_id,
            customer_id,
            "TESTVIN12345678901",
            "Test",
            "Vehicle",
            2025,
        ),
    )

    repository.conn.commit()


def insert_policy(
    repository,
    policy_id="POL-ID-001",
    policy_number="POL-TEST-001",
    customer_id="CUST-001",
    vehicle_id="VEH-001",
    status="ACTIVE",
    start_date="2026-01-01",
    end_date="2026-12-31",
):
    repository.conn.execute(
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
            deductible,
            coverage_limit,
            no_claim_bonus_pct
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            policy_id,
            policy_number,
            customer_id,
            vehicle_id,
            "Motor",
            "Comprehensive",
            status,
            start_date,
            end_date,
            10000.0,
            5000.0,
            500000.0,
            0.0,
        ),
    )

    repository.conn.commit()


def make_intake(
    policy_number="POL-TEST-001",
    incident_date="2026-06-15",
):
    return ClaimIntake(
        policy_number=policy_number,
        incident_date=incident_date,
        incident_description="Vehicle damaged in accident.",
    )


def setup_active_policy(
    tmp_path,
    *,
    status="ACTIVE",
    start_date="2026-01-01",
    end_date="2026-12-31",
):
    repository = make_repository(tmp_path)

    insert_customer(repository)
    insert_vehicle(repository)

    insert_policy(
        repository,
        status=status,
        start_date=start_date,
        end_date=end_date,
    )

    return repository


def test_active_policy_with_incident_inside_period_is_verified(tmp_path):
    repository = setup_active_policy(tmp_path)

    verifier = PolicyVerifier(repository)

    result = verifier.verify(
        make_intake(),
        customer_id="CUST-001",
    )

    assert result.status == PolicyVerifier.VERIFIED
    assert result.verified is True
    assert result.policy["policy_number"] == "POL-TEST-001"


def test_inactive_policy_is_rejected(tmp_path):
    repository = setup_active_policy(
        tmp_path,
        status="EXPIRED",
    )

    verifier = PolicyVerifier(repository)

    result = verifier.verify(
        make_intake(),
        customer_id="CUST-001",
    )

    assert result.status == PolicyVerifier.POLICY_INACTIVE
    assert result.verified is False


def test_incident_before_policy_start_is_rejected(tmp_path):
    repository = setup_active_policy(
        tmp_path,
        start_date="2026-02-01",
    )

    verifier = PolicyVerifier(repository)

    result = verifier.verify(
        make_intake(
            incident_date="2026-01-31",
        ),
        customer_id="CUST-001",
    )

    assert result.status == (
        PolicyVerifier.INCIDENT_OUTSIDE_POLICY_PERIOD
    )
    assert result.verified is False


def test_incident_after_policy_end_is_rejected(tmp_path):
    repository = setup_active_policy(
        tmp_path,
        end_date="2026-06-30",
    )

    verifier = PolicyVerifier(repository)

    result = verifier.verify(
        make_intake(
            incident_date="2026-07-01",
        ),
        customer_id="CUST-001",
    )

    assert result.status == (
        PolicyVerifier.INCIDENT_OUTSIDE_POLICY_PERIOD
    )
    assert result.verified is False


def test_invalid_incident_date_is_rejected(tmp_path):
    repository = setup_active_policy(tmp_path)

    verifier = PolicyVerifier(repository)

    result = verifier.verify(
        make_intake(
            incident_date="2026-99-99",
        ),
        customer_id="CUST-001",
    )

    assert result.status == PolicyVerifier.INVALID_INCIDENT_DATE
    assert result.verified is False


def test_ambiguous_non_iso_incident_date_is_rejected(tmp_path):
    repository = setup_active_policy(tmp_path)

    verifier = PolicyVerifier(repository)

    result = verifier.verify(
        make_intake(
            incident_date="03/10/2026",
        ),
        customer_id="CUST-001",
    )

    assert result.status == PolicyVerifier.INVALID_INCIDENT_DATE
    assert result.verified is False


def test_incident_on_policy_start_date_is_verified(tmp_path):
    repository = setup_active_policy(
        tmp_path,
        start_date="2026-01-01",
    )

    verifier = PolicyVerifier(repository)

    result = verifier.verify(
        make_intake(
            incident_date="2026-01-01",
        ),
        customer_id="CUST-001",
    )

    assert result.status == PolicyVerifier.VERIFIED
    assert result.verified is True


def test_incident_on_policy_end_date_is_verified(tmp_path):
    repository = setup_active_policy(
        tmp_path,
        end_date="2026-12-31",
    )

    verifier = PolicyVerifier(repository)

    result = verifier.verify(
        make_intake(
            incident_date="2026-12-31",
        ),
        customer_id="CUST-001",
    )

    assert result.status == PolicyVerifier.VERIFIED
    assert result.verified is True