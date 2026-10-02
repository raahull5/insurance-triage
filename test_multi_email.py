
import sqlite3
import tempfile
from pathlib import Path

from src.config import AppConfig
from src.db.database import Database
from src.pipeline.autonomous_runner import AutonomousPipeline
from src.core.resilience import OutcomeStatus


MESSAGE_IDS = ["8"]


def normalize_address(value):
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, dict):
                addr = item.get("addr") or item.get("email") or ""
                name = item.get("name") or ""
                items.append(f"{name} <{addr}>" if name else addr)
            else:
                items.append(str(item))
        return ", ".join(items)

    if isinstance(value, dict):
        addr = value.get("addr") or value.get("email") or ""
        name = value.get("name") or ""
        return f"{name} <{addr}>" if name else addr

    return value


def main():
    with tempfile.TemporaryDirectory(
        prefix="insurance_triage_ai_test_"
    ) as temp:
        root = Path(temp)

        # Start with a fresh, isolated configuration.
        config = AppConfig()
        config.gmail.account_name = "gmail"

        config.system.db_path = root / "test.db"
        config.system.watermark_file = root / "watermark.json"
        config.system.csv_report_path = root / "triage.csv"

        # Safety: do not send emails.
        config.autoreply.enabled = False
        config.autoreply.dry_run = True

        db = Database(config.system.db_path)

        # Use the real AI engine, but retain safe test settings.
        pipeline = AutonomousPipeline(
            config,
            db,
            mock_mode=False,
            dry_run=True,
        )

        print("\n--- REAL AI CLASSIFICATION TEST ---")
        print("Fetching Gmail message:", MESSAGE_IDS[0])

        # Fetch the Gmail envelope.
        envelopes = pipeline.himalaya.list_inbox(
            page_size=50,
            page=1,
        )

        by_id = {
            str(item.get("id")): item
            for item in envelopes
        }

        message_id = MESSAGE_IDS[0]

        if message_id not in by_id:
            raise RuntimeError(
                f"Gmail message {message_id} not found in INBOX"
            )

        # Read the full message.
        raw = pipeline.himalaya.read_message(message_id)

        if not raw:
            raise RuntimeError(
                f"Could not read Gmail message {message_id}"
            )

        # Merge envelope metadata with the message.
        combined = dict(raw)

        for key, value in by_id[message_id].items():
            combined.setdefault(key, value)

        for key in ("from", "to", "cc", "reply_to"):
            if key in combined:
                combined[key] = normalize_address(
                    combined[key]
                )

        # Extract actual message text from MIME parts when present.
        if "parts" in combined:
            from src.ingestion.himalaya_client import HimalayaClient

            combined["body"] = HimalayaClient.extract_text_body(
                combined
            )

        # Run the actual pipeline and real AI analysis.
        # Do not advance the Gmail watermark.
        outcome = pipeline.process_email(
            combined,
            advance_watermark=False,
        )

        print("\n--- PIPELINE OUTCOME ---")
        print("Status:", outcome.status)
        print("Stage:", outcome.stage)
        print("Detail:", outcome.detail)

        # Inspect only the isolated test database.
        conn = sqlite3.connect(str(config.system.db_path))

        try:
            rows = conn.execute(
                """
                SELECT
                    email_id,
                    category,
                    intent,
                    priority,
                    urgency_score,
                    sentiment,
                    escalation_needed,
                    summary,
                    suggested_reply,
                    routing_desk,
                    reply_status
                FROM triage_records
                ORDER BY email_id
                """
            ).fetchall()

            print("\n--- REAL AI CLASSIFICATION RESULT ---")

            if not rows:
                print("No triage record was created.")
            else:
                columns = [
                    "email_id",
                    "category",
                    "intent",
                    "priority",
                    "urgency_score",
                    "sentiment",
                    "escalation_needed",
                    "summary",
                    "suggested_reply",
                    "routing_desk",
                    "reply_status",
                ]

                for row in rows:
                    for column, value in zip(columns, row):
                        print(f"{column}: {value}")
                    print()

            tickets = conn.execute(
                """
                SELECT ticket_number, subject, status
                FROM support_tickets
                ORDER BY ticket_number
                """
            ).fetchall()

            print("--- SUPPORT TICKETS ---")
            for ticket in tickets:
                print(ticket)

            # Basic assertions: these verify processing,
            # not the semantic accuracy of the model.
            assert outcome.status == OutcomeStatus.COMPLETED, (
                f"Pipeline did not complete: {outcome}"
            )

            assert len(rows) == 1, (
                f"Expected 1 triage record, got {len(rows)}"
            )

            decision = rows[0]

            assert decision[2], "AI returned an empty intent"
            assert decision[3], "AI returned an empty priority"
            assert decision[7], "AI returned an empty summary"

            print("\n--- SAFETY VERIFICATION ---")
            print(
                "Auto-replies enabled:",
                config.autoreply.enabled,
            )
            print("Watermark advancement: disabled")
            print("Database: isolated temporary database")

            print("\nAll assertions passed.")
            print("Real AI classification test completed.")

        finally:
            conn.close()


if __name__ == "__main__":
    main()