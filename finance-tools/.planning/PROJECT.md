# Banking Payment Forecaster

## What This Is

A CLI tool that combines Zoho Calendar payment schedules with real-time account balances from Monarch Money (personal) and Xero (business) to forecast upcoming payment obligations, project account balances after payments, detect shortfalls, and email alerts when scheduled payments would overdraw available funds. Runs daily via cron for unattended monitoring.

## Core Value

Know before payday whether you can cover every bill — see exactly which debit account pays which credit/loan obligation, what the balances will look like after, and get alerted if anything falls short.

## Current State

**Shipped:** v1.0 (2026-05-05)
**Codebase:** ~3,000 LOC Python across 6 modules
**Tech stack:** Python 3.12, Monarch Money API, Xero API, Zoho Calendar API, Gmail SMTP

## Requirements

### Validated

- v1.0 MAP-01: Bill-to-account mapping extracted from Zoho Calendar event notes
- v1.0 MAP-02: Bill name and amount parsed from Zoho Calendar event titles
- v1.0 MAP-03: Variable-amount payments flagged as estimates in output
- v1.0 FCST-01: `task forecast` with `--days` arg for configurable horizon
- v1.0 FCST-02: Shortfall detection when projected debits exceed available balance
- v1.0 FCST-03: Summary view with total outgoing, total available, net position
- v1.0 FCST-04: Real-time balances from Monarch Money (personal) and Xero (business)
- v1.0 ALRT-01: Gmail email alert on projected negative balance
- v1.0 ALRT-02: Daily summary mode via `--email-summary` flag
- v1.0 ALRT-03: SHA-256 content-hash dedup prevents duplicate alerts
- v1.0 ALRT-04: `--test-alert` flag for verifying email delivery

### Active

(None — next milestone requirements TBD via `/gsd-new-milestone`)

### Out of Scope

| Feature | Reason |
|---------|--------|
| Web dashboard or UI | CLI-first tool, no browser interface needed |
| Automatic payment execution | Read-only forecasting, never moves money |
| Real-time push notifications | Email alerts only, no mobile/push |
| Multi-user support | Single user, personal finance tool |
| Database backend | File-based config and caching, consistent with existing approach |

## Context

Shipped v1.0 with 4 phases (9 plans). The tool bridges three data sources: Zoho Calendar (payment due dates), Monarch Money (personal account balances), and Xero (business account balances via Bank Summary report). Payment schedules and account definitions live in `payments.yaml`.

Key modules:
- `zoho_calendar_payments.py` — Calendar parsing, bill-to-account mapping, grouped display
- `payment_forecast.py` — Forecast engine, CLI, email dispatch
- `alert_email.py` — HTML email construction, SMTP, dedup
- `xero_balances.py` — Xero OAuth2 token management, Bank Summary report parsing
- `coverage_report.py` — Balance resolution (Xero → Monarch fallback)
- `monarch_balances.py` — Monarch Money API client

## Constraints

- **Data source**: Zoho Calendar is source of truth for payment dates
- **Balance authority**: Monarch Money for personal, Xero for business accounts
- **Email**: Gmail via smtplib + App Password (not Gmail API)
- **Runtime**: Python 3.x, minimal dependencies (stdlib + dotenv/pyyaml/tabulate/requests)
- **No database**: File-based config and caching only

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Zoho Calendar as date authority | Calendar already maintained manually; avoids dual-source-of-truth | Good |
| Xero replaces Mercury for business balances | Mercury API deprecated; Xero already used by yumyums accounting | Good |
| Gmail SMTP + App Password over Gmail API | Zero OAuth complexity for email sending; stdlib smtplib sufficient | Good |
| SHA-256 content hash for alert dedup | Simpler than time-window dedup; detects when situation changes | Good |
| Inline CSS in HTML emails | Gmail strips style tags; must inline all styles | Good |
| Optional import guard for alert_email | Keeps forecast usable even if email module dependencies missing | Good |
| Granular Xero scope (accounting.reports.banksummary.read) | New apps after March 2026 can't use broad scopes | Good |

## Evolution

This document evolves at phase transitions and milestone boundaries.

---
*Last updated: 2026-05-05 after v1.0 milestone*
