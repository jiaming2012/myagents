---
phase: 01-calendar-parsing-bill-mapping
plan: 02
subsystem: bill-mapping-pipeline
tags: [cli, balance-fetching, variable-resolution, grouped-display, calendar-update]
dependency_graph:
  requires: [parse_event_title, parse_event_notes, build_nickname_lookup, resolve_account, process_events, load_payments_config]
  provides: [update_event_title, display_grouped_payments, resolve_variable_amounts, bill-map-cli-mode]
  affects: [zoho_calendar_payments.py, Taskfile.yml]
tech_stack:
  added: []
  patterns: [asyncio-run-in-sync-main, abs-balance-for-credit-cards, grouped-display-by-account]
key_files:
  created: []
  modified:
    - zoho_calendar_payments.py
    - Taskfile.yml
decisions:
  - "Bill-map mode is opt-in via --bill-map flag; legacy display_events remains the default"
  - "resolve_variable_amounts uses asyncio.run() from sync main() matching coverage_report.py pattern"
  - "Credit card balances use abs() to convert negative Monarch values to positive payment amounts"
metrics:
  duration: 109s
  completed: 2026-05-04T04:46:00Z
  tasks: 2
  files: 2
---

# Phase 01 Plan 02: Bill Mapping Pipeline Summary

End-to-end CLI pipeline wiring: Zoho Calendar events parsed into structured payments, variable amounts resolved from Monarch Money balances, output grouped by funding account, with optional calendar write-back

## What Was Built

### Task 1: Wire balance fetching, variable resolution, grouped display, and calendar update into main()
Modified zoho_calendar_payments.py with 4 new functions and updated main():

- **update_event_title(access_token, calendar_id, event_uid, new_title, etag)** -- PUT to Zoho Calendar API to update event title with real amount (D-12). Requires etag for optimistic concurrency (Pitfall 4). Only invoked when --update-calendar flag is passed.
- **display_grouped_payments(payments, errors, balances)** -- Groups payments by fund_account_id, sorts by due_date within groups, shows ~estimate flag for variable payments, totals all payments, reports errors to stderr (D-16).
- **resolve_variable_amounts(payments, config)** -- Async function that fetches Monarch balances, resolves source_account nickname to account config, and replaces variable payment amounts with abs(balance) from Monarch (D-11, Pitfall 6).
- **main() updated** -- Added --bill-map and --update-calendar argparse flags. When --bill-map is passed: load_payments_config -> process_events -> resolve_variable_amounts -> optional update_event_title -> display_grouped_payments -> exit(1) on errors. Legacy display_events preserved as default.

Added imports: asyncio, coverage_report (fetch_monarch_balances, fetch_mercury_balances, resolve_balance) with try/except guard.

### Task 2: Add Taskfile entry
Added two task entries to Taskfile.yml:
- `bill-map` -- runs `python zoho_calendar_payments.py --bill-map` with CLI_ARGS passthrough
- `bill-map:month` -- shortcut for 30-day window

## Commits

| # | Hash | Type | Description |
|---|------|------|-------------|
| 1 | 8a6089a | feat | Wire balance fetching, variable resolution, grouped display into main() |
| 2 | e525aba | chore | Add bill-map task entries to Taskfile |

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None -- all functions are fully implemented. Variable resolution depends on live Monarch Money API; calendar update depends on live Zoho Calendar API. Both are gated behind CLI flags and API credentials.

## Checkpoint: Task 3 (human-verify)

Task 3 requires human verification of live API integration. The checkpoint was returned to the orchestrator for user approval.

## Verification Results

- `python -m pytest tests/test_parsing.py -v` -- 28/28 passed
- `python zoho_calendar_payments.py --help` -- shows --bill-map and --update-calendar flags
- `grep "bill-map:" Taskfile.yml` -- both task entries present
- All acceptance criteria patterns verified via grep

## Self-Check: PASSED

All 2 files found. All 2 commits verified. SUMMARY.md present.
