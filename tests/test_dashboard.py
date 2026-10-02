"""Unit tests for the dashboard data service and HTTP API."""

import json
import sqlite3
import sys
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dashboard.data_service import DashboardDataService
from src.dashboard.server import DashboardRequestHandler, start_dashboard_server

SAMPLE_RECORDS = [
    {
        "email_id": "MSG-1",
        "customer_id": None,
        "detected_intent": "CLAIM_STATUS",
        "priority": "High",
        "confidence": 0.95,
        "reason": "Customer references an approved claim number.",
        "routed_to": "Claims Status Desk",
        "suggested_reply": "Your claim was approved for $1,150.",
        "human_review_required": 0,
        "sender_email": "david.chen@example.com",
        "sender_name": "David Chen",
        "subject": "Where is my claim payment?",
        "received_date": "2024-09-24T10:00:00Z",
        "policy_number": "POL-2024-8831",
        "claim_number": "CLM-2024-04190",
        "category": "Claims",
        "intent": "CLAIM_STATUS",
        "urgency_score": 7,
        "sentiment": "Neutral",
        "escalation_needed": 0,
        "escalation_status": "None",
        "summary": "Customer asking about approved claim payout.",
        "routing_desk": "Claims Status Desk",
        "processing_status": "Completed",
        "reply_status": "Sent",
    },
    {
        "email_id": "MSG-2",
        "customer_id": None,
        "detected_intent": "COMPLAINT",
        "priority": "Critical",
        "confidence": 0.98,
        "reason": "Explicit legal threat detected.",
        "routed_to": "Executive Customer Relations & Legal Liaison",
        "suggested_reply": "",
        "human_review_required": 1,
        "sender_email": "maria.lopez@example.com",
        "sender_name": "Maria Lopez",
        "subject": "I am going to contact my lawyer",
        "received_date": "2024-09-25T08:00:00Z",
        "policy_number": "POL-2024-9002",
        "claim_number": "",
        "category": "General Support",
        "intent": "COMPLAINT",
        "urgency_score": 10,
        "sentiment": "Angry",
        "escalation_needed": 1,
        "escalation_status": "Escalated",
        "summary": "Legal threat regarding claim denial.",
        "routing_desk": "Executive Customer Relations & Legal Liaison",
        "processing_status": "Completed",
        "reply_status": "Suppressed",
    },
    {
        "email_id": "MSG-3",
        "customer_id": None,
        "detected_intent": "SPAM_OR_AUTOMATED",
        "priority": "Low",
        "confidence": 0.99,
        "reason": "Automated sender and spam keywords.",
        "routed_to": "Spam Filter",
        "suggested_reply": "",
        "human_review_required": 0,
        "sender_email": "no-reply@spam.biz",
        "sender_name": None,
        "subject": "CHEAP INSURANCE NOW",
        "received_date": "2024-09-25T09:00:00Z",
        "policy_number": "",
        "claim_number": "",
        "category": "General Support",
        "intent": "SPAM_OR_AUTOMATED",
        "urgency_score": 1,
        "sentiment": "Neutral",
        "escalation_needed": 0,
        "escalation_status": "None",
        "summary": "Unsolicited marketing blast.",
        "routing_desk": "Spam Filter",
        "processing_status": "Completed",
        "reply_status": "Suppressed",
    },
]


def build_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE triage_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT,
            customer_id TEXT,
            detected_intent TEXT,
            priority TEXT,
            confidence REAL,
            reason TEXT,
            routed_to TEXT,
            suggested_reply TEXT,
            human_review_required INTEGER DEFAULT 0,
            processed_at TEXT,
            sender_email TEXT,
            sender_name TEXT,
            subject TEXT,
            received_date TEXT,
            policy_number TEXT,
            claim_number TEXT,
            category TEXT,
            intent TEXT,
            urgency_score INTEGER,
            sentiment TEXT,
            escalation_needed INTEGER DEFAULT 0,
            escalation_status TEXT,
            summary TEXT,
            routing_desk TEXT,
            processing_status TEXT,
            reply_status TEXT,
            reply_sent_at TEXT,
            created_at TEXT
        );
        CREATE TABLE support_tickets (
            id TEXT PRIMARY KEY,
            ticket_number TEXT,
            customer_id TEXT,
            email_id TEXT,
            policy_id TEXT,
            claim_id TEXT,
            subject TEXT,
            category TEXT,
            priority TEXT,
            status TEXT,
            assigned_team TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE customers (id TEXT PRIMARY KEY, name TEXT, email TEXT);
        """
    )
    conn.execute("INSERT INTO customers (id, name, email) VALUES ('CUST-001','David Chen','david.chen@example.com')")
    cols = ", ".join(SAMPLE_RECORDS[0].keys())
    marks = ", ".join(["?"] * len(SAMPLE_RECORDS[0]))
    for r in SAMPLE_RECORDS:
        conn.execute(f"INSERT INTO triage_records ({cols}) VALUES ({marks})", list(r.values()))
    conn.execute(
        "INSERT INTO support_tickets (id, ticket_number, customer_id, email_id, subject, category, priority, status, created_at, updated_at)"
        " VALUES ('T-1','TCK-2024-00001','CUST-001','MSG-1','Claim status','Claims','High','Open','2024-09-24','2024-09-24')"
    )
    conn.commit()
    return conn


class TestDashboardDataService(unittest.TestCase):
    def setUp(self):
        self.conn = build_conn()
        self.service = DashboardDataService(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_kpi_metrics(self):
        kpis = self.service.get_kpi_metrics()
        self.assertEqual(kpis["total_inquiries"], 3)
        self.assertEqual(kpis["critical_emergencies"], 1)
        self.assertEqual(kpis["human_escalations"], 1)
        self.assertEqual(kpis["auto_replied"], 1)
        self.assertEqual(kpis["suppressed"], 2)
        self.assertEqual(kpis["unique_senders"], 3)
        self.assertEqual(kpis["avg_urgency_score"], 6.0)
        self.assertEqual(kpis["open_tickets"], 1)
        self.assertEqual(kpis["automation_rate"], 33.3)

    def test_queue_filtered_by_priority(self):
        rows = self.service.get_queue_filtered(priority="Critical")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["intent"], "COMPLAINT")

    def test_queue_filtered_by_search(self):
        rows = self.service.get_queue_filtered(search="chen")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["email_id"], "MSG-1")

    def test_queue_filtered_by_escalation(self):
        self.assertEqual(len(self.service.get_queue_filtered(escalation="yes")), 1)
        self.assertEqual(len(self.service.get_queue_filtered(escalation="no")), 2)

    def test_queue_sorted_by_priority(self):
        rows = self.service.get_queue_filtered()
        self.assertEqual(rows[0]["priority"], "Critical")

    def test_distributions(self):
        cat = self.service.get_distribution("category")
        self.assertEqual(sum(c["count"] for c in cat), 3)
        prio = {d["label"]: d["count"] for d in self.service.get_distribution("priority")}
        self.assertEqual(prio["High"], 1)
        self.assertEqual(prio["Critical"], 1)
        self.assertEqual(prio["Low"], 1)

    def test_distribution_rejects_unknown_field(self):
        with self.assertRaises(ValueError):
            self.service.get_distribution("sender_email; DROP TABLE triage_records")

    def test_get_record_and_missing(self):
        self.assertEqual(self.service.get_record(1)["email_id"], "MSG-1")
        self.assertIsNone(self.service.get_record(999))

    def test_dashboard_payload_shape(self):
        payload = self.service.get_dashboard_payload()
        self.assertIn("kpis", payload)
        self.assertIn("queue", payload)
        self.assertIn("distributions", payload)
        self.assertIn("tickets", payload)


class TestDashboardServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile

        tmpdir = tempfile.TemporaryDirectory()
        cls._tmpdir = tmpdir
        db_path = Path(tmpdir.name) / "dash_test.db"

        # Materialize the schema, then load sample rows with a direct connection.
        from src.db.schema import create_tables

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        create_tables(conn)
        # Ensure the joined customer row exists (mirrors the seeded production DB).
        cols_c = "id, name, email"
        conn.execute(f"INSERT OR IGNORE INTO customers ({cols_c}) VALUES (?,?,?)",
                     ("CUST-001", "David Chen", "david.chen@example.com"))
        cols = ", ".join(SAMPLE_RECORDS[0].keys())
        marks = ", ".join(["?"] * len(SAMPLE_RECORDS[0]))
        for r in SAMPLE_RECORDS:
            conn.execute(f"INSERT INTO triage_records ({cols}) VALUES ({marks})", list(r.values()))
        ticket_cols = (
            "id, ticket_number, customer_id, email_id, subject, category, priority, status, created_at, updated_at"
        )
        conn.execute(
            f"INSERT INTO support_tickets ({ticket_cols}) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "T-1",
                "TCK-2024-00001",
                "CUST-001",
                "MSG-1",
                "Claim status",
                "Claims",
                "High",
                "Open",
                "2024-09-24",
                "2024-09-24",
            ),
        )
        conn.commit()
        conn.close()

        from src.db.database import Database

        cls.db = Database(db_path)
        cls.server = start_dashboard_server(cls.db, host="127.0.0.1", port=8791)
        cls.base = "http://127.0.0.1:8791"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmpdir.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")

    def test_index_html(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("Apex Shield", body)
        self.assertIn("kpi-grid", body)
        self.assertIn("setInterval(fetchData, 10000)", body)

    def test_api_data(self):
        status, body = self._get("/api/data")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["kpis"]["total_inquiries"], 3)
        self.assertEqual(len(data["queue"]), 3)
        self.assertIn("category", data["distributions"])

    def test_api_data_with_filters(self):
        status, body = self._get("/api/data?priority=Critical&escalation=yes")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(len(data["queue"]), 1)

    def test_api_record(self):
        status, body = self._get("/api/record/1")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["intent"], "CLAIM_STATUS")

    def test_api_record_not_found(self):
        try:
            self._get("/api/record/4242")
            self.fail("expected HTTP error for missing record")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_api_kpis_distribution_tickets_health(self):
        status, body = self._get("/api/kpis")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["kpis"]["human_escalations"], 1)

        status, body = self._get("/api/distribution?field=priority")
        self.assertEqual(json.loads(body)["field"], "priority")

        status, body = self._get("/api/tickets")
        self.assertEqual(len(json.loads(body)["tickets"]), 1)

        status, body = self._get("/api/health")
        self.assertEqual(json.loads(body)["status"], "ok")

    def test_unknown_path_404(self):
        try:
            self._get("/nope")
            self.fail("expected HTTP 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)


if __name__ == "__main__":
    unittest.main()


class TestReplyStatusVocabulary(unittest.TestCase):
    """Regression: the dispatcher's real audit statuses must reach the KPIs.

    The Step 24 production run caught this: `ReplyDispatcher` writes the
    literal status `SENT`, but the dashboard counted `reply_status = 'Sent'`
    case-sensitively, so the Auto-Replied KPI read 0 while replies were in
    fact being sent. The pre-existing fixtures only ever used title-case
    values, which is why the bug survived every earlier step.

    These tests use the statuses the dispatcher actually emits.
    """

    def _kpis_for(self, statuses):
        conn = build_conn()
        try:
            for row in SAMPLE_RECORDS:
                conn.execute("DELETE FROM triage_records WHERE email_id = ?", (row["email_id"],))
            base = dict(SAMPLE_RECORDS[0])
            for i, status in enumerate(statuses):
                base["email_id"] = f"MSG-{i}"
                base["sender_email"] = f"user{i}@example.com"
                base["reply_status"] = status
                base["escalation_needed"] = 0
                base["human_review_required"] = 0
                cols = ", ".join(base.keys())
                marks = ", ".join(["?"] * len(base))
                conn.execute(
                    f"INSERT INTO triage_records ({cols}) VALUES ({marks})", list(base.values())
                )
            conn.commit()
            return DashboardDataService(conn).get_kpi_metrics()
        finally:
            conn.close()

    def test_uppercase_sent_counts_as_auto_replied(self):
        kpis = self._kpis_for(["SENT"])
        self.assertEqual(kpis["auto_replied"], 1)
        self.assertEqual(kpis["automation_rate"], 100.0)

    def test_titlecase_sent_still_counts(self):
        """Backwards compatibility with rows written before the fix."""
        kpis = self._kpis_for(["Sent"])
        self.assertEqual(kpis["auto_replied"], 1)

    def test_dry_run_is_not_counted_as_a_reply(self):
        """A dry run must never inflate the Auto-Replied metric."""
        kpis = self._kpis_for(["DRY_RUN"])
        self.assertEqual(kpis["auto_replied"], 0)

    def test_suppression_vocabulary_is_counted(self):
        for status in (
            "Suppressed",
            "SUPPRESSED_HUMAN_REVIEW",
            "SUPPRESSED_SPAM",
            "SUPPRESSED_EMPTY_BODY",
            "RATE_LIMITED",
            "DUPLICATE_SUPPRESSED",
        ):
            with self.subTest(status=status):
                kpis = self._kpis_for([status])
                self.assertEqual(kpis["suppressed"], 1, f"{status} not counted as suppressed")
                self.assertEqual(kpis["auto_replied"], 0)

    def test_failure_is_not_counted_as_suppressed_or_replied(self):
        """A failed send is neither a reply nor a legitimate suppression."""
        kpis = self._kpis_for(["FAILED"])
        self.assertEqual(kpis["auto_replied"], 0)
        self.assertEqual(kpis["suppressed"], 0)
