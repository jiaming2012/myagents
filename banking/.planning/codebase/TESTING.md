# Testing Patterns

**Analysis Date:** 2026-05-03

## Test Framework

**Runner:**
- `pytest` (version unspecified in requirements.txt, installed via `pytest` package)
- Config: No dedicated pytest config file (`pytest.ini`, `setup.cfg`, or `pyproject.toml` not present)

**Assertion Library:**
- Built-in `assert` statements — no external assertion library

**Run Commands:**
```bash
task test                                    # Run integration tests
python -m pytest test_integration.py -v     # Explicit pytest invocation with verbose output
```

## Test File Organization

**Location:**
- Co-located in project root alongside implementation files
- No separate `tests/` directory structure

**Naming:**
- Test files prefixed with `test_`: `test_integration.py`
- Test functions prefixed with `test_`: `def test_payments_month_returns_events():`

**Structure:**
```
banking/
├── coverage_report.py           # Implementation
├── monarch_balances.py          # Implementation
├── zoho_calendar_payments.py    # Implementation
├── test_integration.py          # Integration test (single file)
└── requirements.txt             # Dependencies including pytest
```

## Test Structure

**Suite Organization:**
```python
"""
Integration test: verifies the Zoho Calendar payments script returns > 0 events.

Requires valid credentials in .env or environment variables.
Run: task test
"""

import re
import subprocess
import sys


def test_payments_month_returns_events():
    result = subprocess.run(
        [sys.executable, "zoho_calendar_payments.py", "--days", "30"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, f"Script failed:\n{result.stderr}"

    match = re.search(r"Total: (\d+) event\(s\)", result.stdout)
    assert match, f"Could not find 'Total: N event(s)' in output:\n{result.stdout}"

    count = int(match.group(1))
    assert count > 0, f"Expected > 0 events, got {count}\n{result.stdout}"


if __name__ == "__main__":
    test_payments_month_returns_events()
    print("PASSED: payments:month returned > 0 events")
```

**Patterns:**
- Integration test approach: runs full scripts as subprocesses, validates output and exit codes
- No setup/teardown functions defined
- Test validates both process return code and output parsing
- Error messages include context (expected vs. actual) for debugging

## Mocking

**Framework:** No mocking framework used (`unittest.mock` not imported)

**Patterns:**
- Scripts designed for integration testing without mocks
- Environment variables (`ZOHO_CLIENT_ID`, `MONARCH_TOKEN`, etc.) required for actual API calls
- Optional dependencies handled with try/except at import time rather than mocked

**What to Mock:**
- External API calls (Zoho, Monarch Money, Mercury) could be mocked if unit tests were added
- File I/O could be mocked with temporary directories

**What NOT to Mock:**
- Exit codes and process return values (tested as-is)
- Stdout/stderr output (validated directly)
- Current implementation uses subprocess isolation instead of mocking

## Fixtures and Factories

**Test Data:**
- None currently implemented
- Integration tests use live data from configured API endpoints
- `.env` file contains real credentials (required for test execution)

**Location:**
- No fixtures directory
- `.env.example` serves as template for required test environment variables

## Coverage

**Requirements:** None enforced — no coverage configuration detected

**View Coverage:**
- Not currently measured or reported
- No pytest-cov or coverage.py configuration

## Test Types

**Unit Tests:**
- Not implemented
- Scripts are designed as entry points rather than testable modules

**Integration Tests:**
- `test_integration.py` contains single integration test
- Scope: Validates that `zoho_calendar_payments.py --days 30` returns > 0 events
- Approach: Subprocess invocation with output parsing and exit code validation
- Requires valid `.env` credentials to run

**E2E Tests:**
- The integration test is effectively an end-to-end test
- Validates full workflow: authentication → API calls → output formatting
- Not framework-based (no Cypress, Selenium, Playwright, etc.)

## Common Patterns

**Async Testing:**
- Not directly tested — async functions are called from sync test context via `asyncio.run()` in main scripts
- Integration test approach avoids async testing complexity by testing scripts end-to-end

**Error Testing:**
```python
# From test_integration.py — validates error handling
assert result.returncode == 0, f"Script failed:\n{result.stderr}"

# Validates specific output format
match = re.search(r"Total: (\d+) event\(s\)", result.stdout)
assert match, f"Could not find 'Total: N event(s)' in output:\n{result.stdout}"
```

## Testing Approach Philosophy

**Current State:**
- Single integration test validates the payment calendar ingestion workflow
- No unit tests for individual functions
- Relies on subprocess isolation and output validation
- Scripts are CLI-first, not library-first

**Implicit Testing:**
- Each script can be run standalone via `task` commands
- Manual testing via `task payments`, `task report`, `task balances`
- Coverage is behavioral: scripts must produce expected output and exit cleanly

**Future Testing Opportunities:**
- Extract core logic (parsing, filtering, balance resolution) into testable functions
- Add unit tests for date calculation logic (`get_upcoming_payments()`)
- Mock external API calls in integration tests to remove credential dependency
- Add property-based tests for datetime edge cases (month boundaries, leap years)

---

*Testing analysis: 2026-05-03*
