---
phase: 01-calendar-parsing-bill-mapping
reviewed: 2026-05-04T12:00:00Z
depth: standard
files_reviewed: 4
files_reviewed_list:
  - Taskfile.yml
  - payments.yaml
  - tests/test_parsing.py
  - zoho_calendar_payments.py
findings:
  critical: 0
  warning: 3
  info: 3
  total: 6
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-05-04
**Depth:** standard
**Files Reviewed:** 4
**Status:** issues_found

## Summary

Reviewed the calendar parsing and bill mapping implementation: the main script (`zoho_calendar_payments.py`), the payment registry (`payments.yaml`), task runner config (`Taskfile.yml`), and unit tests (`tests/test_parsing.py`).

The parsing logic is well-structured with solid error handling (collect-and-report pattern). Tests cover the core parsing functions thoroughly. The main concerns are a timezone bug where local time is formatted as UTC for API calls, a potentially incorrect HTTP request format for calendar updates, and a regex edge case in title parsing.

## Warnings

### WR-01: Naive datetime formatted as UTC for Zoho API range queries

**File:** `zoho_calendar_payments.py:620`
**Issue:** `datetime.now(tz=None)` returns a naive local-time datetime. On lines 453-454, this value is formatted with a `Z` suffix (`%Y%m%dT000000Z`), which declares the timestamp as UTC. If the machine is not in UTC, the API query range will be wrong -- events could be missed or extra events returned depending on the offset.
**Fix:**
```python
# Line 620: Use UTC explicitly
from datetime import timezone
start_date = datetime.now(tz=timezone.utc)
```

### WR-02: Event update sends structured data as query parameter instead of request body

**File:** `zoho_calendar_payments.py:213-218`
**Issue:** `update_event_title` passes `eventdata` via `params=` (query string) on a PUT request. For a PUT that modifies a resource, the event data is typically sent in the request body (`data=` or `json=`). Sending a JSON-encoded string as a query parameter may hit URL length limits or be rejected by some proxies, and may not match the Zoho Calendar API contract.
**Fix:**
```python
# Verify against Zoho Calendar API docs. If body is expected:
resp = requests.put(url, headers=headers, data=params, timeout=15)
# Or if JSON body:
resp = requests.put(url, headers=headers, json={"title": new_title, "etag": etag}, timeout=15)
```

### WR-03: Title regex rejects amounts with exactly one decimal place

**File:** `zoho_calendar_payments.py:67`
**Issue:** The regex `[\d,]+(?:\.\d{2})?` requires exactly two decimal digits when a decimal point is present. A title like `"Name - $12.5"` will fail to match, raising `ValueError`. While current calendar data may always use two decimals, this is a fragile assumption that will produce a confusing error if a user enters a one-decimal amount.
**Fix:**
```python
# Allow 1 or 2 decimal places
TITLE_PATTERN = re.compile(r'^(.+?)\s*-\s*\$?([\d,]+(?:\.\d{1,2})?)\s*$')
```

## Info

### IN-01: Commented-out code and TODO markers in payments.yaml

**File:** `payments.yaml:143,179,188,198`
**Issue:** Multiple `# TODO: confirm Monarch name` comments indicate incomplete data migration. These are not bugs but represent known gaps that should be tracked.
**Fix:** Convert to tracked issues or resolve the Monarch match values so automated balance lookups work for these accounts.

### IN-02: Several payments have null funding_account with no resolution path

**File:** `payments.yaml:428,456,467,477,487,497,507,517,527,537,547,555`
**Issue:** Twelve payment entries have `funding_account: null` with `# NEEDS REVIEW` comments. While these are clearly marked as incomplete, they represent a significant portion of the payment registry that cannot participate in bill mapping or coverage calculations.
**Fix:** Prioritize resolving funding accounts for these entries. Consider adding a validation script that flags null funding accounts as part of CI.

### IN-03: MD5 used for cache key generation

**File:** `zoho_calendar_payments.py:301`
**Issue:** `hashlib.md5` is used to generate cache file names. While this is not a security concern (cache keys are not security-sensitive), MD5 is deprecated for new usage and some environments emit warnings.
**Fix:** No immediate action needed. If warnings appear in future Python versions, switch to `hashlib.sha256`.

---

_Reviewed: 2026-05-04_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
