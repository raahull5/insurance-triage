from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ClaimIntake:
    """Structured, non-authoritative data extracted from a new-claim message."""

    policy_number: Optional[str] = None
    incident_date: Optional[str] = None
    incident_description: Optional[str] = None
    damage_description: Optional[str] = None
    claimed_amount: Optional[float] = None
    vehicle_vin: Optional[str] = None

    confidence: float = 0.0
    missing_fields: List[str] = field(default_factory=list)

    def validate(self) -> List[str]:
        """Return required fields that are missing or unusable."""
        missing = []

        if not self.policy_number or not self.policy_number.strip():
            missing.append("policy_number")

        if not self.incident_date or not self.incident_date.strip():
            missing.append("incident_date")

        if not self.incident_description or not self.incident_description.strip():
            missing.append("incident_description")

        self.missing_fields = missing
        return list(missing)

    @property
    def is_complete(self) -> bool:
        """Whether the minimum FNOL information is present."""
        return not self.validate()
