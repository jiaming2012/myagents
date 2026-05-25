# Phase 2: Forecast Engine + CLI - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-04
**Phase:** 02-forecast-engine-cli
**Areas discussed:** Module structure, Forecast calculation, CLI output design, Shortfall behavior

---

## Module Structure

| Option | Description | Selected |
|--------|-------------|----------|
| New payment_forecast.py | Standalone module with its own CLI entry point. Imports shared functions from coverage_report.py. | ✓ |
| Extend coverage_report.py | Add forecast mode to existing file. Simpler but mixes concerns. | |
| Shared library + two CLIs | Extract common functions into shared lib. Cleanest but more refactoring. | |

**User's choice:** New payment_forecast.py
**Notes:** None

| Option | Description | Selected |
|--------|-------------|----------|
| Import from coverage_report.py | payment_forecast.py imports directly from coverage_report.py | |
| You decide | Claude picks approach | ✓ |

**User's choice:** You decide (Claude's discretion)

| Option | Description | Selected |
|--------|-------------|----------|
| Yes — task forecast | Add task forecast, forecast:week, forecast:month to Taskfile | ✓ |
| No — just python command | No Taskfile changes | |

**User's choice:** Yes — task forecast

---

## Forecast Calculation

| Option | Description | Selected |
|--------|-------------|----------|
| Use current statement balance | Pull credit card balance from Monarch as payment amount | ✓ |
| Flag as estimate, use calendar amount | Use calendar event title amount, mark with ~ prefix | |
| Exclude from total, show separately | Don't include variable payments in balance math | |

**User's choice:** Use current statement balance

| Option | Description | Selected |
|--------|-------------|----------|
| Abort with error | Refuse to produce forecast if any payment lacks funding account | ✓ |
| Warn loudly but continue | Print WARNING, produce forecast for assigned payments only | |

**User's choice:** Abort with error
**Notes:** User initially said "Raise an exception" — clarified to mean abort entirely, forces data hygiene

| Option | Description | Selected |
|--------|-------------|----------|
| 30 days | Full billing cycle, matches FCST-01 spec | ✓ |
| 14 days | Two weeks ahead | |
| 7 days | One week | |

**User's choice:** 30 days

---

## CLI Output Design

| Option | Description | Selected |
|--------|-------------|----------|
| Group by funding account | Each account section with balance, payments, projection | |
| Chronological timeline | All payments by date with running balance | |
| Both views available | Default by-account, --timeline for chronological | ✓ |

**User's choice:** Both views available

---

## Shortfall Behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Negative projected balance only | Warn only when below $0 | |
| Configurable threshold | Warn below configurable buffer amount | |
| Both — negative is error, low is warning | Two severity levels: ERROR (red) and WARNING (yellow) | ✓ |

**User's choice:** Both — two severity levels

| Option | Description | Selected |
|--------|-------------|----------|
| Global default in payments.yaml | Single shortfall_threshold at top level | |
| Per-account in payments.yaml | Each account gets min_balance field | ✓ |
| CLI flag --min-balance | Pass as CLI argument | |

**User's choice:** Per-account in payments.yaml

---

## Claude's Discretion

- Whether to extract shared balance-fetching functions into a separate module or keep importing from coverage_report.py
- Terminal color/formatting approach
- How to handle balance fetch failures mid-forecast
