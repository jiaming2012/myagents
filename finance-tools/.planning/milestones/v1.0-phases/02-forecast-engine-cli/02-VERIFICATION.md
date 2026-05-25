---
phase: 02-forecast-engine-cli
verified: 2026-05-04T16:00:00Z
status: human_needed
score: 3/3 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Run `task forecast` (or `python payment_forecast.py --days 30`) against live balances"
    expected: "Grouped forecast output showing each funding account's current balance, scheduled payments table, projected balance with color indicators (red/bold for shortfall, yellow for low, green for ok), and summary line with total outgoing/available/net"
    why_human: "Requires live Monarch Money and Xero credentials to fetch real balances; cannot verify actual runtime output or color rendering programmatically"
  - test: "Run `python payment_forecast.py --timeline --days 7` and inspect output"
    expected: "Chronological single-table view of all payments sorted by date, with running balance per funding account colored by severity"
    why_human: "Visual formatting and correct running-balance sequencing require human inspection with live data"
  - test: "Check exit code: `python payment_forecast.py --days 30; echo $?`"
    expected: "Exit code 0 (all ok), 1 (any account below min_balance), or 2 (any account negative projected balance)"
    why_human: "Correct exit code depends on real balance data; logic is verified by unit tests but runtime behavior against live data needs confirmation"
---

# Phase 2: Forecast Engine + CLI Verification Report

**Phase Goal:** User runs a single CLI command to see all upcoming payments within a configurable horizon, per-account projected balances after those payments, and clear warnings when any funding account would go negative
**Verified:** 2026-05-04T16:00:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | User can run `task forecast` (or `python payment_forecast.py`) with `--days N` to control the forecast horizon (defaults to 30 days) | VERIFIED | `Taskfile.yml` has `forecast:` task calling `python payment_forecast.py {{.CLI_ARGS}}`; `--days` arg present in argparse (line 473-476); `--help` output confirms; default=30 |
| 2 | Each funding account shows its current balance minus scheduled outgoing payments, with a clear shortfall flag if the projected balance goes negative | VERIFIED | `build_forecast()` computes `projected_balance = current_balance - sum(payment_amounts)` (lines 282-295); `print_grouped_view()` renders with BOLD_RED "SHORTFALL" for error, YELLOW "LOW BALANCE" for warning (lines 354-361); two-tier severity in `build_forecast()` (error < 0, warning < min_balance) |
| 3 | A summary line shows total outgoing payments, total available across all funding accounts, and net position for the chosen horizon | VERIFIED | `print_summary()` renders total_outgoing, total_available, net_position (lines 432-448); `build_forecast()` computes all three summary fields (lines 296-317); net_position colored green if >= 0, BOLD_RED if < 0 |

**Score:** 3/3 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `payment_forecast.py` | Complete forecast engine + CLI (533 lines) | VERIFIED | Exists, substantive (533 lines), wired via Taskfile.yml and directly runnable |
| `test_forecast.py` | Unit tests for forecast calculation logic | VERIFIED | 17 tests, all passing — covers validate_funding_accounts, get_payment_dates_in_horizon, resolve_payment_amount, build_forecast (all severity paths), graceful degradation |
| `payments.yaml` | min_balance on 6 funding accounts | VERIFIED | All 6 expected accounts have correct values: mercury-personal-6343=500, cap1-recurring-4354=500, boa-business-1778=500, cap1-income-8513=200, boa-checking-2803=200, navyfed-checking-7909=200; zero credit accounts have min_balance |
| `requirements.txt` | tabulate and python-dateutil | VERIFIED | Both present on lines 8-9, matching bare-name convention |
| `Taskfile.yml` | forecast, forecast:week, forecast:month entries | VERIFIED | All three entries present (lines 44-57) with correct commands and desc fields |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `payment_forecast.py` | `coverage_report.py` | `from coverage_report import fetch_monarch_balances, resolve_balance, load_payments_yaml` | WIRED | Line 35: exact import confirmed; try/except ImportError guard present |
| `payment_forecast.py` | `xero_balances.py` | `from xero_balances import fetch_xero_balances` | WIRED | Lines 40-43: import with graceful fallback to None if unavailable |
| `payment_forecast.py` | `payments.yaml` | `load_payments_yaml()` | WIRED | Line 488: `config = load_payments_yaml()` called in main() |
| `payment_forecast.py` | `build_forecast()` | `main()` calls build_forecast then formats output | WIRED | Line 518: `forecast = build_forecast(config, monarch_balances, xero_balances, days=args.days)` |
| `Taskfile.yml` | `payment_forecast.py` | `python payment_forecast.py` | WIRED | Lines 47, 53, 57: all three forecast tasks invoke payment_forecast.py |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `print_grouped_view()` | `forecast["accounts"]` | `build_forecast()` -> `resolve_balance()` -> Monarch/Xero APIs | Yes — resolve_balance() routes to live API calls | FLOWING |
| `print_timeline_view()` | `all_payments` (flattened from forecast accounts) | Same as above | Yes | FLOWING |
| `print_summary()` | `forecast["summary"]` | `build_forecast()` aggregate computation over live balances | Yes | FLOWING |

Note: Level 4 data-flow through live Monarch/Xero APIs cannot be confirmed without credentials — flagged for human verification.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| CLI help shows --days and --timeline | `python payment_forecast.py --help` | Both flags documented in argparse output | PASS |
| All key symbols importable | `python -c "from payment_forecast import build_forecast, validate_funding_accounts, ..."` | All 10 symbols imported successfully | PASS |
| All 17 unit tests pass | `python -m pytest test_forecast.py -v` | 17 passed in 0.67s | PASS |
| min_balance values correct in payments.yaml | `python3 -c "import yaml; ..."` | All 6 accounts verified, no credit accounts with min_balance | PASS |
| Live forecast run (requires credentials) | `python payment_forecast.py --days 30` | Requires Monarch Money + Xero auth | SKIP |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| FCST-01 | 02-01-PLAN.md, 02-02-PLAN.md | User can run `task forecast` with `--days` arg (default 30) | SATISFIED | Taskfile.yml forecast task + argparse --days with default=30 |
| FCST-02 | 02-01-PLAN.md | Detects shortfalls when debits exceed available balance within horizon | SATISFIED | build_forecast() severity logic: projected_balance < 0 = error; print_grouped_view renders "SHORTFALL" in BOLD_RED |
| FCST-03 | 02-02-PLAN.md | Summary view: total outgoing, total available, net position | SATISFIED | print_summary() renders all three; build_forecast() computes them |

No orphaned requirements: REQUIREMENTS.md maps FCST-01, FCST-02, FCST-03 to Phase 2 — all three are claimed and covered by the plans.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| None found | — | — | — | — |

Scanned `payment_forecast.py` and `test_forecast.py` for TODO/FIXME, placeholder returns, hardcoded empty data, and stub indicators. None found. All functions have docstrings and real implementations. `return null` / `return {}` / `return []` patterns checked — none flow to user-visible rendering.

### Human Verification Required

#### 1. Live Forecast Output — Grouped View

**Test:** Run `task forecast` (or `python payment_forecast.py --days 30`) with valid `.env` credentials for Monarch Money and Xero
**Expected:** Grouped forecast output with each funding account section showing current balance, payment table (Date / Payment / Amount columns), projected balance with color coding (green ok, yellow low balance with threshold, red bold shortfall), and summary section at bottom
**Why human:** Live Monarch Money and Xero API calls required; cannot mock credentials in automated verification

#### 2. Live Forecast Output — Timeline View

**Test:** Run `python payment_forecast.py --timeline --days 7`
**Expected:** Single chronological table of all payments across all accounts sorted by date, with a Running Balance column that updates per-account and is colored by severity (red if negative, yellow if below min_balance)
**Why human:** Visual correctness and running-balance sequencing require human inspection; runtime behavior with real data

#### 3. Exit Code Verification

**Test:** Run `python payment_forecast.py --days 30; echo "Exit: $?"`
**Expected:** Exit code 0 if all projected balances are above min_balance thresholds, 1 if any account is below min_balance but not negative, 2 if any account projects negative
**Why human:** Correct exit code depends on live balance data; unit tests verify the logic but runtime confirmation is needed

### Gaps Summary

No automated gaps found. All three roadmap success criteria are verified at the code level:

1. `task forecast` / `python payment_forecast.py --days N` — CLI entry point is fully wired
2. Per-account shortfall detection — build_forecast() implements two-tier severity with visual output
3. Summary view with total outgoing/available/net — print_summary() renders complete summary

Three human verification items remain for live runtime confirmation. These are not code gaps — the implementation is complete and all unit tests pass (17/17). Human verification confirms the implementation works end-to-end with live credentials.

---

_Verified: 2026-05-04T16:00:00Z_
_Verifier: Claude (gsd-verifier)_
