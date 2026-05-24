---
phase: 03-email-alerts-daily-automation
plan: 01
subsystem: email-alerts
tags: [email, smtp, alerts, dedup, html]
dependency_graph:
  requires: [payment_forecast.py build_forecast output]
  provides: [alert_email.py module with all email functionality]
  affects: [payments.yaml, .env.example, .gitignore]
tech_stack:
  added: [smtplib, email.mime, hashlib]
  patterns: [atomic-json-write, content-hash-dedup, inline-css-email]
key_files:
  created: [alert_email.py]
  modified: [payments.yaml, .env.example, .gitignore]
decisions:
  - "Used stdlib smtplib over third-party email libraries for zero new dependencies"
  - "Inline CSS on all HTML elements since Gmail strips style tags"
  - "SHA-256 content hash truncated to 16 chars for dedup state file"
  - "Atomic JSON writes via os.replace to prevent corruption from concurrent runs"
metrics:
  duration: 130s
  completed: "2026-05-04T17:36:02Z"
  tasks_completed: 2
  tasks_total: 2
  files_created: 1
  files_modified: 3
---

# Phase 03 Plan 01: Alert Email Module Summary

**One-liner:** Standalone email engine with HTML alert/summary construction, Gmail SMTP via TLS, SHA-256 content-hash deduplication, per-account threshold filtering, and dry-run preview export.

## Task Results

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create alert_email.py module | f40aa0b | alert_email.py |
| 2 | Config updates | c7b1408 | payments.yaml, .env.example, .gitignore |

## What Was Built

### alert_email.py (360 lines)

11 public functions providing complete email alert functionality:

- **get_smtp_config()** / **get_alert_recipient()** -- Load SMTP credentials and recipient from .env with validation
- **check_alert_thresholds(forecast, accounts_config)** -- Filter accounts by alert_on config (error/warning/none)
- **build_alert_html(forecast, alertable_accounts)** -- Full forecast HTML table with severity-based row highlighting (#fee2e2 error, #fef9c3 warning)
- **build_summary_html(forecast)** -- Dual-mode: condensed digest for good days, full report for problem days
- **compute_alert_hash(account_id, projected_balance, payment_amounts)** -- SHA-256 content hash for dedup
- **should_send_alert(account_id, alert_hash)** / **record_alert_sent(account_id, alert_hash)** -- Dedup state management via .alert_state.json
- **send_email(subject, html_body, recipient, smtp_config)** -- Gmail SMTP with TLS, 30s timeout, error handling
- **export_preview(html_body, filename)** -- Dry-run HTML export to local file

### Config Changes

- **payments.yaml**: Added alert_on field to 6 depository accounts with min_balance. cap1-recurring-4354 set to "warning" (primary payment hub); remaining 5 set to "error".
- **.env.example**: Added SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL with App Password documentation link.
- **.gitignore**: Added .alert_state.json and forecast_preview.html exclusions.

## Deviations from Plan

None -- plan executed exactly as written.

## Decisions Made

1. **stdlib-only email stack**: Used smtplib + email.mime from Python stdlib. No new dependencies added beyond existing dotenv/pyyaml.
2. **Inline CSS everywhere**: Gmail strips `<style>` tags, so all styling is inline on elements per research findings.
3. **16-char hash truncation**: SHA-256 hex digest truncated to 16 chars for dedup state -- sufficient entropy for this use case, keeps state file readable.
4. **Atomic writes via os.replace**: Prevents .alert_state.json corruption if process is killed mid-write.

## Self-Check: PASSED

All files exist. All commits verified in git log.
