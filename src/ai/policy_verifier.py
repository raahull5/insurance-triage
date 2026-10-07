from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Optional

from src.ai.claim_intake import ClaimIntake
from src.db.repository import InsuranceRepository


@dataclass
class PolicyVerificationResult:
    """
    Result of deterministic verification of an extracted policy number.

    This result is authoritative only with respect to the database checks
    performed here. It does not create or modify a policy or claim.
    """

    status: str
    policy: Optional[Dict[str, Any]] = None
    reason: str = ""

    @property
    def verified(self) -> bool:
        """Whether the policy passed all required verification checks."""
        return self.status == "VERIFIED"


class PolicyVerifier:
    """
    Verify that an extracted policy belongs to the identified customer
    and is eligible for the reported incident.

    The policy number comes from ClaimIntake, which is extracted from
    untrusted email content. It must therefore be checked against the
    authoritative database before any claim creation is allowed.
    """

    VERIFIED = "VERIFIED"
    MISSING_POLICY = "MISSING_POLICY"
    POLICY_NOT_FOUND = "POLICY_NOT_FOUND"
    CUSTOMER_MISMATCH = "CUSTOMER_MISMATCH"
    POLICY_INACTIVE = "POLICY_INACTIVE"
    INCIDENT_OUTSIDE_POLICY_PERIOD = "INCIDENT_OUTSIDE_POLICY_PERIOD"
    INVALID_INCIDENT_DATE = "INVALID_INCIDENT_DATE"

    def __init__(self, repository: InsuranceRepository):
        self.repository = repository

    def verify(
        self,
        intake: ClaimIntake,
        customer_id: Optional[str],
    ) -> PolicyVerificationResult:
        """
        Verify the policy referenced by a ClaimIntake.

        Required checks:
        1. A policy number must have been extracted.
        2. The policy must exist in the database.
        3. A customer must have been identified.
        4. The policy must belong to that customer.
        5. The incident date must use unambiguous ISO format.
        6. The policy must be ACTIVE.
        7. The incident must fall within the policy period.
        """

        # ---------------------------------------------------------
        # 1. Policy number must exist
        # ---------------------------------------------------------
        if not intake.policy_number:
            return PolicyVerificationResult(
                status=self.MISSING_POLICY,
                reason="No policy number was extracted from the FNOL.",
            )

        # ---------------------------------------------------------
        # 2. Policy must exist in authoritative database
        # ---------------------------------------------------------
        policy = self.repository.find_policy_by_number(
            intake.policy_number
        )

        if not policy:
            return PolicyVerificationResult(
                status=self.POLICY_NOT_FOUND,
                reason=(
                    f"Policy {intake.policy_number} was not found "
                    "in the authoritative policy database."
                ),
            )

        # ---------------------------------------------------------
        # 3. Customer must be identified
        # ---------------------------------------------------------
        if not customer_id:
            return PolicyVerificationResult(
                status=self.CUSTOMER_MISMATCH,
                policy=policy,
                reason=(
                    "The policy exists, but the customer could not "
                    "be identified from the incoming message."
                ),
            )

        # ---------------------------------------------------------
        # 4. Policy must belong to identified customer
        # ---------------------------------------------------------
        if str(policy.get("customer_id")) != str(customer_id):
            return PolicyVerificationResult(
                status=self.CUSTOMER_MISMATCH,
                policy=policy,
                reason=(
                    f"Policy {intake.policy_number} does not belong "
                    f"to customer {customer_id}."
                ),
            )

        # ---------------------------------------------------------
        # 5. Incident date must be unambiguous ISO format
        # ---------------------------------------------------------
        incident_date = self._parse_iso_date(
            intake.incident_date
        )

        if incident_date is None:
            return PolicyVerificationResult(
                status=self.INVALID_INCIDENT_DATE,
                policy=policy,
                reason=(
                    "The incident date must use unambiguous "
                    "YYYY-MM-DD format."
                ),
            )

        # ---------------------------------------------------------
        # 6. Policy must be active
        # ---------------------------------------------------------
        policy_status = str(policy.get("status", "")).strip().upper()

        if policy_status != "ACTIVE":
            return PolicyVerificationResult(
                status=self.POLICY_INACTIVE,
                policy=policy,
                reason=(
                    f"Policy {intake.policy_number} is not active. "
                    f"Current status: {policy.get('status')}."
                ),
            )

        # ---------------------------------------------------------
        # 7. Incident must fall within policy period
        # ---------------------------------------------------------
        policy_start = self._parse_database_date(
            policy.get("start_date")
        )
        policy_end = self._parse_database_date(
            policy.get("end_date")
        )

        if policy_start is None or policy_end is None:
            return PolicyVerificationResult(
                status=self.INVALID_INCIDENT_DATE,
                policy=policy,
                reason=(
                    "The authoritative policy record contains "
                    "an invalid coverage period."
                ),
            )

        if incident_date < policy_start or incident_date > policy_end:
            return PolicyVerificationResult(
                status=self.INCIDENT_OUTSIDE_POLICY_PERIOD,
                policy=policy,
                reason=(
                    f"Incident date {incident_date.isoformat()} is "
                    f"outside the policy period "
                    f"{policy_start.isoformat()} to "
                    f"{policy_end.isoformat()}."
                ),
            )

        # ---------------------------------------------------------
        # All verification checks passed
        # ---------------------------------------------------------
        return PolicyVerificationResult(
            status=self.VERIFIED,
            policy=policy,
            reason=(
                "Policy exists, belongs to the identified customer, "
                "is active, and covers the incident date."
            ),
        )

    @staticmethod
    def _parse_iso_date(value: Optional[str]) -> Optional[date]:
        """
        Parse only YYYY-MM-DD.

        Deliberately rejects DD/MM/YYYY and DD-MM-YYYY because those
        formats can be ambiguous and should not be used for an
        authorization decision.
        """
        if not value:
            return None

        value = value.strip()

        if len(value) != 10:
            return None

        if value[4] != "-" or value[7] != "-":
            return None

        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return None

        if parsed.isoformat() != value:
            return None

        return parsed

    @staticmethod
    def _parse_database_date(value: Any) -> Optional[date]:
        """Parse an authoritative database date."""
        if value is None:
            return None

        if isinstance(value, date):
            return value

        value = str(value).strip()

        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None