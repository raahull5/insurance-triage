"""Automated Email Reply Dispatcher with threading headers, rate limiting, duplicate prevention, and audit logging."""

import hashlib
import logging
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from dataclasses import dataclass

from src.ingestion.himalaya_client import HimalayaClient
from src.ingestion.preprocessor import StructuredEmail
from src.ai.reasoning_engine import TriageDecision

logger = logging.getLogger(__name__)


@dataclass
class DispatchAuditRecord:
    """Record of an email dispatch attempt for compliance and auditing."""
    timestamp: str
    message_id: str
    recipient: str
    subject: str
    status: str  # SENT, DRY_RUN, SUPPRESSED_HUMAN_REVIEW, DUPLICATE_SUPPRESSED, RATE_LIMITED, FAILED
    error_details: Optional[str] = None
    in_reply_to: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "message_id": self.message_id,
            "recipient": self.recipient,
            "subject": self.subject,
            "status": self.status,
            "error_details": self.error_details,
            "in_reply_to": self.in_reply_to,
        }


class ReplyDispatcher:
    """Dispatches auto-replies with threading headers, safety controls, rate limits, and audit tracking."""

    def __init__(
        self,
        himalaya_client: Optional[HimalayaClient] = None,
        support_from_address: str = "support@apexshield.com",
        dry_run: bool = False,
        rate_limit_per_minute: int = 30,
        audit_repository: Optional[Any] = None,
    ):
        self.client = himalaya_client or HimalayaClient(mock_mode=dry_run)
        self.support_from_address = support_from_address
        self.dry_run = dry_run
        self.rate_limit_per_minute = rate_limit_per_minute

        # Optional durable audit sink. When a repository is supplied, every
        # dispatch outcome is also written to the reply_audit table so the
        # trail outlives the process. The in-memory log is always kept, since
        # it is what existing callers and tests read.
        self._audit_repository = audit_repository

        # Duplicate prevention cache (set of message_ids replied to)
        self._replied_message_ids: set[str] = set()

        # Rate limiting timestamps window
        self._dispatch_timestamps: List[float] = []

        # Audit log records
        self._audit_log: List[DispatchAuditRecord] = []

    def _record_audit(
        self, record: DispatchAuditRecord, reply_body: Optional[str] = None
    ) -> DispatchAuditRecord:
        """Append to the in-memory log and, if configured, persist to the DB.

        Every dispatch path funnels through here so a new outcome cannot be
        added without also being audited. Persistence is best-effort: if the
        audit write fails the reply is still recorded in memory and the reply
        itself is not blocked, because a database hiccup must not turn into a
        customer-facing failure.

        The body is stored only as a SHA-256 digest plus a character count. The
        text itself can contain record details or PII, and the audit table is
        not the access-controlled path for that data.
        """
        self._audit_log.append(record)

        repo = self._audit_repository
        if repo is None:
            return record

        body_hash = None
        body_chars = None
        if reply_body:
            try:
                body_chars = len(reply_body)
                body_hash = hashlib.sha256(reply_body.encode("utf-8")).hexdigest()
            except Exception:  # pragma: no cover - hashing must never break dispatch
                logger.debug("Could not hash reply body for audit", exc_info=True)

        try:
            repo.record_reply_audit(
                timestamp=record.timestamp,
                message_id=record.message_id,
                recipient=record.recipient,
                subject=record.subject,
                status=record.status,
                error_details=record.error_details,
                in_reply_to=record.in_reply_to,
                body_sha256=body_hash,
                body_chars=body_chars,
                dry_run=self.dry_run,
            )
        except Exception:
            logger.exception(
                "Failed to persist reply audit row for message %s; "
                "in-memory log retained.",
                record.message_id,
            )
        return record

    def can_dispatch(self, message_id: str) -> tuple[bool, str]:
        """Check duplicate prevention and rate limiting."""
        if message_id in self._replied_message_ids:
            return False, "Duplicate message: Reply already dispatched for this Message-ID."

        now = time.time()
        # Filter timestamps within the last 60 seconds
        self._dispatch_timestamps = [t for t in self._dispatch_timestamps if now - t < 60.0]
        if len(self._dispatch_timestamps) >= self.rate_limit_per_minute:
            return False, f"Rate limit exceeded ({self.rate_limit_per_minute} emails/min)."

        return True, "OK"

    def dispatch(
        self,
        email: StructuredEmail,
        decision: TriageDecision,
        reply_body: Optional[str],
        send_to: Optional[str] = None,
        allow_identity_challenge: bool = False,
    ) -> tuple[bool, DispatchAuditRecord]:
        """Dispatch email reply with safety checks, threading headers, and audit recording.

        `send_to` overrides the recipient. It exists for exactly one case: the
        identity-verification challenge, which must be delivered to the contact
        on file rather than to the address that wrote in. Every other call
        leaves it None and replies to the sender as usual.
        """
        is_identity_challenge = decision.intent == "IDENTITY_VERIFICATION"
        recipient = (send_to or "").strip() if is_identity_challenge else (
            send_to or email.sender_email
        )
        now_iso = datetime.now(timezone.utc).isoformat()

        # Identity challenges are out-of-band and must not disclose the
        # original subject or attach to the requester's email thread.
        if is_identity_challenge:
            subject = "Identity verification required"
            in_reply_to = None
        else:
            subject = (
                email.subject
                if email.subject.lower().startswith("re:")
                else f"Re: {email.subject}"
            )
            in_reply_to = email.message_id

        def suppress(status: str, reason: str) -> tuple[bool, DispatchAuditRecord]:
            record = DispatchAuditRecord(
                timestamp=now_iso,
                message_id=email.message_id,
                recipient=recipient,
                subject=subject,
                status=status,
                error_details=reason,
                in_reply_to=in_reply_to,
            )
            self._record_audit(record, reply_body)
            logger.info(
                "Reply suppressed for message %s: %s",
                email.message_id,
                reason,
            )
            return False, record

        # These gates must not depend on whether a suggested reply exists.
        if decision.escalation_needed:
            return suppress(
                "SUPPRESSED_HUMAN_REVIEW",
                "Auto-reply suppressed because escalation is required.",
            )
        if decision.human_review_required:
            return suppress(
                "SUPPRESSED_HUMAN_REVIEW",
                "Auto-reply suppressed because human review is required.",
            )
        if decision.intent == "SPAM_OR_AUTOMATED":
            return suppress(
                "SUPPRESSED_SPAM",
                "Auto-reply suppressed for spam or automated mail.",
            )

        if is_identity_challenge:
            if not allow_identity_challenge:
                return suppress(
                    "SUPPRESSED_IDENTITY_CHALLENGE",
                    "Identity challenge dispatch requires explicit authorization.",
                )
            if not send_to or not recipient:
                return suppress(
                    "SUPPRESSED_IDENTITY_CHALLENGE",
                    "Identity challenge requires an explicit on-file recipient.",
                )
        elif allow_identity_challenge or send_to:
            return suppress(
                "SUPPRESSED_INVALID_OVERRIDE",
                "Recipient overrides are permitted only for authorized identity challenges.",
            )

        # 2. Check for empty body
        if not reply_body or not reply_body.strip():
            record = DispatchAuditRecord(
                timestamp=now_iso,
                message_id=email.message_id,
                recipient=recipient,
                subject=subject,
                status="SUPPRESSED_EMPTY_BODY",
                error_details="No reply body generated.",
                in_reply_to=in_reply_to,
            )
            self._record_audit(record, reply_body)
            return False, record

        # 3. Duplicate and rate-limit check
        can_send, reason = self.can_dispatch(email.message_id)
        if not can_send:
            status = "DUPLICATE_SUPPRESSED" if "Duplicate" in reason else "RATE_LIMITED"
            record = DispatchAuditRecord(
                timestamp=now_iso,
                message_id=email.message_id,
                recipient=recipient,
                subject=subject,
                status=status,
                error_details=reason,
                in_reply_to=in_reply_to,
            )
            self._record_audit(record, reply_body)
            logger.warning(f"Dispatch blocked for message {email.message_id}: {reason}")
            return False, record

        # 4. Dry-run Mode
        if self.dry_run:
            self._replied_message_ids.add(email.message_id)
            self._dispatch_timestamps.append(time.time())
            record = DispatchAuditRecord(
                timestamp=now_iso,
                message_id=email.message_id,
                recipient=recipient,
                subject=subject,
                status="DRY_RUN",
                error_details=None,
                in_reply_to=in_reply_to,
            )
            self._record_audit(record, reply_body)
            logger.info(f"[DRY RUN] Dispatched reply to {recipient} | Subject: {subject}\n{reply_body}")
            return True, record

        # 5. Live Himalaya CLI Send
        try:
            success = self.client.send_email(
                to=recipient,
                subject=subject,
                body=reply_body,
                in_reply_to=in_reply_to,
            )
            if success:
                self._replied_message_ids.add(email.message_id)
                self._dispatch_timestamps.append(time.time())
                record = DispatchAuditRecord(
                    timestamp=now_iso,
                    message_id=email.message_id,
                    recipient=recipient,
                    subject=subject,
                    status="SENT",
                    error_details=None,
                    in_reply_to=in_reply_to,
                )
                self._record_audit(record, reply_body)
                return True, record
            else:
                record = DispatchAuditRecord(
                    timestamp=now_iso,
                    message_id=email.message_id,
                    recipient=recipient,
                    subject=subject,
                    status="FAILED",
                    error_details="Himalaya CLI send failed.",
                    in_reply_to=in_reply_to,
                )
                self._record_audit(record, reply_body)
                return False, record
        except Exception as e:
            logger.error(f"Failed to dispatch email reply: {e}", exc_info=True)
            record = DispatchAuditRecord(
                timestamp=now_iso,
                message_id=email.message_id,
                recipient=recipient,
                subject=subject,
                status="FAILED",
                error_details=str(e),
                in_reply_to=in_reply_to,
            )
            self._record_audit(record, reply_body)
            return False, record

    def get_audit_log(self) -> List[DispatchAuditRecord]:
        """Return all logged dispatch records."""
        return list(self._audit_log)


# Alias for backward compatibility
ReplySender = ReplyDispatcher

