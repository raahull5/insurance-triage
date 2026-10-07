"""Insurance repository for querying database and retrieving relational context."""

import sqlite3
import re
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import json
import logging

logger = logging.getLogger(__name__)


class InsuranceRepository:
    """Repository handling all database lookups, entity extraction, and relational context retrieval."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def find_customer_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Identify a customer by their email address."""
        if not email:
            return None

        cur = self.conn.cursor()

        cur.execute(
            "SELECT * FROM customers WHERE LOWER(email) = LOWER(?)",
            (email.strip(),),
        )

        row = cur.fetchone()

        return dict(row) if row else None

    def find_customer_by_id(
        self,
        customer_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch customer profile by customer ID."""
        if not customer_id:
            return None

        cur = self.conn.cursor()

        cur.execute(
            "SELECT * FROM customers WHERE id = ?",
            (customer_id,),
        )

        row = cur.fetchone()

        return dict(row) if row else None

    def find_policy_by_number(
        self,
        policy_number: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch policy by policy number."""
        if not policy_number:
            return None

        cur = self.conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM policies
            WHERE UPPER(policy_number) = UPPER(?)
            """,
            (policy_number.strip(),),
        )

        row = cur.fetchone()

        return dict(row) if row else None

    def find_claim_by_number(
        self,
        claim_number: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch claim by claim number."""
        if not claim_number:
            return None

        cur = self.conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM claims
            WHERE UPPER(claim_number) = UPPER(?)
            """,
            (claim_number.strip(),),
        )

        row = cur.fetchone()

        return dict(row) if row else None

    # ================================================================
    # CLAIM CREATION SUPPORT
    # ================================================================

    def find_claim_by_policy_and_incident(
        self,
        policy_id: str,
        customer_id: str,
        incident_date: str,
        description: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Find an existing claim matching the same policy, customer,
        incident date, and incident description.

        This is a defensive duplicate check before creating a new
        official claim.
        """

        if not policy_id or not customer_id or not incident_date:
            return None

        cur = self.conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM claims
            WHERE policy_id = ?
              AND customer_id = ?
              AND incident_date = ?
              AND LOWER(TRIM(COALESCE(description, ''))) =
                  LOWER(TRIM(?))
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (
                policy_id,
                customer_id,
                incident_date,
                description or "",
            ),
        )

        row = cur.fetchone()

        return dict(row) if row else None

    def create_claim(
        self,
        claim_id: str,
        claim_number: str,
        policy_id: str,
        customer_id: str,
        incident_date: str,
        reported_date: str,
        status: str,
        description: str,
        damage_description: Optional[str] = None,
        claimed_amount: Optional[float] = None,
    ) -> str:
        """
        Persist a new official insurance claim.

        All authorization and validation checks must be completed
        before this method is called.
        """

        cur = self.conn.cursor()

        cur.execute(
            """
            INSERT INTO claims (
                id,
                claim_number,
                policy_id,
                customer_id,
                incident_date,
                reported_date,
                status,
                description,
                damage_description,
                claimed_amount
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim_id,
                claim_number,
                policy_id,
                customer_id,
                incident_date,
                reported_date,
                status,
                description,
                damage_description,
                claimed_amount if claimed_amount is not None else 0.0,
            ),
        )

        self.conn.commit()

        return claim_id

    def find_vehicle_by_vin(
        self,
        vin: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch vehicle by 17-character VIN."""
        if not vin:
            return None

        cur = self.conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM vehicles
            WHERE UPPER(vin) = UPPER(?)
            """,
            (vin.strip(),),
        )

        row = cur.fetchone()

        return dict(row) if row else None

    def get_customer_vehicles(
        self,
        customer_id: str,
    ) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM vehicles
            WHERE customer_id = ?
            """,
            (customer_id,),
        )

        return [dict(r) for r in cur.fetchall()]

    def get_customer_policies(
        self,
        customer_id: str,
    ) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()

        cur.execute(
            """
            SELECT
                p.*,
                v.make,
                v.model,
                v.year,
                v.license_plate,
                v.vin
            FROM policies p
            LEFT JOIN vehicles v
                ON p.vehicle_id = v.id
            WHERE p.customer_id = ?
            """,
            (customer_id,),
        )

        return [dict(r) for r in cur.fetchall()]

    def get_customer_claims(
        self,
        customer_id: str,
    ) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()

        cur.execute(
            """
            SELECT
                c.*,
                p.policy_number,
                p.policy_type
            FROM claims c
            LEFT JOIN policies p
                ON c.policy_id = p.id
            WHERE c.customer_id = ?
            ORDER BY c.reported_date DESC
            """,
            (customer_id,),
        )

        claims = [dict(r) for r in cur.fetchall()]

        for claim in claims:
            cur.execute(
                """
                SELECT *
                FROM claim_documents
                WHERE claim_id = ?
                """,
                (claim["id"],),
            )

            claim["documents"] = [
                dict(d)
                for d in cur.fetchall()
            ]

        return claims

    def get_customer_payments(
        self,
        customer_id: str,
    ) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM payments
            WHERE customer_id = ?
            ORDER BY payment_date DESC
            """,
            (customer_id,),
        )

        return [dict(r) for r in cur.fetchall()]

    def get_customer_renewals(
        self,
        customer_id: str,
    ) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM renewals
            WHERE customer_id = ?
            ORDER BY renewal_due_date DESC
            """,
            (customer_id,),
        )

        return [dict(r) for r in cur.fetchall()]

    def get_customer_tickets(
        self,
        customer_id: str,
    ) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM support_tickets
            WHERE customer_id = ?
            ORDER BY created_at DESC
            """,
            (customer_id,),
        )

        return [dict(r) for r in cur.fetchall()]

    def get_network_garages(
        self,
        city: Optional[str] = None,
        brand: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()

        if city:
            cur.execute(
                """
                SELECT *
                FROM network_garages
                WHERE LOWER(city) = LOWER(?)
                """,
                (city.strip(),),
            )
        else:
            cur.execute(
                "SELECT * FROM network_garages"
            )

        garages = [
            dict(r)
            for r in cur.fetchall()
        ]

        if brand:
            brand_lower = brand.lower()

            garages = [
                g
                for g in garages
                if brand_lower in (
                    g.get("supported_brands") or ""
                ).lower()
            ]

        return garages

    def get_policy_coverage_info(
        self,
        policy_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()

        if policy_type:
            cur.execute(
                """
                SELECT *
                FROM policy_coverage
                WHERE LOWER(policy_type) LIKE LOWER(?)
                """,
                (f"%{policy_type.strip()}%",),
            )
        else:
            cur.execute(
                "SELECT * FROM policy_coverage"
            )

        return [
            dict(r)
            for r in cur.fetchall()
        ]

    def extract_entities_from_text(
        self,
        text: str,
    ) -> Dict[str, List[str]]:
        """
        Extract potential policy numbers, claim numbers,
        VINs, and ticket numbers from text.
        """

        if not text:
            return {
                "policy_numbers": [],
                "claim_numbers": [],
                "vins": [],
                "ticket_numbers": [],
            }

        policies = re.findall(
            r"\bPOL-[A-Z]{2}-\d{4}-\d{4}\b|\bPOL-\d+\b",
            text,
            flags=re.IGNORECASE,
        )

        claims = re.findall(
            r"\bCLM-\d{4}-\d{5}\b|\bCLM-\d+\b",
            text,
            flags=re.IGNORECASE,
        )

        vins = re.findall(
            r"\b[A-HJ-NPR-Z0-9]{17}\b",
            text,
            flags=re.IGNORECASE,
        )

        tickets = re.findall(
            r"\bTICK-\d{4}-\d{4}\b|\bTCK-\d+\b",
            text,
            flags=re.IGNORECASE,
        )

        return {
            "policy_numbers": list(
                set(p.upper() for p in policies)
            ),
            "claim_numbers": list(
                set(c.upper() for c in claims)
            ),
            "vins": list(
                set(v.upper() for v in vins)
            ),
            "ticket_numbers": list(
                set(t.upper() for t in tickets)
            ),
        }

    def get_full_insurance_context(
        self,
        email: str,
        subject: str = "",
        body: str = "",
    ) -> Dict[str, Any]:
        """
        Retrieve full relational context for customer by email,
        with fallback entity scanning.
        """

        customer = self.find_customer_by_email(email)

        matched_via = (
            "email"
            if customer
            else None
        )

        combined_text = f"{subject} {body}"

        extracted = self.extract_entities_from_text(
            combined_text
        )

        if not customer:
            # Try resolving via policy number.
            for pol_num in extracted["policy_numbers"]:
                pol = self.find_policy_by_number(
                    pol_num
                )

                if pol and pol.get("customer_id"):
                    customer = self.find_customer_by_id(
                        pol["customer_id"]
                    )

                    if customer:
                        matched_via = (
                            f"policy_number ({pol_num})"
                        )
                        break

        if not customer:
            # Try resolving via claim number.
            for clm_num in extracted["claim_numbers"]:
                clm = self.find_claim_by_number(
                    clm_num
                )

                if clm and clm.get("customer_id"):
                    customer = self.find_customer_by_id(
                        clm["customer_id"]
                    )

                    if customer:
                        matched_via = (
                            f"claim_number ({clm_num})"
                        )
                        break

        if not customer:
            # Try resolving via VIN.
            for vin in extracted["vins"]:
                veh = self.find_vehicle_by_vin(vin)

                if veh and veh.get("customer_id"):
                    customer = self.find_customer_by_id(
                        veh["customer_id"]
                    )

                    if customer:
                        matched_via = (
                            f"vin ({vin})"
                        )
                        break

        if not customer:
            # Unidentified customer.
            return {
                "identified": False,
                "matched_via": None,
                "customer": None,
                "vehicles": [],
                "policies": [
                    self.find_policy_by_number(p)
                    for p in extracted["policy_numbers"]
                    if self.find_policy_by_number(p)
                ],
                "claims": [
                    self.find_claim_by_number(c)
                    for c in extracted["claim_numbers"]
                    if self.find_claim_by_number(c)
                ],
                "payments": [],
                "renewals": [],
                "tickets": [],
                "garages": self.get_network_garages(),
                "coverage_guides": (
                    self.get_policy_coverage_info()
                ),
                "extracted_entities": extracted,
            }

        cid = customer["id"]

        return {
            "identified": True,
            "matched_via": matched_via,
            "customer": customer,
            "vehicles": self.get_customer_vehicles(cid),
            "policies": self.get_customer_policies(cid),
            "claims": self.get_customer_claims(cid),
            "payments": self.get_customer_payments(cid),
            "renewals": self.get_customer_renewals(cid),
            "tickets": self.get_customer_tickets(cid),
            "garages": self.get_network_garages(
                city=customer.get("city")
            ),
            "coverage_guides": (
                self.get_policy_coverage_info()
            ),
            "extracted_entities": extracted,
        }

    def find_existing_ticket(
        self,
        customer_id: str,
        claim_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()

        if claim_id:
            cur.execute(
                """
                SELECT *
                FROM support_tickets
                WHERE customer_id = ?
                  AND claim_id = ?
                  AND status != 'Closed'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (customer_id, claim_id),
            )
        else:
            cur.execute(
                """
                SELECT *
                FROM support_tickets
                WHERE customer_id = ?
                  AND status != 'Closed'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (customer_id,),
            )

        row = cur.fetchone()

        return dict(row) if row else None

    def create_support_ticket(
        self,
        ticket_number: str,
        customer_id: Optional[str],
        policy_id: Optional[str],
        claim_id: Optional[str],
        subject: str,
        category: str,
        priority: str,
        status: str,
        assigned_team: str,
        email_id: Optional[str] = None,
    ) -> str:
        # uuid5, not hash(): built-in hash() is salted per process,
        # which would mint a different primary key on every run
        # for the same ticket.
        ticket_id = (
            f"TCK-ABS"
            f"{uuid.uuid5(uuid.NAMESPACE_URL, ticket_number).int % 100000000:08d}"
        )

        cur = self.conn.cursor()

        cur.execute(
            """
            INSERT INTO support_tickets (
                id,
                ticket_number,
                customer_id,
                email_id,
                policy_id,
                claim_id,
                subject,
                category,
                priority,
                status,
                assigned_team
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                ticket_number,
                customer_id,
                email_id,
                policy_id,
                claim_id,
                subject,
                category,
                priority,
                status,
                assigned_team,
            ),
        )

        self.conn.commit()

        return ticket_id

    def reopen_ticket(
        self,
        ticket_id: str,
    ) -> bool:
        """Re-open a previously resolved ticket and stamp updated_at."""

        cur = self.conn.cursor()

        cur.execute(
            """
            UPDATE support_tickets
            SET
                status = 'Open',
                updated_at = ?
            WHERE id = ?
               OR ticket_number = ?
            """,
            (
                datetime.now().isoformat(),
                ticket_id,
                ticket_id,
            ),
        )

        self.conn.commit()

        return cur.rowcount > 0

    def save_triage_record(
        self,
        record: Dict[str, Any],
    ) -> int:
        # triage_records carries both `detected_intent` (NOT NULL)
        # and the legacy `intent` column, plus
        # routed_to/reason/confidence/human_review_required.
        # Normalize whatever the caller supplied into the
        # full required shape.

        row = dict(record)

        intent = (
            row.get("detected_intent")
            or row.get("intent")
            or "GENERAL_QUERY"
        )

        row["detected_intent"] = intent
        row["intent"] = intent

        row.setdefault(
            "priority",
            "Medium",
        )

        row.setdefault(
            "confidence",
            0.95,
        )

        row.setdefault(
            "reason",
            row.get("summary", ""),
        )

        row.setdefault(
            "routed_to",
            row.get(
                "routing_desk",
                "General Support",
            ),
        )

        row.setdefault(
            "human_review_required",
            bool(
                row.get("escalation_needed")
            ),
        )

        cur = self.conn.cursor()

        cur.execute(
            """
            INSERT INTO triage_records (
                email_id,
                sender_email,
                sender_name,
                subject,
                received_date,
                customer_id,
                policy_number,
                claim_number,
                category,
                detected_intent,
                intent,
                priority,
                urgency_score,
                sentiment,
                escalation_needed,
                escalation_status,
                summary,
                reason,
                suggested_reply,
                routed_to,
                routing_desk,
                confidence,
                human_review_required,
                processing_status,
                reply_status,
                reply_sent_at
            )
            VALUES (
                :email_id,
                :sender_email,
                :sender_name,
                :subject,
                :received_date,
                :customer_id,
                :policy_number,
                :claim_number,
                :category,
                :detected_intent,
                :intent,
                :priority,
                :urgency_score,
                :sentiment,
                :escalation_needed,
                :escalation_status,
                :summary,
                :reason,
                :suggested_reply,
                :routed_to,
                :routing_desk,
                :confidence,
                :human_review_required,
                :processing_status,
                :reply_status,
                :reply_sent_at
            )
            """,
            row,
        )

        self.conn.commit()

        return cur.lastrowid

    def get_all_triage_records(
        self,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM triage_records
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )

        return [
            dict(r)
            for r in cur.fetchall()
        ]

    def is_email_processed(
        self,
        email_id: str,
    ) -> bool:
        cur = self.conn.cursor()

        cur.execute(
            """
            SELECT 1
            FROM triage_records
            WHERE email_id = ?
            """,
            (str(email_id),),
        )

        return cur.fetchone() is not None

    # --- Durable reply audit trail -------------------------------------
    # The dispatcher keeps an in-memory log that dies with the process.
    # These methods persist the same events so an auditor can answer
    # "what did the system actually send, suppress, rate-limit,
    # or fail on?" after a restart.

    def record_reply_audit(
        self,
        *,
        timestamp: str,
        status: str,
        message_id: Optional[str] = None,
        recipient: Optional[str] = None,
        subject: Optional[str] = None,
        error_details: Optional[str] = None,
        in_reply_to: Optional[str] = None,
        email_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        intent: Optional[str] = None,
        routing_desk: Optional[str] = None,
        body_sha256: Optional[str] = None,
        body_chars: Optional[int] = None,
        dry_run: bool = False,
    ) -> int:
        """
        Persist a single reply-dispatch event.

        Returns the new row id.

        A failure to write the audit row must never prevent a
        reply from being sent, so this commits independently of
        the caller's transaction and the pipeline treats
        audit-write errors as non-fatal.
        """

        cur = self.conn.cursor()

        cur.execute(
            """
            INSERT INTO reply_audit (
                timestamp,
                message_id,
                recipient,
                subject,
                status,
                error_details,
                in_reply_to,
                email_id,
                customer_id,
                intent,
                routing_desk,
                body_sha256,
                body_chars,
                dry_run
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                timestamp,
                message_id,
                recipient,
                subject,
                status,
                error_details,
                in_reply_to,
                email_id,
                customer_id,
                intent,
                routing_desk,
                body_sha256,
                body_chars,
                1 if dry_run else 0,
            ),
        )

        self.conn.commit()

        return int(
            cur.lastrowid or 0
        )

    def get_reply_audit(
        self,
        limit: int = 200,
        status: Optional[str] = None,
        recipient: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return audit rows newest-first, optionally filtered."""

        clauses: List[str] = []
        params: List[Any] = []

        if status:
            clauses.append(
                "status = ?"
            )
            params.append(status)

        if recipient:
            clauses.append(
                "recipient = ?"
            )
            params.append(recipient)

        where = (
            f" WHERE {' AND '.join(clauses)}"
            if clauses
            else ""
        )

        params.append(limit)

        cur = self.conn.cursor()

        cur.execute(
            f"""
            SELECT *
            FROM reply_audit
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        )

        return [
            dict(r)
            for r in cur.fetchall()
        ]

    def get_reply_audit_stats(
        self,
    ) -> Dict[str, int]:
        """
        Count audit rows by status,
        for monitoring and compliance reporting.
        """

        cur = self.conn.cursor()

        cur.execute(
            """
            SELECT
                status,
                COUNT(*)
            FROM reply_audit
            GROUP BY status
            """
        )

        return {
            row[0]: int(row[1])
            for row in cur.fetchall()
        }