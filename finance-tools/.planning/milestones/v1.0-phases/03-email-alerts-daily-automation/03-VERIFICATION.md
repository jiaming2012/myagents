---
phase: 03-email-alerts-daily-automation
verified: 2026-05-04T18:15:00Z
status: human_needed
score: 3/4 must-haves verified
overrides_applied: 0
gaps:
  - truth: "The same shortfall does not produce duplicate email alerts within a configurable window (default 24 hours)"
    status: partial
    reason: "Dedup is implemented via content-hash only. No time-based window exists — once a hash is recorded, the alert never re-fires for that hash even after 24 hours pass. The 'configurable window' from ALRT-03 and ROADMAP SC3 is not implemented. The design decision D-05 in 03-CONTEXT.md explicitly chose content-hash over time-window, but this deviates from the requirement wording."
    artifacts:
      - path: "alert_email.py"
        issue: "should_send_alert() compares only hash, no timestamp/expiry check. record_alert_sent() stores sent_at but it is never read back for window comparison."
    missing:
      - "Either: implement time-based expiry in should_send_alert() (e.g., check if sent_at > now - 24h AND hash matches, then suppress) OR add an explicit override accepting the content-hash-only approach as the intended behavior"
human_verification:
  - test: "Dry-run email preview"
    expected: "Running `python payment_forecast.py --dry-run` creates forecast_preview.html in project root with a formatted HTML table of accounts, projected balances, and a summary section. File is viewable in a browser."
    why_human: "Requires live Monarch API credentials to fetch balances before email dispatch. Automated spot-check timed out at 30s waiting for network I/O."
  - test: "Full email delivery via --test-alert"
    expected: "Running `python payment_forecast.py --test-alert` with a configured .env (SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL) sends an email with [TEST] subject prefix to the configured recipient."
    why_human: "Requires real Gmail App Password credentials and live network access. Cannot verify email delivery without external service."
  - test: "Cron scheduling works"
    expected: "Adding `task forecast:email` or `python payment_forecast.py --email-summary` to a cron job runs the forecast unattended and sends the summary email daily."
    why_human: "Cron scheduling is a runtime infrastructure concern. The code is cron-friendly (non-interactive, exits with appropriate codes) but actual cron setup and daily execution cannot be verified statically."
---

# Phase 3: Email Alerts + Daily Automation Verification Report

**Phase Goal:** The system proactively emails the user when a projected shortfall is detected and can run unattended on a daily schedule, sending the full forecast report
**Verified:** 2026-05-04T18:15:00Z
**Status:** human_needed
**Re-verification:** No - initial verification

## Goal Achievement

### Observable Truths (from ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | When any funding account's projected balance goes negative, a Gmail alert is sent automatically with the shortfall details | VERIFIED | `check_alert_thresholds()` filters by severity, `send_email()` dispatches via smtplib TLS. Wired in payment_forecast.py main() after `print_summary()`. "Automatically" is via cron invocation of `--email-summary` per D-08. |
| 2 | Running with a daily-summary flag emails the complete forecast report to the configured recipient | VERIFIED | `--email-summary` flag exists in argparse (line 491-493). Calls `build_summary_html()` then `send_email()`. On problem days sends full report; on good days sends condensed digest. |
| 3 | The same shortfall does not produce duplicate email alerts within a configurable window (default 24 hours) | PARTIAL | Content-hash dedup implemented via `compute_alert_hash()` / `should_send_alert()` / `record_alert_sent()`. Idempotency achieved. However, no time-based window exists — the alert hash persists indefinitely with no expiry. ALRT-03 and ROADMAP SC3 specify a "configurable window (e.g., 24 hours)" which is absent. |
| 4 | User can verify email delivery with `--test-alert` without needing a real shortfall condition | VERIFIED | `--test-alert` flag exists (lines 494-497). Forces email send with `[TEST]` subject prefix regardless of shortfall state. Both shortfall path and summary path supported. |

**Score:** 3/4 truths verified (1 partial)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `alert_email.py` | Email construction, SMTP sending, dedup, threshold checking, dry-run export | VERIFIED | 361 lines. All 11 public functions present and importable: `get_smtp_config`, `get_alert_recipient`, `check_alert_thresholds`, `build_alert_html`, `build_summary_html`, `compute_alert_hash`, `should_send_alert`, `record_alert_sent`, `send_email`, `export_preview`. |
| `payment_forecast.py` | CLI flags --email-summary, --test-alert, --dry-run and email dispatch logic | VERIFIED | All three flags present in argparse. Full dispatch block at lines 562-628. ImportError guard for alert_email at lines 45-53. |
| `Taskfile.yml` | forecast:email and forecast:email-weekly task shortcuts | VERIFIED | Both tasks present at lines 59-67. Correct cmds: `--email-summary {{.CLI_ARGS}}` and `--email-summary --days 7`. |
| `payments.yaml` | alert_on field on funding accounts | VERIFIED | 6 accounts have `alert_on:` values: mercury-personal-6343 (error), cap1-income-8513 (error), cap1-recurring-4354 (warning), boa-checking-2803 (error), boa-business-1778 (error), navyfed-checking-7909 (error). |
| `.env.example` | SMTP credential documentation | VERIFIED | Contains SMTP_HOST=smtp.gmail.com, SMTP_PORT=587, SMTP_USER=, SMTP_PASSWORD=, ALERT_EMAIL= with App Password guidance link. |
| `.gitignore` | Ignores for alert state and preview files | VERIFIED | Both `.alert_state.json` (line 8) and `forecast_preview.html` (line 9) present. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `payment_forecast.py` | `alert_email.py` | `from alert_email import` | WIRED | Lines 46-52: optional import with `_has_alert_email` guard. All 10 functions imported. |
| `payment_forecast.py:main` | `alert_email.py:send_email` | email dispatch after build_forecast | WIRED | Line 586 (summary) and line 622 (test-alert): `send_email(subject, html_body, recipient, smtp_config)` |
| `payment_forecast.py:main` | `alert_email.py:check_alert_thresholds` | threshold filtering before alert send | WIRED | Lines 590, 611: `check_alert_thresholds(forecast, accounts_config)` called in both summary and test-alert paths. |
| `Taskfile.yml:forecast:email` | `payment_forecast.py --email-summary` | task runner CLI | WIRED | Line 62: `python payment_forecast.py --email-summary {{.CLI_ARGS}}` |
| `alert_email.py:check_alert_thresholds` | `payments.yaml alert_on field` | reads account config, filters by threshold | WIRED | Lines 91-100: reads `config.get("alert_on", "error")` per account, applies error/warning/none logic. |
| `alert_email.py:should_send_alert` | `.alert_state.json` | JSON file read, hash comparison | WIRED | Lines 296-300: `_load_state()` reads the file, compares `entry.get("hash")` to `alert_hash`. |
| `alert_email.py:send_email` | `smtp.gmail.com:587` | smtplib.SMTP + starttls | WIRED | Lines 341-344: `smtplib.SMTP(smtp_config["host"], smtp_config["port"], timeout=30)`, `.starttls()`, `.login()`, `.send_message()`. |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `alert_email.py:build_alert_html` | `forecast` dict | `payment_forecast.py:build_forecast()` called before email dispatch | Yes — live balances from Monarch/Xero APIs | FLOWING |
| `alert_email.py:build_summary_html` | `forecast` dict | Same `build_forecast()` output | Yes — same source | FLOWING |
| `alert_email.py:should_send_alert` | `alert_hash` | `compute_alert_hash()` called per account with real `projected_balance` and `payment_amounts` | Yes — derived from live forecast data | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| CLI flags present in help | `python payment_forecast.py --help \| grep -E "email-summary\|test-alert\|dry-run"` | All three flags shown with descriptions | PASS |
| All alert_email exports importable | `python -c "from alert_email import get_smtp_config, check_alert_thresholds, ..."` | "All exports OK" | PASS |
| Module imports without error | `python -c "import alert_email"` | Exit 0, no errors | PASS |
| forecast:email Taskfile entry | `grep "forecast:email:" Taskfile.yml` | Both entries present | PASS |
| .gitignore exclusions | `grep ".alert_state.json" .gitignore` | Both state and preview files excluded | PASS |
| --dry-run end-to-end | `python payment_forecast.py --dry-run` | Timed out — requires live API credentials for balance fetch | SKIP (needs human) |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|---------|
| ALRT-01 | 03-01-PLAN, 03-02-PLAN | System sends Gmail email alert when any funding account's projected balance goes negative | SATISFIED | `check_alert_thresholds()` detects negative balance (severity=error), `send_email()` dispatches via Gmail SMTP TLS. Full path wired in payment_forecast.py main(). |
| ALRT-02 | 03-02-PLAN | User can run forecast in daily summary mode that emails the full forecast report | SATISFIED | `--email-summary` flag triggers `build_summary_html()` + `send_email()`. Problem days get full report, good days get condensed digest. `task forecast:email` Taskfile shortcut enables cron setup. |
| ALRT-03 | 03-01-PLAN | Alerts are idempotent — same shortfall does not generate duplicate alerts within a configurable window | PARTIAL | Content-hash dedup prevents re-alerting for identical shortfalls. However, the "configurable window (e.g., 24 hours)" is not implemented — the hash persists indefinitely with no expiry mechanism. `sent_at` is stored but never read for window comparison. |
| ALRT-04 | 03-02-PLAN | User can test alerts with `--test-alert` flag that sends a sample email without requiring a real shortfall | SATISFIED | `--test-alert` flag forces email send with `[TEST]` subject prefix. Uses real forecast data. Wired in payment_forecast.py lines 609-623. |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `alert_email.py` | 313 | `sent_at` stored in `.alert_state.json` but never read back for time-window comparison | Warning | `sent_at` is dead data — stored but unused. Indicates incomplete implementation of time-window idempotency as specified in ALRT-03. |
| `payment_forecast.py` | 466 | `nmercury-biz-operations-0551` typo in payments.yaml (pre-existing, not introduced by Phase 3) | Info | Typo causes lookup failure for that payment's funding account. Pre-existing issue from Phase 2, not introduced by this phase. |

### Human Verification Required

#### 1. Dry-Run HTML Preview

**Test:** Run `python payment_forecast.py --dry-run` with valid Monarch credentials in `.env`
**Expected:** Creates `forecast_preview.html` in project root. Open in browser — should show HTML table with Account, Current Balance, Outgoing, Projected Balance columns. Shortfall accounts (if any) highlighted red (#fee2e2) or yellow (#fef9c3). Summary section at bottom with Total Outgoing, Total Available, Net Position.
**Why human:** Requires live Monarch API credentials to fetch balances before the email dispatch logic executes. Automated spot-check timed out (30s) waiting for network I/O without credentials.

#### 2. Full Email Delivery via --test-alert

**Test:** Configure `.env` with real Gmail App Password (SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL), then run `python payment_forecast.py --test-alert`
**Expected:** Email arrives at ALERT_EMAIL with `[TEST]` prefix in subject. Contains forecast HTML table with severity highlighting. Sender matches SMTP_USER.
**Why human:** Requires real Gmail App Password credentials and live SMTP connection. Cannot verify email delivery programmatically without external service access.

#### 3. Cron-Friendly Daily Automation

**Test:** Run `task forecast:email` (or add `task forecast:email` to crontab) with valid credentials
**Expected:** Command completes non-interactively, sends summary email, exits with code 0 (no shortfalls) or 1/2 (warnings/errors). No interactive prompts.
**Why human:** Actual cron scheduling and unattended daily execution cannot be verified statically. Requires runtime observation.

### Gaps Summary

**One actionable gap identified:**

**ALRT-03 configurable window:** The requirement specifies duplicate suppression "within a configurable window (e.g., 24 hours)". The implementation uses content-hash-based dedup with no time expiry — once an alert hash is recorded, it is suppressed indefinitely even after conditions change back and forth. The `sent_at` timestamp is stored in `.alert_state.json` but is never read back.

This appears to be an intentional design decision (03-CONTEXT.md D-05: "Content hash for dedup is more precise than time windows"). To accept this as the intended approach, add an override to this file. To fully satisfy the requirement, add a `ALERT_DEDUP_HOURS` env var (default 24) and modify `should_send_alert()` to also return `True` if `now - sent_at > alert_dedup_hours`, even when the hash matches.

**This looks intentional.** To accept this deviation, add to this file's frontmatter:

```yaml
overrides:
  - must_have: "The same shortfall does not produce duplicate email alerts within a configurable window (default 24 hours)"
    reason: "Content-hash dedup is the intentional implementation (D-05 in 03-CONTEXT.md). Content-hash is more precise than time-window: same shortfall never re-alerts, but a changed shortfall (new amount, different payment mix) does alert. sent_at is stored for audit purposes. This meets the spirit of ALRT-03 idempotency."
    accepted_by: "your-name"
    accepted_at: "2026-05-04T00:00:00Z"
```

---

_Verified: 2026-05-04T18:15:00Z_
_Verifier: Claude (gsd-verifier)_
