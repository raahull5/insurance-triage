"""Persistent retry queue for email processing."""

import json
import logging
import os
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


class RetryQueue:
    """Persist pending message IDs across application restarts."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ids: List[str] = []
        self.load()

    def load(self) -> List[str]:
        """Load pending IDs. Invalid files raise rather than lose work."""
        if not self.path.exists():
            self._ids = []
            return self.pending

        with self.path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list) or not all(
            isinstance(item, str) and item.strip() for item in data
        ):
            raise ValueError(f"Invalid retry queue format: {self.path}")

        self._ids = list(dict.fromkeys(data))
        return self.pending

    @property
    def pending(self) -> List[str]:
        """Return a copy of pending message IDs."""
        return list(self._ids)

    def add(self, message_id: str) -> None:
        """Persist a message ID unless it is already queued."""
        message_id = str(message_id).strip()
        if not message_id:
            raise ValueError("Retry queue message ID cannot be empty")
        if message_id in self._ids:
            return

        self._persist(self._ids + [message_id])

    def remove(self, message_id: str) -> None:
        """Remove an ID only after its processing is resolved."""
        message_id = str(message_id)
        if message_id not in self._ids:
            return
        self._persist([item for item in self._ids if item != message_id])

    def _persist(self, ids: List[str]) -> None:
        """Atomically replace the queue file."""
        temp_path = self.path.with_name(self.path.name + ".tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as file:
                json.dump(ids, file, indent=2)
                file.flush()
                os.fsync(file.fileno())
            temp_path.replace(self.path)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            logger.exception("Failed to persist retry queue")
            raise

        self._ids = ids
