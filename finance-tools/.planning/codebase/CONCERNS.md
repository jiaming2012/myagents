# Codebase Concerns

**Analysis Date:** 2026-05-03

## Data Quality Issues

**Incomplete Account Mappings:**
- Issue: Mercury accounts (personal and business) lack `monarch_match` entries, creating blind spots in balance resolution
- Files: `payments.yaml` (lines 27, 38, 49, 59, 69, 79)
- Impact: `resolve_balance()` in `coverage_report.py` cannot match Mercury accounts to Monarch Money, falls back to partial last4 matching which is unreliable
- Fix approach: Populate `monarch_match` fields by manually querying Monarch Money API and matching display names, or add a one-time mapping utility

**Unconfirmed Monarch Account Names:**
- Issue: Four credit card accounts have TODO comments indicating Monarch display names were never confirmed
- Files: `payments.yaml` (lines 131, 163, 171, 180) - accounts: "NFCU Check Rewards", "Wells Fargo Propel", "Amazon Prime", "US Bank Cash+"
- Impact: Balance lookups will fail for these accounts, coverage_report will report "unknown" balance, reducing visibility
- Fix approach: Run `python monarch_balances.py` and verify exact display names match, or enable account search by last4 with confidence thresholds

**Unresolved Payment Funding Sources:**
- Issue: 15+ payments lack `funding_account` assignments (marked "NEEDS REVIEW") including business bills totaling $3,000+
- Files: `payments.yaml` (lines 392-394, 420-422, 431, 441, 451, 461, 471, 480, 490, 500, 510-514, 520-524)
- Impact: Coverage report cannot calculate shortfalls for these payments; they appear under "UNASSIGNED" section; no visibility into whether business accounts have cash to cover them
- Fix approach: Manual audit—match each payment against account statements, document funding source in `funding_account` field, review any ambiguous cases

**Autopay Status Uncertainty:**
- Issue: Multiple payments have `autopay: null` with notes "needs review", making it unclear whether they're automatically paid or require manual action
- Files: `payments.yaml` (lines 347, 356, 365, 375, 385, 393, 404, 413, 422, 511, 521)
- Impact: Risk of missed manual payments if autopay status is incorrect; coverage analysis assumes `autopay=false` payments require immediate funds, but null status is ambiguous
- Fix approach: Cross-check with bank statements (Mercury, Capital One, NFCU) to confirm which payments are truly automated; update all autopay fields to explicit true/false

**Inconsistent Amount Data:**
- Issue: Several payments have amount: 0.00, which is either a data entry error or represents variable-amount payments
- Files: `payments.yaml` (lines ~7, 19, 13, 30, 27)
- Impact: Coverage report includes $0 payments in totals (doesn't break math but clutters report); unclear if these are placeholders or intentional
- Fix approach: Remove zero-amount entries if they're placeholders, or add a category like "variable" with instructions to check account before due date

## Architectural Fragility

**Single-Source-of-Truth Synchronization Problem:**
- Issue: Payment data exists in three places (Zoho Calendar, Google Sheets, payments.yaml) with no automated sync
- Files: `payments.yaml` (header comment), `zoho_calendar_payments.py`, `coverage_report.py`
- Impact: Manual seed from Zoho (line 2 of payments.yaml) means updates in calendar aren't reflected until manually re-seeding; easy to drift out of sync; no source-of-truth clarity
- Fix approach: Implement one-way sync from Zoho Calendar → payments.yaml on a schedule (weekly), or establish that payments.yaml is the canonical source and disable manual calendar editing

**Multiple Balance Resolution Attempts with Silent Fallback:**
- Issue: `resolve_balance()` in `coverage_report.py:148-167` tries three strategies (mercury_id, monarch_match, last4 substring) but silently returns None if all fail
- Files: `coverage_report.py` (lines 148-167)
- Impact: Coverage report shows "Balance: unknown" for accounts that fail to match, making it impossible to know if there's a shortfall or if the lookup just broke; no logging/debug info
- Fix approach: Log all resolution attempts with reasons for failure (e.g., "mercury_id not in mercury_balances", "monarch_match null", "last4 '6343' not found in 47 Monarch accounts"); consider warn on any unknown balance in critical account

**Bare Exception Catching:**
- Issue: `monarch_balances.py:52` catches all exceptions with `except Exception: pass`, silently swallowing session load failures
- Files: `monarch_balances.py` (lines 49-53)
- Impact: If session file is corrupted, code falls back to re-login without warning user; if re-login itself fails (e.g., MFA timeout), no feedback; difficult to debug
- Fix approach: Catch specific exceptions (FileNotFoundError, pickle.UnpicklingError) and log them; let other exceptions propagate or give user clear guidance

## Security & API Credential Management

**Environment Variable Exposure Risk:**
- Issue: All three scripts load secrets from env vars (MONARCH_TOKEN, MERCURY_BUSINESS_API_KEY, MERCURY_PERSONAL_API_KEY, ZOHO_* keys) with no validation that they're set
- Files: `coverage_report.py:101-104`, `zoho_calendar_payments.py:354-357`, `monarch_balances.py:41-42`
- Impact: Scripts will attempt to run with missing credentials and fail cryptically; credentials printed in error messages could leak to logs/stderr
- Fix approach: Use a shared credential validation function that fails fast with clear errors; never include actual token values in error messages (redact to prefix only)

**Token Refresh Without Rotation:**
- Issue: Zoho refresh tokens are cached in `.cache/` as plain JSON, and `TOKEN_TTL=50 min` means frequent token fetch calls to Zoho
- Files: `zoho_calendar_payments.py:45, 99-103, 136`
- Impact: Cached tokens are readable by any user with filesystem access; 50-min TTL means high API call volume (480+ token refreshes/year); no token rotation strategy
- Fix approach: Store access tokens in memory only (don't cache); use system keyring for refresh token if multi-user environment; monitor token API quota

**Unauthenticated Last4 Matching:**
- Issue: `coverage_report.py:160-165` matches accounts by last4 digit substring in Monarch account names, which could collide (two Visa cards both end in 6343) and is guessable
- Files: `coverage_report.py` (lines 160-165)
- Impact: Unlikely but possible to match wrong account; no validation that matched account is correct
- Fix approach: Add explicit confirmation step or require canonical `monarch_match` name for all accounts (no fallback to last4)

## Testing & Validation Gaps

**Single Integration Test, No Unit Tests:**
- Issue: Only `test_integration.py` exists; it checks if Zoho returns > 0 events within 30 days but doesn't validate data correctness or error cases
- Files: `test_integration.py` (lines 1-33)
- Impact: No tests for date calculation edge cases (month-end payments, leap years), Mercury balance fetch failures, payment coverage calculation accuracy, or Zoho cache consistency
- Fix approach: Add unit tests for `get_upcoming_payments()` with edge dates (Jan 31, Feb 28/29, month transitions); add tests for resolve_balance() with various input combinations; mock API failures

**No Validation of payments.yaml Structure:**
- Issue: Coverage_report reads payments.yaml but doesn't validate required fields or data types (amounts should be numeric, day_of_month 1-31, funding_account should exist if not null)
- Files: `coverage_report.py:45-48`, `payments.yaml` structure
- Impact: Silently accepts invalid data (negative days, text amounts, invalid account references); report could compute wrong totals
- Fix approach: Add schema validation at load time (e.g., pydantic or jsonschema); report all validation errors before attempting coverage calculation

**Test Requires Live Credentials:**
- Issue: `test_integration.py` requires valid .env with ZOHO_* credentials to pass
- Files: `test_integration.py:13-27`
- Impact: CI/CD cannot run tests without secrets; hard to test in isolation; every test run hits live API (slow, high latency)
- Fix approach: Mock Zoho API responses; separate integration tests (requires creds, slow) from unit tests (mocked, fast); run unit tests in CI, integration tests as separate suite

## Known Limitations

**Date Calculation Logic Edge Cases:**
- Issue: `get_upcoming_payments()` in `coverage_report.py:51-92` tries to construct due dates for "invalid" day-of-month (e.g., 31st in April) by creating a datetime for next month, but doesn't handle all edge cases gracefully
- Files: `coverage_report.py` (lines 66-84)
- Impact: If day-of-month is 29-31, code creates due date in Feb/Apr/Jun/Sep/Nov and may skip month entirely if day doesn't exist; no test for Feb 29 on non-leap-year, no explicit handling of 30-Feb scenario
- Fix approach: Use `dateutil.relativedelta` or explicit day-clamping logic; add test cases for month-end dates across all months in leap/non-leap years

**No Handling of Grace Periods or Payment Cycles:**
- Issue: Coverage report assumes payment "due" on day_of_month means cash must be available at midnight that day; no support for grace periods (e.g., "due on 15th but paid on 18th") or split-payment schedules
- Files: `coverage_report.py:51-92`, `payments.yaml` (single day_of_month per payment)
- Impact: May report false shortfalls if payment due date doesn't align with when account receives funds; can't model bi-weekly or split payments
- Fix approach: Add optional `grace_period_days` and `payment_frequency` fields to payments.yaml; adjust coverage window calculations accordingly

**No Historical Payment Tracking:**
- Issue: System sources payment list from Zoho Calendar + manual payments.yaml but has no record of whether past payments actually went through
- Files: All scripts assume future-focused
- Impact: Can't audit why a payment failed in the past, or detect if Zoho events are outdated; no confidence in accuracy of coverage predictions
- Fix approach: Maintain a transaction ledger (CSV or DB) with Zoho UID, amount, actual payment date, status (pending/completed/failed); cross-check against bank statements

## Dependency Risks

**Deprecated Monarch Money SDK:**
- Issue: `monarchmoney` library is community-maintained (third-party), not official; library still uses old API endpoint (fixed at line 33 of `monarch_balances.py`)
- Files: `monarch_balances.py:32-33`, `requirements.txt` (no version pin)
- Impact: If Monarch Money changes API, this library may not update; endpoint override is fragile; no version constraint allows breaking changes on pip install
- Fix approach: Pin `monarchmoney` to a tested version in requirements.txt (e.g., `monarchmoney==0.1.5`); subscribe to library release notes; consider switching to official Monarch API client if one becomes available

**Unversioned Dependencies:**
- Issue: `requirements.txt` lists packages without version constraints (requests, pyyaml, gql, etc.)
- Files: `requirements.txt`
- Impact: `pip install -r requirements.txt` may pull incompatible versions; `gql<4` is the only constrained dependency
- Fix approach: Run `pip freeze > requirements.txt` to lock all versions; test with constraints; update periodically (quarterly) with tested newer versions

**gql <4 Constraint Reason Unclear:**
- Issue: `requirements.txt` specifies `gql<4` but no `gql` is imported or used in any Python script
- Files: `requirements.txt`, all .py files
- Impact: Dead dependency taking disk space; constraint may be vestigial from earlier code; increases security audit surface
- Fix approach: Search codebase for gql usage; if absent, remove from requirements.txt and test that coverage_report and zoho_calendar_payments still work

## Performance Concerns

**Synchronous HTTP Requests in loops:**
- Issue: `zoho_calendar_payments.py:216-228` fetches event details one-by-one in a loop without concurrency (for event in events: fetch_event_detail(...))
- Files: `zoho_calendar_payments.py` (lines 216-228)
- Impact: If fetching 50 events with 500ms latency each, this takes 25 seconds; report runs slow or times out; no parallelization despite async client available
- Fix approach: Use `asyncio.gather()` to fetch multiple event details in parallel; set reasonable concurrency limit (e.g., 5 concurrent requests)

**Cache Directory Never Pruned:**
- Issue: `.cache/` directory stores MD5-hashed JSON files indefinitely with no cleanup
- Files: `zoho_calendar_payments.py:49, 74-78`, `.cache/` directory (14 files present)
- Impact: Over months/years, cache grows unbounded; old stale data never expires except by TTL on read; if files accumulate, disk usage grows
- Fix approach: Add optional cache prune task (task cache:clear) that removes files older than N days; or implement automatic prune when cache size exceeds threshold

**Monarch Money Balance Fetch Blocks on Every Report:**
- Issue: `coverage_report.py:95-116` calls `await fetch_monarch_balances()` synchronously on every report run, waits for full account list from API
- Files: `coverage_report.py` (lines 95-116, 284-286)
- Impact: Report latency depends on Monarch API responsiveness; no timeout handling; if Monarch is slow or down, report hangs or fails
- Fix approach: Add request timeout (already present in Mercury fetch at line 132, missing in Monarch); consider optional caching of balance results with --fresh-balance flag

## Tech Debt & Maintenance Burden

**Multiple Credential Initialization Patterns:**
- Issue: Three different ways credentials are handled: (1) Zoho uses .env + OAuth flow, (2) Monarch uses token env var OR interactive pickle session, (3) Mercury uses API key from env var
- Files: `zoho_calendar_payments.py`, `monarch_balances.py`, `coverage_report.py`
- Impact: Inconsistent initialization; hard to add new data source; difficult to debug which credential failed; no unified credential validation
- Fix approach: Extract credential handling to a shared module (`credentials.py`) with unified lookup, validation, and error messages

**Scattered Configuration:**
- Issue: Configuration spread across (1) .env file, (2) hardcoded TTLs in zoho_calendar_payments.py (lines 45-47), (3) payment registry in payments.yaml, (4) CLI args in Taskfile
- Files: `.env.example`, `zoho_calendar_payments.py`, `payments.yaml`, `Taskfile.yml`
- Impact: Hard to change settings without editing multiple files; no single config file to understand system behavior
- Fix approach: Create `config.yaml` or `.banking/config.yml` with all TTLs, API endpoints, timeouts, and task defaults; load at startup

**Bare `pass` in Exception Handlers:**
- Issue: Two `pass` statements in exception handlers (`coverage_report.py:33`, `monarch_balances.py:23`) with no indication of intent
- Files: `coverage_report.py:33`, `monarch_balances.py:23`
- Impact: Code clarity is poor; unclear if this is deliberate fallback or incomplete error handling
- Fix approach: Replace `pass` with explicit comment explaining fallback (e.g., `pass  # dotenv is optional, continue without it`)

**No Logging Framework:**
- Issue: All status/error output is via `print()` and `sys.stderr`; no structured logging, no log levels, no persistent logs
- Files: All .py files
- Impact: Difficult to debug failures after the fact; no audit trail; error messages are ad-hoc and inconsistent; hard to parse programmatically
- Fix approach: Add Python `logging` module configuration; set up debug/info/warning/error levels; route to both stderr and optional log file

## Data Integrity Concerns

**No Validation of Coverage Report Accuracy:**
- Issue: Coverage report calculates shortfalls but has no sanity checks (e.g., sum of payment amounts should match sum of individual payment amounts)
- Files: `coverage_report.py:208` - total_due calculation
- Impact: Silent bugs in coverage calculation would go unnoticed; if a payment is missing from the list, shortfall calculation is wrong but report doesn't flag it
- Fix approach: Add assertion checks; log expected vs actual sums; add a dry-run mode that shows detailed calculation steps

**Autopay Field Ambiguity:**
- Issue: `autopay` field in payments.yaml can be `true | false | null`, but report treats null as "manual" (line 244: `"manual" if p.get("autopay") is False else "???"`)
- Files: `coverage_report.py:244`, `payments.yaml`
- Impact: Report shows "???" for null autopay, which is unhelpful; actual autopay status unknown; could miss automated payments or double-pay manually
- Fix approach: Require all autopay fields to be explicit true/false; add validation to reject null autopay in load phase; audit all entries and fix before next report run

---

*Concerns audit: 2026-05-03*
