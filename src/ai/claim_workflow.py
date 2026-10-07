from dataclasses import dataclass
from typing import Optional

from src.ai.claim_intake import ClaimIntake
from src.ai.claim_intake_extractor import ClaimIntakeExtractor
from src.ai.claim_creator import (
    ClaimCreationResult,
    ClaimCreationService,
)
from src.ai.policy_verifier import (
    PolicyVerificationResult,
    PolicyVerifier,
)
from src.db.repository import InsuranceRepository


@dataclass
class ClaimWorkflowResult:
    """
    Result of processing a NEW_CLAIM email through the
    deterministic claim workflow.
    """

    status: str
    intake: ClaimIntake
    verification: PolicyVerificationResult
    creation: Optional[ClaimCreationResult] = None
    reason: str = ""

    HUMAN_REVIEW = "HUMAN_REVIEW"
    CREATED = "CREATED"
    DUPLICATE_CLAIM = "DUPLICATE_CLAIM"

    @property
    def human_review_required(self) -> bool:
        """Whether this workflow result requires human review."""
        return self.status == self.HUMAN_REVIEW

    @property
    def claim(self) -> Optional[dict]:
        """Return the authoritative claim record when available."""
        if self.creation:
            return self.creation.claim

        return None


class ClaimWorkflow:
    """
    Orchestrate the deterministic NEW_CLAIM workflow.

    The workflow deliberately separates:

        1. extraction from untrusted email content
        2. validation of the extracted FNOL
        3. customer identity verification
        4. policy verification against the authoritative database
        5. official claim creation

    A NEW_CLAIM classification alone never creates a claim.
    """

    HUMAN_REVIEW = "HUMAN_REVIEW"
    CREATED = "CREATED"
    DUPLICATE_CLAIM = "DUPLICATE_CLAIM"

    def __init__(
        self,
        repository: InsuranceRepository,
    ):
        self.repository = repository

        self.extractor = ClaimIntakeExtractor()

        self.policy_verifier = PolicyVerifier(
            repository
        )

        self.claim_creator = ClaimCreationService(
            repository
        )

    def process(
        self,
        subject: Optional[str],
        body: Optional[str],
        customer_id: Optional[str],
    ) -> ClaimWorkflowResult:
        """
        Process one NEW_CLAIM message.

        No official claim is created unless:

        - required FNOL information is present
        - customer identity is available
        - policy exists
        - policy belongs to the customer
        - policy is active
        - incident date is valid
        - incident date falls within the policy period
        """

        # ==============================================================
        # STEP 1
        # Extract FNOL information from the incoming email.
        #
        # The email is untrusted input.
        # Nothing extracted here is authoritative.
        # ==============================================================

        intake = self.extractor.extract(
            subject=subject,
            body=body,
        )

        # ==============================================================
        # STEP 2
        # Validate the minimum FNOL information.
        #
        # Required:
        #   - policy number
        #   - incident date
        #   - incident description
        #
        # Do not attempt policy verification when the FNOL itself
        # is incomplete.
        # ==============================================================

        if not intake.is_complete:
            return ClaimWorkflowResult(
                status=self.HUMAN_REVIEW,
                intake=intake,
                verification=PolicyVerificationResult(
                    status=PolicyVerifier.MISSING_POLICY,
                    reason=(
                        "FNOL is incomplete; "
                        "policy verification was not attempted."
                    ),
                ),
                reason=(
                    "FNOL is incomplete. Required fields missing: "
                    f"{', '.join(intake.missing_fields)}."
                ),
            )

        # ==============================================================
        # STEP 3
        # Customer identity is mandatory.
        #
        # A policy number extracted from an email cannot be trusted
        # until it has been associated with an authoritative customer.
        # ==============================================================

        if not customer_id:
            return ClaimWorkflowResult(
                status=self.HUMAN_REVIEW,
                intake=intake,
                verification=PolicyVerificationResult(
                    status=PolicyVerifier.CUSTOMER_MISMATCH,
                    reason=(
                        "No authoritative customer identity was "
                        "available for this incoming NEW_CLAIM."
                    ),
                ),
                reason=(
                    "Customer identity could not be established. "
                    "Human verification is required before "
                    "claim creation."
                ),
            )

        # ==============================================================
        # STEP 4
        # Verify the extracted policy against the authoritative
        # policy database.
        # ==============================================================

        verification = self.policy_verifier.verify(
            intake=intake,
            customer_id=customer_id,
        )

        if not verification.verified:
            return ClaimWorkflowResult(
                status=self.HUMAN_REVIEW,
                intake=intake,
                verification=verification,
                reason=verification.reason,
            )

        # ==============================================================
        # STEP 5
        # Create the official claim.
        #
        # ClaimCreationService receives the authoritative policy
        # returned by PolicyVerifier.
        #
        # The raw policy number extracted from the email is therefore
        # never sufficient by itself to create a claim.
        # ==============================================================

        creation = self.claim_creator.create(
            intake=intake,
            verification=verification,
            customer_id=customer_id,
        )

        # ==============================================================
        # STEP 6
        # Successful claim creation.
        # ==============================================================

        if creation.created:
            return ClaimWorkflowResult(
                status=self.CREATED,
                intake=intake,
                verification=verification,
                creation=creation,
                reason=creation.reason,
            )

        # ==============================================================
        # STEP 7
        # Duplicate claim.
        #
        # Do not create another official claim.
        # ==============================================================

        if creation.status == ClaimCreationService.DUPLICATE_CLAIM:
            return ClaimWorkflowResult(
                status=self.DUPLICATE_CLAIM,
                intake=intake,
                verification=verification,
                creation=creation,
                reason=creation.reason,
            )

        # ==============================================================
        # STEP 8
        # Any other creation failure goes to human review.
        # ==============================================================

        return ClaimWorkflowResult(
            status=self.HUMAN_REVIEW,
            intake=intake,
            verification=verification,
            creation=creation,
            reason=creation.reason,
        )