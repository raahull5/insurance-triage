"""Autonomous background execution coordinator.

Orchestrates the full triage lifecycle for each incoming email:

    ingest -> watermark gate -> dedupe -> preprocess -> safety
           -> customer context -> intent/escalation reasoning
           -> thread resolution -> ticket upsert -> auto-reply
           -> triage record + CSV export -> watermark advance

Every stage is defensive: a failure in one email is logged and skipped rather
than aborting the polling loop.
"""

import time
import logging
import os
from email.utils import getaddresses
import threading
import uuid
from dataclasses import replace
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

from src.config import AppConfig
from src.core.resilience import (
    CircuitBreaker,
    HealthMonitor,
    is_transient,
    OutcomeStatus,
    ProcessingOutcome,
)
from src.db.database import Database
from src.db.repository import InsuranceRepository
from src.ingestion.himalaya_client import (
    HimalayaClient,
    HimalayaError,
    HimalayaPermanentError,
    HimalayaTransientError,
)
from src.ingestion.detector import EmailDetector, WatermarkManager
from src.ingestion.retry_queue import RetryQueue
from src.ingestion.preprocessor import EmailPreprocessor, StructuredEmail
from src.ai.reasoning_engine import LLMReasoningEngine, TriageDecision
from src.ai.safety import SafetyEngine
from src.ai.identity_verification import (
    IdentityVerifier,
    build_challenge_body,
    extract_verification_code,
)
from src.ai.intents import get_intent_info
from src.autoreply.reply_generator import ReplyGenerator
from src.autoreply.sender import ReplyDispatcher
from src.pipeline.threading_engine import ThreadResolutionEngine, ThreadMatchResult
from src.reporting.csv_exporter import CSVExporter

logger = logging.getLogger(__name__)


def _norm(email: Optional[str]) -> str:
    """Normalize an address for comparison (case/whitespace insensitive)."""
    return (email or "").strip().lower()


def _sender_addresses(value: Any) -> set[str]:
    """Extract normalized email addresses from Himalaya sender metadata."""
    if value is None:
        return set()

    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("addr") or item.get("email") or ""))
            else:
                parts.append(str(item))
        value = ", ".join(parts)
    elif isinstance(value, dict):
        value = str(value.get("addr") or value.get("email") or "")
    else:
        value = str(value)

    return {
        address.strip().lower()
        for _, address in getaddresses([value])
        if address.strip()
    }

# Placeholder for a message whose real ID could not be determined. It is
# deliberately never written to the watermark: an unparseable payload may have
# been a real mail, and persisting a fake ID would hide it from the operator.
UNKNOWN_MESSAGE_ID = "unknown"


class PipelineStats:
    """Simple counters surfaced via `status` and the dashboard."""

    def __init__(self) -> None:
        self.emails_seen = 0
        self.emails_processed = 0
        self.emails_skipped_duplicate = 0
        self.emails_archived_spam = 0
        self.emails_held_rate_limit = 0
        self.emails_failed = 0
        self.replies_sent = 0
        self.replies_suppressed = 0
        self.replies_simulated = 0
        self.tickets_created = 0
        self.tickets_reused = 0
        self.escalations = 0
        self.poll_cycles = 0
        self.started_at: Optional[str] = None

    def to_dict(self) -> Dict[str, int]:
        return dict(self.__dict__)

    def __str__(self) -> str:
        return (
            f"cycles={self.poll_cycles} seen={self.emails_seen} processed={self.emails_processed} "
            f"dupes={self.emails_skipped_duplicate} spam={self.emails_archived_spam} "
            f"held={self.emails_held_rate_limit} failed={self.emails_failed} "
            f"replied={self.replies_sent} simulated={self.replies_simulated} "
            f"suppressed={self.replies_suppressed} "
            f"tickets+={self.tickets_created} tickets~={self.tickets_reused} escalations={self.escalations}"
        )


class AutonomousPipeline:
    """End-to-end background pipeline for insurance email ingestion and triage."""

    def __init__(
        self,
        config: AppConfig,
        database: Database,
        mock_mode: bool = False,
        dry_run: Optional[bool] = None,
    ):
        self.config = config
        self.db = database
        self.mock_mode = mock_mode
        # An explicit argument is an intentional override (useful for isolated
        # tests with a recording transport). Otherwise honor the safe config.
        self.dry_run = (
            bool(config.autoreply.dry_run) if dry_run is None else bool(dry_run)
        )
        self.allowed_senders = {
            address.strip().lower()
            for address in os.environ.get(
                "INSURANCE_TRIAGE_ALLOWED_SENDERS", ""
            ).split(",")
            if address.strip()
        }

        self.himalaya = HimalayaClient(
            account=config.gmail.account_name,
            mock_mode=mock_mode,
            config_path="data/config.toml",
        )
        self.watermark_mgr = WatermarkManager(config.system.watermark_file)
        self.detector = EmailDetector(self.watermark_mgr)
        self.ai_engine = LLMReasoningEngine(model=config.ai.model, temperature=config.ai.temperature)
        self.reply_gen = ReplyGenerator(signature=config.autoreply.signature)
        # Out-of-band identity verification for senders resolved to a customer
        # record by a quoted claim/policy number rather than their own address.
        self.identity_verifier = IdentityVerifier()
        # The dispatcher's audit trail is made durable per-email, in
        # _maybe_auto_reply(), where a scoped repository connection is already
        # open. Binding one here would leak a connection across the process
        # lifetime, because the pipeline opens a fresh connection per email.
        self.reply_sender = ReplyDispatcher(
            himalaya_client=self.himalaya,
            dry_run=self.dry_run,
        )
        # How the thread resolver classified the most recent email
        # ("APPEND:<ticket>", "REOPEN:<ticket>", "NEW:<ticket>" or "NONE").
        self.last_ticket_action: str = "NONE"
        self.safety = SafetyEngine()
        self.threading = ThreadResolutionEngine()
        self.csv_exporter = CSVExporter(config.system.csv_report_path)

        self.is_running = False
        self.stats = PipelineStats()

        # Step 22 resilience instrumentation.
        self.health = HealthMonitor()
        self.gmail_breaker = CircuitBreaker(
            "gmail", failure_threshold=3, recovery_timeout=300.0
        )
        retry_path = Path(config.system.watermark_file).parent / "retry_queue.json"
        self.retry_queue = RetryQueue(retry_path)
        self._deferred: List[str] = self.retry_queue.pending

    # ------------------------------------------------------------------ #
    # Single-email processing
    # ------------------------------------------------------------------ #
    def process_email(
        self,
        raw_email_data: Dict[str, Any],
        *,
        advance_watermark: bool = True,
    ) -> ProcessingOutcome:
        """Process one email, optionally advancing the Gmail watermark.

        Normal Gmail polling advances the watermark only for durable outcomes.
        Synthetic test-email injections must pass ``advance_watermark=False``
        so they cannot move the live mailbox checkpoint.
        """
        outcome = self._process_email(raw_email_data)
        if advance_watermark:
            return self._advance_watermark_for(outcome)
        return outcome

    def _advance_watermark_for(self, outcome: ProcessingOutcome) -> ProcessingOutcome:
        """Move the watermark forward for a durable outcome.

        The watermark is what guarantees each email is processed exactly once,
        so it only ever advances when the email's fate is safely recorded. A
        single decision point here is deliberate: spreading `save()` across
        every return path is how emails silently start getting skipped.
        """
        if outcome.should_advance_watermark and outcome.email_id and outcome.email_id != UNKNOWN_MESSAGE_ID:
            self.watermark_mgr.save(outcome.email_id)
        return outcome

    def _process_email(self, raw_email_data: Dict[str, Any]) -> ProcessingOutcome:
        # ---- Stage 0: parse. A malformed message can never succeed later. ----
        try:
            email = EmailPreprocessor.from_himalaya_dict(raw_email_data)
        except Exception as exc:
            self.stats.emails_failed += 1
            self.health.record_failure("preprocessing", str(exc))
            msg_id = str(raw_email_data.get("id") or raw_email_data.get("message_id") or UNKNOWN_MESSAGE_ID)
            logger.error("Malformed email %s could not be parsed: %s", msg_id, exc)
            # Unparseable and therefore unprocessable: record it so the watermark
            # can advance past it, but flag it clearly for human review.
            return ProcessingOutcome(
                status=OutcomeStatus.FAILED_MALFORMED,
                email_id=msg_id,
                detail=f"Malformed email: {exc}",
                stage="preprocessing",
                retryable=False,
                should_advance_watermark=True,
            )

        self.stats.emails_seen += 1
        logger.info(
            "Processing email #%s from %s (Subject: %s)",
            email.message_id,
            email.sender_email,
            email.subject,
        )

        stage = "preprocessing"
        conn = None
        try:
            conn = self.db.get_connection()
            repo = InsuranceRepository(conn)

            # ---- Stage 1: idempotency: never process the same Message-ID twice ----
            stage = "deduplication"
            if repo.is_email_processed(email.message_id):
                self.stats.emails_skipped_duplicate += 1
                logger.info("Email #%s already processed; skipping.", email.message_id)
                return ProcessingOutcome(
                    status=OutcomeStatus.SKIPPED_DUPLICATE,
                    email_id=email.message_id,
                    detail="Already recorded in triage_records",
                    stage=stage,
                    should_advance_watermark=True,
                )

            # ---- Stage 2: safety gate: spam / automated / loop / rate limit ----
            stage = "safety"
            safety = self.safety.check_incoming_email(
                sender_email=email.sender_email,
                subject=email.subject,
                body=email.body_text,
                headers=email.headers or {},
                thread_id=email.in_reply_to or email.message_id,
            )
            if not safety.is_safe:
                reason = "; ".join(safety.violations)
                if safety.action == "ARCHIVE_SPAM":
                    self.stats.emails_archived_spam += 1
                    logger.info("Archived as spam/automated: %s", reason)
                else:
                    self.stats.emails_held_rate_limit += 1
                    logger.warning("Held for human review: %s", reason)
                # A terminal outcome is durable only after its database record
                # has been saved successfully. Let save failures reach the
                # shared exception handler so the watermark does not advance.
                stage = "database_write"
                self._record_terminal_result(
                    repo, email, processing_status=safety.action, reason=reason
                )
                self.health.record_success("database_write")
                stage = "safety"
                return ProcessingOutcome(
                    status=(
                        OutcomeStatus.ARCHIVED_SPAM
                        if safety.action == "ARCHIVE_SPAM"
                        else OutcomeStatus.HELD_FOR_REVIEW
                    ),
                    email_id=email.message_id,
                    detail=reason,
                    stage=stage,
                    should_advance_watermark=True,
                )

            # ---- Stage 3: customer identification and context retrieval ----
            stage = "context_retrieval"
            context = repo.get_full_insurance_context(
                email.sender_email, email.subject, email.body_text
            )
            customer = context.get("customer")
            customer_id = customer["id"] if customer else None
            if customer is None:
                # Not an error: unidentified senders are a supported workflow.
                logger.info("No customer record matched %s; continuing as unidentified.", email.sender_email)

            # If this sender is replying with a verification code, redeem it
            # before anything else: a correct code makes the address trusted for
            # that record, so the rest of this run behaves like a verified
            # sender. A wrong code is a normal failed attempt, not an error.
            candidate_code = extract_verification_code(email.body_text)
            if candidate_code:
                redemption = self.identity_verifier.redeem(
                    email.sender_email, candidate_code
                )
                if redemption.verified:
                    logger.info(
                        "Identity challenge redeemed by %s for customer %s.",
                        email.sender_email, redemption.customer_id,
                    )
                    context["identity_verified_by_code"] = redemption.customer_id
                else:
                    logger.info(
                        "Identity code rejected for %s: %s",
                        email.sender_email, redemption.reason,
                    )
                    context["identity_code_rejected"] = redemption.reason

            # Identity is only *proven* by an exact sender-address match. The
            # context fallbacks (claim/policy/VIN number) resolve whoever OWNS
            # that number, which is not necessarily the sender. Anyone can quote
            # a claim number, so an unverified match must not be treated as
            # authorization to disclose that record.
            context["identity_confirmed"] = bool(customer) and (
                context.get("matched_via") == "email"
            )
            if customer and not context["identity_confirmed"]:
                # Before escalating, give the sender a chance to prove control
                # out of band. If they have already redeemed a code, they are
                # verified and normal auto-reply applies.
                if self.identity_verifier.is_verified(email.sender_email, customer_id):
                    context["identity_confirmed"] = True
                    context["matched_via"] = f"{context.get('matched_via')}+verified"
                    logger.info(
                        "Sender %s redeemed a prior identity challenge for %s; "
                        "treating identity as confirmed.",
                        email.sender_email, customer_id,
                    )
                else:
                    logger.warning(
                        "Sender %s resolved to %s via %s, not a verified address "
                        "match; treating the identity as unconfirmed.",
                        email.sender_email, customer_id, context.get("matched_via"),
                    )
                    # Keep the identity for triage/routing, but strip the personal
                    # record from anything an auto-reply could echo back.
                    context["unverified_customer"] = customer

            # ---- Stage 4: AI intent classification + escalation reasoning ----
            stage = "llm_reasoning"
            decision = self.ai_engine.analyze(email, context)
            if decision is None:
                raise ValueError("Reasoning engine returned no decision")
            if decision.escalation_needed:
                self.stats.escalations += 1

            # ---- Stage 5: thread resolution / ticket dedup ----
            stage = "ticket_handling"
            ticket_action = "NONE"
            self.last_ticket_action = ticket_action
            if decision.intent != "SPAM_OR_AUTOMATED":
                ticket_action = self._resolve_or_create_ticket(
                    repo, email, decision, customer_id, context
                )
                # Exposed so tests and operators can see how the thread resolver
                # classified this email without re-querying the database.
                self.last_ticket_action = ticket_action

            # ---- Stage 6: auto-reply generation and dispatch ----
            stage = "auto_reply"
            # Bind the per-email repository to the dispatcher for the duration
            # of this call so every dispatch outcome lands in the durable
            # reply_audit table, then restore the previous binding (normally
            # None) so a connection is never retained past its scope.
            previous_audit_repo = self.reply_sender._audit_repository
            self.reply_sender._audit_repository = repo
            try:
                reply_status, reply_sent_at = self._maybe_auto_reply(email, decision, context)
            finally:
                self.reply_sender._audit_repository = previous_audit_repo
            if reply_status in ("Failed",):
                # A failed send must never be recorded as "replied"; the email
                # still needs a human, and the status reflects that.
                logger.error("Auto-reply FAILED for #%s -- not marked as replied.", email.message_id)

            # ---- Stage 7: persist triage record ----
            stage = "database_write"
            record = self._build_triage_record(
                email, decision, customer_id, reply_status, reply_sent_at, context
            )
            repo.save_triage_record(record)
            self.health.record_success("database_write")

            # ---- Stage 8: CSV export (side artifact; never fails the email) ----
            stage = "csv_export"
            try:
                self.csv_exporter.append_record(record)
                self.health.record_success("csv_export")
            except Exception as exc:
                logger.error("CSV export failed for %s: %s", email.message_id, exc)
                self.health.record_failure("csv_export", str(exc))

            # ---- Durably stored: safe to advance the watermark ----
            # (the watermark is advanced centrally by `_advance_watermark_for`)
            self.stats.emails_processed += 1
            logger.info(
                "Triage complete for #%s [%s/%s] ticket_action=%s reply=%s",
                email.message_id,
                decision.intent,
                decision.priority,
                ticket_action,
                reply_status,
            )
            return ProcessingOutcome(
                status=OutcomeStatus.COMPLETED,
                email_id=email.message_id,
                detail=f"{decision.intent}/{decision.priority} reply={reply_status}",
                stage="complete",
                should_advance_watermark=True,
                context={
                    "intent": decision.intent,
                    "priority": decision.priority,
                    "reply_status": reply_status,
                    "ticket_action": ticket_action,
                },
            )
        except Exception as exc:
            self.stats.emails_failed += 1
            self.health.record_failure(stage, str(exc))
            logger.error(
                "Failed to process email #%s during stage '%s': %s",
                email.message_id,
                stage,
                exc,
                exc_info=True,
            )
            # NOT durable -> watermark must not advance, so the next cycle retries.
            return ProcessingOutcome(
                status=self._status_for_stage(stage),
                email_id=email.message_id,
                detail=f"{stage}: {exc}",
                stage=stage,
                retryable=is_transient(exc),
                should_advance_watermark=False,
            )
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _status_for_stage(stage: str) -> OutcomeStatus:
        """Map a failing pipeline stage to its outcome status."""
        return {
            "database_write": OutcomeStatus.FAILED_DATABASE,
            "llm_reasoning": OutcomeStatus.FAILED_LLM,
            "auto_reply": OutcomeStatus.FAILED_REPLY,
            "preprocessing": OutcomeStatus.FAILED_MALFORMED,
        }.get(stage, OutcomeStatus.FAILED_DATABASE)

    # ------------------------------------------------------------------ #
    # Stage helpers
    # ------------------------------------------------------------------ #
    def _record_terminal_result(
        self,
        repo: InsuranceRepository,
        email: StructuredEmail,
        processing_status: str,
        reason: str,
    ) -> None:
        """Persist a non-triage outcome (spam/held) so the message is not reprocessed."""
        is_spam = processing_status == "ARCHIVE_SPAM"
        # Route from the canonical taxonomy rather than a hardcoded label, so the
        # stored desk always matches the intent's routing_destination (#archive).
        spam_intent = get_intent_info("SPAM_OR_AUTOMATED")
        record = {
            "email_id": str(email.message_id),
            "sender_email": email.sender_email,
            "sender_name": email.sender_name or None,
            "subject": email.subject or "(no subject)",
            "received_date": email.received_date or datetime.now().isoformat(),
            "customer_id": None,
            "policy_number": None,
            "claim_number": None,
            "category": spam_intent.category,
            "intent": "SPAM_OR_AUTOMATED" if is_spam else "GENERAL_QUERY",
            "priority": "Low",
            "urgency_score": 1,
            "sentiment": "Neutral",
            "escalation_needed": 1 if processing_status in ("RATE_LIMIT_HOLD", "SUPPRESS_LOOP") else 0,
            "escalation_status": processing_status,
            "summary": f"[{processing_status}] {reason}"[:500],
            "suggested_reply": "",
            "routing_desk": spam_intent.routing_destination if is_spam else "Senior Tier-2 Support Operations",
            "processing_status": processing_status,
            "reply_status": "Suppressed",
            "reply_sent_at": None,
        }
        repo.save_triage_record(record)

    def _resolve_or_create_ticket(
        self,
        repo: InsuranceRepository,
        email: StructuredEmail,
        decision: TriageDecision,
        customer_id: Optional[str],
        db_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Run the 4-level thread resolver and persist the resulting ticket action."""
        # The resolver reads the customer from db_context; passing an empty dict
        # silently disables Levels 3 and 4 and duplicates every follow-up ticket.
        db_context = db_context or {}
        recent_tickets: List[Dict[str, Any]] = []
        if customer_id:
            recent_tickets = repo.get_customer_tickets(customer_id)
        # Give the resolver a stable, queryable view of each candidate.
        # `claim_id`/`policy_id` hold UUIDs in this schema, so the human-readable
        # numbers are attached explicitly for the Level 3 entity match.
        candidates: List[Dict[str, Any]] = []
        # Resolve the human-readable claim/policy numbers in one pass: the
        # ticket table stores UUIDs, which Level 3's entity match cannot compare.
        claim_numbers = {
            r["id"]: r["claim_number"]
            for r in repo.conn.execute(
                "SELECT id, claim_number FROM claims"
            ).fetchall()
        }
        policy_numbers = {
            r["id"]: r["policy_number"]
            for r in repo.conn.execute(
                "SELECT id, policy_number FROM policies"
            ).fetchall()
        }
        for t in recent_tickets:
            candidates.append(
                {
                    "id": t.get("id"),
                    "ticket_number": t.get("ticket_number"),
                    "email_id": t.get("email_id"),
                    "subject": t.get("subject"),
                    "status": t.get("status"),
                    "created_at": t.get("created_at"),
                    "customer_id": t.get("customer_id"),
                    "claim_id": t.get("claim_id"),
                    "policy_id": t.get("policy_id"),
                    "claim_number": claim_numbers.get(t.get("claim_id")),
                    "policy_number": policy_numbers.get(t.get("policy_id")),
                    "category": t.get("category"),
                    "intent": t.get("intent"),
                }
            )
        candidates.append(
            {
                "id": None,
                "ticket_number": None,
                "email_id": email.message_id,
                "subject": email.subject,
                "status": "Open",
                "created_at": email.received_date or datetime.now().isoformat(),
                "customer_id": customer_id,
            }
        )

        result: ThreadMatchResult = self.threading.resolve_thread(
            email, decision, db_context, candidates
        )
        logger.info(
            "Thread resolution: matched=%s level=%s action=%s (%s)",
            result.matched,
            result.match_level,
            result.action,
            result.notes,
        )

        if result.action == "APPEND_TO_THREAD" and result.ticket_number:
            self.stats.tickets_reused += 1
            return f"APPEND:{result.ticket_number}"

        if result.action == "REOPEN_TICKET" and result.ticket_number:
            repo.reopen_ticket(result.ticket_number)
            self.stats.tickets_reused += 1
            return f"REOPEN:{result.ticket_number}"

        ticket_number = self._next_ticket_number(email)
        repo.create_support_ticket(
            ticket_number=ticket_number,
            customer_id=customer_id,
            policy_id=self._resolve_fk_id(repo, "policies", "policy_number", decision.policy_number),
            claim_id=self._resolve_fk_id(repo, "claims", "claim_number", decision.claim_number),
            subject=f"[{decision.intent}] {email.subject}"[:250],
            category=decision.category,
            priority=decision.priority,
            status="Open",
            assigned_team=decision.routed_to,
            email_id=str(email.message_id),
        )
        self.stats.tickets_created += 1
        if result.action == "CREATE_NEW_LINKED_TICKET" and result.linked_ticket_number:
            return f"NEW:{ticket_number}<-{result.linked_ticket_number}"
        return f"NEW:{ticket_number}"

    @staticmethod
    def _resolve_fk_id(
        repo: InsuranceRepository,
        table: str,
        column: str,
        value: Optional[str],
    ) -> Optional[str]:
        """Map a human-facing number (POL-/CLM-) to its internal primary key."""
        if not value:
            return None
        lookup = {
            "policies": repo.find_policy_by_number,
            "claims": repo.find_claim_by_number,
        }.get(table)
        if not lookup:
            return None
        try:
            row = lookup(value)
            return row.get("id") if row else None
        except Exception:
            return None

    def _next_ticket_number(self, email: StructuredEmail) -> str:
        """Deterministic, collision-resistant ticket number derived from the Message-ID."""
        stamp = datetime.now().strftime("%Y%m%d")
        digest = uuid.uuid5(uuid.NAMESPACE_URL, str(email.message_id)).int % 100000
        return f"TCK-{stamp}-{digest:05d}"

    def _maybe_auto_reply(
        self,
        email: StructuredEmail,
        decision: TriageDecision,
        context: Dict[str, Any],
    ) -> Tuple[str, Optional[str]]:
        """Generate and dispatch an auto-reply when policy and safety allow it."""
        if not self.config.autoreply.enabled:
            return "Disabled", None
        if decision.escalation_needed or decision.human_review_required:
            self.stats.replies_suppressed += 1
            return "Held", None
        if decision.intent == "SPAM_OR_AUTOMATED":
            self.stats.replies_suppressed += 1
            return "Suppressed", None

        # An unverified identity means the sender quoted a claim/policy/VIN
        # number that belongs to someone else. Auto-replying with that record's
        # status and amounts would disclose one customer's data to another, so
        # the record is withheld.
        #
        # Rather than dead-ending at a human handoff, offer a way forward: issue
        # a single-use code addressed to the contact on file. Redeeming it proves
        # the sender is that customer, and later mail is answered normally.
        if context.get("customer") and not context.get("identity_confirmed", True):
            self.stats.replies_suppressed += 1
            customer = context.get("customer") or {}
            customer_id = customer.get("id") or customer.get("customer_id")
            # Where a challenge code must be delivered: the address on file,
            # never the address that wrote in.
            on_file = (
                customer.get("email")
                or customer.get("on_file_email")
                or context.get("on_file_email")
            )
            logger.warning(
                "Auto-reply withheld for %s: identity resolved via %s is "
                "unverified; issuing identity challenge instead.",
                email.sender_email, context.get("matched_via"),
            )
            if not on_file or _norm(on_file) == _norm(email.sender_email):
                # No separate contact to verify against -- a human must handle it.
                return "Held-Identity-Unverified", None

            issued, reason, challenge = self.identity_verifier.issue(
                email.sender_email, str(customer_id), message_id=email.message_id
            )
            if not issued or challenge is None:
                logger.info("Identity challenge not issued for %s: %s",
                            email.sender_email, reason)
                return "Held-Identity-Unverified", None

            first_name = str(customer.get("name") or "there").split()[0]
            body = build_challenge_body(
                first_name=first_name,
                code=challenge.code,
                signature=self.config.autoreply.signature,
            )
            # The challenge is informational: it must not be suppressed by the
            # human-review gate, and it must not be treated as a record answer.
            challenge_decision = replace(
                decision,
                human_review_required=False,
                escalation_needed=False,
                intent="IDENTITY_VERIFICATION",
                suggested_reply=body,
            )
            # Deliver to the contact on file -- never to the requesting
            # address, and never in that address's thread.
            try:
                sent, audit = self.reply_sender.dispatch(
                    email,
                    challenge_decision,
                    body,
                    send_to=on_file,
                    allow_identity_challenge=True,
                )
            except Exception as exc:
                logger.error(
                    "Identity challenge dispatch failed for %s: %s",
                    email.message_id,
                    exc,
                    exc_info=True,
                )
                return "FAILED", None

            status = getattr(audit, "status", None) or (
                "SENT" if sent else "FAILED"
            )
            if sent:
                if str(status).upper() == "SENT":
                    self.stats.replies_sent += 1
                    return "Verification-Challenge-Sent", datetime.now().isoformat()
                # A dry-run is a simulation, not a sent email.
                if str(status).upper().replace("-", "_") in {"DRY_RUN", "SIMULATED"}:
                    self.stats.replies_simulated += 1
                return status, None

            self.stats.replies_suppressed += 1
            logger.warning(
                "Identity challenge not dispatched for %s (%s): %s",
                email.message_id,
                status,
                getattr(audit, "error_details", ""),
            )
            return status, None

        # Global outgoing rate limit.
        allowed, reason = self.safety.can_dispatch_global_reply()
        if not allowed:
            self.stats.replies_suppressed += 1
            logger.warning("Auto-reply suppressed: %s", reason)
            return "Rate-Limited", None

        try:
            reply_body = self.reply_gen.generate_reply(email, decision, context)
        except Exception as exc:
            logger.error("Reply generation failed for %s: %s", email.message_id, exc)
            return "Failed", None
        if not reply_body:
            self.stats.replies_suppressed += 1
            return "Suppressed", None

        try:
            sent, audit = self.reply_sender.dispatch(email, decision, reply_body)
        except Exception as exc:
            logger.error("Reply dispatch failed for %s: %s", email.message_id, exc)
            return "Failed", None

        if sent:
            # A successful dry-run is only a simulation, not a delivered email.
            # Do not count it as sent or mark the thread as replied.
            status = getattr(audit, "status", None) or "Sent"
            if str(status).upper().replace("-", "_") in {"DRY_RUN", "SIMULATED"}:
                self.stats.replies_simulated += 1
                return status, None

            self.safety.record_dispatched_reply(
                email.in_reply_to or email.message_id
            )
            self.stats.replies_sent += 1
            return status, datetime.now().isoformat()

        self.stats.replies_suppressed += 1
        logger.info("Reply not dispatched (%s): %s", getattr(audit, "status", "?"), getattr(audit, "error_details", ""))
        return getattr(audit, "status", "Suppressed"), None

    def _build_triage_record(
        self,
        email: StructuredEmail,
        decision: TriageDecision,
        customer_id: Optional[str],
        reply_status: str,
        reply_sent_at: Optional[str],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # The reasoning model may omit identifiers even when retrieval found
        # the exact claim/policy. Preserve the identifier for human triage only
        # when it is explicitly present in this message or in the decision.
        context = context or {}
        message_text = f"{email.subject or ''} {email.body_text or ''}".lower()

        def as_records(value: Any) -> List[Dict[str, Any]]:
            if isinstance(value, dict):
                return [value]
            if isinstance(value, (list, tuple)):
                return [item for item in value if isinstance(item, dict)]
            return []

        def retrieved_number(key: str, decided: Optional[str]) -> Optional[str]:
            if decided:
                return decided
            records = as_records(
                context.get("claims" if key == "claim_number" else "policies")
            )
            for item in records:
                number = item.get(key)
                if number and str(number).lower() in message_text:
                    return str(number)

            # A claim can identify its linked policy even when the customer
            # asks only about the claim. This is an internal triage association;
            # it does not authorize disclosing the policy to the sender.
            if key == "policy_number":
                claims = as_records(context.get("claims"))
                policies = as_records(context.get("policies"))
                mentioned_claims = [
                    claim for claim in claims
                    if claim.get("claim_number")
                    and str(claim["claim_number"]).lower() in message_text
                ]
                for claim in mentioned_claims:
                    policy_id = claim.get("policy_id")
                    linked = [
                        policy for policy in policies
                        if policy_id and str(policy.get("id")) == str(policy_id)
                    ]
                    if len(linked) == 1 and linked[0].get("policy_number"):
                        return str(linked[0]["policy_number"])

                # When retrieval returned exactly one policy for the resolved
                # customer, it is unambiguous for internal triage.
                if len(policies) == 1 and policies[0].get("policy_number"):
                    return str(policies[0]["policy_number"])
            return None

        claim_number = retrieved_number("claim_number", decision.claim_number)
        policy_number = retrieved_number("policy_number", decision.policy_number)
        return {
            "email_id": str(email.message_id),
            "sender_email": email.sender_email,
            "sender_name": email.sender_name or None,
            "subject": email.subject or "(no subject)",
            "received_date": email.received_date or datetime.now().isoformat(),
            "customer_id": customer_id,
            "policy_number": policy_number,
            "claim_number": claim_number,
            "category": decision.category,
            "intent": decision.intent,
            "priority": decision.priority,
            "urgency_score": decision.urgency_score,
            "sentiment": decision.sentiment,
            "escalation_needed": 1 if decision.escalation_needed else 0,
            "escalation_status": "Escalated" if decision.escalation_needed else "Standard",
            "summary": decision.summary,
            "suggested_reply": decision.suggested_reply or "",
            "routing_desk": decision.routed_to,
            "processing_status": "Completed",
            "reply_status": reply_status,
            "reply_sent_at": reply_sent_at,
        }

    # ------------------------------------------------------------------ #
    # Polling loop
    # ------------------------------------------------------------------ #
    def _fetch_inbox_pages(self, page_size: int = 20) -> List[Dict[str, Any]]:
        """Fetch inbox pages until the current watermark is reached."""
        first_page = self.himalaya.list_inbox(
            page_size=page_size, page=1
        ) or []

        # On first run, preserve the existing behavior of initializing
        # from the current inbox head instead of importing old messages.
        if self.watermark_mgr.current_watermark is None:
            return first_page

        watermark = str(self.watermark_mgr.current_watermark)
        envelopes = []
        seen_ids = set()
        page = 1

        while page <= 100:
            current = first_page if page == 1 else (
                self.himalaya.list_inbox(
                    page_size=page_size, page=page
                ) or []
            )

            new_ids = 0
            for envelope in current:
                msg_id = envelope.get("id")
                if msg_id is None:
                    continue

                msg_id = str(msg_id)
                if msg_id not in seen_ids:
                    seen_ids.add(msg_id)
                    envelopes.append(envelope)
                    new_ids += 1

            if any(
                str(item.get("id")) == watermark
                for item in current
                if item.get("id") is not None
            ):
                break

            if len(current) < page_size:
                break

            if new_ids == 0:
                raise RuntimeError(
                    "Inbox pagination made no progress before reaching "
                    "the watermark; refusing to process an incomplete batch."
                )

            page += 1
        else:
            raise RuntimeError(
                "Inbox pagination exceeded 100 pages before reaching "
                "the watermark; refusing to process an incomplete batch."
            )

        return envelopes
    def _defer(self, msg_id: str) -> None:
        """Persist a pending message ID before retaining it in memory."""
        msg_id = str(msg_id).strip()
        if not msg_id or msg_id == UNKNOWN_MESSAGE_ID:
            return

        self.retry_queue.add(msg_id)
        if msg_id not in self._deferred:
            self._deferred.append(msg_id)

    def poll_cycle(self) -> int:
        """Run one polling cycle. Returns the number of emails durably processed.

        Resilient by construction:
        - A Gmail outage trips the circuit breaker and ends the cycle cleanly
          without consuming the watermark or losing queued work.
        - Individual email failures are isolated: one bad message never aborts
          the batch.
        - Emails that failed transiently are retried on the next cycle.
        """
        self.stats.poll_cycles += 1
        processed = 0

        # ---- 1. Retry anything deferred by a previous cycle ----
        deferred = list(self._deferred)
        for msg_id in deferred:
            outcome = self._process_one(msg_id, defer_on_failure=True)
            if outcome is not None and outcome.status.is_success:
                processed += 1

        # ---- 2. Fetch new envelopes, guarded by the circuit breaker ----
        try:
            envelopes = self.gmail_breaker.call(
                lambda: self._fetch_inbox_pages(page_size=20)
            )
            self.health.record_success("gmail")
        except HimalayaPermanentError as exc:
            # Config/auth problem: retrying will not help. Surface it loudly.
            self.health.record_failure("gmail", str(exc))
            logger.error(
                "Gmail permanent failure (%s): %s -- check the Himalaya config "
                "and credentials; the pipeline will keep polling but cannot read mail.",
                exc.stage,
                exc,
            )
            return processed
        except (HimalayaError, Exception) as exc:
            self.health.record_failure("gmail", str(exc))
            logger.warning("Poll cycle: Gmail fetch failed, will retry next cycle: %s", exc)
            return processed

        envelopes = envelopes or []
        if not envelopes:
            return processed

        # First run: adopt the current head as the watermark so we never replay history.
        if self.watermark_mgr.current_watermark is None:
            self.detector.initialize_starting_watermark(envelopes)
            logger.info(
                "Initialized watermark to current head; skipping %d historical emails.",
                len(envelopes),
            )
            return processed

        new_envelopes = self.detector.filter_new_emails(envelopes)
        logger.info("Poll cycle: %d envelope(s) fetched, %d new.", len(envelopes), len(new_envelopes))

        for env in new_envelopes:
            msg_id = str(env.get("id"))
            sender_addresses = _sender_addresses(env.get("from"))
            if not self.mock_mode and (
                not self.allowed_senders
                or not (sender_addresses & self.allowed_senders)
            ):
                logger.info(
                    "Skipping email %s: sender is not allowlisted.",
                    msg_id,
                )
                continue

            # Persist before processing so a crash cannot lose this email.
            self._defer(msg_id)
            outcome = self._process_one(
                msg_id, defer_on_failure=True, envelope=env
            )
            if outcome is not None and outcome.status.is_success:
                processed += 1
        return processed

    def _process_one(
        self,
        msg_id: str,
        defer_on_failure: bool,
        envelope: Optional[Dict[str, Any]] = None,
    ) -> Optional[ProcessingOutcome]:
        """Fetch and process a single message, isolating all failure modes."""
        try:
            raw_msg = self.himalaya.read_message(msg_id)
        except Exception as exc:
            logger.error("Failed to read message %s: %s", msg_id, exc)
            self.health.record_failure("gmail_read", str(exc))
            if defer_on_failure and is_transient(exc):
                self._defer(msg_id)
            return None
        if not raw_msg:
            logger.warning("Message %s returned empty; skipping.", msg_id)
            return None

        # Combine envelope metadata with the MIME body returned by
        # Himalaya's message-read command.
        if envelope:
            raw_msg = dict(raw_msg)
            raw_msg.setdefault("id", str(msg_id))

            for key in ("from", "to", "subject", "date", "message-id"):
                if not raw_msg.get(key) and envelope.get(key):
                    raw_msg[key] = envelope[key]

            # The preprocessor expects address objects with an "addr" key.
            for key in ("from", "to"):
                value = raw_msg.get(key)
                if isinstance(value, list):
                    raw_msg[key] = [
                        {
                            "name": item.get("name", ""),
                            "addr": item.get("addr") or item.get("email", ""),
                        } if isinstance(item, dict) else item
                        for item in value
                    ]

        # Enforce the allowlist again after reading, including deferred retries.
        sender_addresses = _sender_addresses(raw_msg.get("from"))
        if not self.mock_mode and (
            not self.allowed_senders
            or not (sender_addresses & self.allowed_senders)
        ):
            logger.info(
                "Skipping email %s: sender is not allowlisted.",
                msg_id,
            )
            return None

        # Himalaya's text_body contains MIME part indexes, not body text.
        if isinstance(raw_msg, dict) and "parts" in raw_msg:
            from src.ingestion.himalaya_client import HimalayaClient
            raw_msg = dict(raw_msg)
            raw_msg["body"] = HimalayaClient.extract_text_body(raw_msg)

        outcome = self.process_email(raw_msg)

        # A non-durable outcome means the watermark did not move, so the message
        # will be re-fetched next cycle anyway. We also queue it explicitly so a
        # transient mid-batch failure is retried immediately rather than waiting
        # for the next Gmail poll.
        if defer_on_failure and outcome.status.is_retryable and outcome.email_id:
            self._defer(outcome.email_id)
            logger.info(
                "Deferred email %s for retry (status=%s).",
                outcome.email_id,
                outcome.status.value,
            )
        if (
            getattr(outcome, "should_advance_watermark", False)
            and getattr(outcome, "email_id", None)
            and outcome.email_id != UNKNOWN_MESSAGE_ID
        ):
            self.retry_queue.remove(outcome.email_id)
            self._deferred = [
                item for item in self._deferred
                if item != outcome.email_id
            ]
        return outcome


    def run_once(self) -> int:
        """Run a single poll cycle and return. Ideal for cron-style execution."""
        return self.poll_cycle()

    def run_forever(self, max_cycles: Optional[int] = None) -> PipelineStats:
        """Run the continuous autonomous polling loop."""
        self.is_running = True
        self.stats.started_at = datetime.now().isoformat()
        interval = self.config.system.polling_interval_seconds
        logger.info("Autonomous pipeline started (interval=%ss, dry_run=%s, mock=%s)", interval, self.dry_run, self.mock_mode)

        cycle = 0
        try:
            while self.is_running:
                self.poll_cycle()
                cycle += 1
                if max_cycles is not None and cycle >= max_cycles:
                    break
                # Sleep in short slices so stop() takes effect promptly.
                deadline = time.time() + interval
                while self.is_running and time.time() < deadline:
                    time.sleep(min(0.5, max(0.0, deadline - time.time())))
        except KeyboardInterrupt:
            logger.info("Interrupted by user.")
        finally:
            self.is_running = False
            logger.info("Autonomous pipeline stopped. %s", self.stats)
        return self.stats

    def stop(self) -> None:
        """Request a graceful shutdown of the polling loop."""
        self.is_running = False

    def start_background(self) -> threading.Thread:
        """Run the continuous polling loop on a daemon thread.

        Step 24 requires the system to operate as a continuous autonomous
        *background process*, so long-running callers (the dashboard, a
        supervisor, or a host service) need the loop to be non-blocking.
        `run_forever` occupies its calling thread, which makes it unusable
        from anything that also needs to do work. The thread is a daemon so
        an un-stopped pipeline cannot block interpreter exit; call `stop()`
        to shut it down cleanly, then `join()` to wait for the final cycle.
        """
        if getattr(self, "_thread", None) is not None and self._thread.is_alive():
            raise RuntimeError("pipeline is already running in the background")
        self.is_running = True
        thread = threading.Thread(
            target=self.run_forever, name="autonomous-pipeline", daemon=True
        )
        self._thread = thread
        thread.start()
        return thread

    def join_background(self, timeout: Optional[float] = None) -> None:
        """Wait for the background loop to finish after stop() was requested."""
        thread = getattr(self, "_thread", None)
        if thread is not None:
            thread.join(timeout)
