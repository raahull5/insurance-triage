"""Database schema definitions and table creation for SQLite."""

import sqlite3
import logging

logger = logging.getLogger(__name__)

CREATE_TABLES_SQL = """
-- 1. Customers table
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    phone TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    postal_code TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);

-- 2. Vehicles table
CREATE TABLE IF NOT EXISTS vehicles (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    vin TEXT UNIQUE NOT NULL,
    make TEXT NOT NULL,
    model TEXT NOT NULL,
    year INTEGER NOT NULL,
    license_plate TEXT,
    color TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(customer_id) REFERENCES customers(id)
);

CREATE INDEX IF NOT EXISTS idx_vehicles_customer ON vehicles(customer_id);
CREATE INDEX IF NOT EXISTS idx_vehicles_vin ON vehicles(vin);

-- 3. Policies table
CREATE TABLE IF NOT EXISTS policies (
    id TEXT PRIMARY KEY,
    policy_number TEXT UNIQUE NOT NULL,
    customer_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    type TEXT NOT NULL,
    policy_type TEXT,
    status TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    premium REAL NOT NULL,
    premium_amount REAL,
    deductible REAL NOT NULL,
    coverage_limit REAL DEFAULT 0.0,
    no_claim_bonus_pct REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(customer_id) REFERENCES customers(id),
    FOREIGN KEY(vehicle_id) REFERENCES vehicles(id)
);

CREATE INDEX IF NOT EXISTS idx_policies_customer ON policies(customer_id);
CREATE INDEX IF NOT EXISTS idx_policies_number ON policies(policy_number);

-- 4. Claims table
CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    claim_number TEXT UNIQUE NOT NULL,
    policy_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    incident_date DATE NOT NULL,
    reported_date DATE,
    status TEXT NOT NULL,
    description TEXT,
    damage_description TEXT,
    estimated_payout REAL DEFAULT 0.0,
    claimed_amount REAL DEFAULT 0.0,
    actual_payout REAL DEFAULT 0.0,
    approved_amount REAL DEFAULT 0.0,
    delay_reason TEXT,
    rejection_reason TEXT,
    assigned_adjuster TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(policy_id) REFERENCES policies(id),
    FOREIGN KEY(customer_id) REFERENCES customers(id)
);

CREATE INDEX IF NOT EXISTS idx_claims_customer ON claims(customer_id);
CREATE INDEX IF NOT EXISTS idx_claims_number ON claims(claim_number);
CREATE INDEX IF NOT EXISTS idx_claims_policy ON claims(policy_id);

-- 5. Claim Documents table
CREATE TABLE IF NOT EXISTS claim_documents (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    file_path TEXT,
    document_name TEXT,
    status TEXT DEFAULT 'RECEIVED',
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    FOREIGN KEY(claim_id) REFERENCES claims(id)
);

CREATE INDEX IF NOT EXISTS idx_claim_docs_claim ON claim_documents(claim_id);

-- 6. Payments table
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    amount REAL NOT NULL,
    payment_date DATE NOT NULL,
    status TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    transaction_reference TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(policy_id) REFERENCES policies(id),
    FOREIGN KEY(customer_id) REFERENCES customers(id)
);

CREATE INDEX IF NOT EXISTS idx_payments_policy ON payments(policy_id);
CREATE INDEX IF NOT EXISTS idx_payments_customer ON payments(customer_id);

-- 7. Renewals table
CREATE TABLE IF NOT EXISTS renewals (
    id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    renewal_date DATE NOT NULL,
    renewal_due_date DATE,
    status TEXT NOT NULL,
    new_premium REAL NOT NULL,
    quoted_premium REAL,
    ncb_discount_pct REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(policy_id) REFERENCES policies(id),
    FOREIGN KEY(customer_id) REFERENCES customers(id)
);

CREATE INDEX IF NOT EXISTS idx_renewals_policy ON renewals(policy_id);
CREATE INDEX IF NOT EXISTS idx_renewals_customer ON renewals(customer_id);

-- 8. Network Garages table
CREATE TABLE IF NOT EXISTS network_garages (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    zip_code TEXT NOT NULL,
    postal_code TEXT,
    phone TEXT NOT NULL,
    rating REAL DEFAULT 4.5,
    services TEXT NOT NULL,
    supported_brands TEXT,
    cashless_available BOOLEAN DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_garages_city ON network_garages(city);
CREATE INDEX IF NOT EXISTS idx_garages_zip ON network_garages(zip_code);

-- 9. Support Tickets table
CREATE TABLE IF NOT EXISTS support_tickets (
    id TEXT PRIMARY KEY,
    ticket_number TEXT UNIQUE,
    customer_id TEXT,
    email_id TEXT,
    policy_id TEXT,
    claim_id TEXT,
    subject TEXT NOT NULL,
    category TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL,
    assigned_team TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(customer_id) REFERENCES customers(id),
    FOREIGN KEY(policy_id) REFERENCES policies(id),
    FOREIGN KEY(claim_id) REFERENCES claims(id)
);

CREATE INDEX IF NOT EXISTS idx_tickets_customer ON support_tickets(customer_id);
CREATE INDEX IF NOT EXISTS idx_tickets_claim ON support_tickets(claim_id);
CREATE INDEX IF NOT EXISTS idx_tickets_email ON support_tickets(email_id);

-- 10. Policy Coverage table
CREATE TABLE IF NOT EXISTS policy_coverage (
    id TEXT PRIMARY KEY,
    policy_id TEXT,
    coverage_type TEXT NOT NULL,
    limit_amount REAL NOT NULL,
    deductible REAL NOT NULL,
    description TEXT,
    limits_clause TEXT,
    exclusions TEXT,
    FOREIGN KEY(policy_id) REFERENCES policies(id)
);

CREATE INDEX IF NOT EXISTS idx_policy_coverage_policy ON policy_coverage(policy_id);

-- 11. Triage Records table
CREATE TABLE IF NOT EXISTS triage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id TEXT UNIQUE NOT NULL,
    customer_id TEXT,
    detected_intent TEXT NOT NULL,
    priority TEXT NOT NULL,
    confidence REAL DEFAULT 0.95,
    reason TEXT,
    routed_to TEXT,
    suggested_reply TEXT,
    human_review_required BOOLEAN DEFAULT 0,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sender_email TEXT,
    sender_name TEXT,
    subject TEXT,
    received_date TEXT,
    policy_number TEXT,
    claim_number TEXT,
    category TEXT,
    intent TEXT,
    urgency_score INTEGER DEFAULT 1,
    sentiment TEXT,
    escalation_needed BOOLEAN DEFAULT 0,
    escalation_status TEXT DEFAULT 'None',
    summary TEXT,
    routing_desk TEXT,
    processing_status TEXT DEFAULT 'Completed',
    reply_status TEXT DEFAULT 'Pending',
    reply_sent_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(customer_id) REFERENCES customers(id)
);

CREATE INDEX IF NOT EXISTS idx_triage_email_id ON triage_records(email_id);
CREATE INDEX IF NOT EXISTS idx_triage_customer ON triage_records(customer_id);
CREATE INDEX IF NOT EXISTS idx_triage_priority ON triage_records(priority);
CREATE INDEX IF NOT EXISTS idx_triage_intent ON triage_records(detected_intent);

-- Durable audit trail for every reply dispatch attempt.
-- The dispatcher's in-memory log dies with the process; for an enterprise
-- system the record of what was sent, suppressed, rate-limited, or failed must
-- survive a restart so the trail is auditable after the fact. Note that this
-- deliberately does NOT store the reply body: the body can contain record
-- details, and retaining it here would create a second, less-protected copy of
-- customer data outside the access path used for triage.
CREATE TABLE IF NOT EXISTS reply_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    message_id TEXT,
    recipient TEXT,
    subject TEXT,
    status TEXT NOT NULL,
    error_details TEXT,
    in_reply_to TEXT,
    email_id TEXT,
    customer_id TEXT,
    intent TEXT,
    routing_desk TEXT,
    body_sha256 TEXT,
    body_chars INTEGER,
    dry_run BOOLEAN DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_reply_audit_timestamp ON reply_audit(timestamp);
CREATE INDEX IF NOT EXISTS idx_reply_audit_status ON reply_audit(status);
CREATE INDEX IF NOT EXISTS idx_reply_audit_message ON reply_audit(message_id);
CREATE INDEX IF NOT EXISTS idx_reply_audit_recipient ON reply_audit(recipient);
"""


def create_tables(conn: sqlite3.Connection) -> None:
    """Execute schema creation statements."""
    cursor = conn.cursor()
    cursor.executescript(CREATE_TABLES_SQL)
    conn.commit()
    logger.info("Database schema initialized successfully.")
