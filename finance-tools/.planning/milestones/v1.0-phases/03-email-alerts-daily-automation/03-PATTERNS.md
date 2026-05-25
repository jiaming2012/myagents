# Phase 3: Email Alerts + Daily Automation - Pattern Map

**Mapped:** 2026-05-04
**Files analyzed:** 6 (1 new, 5 modified)
**Analogs found:** 5 / 6

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `alert_email.py` (NEW) | service | request-response | `payment_forecast.py` | role-match |
| `payment_forecast.py` (MODIFY) | controller | request-response | self (extend) | exact |
| `payments.yaml` (MODIFY) | config | N/A | self (extend) | exact |
| `.env.example` (MODIFY) | config | N/A | self (extend) | exact |
| `.gitignore` (MODIFY) | config | N/A | self (extend) | exact |
| `Taskfile.yml` (MODIFY) | config | N/A | self (extend) | exact |

## Pattern Assignments

### `alert_email.py` (NEW -- service, request-response)

**Analog:** `payment_forecast.py` + `coverage_report.py`

This is the primary new file. It handles email construction (HTML), SMTP sending, content-hash dedup, and alert threshold checking. All stdlib -- no new dependencies.

**Imports pattern** (from `coverage_report.py` lines 14-19 -- dotenv loading):
```python
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
```

**Env var loading pattern** (from `coverage_report.py` lines 100-109 -- guarded env access):
```python
token = os.environ.get("MONARCH_TOKEN")
if not token:
    print("Warning: MONARCH_TOKEN not set, skipping balance lookup", file=sys.stderr)
    return {}
```

**Config file path pattern** (from `coverage_report.py` line 47):
```python
PAYMENTS_FILE = Path(__file__).parent / "payments.yaml"
```

**Error output pattern** (from `payment_forecast.py` lines 253-254 -- stderr warnings):
```python
print(f"Warning: Could not fetch balance for {account.get('name', acct_id)}, "
      f"defaulting to 0.0", file=sys.stderr)
```

**Forecast data structure** (from `payment_forecast.py` lines 209-228 -- build_forecast() return type):
```python
# alert_email.py will consume this structure from build_forecast():
{
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
```

**Summary formatting pattern** (from `payment_forecast.py` lines 431-447 -- print_summary):
```python
def print_summary(forecast):
    """Print summary line with totals (D-09)."""
    summary = forecast["summary"]
    print(f"    Total Outgoing:   ${summary['total_outgoing']:>12,.2f}")
    print(f"    Total Available:  ${summary['total_available']:>12,.2f}")
    net = summary["net_position"]
    # ... color formatting
```

**Grouped account iteration pattern** (from `payment_forecast.py` lines 330-363 -- print_grouped_view):
```python
for i, acct in enumerate(forecast["accounts"]):
    acct_label = acct["name"]
    # ... per-account rendering with severity-based coloring
    if acct["severity"] == "error":
        # bold red
    elif acct["severity"] == "warning":
        # yellow
    else:
        # green
```

---

### `payment_forecast.py` (MODIFY -- controller, request-response)

**Analog:** self -- extend existing patterns

**Argparse extension point** (lines 469-480 -- add new flags after existing ones):
```python
parser = argparse.ArgumentParser(
    description="Payment forecast -- project per-account balances and detect shortfalls"
)
parser.add_argument(
    "--days", type=int, default=30,
    help="Forecast horizon in days (default: 30)"
)
parser.add_argument(
    "--timeline", action="store_true",
    help="Show chronological timeline instead of grouped view"
)
```

**ImportError guard pattern** (lines 22-43 -- used for optional dependencies):
```python
try:
    from xero_balances import fetch_xero_balances
except ImportError:
    fetch_xero_balances = None
```

**Main flow dispatch pattern** (lines 520-528 -- add email modes alongside existing display modes):
```python
# Display output
if args.timeline:
    print_timeline_view(forecast, args.days)
else:
    print_grouped_view(forecast, args.days)

print_summary(forecast)
sys.exit(determine_exit_code(forecast))
```

**Exit code pattern** (lines 62-65 + 450-464):
```python
EXIT_OK = 0
EXIT_WARNING = 1
EXIT_ERROR = 2

def determine_exit_code(forecast):
    worst = EXIT_OK
    for acct in forecast["accounts"]:
        if acct["severity"] == "error":
            return EXIT_ERROR
        elif acct["severity"] == "warning":
            worst = EXIT_WARNING
    return worst
```

---

### `payments.yaml` (MODIFY -- config)

**Analog:** self -- extend existing account fields

**Funding account field pattern** (lines 20-31 -- existing field structure on accounts):
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
    min_balance: 500
    # ADD: alert_on: error | warning | none (default: error)
```

Only depository accounts with `min_balance` need `alert_on`. Accounts without `min_balance` still default to `error` (alert on negative projected balance).

---

### `Taskfile.yml` (MODIFY -- config)

**Analog:** self -- existing task definition pattern

**Task shortcut pattern** (lines 44-57 -- forecast task group with CLI_ARGS passthrough):
```yaml
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

New tasks follow the same pattern:
- `forecast:email` -> `python payment_forecast.py --email-summary {{.CLI_ARGS}}`
- `forecast:email-weekly` -> `python payment_forecast.py --email-summary --days 7`

---

### `.env.example` (MODIFY -- config)

**Analog:** self -- existing env var documentation pattern

**Current pattern** (lines 1-7 -- simple KEY= format):
```
ZOHO_CLIENT_ID=
ZOHO_CLIENT_SECRET=
ZOHO_REFRESH_TOKEN=
ZOHO_CALENDAR_ID=
MONARCH_TOKEN=
MERCURY_BUSINESS_API_KEY=
MERCURY_PERSONAL_API_KEY=
```

Add SMTP section with same format:
```
# Gmail SMTP (use App Password, not regular password)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
ALERT_EMAIL=
```

---

### `.gitignore` (MODIFY -- config)

**Analog:** self -- existing ignore pattern

**Current entries** (lines 1-7):
```
.env
.cache/
.mm/
.xero_token.json
__pycache__/
*.pyc
.pytest_cache/
```

Add `.alert_state.json` and `forecast_preview.html` following the same flat-list format.

---

## Shared Patterns

### Dotenv Loading
**Source:** `coverage_report.py` lines 29-33
**Apply to:** `alert_email.py` (SMTP credential loading)
```python
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
```

### Stderr Error/Warning Output
**Source:** `payment_forecast.py` lines 25-26, 253-254, 492-498
**Apply to:** `alert_email.py` (SMTP errors, missing config)
```python
print("Error: 'tabulate' package is required. Run: pip install tabulate", file=sys.stderr)
sys.exit(1)

print(f"Warning: Could not fetch balance for {account.get('name', acct_id)}, "
      f"defaulting to 0.0", file=sys.stderr)

print("ERROR: Cannot produce forecast -- these payments have no funding_account assigned:",
      file=sys.stderr)
```

### ImportError Guard for Optional Dependencies
**Source:** `payment_forecast.py` lines 42-43
**Apply to:** `payment_forecast.py` import of `alert_email` module
```python
try:
    from xero_balances import fetch_xero_balances
except ImportError:
    fetch_xero_balances = None
```

### YAML Config Loading
**Source:** `coverage_report.py` lines 47-53
**Apply to:** `alert_email.py` if it needs to load payments.yaml for `alert_on` config
```python
PAYMENTS_FILE = Path(__file__).parent / "payments.yaml"

def load_payments_yaml():
    """Load the payment registry."""
    with open(PAYMENTS_FILE) as f:
        return yaml.safe_load(f)
```

### Currency Formatting
**Source:** `payment_forecast.py` lines 345, 353 (consistent `${value:,.2f}` pattern)
**Apply to:** `alert_email.py` HTML email body construction
```python
f"${p['amount']:,.2f}"
f"${acct['projected_balance']:,.2f}"
```

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `alert_email.py` (SMTP sending) | service | request-response | No existing SMTP/email code in codebase. Use RESEARCH.md Pattern 1 (smtplib + MIMEMultipart) |
| `alert_email.py` (content hash dedup) | service | file-I/O | No dedup/state-file pattern exists. Use RESEARCH.md Pattern 2 (hashlib + JSON state file) |
| `alert_email.py` (HTML email template) | service | transform | No HTML generation in codebase. Use RESEARCH.md Pattern 3 (inline CSS, table layout) |

**Note:** While `alert_email.py` has no direct analog for its core email/dedup functionality, its structure (module-level functions, dotenv config, Path-based file references, stderr error output) should follow the patterns extracted above from `payment_forecast.py` and `coverage_report.py`.

## Metadata

**Analog search scope:** Project root (`/Users/jamal/projects/myagents/banking/`)
**Files scanned:** 6 source files (all Python scripts + config files in project)
**Pattern extraction date:** 2026-05-04
