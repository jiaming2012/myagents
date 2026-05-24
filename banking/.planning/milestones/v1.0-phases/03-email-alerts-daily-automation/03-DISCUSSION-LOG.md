# Phase 3: Email Alerts + Daily Automation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-04
**Phase:** 03-email-alerts-daily-automation
**Areas discussed:** Alert trigger + content, Idempotency + dedup, Daily summary mode, Test + dry-run behavior

---

## Alert Trigger + Content

| Option | Description | Selected |
|--------|-------------|----------|
| Errors only (negative balance) | Only email when a funding account would go negative | |
| Errors + warnings | Email on any shortfall — negative balance AND below-threshold warnings | |
| Configurable per-account | Each account gets an alert_on field (error, warning, or none) | ✓ |

**User's choice:** Configurable per-account
**Notes:** Most flexible — each account controls its own alert sensitivity

| Option | Description | Selected |
|--------|-------------|----------|
| Plain text | Simple text email | |
| HTML with tables | Formatted HTML with colored tables | ✓ |
| Both (multipart) | HTML with plain text fallback | |

**User's choice:** HTML with tables

| Option | Description | Selected |
|--------|-------------|----------|
| Single recipient from .env | One ALERT_EMAIL env var | ✓ |
| Configurable in payments.yaml | alert_recipients list | |

**User's choice:** Single recipient from .env

| Option | Description | Selected |
|--------|-------------|----------|
| Problem accounts only | Alert shows only triggered accounts | |
| Full forecast with highlights | Complete forecast, problem accounts highlighted | ✓ |
| You decide | Claude picks | |

**User's choice:** Full forecast with highlights

---

## Idempotency + Dedup

| Option | Description | Selected |
|--------|-------------|----------|
| Time window (default 24h) | Track last alert time per account | |
| Content hash | Hash shortfall details, alert only if hash changes | ✓ |
| You decide | Claude picks simplest approach | |

**User's choice:** Content hash

| Option | Description | Selected |
|--------|-------------|----------|
| JSON file (.alert_state.json) | Simple JSON in repo root, gitignored | ✓ |
| SQLite database | Local SQLite for alert history | |
| You decide | Claude picks | |

**User's choice:** JSON file (.alert_state.json)

---

## Daily Summary Mode

| Option | Description | Selected |
|--------|-------------|----------|
| Full forecast report | Complete forecast as HTML email | |
| Condensed digest | Summary totals + shortfalls only | |
| Full report only if issues | Condensed on good days, full on bad days | ✓ |

**User's choice:** Full report only if issues

| Option | Description | Selected |
|--------|-------------|----------|
| CLI flag (--daily-summary) | Flag on payment_forecast.py, user sets up cron | |
| Separate script | New daily_summary.py | |
| Same script, mode flag | --email-summary and --alert modes | |

**User's choice:** (Other) Summary range should be configurable, ie daily, weekly, 10 days, etc
**Notes:** Uses --email-summary flag with existing --days for range control

---

## Test + Dry-Run Behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Real forecast, forced send | Run actual forecast, send email regardless | ✓ |
| Canned sample email | Pre-built sample with fake data | |
| Real forecast with fake shortfall | Inject fake shortfall | |

**User's choice:** Real forecast, forced send

| Option | Description | Selected |
|--------|-------------|----------|
| Yes — show email without sending | Print to stdout | |
| No — --test-alert is enough | Keep simple | |

**User's choice:** (Other) Yes - export file to be opened locally to match email
**Notes:** --dry-run exports HTML file for browser preview instead of printing to stdout

---

## Claude's Discretion

- HTML email template design (inline CSS for Gmail compatibility)
- smtplib connection handling (TLS, error recovery)
- .alert_state.json schema details
- Whether email-building functions are shared between modes

## Deferred Ideas

None
