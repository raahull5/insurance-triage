# Insurance Claims Triage Agent

An AI-powered insurance claims triage system built using **Hermes Agent software**.

The system processes incoming insurance customer emails, understands intent and urgency, extracts claim information, applies safety controls, verifies customer and policy context, prevents duplicate claims, creates official claims only after deterministic validation, and escalates cases requiring human judgment.

It combines AI reasoning with deterministic business rules, database state, safety controls, human-in-the-loop escalation, auditability, and an operational dashboard.

---

# Overview

Insurance organizations receive large volumes of customer emails covering:

- New insurance claims
- Claim status requests
- Claim delays
- Policy questions
- Document requests
- Billing and payment questions
- Complaints
- Coverage questions
- Other customer-support requests

Manually reviewing, classifying, routing, and processing these emails is repetitive and time-consuming.

The **Insurance Claims Triage Agent** automates the initial triage and workflow while keeping authoritative business decisions under deterministic validation and human review where required.

### Core Principle

> **Use AI for understanding and reasoning, but use deterministic application logic for authoritative business decisions.**

The AI can interpret an email, determine intent and urgency, and extract potential claim information.

However, it cannot create an official insurance claim simply because it classified an email as a new claim.

Official claim creation requires validation of customer identity, policy information, policy status, incident information, and duplicate-claim conditions.

---

# Architecture

```text
                         CUSTOMER EMAIL
                              │
                              ▼
                    ┌─────────────────────┐
                    │   Email Ingestion   │
                    │   Gmail / Himalaya  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   Preprocessing     │
                    │ HTML / Normalization│
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    Hermes Agent     │
                    │    AI Reasoning     │
                    │                     │
                    │ Intent              │
                    │ Priority            │
                    │ Urgency             │
                    │ Sentiment           │
                    │ Routing             │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    Safety Engine    │
                    │                     │
                    │ Spam                │
                    │ Automated Senders   │
                    │ PII                 │
                    │ Rate Limits         │
                    │ Thread Loops        │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Customer Context    │
                    │ & Identity          │
                    └──────────┬──────────┘
                               │
                 ┌─────────────┴─────────────┐
                 │                           │
                 ▼                           ▼
             NEW CLAIM                 OTHER INTENT
                 │                           │
                 ▼                           ▼
          Claim Intake                 Support Ticket
                 │
                 ▼
        Required FNOL Fields?
             │           │
            YES          NO
             │           │
             ▼           ▼
    Policy Verification  Human Review
             │
             ▼
      Customer / Policy Match
             │
             ▼
         Policy Active?
             │
             ▼
      Incident Date Valid?
             │
             ▼
       Duplicate Claim Check
             │
        ┌────┴─────┐
        │          │
        ▼          ▼
   Create Claim  Human Review
        │
        ▼
   Database + Audit
        │
        ▼
      Dashboard