"""Insurance intent definitions, taxonomy, category mappings, default priorities, and routing destinations."""

from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, List, Optional


class Priority(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class IntentCategory(str, Enum):
    CLAIMS = "Claims"
    POLICY_COVERAGE = "Policy & Coverage"
    GARAGE_ROADSIDE = "Garage & Roadside"
    SUPPORT_BILLING = "General Support & Billing"
    SYSTEM = "System & Spam"


@dataclass(frozen=True)
class IntentInfo:
    code: str
    name: str
    category: IntentCategory
    priority: Priority
    routing_destination: str
    description: str


# 26 Detailed Insurance Intent Definitions
ALL_INTENTS: List[IntentInfo] = [
    # 1. Claims
    IntentInfo(
        code="NEW_CLAIM",
        name="FNOL / New Claim Submission",
        category=IntentCategory.CLAIMS,
        priority=Priority.CRITICAL,
        routing_destination="Claims Intake & FNOL Desk",
        description="First notice of loss or reporting a new accident/incident to register a claim."
    ),
    IntentInfo(
        code="CLAIM_STATUS",
        name="Claim Status Inquiry",
        category=IntentCategory.CLAIMS,
        priority=Priority.HIGH,
        routing_destination="Claims Triage Desk",
        description="Customer inquiring about current progress, stage, or approval status of existing claim."
    ),
    IntentInfo(
        code="CLAIM_DELAY",
        name="Claim Delay Escalation",
        category=IntentCategory.CLAIMS,
        priority=Priority.CRITICAL,
        routing_destination="Senior Claims Supervisor Desk",
        description="Escalation due to delay or overdue resolution of an active claim."
    ),
    IntentInfo(
        code="CLAIM_REJECTION_DISPUTE",
        name="Claim Rejection Dispute / Appeal",
        category=IntentCategory.CLAIMS,
        priority=Priority.CRITICAL,
        routing_destination="Claims Appeals & Grievance Desk",
        description="Disputing a denied or rejected claim decision."
    ),
    IntentInfo(
        code="CLAIM_DOCUMENTS",
        name="Claim Document Submission",
        category=IntentCategory.CLAIMS,
        priority=Priority.HIGH,
        routing_destination="Claims Document Review Team",
        description="Submitting photos, police reports, or repair invoices for an open claim."
    ),
    IntentInfo(
        code="SETTLEMENT_INQUIRY",
        name="Settlement & Payout Inquiry",
        category=IntentCategory.CLAIMS,
        priority=Priority.HIGH,
        routing_destination="Claims Settlement & Payout Desk",
        description="Inquiring about settlement payout breakdown, disbursement date, or deduction amounts."
    ),
    IntentInfo(
        code="ADJUSTER_REASSIGNMENT",
        name="Adjuster Reassignment Request",
        category=IntentCategory.CLAIMS,
        priority=Priority.HIGH,
        routing_destination="Claims Adjuster Management",
        description="Requesting change or contact info for assigned claims adjuster."
    ),

    # 2. Policy & Coverage
    IntentInfo(
        code="POLICY_DETAILS",
        name="Policy Details & Coverage Inquiry",
        category=IntentCategory.POLICY_COVERAGE,
        priority=Priority.MEDIUM,
        routing_destination="Policy Servicing Team",
        description="Inquiring about policy inclusions, terms, add-on covers, and coverage limits."
    ),
    IntentInfo(
        code="RENEWAL",
        name="Policy Renewal Inquiry",
        category=IntentCategory.POLICY_COVERAGE,
        priority=Priority.HIGH,
        routing_destination="Renewals & Underwriting Desk",
        description="Inquiring about policy expiration, renewal terms, or NCB discount calculation."
    ),
    IntentInfo(
        code="CANCELLATION",
        name="Policy Cancellation Request",
        category=IntentCategory.POLICY_COVERAGE,
        priority=Priority.HIGH,
        routing_destination="Retention & Cancellations Desk",
        description="Customer requesting early termination or cancellation of coverage."
    ),
    IntentInfo(
        code="POLICY_CHANGE",
        name="Policy Endorsement & Changes",
        category=IntentCategory.POLICY_COVERAGE,
        priority=Priority.MEDIUM,
        routing_destination="Policy Endorsements Desk",
        description="Updating address, contact info, nominal beneficiary, or coverage options."
    ),
    IntentInfo(
        code="ADD_VEHICLE_DRIVER",
        name="Add / Remove Vehicle or Driver",
        category=IntentCategory.POLICY_COVERAGE,
        priority=Priority.HIGH,
        routing_destination="Vehicle Endorsements Desk",
        description="Endorsement to add or remove a vehicle or driver on a multi-car or family policy."
    ),
    IntentInfo(
        code="CERTIFICATE_OF_INSURANCE",
        name="Certificate of Insurance / ID Card",
        category=IntentCategory.POLICY_COVERAGE,
        priority=Priority.MEDIUM,
        routing_destination="Policy Servicing Team",
        description="Requesting digital copy of policy schedule, insurance binder, or ID cards."
    ),
    IntentInfo(
        code="DEDUCTIBLE_INQUIRY",
        name="Deductible Inquiry",
        category=IntentCategory.POLICY_COVERAGE,
        priority=Priority.MEDIUM,
        routing_destination="Policy Servicing Team",
        description="Inquiring about compulsory vs voluntary deductible and out-of-pocket limits."
    ),

    # 3. Garage & Roadside
    IntentInfo(
        code="NETWORK_GARAGE",
        name="Network Garage Locator",
        category=IntentCategory.GARAGE_ROADSIDE,
        priority=Priority.MEDIUM,
        routing_destination="Garage Network Operations",
        description="Searching for authorized in-network repair facilities nearby."
    ),
    IntentInfo(
        code="CASHLESS_REPAIR",
        name="Cashless Repair Authorization",
        category=IntentCategory.GARAGE_ROADSIDE,
        priority=Priority.HIGH,
        routing_destination="Cashless Garage Authorization Desk",
        description="Inquiring about cashless pre-authorization process at network garages."
    ),
    IntentInfo(
        code="ROADSIDE_ASSISTANCE",
        name="Emergency Roadside Breakdown",
        category=IntentCategory.GARAGE_ROADSIDE,
        priority=Priority.CRITICAL,
        routing_destination="24/7 Roadside Emergency Dispatch",
        description="Immediate on-site emergency assistance for battery, flat tire, lockout, or breakdown."
    ),
    IntentInfo(
        code="TOWING_SERVICE",
        name="Towing Service Request",
        category=IntentCategory.GARAGE_ROADSIDE,
        priority=Priority.CRITICAL,
        routing_destination="24/7 Roadside Emergency Dispatch",
        description="Requesting emergency vehicle recovery and towing to certified garage."
    ),
    IntentInfo(
        code="REPAIR_STATUS",
        name="Garage Repair Status Update",
        category=IntentCategory.GARAGE_ROADSIDE,
        priority=Priority.MEDIUM,
        routing_destination="Garage Network Operations",
        description="Tracking vehicle repair progress and estimated completion date at body shop."
    ),
    IntentInfo(
        code="REPAIR_ESTIMATE_DISPUTE",
        name="Repair Estimate Dispute",
        category=IntentCategory.GARAGE_ROADSIDE,
        priority=Priority.HIGH,
        routing_destination="Claims Technical Review Desk",
        description="Disputing garage quotation or adjuster-approved repair labor/parts breakdown."
    ),

    # 4. General Support & Billing
    IntentInfo(
        code="PAYMENT_ISSUE",
        name="Premium Payment & Gateway Issue",
        category=IntentCategory.SUPPORT_BILLING,
        priority=Priority.HIGH,
        routing_destination="Billing & Payment Operations",
        description="Failed transaction, double charge, or payment gateway error."
    ),
    IntentInfo(
        code="BILLING_DISPUTE",
        name="Billing & Premium Dispute",
        category=IntentCategory.SUPPORT_BILLING,
        priority=Priority.HIGH,
        routing_destination="Billing & Payment Operations",
        description="Disputing premium calculation, fees, or unexpected recurring charge."
    ),
    IntentInfo(
        code="REFUND",
        name="Refund Status & Request",
        category=IntentCategory.SUPPORT_BILLING,
        priority=Priority.HIGH,
        routing_destination="Finance & Refunds Desk",
        description="Inquiring about pro-rata cancellation refund or uncredited payment refund."
    ),
    IntentInfo(
        code="PORTAL_ACCESS",
        name="Customer Portal & Login Support",
        category=IntentCategory.SUPPORT_BILLING,
        priority=Priority.LOW,
        routing_destination="Digital Support Helpdesk",
        description="Trouble logging into web portal, password reset, or mobile app issues."
    ),
    IntentInfo(
        code="COMPLAINT",
        name="Formal Grievance / Complaint",
        category=IntentCategory.SUPPORT_BILLING,
        priority=Priority.CRITICAL,
        routing_destination="Executive Customer Relations",
        description="High-priority customer dissatisfaction, legal threat, or regulatory complaint."
    ),
    IntentInfo(
        code="FRAUD_SUSPICION",
        name="Fraud Suspicion / Reporting",
        category=IntentCategory.SUPPORT_BILLING,
        priority=Priority.CRITICAL,
        routing_destination="Special Investigations Unit (SIU)",
        description="Report of unauthorized policy changes, identity theft, or fraudulent claims."
    ),
    IntentInfo(
        code="GENERAL_QUERY",
        name="General Information Inquiry",
        category=IntentCategory.SUPPORT_BILLING,
        priority=Priority.LOW,
        routing_destination="General Customer Support",
        description="General questions regarding business hours, contact numbers, or product lines."
    ),
    IntentInfo(
        code="SPAM_OR_AUTOMATED",
        name="Automated / Spam Message",
        category=IntentCategory.SYSTEM,
        priority=Priority.LOW,
        routing_destination="#archive",
        description="System notifications, spam marketing, or automated auto-replies."
    ),
]

# Quick Lookup Dictionaries
INTENT_LOOKUP: Dict[str, IntentInfo] = {item.code: item for item in ALL_INTENTS}
# Provide alias mappings for backward compatibility
INTENT_LOOKUP["CLAIM_REJECTION"] = INTENT_LOOKUP["CLAIM_REJECTION_DISPUTE"]
INTENT_LOOKUP["CLAIM_APPROVAL"] = INTENT_LOOKUP["SETTLEMENT_INQUIRY"]
INTENT_LOOKUP["POLICY_DOCUMENT"] = INTENT_LOOKUP["CERTIFICATE_OF_INSURANCE"]
INTENT_LOOKUP["VEHICLE_CHANGE"] = INTENT_LOOKUP["ADD_VEHICLE_DRIVER"]
INTENT_LOOKUP["PAYMENT"] = INTENT_LOOKUP["PAYMENT_ISSUE"]
INTENT_LOOKUP["CASHLESS_CLAIM"] = INTENT_LOOKUP["CASHLESS_REPAIR"]
INTENT_LOOKUP["POLICY_EXPIRY"] = INTENT_LOOKUP["RENEWAL"]
INTENT_LOOKUP["RENEWAL_QUOTE"] = INTENT_LOOKUP["RENEWAL"]
INTENT_LOOKUP["PREMIUM_QUERY"] = INTENT_LOOKUP["BILLING_DISPUTE"]
INTENT_LOOKUP["NO_CLAIM_BONUS"] = INTENT_LOOKUP["POLICY_DETAILS"]
INTENT_LOOKUP["INSPECTION"] = INTENT_LOOKUP["CLAIM_DOCUMENTS"]

INTENT_PRIORITY_MAP: Dict[str, Priority] = {k: v.priority for k, v in INTENT_LOOKUP.items()}
INTENT_CATEGORY_MAP: Dict[str, str] = {k: v.category.value for k, v in INTENT_LOOKUP.items()}
INTENT_ROUTING_MAP: Dict[str, str] = {k: v.routing_destination for k, v in INTENT_LOOKUP.items()}


def get_intent_info(intent_code: str) -> IntentInfo:
    """Retrieve full intent metadata by code with fallback to GENERAL_QUERY."""
    return INTENT_LOOKUP.get(intent_code.upper(), INTENT_LOOKUP["GENERAL_QUERY"])
