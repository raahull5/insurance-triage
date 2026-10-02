# Production Deployment Record

**Date:** 2026-09-26 (UTC+05:30)  
**Status:** Functional tests passed; controlled trial pending  
**System:** Autonomous Insurance Customer Support & Claims Triage

---

## Deployment Sign-Off

### Verification Results

| Component | Status | Evidence |
|-----------|--------|----------|
| **Database Schema** | ✅ PASS | 13 tables, 0 FK violations, seeded |
| **Himalaya CLI v2** | ✅ PASS | Installed on PATH, v1→v2 syntax migrated |
| **Configuration** | ✅ PASS | `config.yaml` and `config.toml` present |
| **Core Modules** | ✅ PASS | All 6 key modules present |
| **Test Suite** | ✅ PASS | 241/241 tests passing, `OK` |

### Enterprise Features Enabled

- ✅ Durable reply audit (12th table, body digest, 7 dispatch paths)
- ✅ Out-of-band identity verification (one-time codes, 30-min TTL, single-use)
- ✅ Four-level thread resolution (exact, claim ref, policy ref, fuzzy)
- ✅ Safety filtering (spam, automated-sender, PII masking, rate limits)
- ✅ Live dashboard (`http://127.0.0.1:8080`, KPIs, queue status)
- ✅ Autonomous background pipeline (health monitoring, circuit breaking, retries)
- ✅ Himalaya v2 IMAP/SMTP integration (real Gmail transport)

---

## Pre-Launch Checklist

### Immediate Actions (User)

- [ ] **Add Gmail App Password to `%APPDATA%\himalaya\config.toml`**
  ```toml
  imap.sasl.plain.passwd.raw = "YOUR_16_CHAR_APP_PASSWORD"
  smtp.sasl.plain.passwd.raw = "YOUR_16_CHAR_APP_PASSWORD"
  ```
  Generate at: https://myaccount.google.com/apppasswords

- [ ] **Verify Himalaya connectivity**
  ```bash
  export PATH="$LOCALAPPDATA/Programs/himalaya:$PATH"
  himalaya account check
  ```

- [ ] **Set environment variables**
  ```bash
  export OPENAI_API_KEY="sk-..."
  # Optional: export OPENAI_BASE_URL="..."
  ```

### Dry-Run Phase (5-10 polling cycles)

1. **Keep `system.dry_run: true` in `config/default_config.yaml`**

2. **Start background pipeline**
   ```bash
   python main.py run
   ```

3. **Observe logs for:**
   - Successful IMAP connections
   - Triage decisions being logged
   - No unhandled exceptions
   - Identity challenges issued correctly
   - Reply audit rows being persisted

4. **Verify dashboard**
   ```bash
   # In another terminal:
   python main.py dashboard --port 8080
   ```
   Open `http://127.0.0.1:8080` and confirm:
   - Health endpoint responds
   - KPIs are non-zero
   - Distribution charts render

### Production Cutover

1. **Stop dry-run (Ctrl+C)**

2. **Set `system.dry_run: false` in `config/default_config.yaml`**

3. **Start live pipeline**
   ```bash
   python main.py run
   ```

4. **Smoke test (within 5 minutes)**
   - Send test email to `support@apexshield.com`
   - Wait 60 seconds (one poll cycle)
   - Verify reply received
   - Check `reply_audit` table for new row

5. **Monitor first 24 hours**
   - Watch for spikes in `FAILED` status
   - Verify identity challenges reaching customers
   - Review escalation queue
   - Confirm spam filtering accuracy

---

## Support Contacts

| Issue | Action |
|-------|--------|
| Himalaya CLI errors | Check `himalaya account check` |
| Gmail auth failures | Verify App Password, check config syntax |
| No replies sent | Check `reply_audit` table, review `SUPPRESSED_*` statuses |
| Dashboard unavailable | Verify port 8080 is free, check firewall |
| Performance degradation | Monitor background worker health, check OpenAI API status |

---

## Rollback Procedure

If critical issues arise:

```bash
# 1. Stop pipeline
# Ctrl+C

# 2. Revert to dry-run mode
# Edit config/default_config.yaml:
#   system.dry_run: true

# 3. Restart for diagnostics
python main.py run

# 4. Review logs and reply_audit table
# Contact development team
```

---

## Post-Launch Tasks

- [ ] **24-hour monitoring** — Check dashboard and logs hourly
- [ ] **Weekly backup** — `sqlite3 data/insurance_triage.db ".backup 'backup_$(date +%Y%m%d).db'"`
- [ ] **Monthly review** — Check KPI trends, reply audit retention, escalation queue

---

**Prepared by:** Autonomous System  
**Timestamp:** 2026-09-26T00:41:16+05:30  
**System Version:** 24 PDF Stages Complete + Post-PDF Gaps Closed  
**Test Coverage:** 241/241 passing
