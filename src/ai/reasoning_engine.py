"""LLM Reasoning Engine for multi-step intent classification, triage reasoning, and context-grounded response generation."""

import json
import logging
import re
import os
import time

from openai import (
    OpenAI,
    APIConnectionError,
    APITimeoutError,
    APIStatusError,
)
from typing import Dict, Any, Optional
from dataclasses import dataclass

from src.ai.intents import (
    INTENT_PRIORITY_MAP,
    INTENT_CATEGORY_MAP,
    INTENT_ROUTING_MAP,
    Priority,
    get_intent_info,
)
from src.ai.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from src.ai.safety import SafetyEngine
from src.ai.escalation import EscalationEngine
from src.ingestion.preprocessor import StructuredEmail

logger = logging.getLogger(__name__)


@dataclass
class TriageDecision:
    """Structured decision output from the triage reasoning engine."""
    category: str
    intent: str
    priority: str
    urgency_score: int
    sentiment: str
    escalation_needed: bool
    escalation_reason: str
    summary: str
    policy_number: Optional[str]
    claim_number: Optional[str]
    routed_to: str
    suggested_reply: str
    confidence: float = 0.95
    human_review_required: bool = False
    raw_response: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "intent": self.intent,
            "priority": self.priority,
            "urgency_score": self.urgency_score,
            "sentiment": self.sentiment,
            "escalation_needed": self.escalation_needed,
            "escalation_reason": self.escalation_reason,
            "summary": self.summary,
            "policy_number": self.policy_number,
            "claim_number": self.claim_number,
            "routed_to": self.routed_to,
            "suggested_reply": self.suggested_reply,
            "confidence": self.confidence,
            "human_review_required": self.human_review_required,
        }


class LLMReasoningEngine:
    """Reasoning engine performing multi-step insurance triage with strict schema compliance."""

    def __init__(self, model: str = "groq/qwen/qwen3.8-27b", temperature: float = 0.2):
        self.model = model
        self.temperature = temperature
        self.base_url = os.getenv("OMNIROUTE_BASE_URL", "http://localhost:20128/v1")
        self.timeout_seconds = 30
        self.max_retries = 2  # Two retries after the initial attempt.
        self.retry_backoff_seconds = 1.0
        self.max_tokens = 2000
        self._client = None

    def _get_client(self):
        """Create the OmniRoute OpenAI-compatible client lazily."""
        if self._client is None:
            api_key = os.getenv("OMNIROUTE_KEY")
            if not api_key:
                raise RuntimeError("OMNIROUTE_KEY is not configured in this session.")

            self._client = OpenAI(
                base_url=self.base_url,
                api_key=api_key,
                timeout=self.timeout_seconds,
                max_retries=0,
            )
        return self._client

    @staticmethod
    def _ground_reply_brand(reply: str) -> str:
        """Use only a configured insurer name; otherwise keep the reply brand-neutral.

        Set INSURER_NAME to the insurer's verified display name. When it is
        absent, remove model-invented insurer names from customer-facing text.
        """
        if not isinstance(reply, str) or not reply:
            return reply

        insurer_name = os.getenv("INSURER_NAME", "").strip()
        # Match common insurer suffixes without assuming a particular brand.
        brand_pattern = re.compile(
            r"\\b[A-Z][A-Za-z&'’-]*(?:\\s+[A-Z][A-Za-z&'’-]*){0,3}"
            r"\\s+(?:Insurance|Insurers|Assurance|General Insurance)\\b"
        )

        if insurer_name:
            # Preserve the configured name and replace other insurer-like names.
            def replace_unverified(match):
                return insurer_name if match.group(0).casefold() != insurer_name.casefold() else match.group(0)
            return brand_pattern.sub(replace_unverified, reply)

        # Without a configured brand, make common greeting/sign-off patterns neutral.
        reply = re.sub(
            r"(?i)(Thank you for contacting)\\s+"
            r"[A-Z][A-Za-z&'’-]*(?:\\s+[A-Z][A-Za-z&'’-]*){0,3}"
            r"\\s+(?:Insurance|Insurers|Assurance|General Insurance)(?=[.!?])",
            r"\\1 us",
            reply,
        )
        # Remove insurer names elsewhere rather than presenting them as verified.
        reply = brand_pattern.sub("our team", reply)
        return reply

    def analyze(self, email: StructuredEmail, db_context: Dict[str, Any]) -> TriageDecision:
        """Execute full multi-step triage on an incoming email."""
        # Step 1: Safety & Automated Spam Check
        is_spam, reason = SafetyEngine.is_spam_or_automated(
            email.sender_email, email.subject, email.body_text, email.headers
        )
        if is_spam:
            return TriageDecision(
                category="System & Spam",
                intent="SPAM_OR_AUTOMATED",
                priority=Priority.LOW.value,
                urgency_score=1,
                sentiment="Neutral",
                escalation_needed=False,
                escalation_reason="",
                summary=f"Automated message or spam filtered: {reason}",
                policy_number=None,
                claim_number=None,
                routed_to="#archive",
                suggested_reply="",
                human_review_required=False,
                raw_response="Filtered by SafetyEngine",
            )

        # Step 2: Formulate Prompt for Hermes Model
        context_str = json.dumps(db_context, indent=2, default=str)
        user_prompt = USER_PROMPT_TEMPLATE.format(
            sender_name=email.sender_name or "Valued Customer",
            sender_email=email.sender_email,
            subject=email.subject,
            received_date=email.received_date,
            body_text=email.body_text,
            context_json=context_str,
        )

        insurer_name = os.getenv("INSURER_NAME", "").strip()
        if insurer_name:
            user_prompt += (
                "\\n\\nCUSTOMER-REPLY BRANDING: Use only this verified insurer name "
                f"when a company name is needed: {insurer_name!r}. "
                "Do not invent or substitute any other insurer or brand name."
            )
        else:
            user_prompt += (
                "\\n\\nCUSTOMER-REPLY BRANDING: No verified insurer name is configured. "
                "Use neutral wording such as 'Thank you for contacting us' and do not "
                "invent or include an insurer, company, or brand name."
            )

        # Step 3: Multi-step reasoning with context-grounding
        decision = self._reason_over_email(email, db_context, user_prompt)
        return decision

    def _parse_llm_json(self, raw_json_str: str) -> Optional[Dict[str, Any]]:
        """Parse and sanitize JSON from LLM response."""
        try:
            cleaned = raw_json_str.strip()
            # Strip markdown if present
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)
            return json.loads(cleaned)
        except Exception as e:
            logger.warning(f"Could not parse LLM JSON output: {e}")
            return None

    def _reason_over_email(
        self, email: StructuredEmail, db_context: Dict[str, Any], prompt: str
    ) -> TriageDecision:
        """Classify an email with OmniRoute, then enforce deterministic escalation."""
        client = self._get_client()
        response = None

        # Retry transient transport/API failures and incomplete model responses.
        # Semantic/taxonomy validation below remains fail-closed and is not retried.
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )

                if not response.choices or not response.choices[0].message.content:
                    raise ValueError("OmniRoute returned an empty response.")

                raw = response.choices[0].message.content
                data = self._parse_llm_json(raw)
                if not isinstance(data, dict):
                    raise ValueError("OmniRoute response was not a valid JSON object.")

                required = {
                    "intent", "urgency_score", "sentiment", "summary",
                    "escalation_needed", "escalation_reason", "suggested_reply",
                }
                missing = required - data.keys()
                if missing:
                    raise ValueError(
                        f"OmniRoute response is missing fields: {sorted(missing)}"
                    )
                break
            except ValueError as exc:
                last_error = exc
                error_name = f"invalid model response: {exc}"
            except (APITimeoutError, APIConnectionError) as exc:
                last_error = exc
                error_name = type(exc).__name__
            except APIStatusError as exc:
                last_error = exc
                error_name = f"{type(exc).__name__} (HTTP {exc.status_code})"
                if not (
                    exc.status_code in {408, 409, 429}
                    or exc.status_code >= 500
                ):
                    raise

            if attempt >= self.max_retries:
                logger.error(
                    "OmniRoute failed after %d attempt(s): %s",
                    attempt + 1,
                    error_name,
                )
                raise last_error

            delay = self.retry_backoff_seconds * (2 ** attempt)
            logger.warning(
                "Transient OmniRoute failure (%s); retrying in %.1f second(s) "
                "(attempt %d/%d).",
                error_name,
                delay,
                attempt + 1,
                self.max_retries + 1,
            )
            time.sleep(delay)

        if response is None:
            raise RuntimeError("OmniRoute request ended without a response.")

        # Validate against canonical intents; never accept taxonomy fallbacks.
        canonical_intents = {
            "NEW_CLAIM", "CLAIM_STATUS", "CLAIM_DELAY",
            "CLAIM_REJECTION_DISPUTE", "CLAIM_DOCUMENTS",
            "SETTLEMENT_INQUIRY", "ADJUSTER_REASSIGNMENT",
            "POLICY_DETAILS", "RENEWAL", "CANCELLATION",
            "POLICY_CHANGE", "ADD_VEHICLE_DRIVER",
            "CERTIFICATE_OF_INSURANCE", "DEDUCTIBLE_INQUIRY",
            "NETWORK_GARAGE", "CASHLESS_REPAIR",
            "ROADSIDE_ASSISTANCE", "TOWING_SERVICE",
            "REPAIR_STATUS", "REPAIR_ESTIMATE_DISPUTE",
            "PAYMENT_ISSUE", "BILLING_DISPUTE", "REFUND",
            "PORTAL_ACCESS", "COMPLAINT", "FRAUD_SUSPICION",
            "GENERAL_QUERY",
        }
        intent = data["intent"]
        if not isinstance(intent, str):
            raise ValueError(f"Unknown canonical intent: {intent!r}")

        normalized_intent = intent.strip()
        model_flagged_as_spam = normalized_intent.casefold() in {
            "spam_or_automated",
            "spam or automated",
        }

        # Spam filtering is handled by SafetyEngine before the LLM. If the
        # model disagrees, retain the message for conservative human review.
        if model_flagged_as_spam:
            normalized_intent = "GENERAL_QUERY"
            logger.warning(
                "Model classified an email as spam after it passed the "
                "deterministic spam check; routing for human review."
            )

        # Accept only explicit, unambiguous display-name aliases from the
        # model. Unknown or ambiguous labels still fail closed.
        intent_aliases = {
            "policy details": "POLICY_DETAILS",
            "policy details & coverage inquiry": "POLICY_DETAILS",
            "complaint": "COMPLAINT",
            "formal grievance / complaint": "COMPLAINT",
            "roadside assistance": "ROADSIDE_ASSISTANCE",
            "emergency roadside breakdown": "ROADSIDE_ASSISTANCE",
            "roadside assistance request": "ROADSIDE_ASSISTANCE",
            "towing service request": "TOWING_SERVICE",
            "claim status inquiry": "CLAIM_STATUS",
            "claim delay escalation": "CLAIM_DELAY",
            "fraud suspicion / reporting": "FRAUD_SUSPICION",
            "policy renewal inquiry": "RENEWAL",
            "claim document submission": "CLAIM_DOCUMENTS",
        }
        if normalized_intent not in canonical_intents:
            normalized_intent = intent_aliases.get(
                normalized_intent.casefold(), normalized_intent
            )
        if normalized_intent not in canonical_intents:
            raise ValueError(f"Unknown canonical intent: {intent!r}")
        intent = normalized_intent

        urgency = data["urgency_score"]
        if isinstance(urgency, bool) or not isinstance(urgency, int) or not 1 <= urgency <= 10:
            raise ValueError("urgency_score must be an integer from 1 to 10.")

        allowed_sentiments = {"Calm", "Frustrated", "Angry", "Anxious", "Neutral"}
        sentiment = data["sentiment"]
        if sentiment not in allowed_sentiments:
            raise ValueError(f"Invalid sentiment: {sentiment!r}")

        if not isinstance(data["escalation_needed"], bool):
            raise ValueError("escalation_needed must be a boolean.")

        for field in ("summary", "escalation_reason", "suggested_reply"):
            if not isinstance(data[field], str):
                raise ValueError(f"{field} must be a string.")

        # Deterministic, narrowly scoped safeguards for high-impact cases.
        # These rules use the message text; they do not rely on model labels.
        content = f"{email.subject} {email.body_text}".lower()

        # Explicit requests for a policy document/certificate should not be
        # collapsed into a general policy-details inquiry.
        policy_document_terms = (
            "policy document", "copy of my policy", "copy of the policy",
            "certificate of insurance", "insurance certificate",
            "proof of insurance",
        )
        if any(term in content for term in policy_document_terms):
            intent = "CERTIFICATE_OF_INSURANCE"

        # A legal action explicitly tied to an unresolved claim is handled
        # as a claim delay, rather than a generic status enquiry.
        legal_action_terms = (
            "intend to file a lawsuit",
            "file a lawsuit",
            "take legal action",
            "legal action",
            "consulted an attorney",
            "consulted a lawyer",
        )
        unresolved_claim_terms = (
            "claim has not been resolved",
            "claim remains unresolved",
            "claim is still pending",
            "claim has not been settled",
            "claim has not been paid",
        )
        if (
            any(term in content for term in legal_action_terms)
            and any(term in content for term in unresolved_claim_terms)
        ):
            intent = "CLAIM_DELAY"

        # Billing/payment questions that include card details should not
        # fall through to GENERAL_QUERY. Do not store or echo card data.
        payment_terms = (
            "payment", "billing", "card number", "credit card",
            "debit card", "billing details", "payment details",
        )
        if any(term in content for term in payment_terms):
            intent = "PAYMENT_ISSUE"

        # Resolve clear claim-delay and injury cases when the model selects
        # a less specific intent. These rules require explicit evidence.
        prolonged_claim_delay_terms = (
            "more than two months", "over two months", "more than 60 days",
            "over 60 days", "for months", "several months",
        )
        claim_delay_context_terms = (
            "claim", "claim status", "claim update", "claim payment",
        )
        if (
            any(term in content for term in prolonged_claim_delay_terms)
            and any(term in content for term in claim_delay_context_terms)
            and intent == "CLAIM_STATUS"
        ):
            intent = "CLAIM_DELAY"

        injury_after_accident_terms = (
            "injured passenger", "passenger is injured", "passenger was injured",
            "passenger has been injured", "person was injured",
            "someone was injured", "someone is injured",
        )
        if (
            any(term in content for term in injury_after_accident_terms)
            and ("accident" in content or "collision" in content)
            and intent == "CLAIM_STATUS"
        ):
            intent = "NEW_CLAIM"

        # Recompute urgency floors for explicitly described circumstances.
        # A serious injury in an accident warrants urgent human attention.
        serious_injury_terms = (
            "serious injury", "seriously injured", "severe injury",
            "severe injuries", "severely injured",
        )
        multiple_injuries_terms = (
            "multiple injuries", "several people have been injured",
            "two people are injured", "three people are injured",
        )
        if (
            any(term in content for term in serious_injury_terms)
            or any(term in content for term in multiple_injuries_terms)
        ) and ("accident" in content or "collision" in content):
            urgency = max(urgency, 9)

        # Possible injury with severe pain or a stated need for medical
        # attention warrants urgent review, even when injury status is
        # uncertain. Keep this below the confirmed serious-injury floor.
        possible_injury_terms = (
            "severe pain",
            "may need medical attention",
            "might need medical attention",
            "needs medical attention",
            "need medical attention",
        )
        if (
            any(term in content for term in possible_injury_terms)
            and ("accident" in content or "collision" in content)
        ):
            urgency = max(urgency, 7)

        # Explicit formal complaints about a claim take precedence over
        # a routine claim-status classification.
        formal_complaint_terms = (
            "formally complaining",
            "formal complaint",
            "lodging a formal complaint",
            "filing a formal complaint",
            "making a formal complaint",
        )
        claim_context_terms = (
            "claim", "claim status", "claim update", "claim delay",
            "claim has not been resolved", "claim remains unresolved",
            "claim is still pending", "claim has not been settled",
            "claim has not been paid", "no update",
        )
        if (
            any(term in content for term in formal_complaint_terms)
            and any(term in content for term in claim_context_terms)
        ):
            intent = "COMPLAINT"

        # Legal threats tied to dissatisfaction with service are complaints,
        # even if the model labels them as a claim dispute.
        legal_threat_terms = (
            "i will sue", "i'll sue", "sue you", "file a lawsuit",
            "take legal action", "contact the insurance commissioner",
            "report this to the insurance commissioner",
            "speaking with my lawyer", "contact my attorney",
            "contact my lawyer",
        )
        service_dissatisfaction_terms = (
            "terrible service", "unacceptable service", "poor service",
            "unacceptable", "this is terrible", "not resolved today",
        )
        if (
            any(term in content for term in legal_threat_terms)
            and any(term in content for term in service_dissatisfaction_terms)
        ):
            intent = "COMPLAINT"

        # A prolonged claim delay combined with repeated follow-ups and
        # no resolution warrants a minimum urgency of 6.
        prolonged_delay_terms = (
            "more than two months",
            "over two months",
            "more than 60 days",
            "over 60 days",
            "for months",
            "several months",
            "months",
        )
        repeated_followup_terms = (
            "followed up repeatedly",
            "repeated follow-ups",
            "repeatedly followed up",
            "contacted your team several times",
            "followed up several times",
            "multiple follow-ups",
            "several follow-ups",
            "followed up multiple times",
        )
        unresolved_terms = (
            "not received any resolution",
            "no resolution",
            "still unresolved",
            "remains unresolved",
            "no response",
            "no update",
        )
        if (
            any(term in content for term in prolonged_delay_terms)
            and any(term in content for term in repeated_followup_terms)
            and any(term in content for term in unresolved_terms)
        ):
            urgency = max(urgency, 6)

        # A formal complaint requesting managerial escalation has a
        # minimum urgency, but is not automatically treated as an emergency.
        if (
            any(term in content for term in formal_complaint_terms)
            and ("escalate" in content or "manager" in content)
        ):
            urgency = max(urgency, 5)

        # Cap urgency for explicitly safe, non-injury roadside breakdowns.
        # Do not apply this cap when the message describes a hazardous
        # location or an emergency.
        safe_roadside_terms = (
            "safely parked",
            "safe parking area",
            "safe parking lot",
            "safe location",
        )
        no_injury_terms = (
            "no injuries",
            "no injury",
            "nobody is injured",
            "no one is injured",
        )
        roadside_hazard_terms = (
            "in traffic",
            "blocking traffic",
            "on a busy road",
            "on the highway",
            "unsafe location",
            "dangerous location",
            "at risk of being hit",
            "vehicle fire",
            "smoke coming from",
        )

        if (
            intent in {"ROADSIDE_ASSISTANCE", "TOWING_SERVICE"}
            and any(term in content for term in safe_roadside_terms)
            and any(term in content for term in no_injury_terms)
            and not any(term in content for term in roadside_hazard_terms)
        ):
            urgency = min(urgency, 7)

        # Use taxonomy for category and routing, not model-supplied values.
        intent_info = get_intent_info(intent)
        confidence = data.get("confidence", 0.80)
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise ValueError("confidence must be a number between 0 and 1.")

        esc = EscalationEngine.evaluate(
            subject=email.subject,
            body=email.body_text,
            intent=intent_info.code,
            sentiment=sentiment,
            confidence=float(confidence),
            db_context=db_context,
        )

        # Escalation priority may upgrade taxonomy priority; never downgrade it.
        priority_order = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}
        base_priority = intent_info.priority.value
        escalated_priority = esc.priority
        final_priority = (
            escalated_priority
            if priority_order.get(escalated_priority, 0) >
               priority_order.get(base_priority, 0)
            else base_priority
        )

        human_review = bool(
            esc.human_review_required
            or data["escalation_needed"]
            or float(confidence) < 0.75
            or model_flagged_as_spam
        )
        routed_to = esc.routed_to if esc.human_review_required else intent_info.routing_destination

        reasons = list(esc.reasons)
        if data["escalation_needed"] and data["escalation_reason"]:
            reasons.append(data["escalation_reason"])
        if float(confidence) < 0.75:
            reasons.append("Model confidence below the review threshold.")
        if model_flagged_as_spam:
            reasons.append(
                "Model flagged this email as spam despite passing "
                "the deterministic spam check."
            )

        # Do not use a customer-facing response for acknowledgement-only cases.
        reply = data["suggested_reply"]
        if esc.send_acknowledgement_only:
            reply = (
                "Thank you for contacting us. We have received your message "
                "and referred it for specialist review. A team member will "
                "follow up after reviewing the available information."
            )

        # Enforce brand grounding even if the model ignores the prompt guidance.
        reply = self._ground_reply_brand(reply)

        extracted = db_context.get("extracted_entities", {})
        known_policies = {
            str(p.get("policy_number")) for p in db_context.get("policies", [])
            if p.get("policy_number")
        }
        known_claims = {
            str(c.get("claim_number")) for c in db_context.get("claims", [])
            if c.get("claim_number")
        }
        known_policies.update(str(x) for x in extracted.get("policy_numbers", []) if x)
        # Do not treat claim numbers extracted from the customer's message as
        # verified. Only claim records returned by the database are trusted.

        # If the customer references a claim number that is not present in
        # trusted records, do not let the model echo it or disclose a different
        # claim/policy that happens to belong to the same customer.
        referenced_claims = set(
            re.findall(r"\bCLM-[A-Z0-9-]+\b", f"{email.subject} {email.body_text}", re.IGNORECASE)
        )
        unknown_referenced_claims = {
            number for number in referenced_claims
            if number not in known_claims
        }
        if unknown_referenced_claims:
            reply = (
                "Thank you for contacting us. We could not verify the claim "
                "reference in your message from the information currently "
                "available. Your message requires a review of the relevant "
                "records before we can provide claim-specific information."
            )

        policy_number = data.get("policy_number")
        claim_number = data.get("claim_number")
        if policy_number is not None and (
            not isinstance(policy_number, str) or policy_number not in known_policies
        ):
            policy_number = None
        if claim_number is not None and (
            not isinstance(claim_number, str) or claim_number not in known_claims
        ):
            claim_number = None

        return TriageDecision(
            category=intent_info.category.value,
            intent=intent_info.code,
            priority=final_priority,
            urgency_score=urgency,
            sentiment=sentiment,
            escalation_needed=human_review,
            escalation_reason="; ".join(dict.fromkeys(reasons)),
            summary=data["summary"][:2000],
            policy_number=policy_number,
            claim_number=claim_number,
            routed_to=routed_to,
            suggested_reply=reply[:5000],
            confidence=float(confidence),
            human_review_required=human_review,
            raw_response="Processed via OmniRoute",
        )
