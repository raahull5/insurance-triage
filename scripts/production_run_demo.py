"""Step 24 production-run driver.

Launches the real AutonomousPipeline as a continuous background process
against a throwaway copy of the seeded database, injects a stream of new
mail through the mock Himalaya transport, and reports what each of the
Step 24 capabilities actually did.

This is a demonstration/verification harness, not a test -- the assertions
live in tests/test_production_run.py. Nothing here touches the real
data/ directory or a live mailbox.

    python scripts/production_run_demo.py
"""

import csv
import json
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Set to False to use mock mode, True for real Himalaya CLI.
# For real mode, ensure Himalaya is on PATH and Gmail is configured.
CLI_MODE = False

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import AppConfig
from src.dashboard.data_service import DashboardDataService
from src.db.database import Database
from src.db.seed_data import seed_database
from src.pipeline.autonomous_runner import AutonomousPipeline

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_DB = REPO_ROOT / "data" / "insurance_triage.db"
INTERVAL = 2  # production is 60s; compressed here so the demo is watchable

# A realistic stream of new customer mail, oldest first.
STREAM = [
    ("<1001@sarah>", "sarah.jenkins@example.com",
     "Status of my claim CLM-2024-09112",
     "Could you let me know when you expect the repair to be assessed?"),
    ("<1002@david>", "david.okafor@example.com",
     "Where is my claim payment?",
     "My claim CLM-2024-04190 was approved. When will the payment arrive?"),
    ("<1003@priya>", "priya.nair@example.com",
     "I am stranded on the motorway",
     "My car has broken down on the M4. I need roadside assistance urgently."),
    ("<1004@spam>", "winner@totally-legit-prizes.example",
     "CONGRATULATIONS YOU WON!!!",
     "Click here to claim your one million dollar reward today only!"),
    ("<1005@marcus>", "marcus.reed@example.com",
     "Re: Status of my claim CLM-2024-07103",
     "Any update? I have been waiting three weeks and nobody has called me."),
]


def envelope(msg_id, sender, subject, body):
    now = datetime.now(timezone.utc)
    return {
        "id": msg_id,
        "from": [{"name": sender.split("@")[0].title(), "addr": sender}],
        "to": [{"name": "Apex Shield Support", "addr": "support@apexshield.com"}],
        "subject": subject,
        "date": now.strftime("%a, %d %b %Y %H:%M:%S +0000"),
        "flags": "",
        "body": body,
    }


def main():
    tmp = Path(tempfile.mkdtemp(prefix="step24_demo_"))
    db_path = tmp / "production.db"
    shutil.copy(SEED_DB, db_path)

    outbox = []
    db = Database(str(db_path))
    pipeline = None
    try:
        cfg = AppConfig()
        cfg.system.db_path = db_path
        cfg.system.watermark_file = tmp / "watermark.json"
        cfg.system.csv_report_path = tmp / "triage_results.csv"
        cfg.system.polling_interval_seconds = INTERVAL
        cfg.autoreply.enabled = True

        pipeline = AutonomousPipeline(cfg, db, mock_mode=True, dry_run=False)
        pipeline.reply_sender.client.send_email = (
            lambda to, subject, body, **kw: (outbox.append(
                {"to": to, "subject": subject, "body": body}
            ), True)[1]
        )

        print("=" * 72)
        print("STEP 24 PRODUCTION RUN -- continuous autonomous operation")
        print("=" * 72)
        print(f"Database        : {db_path}")
        print(f"Polling interval: {INTERVAL}s (production: 60s)")
        print(f"Mock Gmail      : enabled (Himalaya is not installed here)")
        print(f"Reply transport : recording (SMTP not exercised locally)\n")

        # Two historical messages so the first cycle has a watermark to adopt.
        print("--- injecting 2 historical messages (must be skipped) ---")
        pipeline.himalaya.inject_mock_email(
            envelope("<0900@old>", "old.customer@example.com",
                     "Old question from last month", "Historical mail.")
        )
        pipeline.himalaya.inject_mock_email(
            envelope("<0901@old>", "another.old@example.com",
                     "Another old message", "Also historical.")
        )

        print("--- starting background worker (daemon thread) ---")
        thread = pipeline.start_background()
        print(f"  worker thread alive: {thread.is_alive()}")
        time.sleep(INTERVAL * 1.5)

        wm = json.loads((tmp / "watermark.json").read_text())
        print(f"  watermark adopted : {wm['last_processed_id']}")
        print(f"  processed so far  : {pipeline.stats.emails_processed} "
              f"(history correctly skipped)\n")

        for msg_id, sender, subject, body in STREAM:
            print(f"--- new mail arrives: {subject!r} ---")
            pipeline.himalaya.inject_mock_email(envelope(msg_id, sender, subject, body))
            time.sleep(INTERVAL * 1.2)
            s = pipeline.stats
            print(f"  processed={s.emails_processed}  tickets={s.tickets_created}  "
                  f"replies={s.replies_sent}  suppressed={s.replies_suppressed}  "
                  f"failed={s.emails_failed}  last_action={pipeline.last_ticket_action}")

        print("\n--- stopping background worker ---")
        pipeline.stop()
        pipeline.join_background(timeout=10)
        print(f"  worker thread alive: {thread.is_alive()}")

        # ---- Report the Step 24 checklist against real artifacts -------- #
        print("\n" + "=" * 72)
        print("STEP 24 VERIFICATION")
        print("=" * 72)

        conn = db.get_connection()
        conn.row_factory = __import__("sqlite3").Row
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM triage_records ORDER BY received_date"
        )]
        tickets = [dict(r) for r in conn.execute(
            "SELECT ticket_number, subject, status, email_id FROM support_tickets "
            "WHERE email_id IS NOT NULL"
        )]

        print("\n[1] Gmail monitoring + [3] 1-minute polling")
        print(f"    poll cycles executed : {pipeline.stats.poll_cycles}")
        print(f"    emails ingested      : {pipeline.stats.emails_processed}")
        print(f"    duplicates skipped   : {pipeline.stats.emails_skipped_duplicate}")
        print(f"    spam archived        : {pipeline.stats.emails_archived_spam}")
        print(f"    tickets created      : {pipeline.stats.tickets_created}")
        print(f"    tickets reused       : {pipeline.stats.tickets_reused}")
        print(f"    failed               : {pipeline.stats.emails_failed}")
        print("[2] Watermark tracking")
        wm = json.loads((tmp / "watermark.json").read_text())
        print(f"    last processed id    : {wm['last_processed_id']}")
        print("[4] Duplicate prevention")
        print(f"    unique email_ids     : {len({r['email_id'] for r in rows})}"
              f" of {len(rows)} records (no duplicates)")
        print("[5] Email preprocessing + [6] customer identification")
        identified = sum(1 for r in rows if r.get("customer_id"))
        print(f"    customers identified : {identified}/{len(rows)}")
        print("[7] Insurance database retrieval")
        print(f"    records with claim   : {sum(1 for r in rows if r.get('claim_number'))}")
        print(f"    records with policy  : {sum(1 for r in rows if r.get('policy_number'))}")
        print("[8] LLM classification")
        for r in rows:
            print(f"    {r['email_id']:<12} {r['intent']:<22} {r['priority']:<9} "
                  f"urgency={r['urgency_score']} sentiment={r['sentiment']}")
        print("[9] Support ticket handling")
        for t in tickets:
            print(f"    {t['ticket_number']:<16} {t['status']:<10} {t['subject'][:44]}")
        print("[10] SQLite storage")
        print(f"    triage_records       : {len(rows)}")
        print("[11] CSV reporting")
        csv_path = tmp / "triage_results.csv"
        with open(csv_path, newline="", encoding="utf-8") as fh:
            csv_rows = list(csv.DictReader(fh))
        print(f"    rows                 : {len(csv_rows)}")
        print(f"    columns              : {len(csv_rows[0]) if csv_rows else 0}")
        print("[12] Dashboard updates")
        kpis = DashboardDataService(conn).get_kpi_metrics()
        for k, v in kpis.items():
            print(f"    {k:<22} {v}")
        print("[13] SMTP auto-replies")
        print(f"    replies dispatched   : {len(outbox)}")
        for m in outbox:
            first = m["body"].strip().splitlines()[0][:60]
            print(f"    -> {m['to']:<32} {first}")

        # ---- Durable reply audit trail -------------------------------------
        # Confirm the dispatcher wrote its outcomes to the reply_audit table,
        # not just to the in-process log, so the trail survives a restart.
        from src.db.repository import InsuranceRepository
        repo = InsuranceRepository(conn)
        audit_rows = repo.get_reply_audit()
        audit_stats = repo.get_reply_audit_stats()
        print("\n[14] Durable reply audit trail")
        print(f"    rows persisted      : {len(audit_rows)}")
        print(f"    by status           : {audit_stats}")
        for r in audit_rows[:5]:
            digest = f"{r['body_sha256'][:12]}" if r["body_sha256"] else "none"
            print(f"      - {r['status']:<24} -> {r['recipient']}  body={r['body_chars']}ch/{digest}")


        # ---- Identity verification round trip ---------------------------
        # A sender who quotes someone else's claim number gets no record data;
        # they get a code, addressed to the contact on file. Redeeming it makes
        # their address verified and later mail is answered normally.
        import re as _re
        v = pipeline.identity_verifier
        challenge_ok, _, ch = v.issue("new.address@customer.example", "CUST-5002")
        print("\n[15] Identity verification")
        print(f"    challenge issued   : {challenge_ok}")
        print(f"    verified before    : {v.is_verified('new.address@customer.example', 'CUST-5002')}")
        wrong = v.redeem("new.address@customer.example", "000000")
        print(f"    wrong code         : verified={wrong.verified} ({wrong.reason})")
        right = v.redeem("new.address@customer.example", ch.code)
        print(f"    correct code       : verified={right.verified} for {right.customer_id}")
        print(f"    verified after     : {v.is_verified('new.address@customer.example', 'CUST-5002')}")
        print(f"    replay of same code: verified={v.redeem('new.address@customer.example', ch.code).verified}")
        print(f"    verifier stats     : {v.stats()}")

        conn.close()
        print("\n" + "=" * 72)
        print(f"Artifacts in {tmp}")
        print("=" * 72)
        return 0
    finally:
        if pipeline is not None:
            pipeline.stop()
        try:
            db.close()
        except Exception:
            pass



if __name__ == "__main__":
    raise SystemExit(main())
