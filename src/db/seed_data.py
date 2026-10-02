"""Seed rich, realistic, interconnected insurance test data for development and testing."""

import sqlite3
from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)

SAMPLE_CUSTOMERS = [
    {
        "id": "CUST-1001",
        "name": "Sarah Jenkins",
        "email": "sarah.jenkins@example.com",
        "phone": "+1-555-019-2831",
        "address": "742 Evergreen Terrace",
        "city": "Springfield",
        "state": "IL",
        "postal_code": "62704",
    },
    {
        "id": "CUST-1002",
        "name": "Marcus Vance",
        "email": "marcus.vance@example.com",
        "phone": "+1-555-014-9922",
        "address": "1204 Elmhurst Lane",
        "city": "Austin",
        "state": "TX",
        "postal_code": "78701",
    },
    {
        "id": "CUST-1003",
        "name": "Elena Rostova",
        "email": "elena.rostova@example.com",
        "phone": "+1-555-018-3341",
        "address": "450 Bellevue Way NE",
        "city": "Seattle",
        "state": "WA",
        "postal_code": "98101",
    },
    {
        "id": "CUST-1004",
        "name": "David Chen",
        "email": "david.chen@example.com",
        "phone": "+1-555-017-7721",
        "address": "88 Riverside Blvd",
        "city": "New York",
        "state": "NY",
        "postal_code": "10024",
    },
    {
        "id": "CUST-1005",
        "name": "Priya Patel",
        "email": "priya.patel@example.com",
        "phone": "+1-555-013-4411",
        "address": "330 N Wabash Ave",
        "city": "Chicago",
        "state": "IL",
        "postal_code": "60611",
    },
]

SAMPLE_VEHICLES = [
    {
        "id": "VEH-101",
        "customer_id": "CUST-1001",
        "make": "Honda",
        "model": "CR-V",
        "year": 2022,
        "vin": "1HGCR2F83NA109283",
        "license_plate": "IL-789-XYZ",
        "color": "Sonic Gray Pearl",
    },
    {
        "id": "VEH-102",
        "customer_id": "CUST-1002",
        "make": "Tesla",
        "model": "Model 3",
        "year": 2023,
        "vin": "5YJ3E1EB8PF891023",
        "license_plate": "TX-420-EVP",
        "color": "Solid Black",
    },
    {
        "id": "VEH-103",
        "customer_id": "CUST-1003",
        "make": "Toyota",
        "model": "RAV4 Hybrid",
        "year": 2021,
        "vin": "2T3P1RFV5MW449912",
        "license_plate": "WA-881-TRK",
        "color": "Silver Sky Metallic",
    },
    {
        "id": "VEH-104",
        "customer_id": "CUST-1004",
        "make": "BMW",
        "model": "330i xDrive",
        "year": 2024,
        "vin": "WBA5R1C56NFP12948",
        "license_plate": "NY-BMR-330",
        "color": "Mineral Grey",
    },
    {
        "id": "VEH-105",
        "customer_id": "CUST-1005",
        "make": "Audi",
        "model": "Q5 Quattro",
        "year": 2023,
        "vin": "WAUZZZF27PA098124",
        "license_plate": "IL-AUD-992",
        "color": "Ibis White",
    },
]

SAMPLE_POLICIES = [
    {
        "id": "POL-9001",
        "policy_number": "POL-IL-2024-8819",
        "customer_id": "CUST-1001",
        "vehicle_id": "VEH-101",
        "type": "Comprehensive Auto",
        "policy_type": "Comprehensive Auto",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "status": "Active",
        "premium": 1250.00,
        "premium_amount": 1250.00,
        "deductible": 500.00,
        "coverage_limit": 100000.00,
        "no_claim_bonus_pct": 20.0,
    },
    {
        "id": "POL-9002",
        "policy_number": "POL-TX-2024-3401",
        "customer_id": "CUST-1002",
        "vehicle_id": "VEH-102",
        "type": "Comprehensive EV Premier",
        "policy_type": "Comprehensive EV Premier",
        "start_date": "2024-03-15",
        "end_date": "2025-03-14",
        "status": "Active",
        "premium": 1850.00,
        "premium_amount": 1850.00,
        "deductible": 1000.00,
        "coverage_limit": 250000.00,
        "no_claim_bonus_pct": 10.0,
    },
    {
        "id": "POL-9003",
        "policy_number": "POL-WA-2023-7712",
        "customer_id": "CUST-1003",
        "vehicle_id": "VEH-103",
        "type": "Standard Collision & Comprehensive",
        "policy_type": "Standard Collision & Comprehensive",
        "start_date": "2023-10-01",
        "end_date": "2024-09-30",
        "status": "Expiring Soon",
        "premium": 980.00,
        "premium_amount": 980.00,
        "deductible": 750.00,
        "coverage_limit": 75000.00,
        "no_claim_bonus_pct": 35.0,
    },
    {
        "id": "POL-9004",
        "policy_number": "POL-NY-2024-1190",
        "customer_id": "CUST-1004",
        "vehicle_id": "VEH-104",
        "type": "Gold Executive Auto",
        "policy_type": "Gold Executive Auto",
        "start_date": "2024-05-01",
        "end_date": "2025-04-30",
        "status": "Active",
        "premium": 2400.00,
        "premium_amount": 2400.00,
        "deductible": 500.00,
        "coverage_limit": 500000.00,
        "no_claim_bonus_pct": 0.0,
    },
    {
        "id": "POL-9005",
        "policy_number": "POL-IL-2023-0044",
        "customer_id": "CUST-1005",
        "vehicle_id": "VEH-105",
        "type": "Liability Only",
        "policy_type": "Liability Only",
        "start_date": "2023-01-01",
        "end_date": "2023-12-31",
        "status": "Expired",
        "premium": 650.00,
        "premium_amount": 650.00,
        "deductible": 1000.00,
        "coverage_limit": 50000.00,
        "no_claim_bonus_pct": 0.0,
    },
]

SAMPLE_CLAIMS = [
    {
        "id": "CLM-5001",
        "claim_number": "CLM-2024-09112",
        "policy_id": "POL-9001",
        "customer_id": "CUST-1001",
        "incident_date": "2024-08-14",
        "reported_date": "2024-08-15",
        "status": "In Review",
        "description": "Front bumper and right headlight cracked due to low-speed collision in parking lot.",
        "damage_description": "Front bumper and right headlight cracked due to low-speed collision in parking lot.",
        "estimated_payout": 4200.00,
        "claimed_amount": 4200.00,
        "actual_payout": 0.0,
        "approved_amount": 0.0,
        "delay_reason": "Awaiting repair estimate from approved workshop.",
        "rejection_reason": None,
        "assigned_adjuster": "Robert Sterling (robert.sterling@apexshield.com)",
    },
    {
        "id": "CLM-5002",
        "claim_number": "CLM-2024-04190",
        "policy_id": "POL-9002",
        "customer_id": "CUST-1002",
        "incident_date": "2024-07-02",
        "reported_date": "2024-07-02",
        "status": "Approved",
        "description": "Pebble impact cracked front windshield while driving on highway.",
        "damage_description": "Pebble impact cracked front windshield while driving on highway.",
        "estimated_payout": 1150.00,
        "claimed_amount": 1150.00,
        "actual_payout": 1150.00,
        "approved_amount": 1150.00,
        "delay_reason": None,
        "rejection_reason": None,
        "assigned_adjuster": "Claire Henderson (claire.henderson@apexshield.com)",
    },
    {
        "id": "CLM-5003",
        "claim_number": "CLM-2024-07103",
        "policy_id": "POL-9003",
        "customer_id": "CUST-1003",
        "incident_date": "2024-06-10",
        "reported_date": "2024-06-11",
        "status": "Delayed",
        "description": "Rear quarter panel dented and taillight assembly crushed in parking garage hit-and-run.",
        "damage_description": "Rear quarter panel dented and taillight assembly crushed in parking garage hit-and-run.",
        "estimated_payout": 3400.00,
        "claimed_amount": 3400.00,
        "actual_payout": 0.0,
        "approved_amount": 0.0,
        "delay_reason": "Replacement OEM taillight module is on national backorder with estimated arrival in 10 business days.",
        "rejection_reason": None,
        "assigned_adjuster": "Robert Sterling (robert.sterling@apexshield.com)",
    },
    {
        "id": "CLM-5004",
        "claim_number": "CLM-2024-03110",
        "policy_id": "POL-9005",
        "customer_id": "CUST-1005",
        "incident_date": "2024-02-20",
        "reported_date": "2024-02-21",
        "status": "Rejected",
        "description": "Vehicle collision damage occurred during commercial ridesharing trip outside policy term.",
        "damage_description": "Vehicle collision damage occurred during commercial ridesharing trip outside policy term.",
        "estimated_payout": 5500.00,
        "claimed_amount": 5500.00,
        "actual_payout": 0.0,
        "approved_amount": 0.0,
        "delay_reason": None,
        "rejection_reason": "Policy POL-IL-2023-0044 lapsed on 2023-12-31 prior to incident date, and commercial ride-sharing is excluded under Section 4.2 of standard policy terms.",
        "assigned_adjuster": "David Thorne (david.thorne@apexshield.com)",
    },
]

SAMPLE_CLAIM_DOCUMENTS = [
    {
        "id": "DOC-701",
        "claim_id": "CLM-5001",
        "document_name": "police_report_incident_882.pdf",
        "document_type": "Police Report",
        "file_path": "/documents/claims/2024/police_report_incident_882.pdf",
        "status": "VERIFIED",
        "notes": "Verified officer badge #441 Springfield PD.",
    },
    {
        "id": "DOC-702",
        "claim_id": "CLM-5001",
        "document_name": "repair_estimate_autobody.pdf",
        "document_type": "Repair Estimate",
        "file_path": "/documents/claims/2024/repair_estimate_autobody.pdf",
        "status": "PENDING",
        "notes": "Awaiting final itemized parts quote from garage.",
    },
    {
        "id": "DOC-703",
        "claim_id": "CLM-5002",
        "document_name": "windshield_damage_photo1.jpg",
        "document_type": "Damage Photos",
        "file_path": "/documents/claims/2024/windshield_damage_photo1.jpg",
        "status": "VERIFIED",
        "notes": "High resolution photo showing point-of-impact crack.",
    },
    {
        "id": "DOC-704",
        "claim_id": "CLM-5003",
        "document_name": "hit_and_run_cctv_summary.pdf",
        "document_type": "Police Report",
        "file_path": "/documents/claims/2024/hit_and_run_cctv_summary.pdf",
        "status": "VERIFIED",
        "notes": "Seattle PD incident report #SEA-2024-9910.",
    },
]

SAMPLE_PAYMENTS = [
    {
        "id": "PAY-301",
        "customer_id": "CUST-1001",
        "policy_id": "POL-9001",
        "amount": 1250.00,
        "payment_date": "2023-12-28",
        "status": "SUCCESSFUL",
        "payment_method": "Credit Card (Visa ending 4112)",
        "transaction_reference": "TXN-CC-99481029",
    },
    {
        "id": "PAY-302",
        "customer_id": "CUST-1002",
        "policy_id": "POL-9002",
        "amount": 1850.00,
        "payment_date": "2024-03-14",
        "status": "SUCCESSFUL",
        "payment_method": "ACH Bank Transfer (ending 8820)",
        "transaction_reference": "TXN-ACH-4410298",
    },
    {
        "id": "PAY-303",
        "customer_id": "CUST-1003",
        "policy_id": "POL-9003",
        "amount": 980.00,
        "payment_date": "2024-09-15",
        "status": "PENDING",
        "payment_method": "Credit Card (MasterCard ending 9011)",
        "transaction_reference": "TXN-CC-11092834",
    },
    {
        "id": "PAY-304",
        "customer_id": "CUST-1005",
        "policy_id": "POL-9005",
        "amount": 650.00,
        "payment_date": "2023-12-30",
        "status": "FAILED",
        "payment_method": "Debit Card (ending 2341)",
        "transaction_reference": "TXN-DC-FAIL-8812",
    },
]

SAMPLE_RENEWALS = [
    {
        "id": "REN-201",
        "policy_id": "POL-9003",
        "customer_id": "CUST-1003",
        "renewal_date": "2024-09-30",
        "renewal_due_date": "2024-09-30",
        "status": "OFFERED",
        "new_premium": 890.00,
        "quoted_premium": 890.00,
        "ncb_discount_pct": 40.0,
    },
    {
        "id": "REN-202",
        "policy_id": "POL-9005",
        "customer_id": "CUST-1005",
        "renewal_date": "2024-01-01",
        "renewal_due_date": "2024-01-01",
        "status": "LAPSED",
        "new_premium": 680.00,
        "quoted_premium": 680.00,
        "ncb_discount_pct": 0.0,
    },
]

SAMPLE_GARAGES = [
    {
        "id": "GAR-01",
        "name": "Precision Auto Body & EV Care",
        "address": "1050 North Grand Ave E",
        "city": "Springfield",
        "state": "IL",
        "zip_code": "62704",
        "postal_code": "62704",
        "phone": "+1-217-555-0199",
        "rating": 4.8,
        "services": "Collision Repair, Paint Matching, ADAS Calibration, Glass Replacement, Towing Intake",
        "supported_brands": "Honda, Toyota, Ford, Chevrolet, Nissan",
        "cashless_available": 1,
    },
    {
        "id": "GAR-02",
        "name": "Austin Tesla & Euro Collision Center",
        "address": "400 E 6th St",
        "city": "Austin",
        "state": "TX",
        "zip_code": "78701",
        "postal_code": "78701",
        "phone": "+1-512-555-0144",
        "rating": 4.9,
        "services": "Certified EV Repair, Aluminum Body Repair, Battery Systems, Paint Protection, Fast-Track Glass",
        "supported_brands": "Tesla, BMW, Audi, Mercedes-Benz, Porsche",
        "cashless_available": 1,
    },
    {
        "id": "GAR-03",
        "name": "Emerald City Certified Repairs",
        "address": "1520 4th Ave",
        "city": "Seattle",
        "state": "WA",
        "zip_code": "98101",
        "postal_code": "98101",
        "phone": "+1-206-555-0188",
        "rating": 4.7,
        "services": "Frame Straightening, Hybrid Powertrain Bodywork, Windshield Calibration, Cashless Settling",
        "supported_brands": "Toyota, Subaru, Honda, Mazda, Hyundai",
        "cashless_available": 1,
    },
    {
        "id": "GAR-04",
        "name": "Windy City Premier Collision & Glass",
        "address": "2100 S Michigan Ave",
        "city": "Chicago",
        "state": "IL",
        "zip_code": "60616",
        "postal_code": "60616",
        "phone": "+1-312-555-0180",
        "rating": 4.9,
        "services": "24/7 Roadside Drop-off, Full Structural Repair, OEM Parts Guarantee, Luxury Detailing",
        "supported_brands": "Audi, BMW, Mercedes-Benz, Honda, Ford",
        "cashless_available": 1,
    },
]

SAMPLE_COVERAGE = [
    {
        "id": "COV-01",
        "policy_id": "POL-9001",
        "coverage_type": "Collision & Zero Depreciation",
        "limit_amount": 100000.00,
        "deductible": 500.00,
        "description": "Covers complete replacement cost of damaged parts without factoring in age depreciation.",
        "limits_clause": "Valid for vehicles under 5 years old. Max 2 claims per policy year.",
        "exclusions": "Tires and tubes wear & tear unless damaged in covered accident.",
    },
    {
        "id": "COV-02",
        "policy_id": "POL-9001",
        "coverage_type": "24/7 Roadside Assistance & Towing",
        "limit_amount": 5000.00,
        "deductible": 0.00,
        "description": "Emergency on-site breakdown support, battery jumpstart, flat-tire change, and free towing up to 50 miles to the nearest network garage.",
        "limits_clause": "Towing covered up to 50 miles. Unlimited service calls per year.",
        "exclusions": "Commercial use or unauthorized towing operators.",
    },
    {
        "id": "COV-03",
        "policy_id": "POL-9002",
        "coverage_type": "Comprehensive EV Battery & Glass",
        "limit_amount": 250000.00,
        "deductible": 1000.00,
        "description": "Full battery pack protection against water ingress and road debris, plus $0-deductible windshield glass repairs.",
        "limits_clause": "OEM certified EV service centers only.",
        "exclusions": "Intentional submergence or non-certified software modifications.",
    },
    {
        "id": "COV-04",
        "policy_id": "POL-9003",
        "coverage_type": "No Claim Bonus (NCB) Protection",
        "limit_amount": 75000.00,
        "deductible": 750.00,
        "description": "Preserves accumulated discount tier (up to 50%) even after registering 1 claim during the policy term.",
        "limits_clause": "Applicable for 1 claim per policy period.",
        "exclusions": "Not applicable if total write-off or theft occurs.",
    },
]

SAMPLE_TICKETS = [
    {
        "id": "TCK-8001",
        "ticket_number": "TICK-2024-8801",
        "customer_id": "CUST-1001",
        "email_id": "MSG-INIT-001",
        "policy_id": "POL-9001",
        "claim_id": "CLM-5001",
        "subject": "Status update inquiry for collision claim CLM-2024-09112",
        "category": "Claims",
        "priority": "High",
        "status": "In Progress",
        "assigned_team": "Claims Triage Desk",
    },
    {
        "id": "TCK-8002",
        "ticket_number": "TICK-2024-8802",
        "customer_id": "CUST-1003",
        "email_id": "MSG-INIT-002",
        "policy_id": "POL-9003",
        "claim_id": "CLM-5003",
        "subject": "Delay investigation for OEM parts CLM-2024-07103",
        "category": "Claims",
        "priority": "Medium",
        "status": "Pending Customer Response",
        "assigned_team": "Parts & Logistics Desk",
    },
]


def seed_database(conn: sqlite3.Connection) -> None:
    """Seed sample data into SQLite database."""
    cur = conn.cursor()

    cur.executemany(
        """INSERT OR REPLACE INTO customers 
           (id, name, email, phone, address, city, state, postal_code) 
           VALUES (:id, :name, :email, :phone, :address, :city, :state, :postal_code)""",
        SAMPLE_CUSTOMERS,
    )

    cur.executemany(
        """INSERT OR REPLACE INTO vehicles 
           (id, customer_id, make, model, year, vin, license_plate, color) 
           VALUES (:id, :customer_id, :make, :model, :year, :vin, :license_plate, :color)""",
        SAMPLE_VEHICLES,
    )

    cur.executemany(
        """INSERT OR REPLACE INTO policies 
           (id, policy_number, customer_id, vehicle_id, type, policy_type, start_date, end_date, status, premium, premium_amount, deductible, coverage_limit, no_claim_bonus_pct) 
           VALUES (:id, :policy_number, :customer_id, :vehicle_id, :type, :policy_type, :start_date, :end_date, :status, :premium, :premium_amount, :deductible, :coverage_limit, :no_claim_bonus_pct)""",
        SAMPLE_POLICIES,
    )

    cur.executemany(
        """INSERT OR REPLACE INTO claims 
           (id, claim_number, policy_id, customer_id, incident_date, reported_date, status, description, damage_description, estimated_payout, claimed_amount, actual_payout, approved_amount, delay_reason, rejection_reason, assigned_adjuster) 
           VALUES (:id, :claim_number, :policy_id, :customer_id, :incident_date, :reported_date, :status, :description, :damage_description, :estimated_payout, :claimed_amount, :actual_payout, :approved_amount, :delay_reason, :rejection_reason, :assigned_adjuster)""",
        SAMPLE_CLAIMS,
    )

    cur.executemany(
        """INSERT OR REPLACE INTO claim_documents 
           (id, claim_id, document_name, document_type, file_path, status, notes) 
           VALUES (:id, :claim_id, :document_name, :document_type, :file_path, :status, :notes)""",
        SAMPLE_CLAIM_DOCUMENTS,
    )

    cur.executemany(
        """INSERT OR REPLACE INTO payments 
           (id, customer_id, policy_id, amount, payment_date, status, payment_method, transaction_reference) 
           VALUES (:id, :customer_id, :policy_id, :amount, :payment_date, :status, :payment_method, :transaction_reference)""",
        SAMPLE_PAYMENTS,
    )

    cur.executemany(
        """INSERT OR REPLACE INTO renewals 
           (id, policy_id, customer_id, renewal_date, renewal_due_date, status, new_premium, quoted_premium, ncb_discount_pct) 
           VALUES (:id, :policy_id, :customer_id, :renewal_date, :renewal_due_date, :status, :new_premium, :quoted_premium, :ncb_discount_pct)""",
        SAMPLE_RENEWALS,
    )

    cur.executemany(
        """INSERT OR REPLACE INTO network_garages 
           (id, name, address, city, state, zip_code, postal_code, phone, rating, services, supported_brands, cashless_available) 
           VALUES (:id, :name, :address, :city, :state, :zip_code, :postal_code, :phone, :rating, :services, :supported_brands, :cashless_available)""",
        SAMPLE_GARAGES,
    )

    cur.executemany(
        """INSERT OR REPLACE INTO policy_coverage 
           (id, policy_id, coverage_type, limit_amount, deductible, description, limits_clause, exclusions) 
           VALUES (:id, :policy_id, :coverage_type, :limit_amount, :deductible, :description, :limits_clause, :exclusions)""",
        SAMPLE_COVERAGE,
    )

    cur.executemany(
        """INSERT OR REPLACE INTO support_tickets 
           (id, ticket_number, customer_id, email_id, policy_id, claim_id, subject, category, priority, status, assigned_team) 
           VALUES (:id, :ticket_number, :customer_id, :email_id, :policy_id, :claim_id, :subject, :category, :priority, :status, :assigned_team)""",
        SAMPLE_TICKETS,
    )

    conn.commit()
    logger.info("Seeded realistic insurance data successfully.")


def verify_seed_integrity(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Execute validation and relationship integrity queries."""
    cur = conn.cursor()
    
    # Check foreign key violations
    cur.execute("PRAGMA foreign_key_check")
    fk_violations = cur.fetchall()

    counts = {}
    tables = [
        "customers", "vehicles", "policies", "claims", "claim_documents",
        "payments", "renewals", "network_garages", "policy_coverage", "support_tickets"
    ]
    for tbl in tables:
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        counts[tbl] = cur.fetchone()[0]

    return {
        "foreign_key_violations": len(fk_violations),
        "counts": counts,
        "status": "VALID" if len(fk_violations) == 0 else "INVALID_FOREIGN_KEYS"
    }
