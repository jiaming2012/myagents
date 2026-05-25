# Phase 2: Forecast Engine + CLI - Research

**Researched:** 2026-05-04
**Domain:** Python CLI tool -- payment forecasting with live balance integration
**Confidence:** HIGH

## Summary

Phase 2 builds a new `payment_forecast.py` CLI that projects per-account balances forward by subtracting upcoming scheduled payments from live balances (Monarch for personal, Xero for business). The codebase already has all the building blocks: `coverage_report.py` provides balance fetching (`fetch_monarch_balances`, `resolve_balance`), YAML loading, and payment scheduling; `zoho_calendar_payments.py` shows the import-from-coverage-report pattern and variable-amount resolution via Monarch. The new module assembles these existing primitives into a forecast calculation with shortfall detection and a formatted CLI output.

The primary complexity is in the calculation logic (grouping payments by funding account, computing running projected balances, handling variable-amount credit card payments) and the output formatting (two views: grouped-by-account default and chronological timeline). All balance-fetching, API auth, and YAML parsing are solved problems in the existing codebase.

Two new dependencies are needed: `python-dateutil` (already installed, used by xero_python) for correct date recurrence math, and `tabulate` (not yet installed, latest is 0.10.0) for aligned table output.

**Primary recommendation:** Build `payment_forecast.py` as a standalone script following the exact patterns in `coverage_report.py` (argparse CLI, async main for balance fetching, import shared functions). Add `tabulate` to requirements.txt. Add `min_balance` field to funding accounts in `payments.yaml`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** New `payment_forecast.py` module in repo root. Standalone CLI entry point, separate from coverage_report.py.
- **D-02:** payment_forecast.py imports shared functions from coverage_report.py (fetch_monarch_balances, resolve_balance, load_payments_yaml) -- same pattern zoho_calendar_payments.py already uses. Claude's discretion on whether to extract a shared lib or keep importing directly.
- **D-03:** Add `task forecast` command to Taskfile.yml that runs `python payment_forecast.py`. Also add `task forecast:week` (--days 7) and `task forecast:month` (--days 30) shortcuts.
- **D-04:** Variable-amount payments (credit cards) use the current statement balance from Monarch as the payment amount. Pull the credit card's current balance via monarch_match and use that as the projected debit. Utilities and other variable amounts use the calendar event title amount.
- **D-05:** Payments without a funding account assigned in payments.yaml cause the forecast to abort with error. Exit non-zero and list which payments need funding_account assignment.
- **D-06:** Default forecast horizon is 30 days. User overrides with `--days N` per FCST-01.
- **D-07:** Default view: group by funding account. Each account section shows current balance, list of outgoing payments (date, name, amount), and projected balance after all payments.
- **D-08:** Alternative view: `--timeline` flag shows chronological timeline of all payments across all accounts, with running balance per account.
- **D-09:** Summary line at bottom: total outgoing payments, total available across all funding accounts, net position for the horizon (per FCST-03).
- **D-10:** Two severity levels: negative projected balance = ERROR (red/bold), below per-account threshold = WARNING (yellow). Nuanced view of financial health.
- **D-11:** Low-balance threshold configured per-account in payments.yaml via a `min_balance` field on each funding account. Accounts without min_balance default to 0 (only negative triggers warning).
- **D-12:** Exit code reflects worst severity: 0 = all clear, 1 = warnings only, 2 = errors (negative balance). Enables scripted checks.

### Claude's Discretion
- Whether to extract shared balance-fetching functions into a separate module or keep importing from coverage_report.py
- Terminal color/formatting approach (ANSI codes, tabulate library, or plain text with markers)
- How to handle the case where Monarch or Xero balance fetch fails mid-forecast (graceful degradation vs abort)

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| FCST-01 | User can run `task forecast` with `--days` arg to set forecast horizon (default 30) | D-03 (Taskfile entries), D-06 (default 30), argparse pattern from coverage_report.py |
| FCST-02 | System detects shortfalls -- flags when scheduled debits exceed available balance | D-10 (two severity levels), D-11 (per-account min_balance), D-12 (exit codes) |
| FCST-03 | Summary view showing total outgoing, total available, net position | D-09 (summary line at bottom) |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Balance fetching (Monarch) | External API (async) | -- | Monarch Money API via `monarchmoney` library; already implemented in coverage_report.py |
| Balance fetching (Xero) | External API (sync) | -- | Xero SDK via `xero_balances.py`; already implemented |
| Payment schedule loading | Local file (YAML) | -- | `payments.yaml` is the single source of truth for payment definitions |
| Forecast calculation | Local computation | -- | Pure Python arithmetic: balance minus scheduled payments per account |
| Shortfall detection | Local computation | -- | Compare projected balance against 0 (error) and min_balance (warning) |
| CLI output formatting | Local computation | -- | `tabulate` for tables, ANSI escape codes for color |
| CLI entry point | Python script + Taskfile | -- | `payment_forecast.py` with argparse, Taskfile.yml shortcuts |

## Standard Stack

### Core (Already Installed)

| Library | Version | Purpose | Status |
|---------|---------|---------|--------|
| `monarchmoney` | 0.1.15 | Monarch Money balance API | Already in requirements.txt [VERIFIED: pip3 show] |
| `xero-python` | installed | Xero business balance API | Already in requirements.txt [VERIFIED: pip3 show] |
| `pyyaml` | installed | payments.yaml parsing | Already in requirements.txt [VERIFIED: requirements.txt] |
| `requests` | installed | HTTP calls | Already in requirements.txt [VERIFIED: requirements.txt] |
| `python-dotenv` | installed | .env loading | Already in requirements.txt [VERIFIED: requirements.txt] |
| `python-dateutil` | 2.9.0.post0 | Date recurrence (rrule) | Already installed as xero_python dep [VERIFIED: pip3 show] |

### New Dependencies

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| `tabulate` | 0.10.0 | Aligned CLI table output | Variable-width account names break manual f-string formatting; tabulate handles alignment cleanly [VERIFIED: pip3 index versions -- 0.10.0 is latest] |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `tabulate` | `rich` | Style mismatch -- existing codebase uses plain `print()` statements |
| `tabulate` | Manual f-strings | Breaks alignment with variable-width account names; fragile |
| `python-dateutil` rrule | Manual try/except month-rollover | Already known buggy in coverage_report.py lines 69-89 |

**Installation:**
```bash
pip install tabulate>=0.10.0
```

Add to requirements.txt:
```
tabulate>=0.10.0
python-dateutil>=2.9.0
```

Note: `python-dateutil` is already installed but not listed in requirements.txt. Add it explicitly since payment_forecast.py will import it directly. [VERIFIED: requirements.txt does not contain python-dateutil]

## Architecture Patterns

### System Architecture Diagram

```
                     CLI Entry
                        |
                  payment_forecast.py
                   (argparse --days N, --timeline)
                        |
              +---------+---------+
              |                   |
        Load Config          Fetch Balances
     (load_payments_yaml)    (async: Monarch + Xero)
              |                   |
              +---------+---------+
                        |
                  Validate Data
              (check all payments have
               funding_account -- abort if not)
                        |
                  Build Forecast
              (group payments by funding acct,
               compute projected balance per acct,
               resolve variable amounts from Monarch)
                        |
                  Detect Shortfalls
              (projected < 0 = ERROR,
               projected < min_balance = WARNING)
                        |
              +---------+---------+
              |                   |
        Default View        Timeline View
     (grouped by account)  (chronological, --timeline)
              |                   |
              +---------+---------+
                        |
                  Print Summary
              (total outgoing, total available,
               net position)
                        |
                  Set Exit Code
              (0=clear, 1=warning, 2=error)
```

### Recommended Project Structure

```
banking/
├── payment_forecast.py      # NEW: Phase 2 CLI entry point
├── coverage_report.py       # EXISTING: shared balance functions imported from here
├── zoho_calendar_payments.py # EXISTING: reference for import pattern
├── xero_balances.py         # EXISTING: Xero balance fetching
├── monarch_balances.py      # EXISTING: standalone Monarch balance display
├── payments.yaml            # MODIFIED: add min_balance field to funding accounts
├── Taskfile.yml             # MODIFIED: add forecast, forecast:week, forecast:month
└── requirements.txt         # MODIFIED: add tabulate, python-dateutil
```

### Pattern 1: Import Shared Functions from coverage_report.py

**What:** Reuse existing balance-fetching and YAML-loading functions by importing from coverage_report.py, following the exact pattern zoho_calendar_payments.py already uses.
**When to use:** Always -- this is the locked decision (D-02).

```python
# Source: zoho_calendar_payments.py lines 49-57 (existing pattern) [VERIFIED: codebase]
try:
    from coverage_report import fetch_monarch_balances, resolve_balance, load_payments_yaml, get_upcoming_payments
except ImportError:
    print("Error: coverage_report.py must be in the same directory", file=sys.stderr)
    sys.exit(1)

try:
    from xero_balances import fetch_xero_balances
except ImportError:
    fetch_xero_balances = None
```

**Recommendation on discretion area (extract shared lib):** Keep importing directly from coverage_report.py. Extracting a shared lib would require refactoring coverage_report.py (breaking existing imports from zoho_calendar_payments.py) for minimal benefit. The import pattern is already established and works.

### Pattern 2: Async Main with Balance Fetching

**What:** Use `asyncio.run(main())` pattern for async Monarch balance fetching alongside sync Xero balance fetching.
**When to use:** Always -- Monarch API is async.

```python
# Source: coverage_report.py lines 252-273 (existing pattern) [VERIFIED: codebase]
async def main():
    parser = argparse.ArgumentParser(description="Payment forecast")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--timeline", action="store_true")
    args = parser.parse_args()

    config = load_payments_yaml()

    # Validate: all payments must have funding_account (D-05)
    missing = [p for p in config.get("payments", []) if not p.get("funding_account")]
    if missing:
        print("ERROR: These payments have no funding_account assigned:", file=sys.stderr)
        for p in missing:
            print(f"  - {p['name']}", file=sys.stderr)
        sys.exit(2)

    monarch_balances = await fetch_monarch_balances()
    xero_balances = fetch_xero_balances() if fetch_xero_balances else {}

    # ... forecast calculation ...

if __name__ == "__main__":
    asyncio.run(main())
```

### Pattern 3: Date Recurrence with dateutil.rrule

**What:** Use `rrule(MONTHLY, bymonthday=X)` for correct next-occurrence calculation instead of manual month-rollover.

```python
# Source: dateutil docs -- rrule [CITED: dateutil.readthedocs.io/en/stable/rrule.html]
from dateutil.rrule import rrule, MONTHLY
from datetime import datetime, timedelta

def get_payment_dates_in_horizon(day_of_month, days_ahead, start=None):
    """Get all future occurrences of day_of_month within the horizon."""
    start = start or datetime.now()
    end = start + timedelta(days=days_ahead)
    # rrule handles short months (Feb 29, months with 30/31 days)
    dates = list(rrule(MONTHLY, bymonthday=day_of_month, dtstart=start, until=end))
    return [d for d in dates if d > start]
```

### Pattern 4: ANSI Color Output

**What:** Use ANSI escape codes for terminal coloring (ERROR=red/bold, WARNING=yellow) without adding a dependency.
**Recommendation on discretion area:** Use simple ANSI codes. The codebase already uses plain print(); ANSI codes are the minimal addition for color without importing rich/colorama.

```python
# ANSI color codes [ASSUMED -- standard terminal escape sequences]
class Color:
    RED = "\033[31m"
    BOLD_RED = "\033[1;31m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    RESET = "\033[0m"

    @classmethod
    def disable(cls):
        """Disable colors (for non-TTY output)."""
        cls.RED = cls.BOLD_RED = cls.YELLOW = cls.GREEN = cls.RESET = ""

# Detect non-TTY and disable colors
import sys
if not sys.stdout.isatty():
    Color.disable()
```

### Pattern 5: Variable Amount Resolution for Credit Cards (D-04)

**What:** For credit card payments, use the current Monarch balance as the payment amount (what you owe = what you'll pay).

```python
# Source: zoho_calendar_payments.py resolve_variable_amounts pattern [VERIFIED: codebase lines 268-300]
def resolve_payment_amount(payment, account, monarch_balances, xero_balances):
    """Resolve the actual amount for a payment.

    For credit card accounts: use current balance from Monarch as payment amount.
    For others: use the amount from payments.yaml.
    """
    # Find the payment's target account (the credit card being paid)
    # D-04: credit cards use current statement balance
    target_acct_id = payment.get("name")  # need to match to credit card account
    # ... lookup logic based on payment name/zoho_match to credit card account
    # ... then resolve_balance() on that credit card account

    # For non-variable: use payments.yaml amount
    return payment.get("amount", 0)
```

**Key insight for D-04:** The existing `resolve_variable_amounts()` in zoho_calendar_payments.py resolves via the `source_account` field and `is_variable` flag on calendar events. For the forecast, the mechanism is different: identify which payments map to credit card accounts (type == "credit") and pull the credit card's current balance from Monarch as the payment amount. The amount field in payments.yaml may be stale/zero for credit cards -- the live Monarch balance is the truth.

### Pattern 6: Taskfile Entry (D-03)

```yaml
# Source: Taskfile.yml existing pattern [VERIFIED: codebase]
  forecast:
    desc: Payment forecast (default 30 days)
    cmds:
      - python payment_forecast.py {{.CLI_ARGS}}

  forecast:week:
    desc: Payment forecast for next 7 days
    cmds:
      - python payment_forecast.py --days 7

  forecast:month:
    desc: Payment forecast for next 30 days
    cmds:
      - python payment_forecast.py --days 30
```

### Anti-Patterns to Avoid
- **Don't re-implement balance fetching:** All balance logic exists in coverage_report.py and xero_balances.py. Import, don't copy.
- **Don't modify coverage_report.py behavior:** It works. Import its functions, don't refactor it.
- **Don't use payments.yaml `amount` for credit cards:** Per D-04, credit card payment amounts come from live Monarch balances, not the static YAML field (which may be zero or stale).
- **Don't produce partial forecasts:** Per D-05, if ANY payment lacks a funding_account, abort entirely. No partial output.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Date recurrence (next occurrence of day X) | Manual month-rollover with try/except | `dateutil.rrule(MONTHLY, bymonthday=X)` | Existing manual logic in coverage_report.py is already buggy for edge cases (short months, year boundaries) [VERIFIED: codebase lines 69-89] |
| Table alignment | Manual f-string width calculation | `tabulate` library | Variable-width account names (e.g., "360 Checking (Recurring ACH)" vs "BILT") make manual padding fragile |
| Balance fetching | Custom HTTP calls to Monarch/Xero | Existing `fetch_monarch_balances()` + `fetch_xero_balances()` | Already implemented, tested, handles auth/retry |
| YAML config loading | Custom file parsing | Existing `load_payments_yaml()` | Already implemented with correct path resolution |

## Common Pitfalls

### Pitfall 1: Credit Card Balances Are Negative in Monarch
**What goes wrong:** Monarch reports credit card balances as negative numbers (what you owe). Using the raw value as a payment amount produces negative deductions.
**Why it happens:** Financial APIs report credit card debt as negative balance.
**How to avoid:** Use `abs(balance)` when converting a credit card's Monarch balance to a payment amount. The existing code in zoho_calendar_payments.py line 297 already does this: `p["amount"] = abs(balance)`.
**Warning signs:** Negative amounts in forecast output; shortfall detection incorrectly shows surplus.

### Pitfall 2: Payments with funding_account: null
**What goes wrong:** 12 of 20 payments in payments.yaml currently have `funding_account: null`. Per D-05, the forecast MUST abort if any payment lacks a funding_account.
**Why it happens:** These payments were seeded from Google Sheets and haven't been assigned funding accounts yet.
**How to avoid:** The validation step (D-05) handles this correctly by design. But be aware that the tool will NOT produce output until all payments.yaml entries have funding accounts. This is intentional -- it forces data hygiene.
**Warning signs:** Tool always exits with error on current data.

### Pitfall 3: get_upcoming_payments() Month Boundary Bug
**What goes wrong:** The existing `get_upcoming_payments()` in coverage_report.py has fragile month-rollover logic that can skip payments or produce wrong dates for day-of-month values > 28.
**Why it happens:** Manual `datetime(year, month, dom)` construction with try/except for ValueError doesn't correctly handle all edge cases.
**How to avoid:** Use `dateutil.rrule` in payment_forecast.py's own date logic. Don't rely on `get_upcoming_payments()` from coverage_report.py for the forecast -- write a new function using rrule.
**Warning signs:** Missing payments in February; incorrect dates near month boundaries.

### Pitfall 4: Async/Sync Mixing
**What goes wrong:** `fetch_monarch_balances()` is async; `fetch_xero_balances()` is sync. Calling sync from async context or async from sync context causes runtime errors.
**Why it happens:** Monarch Money library is async-only; Xero SDK is sync.
**How to avoid:** Follow the exact pattern from coverage_report.py: `async def main()` with `await fetch_monarch_balances()` and direct call to `fetch_xero_balances()`. Run via `asyncio.run(main())`.
**Warning signs:** `RuntimeError: cannot be called from a running event loop` or coroutine never awaited warnings.

### Pitfall 5: Exit Code Semantics
**What goes wrong:** Returning wrong exit code breaks downstream automation (Phase 3 alerts).
**Why it happens:** Mixing up which severity maps to which code.
**How to avoid:** Clear constants: `EXIT_OK = 0`, `EXIT_WARNING = 1`, `EXIT_ERROR = 2`. Track worst severity seen during the run and exit with that code.
**Warning signs:** Scripted checks (cron, Phase 3) triggering incorrectly.

### Pitfall 6: min_balance Default Handling
**What goes wrong:** Accounts without `min_balance` in payments.yaml would cause KeyError or NoneType errors.
**Why it happens:** Existing accounts in payments.yaml don't have this field yet.
**How to avoid:** Always use `account.get("min_balance", 0)` -- default to 0 means only negative balance triggers a warning.
**Warning signs:** KeyError exceptions when processing accounts.

## Code Examples

### Forecast Calculation Core

```python
# Verified pattern derived from coverage_report.py print_report logic [VERIFIED: codebase]
from dateutil.rrule import rrule, MONTHLY
from datetime import datetime, timedelta

def build_forecast(config, monarch_balances, xero_balances, days):
    """Build per-account forecast with shortfall detection.

    Returns: {
        "accounts": [
            {
                "id": str,
                "name": str,
                "current_balance": float,
                "balance_source": str,
                "payments": [{"name": str, "amount": float, "due_date": datetime}],
                "projected_balance": float,
                "min_balance": float,
                "severity": "ok" | "warning" | "error",
            }
        ],
        "summary": {
            "total_outgoing": float,
            "total_available": float,
            "net_position": float,
        }
    }
    """
    accounts_by_id = {a["id"]: a for a in config.get("accounts", [])}
    payments = config.get("payments", [])
    today = datetime.now()
    horizon = today + timedelta(days=days)

    # Group payments by funding account
    payments_by_account = {}
    for p in payments:
        fa = p["funding_account"]  # D-05 guarantees this exists
        payments_by_account.setdefault(fa, []).append(p)

    results = []
    for acct_id, acct_payments in payments_by_account.items():
        account = accounts_by_id.get(acct_id)
        if not account:
            continue

        balance, source = resolve_balance(account, monarch_balances, xero_balances)
        if balance is None:
            balance = 0  # or handle per discretion area
            source = "unknown"

        # Calculate upcoming payment amounts
        upcoming = []
        for p in acct_payments:
            dom = p.get("day_of_month")
            if dom is None:
                continue

            # Use rrule for correct date math
            dates = list(rrule(MONTHLY, bymonthday=dom, dtstart=today, until=horizon))
            dates = [d for d in dates if d > today]

            amount = p.get("amount", 0)
            # D-04: credit card payments use live Monarch balance
            # (resolve variable amounts here)

            for due_date in dates:
                upcoming.append({
                    "name": p["name"],
                    "amount": amount,
                    "due_date": due_date,
                })

        upcoming.sort(key=lambda x: x["due_date"])
        total_outgoing = sum(u["amount"] for u in upcoming)
        projected = balance - total_outgoing
        min_bal = account.get("min_balance", 0)

        if projected < 0:
            severity = "error"
        elif projected < min_bal:
            severity = "warning"
        else:
            severity = "ok"

        results.append({
            "id": acct_id,
            "name": account.get("name", acct_id),
            "current_balance": balance,
            "balance_source": source,
            "payments": upcoming,
            "projected_balance": projected,
            "min_balance": min_bal,
            "severity": severity,
        })

    total_outgoing = sum(sum(u["amount"] for u in r["payments"]) for r in results)
    total_available = sum(r["current_balance"] for r in results if r["current_balance"] > 0)

    return {
        "accounts": results,
        "summary": {
            "total_outgoing": total_outgoing,
            "total_available": total_available,
            "net_position": total_available - total_outgoing,
        }
    }
```

### payments.yaml min_balance Addition

```yaml
# Source: D-11 decision -- add min_balance to funding accounts [VERIFIED: CONTEXT.md]
accounts:
  - id: mercury-personal-6343
    name: "Mercury Personal Checking"
    # ... existing fields ...
    min_balance: 500  # WARNING if projected balance drops below this

  - id: cap1-recurring-4354
    name: "360 Checking (Recurring ACH)"
    # ... existing fields ...
    min_balance: 200

  # Accounts without min_balance default to 0 (only negative triggers warning)
```

### Exit Code Pattern

```python
# D-12: Exit codes [VERIFIED: CONTEXT.md]
EXIT_OK = 0       # All clear
EXIT_WARNING = 1  # Below min_balance but not negative
EXIT_ERROR = 2    # Negative projected balance

def determine_exit_code(forecast):
    """Return worst severity exit code."""
    worst = EXIT_OK
    for acct in forecast["accounts"]:
        if acct["severity"] == "error":
            return EXIT_ERROR  # Can't get worse
        elif acct["severity"] == "warning":
            worst = EXIT_WARNING
    return worst
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| tabulate 0.9.0 | tabulate 0.10.0 | Recent | Prior project research referenced 0.9.0; 0.10.0 is current [VERIFIED: pip3 index versions] |
| Manual month-rollover in coverage_report.py | dateutil.rrule | Phase 2 introduces | Fixes known edge-case bugs in date calculation |

**Deprecated/outdated:**
- Prior stack research mentioned `tabulate>=0.9.0` -- update to `>=0.10.0` [VERIFIED: pip3 index versions tabulate]

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | ANSI escape codes work in user's terminal | Pattern 4 (Color) | Colors display as garbled text; mitigated by TTY detection + disable |
| A2 | All 12 null funding_account payments will be filled before Phase 2 execution | Pitfall 2 | Tool aborts on every run until data is complete; this is by design per D-05 |
| A3 | tabulate `tablefmt="simple"` provides adequate formatting for forecast output | Standard Stack | Output may need a different tablefmt; trivial to change |

## Open Questions (RESOLVED)

1. **Variable amount resolution for credit cards vs. payments.yaml**
   - What we know: D-04 says credit card payments use live Monarch balance. payments.yaml has static amounts (some zero, some stale).
   - What's unclear: The mapping from payment entry in payments.yaml to the credit card account whose balance should be used. Some payments like "BoA Platinum Plus 1 (min)" map to account `boa-plat1-5153` but there's no explicit field linking them. Possible approaches: use a naming convention, add a `source_account` field, or match by `zoho_match`.
   - Recommendation: Add a `credit_card_account` field to credit card payment entries in payments.yaml that references the credit card account ID. This makes the mapping explicit and unambiguous. Example: `credit_card_account: boa-plat1-5153`.

2. **Graceful degradation when balance fetch fails (discretion area)**
   - What we know: Monarch and Xero fetches can fail (network, auth, token expired).
   - What's unclear: Whether to show "balance unknown" and continue, or abort entirely.
   - Recommendation: Graceful degradation -- show the forecast with "balance: unknown" for failed accounts, mark those accounts as severity "error" (can't verify solvency without a balance), and note in the summary. This is more useful than a complete abort when one API is temporarily down.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | Runtime | Yes | 3.12.2 | -- |
| python-dateutil | Date recurrence | Yes | 2.9.0.post0 | -- |
| tabulate | Table formatting | No (not installed) | -- | Install via pip |
| monarchmoney | Monarch balances | Yes | 0.1.15 | -- |
| xero-python | Xero balances | Yes | installed | -- |
| pyyaml | YAML parsing | Yes | installed | -- |
| task (go-task) | Taskfile runner | Unknown | -- | Run `python payment_forecast.py` directly |

**Missing dependencies with no fallback:**
- None (tabulate install is trivial)

**Missing dependencies with fallback:**
- `tabulate`: Not installed. Install with `pip install tabulate>=0.10.0` and add to requirements.txt.

## Security Domain

Not applicable for this phase. The tool reads balances from already-authenticated APIs (Monarch token in .env, Xero token in file). No new authentication, no new secrets, no user input beyond CLI args (--days integer, --timeline flag). No network-facing surface.

## Sources

### Primary (HIGH confidence)
- Codebase: `coverage_report.py` -- balance fetching patterns, YAML loading, payment scheduling [VERIFIED: direct file read]
- Codebase: `zoho_calendar_payments.py` -- import pattern, variable amount resolution [VERIFIED: direct file read]
- Codebase: `payments.yaml` -- account definitions, payment entries, current null funding_accounts [VERIFIED: direct file read]
- Codebase: `Taskfile.yml` -- existing task entry pattern [VERIFIED: direct file read]
- pip3: tabulate 0.10.0 is latest [VERIFIED: pip3 index versions tabulate]
- pip3: python-dateutil 2.9.0.post0 installed [VERIFIED: pip3 show python-dateutil]

### Secondary (MEDIUM confidence)
- `.planning/research/STACK.md` -- prior project-level stack research [VERIFIED: direct file read]
- dateutil rrule documentation [CITED: dateutil.readthedocs.io/en/stable/rrule.html]

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries verified installed or available via pip; versions confirmed against registry
- Architecture: HIGH -- all patterns derived from existing codebase with zero speculation; every function referenced exists and was read
- Pitfalls: HIGH -- pitfalls identified from actual code review (credit card negative balance in zoho_calendar_payments.py line 297, month boundary bug in coverage_report.py lines 69-89, 12 null funding_accounts in payments.yaml)

**Research date:** 2026-05-04
**Valid until:** 2026-06-04 (stable -- no fast-moving dependencies)
