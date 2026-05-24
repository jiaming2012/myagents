# Phase 1: Calendar Parsing + Bill Mapping - Pattern Map

**Mapped:** 2026-05-03
**Files analyzed:** 3 modified files
**Analogs found:** 3 / 3

## File Classification

| Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---------------|------|-----------|----------------|---------------|
| `zoho_calendar_payments.py` | script | request-response + transform | `zoho_calendar_payments.py` (self) + `coverage_report.py` | exact |
| `payments.yaml` | config | static | `payments.yaml` (self) | exact |
| `Taskfile.yml` | config | static | `Taskfile.yml` (self) | exact |

## Pattern Assignments

### `zoho_calendar_payments.py` (script, request-response + transform)

This file is being **extended** with new functions. The analog is itself (for structure, imports, error handling) and `coverage_report.py` (for balance-fetching and YAML-loading patterns).

**Imports pattern** (`zoho_calendar_payments.py` lines 1-38):
```python
#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: 'requests' package is required. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
```
New code must add these imports at the top:
- `import re` (for title parsing regex)
- `import yaml` (for payments.yaml loading)
- `import asyncio` (for Monarch async calls)
- Import balance functions from `coverage_report.py`

**Import pattern for Monarch reuse** (`coverage_report.py` lines 35-41):
```python
try:
    from monarchmoney import MonarchMoney
    from monarchmoney.monarchmoney import MonarchMoneyEndpoints
    MonarchMoneyEndpoints.BASE_URL = "https://api.monarch.com"
except ImportError:
    MonarchMoney = None
```

**Constants pattern** (`zoho_calendar_payments.py` lines 41-49):
```python
ZOHO_TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"
ZOHO_CALENDAR_API_BASE = "https://calendar.zoho.com/api/v1"

# Cache TTLs (seconds)
TOKEN_TTL = 50 * 60       # 50 minutes (tokens expire at 60)
EVENT_LIST_TTL = 15 * 60  # 15 minutes
EVENT_DETAIL_TTL = 24 * 60 * 60  # 24 hours

CACHE_DIR = Path(__file__).parent / ".cache"
```
New constants to add in same style: `TITLE_PATTERN`, `PAYMENTS_FILE`.

**Required env var pattern** (`zoho_calendar_payments.py` lines 83-94):
```python
def get_required_env(name):
    """Get a required environment variable or exit with a clear error."""
    value = os.environ.get(name)
    if not value:
        print(
            f"Error: Missing required environment variable: {name}\n"
            f"Set it in your shell or in a .env file.\n"
            f"See script docstring for all required variables.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value
```

**API GET with error handling pattern** (`zoho_calendar_payments.py` lines 140-171):
```python
def _api_get(url, headers, params=None):
    """Make a GET request to Zoho API with standard error handling."""
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.RequestException as e:
        print(f"Error: Failed to connect to Zoho Calendar API: {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code == 401:
        print(
            "Error: Authentication failed (401). Your refresh token may be expired.\n"
            "Generate a new one at https://api-console.zoho.com/",
            file=sys.stderr,
        )
        sys.exit(1)

    if resp.status_code != 200:
        print(
            f"Error: Zoho Calendar API returned HTTP {resp.status_code}\n"
            f"Response: {resp.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    return resp.json()
```
Use this same pattern for the new `update_event_title()` PUT call (with `requests.put` instead).

**Event fetching with detail enrichment** (`zoho_calendar_payments.py` lines 193-231):
```python
def fetch_events(access_token, calendar_id, start_date, end_date, use_cache=True):
    """Fetch events from a Zoho Calendar within a date range, including descriptions."""
    range_key = f"list:{calendar_id}:{start_date.strftime('%Y%m%d')}:{end_date.strftime('%Y%m%d')}"
    if use_cache:
        cached = cache_get(range_key, EVENT_LIST_TTL)
        if cached:
            return cached

    url = f"{ZOHO_CALENDAR_API_BASE}/calendars/{calendar_id}/events"
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    params = {
        "range": json.dumps({
            "start": start_date.strftime("%Y%m%dT000000Z"),
            "end": end_date.strftime("%Y%m%dT235959Z"),
        }),
    }

    data = _api_get(url, headers, params)
    events = data.get("events", [])

    # List endpoint omits descriptions -- fetch each event's detail
    detailed = []
    for event in events:
        uid = event.get("uid", "")
        if uid:
            detail = fetch_event_detail(access_token, calendar_id, uid, use_cache)
            if detail:
                merged = detail.copy()
                merged["dateandtime"] = event.get("dateandtime", merged.get("dateandtime", {}))
                detailed.append(merged)
            else:
                detailed.append(event)
        else:
            detailed.append(event)

    cache_set(range_key, detailed)
    return detailed
```
This is the upstream data source. New parsing functions consume `detailed` events from here.

**YAML loading pattern** (`coverage_report.py` lines 42-48):
```python
PAYMENTS_FILE = Path(__file__).parent / "payments.yaml"

def load_payments_yaml():
    """Load the payment registry."""
    with open(PAYMENTS_FILE) as f:
        return yaml.safe_load(f)
```

**Balance fetching (Monarch, async)** (`coverage_report.py` lines 95-116):
```python
async def fetch_monarch_balances():
    """Fetch all account balances from Monarch Money."""
    if MonarchMoney is None:
        print("Warning: monarchmoney not installed, skipping balance lookup", file=sys.stderr)
        return {}

    token = os.environ.get("MONARCH_TOKEN")
    if not token:
        print("Warning: MONARCH_TOKEN not set, skipping balance lookup", file=sys.stderr)
        return {}

    mm = MonarchMoney(token=token)
    data = await mm.get_accounts()

    balances = {}
    for acct in data.get("accounts", []):
        name = acct.get("displayName", "")
        balances[name] = {
            "balance": acct.get("currentBalance", 0),
            "type": acct.get("type", {}).get("name", ""),
        }
    return balances
```

**Balance fetching (Mercury, sync)** (`coverage_report.py` lines 119-145):
```python
def fetch_mercury_balances():
    """Fetch balances from Mercury API for both personal and business."""
    balances = {}

    for key_name, env_var in [("personal", "MERCURY_PERSONAL_API_KEY"), ("business", "MERCURY_BUSINESS_API_KEY")]:
        api_key = os.environ.get(env_var)
        if not api_key:
            continue

        try:
            resp = requests.get(
                "https://api.mercury.com/api/v1/accounts",
                headers={"Authorization": f"Bearer {api_key}", "accept": "application/json"},
                timeout=15,
            )
            if resp.status_code == 200:
                for acct in resp.json().get("accounts", []):
                    if acct.get("status") == "active":
                        balances[acct["id"]] = {
                            "balance": acct.get("currentBalance", 0),
                            "name": acct.get("nickname") or acct.get("name", ""),
                            "key": key_name,
                        }
        except requests.RequestException:
            print(f"Warning: failed to fetch Mercury {key_name} balances", file=sys.stderr)

    return balances
```

**Balance resolution pattern** (`coverage_report.py` lines 148-167):
```python
def resolve_balance(account, monarch_balances, mercury_balances):
    """Get the current balance for an account from available sources."""
    mercury_id = account.get("mercury_id")
    if mercury_id and mercury_id in mercury_balances:
        return mercury_balances[mercury_id]["balance"], "mercury"

    monarch_match = account.get("monarch_match")
    if monarch_match and monarch_match in monarch_balances:
        return monarch_balances[monarch_match]["balance"], "monarch"

    last4 = account.get("last4")
    if last4:
        for name, data in monarch_balances.items():
            if last4 in name:
                return data["balance"], "monarch"

    return None, None
```

**Credit card negative balance handling** (`coverage_report.py` lines 220-222):
```python
if account.get("type") == "credit":
    # Credit cards: balance is what you owe, payments come from linked bank
    print(f"      Balance:  ${abs(balance):,.2f} owed")
```
Use `abs(balance)` when converting Monarch credit card balance to variable payment amount.

**CLI display pattern -- grouped output with separators** (`coverage_report.py` lines 188-196):
```python
print(f"\n{'=' * 70}")
print(f"  COVERAGE REPORT -- {datetime.now().strftime('%a %b %d, %Y')}")
print(f"{'=' * 70}")

# Per-group:
print(f"\n  {'─' * 60}")
print(f"  Next {window} Days")
print(f"  {'─' * 60}")
```

**argparse pattern** (`zoho_calendar_payments.py` lines 320-345):
```python
def main():
    parser = argparse.ArgumentParser(
        description="Fetch and display upcoming events from a Zoho Calendar 'Payments' calendar.",
        epilog=(
            "Required environment variables:\n"
            "  ZOHO_CLIENT_ID       OAuth2 client ID\n"
            ...
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to look ahead (default: 7)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass cache and fetch fresh data",
    )
    args = parser.parse_args()
```
New `--update-calendar` flag follows same `store_true` pattern.

**main() entry point pattern** (`zoho_calendar_payments.py` lines 360-377):
```python
    # Load configuration
    client_id = get_required_env("ZOHO_CLIENT_ID")
    ...

    # Authenticate
    print("Authenticating with Zoho...", end=" ", flush=True)
    access_token = get_access_token(client_id, client_secret, refresh_token, use_cache)
    print("OK")

    # Fetch events
    ...
    print("OK")

    # Display
    display_events(events, args.days)


if __name__ == "__main__":
    main()
```
Extended main() will add: load payments.yaml, parse events, resolve accounts, fetch balances for variable payments, display grouped output.

---

### `payments.yaml` (config, static)

**Analog:** `payments.yaml` (self -- extending with new field)

**Existing account structure** (`payments.yaml` lines 19-30):
```yaml
accounts:
  - id: mercury-personal-6343
    name: "Mercury Personal Checking"
    institution: "Mercury"
    last4: "6343"
    type: depository
    category: personal
    monarch_match: null
    mercury_id: "b0ce95b6-1072-11f1-bbb7-8f5dcd6b8907"
    mercury_key: personal
    role: income_hub
```
New `nicknames` list field added to each account, following the same YAML indentation (2-space) and quoting pattern.

**Existing payment structure** (`payments.yaml` lines 340-349):
```yaml
payments:
  - name: "Quickbooks"
    amount: 38.00
    day_of_month: 11
    funding_account: chase-ink-7667
    autopay: true
    autopay_type: null
    category: business
    zoho_match: "Quickbooks"
```

---

### `Taskfile.yml` (config, static)

**Analog:** `Taskfile.yml` (self -- adding new task entry)

**Existing task pattern** (`Taskfile.yml` lines 9-12):
```yaml
  payments:
    desc: List upcoming payment events (default 7 days)
    cmds:
      - python zoho_calendar_payments.py {{.CLI_ARGS}}
```
New `forecast` (or similar) task follows same structure: `desc` + `cmds` with `{{.CLI_ARGS}}` passthrough.

---

## Shared Patterns

### Import Guard with Graceful Error
**Source:** `zoho_calendar_payments.py` lines 27-38, `coverage_report.py` lines 26-33
**Apply to:** Any new import added to `zoho_calendar_payments.py`
```python
try:
    import requests
except ImportError:
    print("Error: 'requests' package is required. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
```

### Progress Messages
**Source:** `zoho_calendar_payments.py` lines 360-369, `coverage_report.py` lines 283-286
**Apply to:** All new long-running operations (balance fetching, parsing)
```python
print("Authenticating with Zoho...", end=" ", flush=True)
access_token = get_access_token(...)
print("OK")

print("Fetching balances...", end=" ", flush=True)
monarch_balances = await fetch_monarch_balances()
print("OK")
```

### Error Output to stderr
**Source:** Every script in the codebase
**Apply to:** All error/warning messages
```python
print(f"Error: ...", file=sys.stderr)
print(f"Warning: ...", file=sys.stderr)
```

### File-Based Caching
**Source:** `zoho_calendar_payments.py` lines 52-78
**Apply to:** Any new API responses that should be cached
```python
def cache_get(key, ttl):
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - data.get("ts", 0) > ttl:
        return None
    return data.get("value")

def cache_set(key, value):
    CACHE_DIR.mkdir(exist_ok=True)
    path = _cache_path(key)
    path.write_text(json.dumps({"ts": time.time(), "value": value}))
```

### YAML Config Loading
**Source:** `coverage_report.py` lines 42-48
**Apply to:** Loading payments.yaml in `zoho_calendar_payments.py`
```python
PAYMENTS_FILE = Path(__file__).parent / "payments.yaml"

def load_payments_yaml():
    """Load the payment registry."""
    with open(PAYMENTS_FILE) as f:
        return yaml.safe_load(f)
```

### Monarch BASE_URL Patch
**Source:** `coverage_report.py` lines 36-38, `monarch_balances.py` lines 32-33
**Apply to:** Any file importing MonarchMoney
```python
from monarchmoney.monarchmoney import MonarchMoneyEndpoints
MonarchMoneyEndpoints.BASE_URL = "https://api.monarch.com"
```

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| (none) | -- | -- | All files are extensions of existing code with exact analogs |

**Note:** The new functions being added (`parse_event_title`, `parse_event_notes`, `resolve_account`, `build_nickname_lookup`, `update_event_title`, `display_grouped_payments`, `process_events`) are new logic but follow established patterns from the codebase. The collect-and-report error handling pattern (D-03) is new to this codebase -- use the pattern from RESEARCH.md section "Pattern 1: Collect-and-Report Error Handling" as the reference.

## Metadata

**Analog search scope:** `/Users/jamal/projects/myagents/banking/` (project root)
**Files scanned:** 7 (zoho_calendar_payments.py, coverage_report.py, monarch_balances.py, payments.yaml, Taskfile.yml, test_integration.py, requirements.txt)
**Pattern extraction date:** 2026-05-03
