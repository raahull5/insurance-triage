"""SQLite database connection and session manager.

Step 22 hardening: the pipeline and the dashboard read the same file
concurrently, so connections enable WAL journaling and a busy timeout, and
lock contention is retried with backoff rather than raising.
"""

import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional
from src.db.schema import create_tables
import logging

logger = logging.getLogger(__name__)


class Database:
    """Manages SQLite database connections and lifecycle."""

    #: Retry budget for `database is locked` on writer transactions.
    BUSY_RETRIES = 5
    BUSY_BASE_DELAY = 0.1

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def get_connection(self) -> sqlite3.Connection:
        """Create and return a configured SQLite connection.

        WAL mode lets the dashboard read while the pipeline writes, and
        `busy_timeout` makes concurrent access wait instead of raising
        "database is locked" immediately.
        """
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            # WAL is persistent on the file itself; harmless if already set.
            conn.execute("PRAGMA journal_mode = WAL;")
        except sqlite3.DatabaseError as exc:  # pragma: no cover - exotic filesystems
            logger.warning("Could not enable WAL journaling: %s", exc)
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def initialize(self) -> None:
        """Initialize database tables if they do not exist."""
        conn = self.get_connection()
        try:
            self.with_retry(lambda: create_tables(conn), "schema_init")
        finally:
            conn.close()

    @classmethod
    def with_retry(cls, operation: "callable", stage: str = "db") -> object:
        """Run a write `operation`, retrying transient lock/busy errors.

        This addresses the specific failure mode of the pipeline writing while
        the dashboard reads: SQLite raises `OperationalError: database is
        locked` under contention, which is safe to retry because every writer
        here commits a single atomic statement.
        """
        last_exc: Optional[BaseException] = None
        for attempt in range(1, cls.BUSY_RETRIES + 1):
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                text = str(exc).lower()
                if "locked" not in text and "busy" not in text:
                    raise
                last_exc = exc
                if attempt >= cls.BUSY_RETRIES:
                    break
                delay = cls.BUSY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "SQLite busy during %s (attempt %d/%d): %s -- retrying in %.2fs",
                    stage,
                    attempt,
                    cls.BUSY_RETRIES,
                    exc,
                    delay,
                )
                time.sleep(delay)
        raise last_exc  # type: ignore[misc]
