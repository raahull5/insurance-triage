# Autonomous Insurance Customer Support & Claims Triage System

An enterprise-grade autonomous system built to ingest, preprocess, classify, and triage insurance customer support emails and claims using Hermes AI reasoning, SQLite relational data context, and automated response capabilities.

## Features

- **IMAP/SMTP via Himalaya CLI** — Real Gmail connectivity using the [himalaya](https://github.com/pimalaya/himalaya) v2 CLI
- **Out-of-band identity verification** — One-time codes, 30-min TTL, single-use enforcement
- **Durable reply audit** — Every dispatch outcome persisted with body digest
- **28 canonical intents** — Insurance-specific classification with LLM/heuristic fallback
- **Four-level thread resolution** — Exact match, claim reference, policy reference, fuzzy subject
- **Safety filtering** — Spam detection, automated-sender detection, PII masking, rate limits
- **Live dashboard** — KPIs, ticket distribution, queue status on http://127.0.0.1:8080
- **Autonomous pipeline** — Background worker with health monitoring, circuit breaking, retry logic

## Architecture Overview

```
insurance_triage/
├── config/
│   └── default_config.yaml      # Polling, AI, DB, and Gmail configuration
├── data/
│   ├── insurance_triage.db      # SQLite relational database (13 tables)
│   ├── watermark.json           # Polling watermark state tracker
│   └── triage_results.csv       # Exported CSV reporting file (15 columns)
├── src/
│   ├── ai/                      # Intent taxonomy, LLM reasoning, safety, identity
│   │   ├── intents.py
│   │   ├── prompts.py
│   │   ├── reasoning_engine.py
│   │   ├── safety.py
│   │   └── identity_verification.py
│   ├── autoreply/               # Customer response generation & dispatch
│   │   ├── reply_generator.py
│   │   └── sender.py
│   ├── core/                    # Resilience, circuit breaking, retry policies
│   │   └── resilience.py
│   ├── dashboard/               # Live enterprise operations web dashboard
│   │   ├── data_service.py
│   │   └── server.py
│   ├── db/                      # Relational schema, SQLite operations, test seed data
│   │   ├── database.py
│   │   ├── repository.py
│   │   ├── schema.py
│   │   └── seed_data.py
│   ├── ingestion/               # Himalaya CLI client, email detector, HTML stripper
│   │   ├── detector.py
│   │   ├── gmail_config.py
│   │   ├── himalaya_client.py
│   │   └── preprocessor.py
│   ├── pipeline/                # 1-minute polling coordinator & workflow runner
│   │   ├── autonomous_runner.py
│   │   └── threading_engine.py
│   └── reporting/               # CSV export pipeline
│       └── csv_exporter.py
├── scripts/
│   └── production_run_demo.py   # Background worker demo with identity verification
├── tests/                       # 240+ unit tests
├── main.py                      # CLI entry point
└── requirements.txt             # Project dependencies
```

## Quick Start

### Prerequisites

- Python 3.11+
- Himalaya CLI v2.1.0+ ([installation guide](https://github.com/pimalaya/himalaya))
- Gmail account with App Password enabled
- OpenAI API key (or compatible LLM endpoint)

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Install Himalaya CLI

**Windows:**
```bash
# Download latest release
curl -L -o himalaya.zip https://github.com/pimalaya/himalaya/releases/download/v2.1.0/himalaya.x86_64-windows.zip
unzip himalaya.zip
move himalaya.exe %LOCALAPPDATA%\Programs\himalaya\
set PATH=%LOCALAPPDATA%\Programs\himalaya;%PATH%
```

**macOS/Linux:**
```bash
# Via Homebrew (macOS)
brew install himalaya

# Or download binary
curl -L https://github.com/pimalaya/himalaya/releases/latest/download/himalaya.$(uname -m)-$(uname -s | tr '[:upper:]' '[:lower:]').tgz | tar xz
sudo mv himalaya /usr/local/bin/
```

Verify: `himalaya --version` should show `v2.1.0` or later.

### 3. Configure Gmail

Create `~/.config/himalaya/config.toml` (or `%APPDATA%\himalaya\config.toml` on Windows):

```toml
[accounts.default]
default = true
email = "support@apexshield.com"
display-name = "Apex Shield Insurance Support"

imap.server = "imap.gmail.com:993"
imap.sasl.plain.username = "support@apexshield.com"
imap.sasl.plain.passwd.raw = "YOUR_GMAIL_APP_PASSWORD"

smtp.server = "smtp://smtp.gmail.com:587"
smtp.starttls = true
smtp.sasl.plain.username = "support@apexshield.com"
smtp.sasl.plain.passwd.raw = "YOUR_GMAIL_APP_PASSWORD"

folder.aliases.inbox = "INBOX"
folder.aliases.sent = "[Gmail]/Sent Mail"
folder.aliases.drafts = "[Gmail]/Drafts"
folder.aliases.trash = "[Gmail]/Trash"
```

**Generate Gmail App Password:**
1. Enable 2FA: https://myaccount.google.com/security
2. Create App Password: https://myaccount.google.com/apppasswords
3. Replace `YOUR_GMAIL_APP_PASSWORD` above

Test: `himalaya account check`

### 4. Seed Test Database
```bash
python main.py seed
```

### 5. Check System Status
```bash
python main.py status
```

### 6. Test Single Email Triage
```bash
python main.py test-email \
  --from "sarah.jenkins@example.com" \
  --subject "Status of claim CLM-2024-09112" \
  --body "Please let me know how my claim is progressing."
```

### 7. Launch Live Operations Dashboard
```bash
python main.py dashboard --port 8080
```
Open `http://127.0.0.1:8080` in your browser.

### 8. Run Autonomous Background Pipeline
```bash
python main.py run
```

Polls Gmail every 60 seconds (configurable in `config/default_config.yaml`).

## Configuration

### Environment Variables

```bash
export OPENAI_API_KEY="sk-..."           # OpenAI API key for LLM reasoning
export OPENAI_BASE_URL="..."            # Optional: custom OpenAI-compatible endpoint
export OPENAI_MODEL="gpt-4o"            # Default: gpt-4o
```

### config/default_config.yaml

Key settings:

- `polling.interval_seconds`: How often to poll Gmail (default: 60)
- `system.dry_run`: When `true`, no emails are actually sent
- `reply.enabled`: Toggle auto-reply feature
- `reply.rate_limit_global_per_hour`: Global outgoing rate limit (default: 60)
- `dashboard.host`: Dashboard bind address (default: 127.0.0.1)
- `dashboard.port`: Dashboard port (default: 8080)

## Identity Verification

When a sender quotes someone else's claim/policy number, the system:

1. **Does not disclose** private record data to the unverified address
2. **Issues a challenge** — one-time 6-digit code sent to the customer's on-file email
3. **Waits for redemption** — sender replies with the code within 30 minutes
4. **Grants verified status** — future emails from that address receive normal replies
5. **Enforces single-use** — each code can only be redeemed once

Codes expire after 30 minutes and are bound to the specific sender/customer pair.

## Reply Audit Trail

Every dispatch outcome is persisted to the `reply_audit` table:

- `email_id` — which email triggered the reply
- `timestamp` — when the dispatch occurred
- `status` — `SENT`, `DRY_RUN`, `SUPPRESSED_*`, `RATE_LIMITED`, `FAILED`
- `reply_body_hash` — SHA-256 digest of the reply content
- `recipient` — who the reply was addressed to
- `error_details` — populated on failure

Immutable append-only log for compliance and forensic review.

## Testing

Run full test suite (241 tests):

```bash
python -m unittest discover tests
```

Run specific test modules:

```bash
python -m unittest tests.test_identity_verification
python -m unittest tests.test_production_run
python -m unittest tests.test_reply_audit
```

## Production Deployment Checklist

### Pre-Deployment

- [ ] **Himalaya CLI installed and on PATH**
  - `himalaya --version` returns v2.1.0+
  - `himalaya account check` succeeds

- [ ] **Gmail App Password configured**
  - 2FA enabled on Google account
  - App Password generated and stored in config
  - IMAP/SMTP connectivity verified

- [ ] **Database initialized**
  - `python main.py seed` completed
  - `data/insurance_triage.db` contains 13 tables
  - Foreign-key constraints enabled

- [ ] **Environment variables set**
  - `OPENAI_API_KEY` configured
  - Optional: `OPENAI_BASE_URL` for custom endpoint

- [ ] **Configuration reviewed**
  - `config/default_config.yaml` tuned for production
  - `system.dry_run: false` (when ready for live)
  - `reply.enabled: true`
  - Dashboard bind address confirmed (default: 127.0.0.1)

### Security Checklist

- [ ] **No credentials in code or config files**
  - Gmail passwords use `passwd.cmd` with secure vault
  - OpenAI key via environment variable only
  - `.gitignore` excludes `data/*.db`, `data/*.json`, `*.toml` with secrets

- [ ] **Identity verification enabled**
  - Unverified senders cannot access private records
  - Challenge codes expire after 30 minutes
  - Single-use enforcement active

- [ ] **Rate limiting configured**
  - Global outgoing limit (default: 60/hour)
  - Per-sender incoming limit (default: 5/hour)
  - Per-thread reply ceiling (default: 3)

- [ ] **Audit trail active**
  - `reply_audit` table receiving all dispatch outcomes
  - Body digests computed and stored
  - Retention policy defined (optional)

### Monitoring

- [ ] **Dashboard accessible**
  - `http://127.0.0.1:8080` loads successfully
  - `/api/health` returns HTTP 200
  - `/api/kpis` shows real-time metrics

- [ ] **Log aggregation configured** (optional)
  - Application logs structured for SIEM ingestion
  - Error alerts configured for on-call

- [ ] **Backup strategy defined**
  - SQLite database backed up regularly
  - Watermark file preserved across restarts

### Launch Sequence

1. **Dry-run validation**
   ```bash
   # Set in config/default_config.yaml:
   system.dry_run: true
   reply.enabled: true
   
   python main.py run
   ```
   Observe logs for 5-10 polling cycles. Confirm:
   - No emails sent (dry-run mode)
   - Triage decisions logged correctly
   - No unhandled exceptions

2. **Production cutover**
   ```bash
   # Set in config/default_config.yaml:
   system.dry_run: false
   
   python main.py run
   ```

3. **Smoke test**
   - Send test email to Gmail account
   - Wait 60 seconds for poll cycle
   - Verify reply received (if eligible)
   - Check dashboard for updated KPIs
   - Confirm audit row in `reply_audit` table

4. **Monitor first 24 hours**
   - Watch for spike in `FAILED` dispatches
   - Verify identity challenges reaching customers
   - Check spam/automated filtering accuracy
   - Review escalation queue

### Troubleshooting

**Himalaya CLI not found:**
```bash
# Verify PATH includes Himalaya binary
which himalaya  # Linux/macOS
where himalaya  # Windows

# Add to PATH if missing
export PATH="$LOCALAPPDATA/Programs/himalaya:$PATH"  # Windows
```

**Gmail auth failure:**
```bash
# Test IMAP directly
himalaya mailbox list -a default --json

# Check config syntax
himalaya account check

# Verify App Password is correct (no spaces, 16 chars)
```

**No replies sent:**
- Check `system.dry_run` is `false`
- Verify `reply.enabled` is `true`
- Check OpenAI API key is valid
- Review `reply_audit` table for `SUPPRESSED_*` statuses

**Identity challenges not delivered:**
- Confirm customer email address is correct in database
- Check spam folder for challenge emails
- Verify SMTP credentials in Himalaya config

**Dashboard not loading:**
```bash
# Check if port is already in use
netstat -an | grep 8080

# Try different port
python main.py dashboard --port 8081
```

### Maintenance

**Database backup:**
```bash
sqlite3 data/insurance_triage.db ".backup 'data/backup_$(date +%Y%m%d).db'"
```

**Log rotation:**
Configure logrotate (Linux) or scheduled task (Windows) to archive old logs.

**Schema migrations:**
Future schema changes will include migration scripts in `scripts/`.

---

## License

Internal use only. Contact IT Security for deployment authorization.

## Support

- **Documentation:** `README.md`, inline code comments
- **Test Suite:** `python -m unittest discover tests`
- **Issues:** Contact development team
- **Security Incidents:** Follow incident response procedure
