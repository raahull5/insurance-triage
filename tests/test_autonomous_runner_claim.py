import sqlite3
from pathlib import Path
from unittest.mock import patch

from src.config import (
    AIConfig,
    AppConfig,
    AutoReplyConfig,
    DashboardConfig,
    GmailConfig,
    SystemConfig,
)
from src.db.database import Database
from src.pipeline.autonomous_runner import AutonomousPipeline
from src.ai.reasoning_engine import TriageDecision


class FakeReasoningEngine:
    """Deterministic reasoning engine for the isolated runner test."""

    def analyze(self, *args, **kwargs):
        return TriageDecision(
            category="CLAIMS",
            intent="NEW_CLAIM",
            priority="HIGH",
            urgency_score=80,
            sentiment="NEGATIVE",
            escalation_needed=False,
            escalation_reason="",
            summary="Customer submitted a new motor insurance claim.",
            policy_number="POL-TEST-001",
            claim_number=None,
            routed_to="claims",
            suggested_reply="Your claim has been received.",
            confidence=0.99,
            human_review_required=False,
            raw_response="TEST",
        )


def build_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        system=SystemConfig(
            polling_interval_seconds=1,
            watermark_file=tmp_path / "watermark.json",
            csv_report_path=tmp_path / "triage.csv",
            db_path=tmp_path / "insurance_triage.db",
        ),
        gmail=GmailConfig(
            account_name="test",
            inbox_folder="INBOX",
        ),
        ai=AIConfig(
            model="test",
            temperature=0.0,
            max_tokens=100,
            enable_safety_check=True,
        ),
        autoreply=AutoReplyConfig(
            enabled=False,
            dry_run=True,
        ),
        dashboard=DashboardConfig(),
    )


def seed_customer_vehicle_policy(database: Database):
    conn = database.get_connection()

    conn.execute(
        """
        INSERT INTO customers (id, name, email)
        VALUES (?, ?, ?)
        """,
        (
            "CUST-TEST-001",
            "Test Customer",
            "test@example.com",
        ),
    )

    conn.execute(
        """
        INSERT INTO vehicles (
            id, customer_id, vin, make, model, year,
            license_plate, color
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "VEH-001",
            "CUST-TEST-001",
            "1HGBH41JXMN109186",
            "Honda",
            "City",
            2024,
            "TEST-001",
            "White",
        ),
    )

    conn.execute(
        """
        INSERT INTO policies (
            id, policy_number, customer_id, vehicle_id,
            type, policy_type, status,
            start_date, end_date,
            premium, premium_amount,
            deductible, coverage_limit,
            no_claim_bonus_pct
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "POLICY-ID-001",
            "POL-TEST-001",
            "CUST-TEST-001",
            "VEH-001",
            "AUTO",
            "Comprehensive",
            "ACTIVE",
            "2026-01-01",
            "2026-12-31",
            10000.0,
            10000.0,
            5000.0,
            500000.0,
            0.0,
        ),
    )

    conn.commit()
    conn.close()


def synthetic_email(message_id="runner-claim-001"):
    return {
        "id": message_id,
        "from": [
            {
                "name": "Test Customer",
                "addr": "test@example.com",
            }
        ],
        "to": [
            {
                "name": "Claims",
                "addr": "claims@example.com",
            }
        ],
        "subject": "New motor insurance claim - POL-TEST-001",
        "date": {
            "DateTime": {
                "year": 2026,
                "month": 9,
                "day": 30,
                "hour": 10,
                "minute": 0,
                "second": 0,
                "tz_hour": 0,
                "tz_minute": 0,
                "tz_before_gmt": False,
            }
        },
        "text_body": [0],
        "parts": [
            {
                "headers": [
                    {
                        "name": "from",
                        "value": "Test Customer <test@example.com>",
                    },
                    {
                        "name": "to",
                        "value": "claims@example.com",
                    },
                    {
                        "name": "subject",
                        "value": "New motor insurance claim - POL-TEST-001",
                    },
                    {
                        "name": "date",
                        "value": "2026-09-30",
                    },
                ],
                "body": (
                    "Incident: Vehicle was damaged in an accident.\n"
                    "Damage: Front bumper and headlamp damaged.\n"
                    "Incident Date: 2026-09-30\n"
                    "Policy: POL-TEST-001"
                ),
            }
        ],
    }


def test_autonomous_runner_creates_claim_without_live_llm(tmp_path):
    config = build_config(tmp_path)
    database = Database(config.system.db_path)

    seed_customer_vehicle_policy(database)

    runner = AutonomousPipeline(
        config=config,
        database=database,
        mock_mode=True,
        dry_run=True,
    )

    runner.ai_engine = FakeReasoningEngine()

    outcome = runner.process_email(
        synthetic_email(),
        advance_watermark=False,
    )

    assert outcome.email_id == "runner-claim-001"

    conn = database.get_connection()

    claims = conn.execute(
        "SELECT * FROM claims"
    ).fetchall()

    triage = conn.execute(
        "SELECT * FROM triage_records"
    ).fetchall()

    tickets = conn.execute(
        "SELECT * FROM support_tickets"
    ).fetchall()

    assert len(claims) == 1
    assert claims[0]["policy_id"] == "POLICY-ID-001"
    assert claims[0]["customer_id"] == "CUST-TEST-001"

    assert len(triage) == 1
    assert len(tickets) == 1

    assert runner.stats.replies_sent == 0

    conn.close()


def test_autonomous_runner_does_not_create_duplicate_claim(tmp_path):
    config = build_config(tmp_path)
    database = Database(config.system.db_path)

    seed_customer_vehicle_policy(database)

    runner = AutonomousPipeline(
        config=config,
        database=database,
        mock_mode=True,
        dry_run=True,
    )

    runner.ai_engine = FakeReasoningEngine()

    first = runner.process_email(
        synthetic_email("runner-claim-001"),
        advance_watermark=False,
    )

    second = runner.process_email(
        synthetic_email("runner-claim-002"),
        advance_watermark=False,
    )

    conn = database.get_connection()

    claims = conn.execute(
        "SELECT * FROM claims"
    ).fetchall()

    assert len(claims) == 1

    conn.close()


def test_claim_creation_database_failure_does_not_advance_watermark(
    tmp_path,
):
    config = build_config(tmp_path)
    database = Database(config.system.db_path)

    seed_customer_vehicle_policy(database)

    runner = AutonomousPipeline(
        config=config,
        database=database,
        mock_mode=True,
        dry_run=True,
    )

    runner.ai_engine = FakeReasoningEngine()

    with patch(
        "src.db.repository.InsuranceRepository.create_claim",
        side_effect=sqlite3.OperationalError(
            "database is locked"
        ),
    ):
        outcome = runner.process_email(
            synthetic_email("runner-claim-db-fail"),
        )

    assert outcome.email_id == "runner-claim-db-fail"
    assert outcome.status.value == "FAILED_DATABASE"
    assert outcome.stage == "database_write"
    assert outcome.should_advance_watermark is False
    assert outcome.retryable is True

    assert runner.watermark_mgr.current_watermark is None

    conn = database.get_connection()

    claims = conn.execute(
        "SELECT * FROM claims"
    ).fetchall()

    assert len(claims) == 0

    conn.close()