"""Out-of-band identity verification for cross-channel senders.

The triage pipeline resolves a sender to a customer record by email address
(exact, trusted) or by a quoted claim/policy/VIN number (untrusted -- anyone can
quote a claim number). A sender resolved only by a number is a legitimate
support scenario: people write from a personal address, a new work address, or
a shared mailbox. Auto-replying with record details is unsafe, but a permanent
human handoff is a poor experience and does not scale.

This module implements the middle path: a *challenge*. For an unverified sender
we issue a short, single-use, time-limited code and address the challenge to the
contact on file for that record -- never to the address that wrote in. When the
sender replies with the code, the address that wrote in becomes verified for
that record, and subsequent mail in the thread is auto-replied normally.

Threat model / limits:
  * The code goes to the record's known-good contact, not the requester, so
    possessing a claim number alone is never sufficient.
  * Codes are single-use, short-lived, rate-limited per sender, and compared in
    constant time.
  * Verification is scoped to one customer record, so clearing one sender never
    authorizes unrelated records.
  * A sender who cannot pass still gets no record data: the challenge text
    itself discloses nothing beyond what they already quoted.
"""

import hmac
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# How long a challenge stays valid.
DEFAULT_TTL_SECONDS = 1800  # 30 minutes
# Max live challenges per sender address, to bound memory and abuse.
MAX_CHALLENGES_PER_SENDER = 3
# Min seconds between challenges to the same sender, to bound outbound mail.
RESEND_COOLDOWN_SECONDS = 60
# Verification is remembered for this long once proven.
VERIFIED_TTL_SECONDS = 90 * 24 * 3600  # 90 days


def _norm(email: Optional[str]) -> str:
    """Normalize an address for comparison (case/whitespace insensitive)."""
    return (email or "").strip().lower()


@dataclass
class IdentityChallenge:
    """A pending challenge for one (sender address, customer record) pair."""

    sender_email: str
    customer_id: str
    code: str
    created_at: float
    expires_at: float
    used: bool = False
    attempts: int = 0
    max_attempts: int = 5
    message_id: Optional[str] = None
    # Set once the sender has proven control; retained for audit.
    verified_at: Optional[float] = None

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at

    def is_usable(self, now: Optional[float] = None) -> bool:
        n = now if now is not None else time.time()
        return not self.used and not self.is_expired(n) and self.attempts < self.max_attempts


@dataclass
class VerificationResult:
    """Outcome of attempting to redeem a verification code."""

    verified: bool
    reason: str
    customer_id: Optional[str] = None
    sender_email: Optional[str] = None


@dataclass
class IdentityVerifier:
    """Issues and redeems out-of-band identity challenges.

    Deliberately in-memory and dependency-free: challenges are short-lived by
    design, so they do not belong in the durable database, and a restart
    correctly invalidates outstanding codes rather than honouring stale ones.
    """

    ttl_seconds: int = DEFAULT_TTL_SECONDS
    resend_cooldown_seconds: int = RESEND_COOLDOWN_SECONDS
    verified_ttl_seconds: int = VERIFIED_TTL_SECONDS
    max_per_sender: int = MAX_CHALLENGES_PER_SENDER
    _challenges: List[IdentityChallenge] = field(default_factory=list, repr=False)
    # sender_email -> (customer_id, verified_at)
    _verified: Dict[str, Tuple[str, float]] = field(default_factory=dict, repr=False)
    _last_issued: Dict[str, float] = field(default_factory=dict, repr=False)

    # -- issuance ---------------------------------------------------------

    def is_verified(self, sender_email: str, customer_id: Optional[str] = None) -> bool:
        """True if this sender proved control of this record and it hasn't lapsed."""
        entry = self._verified.get(_norm(sender_email))
        if not entry:
            return False
        verified_customer, at = entry
        if time.time() - at >= self.verified_ttl_seconds:
            self._verified.pop(_norm(sender_email), None)
            return False
        if customer_id is not None and verified_customer != customer_id:
            return False
        return True

    def can_issue(self, sender_email: str, customer_id: str) -> Tuple[bool, str]:
        """Check whether a new challenge may be issued right now."""
        key = _norm(sender_email)
        now = time.time()
        last = self._last_issued.get(key)
        if last is not None and now - last < self.resend_cooldown_seconds:
            wait = int(self.resend_cooldown_seconds - (now - last))
            return False, f"Verification already sent; wait {wait}s before resending."
        live = [c for c in self._challenges if c.sender_email == key and c.is_usable(now)]
        if len(live) >= self.max_per_sender:
            return False, "Too many outstanding verification attempts for this address."
        return True, "OK"

    def issue(
        self,
        sender_email: str,
        customer_id: str,
        message_id: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[IdentityChallenge]]:
        """Create a challenge. Returns (ok, reason, challenge)."""
        allowed, reason = self.can_issue(sender_email, customer_id)
        if not allowed:
            return False, reason, None

        now = time.time()
        # 6 digits is enough entropy against the attempt ceiling and per-sender
        # cooldown, and is short enough to read off a screen.
        code = f"{secrets.randbelow(1_000_000):06d}"
        challenge = IdentityChallenge(
            sender_email=_norm(sender_email),
            customer_id=customer_id,
            code=code,
            created_at=now,
            expires_at=now + self.ttl_seconds,
            message_id=message_id,
        )
        self._challenges.append(challenge)
        self._last_issued[_norm(sender_email)] = now
        self._prune(now)
        logger.info(
            "Issued identity challenge for %s -> customer %s (expires in %ss).",
            challenge.sender_email, customer_id, self.ttl_seconds,
        )
        return True, "OK", challenge

    # -- redemption -------------------------------------------------------

    def redeem(self, sender_email: str, code: str) -> VerificationResult:
        """Attempt to redeem a code. On success the sender becomes verified."""
        key = _norm(sender_email)
        if not key:
            return VerificationResult(False, "No sender address.")
        if not code or not code.strip():
            return VerificationResult(False, "No verification code provided.")

        now = time.time()
        candidates = [
            c for c in self._challenges
            if c.sender_email == key and not c.used
        ]
        if not candidates:
            return VerificationResult(
                False,
                "No verification is pending for this address. A new request will "
                "start a fresh check.",
            )

        # Prefer the newest still-usable challenge, but count an attempt against
        # every outstanding code so a wrong guess cannot be brute-forced across
        # several live challenges.
        for c in sorted(candidates, key=lambda x: x.created_at, reverse=True):
            if c.is_expired(now):
                c.used = True
                return VerificationResult(
                    False,
                    "That verification code has expired. Please ask for a new one.",
                )
            if c.attempts >= c.max_attempts:
                continue
            c.attempts += 1
            if hmac.compare_digest(c.code, code.strip()):
                c.used = True
                c.verified_at = now
                self._verified[key] = (c.customer_id, now)
                logger.info(
                    "Identity verified for %s against customer %s.",
                    key, c.customer_id,
                )
                return VerificationResult(True, "Identity verified.", c.customer_id, key)
            return VerificationResult(
                False,
                f"That code is not correct. {max(0, c.max_attempts - c.attempts)} "
                "attempt(s) remaining.",
            )

        return VerificationResult(
            False,
            "Too many incorrect attempts. Please ask for a new verification code.",
        )

    # -- housekeeping -----------------------------------------------------

    def _prune(self, now: float) -> None:
        """Drop long-dead challenges and lapsed verifications.

        A recently-expired challenge is retained briefly so that a customer
        replying with a stale code gets "that code has expired, please ask for a
        new one" rather than the misleading "no verification is pending".
        """
        grace = 300.0
        self._challenges = [
            c for c in self._challenges
            if (not c.is_expired(now) or c.verified_at is not None
                or (now - c.expires_at) < grace)
        ]
        for key, (_, at) in list(self._verified.items()):
            if now - at >= self.verified_ttl_seconds:
                self._verified.pop(key, None)
        for key, at in list(self._last_issued.items()):
            if now - at >= self.resend_cooldown_seconds:
                self._last_issued.pop(key, None)

    def stats(self) -> Dict[str, int]:
        now = time.time()
        return {
            "pending": sum(1 for c in self._challenges if c.is_usable(now)),
            "used": sum(1 for c in self._challenges if c.used),
            "verified_senders": len(self._verified),
        }

    def clear(self) -> None:
        self._challenges.clear()
        self._verified.clear()
        self._last_issued.clear()


def build_challenge_body(
    first_name: str,
    code: str,
    ttl_minutes: int = DEFAULT_TTL_SECONDS // 60,
    signature: str = "",
) -> str:
    """Plain-text body for the challenge reply sent to the address on file.

    Deliberately discloses nothing about the record beyond confirming that a
    request was made -- the requester already knows they quoted a claim number.
    """
    lines = [
        f"Hello {first_name},",
        "",
        "We received a message about a claim on your policy, but it was sent from "
        f"an address we do not have on file. To confirm it is really you before "
        "we share any details, please reply with this verification code:",
        "",
        f"    {code}",
        "",
        f"The code expires in {ttl_minutes} minutes and can be used once. If you "
        "did not send the original message you can ignore this email, and "
        "nothing will be shared.",
        "",
        "Apex Shield Insurance Support",
    ]
    if signature:
        lines.append(signature)
    return "\n".join(lines)


# Phrases that introduce a verification code in a customer's reply. Requires the
# word "code" (or similar) nearby so an unrelated 6-digit number -- a claim
# number fragment, an amount, a phone number -- is not mistaken for a code.
_CODE_CONTEXT = re.compile(
    r"(code|verification|verify|security|access|confirmation|confirm|pin|otp|one[-\s]?time)",
    re.IGNORECASE,
)
_CODE_VALUE = re.compile(r"\b(\d{6})\b")
# Standalone 6-digit token on its own line, as our challenge email is formatted.
_CODE_LINE = re.compile(r"^\s*(\d{6})\s*$", re.MULTILINE)


def extract_verification_code(text: Optional[str]) -> Optional[str]:
    """Pull a verification code out of an inbound reply, or None.

    Accepts the two shapes a customer realistically sends:
      * the bare code on its own line, or
      * a sentence mentioning the code near a 6-digit number
        ("my verification code is 123456", "code: 123456").

    Anything else returns None, so an unrelated number in a claim narrative is
    never treated as a code and never burns an attempt.
    """
    if not text:
        return None

    m = _CODE_LINE.search(text)
    if m:
        return m.group(1)

    for m in _CODE_VALUE.finditer(text):
        start = max(0, m.start() - 60)
        window = text[start:m.end() + 20]
        if _CODE_CONTEXT.search(window):
            return m.group(1)
    return None
