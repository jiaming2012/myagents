# Retrospective: Banking Payment Forecaster

## Milestone: v1.0 — Banking Payment Forecaster MVP

**Shipped:** 2026-05-05
**Phases:** 4 | **Plans:** 9 | **Commits:** ~102
**Timeline:** 25 days (2026-04-10 to 2026-05-05)

### What Was Built

- Zoho Calendar event parser with bill-to-account mapping via event titles and notes
- Xero OAuth2 integration for business account balances (replaced Mercury)
- Forecast engine projecting per-account balances with shortfall detection
- CLI with grouped and timeline views, ANSI colors, configurable horizon
- Gmail email alerts with HTML formatting, SHA-256 content-hash dedup
- Daily summary mode with cron-friendly Taskfile tasks

### What Worked

- TDD approach for core parsing and forecast calculation — caught edge cases early
- Phase insertion (01.1) for urgent Xero integration — decimal numbering kept things clean
- Code review catching real bugs (payments.yaml typo, duplicate email send)
- Human UAT as final gate before milestone close

### What Was Inefficient

- Xero scope migration (broad → granular) required multiple auth retry cycles during UAT
- Bank Summary report parser used string comparison against enum values — subtle bug that only surfaced at runtime
- Mercury references lingered in .env.example and docs after Xero replaced it
- VERIFICATION.md files accumulated "human_needed" status without clear resolution path

### Patterns Established

- `payments.yaml` as single source of truth for account definitions and payment schedules
- `xero_account_id` field for explicit Xero account name mapping (names include last4)
- Optional import guards for modules with external dependencies (alert_email)
- Atomic JSON writes via `os.replace` for state files
- `task xero:auth` for OAuth re-authorization flow from this project

### Key Lessons

- Xero granular scopes must be enabled in the developer portal AND requested in OAuth flow — both steps required
- Xero Bank Summary report only includes accounts with non-zero reconciled balances — must return 0.0 for configured accounts not in report
- Gmail strips `<style>` tags — all email CSS must be inline on elements
- Content-hash dedup is simpler and more correct than time-window dedup for alert scenarios

## Cross-Milestone Trends

| Metric | v1.0 |
|--------|------|
| Phases | 4 |
| Plans | 9 |
| Timeline | 25 days |
| Key tech | Python, Xero, Monarch, Zoho, Gmail SMTP |
