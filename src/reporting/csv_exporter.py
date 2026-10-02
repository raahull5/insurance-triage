"""CSV Triage Export Pipeline for logging and filtering triaged insurance records."""

import os
import csv
import shutil
import tempfile
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from src.ai.safety import SafetyEngine

logger = logging.getLogger(__name__)

# Exact 15-column schema required by the specification
CSV_COLUMNS = [
    "email_id",
    "sender",
    "subject",
    "received_date",
    "intent",
    "category",
    "priority",
    "urgency_score",
    "sentiment",
    "escalation_status",
    "policy_number",
    "claim_number",
    "routing_desk",
    "reply_status",
    "summary",
]


class CSVExporter:
    """Enterprise CSV triage export pipeline with atomic operations, PII redaction, and filtering."""

    def __init__(self, csv_path: Path):
        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_header()

    def _ensure_header(self) -> None:
        """Create CSV file with standard headers if it does not exist."""
        if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
                writer.writerow(CSV_COLUMNS)

    def _format_row(self, record: Dict[str, Any]) -> List[str]:
        """Normalize, sanitize, and redact PII for each column in the record."""
        sender = record.get("sender") or record.get("sender_email") or ""
        subject = SafetyEngine.redact_pii(record.get("subject", "") or "")
        summary = SafetyEngine.redact_pii(record.get("summary", "") or "")
        received_date = record.get("received_date") or datetime.now(timezone.utc).isoformat()
        
        # Escalation status normalization
        esc_status = record.get("escalation_status")
        if not esc_status or esc_status == "None":
            if record.get("escalation_needed") or record.get("human_review_required"):
                esc_status = "Escalated"
            # Case-insensitive: the dispatcher writes 'SENT' while older rows
            # and dry-run paths use 'Sent'/'Dry-Run'/'DRY_RUN'.
            elif (record.get("reply_status") or "").upper() in ("SENT", "DRY_RUN", "DRY-RUN"):
                esc_status = "Auto-Resolved"
            else:
                esc_status = "Held-for-Review"

        reply_status = record.get("reply_status") or "Suppressed"
        if reply_status.upper() == "DRY_RUN":
            reply_status = "Dry-Run"

        row_dict = {
            "email_id": str(record.get("email_id", "") or ""),
            "sender": str(sender),
            "subject": str(subject),
            "received_date": str(received_date),
            "intent": str(record.get("intent") or record.get("detected_intent") or "GENERAL_QUERY"),
            "category": str(record.get("category", "") or "General Support & Billing"),
            "priority": str(record.get("priority", "") or "Medium"),
            "urgency_score": str(record.get("urgency_score", "5")),
            "sentiment": str(record.get("sentiment", "") or "Neutral"),
            "escalation_status": str(esc_status),
            "policy_number": str(record.get("policy_number", "") or ""),
            "claim_number": str(record.get("claim_number", "") or ""),
            "routing_desk": str(record.get("routing_desk") or record.get("routed_to") or "General Support"),
            "reply_status": str(reply_status),
            "summary": str(summary),
        }

        return [row_dict.get(col, "") for col in CSV_COLUMNS]

    def append_record(self, record: Dict[str, Any]) -> None:
        """Append a single sanitized record to the CSV."""
        self._ensure_header()
        row = self._format_row(record)
        try:
            with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
                writer.writerow(row)
        except Exception as e:
            logger.error(f"Failed to append record {record.get('email_id')} to CSV: {e}")

    def export_all(self, records: List[Dict[str, Any]]) -> None:
        """Atomically overwrite the CSV with all provided records using a temp file."""
        temp_dir = self.csv_path.parent
        fd, temp_path = tempfile.mkstemp(prefix="triage_export_", suffix=".csv", dir=temp_dir)
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
                writer.writerow(CSV_COLUMNS)
                for r in records:
                    writer.writerow(self._format_row(r))

            # Atomic file replacement
            os.replace(temp_path, self.csv_path)
            logger.info(f"Exported {len(records)} records atomically to {self.csv_path}")
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            logger.error(f"Failed atomic export to CSV: {e}")
            raise

    def read_records(self) -> List[Dict[str, str]]:
        """Read all rows from the CSV as a list of dictionaries."""
        if not self.csv_path.exists():
            return []
        with open(self.csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)

    def filter_records(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        priority: Optional[str] = None,
        intent: Optional[str] = None,
        escalation_status: Optional[str] = None,
        sentiment: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Filter CSV records based on query parameters."""
        records = self.read_records()
        filtered = []

        for r in records:
            if priority and r.get("priority", "").lower() != priority.lower():
                continue
            if intent and r.get("intent", "").lower() != intent.lower():
                continue
            if escalation_status and r.get("escalation_status", "").lower() != escalation_status.lower():
                continue
            if sentiment and r.get("sentiment", "").lower() != sentiment.lower():
                continue

            r_date = r.get("received_date", "")
            if start_date and r_date < start_date:
                continue
            if end_date and r_date > end_date:
                continue

            filtered.append(r)

        return filtered
