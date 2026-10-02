
"""Human Review Escalation Engine for high-risk, sensitive, and complex insurance inquiries."""

import re
from typing import Dict, Any, List
from dataclasses import dataclass, field

from src.ai.intents import Priority


@dataclass
class EscalationDecision:
    """Detailed escalation decision containing triggers, routing, and suppression flags."""

    human_review_required: bool
    priority: str
    reasons: List[str] = field(default_factory=list)
    routed_to: str = "General Customer Support"
    suppress_auto_reply: bool = False
    send_acknowledgement_only: bool = False
    internal_routing_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "human_review_required": self.human_review_required,
            "priority": self.priority,
            "reasons": self.reasons,
            "routed_to": self.routed_to,
            "suppress_auto_reply": self.suppress_auto_reply,
            "send_acknowledgement_only": self.send_acknowledgement_only,
            "internal_routing_note": self.internal_routing_note,
        }


class EscalationEngine:
    """Evaluates multi-factor escalation triggers to protect policyholders and prevent unauthorized automation."""

    HIGH_VALUE_THRESHOLD: float = 5000.0

    LEGAL_PATTERNS = [
        r"\blawyer\b",
        r"\battorney\b",
        r"\bsue\b",
        r"\blawsuit\b",
        r"\blitigation\b",
        r"\blegal counsel\b",
        r"\blegal action\b",
        r"\bretaining counsel\b",
        r"\bcourt\b",
    ]

    REGULATORY_PATTERNS = [
        r"\binsurance commissioner\b",
        r"\bombudsman\b",
        r"\bdepartment of insurance\b",
        r"\bdoi complaint\b",
        r"\bconsumer protection\b",
        r"\bcfpb\b",
        r"\bstate regulator\b",
        r"\bbetter business bureau\b",
        r"\bbbb complaint\b",
    ]

    FRAUD_PATTERNS = [
        r"\bfraud\b",
        r"\bstolen identity\b",
        r"\bidentity theft\b",
        r"\bunauthorized policy\b",
        r"\bforged signature\b",
        r"\bfake claim\b",
        r"\bimpersonation\b",
        r"\bsuspicious transaction\b",
    ]

    CANCELLATION_PATTERNS = [
        r"\bcancel policy\b",
        r"\bcancel my insurance\b",
        r"\bterminate coverage\b",
        r"\bcancellation request\b",
        r"\bstop my policy\b",
    ]

    FORMAL_COMPLAINT_PATTERNS = [
        r"\bformally complaining\b",
        r"\bformal complaint\b",
        r"\blodging a formal complaint\b",
        r"\bsubmit a formal complaint\b",
        r"\bregister a formal complaint\b",
        r"\blodge a complaint\b",
        r"\bfile a complaint\b",
    ]

    @classmethod
    def evaluate(
        cls,
        subject: str,
        body: str,
        intent: str,
        sentiment: str,
        confidence: float,
        db_context: Dict[str, Any],
        high_value_threshold: float = HIGH_VALUE_THRESHOLD,
    ) -> EscalationDecision:
        """Evaluate incoming email text, intent, and DB context against escalation policies."""

        content = f"{subject} {body}".lower()
        reasons: List[str] = []
        is_escalated = False
        priority = Priority.MEDIUM.value
        routed_to = "General Customer Support"
        suppress_auto = False
        send_ack_only = False

        customer = db_context.get("customer")
        claims = db_context.get("claims", [])
        tickets = db_context.get("tickets", [])

        # 1. Legal Threat Check
        for pat in cls.LEGAL_PATTERNS:
            if re.search(pat, content):
                reasons.append("Legal threat / mention of attorney or lawsuit")
                is_escalated = True
                priority = Priority.CRITICAL.value
                routed_to = "Executive Customer Relations & Legal Liaison"
                suppress_auto = True
                send_ack_only = True
                break

        # 2. Regulatory Complaint Check
        for pat in cls.REGULATORY_PATTERNS:
            if re.search(pat, content):
                reasons.append(
                    "Regulatory complaint (Insurance Commissioner / Ombudsman / DOI)"
                )
                is_escalated = True
                priority = Priority.CRITICAL.value
                routed_to = "Executive Escalations & Regulatory Compliance"
                suppress_auto = True
                send_ack_only = True
                break

        # 3. Fraud / Identity Theft Check
        for pat in cls.FRAUD_PATTERNS:
            if re.search(pat, content):
                reasons.append(
                    "Suspected fraud, identity theft, or unauthorized policy modification"
                )
                is_escalated = True
                priority = Priority.CRITICAL.value
                routed_to = "Special Investigations Unit (SIU)"
                suppress_auto = True
                send_ack_only = True
                break

        # 4. High-Value Claim Dispute Check
        # (> $5,000 threshold or large claim rejection)
        for claim in claims:
            claimed_amt = float(claim.get("claimed_amount", 0.0) or 0.0)
            approved_amt = float(claim.get("approved_amount", 0.0) or 0.0)
            status = str(claim.get("status", ""))

            if claimed_amt >= high_value_threshold and (
                status in ["Rejected", "In-Review", "Delayed"]
                or intent in ["CLAIM_REJECTION_DISPUTE", "CLAIM_DELAY"]
            ):
                reasons.append(
                    f"High-value claim dispute: Claim #{claim.get('claim_number')} "
                    f"for ${claimed_amt:,.2f} exceeding "
                    f"${high_value_threshold:,.2f} threshold"
                )
                is_escalated = True

                if priority != Priority.CRITICAL.value:
                    priority = Priority.HIGH.value

                if routed_to == "General Customer Support":
                    routed_to = "Senior Claims Adjuster & Supervisory Desk"

                send_ack_only = True

        # 5. Explicit Formal Complaint Check
        if any(
            re.search(pattern, content)
            for pattern in cls.FORMAL_COMPLAINT_PATTERNS
        ):
            reasons.append("Explicit formal customer complaint")
            is_escalated = True

            if priority != Priority.CRITICAL.value:
                priority = Priority.HIGH.value

            if routed_to == "General Customer Support":
                routed_to = "Executive Customer Relations"

            suppress_auto = True
            send_ack_only = True

        # 6. Policy Cancellation Check (Retention Opportunity)
        if intent == "CANCELLATION" or any(
            re.search(pattern, content)
            for pattern in cls.CANCELLATION_PATTERNS
        ):
            reasons.append(
                "Policy cancellation request "
                "(Customer Retention & Underwriting Review opportunity)"
            )
            is_escalated = True

            if priority != Priority.CRITICAL.value:
                priority = Priority.HIGH.value

            if routed_to == "General Customer Support":
                routed_to = "Customer Retention & Cancellations Desk"

            send_ack_only = True

        # 7. Severe Customer Distress / Hostile Sentiment
        if sentiment.lower() in ["angry", "furious", "hostile"]:
            reasons.append("Severe customer distress or hostile sentiment detected")
            is_escalated = True

            if priority != Priority.CRITICAL.value:
                priority = Priority.HIGH.value

            if routed_to == "General Customer Support":
                routed_to = "Executive Customer Relations"

        # 8. Repeat Contacts / Multiple Open Tickets within Short Window
        if len(tickets) >= 2 or any(
            ticket.get("status") in ["Open", "In-Progress", "Escalated"]
            for ticket in tickets
        ):
            reasons.append(
                f"Repeat customer contact: {len(tickets)} existing support tickets on file"
            )
            is_escalated = True

            if priority != Priority.CRITICAL.value:
                priority = Priority.HIGH.value

            if routed_to == "General Customer Support":
                routed_to = "Senior Tier-2 Support Operations"

        # 9. Low AI Confidence (< 0.75)
        if confidence < 0.75:
            reasons.append(
                f"Low AI classification confidence score ({confidence:.2f} < 0.75)"
            )
            is_escalated = True

            if priority not in [
                Priority.CRITICAL.value,
                Priority.HIGH.value,
            ]:
                priority = Priority.MEDIUM.value

            if routed_to == "General Customer Support":
                routed_to = "Human Triage Review Queue"

            suppress_auto = True
            send_ack_only = True

        # 10. Identity verification for claim-related requests
        # An unknown customer requires human verification before account-specific
        # action. This is a review requirement, not by itself an emergency.
        if customer is None and intent in [
            "NEW_CLAIM",
            "CLAIM_DELAY",
            "CLAIM_REJECTION_DISPUTE",
            "ROADSIDE_ASSISTANCE",
        ]:
            reasons.append(
                "Customer identity could not be matched; human verification is "
                "required before account-specific claim action"
            )
            is_escalated = True

            if priority != Priority.CRITICAL.value:
                priority = Priority.HIGH.value

            if routed_to in [
                "General Customer Support",
                "Claims Intake & FNOL Desk",
            ]:
                routed_to = "Identity Verification & Claims Intake Desk"

        # Format internal routing note
        internal_note = ""
        if is_escalated:
            reasons_str = "; ".join(reasons)
            internal_note = (
                f"[ESCALATED to {routed_to} | Priority: {priority}] "
                f"Triggers: {reasons_str}"
            )

        return EscalationDecision(
            human_review_required=is_escalated,
            priority=priority,
            reasons=reasons,
            routed_to=routed_to,
            suppress_auto_reply=suppress_auto,
            send_acknowledgement_only=send_ack_only,
            internal_routing_note=internal_note,
        )