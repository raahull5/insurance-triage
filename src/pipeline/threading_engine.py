"""Ticket Deduplication and Conversation Thread Resolution Engine."""

import re
import uuid
import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field

from src.ingestion.preprocessor import StructuredEmail
from src.ai.reasoning_engine import TriageDecision

logger = logging.getLogger(__name__)


@dataclass
class ThreadMatchResult:
    """Result of thread hierarchy matching."""
    matched: bool
    match_level: Optional[str]  # LEVEL_1_IN_REPLY_TO, LEVEL_2_REFERENCES, LEVEL_3_SUBJECT_ENTITIES, LEVEL_4_CUSTOMER_INTENT_WINDOW
    ticket_id: Optional[str] = None
    ticket_number: Optional[str] = None
    action: str = "CREATE_NEW_TICKET"  # APPEND_TO_THREAD, REOPEN_TICKET, CREATE_NEW_LINKED_TICKET, CREATE_NEW_TICKET
    linked_ticket_number: Optional[str] = None
    notes: str = ""


class ThreadResolutionEngine:
    """4-level hierarchical thread resolver and ticket deduplication manager."""

    RESOLVED_REOPEN_WINDOW_DAYS: int = 7
    INTENT_RECENCY_WINDOW_HOURS: int = 48

    def __init__(
        self,
        reopen_window_days: int = RESOLVED_REOPEN_WINDOW_DAYS,
        intent_window_hours: int = INTENT_RECENCY_WINDOW_HOURS,
    ):
        self.reopen_window_days = reopen_window_days
        self.intent_window_hours = intent_window_hours

    @staticmethod
    def normalize_subject(subject: str) -> str:
        """Strip Re:, Fwd:, [Ticket#], and excessive whitespace."""
        subj = subject or ""
        # Strip prefixes
        subj = re.sub(r"^(?:re|fwd|fw|aw|sv):\s*", "", subj, flags=re.IGNORECASE)
        subj = re.sub(r"\[(?:tck|clm|pol)-[^\]]+\]", "", subj, flags=re.IGNORECASE)
        return " ".join(subj.split()).strip().lower()

    def resolve_thread(
        self,
        email: StructuredEmail,
        decision: TriageDecision,
        db_context: Dict[str, Any],
        recent_tickets: List[Dict[str, Any]],
    ) -> ThreadMatchResult:
        """Execute 4-level hierarchical thread resolution over candidate existing tickets."""
        # Level 1: In-Reply-To Header Match
        if email.in_reply_to:
            for t in recent_tickets:
                if t.get("email_id") == email.in_reply_to:
                    return self._determine_ticket_action(t, "LEVEL_1_IN_REPLY_TO", "Matched exact In-Reply-To Message-ID")

        # Level 2: References Header Match
        if email.references:
            # Extract tokens with and without angle brackets
            raw_tokens = email.references.replace("<", " ").replace(">", " ").split()
            ref_ids = set(raw_tokens + [f"<{tok}>" for tok in raw_tokens])
            for t in recent_tickets:
                t_email_id = str(t.get("email_id") or "").strip("<>")
                if t.get("email_id") in ref_ids or t_email_id in raw_tokens:
                    return self._determine_ticket_action(t, "LEVEL_2_REFERENCES", "Matched Message-ID in References thread ancestry")

        # Level 3: Subject Thread Pattern & Entity Match (Claim / Policy + Customer)
        customer = db_context.get("customer")
        # The customers row keys the primary key as `id`; accept `customer_id`
        # too so either context shape resolves instead of raising KeyError.
        cust_id = None
        if customer:
            cust_id = str(customer.get("customer_id") or customer.get("id") or "") or None
        norm_subj = self.normalize_subject(email.subject)

        for t in recent_tickets:
            t_cust_id = str(t.get("customer_id") or "")
            t_subj = self.normalize_subject(t.get("subject", ""))

            # If matching customer and normalized subject is identical
            if cust_id and t_cust_id == cust_id and norm_subj and norm_subj == t_subj:
                return self._determine_ticket_action(t, "LEVEL_3_SUBJECT_ENTITIES", "Matched normalized subject pattern for same customer")

            # Or if matching specific policy/claim entity tokens. The stored
            # columns are UUIDs, so the human-readable numbers are compared when
            # present (the runner attaches them) and UUIDs as a last resort.
            if decision.claim_number and (
                t.get("claim_number") == decision.claim_number
                or (t.get("claim_id") and decision.claim_number in str(t.get("claim_id")))
            ):
                return self._determine_ticket_action(t, "LEVEL_3_SUBJECT_ENTITIES", f"Matched Claim Number {decision.claim_number}")
            if decision.policy_number and (
                t.get("policy_number") == decision.policy_number
                or (t.get("policy_id") and decision.policy_number in str(t.get("policy_id")))
            ):
                return self._determine_ticket_action(t, "LEVEL_3_SUBJECT_ENTITIES", f"Matched Policy Number {decision.policy_number}")

        # Level 4: Customer ID + Intent within Recency Window (e.g. 48 hours)
        now = datetime.now(timezone.utc)
        if cust_id:
            for t in recent_tickets:
                t_cust_id = str(t.get("customer_id") or "")
                if t_cust_id == cust_id:
                    # Check recency window
                    created_at_str = t.get("created_at") or t.get("updated_at")
                    if created_at_str:
                        try:
                            # Parse sqlite timestamp
                            t_time = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                            if t_time.tzinfo is None:
                                t_time = t_time.replace(tzinfo=timezone.utc)
                            diff_hours = (now - t_time).total_seconds() / 3600.0
                            if diff_hours <= self.intent_window_hours:
                                t_cat = t.get("category", "")
                                if t_cat == decision.category or t.get("intent") == decision.intent:
                                    return self._determine_ticket_action(
                                        t,
                                        "LEVEL_4_CUSTOMER_INTENT_WINDOW",
                                        f"Matched active customer inquiry within {self.intent_window_hours}h window",
                                    )
                        except Exception as e:
                            logger.debug(f"Could not parse ticket timestamp: {e}")

        # No thread match found -> Create new ticket
        new_ticket_num = f"TCK-2024-{uuid.uuid4().hex[:5].upper()}"
        return ThreadMatchResult(
            matched=False,
            match_level=None,
            ticket_id=None,
            ticket_number=new_ticket_num,
            action="CREATE_NEW_TICKET",
            notes="New customer conversation thread",
        )

    def _determine_ticket_action(
        self,
        ticket: Dict[str, Any],
        match_level: str,
        reason: str,
    ) -> ThreadMatchResult:
        """Apply re-open vs new linked ticket logic based on resolution timestamp."""
        status = ticket.get("status", "Open")
        ticket_id = ticket.get("id")
        ticket_num = ticket.get("ticket_number")
        if not ticket_num:
            # The candidate list ends with a synthetic placeholder for the
            # incoming email, whose `id` is None. `str(None)` used to make that
            # fabricate the identifier "TCK-None" -- a value that looks like a
            # real ticket but resolves to nothing. Emit None instead, so callers
            # fall through to their create-ticket path.
            ticket_num = f"TCK-{ticket_id}" if ticket_id else None
            if ticket_num is None:
                logger.debug("Thread candidate has no ticket number; not a persisted ticket.")
                return ThreadMatchResult(
                    matched=False,
                    match_level=match_level,
                    action="CREATE_NEW_TICKET",
                    notes=f"{reason}; candidate is not a persisted ticket",
                )
        updated_at_str = ticket.get("updated_at") or ticket.get("created_at")

        if status in ["Resolved", "Closed"]:
            now = datetime.now(timezone.utc)
            days_since_resolved = 0
            if updated_at_str:
                try:
                    resolved_time = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                    if resolved_time.tzinfo is None:
                        resolved_time = resolved_time.replace(tzinfo=timezone.utc)
                    days_since_resolved = (now - resolved_time).total_seconds() / 86400.0
                except Exception:
                    days_since_resolved = 1.0

            if days_since_resolved <= self.reopen_window_days:
                return ThreadMatchResult(
                    matched=True,
                    match_level=match_level,
                    ticket_id=ticket_id,
                    ticket_number=ticket_num,
                    action="REOPEN_TICKET",
                    notes=f"{reason} (Re-opening ticket resolved {days_since_resolved:.1f} days ago < {self.reopen_window_days}d threshold)",
                )
            else:
                new_ticket_num = f"TCK-2024-{uuid.uuid4().hex[:5].upper()}"
                return ThreadMatchResult(
                    matched=True,
                    match_level=match_level,
                    ticket_id=None,
                    ticket_number=new_ticket_num,
                    action="CREATE_NEW_LINKED_TICKET",
                    linked_ticket_number=ticket_num,
                    notes=f"{reason} (Previous ticket resolved {days_since_resolved:.1f} days ago > {self.reopen_window_days}d threshold; creating linked ticket)",
                )

        # Active / Open ticket
        return ThreadMatchResult(
            matched=True,
            match_level=match_level,
            ticket_id=ticket_id,
            ticket_number=ticket_num,
            action="APPEND_TO_THREAD",
            notes=f"{reason} (Appending email to active ticket {ticket_num})",
        )
