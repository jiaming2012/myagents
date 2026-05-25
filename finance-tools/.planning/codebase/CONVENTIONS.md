# Coding Conventions

**Analysis Date:** 2026-05-03

## Naming Patterns

**Files:**
- All lowercase with underscores: `coverage_report.py`, `monarch_balances.py`, `zoho_calendar_payments.py`
- Test files prefixed with `test_`: `test_integration.py`
- Script files are executable entry points with `#!/usr/bin/env python3` shebang

**Functions:**
- Snake case: `load_payments_yaml()`, `get_upcoming_payments()`, `fetch_monarch_balances()`, `resolve_balance()`
- Async functions prefixed with `async def`: `async def fetch_monarch_balances()`, `async def get_client()`
- Private/internal helper functions prefixed with underscore: `_cache_path()`, `_api_get()`
- Verbs indicate action: `fetch_`, `get_`, `load_`, `parse_`, `format_`, `display_`, `resolve_`

**Variables:**
- Snake case for variables and parameters: `client_id`, `refresh_token`, `account_type`, `use_cache`
- Constants in UPPERCASE with underscores: `ZOHO_TOKEN_URL`, `TOKEN_TTL`, `CACHE_DIR`, `SESSION_FILE`, `PAYMENTS_FILE`
- Dictionary keys use lowercase with underscores: `current_balance`, `display_name`, `funding_account`, `day_of_month`

**Types/Classes:**
- No classes defined in this codebase — scripts use only functions and module-level constants

## Code Style

**Formatting:**
- No explicit formatter configured (no `.flake8`, `pyproject.toml`, or `.pylintrc` detected)
- Uses consistent spacing: 4-space indentation, single blank line between functions, double blank lines between sections
- Line length appears unconstrained (lines reach 100+ characters in some places)
- String formatting uses f-strings: `f"${balance:,.2f}"`, `f"{name} ({institution})"`

**Linting:**
- No linter configuration detected
- Style appears to follow PEP 8 conventions implicitly

## Import Organization

**Order:**
1. Standard library imports: `import os`, `import sys`, `import argparse`, `import asyncio`, `from datetime import datetime, timedelta`, `from pathlib import Path`
2. Third-party imports: `import requests`, `import yaml`
3. Conditional imports (optional dependencies wrapped in try/except): `from dotenv import load_dotenv`, `from monarchmoney import MonarchMoney`

**Path Aliases:**
- None detected — imports use absolute module names

**Example from `zoho_calendar_payments.py`:**
```python
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
    print("Error: 'requests' package is required...", file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
```

## Error Handling

**Patterns:**
- Graceful degradation for optional dependencies: Try to import, catch `ImportError`, set fallback or exit with clear error message
- Explicit error messages to stderr: `print("Error: ...", file=sys.stderr)`
- Exit codes used for failure: `sys.exit(1)` for configuration errors, import failures, API errors
- Request errors caught broadly: `except requests.RequestException as e:`
- Missing environment variables trigger early exit with context: `get_required_env()` validates and exits if missing

**Example from `monarch_balances.py`:**
```python
try:
    from monarchmoney import MonarchMoney
    from monarchmoney.monarchmoney import MonarchMoneyEndpoints
except ImportError:
    print("Error: 'monarchmoney' package is required...", file=sys.stderr)
    sys.exit(1)
```

**Example from `coverage_report.py`:**
```python
try:
    resp = requests.get(..., timeout=15)
    if resp.status_code == 200:
        ...
except requests.RequestException:
    print(f"Warning: failed to fetch Mercury {key_name} balances", file=sys.stderr)
```

## Logging

**Framework:** `print()` to stdout and stderr — no logging framework used

**Patterns:**
- Status messages during execution: `print("Fetching balances...", end=" ", flush=True)` followed by `print("OK")`
- Errors always go to stderr: `print("Error: ...", file=sys.stderr)`
- Warnings go to stderr with "Warning:" prefix: `print("Warning: ...", file=sys.stderr)`
- Formatted output to stdout for reports and display

**Example from `coverage_report.py`:**
```python
print("Fetching balances...", end=" ", flush=True)
monarch_balances = await fetch_monarch_balances()
mercury_balances = fetch_mercury_balances()
print("OK")
```

## Comments

**When to Comment:**
- Module-level docstrings explain purpose and usage: `"""Fetch and display account balances from Monarch Money."""`
- Function docstrings describe inputs, outputs, and side effects
- Inline comments explain non-obvious logic (date calculations, API quirks, fallback behavior)
- No comments needed for straightforward code paths

**JSDoc/TSDoc:**
- Not applicable — this is Python, not TypeScript

**Example from `zoho_calendar_payments.py`:**
```python
def parse_zoho_datetime(dt_str):
    """Parse a Zoho datetime string into a Python datetime.

    Zoho returns dates in formats like '20260410T140000Z' or '20260410T140000+0530'.
    Falls back to returning the raw string if parsing fails.
    """
```

## Function Design

**Size:** Ranges from ~10 lines (simple helpers like `_cache_path()`) to ~60 lines (complex functions like `print_report()`)

**Parameters:**
- Functions take explicit parameters rather than relying on global state
- Optional parameters use sensible defaults: `use_cache=True`, `force_login=False`, `account_type=None`
- Related parameters grouped logically: `(client_id, client_secret, refresh_token)`

**Return Values:**
- Single return values are common: `return mm`, `return balances`, `return cached`
- Tuples returned for multi-value results: `return balance, source` in `resolve_balance()`
- Dictionary returns for complex data: `fetch_events()` returns list of event dicts

**Example from `coverage_report.py`:**
```python
def resolve_balance(account, monarch_balances, mercury_balances):
    """Get the current balance for an account from available sources."""
    # Try Mercury first (more accurate for Mercury accounts)
    mercury_id = account.get("mercury_id")
    if mercury_id and mercury_id in mercury_balances:
        return mercury_balances[mercury_id]["balance"], "mercury"

    # Try Monarch
    monarch_match = account.get("monarch_match")
    if monarch_match and monarch_match in monarch_balances:
        return monarch_balances[monarch_match]["balance"], "monarch"

    return None, None
```

## Module Design

**Exports:**
- No explicit module exports — all top-level functions are public and callable
- Private functions prefixed with underscore: `_cache_path()`, `_api_get()`
- Entry point is `if __name__ == "__main__": main()` pattern in all scripts

**Barrel Files:**
- Not applicable — flat module structure, no package hierarchy

## Special Patterns

**Configuration Loading:**
- Environment variables loaded via `dotenv.load_dotenv()` at module import time when available
- Required environment variables validated early via `get_required_env()`
- Data files (YAML) loaded from relative paths: `PAYMENTS_FILE = Path(__file__).parent / "payments.yaml"`

**Async/Await:**
- Used for I/O-bound operations: `async def fetch_monarch_balances()`, `async def get_client()`
- Top-level async entry point: `asyncio.run(main())` in `coverage_report.py` and `monarch_balances.py`
- Mixed sync/async calls within async contexts: sync function `fetch_mercury_balances()` called from async `main()`

**Caching Pattern (from `zoho_calendar_payments.py`):**
```python
def cache_get(key, ttl):
    """Read a cached value if it exists and hasn't expired."""
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
```

---

*Convention analysis: 2026-05-03*
