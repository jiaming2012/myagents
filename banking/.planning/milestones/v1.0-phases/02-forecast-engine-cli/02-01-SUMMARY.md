---
phase: 02-forecast-engine-cli
plan: 01
subsystem: forecast-engine
tags: [tdd, forecast, calculation, payments, shortfall-detection]

dependency_graph:
  requires: [coverage_report.py, xero_balances.py, payments.yaml]
  provides: [payment_forecast.py, test_forecast.py]
  affects: [payments.yaml, requirements.txt]

tech_stack:
  added: [tabulate, python-dateutil]
  patterns: [dateutil.rrule, try/except ImportError guards, async balance fetching]

key_files:
  created:
    - payment_forecast.py
    - test_forecast.py
  modified:
    - payments.yaml
    - requirements.txt

decisions:
  - "Used credit_account parameter on resolve_payment_amount rather than auto-detection to keep unit tests deterministic"
  - "Applied T-02-03 mitigation: --days range check 1-365 in CLI entry point"

metrics:
  duration_seconds: 259
  completed: "2026-05-04T15:28:29Z"
  tasks_completed: 2
  tasks_total: 2
  test_count: 17
  test_pass: 17
---

# Phase 02 Plan 01: Forecast Calculation Engine Summary

TDD forecast engine with dateutil.rrule date handling, per-account balance projection, two-tier shortfall severity (error/warning), and credit card variable amount resolution via abs(Monarch balance).

## Completed Tasks

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | TDD forecast calculation core | `30486d8` (RED), `ea80417` (GREEN) | payment_forecast.py, test_forecast.py |
| 2 | Config updates -- min_balance + requirements | `0e115e0` | payments.yaml, requirements.txt |

## What Was Built

### payment_forecast.py (267 lines)
Four core exported functions:

- **validate_funding_accounts(payments)** -- Returns list of payment names missing funding_account. Treats None and empty string as missing.
- **get_payment_dates_in_horizon(day_of_month, days_ahead, start)** -- Uses `dateutil.rrule(MONTHLY, bymonthday=N)` for correct month-boundary handling. Months without the target day (e.g., Feb 31) are skipped rather than silently rolled.
- **resolve_payment_amount(payment, account_lookup, monarch_balances, xero_balances, credit_account)** -- Returns `abs(monarch_balance)` for credit card payments, falls back to YAML amount if balance unavailable.
- **build_forecast(config, monarch_balances, xero_balances, days)** -- Groups payments by funding account, resolves balances, computes projected_balance, assigns severity (ok/warning/error), produces summary totals.

Internal helper:
- **_find_credit_card_for_payment(payment, accounts)** -- Heuristic matching: autopay_type presence + name/nickname matching against credit card accounts.

CLI entry point with argparse (--days, --timeline), async balance fetching, basic output formatting.

### test_forecast.py (17 tests)
- 3 tests: validate_funding_accounts (all valid, missing, empty string)
- 4 tests: get_payment_dates_in_horizon (normal, short month, past day, same day)
- 3 tests: resolve_payment_amount (credit card abs, non-credit yaml, credit fallback)
- 6 tests: build_forecast (grouping, error severity, warning severity, ok severity, summary totals, no-payments account)
- 1 test: balance fetch failure graceful degradation

### payments.yaml
Added `min_balance` to 6 funding accounts:
- 500: mercury-personal-6343, cap1-recurring-4354, boa-business-1778
- 200: cap1-income-8513, boa-checking-2803, navyfed-checking-7909

### requirements.txt
Added: tabulate, python-dateutil (bare names, matching existing convention)

## Decisions Made

1. **credit_account as explicit parameter** -- resolve_payment_amount takes an optional credit_account parameter rather than auto-detecting internally. This keeps the function testable without complex mocking of the nickname-matching heuristic.
2. **T-02-03 mitigation applied** -- --days argument validated to 1-365 range in the CLI entry point per threat model.
3. **Unknown balance = error severity** -- When resolve_balance returns None, account gets balance=0.0, source="unknown", and severity is forced to "error" regardless of projected balance (can't verify solvency without a balance).

## Deviations from Plan

None -- plan executed exactly as written.

## TDD Gate Compliance

- RED gate: `30486d8` -- test(02-01): add failing tests (17 failures, ModuleNotFoundError)
- GREEN gate: `ea80417` -- feat(02-01): implement forecast engine (17 tests pass)
- REFACTOR gate: Not needed -- code was clean after GREEN phase, all functions have docstrings.

## Known Stubs

None -- all functions are fully implemented with real logic.

## Self-Check: PASSED
