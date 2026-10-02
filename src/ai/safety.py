"""Safety Engine: loop prevention, spam scoring, rate limiting, and PII redaction."""

import re
import time
import logging
from typing import Dict, Any, Tuple, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

AUTOMATED_HEADERS = [
    "list-unsubscribe",
    "precedence",
    "x-autoreply",
    "auto-submitted",
    "x-auto-response-suppress",
]

AUTOMATED_SENDER_PATTERNS = [
    r"^no-?reply@",
    r"^donotreply@",
    r"^mailer-daemon@",
    r"^postmaster@",
    r"^notifications?@",
    r"^automated@",
    r"^bounce[s]?@",
    r"^newsletter@",
    r"^system@",
    r"^alert[s]?@",
]

SPAM_KEYWORDS = [
    "casino",
    "lottery",
    "winner",
    "crypto investment",
    "viagra",
    "weight loss",
    "wire transfer to claim",
    "prince of",
    "100% free guaranteed",
    "make money fast",
    "claim your prize",
    "work from home earn $$$",
    "bitcoin reward",
    # Prize/phishing lures. Without these, a mailer writing "claim your reward"
    # instead of the exact phrase "claim your prize" scores only 0.25 and sails
    # through the 0.6 threshold while still being unambiguous spam.
    "congratulations",
    "you have won",
    "you've won",
    "you are a winner",
    "claim your reward",
    "claim your gift",
    "claim your bonus",
    "cash prize",
    "million dollar",
    "millionaire",
    "click here to claim",
    "act now",
    "limited time only",
    "urgent action required",
    "send your bank details",
    "verify your account immediately",
    "your account has been suspended",
]

# Sensitive PII Patterns
SSN_PATTERN = r"\b\d{3}-\d{2}-\d{4}\b"
CREDIT_CARD_PATTERN = r"\b(?:\d{4}[- ]?){3}\d{4}\b"
BANK_ACCOUNT_PATTERN = r"\b(?:acc(?:ount)?\.?\s*(?:no|number)?\s*[:#]?\s*)(\d{8,17})\b"
API_KEY_PATTERN = r"\b(?:sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{20,}|AIza[0-9A-Za-z-_]{35})\b"


@dataclass
class SafetyCheckResult:
    """Outcome of safety, spam, loop, and rate limit checks."""
    is_safe: bool
    is_spam: bool
    is_automated: bool
    is_rate_limited: bool
    thread_loop_detected: bool
    spam_score: float
    violations: List[str] = field(default_factory=list)
    action: str = "PROCEED"  # PROCEED, ARCHIVE_SPAM, SUPPRESS_LOOP, RATE_LIMIT_HOLD


class SafetyEngine:
    """Comprehensive safety layer protecting system integrity and user data."""

    MAX_REPLIES_PER_THREAD: int = 3
    MAX_SENDER_EMAILS_PER_HOUR: int = 5
    MAX_GLOBAL_DISPATCH_PER_HOUR: int = 60

    def __init__(
        self,
        max_replies_per_thread: int = MAX_REPLIES_PER_THREAD,
        max_sender_per_hour: int = MAX_SENDER_EMAILS_PER_HOUR,
        max_global_per_hour: int = MAX_GLOBAL_DISPATCH_PER_HOUR,
    ):
        self.max_replies_per_thread = max_replies_per_thread
        self.max_sender_per_hour = max_sender_per_hour
        self.max_global_per_hour = max_global_per_hour

        # Sliding window trackers
        # sender_email -> list of timestamps
        self._sender_history: Dict[str, List[float]] = {}
        # thread_id -> count of auto-replies
        self._thread_reply_counts: Dict[str, int] = {}
        # global dispatch timestamps
        self._global_dispatch_history: List[float] = []

    # 1. PII Redaction
    @classmethod
    def redact_pii(cls, text: str) -> str:
        """Redact SSNs, credit cards, bank accounts, and API tokens from text."""
        if not text:
            return ""
        sanitized = re.sub(SSN_PATTERN, "[REDACTED-SSN]", text)
        sanitized = re.sub(CREDIT_CARD_PATTERN, "[REDACTED-CARD]", sanitized)
        sanitized = re.sub(BANK_ACCOUNT_PATTERN, r"acc [REDACTED-ACCOUNT]", sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(API_KEY_PATTERN, "[REDACTED-KEY]", sanitized)
        return sanitized

    # 2. Spam & Automated Header Checks
    @classmethod
    def calculate_spam_score(cls, sender_email: str, subject: str, body: str, headers: Dict[str, Any]) -> float:
        """Calculate spam score from 0.0 (clean) to 1.0 (definite spam)."""
        score = 0.0
        # The sender carries as much spam signal as the prose: bulk mailers
        # routinely use clean, keyword-free subjects to slip past filters.
        text = f"{sender_email} {subject} {body}".lower()

        # Keyword density
        kw_matches = sum(1 for kw in SPAM_KEYWORDS if kw in text)
        score += min(0.6, kw_matches * 0.25)

        # Suspicious sender domain (non-freemail, hyphen-heavy, or spam TLDs)
        domain = (sender_email or "").lower().rsplit("@", 1)[-1]
        if domain.endswith((".biz", ".info", ".click", ".xyz", ".top", ".gq", ".tk", ".cf")):
            score += 0.35
        if re.search(r"\d{2,}[a-z0-9]*\.(com|net|org|info|biz)", domain):
            score += 0.2

        # Link count
        links = re.findall(r"https?://[^\s]+", body)
        if len(links) >= 4:
            score += 0.3
        elif len(links) >= 2:
            score += 0.15

        # Check for IP address in links
        if re.search(r"https?://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", body):
            score += 0.4

        # Excessive uppercase / shout — check subject AND body, since phishing
        # subjects are almost always fully capitalized even with a plain body.
        for chunk in (subject or "", body or ""):
            if len(chunk) > 20 and (sum(1 for c in chunk if c.isupper()) / len(chunk)) > 0.45:
                score += 0.2
                break

        return min(1.0, score)

    @classmethod
    def is_spam_or_automated(
        cls, sender_email: str, subject: str, body: str, headers: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Check if email matches spam or automated sender patterns."""
        sender_lower = (sender_email or "").strip().lower()
        subject_lower = (subject or "").lower()

        # Check automated headers (case-insensitive)
        headers_lower = {k.lower(): str(v).lower() for k, v in headers.items()}
        for h in AUTOMATED_HEADERS:
            val = headers_lower.get(h, "")
            if val in ["auto-replied", "auto-generated", "bulk", "junk", "list"] or (h in headers_lower):
                return True, f"Automated email header detected: {h}={val or 'present'}"

        # Check automated sender patterns
        for pattern in AUTOMATED_SENDER_PATTERNS:
            if re.search(pattern, sender_lower):
                return True, f"Automated sender pattern match: {pattern}"

        # Spam score threshold
        spam_score = cls.calculate_spam_score(sender_email, subject, body, headers)
        if spam_score >= 0.6:
            return True, f"High spam score: {spam_score:.2f}"

        return False, "Clean"

    # 3. Comprehensive Evaluation
    def check_incoming_email(
        self,
        sender_email: str,
        subject: str,
        body: str,
        headers: Dict[str, Any],
        thread_id: Optional[str] = None,
    ) -> SafetyCheckResult:
        """Evaluate incoming email against spam, automated loop, and rate limit rules."""
        violations: List[str] = []
        now = time.time()
        sender_lower = (sender_email or "").strip().lower()

        # 1. Spam & Automated Sender Check
        is_automated, auto_reason = self.is_spam_or_automated(sender_email, subject, body, headers)
        spam_score = self.calculate_spam_score(sender_email, subject, body, headers)
        is_spam = spam_score >= 0.6

        if is_automated or is_spam:
            violations.append(auto_reason)
            return SafetyCheckResult(
                is_safe=False,
                is_spam=is_spam,
                is_automated=is_automated,
                is_rate_limited=False,
                thread_loop_detected=False,
                spam_score=spam_score,
                violations=violations,
                action="ARCHIVE_SPAM",
            )

        # 2. Sender Rate Limit Check
        timestamps = self._sender_history.get(sender_lower, [])
        # Filter timestamps to last 3600 seconds (1 hour)
        timestamps = [t for t in timestamps if now - t < 3600.0]
        self._sender_history[sender_lower] = timestamps

        if len(timestamps) >= self.max_sender_per_hour:
            msg = f"Per-sender rate limit exceeded ({len(timestamps)}/hr > {self.max_sender_per_hour}/hr)"
            violations.append(msg)
            return SafetyCheckResult(
                is_safe=False,
                is_spam=False,
                is_automated=False,
                is_rate_limited=True,
                thread_loop_detected=False,
                spam_score=spam_score,
                violations=violations,
                action="RATE_LIMIT_HOLD",
            )

        # 3. Thread Loop Prevention Check
        if thread_id:
            count = self._thread_reply_counts.get(thread_id, 0)
            if count >= self.max_replies_per_thread:
                msg = f"Thread auto-reply loop limit reached ({count}/{self.max_replies_per_thread} replies sent to thread {thread_id})"
                violations.append(msg)
                return SafetyCheckResult(
                    is_safe=False,
                    is_spam=False,
                    is_automated=False,
                    is_rate_limited=False,
                    thread_loop_detected=True,
                    spam_score=spam_score,
                    violations=violations,
                    action="SUPPRESS_LOOP",
                )

        # Record incoming timestamp
        self._sender_history[sender_lower].append(now)

        return SafetyCheckResult(
            is_safe=True,
            is_spam=False,
            is_automated=False,
            is_rate_limited=False,
            thread_loop_detected=False,
            spam_score=spam_score,
            violations=[],
            action="PROCEED",
        )

    def can_dispatch_global_reply(self) -> Tuple[bool, str]:
        """Check global outgoing rate limits."""
        now = time.time()
        self._global_dispatch_history = [t for t in self._global_dispatch_history if now - t < 3600.0]
        if len(self._global_dispatch_history) >= self.max_global_per_hour:
            return False, f"Global dispatch limit exceeded ({self.max_global_per_hour}/hr)."
        return True, "OK"

    def record_dispatched_reply(self, thread_id: Optional[str] = None) -> None:
        """Record an outgoing reply for global rate limiting and thread loop counts."""
        now = time.time()
        self._global_dispatch_history.append(now)
        if thread_id:
            self._thread_reply_counts[thread_id] = self._thread_reply_counts.get(thread_id, 0) + 1

    @classmethod
    def should_escalate_to_human(cls, intent: str, priority: str, sentiment: str) -> bool:
        """Determine if request requires human escalation."""
        critical_intents = ["COMPLAINT", "FRAUD_SUSPICION", "CLAIM_REJECTION", "CLAIM_DELAY", "CLAIM_REJECTION_DISPUTE"]
        if intent in critical_intents:
            return True
        if str(priority).lower() == "critical":
            return True
        if str(sentiment).lower() in ["furious", "angry", "hostile"]:
            return True
        return False
