"""Context-Aware Auto-Reply Generator for insurance customer communications."""

import re
from typing import Dict, Any, Optional, List
from src.ai.reasoning_engine import TriageDecision
from src.ingestion.preprocessor import StructuredEmail


class ReplyGenerator:
    """Drafts empathetic, grounded, plain-text responses strictly using database context."""

    DEFAULT_HOTLINE = "1-800-555-ROAD (7623)"
    SUPPORT_PHONE = "1-800-555-APEX (2739)"
    PORTAL_URL = "https://portal.apexshield.com"

    def __init__(self, signature: str = "\n\nBest regards,\nApex Shield Insurance Services"):
        self.signature = signature

    @staticmethod
    def clean_markdown_to_plaintext(text: str) -> str:
        """Strip markdown syntax to ensure clean plain-text email output."""
        if not text:
            return ""
        # Remove bold / italics
        cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
        cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
        cleaned = re.sub(r"_([^_]+)_", r"\1", cleaned)
        # Remove markdown headings
        cleaned = re.sub(r"^#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
        # Convert markdown bullets to clean standard bullet characters
        cleaned = re.sub(r"^\s*[-*]\s+", "  - ", cleaned, flags=re.MULTILINE)
        # Clean double blank lines
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def format_reply_subject(self, original_subject: str) -> str:
        """Format the reply subject with standard 'Re: ' prefix."""
        subj = (original_subject or "Insurance Support Request").strip()
        if not subj.lower().startswith("re:"):
            return f"Re: {subj}"
        return subj

    def generate_reply(
        self,
        email: StructuredEmail,
        decision: TriageDecision,
        db_context: Dict[str, Any],
    ) -> Optional[str]:
        """Construct full plain-text email response tailored to intent and sentiment."""
        if decision.intent == "SPAM_OR_AUTOMATED":
            return None

        # Determine the greeting name from the ENVELOPE, never from the matched
        # customer record. A customer is resolved by claim/policy/VIN number as
        # well as by email, so an unrelated sender quoting someone else's claim
        # number resolves to that other customer. Greeting them with the record
        # owner's name -- and replying to them with that record's details --
        # discloses one customer's information to another. `matched_via` tells us
        # which resolution path was taken: anything other than an exact email
        # match is an asserted identity we must not treat as proof.
        customer = db_context.get("customer")
        matched_via = db_context.get("matched_via") or ""
        identity_confirmed = bool(customer) and matched_via == "email"
        if identity_confirmed:
            name = customer.get("name") or email.sender_name or "Valued Policyholder"
        else:
            # Use only what the sender themselves asserted, and never a name
            # belonging to a different customer.
            name = email.sender_name or "Valued Policyholder"

        # Fail closed when the sender references a claim number that is not
        # present in the resolved context. Do not let an LLM reply disclose
        # other claims that happen to be present in that context.
        referenced_claims = set(re.findall(
            r"\bCLM[- ]?\d{4}[- ]?\d+\b",
            f"{email.subject or ''} {email.body_text or ''}",
            flags=re.IGNORECASE,
        ))
        known_claims = {
            str(c.get("claim_number", "")).upper()
            for c in db_context.get("claims", [])
            if c.get("claim_number")
        }
        known_claims.update(
            str(number).upper()
            for number in db_context.get("extracted_entities", {}).get("claim_numbers", [])
            if number
        )
        normalized_known = {
            re.sub(r"[^A-Z0-9]", "", number) for number in known_claims
        }
        unverified_references = {
            number for number in referenced_claims
            if re.sub(r"[^A-Z0-9]", "", number.upper()) not in normalized_known
        }
        if unverified_references:
            return (
                f"Dear {name},\n\n"
                "Thank you for contacting us. We could not verify the claim "
                "reference in your message. For your privacy, we cannot provide "
                "claim-specific information until the correct claim is verified. "
                "Please contact our support team through your established "
                "customer channel.\n\n"
                "Best regards,\nCustomer Support Team"
            )

        # Select specialized template if suggested_reply is missing or generic
        body = decision.suggested_reply
        if not body or len(body.strip()) < 20:
            body = self._generate_template_reply(name, decision, db_context, email)

        # Sanitize to plain text
        clean_body = self.clean_markdown_to_plaintext(body)

        # Append standard signoff if missing
        if "Apex Shield" not in clean_body and "Sincerely" not in clean_body:
            clean_body += self.signature

        return clean_body

    def _generate_template_reply(
        self,
        customer_name: str,
        decision: TriageDecision,
        db_context: Dict[str, Any],
        email: StructuredEmail,
    ) -> str:
        """Specialized template generator adhering to strict database grounding."""
        intent = decision.intent
        claims = db_context.get("claims", [])
        policies = db_context.get("policies", [])
        renewals = db_context.get("renewals", [])
        garages = db_context.get("garages", [])

        # 1. Claim Status
        if intent == "CLAIM_STATUS":
            if claims:
                c = claims[0]
                claim_type = c.get("claim_type") or "Auto Insurance Claim"
                claimed_amt = float(c.get("claimed_amount", 0.0) or 0.0)
                approved_amt = float(c.get("approved_amount", 0.0) or 0.0)
                adjuster = c.get("assigned_adjuster", "Michael Chang")
                return (
                    f"Dear {customer_name},\n\n"
                    f"Thank you for contacting Apex Shield Claims Support regarding Claim #{c['claim_number']}.\n\n"
                    f"Here is your current claim status summary:\n"
                    f"  - Claim Number: {c['claim_number']}\n"
                    f"  - Current Status: {c['status']}\n"
                    f"  - Claim Type: {claim_type}\n"
                    f"  - Claimed Amount: ${claimed_amt:,.2f}\n"
                    f"  - Approved Amount: ${approved_amt:,.2f}\n"
                    f"  - Assigned Adjuster: {adjuster}\n\n"
                    f"Next Steps:\n"
                    f"You can track real-time claim milestones or upload additional documentation directly at {self.PORTAL_URL}.\n"
                    f"If you need to reach your adjuster directly, please contact our Claims Desk at {self.SUPPORT_PHONE}.\n\n"
                    f"Sincerely,\nClaims Operations Team\nApex Shield Insurance"
                )
            return (
                f"Dear {customer_name},\n\n"
                f"Thank you for reaching out to Apex Shield Claims Support. We could not locate an active claim associated with your sender address.\n\n"
                f"Next Steps:\n"
                f"Please reply with your Claim Number or Policy Number so we can locate your record and provide an immediate status update.\n\n"
                f"Sincerely,\nClaims Operations Team\nApex Shield Insurance"
            )

        # 2. Claim Delay
        elif intent == "CLAIM_DELAY":
            if claims:
                c = claims[0]
                reason = c.get("delay_reason", "Awaiting parts delivery and technical adjuster review")
                adjuster = c.get("assigned_adjuster", "Senior Claims Adjuster")
                return (
                    f"Dear {customer_name},\n\n"
                    f"We understand your concern regarding the time taken to process Claim #{c['claim_number']}, and we sincerely apologize for the delay.\n\n"
                    f"Claim Details:\n"
                    f"  - Claim Number: {c['claim_number']}\n"
                    f"  - Current Status: {c['status']}\n"
                    f"  - Reason for Delay: {reason}\n"
                    f"  - Assigned Adjuster: {adjuster}\n\n"
                    f"Next Steps:\n"
                    f"We have expedited your file to our Senior Claims Supervisory Desk. Your adjuster will review the status and provide a comprehensive update within 24 business hours.\n\n"
                    f"Sincerely,\nSenior Claims Supervisor Desk\nApex Shield Insurance"
                )
            return (
                f"Dear {customer_name},\n\n"
                f"We apologize for the delay in resolving your insurance request. We have prioritized your inquiry with our senior management team.\n\n"
                f"Next Steps:\n"
                f"A claims supervisor will review your account and contact you within 1 business day.\n\n"
                f"Sincerely,\nClaims Operations Desk\nApex Shield Insurance"
            )

        # 3. Claim Rejection Dispute
        elif intent == "CLAIM_REJECTION_DISPUTE":
            rejected_claim = next((c for c in claims if c.get("status") == "Rejected"), (claims[0] if claims else None))
            claim_num = rejected_claim["claim_number"] if rejected_claim else "your claim"
            reason_str = f"\n  - Original Decision Basis: {rejected_claim.get('rejection_reason')}" if (rejected_claim and rejected_claim.get("rejection_reason")) else ""
            return (
                f"Dear {customer_name},\n\n"
                f"We have received your request to dispute the determination for Claim #{claim_num}.{reason_str}\n\n"
                f"We take appeals very seriously and want to ensure a fair and comprehensive review of your file.\n\n"
                f"Next Steps:\n"
                f"1. Your file has been transferred to our Claims Appeals & Grievance Desk for secondary independent evaluation.\n"
                f"2. Please reply directly to this email with any additional evidence (e.g. photos, updated repair estimates, witness statements).\n"
                f"3. An Appeals Officer will review all submitted records and provide a formal written determination within 3-5 business days.\n\n"
                f"Sincerely,\nClaims Appeals & Grievance Desk\nApex Shield Insurance"
            )

        # 4. FNOL / New Claim
        elif intent == "NEW_CLAIM":
            pol_num = policies[0]["policy_number"] if policies else "your active policy"
            return (
                f"Dear {customer_name},\n\n"
                f"We are sorry to hear about your recent incident. We have recorded your First Notice of Loss under Policy #{pol_num}.\n\n"
                f"Next Steps to complete your claim registration:\n"
                f"  1. Photos of vehicle damage and the accident scene.\n"
                f"  2. Copy of the police report or incident number (if applicable).\n"
                f"  3. Contact details of any third parties involved.\n\n"
                f"A dedicated claims adjuster will be assigned within 4 business hours to inspect the vehicle and coordinate cashless repairs.\n\n"
                f"Sincerely,\nEmergency Claims Intake Desk\nApex Shield Insurance"
            )

        # 5. Network Garage Inquiry
        elif intent in ["NETWORK_GARAGE", "CASHLESS_REPAIR"]:
            garage_lines = []
            for g in garages[:3]:
                garage_lines.append(f"  - {g['name']}: {g['address']}, {g['city']} (Tel: {g['phone']}) | Rating: {g['rating']}★")
            garage_text = "\n".join(garage_lines) if garage_lines else "  - Visit our online portal for a full list of certified repair centers."
            return (
                f"Dear {customer_name},\n\n"
                f"Here are authorized Apex Shield Network Garages offering cashless claim settlements in your area:\n\n"
                f"{garage_text}\n\n"
                f"Cashless Benefits:\n"
                f"  - Direct billing between the garage and Apex Shield (subject only to your deductible).\n"
                f"  - Priority repair queues and guaranteed OEM replacement parts.\n\n"
                f"Next Steps:\n"
                f"Bring your vehicle to any network garage and present your policy number to initiate cashless service.\n\n"
                f"Sincerely,\nGarage Network Operations\nApex Shield Insurance"
            )

        # 6. Policy Details / Coverage Inquiry
        elif intent == "POLICY_DETAILS":
            if policies:
                p = policies[0]
                pol_type = p.get("type") or p.get("policy_type") or "Comprehensive Auto"
                return (
                    f"Dear {customer_name},\n\n"
                    f"Here is your policy summary for Policy #{p['policy_number']}:\n\n"
                    f"  - Policy Type: {pol_type}\n"
                    f"  - Status: {p['status']}\n"
                    f"  - Policy Period: {p['start_date']} to {p['end_date']}\n"
                    f"  - Deductible: ${p['deductible']:,.2f}\n"
                    f"  - Coverage Limit: ${float(p.get('coverage_limit', 0.0) or 0.0):,.2f}\n\n"
                    f"Next Steps:\n"
                    f"You can view complete endorsement schedules and download digital ID cards anytime at {self.PORTAL_URL}.\n\n"
                    f"Sincerely,\nPolicy Servicing Team\nApex Shield Insurance"
                )
            return (
                f"Dear {customer_name},\n\n"
                f"Thank you for contacting Apex Shield Policy Support. To view your policy details, please reply with your Policy Number or Vehicle Identification Number (VIN).\n\n"
                f"Sincerely,\nPolicy Servicing Team\nApex Shield Insurance"
            )

        # 7. Renewal Inquiry
        elif intent == "RENEWAL":
            if renewals:
                r = renewals[0]
                pol_num = policies[0]["policy_number"] if policies else "your policy"
                return (
                    f"Dear {customer_name},\n\n"
                    f"Here are the renewal terms for Policy #{pol_num}:\n\n"
                    f"  - Renewal Due Date: {r['renewal_due_date']}\n"
                    f"  - Quoted Renewal Premium: ${r['quoted_premium']:,.2f}\n"
                    f"  - No-Claim Bonus (NCB) Discount: {r['ncb_discount_pct']}%\n"
                    f"  - Renewal Status: {r['status']}\n\n"
                    f"Next Steps:\n"
                    f"To lock in your NCB discount and confirm coverage continuity, reply directly to this email or complete payment at {self.PORTAL_URL}/renew.\n\n"
                    f"Sincerely,\nRenewals & Underwriting Desk\nApex Shield Insurance"
                )
            return (
                f"Dear {customer_name},\n\n"
                f"Thank you for your inquiry regarding policy renewal. Our Underwriting Team is calculating your renewal premium with applicable discounts and will dispatch your quote shortly.\n\n"
                f"Sincerely,\nRenewals Desk\nApex Shield Insurance"
            )

        # 8. Emergency Roadside & Towing
        elif intent in ["ROADSIDE_ASSISTANCE", "TOWING_SERVICE"]:
            return (
                f"Dear {customer_name},\n\n"
                f"Emergency roadside assistance has been alerted for your vehicle.\n\n"
                f"Immediate Safety Instructions:\n"
                f"  1. Ensure you and your passengers are safely behind the roadside barrier or in a secure location.\n"
                f"  2. Turn on your vehicle hazard warning flashers.\n\n"
                f"Immediate Dispatch Hotline:\n"
                f"For real-time GPS technician dispatch and immediate towing coordination, please call our 24/7 Emergency Dispatch Center directly at {self.DEFAULT_HOTLINE}.\n\n"
                f"Sincerely,\n24/7 Roadside Emergency Dispatch\nApex Shield Insurance"
            )

        # Default General Response
        return (
            f"Dear {customer_name},\n\n"
            f"Thank you for reaching out to Apex Shield Insurance regarding '{email.subject}'.\n\n"
            f"A customer care specialist is reviewing your request and will provide detailed follow-up within 1 business day.\n\n"
            f"Next Steps:\n"
            f"For immediate assistance, visit {self.PORTAL_URL} or call us at {self.SUPPORT_PHONE}.\n\n"
            f"Sincerely,\nCustomer Support Team\nApex Shield Insurance"
        )
