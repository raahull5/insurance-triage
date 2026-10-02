"""Resilience primitives for the autonomous triage pipeline.

Step 22 of the build specification requires the pipeline to survive Gmail
connection failures, Himalaya errors, malformed emails, missing database
records, invalid LLM responses, database errors, dashboard update failures,
and SMTP sending failures -- without crashing the background process, and
without ever advancing the watermark for an email that was not durably stored.

This module provides the shared building blocks:

* :class:`RetryPolicy`      -- bounded exponential backoff with jitter.
* :class:`CircuitBreaker`   -- trips after repeated failures, half-opens to probe.
* :class:`HealthMonitor`    -- rolling counters for each dependency.
* :class:`ProcessingOutcome`-- the result contract the pipeline must honour.
* :func:`is_transient`      -- classify retryable vs permanent exceptions.
"""

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# --------------------------------------------------------------------------- #
# Exception classification
# --------------------------------------------------------------------------- #
# Network/IO problems that are worth retrying.
TRANSIENT_ERRORS = (
    ConnectionError,
    TimeoutError,
    OSError,
)

# Substrings that mark a transient failure even when the concrete exception
# type is unhelpful (subprocess wrappers, HTTP libraries, sqlite locking).
TRANSIENT_MARKERS = (
    "temporarily unavailable",
    "connection reset",
    "connection refused",
    "connection aborted",
    "timed out",
    "timeout",
    "broken pipe",
    "network is unreachable",
    "try again",
    "rate limit",
    "too many requests",
    "database is locked",
    "database table is locked",
    "could not serialize access",
    "server disconnected",
    "remote end closed",
)

# Substrings that mean "retrying will never help".
PERMANENT_MARKERS = (
    "authentication failed",
    "invalid credentials",
    "authenticationrequired",
    "permission denied",
    "not authorized",
    "mailbox not found",
    "unknown command",
    "no such file",
    "certificate verify failed",
)


#: Consecutive failures after which a dependency is reported unhealthy.
DEFAULT_DEGRADED_THRESHOLD = 3


def is_transient(exc: BaseException) -> bool:
    """Return True when `exc` is worth retrying.

    Permanent failures (auth, bad config, malformed input) must fail fast so a
    broken credential does not cause the pipeline to spin on backoff forever.
    """
    # Typed ingestion errors are authoritative: classify before text sniffing.
    from src.ingestion.himalaya_client import (
        HimalayaPermanentError,
        HimalayaTransientError,
    )

    if isinstance(exc, HimalayaPermanentError):
        return False
    if isinstance(exc, HimalayaTransientError):
        return True

    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    # sqlite3.OperationalError subclasses sqlite3.Error -> Exception, not OSError.
    if exc.__class__.__name__ in ("OperationalError", "InterfaceError", "DatabaseError"):
        text = str(exc).lower()
        return any(m in text for m in ("locked", "busy", "disk i/o", "unavailable"))
    if isinstance(exc, OSError):
        return True

    text = str(exc).lower()
    if any(m in text for m in PERMANENT_MARKERS):
        return False
    return any(m in text for m in TRANSIENT_MARKERS)



# --------------------------------------------------------------------------- #
# Retry
# --------------------------------------------------------------------------- #
@dataclass
class RetryPolicy:
    """Bounded exponential backoff with full jitter."""

    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    multiplier: float = 2.0
    jitter: bool = True

    def delay_for(self, attempt: int) -> float:
        """Delay in seconds before `attempt` (1-based)."""
        raw = self.base_delay * (self.multiplier ** max(0, attempt - 1))
        capped = min(self.max_delay, raw)
        return random.uniform(0, capped) if self.jitter else capped

    def run(
        self,
        func: Callable[..., T],
        *args: Any,
        retry_on: Optional[type] = None,
        **kwargs: Any,
    ) -> T:
        """Call `func`, retrying transient failures up to `max_attempts`."""
        last_exc: Optional[BaseException] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if retry_on and not isinstance(exc, retry_on):
                    raise
                if attempt >= self.max_attempts or not is_transient(exc):
                    raise
                delay = self.delay_for(attempt)
                logger.warning(
                    "Transient failure (attempt %d/%d): %s -- retrying in %.2fs",
                    attempt,
                    self.max_attempts,
                    exc,
                    delay,
                )
                time.sleep(delay)
        # Unreachable: the loop either returns or raises.
        raise last_exc  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Circuit breaker
# --------------------------------------------------------------------------- #
class CircuitState(str, Enum):
    CLOSED = "CLOSED"          # healthy, traffic flows
    OPEN = "OPEN"              # failing, traffic blocked
    HALF_OPEN = "HALF_OPEN"    # probing recovery


class CircuitOpenError(RuntimeError):
    """Raised when a call is rejected because the breaker is open.

    Distinct from a dependency failure: the dependency was never contacted, so
    this must NOT be counted as another failure against the breaker.
    """

    def __init__(self, name: str, retry_in: float) -> None:
        super().__init__(
            f"Circuit '{name}' is open; skipping call (retry in {retry_in:.0f}s)."
        )
        self.name = name
        self.retry_in = retry_in


class CircuitBreaker:
    """Stops hammering a dependency that is clearly down.

    Gmail being unreachable for an hour should produce one log line per
    cooldown window, not a burst of failed SMTP/IMAP attempts every minute.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 300.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.opened_at: Optional[float] = None
        self._lock = threading.Lock()

    def _transition_to_open(self) -> None:
        self.state = CircuitState.OPEN
        self.opened_at = time.time()
        logger.error(
            "Circuit breaker '%s' OPEN after %d consecutive failures. Pausing for %.0fs.",
            self.name,
            self.failure_count,
            self.recovery_timeout,
        )

    def allow_request(self) -> bool:
        """Return True when a call may proceed."""
        with self._lock:
            if self.state is CircuitState.CLOSED:
                return True
            if self.state is CircuitState.OPEN:
                elapsed = time.time() - (self.opened_at or 0)
                if elapsed >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    logger.info("Circuit breaker '%s' HALF_OPEN -- probing recovery.", self.name)
                    return True
                return False
            return True  # HALF_OPEN: let a single probe through

    def record_success(self) -> None:
        with self._lock:
            self.failure_count = 0
            self.state = CircuitState.CLOSED
            self.opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self.failure_count += 1
            if self.state is CircuitState.HALF_OPEN or self.failure_count >= self.failure_threshold:
                self._transition_to_open()

    def is_open(self) -> bool:
        return self.state is CircuitState.OPEN and not self.allow_request()

    def reset(self) -> None:
        with self._lock:
            self.failure_count = 0
            self.state = CircuitState.CLOSED
            self.opened_at = None

    @property
    def retry_in(self) -> float:
        """Seconds until the breaker will permit a probe."""
        if self.state is CircuitState.CLOSED:
            return 0.0
        return max(0.0, self.recovery_timeout - (time.time() - (self.opened_at or 0)))

    def call(self, func, *args, **kwargs):
        """Invoke `func` through the breaker.

        Returns the function's result. On success the failure count resets; on
        a transient or permanent exception the breaker records the failure and
        re-raises. If the breaker is open, `CircuitOpenError` is raised *without*
        calling `func` and without incrementing the failure count.
        """
        if not self.allow_request():
            raise CircuitOpenError(self.name, self.retry_in)

        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            # Imported lazily to keep this module free of ingestion imports.
            from src.ingestion.himalaya_client import HimalayaPermanentError

            # A permanent failure (bad credentials) will not heal on its own, so
            # trip immediately rather than waiting for the full threshold.
            if isinstance(exc, HimalayaPermanentError):
                with self._lock:
                    self.failure_count = max(self.failure_count, self.failure_threshold)
                    self._transition_to_open()
                raise
            # Unknown errors only count against the dependency when they look
            # transient, so an application bug never opens the circuit.
            if is_transient(exc):
                self.record_failure()
            raise

        self.record_success()
        return result


# --------------------------------------------------------------------------- #
# Health monitoring
# --------------------------------------------------------------------------- #
@dataclass
class DependencyHealth:
    name: str
    total_calls: int = 0
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_success_at: Optional[str] = None
    last_error_at: Optional[str] = None
    last_error: str = ""

    @property
    def success_rate(self) -> float:
        return (self.successes / self.total_calls) if self.total_calls else 0.0

    @property
    def healthy(self) -> bool:
        """A dependency is healthy while its failure streak is below threshold.

        A dependency with no recorded calls is healthy: nothing has gone wrong
        yet, which is not the same as having failed.
        """
        return self.consecutive_failures < DEFAULT_DEGRADED_THRESHOLD

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "total_calls": self.total_calls,
            "successes": self.successes,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "success_rate": round(self.success_rate, 4),
            "last_success_at": self.last_success_at,
            "last_error_at": self.last_error_at,
            "last_error": self.last_error,
        }


class HealthMonitor:
    """Thread-safe rolling health counters per dependency."""

    def __init__(self) -> None:
        self._health: Dict[str, DependencyHealth] = {}
        self._lock = threading.Lock()

    def _slot(self, name: str) -> DependencyHealth:
        if name not in self._health:
            self._health[name] = DependencyHealth(name=name)
        return self._health[name]

    def record_success(self, name: str) -> None:
        from datetime import datetime

        with self._lock:
            h = self._slot(name)
            h.total_calls += 1
            h.successes += 1
            h.consecutive_failures = 0
            h.last_success_at = datetime.now().isoformat()

    def record_failure(self, name: str, error: str = "") -> None:
        from datetime import datetime

        with self._lock:
            h = self._slot(name)
            h.total_calls += 1
            h.failures += 1
            h.consecutive_failures += 1
            h.last_error_at = datetime.now().isoformat()
            h.last_error = error[:500]

    def get(self, name: str) -> DependencyHealth:
        with self._lock:
            return self._slot(name)

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {name: h.to_dict() for name, h in self._health.items()}

    def is_healthy(self, name: str, max_consecutive_failures: int = 10) -> bool:
        return self.get(name).consecutive_failures < max_consecutive_failures

    def reset(self) -> None:
        with self._lock:
            self._health.clear()


# --------------------------------------------------------------------------- #
# Processing outcome contract
# --------------------------------------------------------------------------- #
class OutcomeStatus(str, Enum):
    """Terminal state of one email's trip through the pipeline.

    A status is *durable* when the email's outcome is safely recorded in
    SQLite, meaning the watermark may advance and the message must never be
    re-processed. Every other status leaves the watermark untouched so the next
    cycle retries the email.
    """

    COMPLETED = "COMPLETED"
    ARCHIVED_SPAM = "ARCHIVED_SPAM"
    HELD_FOR_REVIEW = "HELD_FOR_REVIEW"
    FAILED_GMAIL = "FAILED_GMAIL"
    FAILED_DATABASE = "FAILED_DATABASE"
    FAILED_LLM = "FAILED_LLM"
    FAILED_REPLY = "FAILED_REPLY"
    FAILED_MALFORMED = "FAILED_MALFORMED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"

    @property
    def is_durable(self) -> bool:
        """True when the outcome is recorded and the watermark may advance."""
        return self in DURABLE_OUTCOMES

    @property
    def is_retryable(self) -> bool:
        """True when re-processing the email later could succeed.

        Malformed input and hard validation errors are permanent: retrying
        them just burns cycles.
        """
        return self in RETRYABLE_OUTCOMES

    @property
    def is_success(self) -> bool:
        """True only when the email was fully handled.

        A duplicate skip is durable but was not new work, so it is not a
        success. A failed auto-reply is likewise not a pipeline success: the
        triage result is stored, but a human still has to respond.
        """
        return self is OutcomeStatus.COMPLETED


#: Statuses after which the watermark MUST advance: the email's fate is
#: durably recorded, so re-processing it would be a duplicate.
DURABLE_OUTCOMES = frozenset(
    {
        OutcomeStatus.COMPLETED,
        OutcomeStatus.ARCHIVED_SPAM,
        OutcomeStatus.HELD_FOR_REVIEW,
        OutcomeStatus.SKIPPED_DUPLICATE,
        # Unparseable input can never succeed later. Its failure is recorded so
        # the watermark can move past it instead of retrying forever.
        OutcomeStatus.FAILED_MALFORMED,
    }
)

#: Statuses worth retrying on a later cycle: transient conditions only.
RETRYABLE_OUTCOMES = frozenset(
    {
        OutcomeStatus.FAILED_GMAIL,
        OutcomeStatus.FAILED_DATABASE,
        OutcomeStatus.FAILED_LLM,
        OutcomeStatus.FAILED_REPLY,
    }
)


@dataclass
class ProcessingOutcome:
    """Structured result of processing a single email."""

    status: OutcomeStatus
    email_id: str
    detail: str = ""
    stage: str = ""
    retryable: bool = False
    should_advance_watermark: bool = False
    context: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Default policy: a durable outcome always advances the watermark.
        if self.status.is_durable:
            self.should_advance_watermark = True
        # Retryability is implied by the status unless explicitly overridden.
        if not self.retryable:
            self.retryable = self.status.is_retryable

    @property
    def is_durable(self) -> bool:
        return self.status.is_durable

    @property
    def is_success(self) -> bool:
        """True only when the email was fully handled.

        Note a failed auto-reply is *not* a pipeline failure: the triage result
        is stored and the message is durable, it simply waits for a human.
        """
        return self.status.is_success

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "email_id": self.email_id,
            "detail": self.detail,
            "stage": self.stage,
            "retryable": self.retryable,
            "should_advance_watermark": self.should_advance_watermark,
            "is_durable": self.is_durable,
        }
