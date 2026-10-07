import pytest

from src.ai.claim_intake_extractor import ClaimIntakeExtractor


def test_extract_policy_number():
    intake = ClaimIntakeExtractor.extract(
        subject="New motor insurance claim",
        body="Policy reference: POL-TEST-003",
    )

    assert intake.policy_number == "POL-TEST-003"


def test_extract_incident_date():
    intake = ClaimIntakeExtractor.extract(
        subject="New motor insurance claim",
        body="Incident date: 2026-09-27",
    )

    assert intake.incident_date == "2026-09-27"


def test_extract_incident_description():
    intake = ClaimIntakeExtractor.extract(
        subject="New motor insurance claim",
        body="Incident: Minor road accident involving my vehicle.",
    )

    assert intake.incident_description == (
        "Minor road accident involving my vehicle."
    )


def test_extract_damage_description():
    intake = ClaimIntakeExtractor.extract(
        subject="New motor insurance claim",
        body="Damage: Front bumper and headlight damaged.",
    )

    assert intake.damage_description == (
        "Front bumper and headlight damaged."
    )


def test_extract_claimed_amount():
    intake = ClaimIntakeExtractor.extract(
        subject="New motor insurance claim",
        body="Claimed amount: INR 45000",
    )

    assert intake.claimed_amount == 45000.0


def test_extract_vin():
    intake = ClaimIntakeExtractor.extract(
        subject="New motor insurance claim",
        body="VIN: 1HGCM82633A123456",
    )

    assert intake.vehicle_vin == "1HGCM82633A123456"


def test_extract_complete_claim():
    intake = ClaimIntakeExtractor.extract(
        subject="New motor insurance claim",
        body=(
            "Policy reference: POL-TEST-003\n"
            "Incident date: 2026-09-27\n"
            "Incident: Minor road accident involving my vehicle.\n"
            "Damage: Front bumper and headlight damaged.\n"
            "Claimed amount: INR 45000\n"
            "VIN: 1HGCM82633A123456"
        ),
    )

    assert intake.policy_number == "POL-TEST-003"
    assert intake.incident_date == "2026-09-27"
    assert intake.incident_description == (
        "Minor road accident involving my vehicle."
    )
    assert intake.damage_description == (
        "Front bumper and headlight damaged."
    )
    assert intake.claimed_amount == 45000.0
    assert intake.vehicle_vin == "1HGCM82633A123456"


def test_extract_empty_email():
    intake = ClaimIntakeExtractor.extract(
        subject=None,
        body=None,
    )

    assert intake.policy_number is None
    assert intake.incident_date is None
    assert intake.incident_description is None
    assert intake.damage_description is None
    assert intake.claimed_amount is None
    assert intake.vehicle_vin is None


def test_extract_policy_reference_pol_format():
    intake = ClaimIntakeExtractor.extract(
        subject="New motor insurance claim",
        body="Policy reference: POL-TEST-003",
    )

    assert intake.policy_number == "POL-TEST-003"


def test_extract_policy_reference_test_policy_format():
    intake = ClaimIntakeExtractor.extract(
        subject="New motor insurance claim",
        body="Policy reference: TEST-POLICY-003",
    )

    assert intake.policy_number == "TEST-POLICY-003"


def test_policy_label_is_not_extracted_as_policy_number():
    intake = ClaimIntakeExtractor.extract(
        subject="New motor insurance claim",
        body=(
            "Policy reference: POL-TEST-003\n"
            "Please process this claim."
        ),
    )

    assert intake.policy_number != "POLICY"