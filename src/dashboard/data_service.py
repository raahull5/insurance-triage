"""Data aggregation service for insurance triage dashboard KPIs and queues."""

import sqlite3
from typing import Dict, Any, List

DISTRIBUTION_COLUMNS = {
    "category": "category",
    "intent": "intent",
    "priority": "priority",
    "sentiment": "sentiment",
    "routing_desk": "routing_desk",
    "escalation_needed": "escalation_needed",
}


class DashboardDataService:
    """Aggregates metrics and queue records directly from SQLite."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_kpi_metrics(self) -> Dict[str, Any]:
        cur = self.conn.cursor()

        # Total inquiries
        cur.execute("SELECT COUNT(*) FROM triage_records")
        total_inquiries = cur.fetchone()[0]

        # Critical & Emergencies
        cur.execute(
            "SELECT COUNT(*) FROM triage_records WHERE priority = 'Critical' OR intent IN "
            "('ROADSIDE_ASSISTANCE', 'TOWING_SERVICE', 'NEW_CLAIM', 'CLAIM_DELAY', 'COMPLAINT', 'FRAUD_SUSPICION')"
        )
        critical_count = cur.fetchone()[0]

        # Human Escalations
        cur.execute("SELECT COUNT(*) FROM triage_records WHERE escalation_needed = 1")
        escalations_count = cur.fetchone()[0]

        # Auto-replied.
        # The stored reply_status is the dispatcher's audit status verbatim
        # (SENT, DRY_RUN, SUPPRESSED_*, RATE_LIMITED, FAILED) while the
        # pipeline's own terminal labels (Suppressed, Held, Disabled) are
        # written for non-dispatch paths. Match case-insensitively across both
        # vocabularies -- a case-sensitive match on 'Sent' silently reported an
        # Auto-Replied count of 0 even when replies were being sent.
        cur.execute(
            "SELECT COUNT(*) FROM triage_records WHERE UPPER(reply_status) = 'SENT'"
        )
        auto_replied_count = cur.fetchone()[0]

        # Supplementary operational metrics
        cur.execute(
            "SELECT COUNT(*) FROM triage_records WHERE UPPER(reply_status) IN "
            "('SUPPRESSED', 'HELD', 'SUPPRESSED_HUMAN_REVIEW', 'SUPPRESSED_SPAM', "
            "'SUPPRESSED_EMPTY_BODY', 'RATE_LIMITED', 'DUPLICATE_SUPPRESSED', "
            "'SKIPPED', 'DISABLED')"
        )
        suppressed_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(DISTINCT sender_email) FROM triage_records")
        unique_senders = cur.fetchone()[0]

        cur.execute("SELECT AVG(urgency_score) FROM triage_records")
        avg = cur.fetchone()[0]
        avg_urgency = round(float(avg), 1) if avg is not None else 0.0

        cur.execute("SELECT COUNT(*) FROM support_tickets WHERE status = 'Open'")
        open_tickets = cur.fetchone()[0]

        # Automation rate: auto-replied share of all triaged records
        automation_rate = 0.0
        if total_inquiries:
            automation_rate = round(100.0 * auto_replied_count / total_inquiries, 1)

        return {
            "total_inquiries": total_inquiries,
            "critical_emergencies": critical_count,
            "human_escalations": escalations_count,
            "auto_replied": auto_replied_count,
            "suppressed": suppressed_count,
            "unique_senders": unique_senders,
            "avg_urgency_score": avg_urgency,
            "open_tickets": open_tickets,
            "automation_rate": automation_rate,
        }

    def get_queue(self, search: str = "", priority: str = "", limit: int = 100) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        query = "SELECT * FROM triage_records WHERE 1=1"
        params: List[Any] = []

        if priority:
            query += " AND priority = ?"
            params.append(priority)

        if search:
            query += " AND (sender_email LIKE ? OR sender_name LIKE ? OR subject LIKE ? OR intent LIKE ?)"
            s_param = f"%{search}%"
            params.extend([s_param, s_param, s_param, s_param])

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]

    def get_queue_filtered(
        self,
        search: str = "",
        priority: str = "",
        intent: str = "",
        escalation: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Server-side filtering for the dashboard queue."""
        cur = self.conn.cursor()
        query = "SELECT * FROM triage_records WHERE 1=1"
        params: List[Any] = []

        if priority:
            query += " AND priority = ?"
            params.append(priority)
        if intent:
            query += " AND intent = ?"
            params.append(intent)
        if search:
            query += (
                " AND (sender_email LIKE ? OR sender_name LIKE ? OR subject LIKE ?"
                " OR intent LIKE ? OR category LIKE ? OR summary LIKE ?)"
            )
            s = f"%{search}%"
            params.extend([s, s, s, s, s, s])
        if escalation in ("yes", "true", "1"):
            query += " AND escalation_needed = 1"
        elif escalation in ("no", "false", "0"):
            query += " AND escalation_needed = 0"

        query += " ORDER BY CASE priority WHEN 'Critical' THEN 0 WHEN 'High' THEN 1"
        query += " WHEN 'Medium' THEN 2 ELSE 3 END, id DESC LIMIT ?"
        params.append(limit)

        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]

    def get_record(self, record_id: int) -> Dict[str, Any] | None:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM triage_records WHERE id = ?", (record_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_distribution(self, field: str = "category") -> List[Dict[str, Any]]:
        """Count triage records grouped by a whitelisted field."""
        column = DISTRIBUTION_COLUMNS.get(field)
        if not column:
            raise ValueError(f"Unsupported distribution field: {field}")

        cur = self.conn.cursor()
        cur.execute(
            f"SELECT COALESCE({column}, 'Unclassified') AS label, COUNT(*) AS count "
            f"FROM triage_records GROUP BY label ORDER BY count DESC"
        )
        return [{"label": r["label"], "count": r["count"]} for r in cur.fetchall()]

    def get_ticket_summary(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Recent tickets. The schema has no sender_email column, so it is joined
        from customers via customer_id (NULL-safe for unidentified senders)."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT t.ticket_number, c.email AS sender_email, t.subject, t.status, t.priority,"
            " t.assigned_team, t.created_at, t.updated_at "
            "FROM support_tickets t "
            "LEFT JOIN customers c ON c.id = t.customer_id "
            "ORDER BY t.id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_dashboard_payload(self, search: str = "", priority: str = "", limit: int = 100) -> Dict[str, Any]:
        """Single-roundtrip payload used by the dashboard front end."""
        return {
            "kpis": self.get_kpi_metrics(),
            "queue": self.get_queue_filtered(search=search, priority=priority, limit=limit),
            "distributions": {
                "category": self.get_distribution("category"),
                "priority": self.get_distribution("priority"),
                "sentiment": self.get_distribution("sentiment"),
            },
            "tickets": self.get_ticket_summary(limit=10),
        }
