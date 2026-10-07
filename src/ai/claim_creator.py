from dataclasses import dataclass
from datetime import date
import uuid
from typing import Optional

from src.ai.claim_intake import ClaimIntake
from src.ai.policy_verifier import PolicyVerificationResult
from src.db.repository import InsuranceRepository


@dataclass
class ClaimCreationResult:
    """Result of attempting to create an official insurance claim."""

    status: str
    claim: Optional[dict] = None
    reason: str = ""

    @property
    def created(self) -> bool:
        """Whether a new claim was successfully created."""
        return self.status == "CREATED"


class ClaimCreationService:
    """
    Create an official claim only after deterministic authorization.

    Raw email/LLM extraction is never sufficient authorization.
    """

    CREATED = "CREATED"
    INCOMPLETE_INTAKE = "INCOMPLETE_INTAKE"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    DUPLICATE_CLAIM = "DUPLICATE_CLAIM"
    CREATION_FAILED = "CREATION_FAILED"

    INITIAL_STATUS = "FNOL Received"

    def __init__(
        self,
        repository: InsuranceRepository,
    ):
        self.repository = repository

    def create(
        self,
        intake: ClaimIntake,
        verification: PolicyVerificationResult,
        customer_id: Optional[str],
    ) -> ClaimCreationResult:
        """
        Create an official claim from a verified ClaimIntake.

        Required conditions:

        1. Intake must be complete.
        2. Policy verification must be VERIFIED.
        3. Verification must contain the authoritative policy.
        4. Customer must match the verified policy owner.
        5. The policy must have an authoritative ID.
        6. No matching claim may already exist.
        """

        # ----------------------------------------------------------
        # 1. Validate the extracted FNOL
        # ----------------------------------------------------------

        if not intake.is_complete:
            return ClaimCreationResult(
                status=self.INCOMPLETE_INTAKE,
                reason=(
                    "The FNOL is incomplete. Required fields are "
                    f"missing: {', '.join(intake.missing_fields)}."
                ),
            )

        # ----------------------------------------------------------
        # 2. Policy verification must have succeeded
        # ----------------------------------------------------------

        if not verification.verified:
            return ClaimCreationResult(
                status=self.VERIFICATION_FAILED,
                reason=(
                    "Claim creation requires a VERIFIED policy. "
                    f"Verification status: {verification.status}."
                ),
            )

        # ----------------------------------------------------------
        # 3. Authoritative policy record must be present
        # ----------------------------------------------------------

        policy = verification.policy

        if not policy:
            return ClaimCreationResult(
                status=self.VERIFICATION_FAILED,
                reason=(
                    "Policy verification was marked successful "
                    "but did not provide the authoritative policy "
                    "record."
                ),
            )

        # ----------------------------------------------------------
        # 4. Customer must be identified
        # ----------------------------------------------------------

        if not customer_id:
            return ClaimCreationResult(
                status=self.VERIFICATION_FAILED,
                reason=(
                    "Claim creation requires an identified customer."
                ),
            )

        # ----------------------------------------------------------
        # 5. Customer must own the verified policy
        # ----------------------------------------------------------

        authoritative_customer_id = policy.get(
            "customer_id"
        )

        if str(authoritative_customer_id) != str(
            customer_id
        ):
            return ClaimCreationResult(
                status=self.VERIFICATION_FAILED,
                reason=(
                    "The identified customer does not match "
                    "the verified policy owner."
                ),
            )

        # ----------------------------------------------------------
        # 6. Policy must have an authoritative internal ID
        # ----------------------------------------------------------

        policy_id = policy.get("id")

        if not policy_id:
            return ClaimCreationResult(
                status=self.VERIFICATION_FAILED,
                reason=(
                    "The verified policy does not contain "
                    "an authoritative policy ID."
                ),
            )

        # ----------------------------------------------------------
        # 7. Defensive duplicate check
        # ----------------------------------------------------------

        existing_claim = (
            self.repository.find_claim_by_policy_and_incident(
                policy_id=str(policy_id),
                customer_id=str(
                    authoritative_customer_id
                ),
                incident_date=intake.incident_date,
                description=intake.incident_description,
            )
        )

        if existing_claim:
            return ClaimCreationResult(
                status=self.DUPLICATE_CLAIM,
                claim=existing_claim,
                reason=(
                    "An existing claim already matches this "
                    "policy, customer, incident date, and "
                    "incident description."
                ),
            )

        # ----------------------------------------------------------
        # 8. Generate identifiers
        # ----------------------------------------------------------

        claim_id = (
            f"CLM-ID-"
            f"{uuid.uuid4().hex[:12].upper()}"
        )

        claim_number = (
            self._generate_claim_number()
        )

        reported_date = date.today().isoformat()

        # ----------------------------------------------------------
        # 9. Create official claim
        # ----------------------------------------------------------

        try:
            self.repository.create_claim(
                claim_id=claim_id,
                claim_number=claim_number,
                policy_id=str(policy_id),
                customer_id=str(
                    authoritative_customer_id
                ),
                incident_date=intake.incident_date,
                reported_date=reported_date,
                status=self.INITIAL_STATUS,
                description=(
                    intake.incident_description
                ),
                damage_description=(
                    intake.damage_description
                ),
                claimed_amount=(
                    intake.claimed_amount
                ),
            )

        except Exception:
            # Infrastructure/database failures must reach the outer
            # AutonomousPipeline resilience layer. It is responsible for
            # transient-error classification, retry handling, and ensuring
            # the Gmail watermark is not advanced.
            raise

        # ----------------------------------------------------------
        # 10. Re-read the authoritative claim
        # ----------------------------------------------------------

        created_claim = (
            self.repository.find_claim_by_number(
                claim_number
            )
        )

        if not created_claim:
            return ClaimCreationResult(
                status=self.CREATION_FAILED,
                reason=(
                    "The claim insert completed but "
                    "the created claim could not be retrieved."
                ),
            )

        return ClaimCreationResult(
            status=self.CREATED,
            claim=created_claim,
            reason="Official claim created successfully.",
        )

    def _generate_claim_number(self) -> str:
        """
        Generate a human-readable claim number.

        Existing project claim numbers use:

            CLM-YYYY-NNNNN
        """

        year = date.today().year

        cur = self.repository.conn.cursor()

        cur.execute(
            """
            SELECT claim_number
            FROM claims
            WHERE claim_number LIKE ?
            ORDER BY claim_number DESC
            LIMIT 1
            """,
            (f"CLM-{year}-%",),
        )

        row = cur.fetchone()

        if not row:
            sequence = 1
        else:
            existing = str(row[0])

            try:
                sequence = (
                    int(
                        existing.rsplit(
                            "-",
                            1,
                        )[1]
                    )
                    + 1
                )
            except (
                ValueError,
                IndexError,
            ):
                sequence = 1

        return (
            f"CLM-{year}-{sequence:05d}"
        )