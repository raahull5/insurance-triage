from src.ai.claim_intake import ClaimIntake


def test_complete_claim_intake():
    intake = ClaimIntake(
        policy_number="POL-9001",
        incident_date="2026-10-03",
        incident_description="Rear-ended at a traffic signal.",
    )

    assert intake.validate() == []
    assert intake.is_complete is True


def test_claimed_amount_is_optional():
    intake = ClaimIntake(
        policy_number="POL-9001",
        incident_date="2026-10-03",
        incident_description="Vehicle collision.",
    )

    assert intake.is_complete is True
    assert intake.claimed_amount is None


def test_missing_required_fields():
    intake = ClaimIntake(
        policy_number="POL-9001",
    )

    assert intake.validate() == [
        "incident_date",
        "incident_description",
    ]
    assert intake.is_complete is False


def test_blank_required_fields_are_missing():
    intake = ClaimIntake(
        policy_number=" ",
        incident_date="",
        incident_description="  ",
    )

    assert intake.validate() == [
        "policy_number",
        "incident_date",
        "incident_description",
    ]
