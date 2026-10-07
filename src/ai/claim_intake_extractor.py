import re
from typing import Optional

from src.ai.claim_intake import ClaimIntake


class ClaimIntakeExtractor:
    """Extract basic FNOL fields from an email without creating a claim."""

    # Policy numbers should be extracted from an explicitly labelled
    # policy reference/number whenever possible.
    #
    # Examples supported:
    #   Policy reference: POL-TEST-003
    #   Policy reference: TEST-POLICY-003
    #   Policy number: POL-ABC-123
    #
    # The explicit label prevents ordinary words such as "POLICY"
    # from being mistaken for a policy number.
    POLICY_REFERENCE_PATTERN = re.compile(
        r"(?im)^\s*Policy\s*(?:Reference|Number|No\.?)"
        r"\s*:\s*([A-Z0-9]+(?:-[A-Z0-9]+)+)\s*$"
    )

    # Fallback for conventional policy numbers appearing elsewhere
    # in the message.
    POLICY_FALLBACK_PATTERN = re.compile(
        r"(?<![A-Z0-9])"
        r"(POL-[A-Z0-9]+(?:-[A-Z0-9]+)*)"
        r"(?![A-Z0-9])",
        re.IGNORECASE,
    )

    DATE_PATTERNS = (
        re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
        re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{4})\b"),
    )

    AMOUNT_PATTERN = re.compile(
        r"(?:INR|RS|Rupees)\s*[:\-]?\s*"
        r"([0-9][0-9,]*(?:\.[0-9]+)?)",
        re.IGNORECASE,
    )

    @classmethod
    def extract(
        cls,
        subject: Optional[str],
        body: Optional[str],
    ) -> ClaimIntake:
        text = f"{subject or ''}\n{body or ''}".strip()

        return ClaimIntake(
            policy_number=cls._extract_policy_number(text),
            incident_date=cls._extract_incident_date(text),
            incident_description=cls._extract_incident_description(text),
            damage_description=cls._extract_damage_description(text),
            claimed_amount=cls._extract_amount(text),
            vehicle_vin=cls._extract_vin(text),
        )

    @classmethod
    def _extract_policy_number(
        cls,
        text: str,
    ) -> Optional[str]:
        # ----------------------------------------------------------
        # 1. Prefer explicitly labelled policy reference/number.
        # ----------------------------------------------------------

        match = cls.POLICY_REFERENCE_PATTERN.search(text)

        if match:
            return match.group(1).upper()

        # ----------------------------------------------------------
        # 2. Fallback to a conventional POL-XXXX policy number.
        # ----------------------------------------------------------

        match = cls.POLICY_FALLBACK_PATTERN.search(text)

        if match:
            return match.group(1).upper()

        return None

    @classmethod
    def _extract_incident_date(
        cls,
        text: str,
    ) -> Optional[str]:
        for pattern in cls.DATE_PATTERNS:
            match = pattern.search(text)

            if match:
                return match.group(1)

        return None

    @classmethod
    def _extract_amount(
        cls,
        text: str,
    ) -> Optional[float]:
        match = cls.AMOUNT_PATTERN.search(text)

        if not match:
            return None

        try:
            return float(
                match.group(1).replace(",", "")
            )
        except ValueError:
            return None

    @staticmethod
    def _extract_incident_description(
        text: str,
    ) -> Optional[str]:
        patterns = (
            # Explicit "Incident:" field.
            r"(?im)^\s*Incident\s*:\s*(.+?)\s*$",

            # Explicit "Accident:" field.
            r"(?im)^\s*Accident\s*:\s*(.+?)\s*$",

            # Explicit "Accident Description:" field.
            r"(?im)^\s*Accident\s+Description\s*:\s*(.+?)\s*$",
        )

        for pattern in patterns:
            match = re.search(pattern, text)

            if match:
                return match.group(1).strip()

        return None

    @staticmethod
    def _extract_damage_description(
        text: str,
    ) -> Optional[str]:
        match = re.search(
            r"(?im)^\s*(?:Damage|Damages)\s*:\s*(.+?)\s*$",
            text,
        )

        if match:
            return match.group(1).strip()

        return None

    @staticmethod
    def _extract_vin(
        text: str,
    ) -> Optional[str]:
        match = re.search(
            r"(?i)\bVIN\s*[:\-]?\s*"
            r"([A-HJ-NPR-Z0-9]{17})\b",
            text,
        )

        if match:
            return match.group(1).upper()

        return None