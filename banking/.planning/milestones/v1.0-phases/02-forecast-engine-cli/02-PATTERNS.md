# Phase 2: Forecast Engine + CLI - Pattern Map

**Mapped:** 2026-05-04
**Files analyzed:** 4 (new/modified)
**Analogs found:** 4 / 4

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `payment_forecast.py` | controller (CLI entry point) | request-response (async balance fetch + compute + output) | `coverage_report.py` | exact |
| `payments.yaml` | config | static data | `payments.yaml` (self -- add `min_balance` field) | exact |
| `Taskfile.yml` | config | CLI dispatch | `Taskfile.yml` (self -- add forecast tasks) | exact |
| `requirements.txt` | config | dependency list | `requirements.txt` (self -- add tabulate, python-dateutil) | exact |

## Pattern Assignments

### `payment_forecast.py` (controller, request-response)

**Analog:** `coverage_report.py` (primary), `zoho_calendar_payments.py` (secondary -- import pattern)

**Imports pattern** (`coverage_report.py` lines 1-46):
```python
#!/usr/bin/env python3
"""..."""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

try:
    import requests
except ImportError:
    print("Error: 'requests' package is required.", file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from monarchmoney import MonarchMoney
    from monarchmoney.monarchmoney import MonarchMoneyEndpoints
    MonarchMoneyEndpoints.BASE_URL = "https://api.monarch.com"
except ImportError:
    MonarchMoney = None

try:
    from xero_balances import fetch_xero_balances
except ImportError:
    fetch_xero_balances = None
```

**Cross-module import pattern** (`zoho_calendar_payments.py` lines 48-57):
```python
try:
    from coverage_report import fetch_monarch_balances, resolve_balance
except ImportError:
    print("Error: coverage_report.py must be in the same directory", file=sys.stderr)
    sys.exit(1)

try:
    from xero_balances import fetch_xero_balances
except ImportError:
    fetch_xero_balances = None
```

**YAML loading pattern** (`coverage_report.py` lines 47-53):
```python
PAYMENTS_FILE = Path(__file__).parent / "payments.yaml"

def load_payments_yaml():
    """Load the payment registry."""
    with open(PAYMENTS_FILE) as f:
        return yaml.safe_load(f)
```

**Async main + argparse + balance fetching pattern** (`coverage_report.py` lines 252-273):
```python
async def main():
    parser = argparse.ArgumentParser(description="Weekly payment coverage report")
    parser.add_argument("--days", type=int, help="Show only one window (e.g., 7, 14, 30)")
    args = parser.parse_args()

    config = load_payments_yaml()

    print("Fetching balances...", end=" ", flush=True)
    monarch_balances = await fetch_monarch_balances()
    xero_balances = fetch_xero_balances() if fetch_xero_balances else {}
    print("OK")

    # ... processing ...

if __name__ == "__main__":
    asyncio.run(main())
```

**Group-by-funding-account + balance resolve pattern** (`coverage_report.py` lines 146-163, 175-186):
```python
def print_report(config, monarch_balances, xero_balances, windows):
    accounts_by_id = {a["id"]: a for a in config.get("accounts", [])}
    payments = config.get("payments", [])

    # Group payments by funding account
    payments_by_account = {}
    unassigned = []
    for p in payments:
        fa = p.get("funding_account")
        if fa:
            payments_by_account.setdefault(fa, []).append(p)
        else:
            unassigned.append(p)

    # Per-account processing
    for acct_id, acct_payments in sorted(payments_by_account.items()):
        account = accounts_by_id.get(acct_id)
        if not account:
            continue

        upcoming = get_upcoming_payments(acct_payments, window)
        total_due = sum(p.get("amount", 0) for p in upcoming)
        balance, source = resolve_balance(account, monarch_balances, xero_balances)
```

**resolve_balance function** (`coverage_report.py` lines 124-143):
```python
def resolve_balance(account, monarch_balances, xero_balances):
    """Get the current balance for an account from available sources."""
    # Try Xero first (for business accounts with xero_account_id)
    xero_id = account.get("xero_account_id")
    if xero_id and xero_balances and xero_id in xero_balances:
        return xero_balances[xero_id]["balance"], "xero"

    # Try Monarch
    monarch_match = account.get("monarch_match")
    if monarch_match and monarch_match in monarch_balances:
        return monarch_balances[monarch_match]["balance"], "monarch"

    # Partial match on Monarch (displayName contains last4)
    last4 = account.get("last4")
    if last4:
        for name, data in monarch_balances.items():
            if last4 in name:
                return data["balance"], "monarch"

    return None, None
```

**Variable amount resolution (credit card abs balance)** (`zoho_calendar_payments.py` lines 268-300):
```python
async def resolve_variable_amounts(payments, config):
    """For variable payments, replace title amount with real Monarch balance."""
    variable = [p for p in payments if p.get("is_variable") and p.get("source_account")]
    if not variable:
        return payments

    monarch_balances = await fetch_monarch_balances()
    xero_balances = fetch_xero_balances() if fetch_xero_balances else {}

    nickname_lookup = build_nickname_lookup(config)

    for p in variable:
        source = p["source_account"]
        try:
            acct_id = resolve_account(source, nickname_lookup)
        except ValueError:
            continue

        acct_config = next((a for a in config.get("accounts", []) if a["id"] == acct_id), None)
        if not acct_config:
            continue

        balance, _ = resolve_balance(acct_config, monarch_balances, xero_balances)
        if balance is not None:
            p["amount"] = abs(balance)  # Credit cards are negative in Monarch
            p["amount_source"] = "monarch"

    return payments
```

**Output formatting pattern** (`coverage_report.py` lines 164-249):
```python
    print(f"\n{'=' * 70}")
    print(f"  COVERAGE REPORT -- {datetime.now().strftime('%a %b %d, %Y')}")
    print(f"{'=' * 70}")

    # Per-section headers
    print(f"\n  {'---' * 20}")
    print(f"  Next {window} Days")
    print(f"  {'---' * 20}")

    # Account label with last4
    acct_label = f"{account['name']}"
    if account.get("last4"):
        acct_label += f" (..{account['last4']})"

    # Dollar formatting
    print(f"      Balance:  ${balance:,.2f}")
    print(f"      Due:      ${total_due:,.2f} ({len(upcoming)} payment{'s' if len(upcoming) != 1 else ''})")

    # Shortfall detection
    if surplus >= 0:
        print(f"      Surplus:  ${surplus:,.2f}")
    else:
        print(f"      SHORT:    -${abs(surplus):,.2f}  *** ALERT ***")

    # Summary footer
    print(f"\n{'=' * 70}")
```

**Error exit pattern** (`coverage_report.py` lines 23-27, `zoho_calendar_payments.py` lines 659-661):
```python
# Hard exit on missing dependency
print("Error: ...", file=sys.stderr)
sys.exit(1)

# Exit non-zero on errors
if errors:
    sys.exit(1)
```

---

### `payments.yaml` (config, static data)

**Analog:** Self (add `min_balance` field to funding accounts)

**Existing account structure** (`payments.yaml` lines 22-30):
```yaml
accounts:
  - id: mercury-personal-6343
    name: "Mercury Personal Checking"
    institution: "Mercury"
    last4: "6343"
    type: depository
    category: personal
    monarch_match: null
    role: income_hub
    nicknames: ["Mercury Personal", "Mercury 6343", "Mercury Checking"]
```

**Pattern for adding min_balance:** Add as a new optional field alongside existing fields. Follow the established pattern of optional fields being omitted (not set to null) when not needed, similar to how `xero_account_id` and `role` are only present on accounts that use them.

---

### `Taskfile.yml` (config, CLI dispatch)

**Analog:** Self (existing task entries)

**Task entry pattern with CLI_ARGS passthrough** (`Taskfile.yml` lines 9-12):
```yaml
  payments:
    desc: List upcoming payment events (default 7 days)
    cmds:
      - python zoho_calendar_payments.py {{.CLI_ARGS}}
```

**Task shortcut pattern (fixed args)** (`Taskfile.yml` lines 14-22):
```yaml
  payments:today:
    desc: List today's payment events
    cmds:
      - python zoho_calendar_payments.py --days 1

  payments:month:
    desc: List payment events for the next 30 days
    cmds:
      - python zoho_calendar_payments.py --days 30
```

**Report task pattern** (`Taskfile.yml` lines 34-42):
```yaml
  report:
    desc: Weekly coverage report (all windows)
    cmds:
      - python coverage_report.py {{.CLI_ARGS}}

  report:week:
    desc: Coverage report for next 7 days only
    cmds:
      - python coverage_report.py --days 7
```

---

### `requirements.txt` (config, dependency list)

**Analog:** Self

**Current pattern** (`requirements.txt` lines 1-7):
```
requests
python-dotenv
pyyaml
monarchmoney
gql<4
pytest
xero-python
```

Note: Current entries use bare package names (no version pins) except `gql<4`. New entries (`tabulate`, `python-dateutil`) should follow the same convention: bare names or minimal version constraints.

---

## Shared Patterns

### Import Guard Pattern
**Source:** `coverage_report.py` lines 23-45, `zoho_calendar_payments.py` lines 29-57
**Apply to:** `payment_forecast.py`

All scripts use `try/except ImportError` guards for dependencies. Hard dependencies exit with `sys.exit(1)` and a clear error message. Optional dependencies set the import to `None` and check at call time.

```python
# Hard dependency (required)
try:
    from coverage_report import fetch_monarch_balances, resolve_balance, load_payments_yaml
except ImportError:
    print("Error: coverage_report.py must be in the same directory", file=sys.stderr)
    sys.exit(1)

# Soft dependency (optional, graceful degradation)
try:
    from xero_balances import fetch_xero_balances
except ImportError:
    fetch_xero_balances = None
```

### Async/Sync Balance Fetching
**Source:** `coverage_report.py` lines 252-262
**Apply to:** `payment_forecast.py`

Monarch is async (`await fetch_monarch_balances()`), Xero is sync (`fetch_xero_balances()`). Both called inside `async def main()`, run via `asyncio.run(main())`.

```python
async def main():
    # ... argparse setup ...
    monarch_balances = await fetch_monarch_balances()
    xero_balances = fetch_xero_balances() if fetch_xero_balances else {}
```

### Dollar Formatting Convention
**Source:** `coverage_report.py` lines 198-208
**Apply to:** `payment_forecast.py` output

All dollar amounts use `${value:,.2f}` format. Negative values shown as `-${abs(value):,.2f}`.

### Progress Feedback Pattern
**Source:** `coverage_report.py` lines 259-262
**Apply to:** `payment_forecast.py`

```python
print("Fetching balances...", end=" ", flush=True)
# ... fetch ...
print("OK")
```

### Credit Card Balance Sign Convention
**Source:** `zoho_calendar_payments.py` line 297
**Apply to:** `payment_forecast.py` when resolving credit card payment amounts

```python
p["amount"] = abs(balance)  # Credit cards are negative in Monarch
```

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| (none) | -- | -- | All files have exact analogs in the existing codebase |

**Note on novel logic within `payment_forecast.py`:** While the file structure has an exact analog (`coverage_report.py`), two internal functions have no direct analog and should follow RESEARCH.md patterns:

1. **`dateutil.rrule` date recurrence** -- No existing code uses rrule. The existing `get_upcoming_payments()` in `coverage_report.py` (lines 56-97) uses fragile manual month-rollover logic that RESEARCH.md explicitly warns against reusing (Pitfall 3). Use `dateutil.rrule(MONTHLY, bymonthday=X)` as shown in RESEARCH.md Pattern 3.

2. **ANSI color output** -- No existing code uses terminal colors. All scripts use plain `print()`. Follow RESEARCH.md Pattern 4 (simple ANSI escape codes with TTY detection).

3. **Shortfall severity + exit codes** -- `coverage_report.py` has basic shortfall detection (lines 193-213) but no severity levels or exit codes. The two-tier severity (ERROR/WARNING) and exit code mapping (0/1/2) are new. Follow RESEARCH.md exit code pattern.

## Metadata

**Analog search scope:** `/Users/jamal/projects/myagents/banking/` (repo root -- flat structure, all scripts at top level)
**Files scanned:** 7 (coverage_report.py, zoho_calendar_payments.py, xero_balances.py, monarch_balances.py, payments.yaml, Taskfile.yml, requirements.txt)
**Pattern extraction date:** 2026-05-04
