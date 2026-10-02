"""New email detection with persistent watermark tracking and duplicate prevention."""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


def is_id_greater(new_id: str, current_id: str) -> bool:
    """Compare two message IDs whether new_id > current_id."""
    if not new_id or not current_id:
        return False
    # If both are numeric, perform numeric comparison
    if str(new_id).isdigit() and str(current_id).isdigit():
        return int(new_id) > int(current_id)
    # Alphanumeric fallback
    return str(new_id) > str(current_id)


class WatermarkManager:
    """Manages the persistent watermark state in watermark.json to prevent duplicate and historical processing."""

    def __init__(self, watermark_path: Path):
        self.watermark_path = Path(watermark_path)
        self.watermark_path.parent.mkdir(parents=True, exist_ok=True)
        self._watermark_id: Optional[str] = None
        self._processed_count: int = 0
        self.load()

    def load(self) -> Optional[str]:
        """Load watermark state from JSON file."""
        if self.watermark_path.exists():
            try:
                with open(self.watermark_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._watermark_id = str(data.get("last_processed_id", "")) or None
                    self._processed_count = int(data.get("processed_count", 0))
            except Exception as e:
                logger.error(f"Error loading watermark file: {e}")
                self._watermark_id = None
                self._processed_count = 0
        return self._watermark_id

    def save(self, message_id: str) -> None:
        """Atomically persist a monotonic watermark update."""
        message_id = str(message_id)
        if not message_id:
            raise ValueError("Watermark message ID cannot be empty")

        if self._watermark_id is not None and not is_id_greater(
            message_id, self._watermark_id
        ):
            return

        next_count = self._processed_count + 1
        payload = {
            "last_processed_id": message_id,
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "processed_count": next_count,
        }
        temp_path = self.watermark_path.with_name(
            self.watermark_path.name + ".tmp"
        )

        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                import os
                os.fsync(f.fileno())
            temp_path.replace(self.watermark_path)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            logger.exception("Error saving watermark file")
            raise

        self._watermark_id = message_id
        self._processed_count = next_count
        logger.info(
            "Watermark advanced to %s (total processed: %s)",
            self._watermark_id,
            self._processed_count,
        )

    @property
    def current_watermark(self) -> Optional[str]:
        return self._watermark_id

    @property
    def processed_count(self) -> int:
        return self._processed_count

    def reset(self) -> None:
        """Reset watermark state."""
        self._watermark_id = None
        self._processed_count = 0
        if self.watermark_path.exists():
            try:
                self.watermark_path.unlink()
            except Exception as e:
                logger.error(f"Error removing watermark file: {e}")


class EmailDetector:
    """Detects new incoming emails strictly greater than watermark."""

    def __init__(self, watermark_manager: WatermarkManager):
        self.watermark = watermark_manager

    def find_highest_id(self, envelopes: List[Dict[str, Any]]) -> Optional[str]:
        """Find the highest message ID in a list of envelopes."""
        if not envelopes:
            return None
        highest_id = str(envelopes[0].get("id", ""))
        for env in envelopes[1:]:
            eid = str(env.get("id", ""))
            if is_id_greater(eid, highest_id):
                highest_id = eid
        return highest_id or None

    def initialize_starting_watermark(self, envelopes: List[Dict[str, Any]]) -> None:
        """Establish starting watermark from the current inbox state on first run."""
        if self.watermark.current_watermark is None and envelopes:
            highest_id = self.find_highest_id(envelopes)
            if highest_id:
                self.watermark.save(highest_id)
                logger.info(f"Established starting watermark at email ID {highest_id} (ignoring historical messages)")

    def filter_new_emails(self, envelopes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter only emails with an ID strictly greater than the watermark, in ascending order."""
        curr = self.watermark.current_watermark
        if curr is None:
            # If no watermark exists yet, do not process existing emails until initialized
            return []

        new_emails = []
        for env in envelopes:
            env_id = str(env.get("id", ""))
            if is_id_greater(env_id, curr):
                new_emails.append(env)

        # Sort new emails ascending by ID so oldest new email is triaged first
        if all(str(e.get("id", "")).isdigit() for e in new_emails):
            new_emails.sort(key=lambda x: int(x.get("id", 0)))
        else:
            new_emails.sort(key=lambda x: str(x.get("id", "")))

        return new_emails
