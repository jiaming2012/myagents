---
phase: 03-email-alerts-daily-automation
plan: 02
subsystem: email-alerts-cli
tags: [cli, email, forecast, taskfile, dispatch]
dependency_graph:
  requires: [alert_email.py from 03-01]
  provides: [CLI email flags on payment_forecast.py, Taskfile email shortcuts]
  affects: [payment_forecast.py, Taskfile.yml]
tech_stack:
  added: []
  patterns: [optional-import-guard, cli-flag-dispatch, dedup-before-send]
key_files:
  created: []
  modified: [payment_forecast.py, Taskfile.yml]
decisions:
  - "ImportError guard for alert_email keeps forecast usable without email module"
  - "Email dispatch runs after print_summary but before sys.exit to preserve exit codes"
  - "Dry-run combined with --email-summary previews summary; combined with --test-alert previews alert"
metrics:
  duration: 89s
  completed: "2026-05-04T17:39:56Z"
  tasks_completed: 2
  tasks_total: 3
  files_created: 0
  files_modified: 2
status: checkpoint-paused
checkpoint_at: task-3
---

# Phase 03 Plan 02: Email CLI Integration Summary

**One-liner:** Wired alert_email.py into payment_forecast.py CLI with --email-summary, --test-alert, --dry-run flags, dedup-filtered shortfall alerts, and Taskfile shortcuts for cron scheduling.

## Task Results

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add email flags and dispatch logic to payment_forecast.py | 99a9582 | payment_forecast.py |
| 2 | Add forecast:email and forecast:email-weekly to Taskfile.yml | f7d23fe | Taskfile.yml |
| 3 | Verify email pipeline end-to-end | -- | CHECKPOINT (human-verify) |

## What Was Built

### payment_forecast.py Changes (90 lines added)

Three areas modified:

1. **Import section**: Optional import of all 10 alert_email functions with `_has_alert_email` guard. Forecast remains fully functional without the email module installed.

2. **Argparse**: Three new flags:
   - `--email-summary` -- Cron-friendly daily summary mode
   - `--test-alert` -- Force-send test email with [TEST] prefix
   - `--dry-run` -- Export HTML to forecast_preview.html

3. **Main dispatch** (after print_summary, before sys.exit):
   - `--email-summary`: Sends condensed/full summary via build_summary_html, then checks shortfall alerts with dedup (compute_alert_hash + should_send_alert + record_alert_sent)
   - `--test-alert`: Forces email regardless of shortfalls, uses [TEST] subject prefix
   - `--dry-run`: Exports HTML preview via export_preview, works with both --email-summary and --test-alert
   - Exit codes unchanged from Phase 2 behavior

### Taskfile.yml Changes

Two new task entries:
- `forecast:email` -- Runs `--email-summary` with CLI_ARGS passthrough for additional flags
- `forecast:email-weekly` -- Runs `--email-summary --days 7` for weekly cron

## Deviations from Plan

None -- plan executed exactly as written.

## Decisions Made

1. **Optional import guard**: alert_email imported with try/except ImportError, setting `_has_alert_email = False` on failure. This keeps the forecast tool usable even if alert_email.py is missing.
2. **SMTP config loaded once**: smtp_config and recipient loaded at the top of the email dispatch block to avoid redundant .env reads.
3. **Exit codes preserved**: Email dispatch block sits between print_summary() and sys.exit(determine_exit_code()), so exit code always reflects forecast severity, not email success/failure.

## Known Stubs

None -- all dispatch paths are fully wired to alert_email.py functions.

## Self-Check: PASSED

All files exist. All commits verified in git log.
