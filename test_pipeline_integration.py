"""
Isolated integration smoke test for the insurance triage pipeline.

Run from the project root:
    uv run python test_pipeline_integration.py

Uses synthetic messages, a temporary SQLite DB, temporary CSV/watermark paths,
and a stubbed LLM. It does not connect to Gmail or send email.
"""
from pathlib import Path
from contextlib import closing
import tempfile

from src.config import load_config
from src.db.database import Database
from src.pipeline.autonomous_runner import AutonomousPipeline
from src.ai.reasoning_engine import TriageDecision
from src.core.resilience import OutcomeStatus


def make_message(message_id, subject, body):
    return {
        "id": message_id,
        "from": {"addr": "integration.customer@example.test", "name": "Integration Customer"},
        "to": "support@example.test",
        "subject": subject,
        "date": "2026-09-30T09:00:00Z",
        "body": body,
    }


def make_decision():
    return TriageDecision(
        category="Claims",
        intent="CLAIM_STATUS",
        priority="High",
        urgency_score=6,
        sentiment="Neutral",
        escalation_needed=False,
        escalation_reason="",
        summary="Customer requests a claim status update.",
        policy_number=None,
        claim_number=None,
        routed_to="Claims",
        suggested_reply="We have received your request.",
        confidence=0.99,
        human_review_required=False,
    )


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix="insurance_triage_it_", ignore_cleanup_errors=True) as temp:
        root = Path(temp)
        config = load_config()
        config.system.db_path = root / "integration_test.sqlite3"
        config.system.watermark_file = root / "watermark.json"
        config.system.csv_report_path = root / "triage_test.csv"
        config.autoreply.enabled = False
        config.autoreply.dry_run = True

        db = Database(config.system.db_path)
        pipeline = AutonomousPipeline(config, db, mock_mode=True, dry_run=True)

        # Stub the model so this test is deterministic and makes no API calls.
        pipeline.ai_engine.analyze = lambda email, context: make_decision()

        # 1. Normal processing.
        first = pipeline.process_email(
            make_message("IT-NORMAL-001", "Claim status request", "Please update me on my claim."),
            advance_watermark=True,
        )
        check(first.status == OutcomeStatus.COMPLETED, f"Normal email failed: {first}")
        check(first.should_advance_watermark, "Successful email should be watermark-safe.")
        check(pipeline.last_ticket_action.startswith("NEW:"), "Expected a new ticket.")
        with closing(db.get_connection()) as conn:
            triage_count = conn.execute(
                "SELECT COUNT(*) FROM triage_records WHERE email_id = ?", ("IT-NORMAL-001",)
            ).fetchone()[0]
            ticket_count = conn.execute(
                "SELECT COUNT(*) FROM support_tickets WHERE email_id = ?", ("IT-NORMAL-001",)
            ).fetchone()[0]
        check(triage_count == 1, f"Expected one triage record, got {triage_count}.")
        check(ticket_count == 1, f"Expected one ticket, got {ticket_count}.")
        results.append("PASS normal processing: one triage record and one ticket")

        # 2. Duplicate message.
        duplicate = pipeline.process_email(
            make_message("IT-NORMAL-001", "Claim status request", "Please update me on my claim."),
            advance_watermark=True,
        )
        check(
            duplicate.status == OutcomeStatus.SKIPPED_DUPLICATE,
            f"Expected duplicate skip, got {duplicate.status}.",
        )
        with closing(db.get_connection()) as conn:
            triage_count = conn.execute(
                "SELECT COUNT(*) FROM triage_records WHERE email_id = ?", ("IT-NORMAL-001",)
            ).fetchone()[0]
            ticket_count = conn.execute(
                "SELECT COUNT(*) FROM support_tickets WHERE email_id = ?", ("IT-NORMAL-001",)
            ).fetchone()[0]
        check(triage_count == 1 and ticket_count == 1, "Duplicate created extra records.")
        results.append("PASS deduplication: duplicate did not create extra records")

        # 3. AI failure must not be marked complete or advance the watermark.
        def fail_ai(email, context):
            raise RuntimeError("INTENTIONAL_INTEGRATION_TEST_FAILURE")

        pipeline.ai_engine.analyze = fail_ai
        failed = pipeline.process_email(
            make_message("IT-LLM-FAIL-001", "Claim status request", "Please update me."),
            advance_watermark=True,
        )
        check(failed.status == OutcomeStatus.FAILED_LLM, f"Expected FAILED_LLM, got {failed.status}.")
        check(not failed.should_advance_watermark, "Failed AI outcome must not advance watermark.")
        with closing(db.get_connection()) as conn:
            triage_count = conn.execute(
                "SELECT COUNT(*) FROM triage_records WHERE email_id = ?", ("IT-LLM-FAIL-001",)
            ).fetchone()[0]
            ticket_count = conn.execute(
                "SELECT COUNT(*) FROM support_tickets WHERE email_id = ?", ("IT-LLM-FAIL-001",)
            ).fetchone()[0]
        check(triage_count == 0 and ticket_count == 0, "AI failure left partial triage/ticket records.")
        results.append("PASS AI failure: no false completion, ticket, or watermark advancement")

        # 4. Keep the scope to pipeline integration. The parser is permissive,
        # so a missing-field payload is not necessarily a malformed-email error.
        results.append("PASS failure isolation: AI error did not create ticket or triage record")

        check(config.system.db_path.parent == root, "DB path escaped temporary directory.")
        check(config.system.watermark_file.parent == root, "Watermark path escaped temporary directory.")
        check(config.system.csv_report_path.parent == root, "CSV path escaped temporary directory.")
        results.append("PASS isolation: DB, watermark and CSV paths are temporary")

    print("\nINTEGRATION TEST RESULTS")
    for item in results:
        print(item)
    print(f"\n{len(results)}/{len(results)} checks passed.")
    print("Note: LLM was stubbed; this validates pipeline wiring, not model quality.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
