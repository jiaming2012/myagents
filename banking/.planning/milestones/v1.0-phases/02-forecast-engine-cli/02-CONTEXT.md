# Phase 2: Forecast Engine + CLI - Context

**Gathered:** 2026-05-04
**Status:** Ready for planning

<domain>
## Phase Boundary

Build a payment forecast CLI tool that projects per-account balances forward using real-time balances (Monarch for personal, Xero for business) and upcoming payment schedules from Zoho Calendar. Detect shortfalls (negative projected balance) and low-balance warnings. Present results grouped by funding account (default) or chronologically (--timeline flag).

</domain>

<decisions>
## Implementation Decisions

### Module Structure
- **D-01:** New `payment_forecast.py` module in repo root. Standalone CLI entry point, separate from coverage_report.py.
- **D-02:** payment_forecast.py imports shared functions from coverage_report.py (fetch_monarch_balances, resolve_balance, load_payments_yaml) — same pattern zoho_calendar_payments.py already uses. Claude's discretion on whether to extract a shared lib or keep importing directly.
- **D-03:** Add `task forecast` command to Taskfile.yml that runs `python payment_forecast.py`. Also add `task forecast:week` (--days 7) and `task forecast:month` (--days 30) shortcuts.

### Forecast Calculation
- **D-04:** Variable-amount payments (credit cards) use the **current statement balance from Monarch** as the payment amount. Pull the credit card's current balance via monarch_match and use that as the projected debit. Utilities and other variable amounts use the calendar event title amount.
- **D-05:** Payments without a funding account assigned in payments.yaml cause the forecast to **abort with error**. Exit non-zero and list which payments need funding_account assignment. Forces data hygiene — no forecast produced with incomplete mappings.
- **D-06:** Default forecast horizon is **30 days**. User overrides with `--days N` per FCST-01.

### CLI Output Design
- **D-07:** Default view: **group by funding account**. Each account section shows current balance, list of outgoing payments (date, name, amount), and projected balance after all payments.
- **D-08:** Alternative view: `--timeline` flag shows **chronological timeline** of all payments across all accounts, with running balance per account.
- **D-09:** Summary line at bottom: total outgoing payments, total available across all funding accounts, net position for the horizon (per FCST-03).

### Shortfall Behavior
- **D-10:** Two severity levels: **negative projected balance = ERROR** (red/bold), **below per-account threshold = WARNING** (yellow). Nuanced view of financial health.
- **D-11:** Low-balance threshold configured **per-account** in payments.yaml via a `min_balance` field on each funding account. Accounts without min_balance default to 0 (only negative triggers warning).
- **D-12:** Exit code reflects worst severity: 0 = all clear, 1 = warnings only, 2 = errors (negative balance). Enables scripted checks.

### Claude's Discretion
- Whether to extract shared balance-fetching functions into a separate module or keep importing from coverage_report.py
- Terminal color/formatting approach (ANSI codes, tabulate library, or plain text with markers)
- How to handle the case where Monarch or Xero balance fetch fails mid-forecast (graceful degradation vs abort)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Current Codebase
- `coverage_report.py` — Balance fetching (fetch_monarch_balances, fetch_xero_balances), resolve_balance(), load_payments_yaml(), get_upcoming_payments(), print_report() — primary pattern reference
- `zoho_calendar_payments.py` — Event fetching, parsing, process_events(), resolve_variable_amounts() — payment data pipeline
- `monarch_balances.py` — Standalone balance module pattern
- `xero_balances.py` — Xero balance fetching, token management
- `payments.yaml` — Account definitions, funding_account mappings, payment schedules

### Requirements
- `.planning/REQUIREMENTS.md` — FCST-01, FCST-02, FCST-03 are the target requirements

### Prior Phase Context
- `.planning/phases/01.1-xero-business-balance-integration/01.1-CONTEXT.md` — Xero integration decisions (balance dict shape, resolve_balance pattern)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `coverage_report.py:fetch_monarch_balances()` — Async Monarch balance fetch, returns dict
- `coverage_report.py:resolve_balance()` — Checks xero_account_id then monarch_match, returns (balance, source)
- `coverage_report.py:load_payments_yaml()` — Loads payments.yaml config
- `coverage_report.py:get_upcoming_payments()` — Returns payments due within N days
- `xero_balances.py:fetch_xero_balances()` — Xero business balance fetch
- `zoho_calendar_payments.py:process_events()` — Zoho Calendar event parsing and account resolution
- `zoho_calendar_payments.py:resolve_variable_amounts()` — Resolves variable payments using Monarch balances

### Established Patterns
- Standalone Python scripts with argparse CLI, async main() for balance fetching
- `try/except ImportError` guards for optional dependencies
- `tabulate` in requirements.txt (available for table formatting)
- Taskfile.yml task definitions with CLI_ARGS passthrough

### Integration Points
- payment_forecast.py imports from coverage_report.py (balance fetching, resolve_balance)
- payment_forecast.py imports from xero_balances.py (if needed directly)
- payments.yaml needs `min_balance` field added to funding accounts
- Taskfile.yml needs `forecast`, `forecast:week`, `forecast:month` entries

</code_context>

<specifics>
## Specific Ideas

- Forecast aborts (non-zero exit) if any payment lacks a funding account — forces complete data before producing output
- Variable payments use live Monarch credit card balance as the payment amount — real numbers, not estimates
- Per-account min_balance threshold in payments.yaml enables two-tier shortfall detection (ERROR vs WARNING)
- Exit codes: 0=clear, 1=warnings, 2=errors — enables scripted automation in Phase 3

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 02-forecast-engine-cli*
*Context gathered: 2026-05-04*
