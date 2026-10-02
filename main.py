"""CLI Entry point for Insurance Customer Support & Claims Triage System."""

import argparse
import logging
import sys
from pathlib import Path

from src.reporting.csv_exporter import CSVExporter

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import load_config
from src.db.database import Database
from src.db.seed_data import seed_database
from src.db.repository import InsuranceRepository
from src.dashboard.server import start_dashboard_server
from src.pipeline.autonomous_runner import AutonomousPipeline
from src.ingestion.himalaya_client import HimalayaClient
from src.ingestion.gmail_config import generate_himalaya_gmail_toml


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main():
    parser = argparse.ArgumentParser(description="Autonomous Insurance Customer Support & Claims Triage CLI")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Command: seed
    subparsers.add_parser("seed", help="Seed database with realistic test records")

    # Command: dashboard
    dash_parser = subparsers.add_parser("dashboard", help="Launch the real-time triage dashboard")
    dash_parser.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1)")
    dash_parser.add_argument("--port", type=int, default=None, help="Bind port (default from config)")

    # Command: run
    run_parser = subparsers.add_parser("run", help="Run the autonomous background triage pipeline")
    run_parser.add_argument("--once", action="store_true", help="Run a single poll cycle and exit (cron-friendly)")
    run_parser.add_argument("--cycles", type=int, default=None, help="Stop after N poll cycles (for testing)")
    run_parser.add_argument("--mock", action="store_true", help="Use the mock Himalaya client (no live Gmail)")
    run_parser.add_argument("--dry-run", action="store_true", help="Generate replies but do not send them")
    run_parser.add_argument("--no-reply", action="store_true", help="Disable auto-reply generation entirely")

    # Command: test-email
    test_parser = subparsers.add_parser("test-email", help="Inject a synthetic test email into the pipeline")
    test_parser.add_argument("--from", dest="sender", default="sarah.jenkins@example.com", help="Sender email")
    test_parser.add_argument("--subject", default="Status of my claim CLM-2024-09112", help="Subject")
    test_parser.add_argument("--body", default="Hello, can you please provide an update on my claim? I haven't heard back.", help="Body text")
    test_parser.add_argument("--id", dest="msg_id", default="TEST-MSG-001", help="Message ID")

    # Command: status
    subparsers.add_parser("status", help="Show system status and triage counts")

    # Command: setup-gmail
    gmail_cfg_parser = subparsers.add_parser("setup-gmail", help="Generate Himalaya Gmail configuration file with Keychain integration")
    gmail_cfg_parser.add_argument("--email", default="support@apexshield.com", help="Gmail address")
    gmail_cfg_parser.add_argument("--out", default=str(Path.home() / ".config" / "himalaya" / "config.toml"), help="Output path")

    # Command: test-connection
    conn_parser = subparsers.add_parser("test-connection", help="Test Himalaya CLI Gmail connection")
    conn_parser.add_argument("--mock", action="store_true", help="Run connection test in mock mode")

    # Command: export-csv
    export_parser = subparsers.add_parser("export-csv", help="Export or filter triage results from CSV")
    export_parser.add_argument("--priority", help="Filter by priority (Critical, High, Medium, Low)")
    export_parser.add_argument("--intent", help="Filter by intent code")
    export_parser.add_argument("--status", help="Filter by escalation status")
    export_parser.add_argument("--start", help="Filter by start date (ISO)")
    export_parser.add_argument("--end", help="Filter by end date (ISO)")
    export_parser.add_argument("--out", help="Optional output path to save filtered CSV")

    args = parser.parse_args()
    config = load_config()
    setup_logging(config.system.log_level)

    db = Database(config.system.db_path)

    if args.command == "seed":
        print(f"Seeding database at {config.system.db_path}...")
        with db.get_connection() as conn:
            seed_database(conn)
        print("Database seeded successfully with customers, vehicles, policies, claims, and garages.")

    elif args.command == "run":
        if args.no_reply:
            config.autoreply.enabled = False
        pipeline = AutonomousPipeline(
            config, db, mock_mode=args.mock, dry_run=args.dry_run
        )
        if args.once:
            processed = pipeline.run_once()
            print(f"Poll cycle complete. Emails processed: {processed}")
            print(pipeline.stats)
        else:
            print(
                f"Autonomous Insurance Triage Pipeline started. Polling every "
                f"{config.system.polling_interval_seconds}s"
                f"{' [MOCK]' if args.mock else ''}{' [DRY-RUN]' if args.dry_run else ''}..."
            )
            try:
                stats = pipeline.run_forever(max_cycles=args.cycles)
                print(stats)
            except KeyboardInterrupt:
                print("\nPipeline stopped.")

    elif args.command == "test-email":
        pipeline = AutonomousPipeline(config, db, dry_run=True)
        raw_msg = {
            "id": args.msg_id,
            "from": {"addr": args.sender, "name": args.sender.split("@")[0].replace(".", " ").title()},
            "to": "support@apexshield.com",
            "subject": args.subject,
            "date": "2024-09-25T10:00:00Z",
            "body": args.body,
        }
        print(f"Injecting test email ID #{args.msg_id} from {args.sender}...")
        success = pipeline.process_email(raw_msg, advance_watermark=False)
        print(f"Email processed: {success}")

    elif args.command == "status":
        with db.get_connection() as conn:
            repo = InsuranceRepository(conn)
            records = repo.get_all_triage_records(limit=10)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM customers")
            cust_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM policies")
            pol_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM claims")
            claim_count = cur.fetchone()[0]

        print("=== APEX SHIELD INSURANCE TRIAGE SYSTEM STATUS ===")
        print(f"Database Path: {config.system.db_path}")
        print(f"Customers: {cust_count} | Policies: {pol_count} | Claims: {claim_count}")
        print(f"Processed Triage Records: {len(records)}")
        print(f"Watermark File: {config.system.watermark_file}")

    elif args.command == "setup-gmail":
        out_path = Path(args.out)
        generate_himalaya_gmail_toml(email=args.email, output_path=out_path)
        print(f"Generated Himalaya Gmail config at: {out_path}")
        print("Keychain integration configured: passwords are read dynamically from macOS Keychain / secret store and never saved in plaintext.")

    elif args.command == "test-connection":
        client = HimalayaClient(
            account=config.gmail.account_name,
            mock_mode=args.mock,
            config_path="data/config.toml",
        )
        res = client.test_connection()
        print("=== GMAIL CONNECTION TEST ===")
        print(f"Connected: {res.get('connected')}")
        print(f"Mode: {res.get('mode')}")
        print(f"Message: {res.get('message')}")
        if "folders" in res:
            print(f"Available Folders: {res['folders']}")
        if "error" in res:
            print(f"Error Details: {res['error']}")

    elif args.command == "export-csv":
        # Export from the database of record, not from the incremental CSV
        # artifact: the artifact only holds rows appended since the last run,
        # so filtering it silently under-reports on any filtered export.
        # Close the connection when done -- leaving it open keeps a Windows
        # file lock on the database and blocks any later move or delete.
        conn = db.get_connection()
        try:
            repo = InsuranceRepository(conn)
            results = repo.get_all_triage_records(limit=100000)
        finally:
            conn.close()
        results = [
            r for r in results
            if (not args.priority or r.get("priority") == args.priority)
            and (not args.intent or r.get("intent") == args.intent)
            and (not args.status or r.get("escalation_status") == args.status)
            and (not args.start or str(r.get("received_date") or "") >= args.start)
            and (not args.end or str(r.get("received_date") or "") <= args.end)
        ]
        results.sort(key=lambda r: str(r.get("received_date") or ""), reverse=True)
        print(f"=== TRIAGE CSV EXPORT ({len(results)} matching records) ===")
        for r in results:
            print(f"[{str(r.get('received_date', ''))[:10]}] {r.get('email_id')} | {r.get('priority')} | {r.get('intent')} | {r.get('escalation_status')} | {r.get('sender_email')}: {r.get('subject')}")

        if args.out:
            out_exporter = CSVExporter(Path(args.out))
            out_exporter.export_all(results)
            print(f"Saved {len(results)} records to {args.out}")

    elif args.command == "dashboard":
        from src.dashboard.server import start_dashboard_server

        dash_db = Database(config.system.db_path)
        port = args.port or config.dashboard.port
        start_dashboard_server(dash_db, host=args.host, port=port)
        print("=== APEX SHIELD TRIAGE DASHBOARD ===")
        print(f"URL: http://{args.host}:{port}")
        print(f"Database: {config.system.db_path}")
        print("API: /api/data | /api/kpis | /api/record/<id> | /api/distribution?field=priority | /api/tickets | /api/health")
        print("Press Ctrl+C to stop.")
        try:
            while True:
                import time

                time.sleep(3600)
        except KeyboardInterrupt:
            print("\nDashboard stopped.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
