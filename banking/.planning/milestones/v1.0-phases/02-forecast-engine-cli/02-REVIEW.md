---
phase: 02-forecast-engine-cli
reviewed: 2026-05-04T00:00:00Z
depth: standard
files_reviewed: 5
files_reviewed_list:
  - payment_forecast.py
  - test_forecast.py
  - Taskfile.yml
  - payments.yaml
  - requirements.txt
findings:
  critical: 0
  warning: 3
  info: 4
  total: 7
status: issues_found
---

# Phase 02: Code Review Report

**Reviewed:** 2026-05-04
**Depth:** standard
**Files Reviewed:** 5
**Status:** issues\_found

## Summary

Reviewed the forecast engine CLI (`payment_forecast.py`), its unit tests (`test_forecast.py`), task runner config (`Taskfile.yml`), data file (`payments.yaml`), and dependencies (`requirements.txt`).

The core forecast logic is well-structured: the balance resolution pipeline, severity classification, and exit-code contract are correctly implemented. The `rrule`-based date handling is sound.

Three warnings were found: a double-indent display bug in the grouped view, an unguarded `KeyError` on required YAML fields, and an incomplete test that gives false coverage confidence. Four info items cover the missing unit test task in Taskfile, an unpinned dependency inconsistency, weak data quality in payments.yaml, and a test assertion gap.

No security vulnerabilities or data-loss risks were found.

---

## Warnings

### WR-01: KeyError crash on missing `day_of_month` or `name` in payment entry

**File:** `payment_forecast.py:266`, `payment_forecast.py:499`

**Issue:** Both `p["day_of_month"]` (line 266, inside `build_forecast`) and `p["name"]` (line 499, inside `main`) use hard key access on payment dicts loaded from YAML. If a payment entry in `payments.yaml` omits either field, this raises an unhandled `KeyError` that crashes the entire forecast with a Python traceback instead of a clear error message. The existing `validate_funding_accounts` function shows a pattern of using `.get()` defensively — this same pattern is not applied to `day_of_month` or `name`.

**Fix:**
```python
# Line 266 — in build_forecast, inside the payment loop:
day = p.get("day_of_month")
if day is None:
    print(f"Warning: payment '{p.get('name', '<unnamed>')}' missing day_of_month, skipping",
          file=sys.stderr)
    continue
dates = get_payment_dates_in_horizon(day, days)

# Line 499 — in main, inside the missing-funding-account report loop:
day = p.get("day_of_month", "?")
amount = p.get("amount", 0.0)
print(f"  - {p.get('name', '<unnamed>')}  (day {day}, ${amount:,.2f})", file=sys.stderr)
```

---

### WR-02: Double-indent in `print_grouped_view` severity output lines

**File:** `payment_forecast.py:354-361`

**Issue:** `proj_str` is built as `f"  Projected Balance: ${acct['projected_balance']:,.2f}"` (with two leading spaces). It is then interpolated into the print statements as `f"  {Color.BOLD_RED}{proj_str}..."` — adding another two leading spaces. This produces four spaces of indentation on the colored output lines while the non-colored fallback (`print(f"  {Color.GREEN}{proj_str}{Color.RESET}")`) also double-indents. The plain `print(f"  (threshold: ...)")` line at 359 only has two spaces, creating inconsistent alignment.

**Fix:** Remove the leading spaces from `proj_str` and keep them only in the enclosing format strings:
```python
# Line 354 — define without leading indent:
proj_str = f"Projected Balance: ${acct['projected_balance']:,.2f}"

# Lines 356, 358, 361 already supply the "  " indent via the f-string prefix,
# so alignment is consistent with the threshold line at 359.
```

---

### WR-03: `test_account_with_no_payments` does not assert its named case

**File:** `test_forecast.py:277-300`

**Issue:** The test is named "handles account with no payments (projected == current, severity='ok')" but the actual assertions only check `checking-1` (which has a payment). The `acct2` lookup result is retrieved but never asserted — the inline comment says "It may or may not appear depending on implementation." This means the test does not verify the behavior it advertises, giving false confidence that the no-payment path is covered.

**Fix:** Either assert the specific claimed behavior or rename/split the test to accurately reflect what is covered:
```python
# If checking-2 is expected to NOT appear (current implementation only includes
# accounts that have at least one payment assigned):
self.assertIsNone(acct2)  # accounts with no payments are excluded from forecast

# Or if the intent is to include them:
# Fix the implementation to include all accounts, then assert:
self.assertIsNotNone(acct2)
self.assertAlmostEqual(acct2["projected_balance"], acct2["current_balance"])
self.assertEqual(acct2["severity"], "ok")
```

---

## Info

### IN-01: No task defined to run unit tests (`test_forecast.py`)

**File:** `Taskfile.yml:69-72`

**Issue:** The only `test` task runs `pytest test_integration.py`, which requires live Monarch Money credentials. There is no task to run the unit tests in `test_forecast.py`. Developers running `task test` expecting to verify logic changes will instead trigger a live API call (or fail with missing credentials).

**Fix:** Add a `test:unit` task:
```yaml
test:unit:
  desc: Run unit tests (no credentials required)
  cmds:
    - python -m pytest test_forecast.py -v

test:
  desc: Run integration tests (requires valid .env credentials)
  cmds:
    - python -m pytest test_integration.py -v
```

---

### IN-02: `requirements.txt` has inconsistent pinning — only `gql` is constrained

**File:** `requirements.txt:5`

**Issue:** `gql<4` is the only package with a version constraint. All others (`requests`, `python-dotenv`, `pyyaml`, `monarchmoney`, `pytest`, `xero-python`, `tabulate`, `python-dateutil`) are unpinned. Unpinned packages silently update on `pip install`, which can introduce breaking changes. The inconsistency also suggests the `gql<4` pin was added reactively (it likely broke), meaning the same risk exists for other packages.

**Fix:** Pin all dependencies to tested versions, or use a lock file:
```
# Either pin explicitly:
tabulate==0.9.0
python-dateutil==2.9.0
pyyaml==6.0.2
# etc.

# Or generate a lock file:
pip freeze > requirements.lock
```
At minimum, add `python-dateutil` and `tabulate` pins since `payment_forecast.py` exits at startup if either is missing.

---

### IN-03: Many payments have `funding_account: null` — tool is currently non-functional

**File:** `payments.yaml:431-563`

**Issue:** Twelve payment entries have `funding_account: null` with `# NEEDS REVIEW` comments. The `validate_funding_accounts` check in `main()` correctly blocks execution when any payment lacks a funding account, so running `task forecast` today will print an error and exit without producing output. This is intentional fail-fast behavior, but it means the tool cannot be used until the data gaps are resolved.

Affected payments include several large credit card payments (`BoA Platinum Plus 1 (min)` at $953, `BoA Platinum Plus 2 (min)` at $1,801, `Whole Foods Chase (min)` at $868, `Citi Rewards+` at $556) as well as `Chase Credit Card`, `Northwest`, and others.

**Fix:** Assign `funding_account` values for each unresolved payment. The payments likely map to `boa-checking-2803` or `cap1-recurring-4354` based on the account descriptions. Alternatively, if some payments should be excluded from the forecast temporarily, a separate `enabled: false` flag would allow progressive rollout without blocking the tool.

---

### IN-04: `total_available` in summary silently excludes accounts with unknown balances

**File:** `payment_forecast.py:296-298`

**Issue:** When `balance_source == "unknown"` (line 293), `current_balance` is set to `0.0`. The `total_available` accumulation guard `if current_balance > 0` (line 297) then excludes that account from the available pool. The account's payments are still included in `total_outgoing`. This means the `net_position` in the summary (`total_available - total_outgoing`) correctly reflects the risk (outgoing with no confirmed funding). However, the summary does not surface this distortion — a user seeing a large `total_outgoing` with small `total_available` may not realize some accounts dropped out of the calculation due to balance fetch failures.

**Fix:** Add a note to the summary output when unknown-balance accounts are present:
```python
# In print_summary or build_forecast, track unknown-balance accounts:
unknown_accounts = [a for a in forecast["accounts"] if a["balance_source"] == "unknown"]
if unknown_accounts:
    names = ", ".join(a["name"] for a in unknown_accounts)
    print(f"  Note: balance unavailable for {names}; treated as $0 in summary.",
          file=sys.stderr)
```

---

_Reviewed: 2026-05-04_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
