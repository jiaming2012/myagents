# Phase 3: Email Alerts + Daily Automation - Context

**Gathered:** 2026-05-04
**Status:** Ready for planning

<domain>
## Phase Boundary

Send Gmail alerts on projected shortfalls with idempotency, plus a cron-friendly summary mode that emails the full forecast report. Builds on Phase 2's forecast engine — reuses `build_forecast()` output and exit code severity levels.

</domain>

<decisions>
## Implementation Decisions

### Alert Trigger + Content
- **D-01:** Alert triggering is **configurable per-account** via an `alert_on` field in payments.yaml on each funding account. Values: `error` (negative balance only), `warning` (negative or below min_balance), `none` (no alerts). Default if omitted: `error`.
- **D-02:** Alert emails use **HTML with tables** — formatted tables showing account balances and shortfalls with color highlighting.
- **D-03:** Single recipient from `ALERT_EMAIL` env var in `.env`. No multi-recipient support needed.
- **D-04:** Alert email shows the **full forecast with problem accounts highlighted** — complete picture at a glance, shortfall accounts bolded/colored.

### Idempotency + Dedup
- **D-05:** Duplicate prevention via **content hash** — hash the shortfall details (account ID + projected balance + payment amounts triggering the shortfall). Only send if hash differs from last sent alert for that account.
- **D-06:** Dedup state stored in **`.alert_state.json`** in repo root (gitignored). Tracks per-account alert hashes and timestamps.

### Daily Summary Mode
- **D-07:** Daily summary sends **condensed digest on good days, full report when there are warnings/errors**. Good-day digest shows just summary totals (total outgoing, total available, net position). Problem-day report includes full per-account breakdown with highlighted shortfalls.
- **D-08:** Summary triggered via **`--email-summary` flag** on `payment_forecast.py`. Configurable range via existing `--days` flag (e.g., `--email-summary --days 7` for weekly, `--days 10` for 10-day). User sets up their own cron job. Add `task forecast:email` and `task forecast:email-weekly` shortcuts to Taskfile.yml.

### Test + Dry-Run Behavior
- **D-09:** `--test-alert` runs the **real forecast and forces the email send** regardless of whether shortfalls exist. Tests the full pipeline end-to-end.
- **D-10:** `--dry-run` **exports the email as an HTML file** (e.g., `forecast_preview.html`) that can be opened locally in a browser to preview exactly what would be sent. Does not send any email.

### Claude's Discretion
- HTML email template design (inline CSS for Gmail compatibility)
- smtplib connection handling (TLS, error recovery)
- `.alert_state.json` schema details (what fields beyond hash and timestamp)
- Whether `--email-summary` and `--alert` modes share a common email-building function or are separate

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Current Codebase
- `payment_forecast.py` — Forecast engine, `build_forecast()` output structure, exit codes, CLI argparse — the foundation this phase extends
- `coverage_report.py` — Balance fetching functions imported by payment_forecast.py
- `payments.yaml` — Account definitions (needs `alert_on` field added), payment schedules
- `Taskfile.yml` — Task shortcuts (needs email task entries)
- `.env.example` — Environment variables (needs `ALERT_EMAIL`, SMTP config)

### Requirements
- `.planning/REQUIREMENTS.md` — ALRT-01, ALRT-02, ALRT-03, ALRT-04 are the target requirements

### Prior Phase Context
- `.planning/phases/02-forecast-engine-cli/02-CONTEXT.md` — Phase 2 decisions (exit codes, shortfall detection, output formatting)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `payment_forecast.py:build_forecast()` — Returns per-account forecast dicts with projected balances and severity levels — direct input for alert content
- `payment_forecast.py:print_summary()` — Summary calculation logic (total outgoing, available, net) — reuse for digest email
- `payment_forecast.py:print_grouped_view()` — Account grouping and formatting logic — adapt for HTML email body
- `coverage_report.py:load_payments_yaml()` — Config loading

### Established Patterns
- Standalone Python scripts with argparse CLI, async main() for balance fetching
- `try/except ImportError` guards for optional dependencies
- Taskfile.yml task definitions with CLI_ARGS passthrough
- `.env` for credentials, `.gitignore` for local state files

### Integration Points
- payment_forecast.py needs new flags: `--email-summary`, `--test-alert`, `--dry-run`
- payments.yaml needs `alert_on` field on funding accounts
- .env.example needs SMTP/Gmail credentials (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL)
- .gitignore needs `.alert_state.json`
- Taskfile.yml needs `forecast:email` and `forecast:email-weekly` entries

</code_context>

<specifics>
## Specific Ideas

- Content hash for dedup is more precise than time windows — same shortfall doesn't re-alert, but a NEW shortfall on the same account does
- Dry-run exports HTML file for local browser preview — avoids needing to send test emails during development
- Condensed vs full report based on severity — keeps good-day emails short, bad-day emails detailed
- Per-account alert_on config lets the user silence noisy accounts while keeping critical ones alerting

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-email-alerts-daily-automation*
*Context gathered: 2026-05-04*
