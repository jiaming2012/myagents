---
phase: 02-forecast-engine-cli
plan: 02
subsystem: cli
tags: [argparse, tabulate, ansi-colors, asyncio, taskfile]

requires:
  - phase: 02-forecast-engine-cli/01
    provides: "build_forecast(), validate_funding_accounts(), resolve_payment_amount() calculation engine"
provides:
  - "CLI entry point with async main(), --days and --timeline flags"
  - "Grouped-by-account output view with ANSI severity coloring"
  - "Chronological timeline view with running balances"
  - "Summary line with total outgoing, available, net position"
  - "Severity-based exit codes (0=ok, 1=warning, 2=error)"
  - "Taskfile.yml forecast, forecast:week, forecast:month entries"
  - "Actionable funding_account validation error with date/amount and available account list"
affects: [03-notifications, 04-automation]

tech-stack:
  added: [tabulate, python-dateutil]
  patterns: [ANSI color with TTY detection, severity-based exit codes, argparse async CLI]

key-files:
  created: []
  modified: [payment_forecast.py, Taskfile.yml]

key-decisions:
  - "Used tabulate with tablefmt='simple' for clean terminal output"
  - "Color class with TTY detection disables ANSI when piped"
  - "Exit codes: 0=ok, 1=warning (below min_balance), 2=error (negative projected)"
  - "Improved funding_account error per user feedback: show day/amount per payment and list available accounts"

patterns-established:
  - "CLI error messaging: show actionable context (dates, amounts) plus valid values for reference"
  - "ANSI color pattern: Color class with disable() classmethod gated on isatty()"

requirements-completed: [FCST-01, FCST-03]

duration: 12min
completed: 2026-05-04
---

# Phase 02 Plan 02: Forecast CLI Output and Taskfile Summary

**CLI entry point with argparse (--days, --timeline), ANSI-colored grouped/timeline views, severity exit codes, Taskfile entries, and actionable funding_account validation errors**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-05-04T15:30:00Z
- **Completed:** 2026-05-04T15:42:00Z
- **Tasks:** 3
- **Files modified:** 2

## Accomplishments
- Complete CLI tool with two output views (grouped by funding account, chronological timeline)
- ANSI-colored severity indicators: red/bold for shortfall, yellow for low balance, green for ok
- Summary line showing total outgoing, total available, and net position
- Taskfile.yml entries for forecast, forecast:week, forecast:month
- Actionable error messages showing date/amount per unassigned payment and available funding account values

## Task Commits

Each task was committed atomically:

1. **Task 1: CLI entry point with output formatting and exit codes** - `66140cd` (feat)
2. **Task 2: Taskfile.yml forecast entries** - `0ba584d` (chore)
3. **Task 3: Improve funding_account error messaging** - `b519cc8` (fix)

## Files Created/Modified
- `payment_forecast.py` - Complete CLI with async main(), Color class, grouped/timeline views, summary, exit codes, improved validation error
- `Taskfile.yml` - Added forecast, forecast:week, forecast:month task entries

## Decisions Made
- Used tabulate with tablefmt="simple" for minimal, clean terminal tables
- Color class uses classmethod disable() pattern for TTY detection
- Exit codes follow convention: 0=ok, 1=warning, 2=error
- Improved funding_account error to show payment day/amount and list available account IDs per user feedback

## Deviations from Plan

### User-Requested Changes

**1. Improved funding_account validation error messaging**
- **Found during:** Task 3 (checkpoint verification)
- **Issue:** User tested CLI and found error output insufficient -- showed only payment names without dates/amounts, and didn't show available funding_account values
- **Fix:** Modified validate_funding_accounts() to return full payment dicts; added collect_funding_accounts() helper; updated error output to show day_of_month and amount per payment and list all available funding_account values from payments.yaml
- **Files modified:** payment_forecast.py
- **Committed in:** b519cc8

---

**Total deviations:** 1 user-requested improvement
**Impact on plan:** Improved usability of error output. No scope creep -- same validation, better messaging.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Forecast CLI is complete and runnable via `task forecast`
- Payments with null funding_account will block forecast with actionable error
- Ready for notification/alerting integration or automation

---
*Phase: 02-forecast-engine-cli*
*Completed: 2026-05-04*
