---
phase: 01-calendar-parsing-bill-mapping
verified: 2026-05-04T13:26:52Z
status: gaps_found
score: 3/4 must-haves verified
overrides_applied: 0
gaps:
  - truth: "Balances are fetched from Monarch Money for personal accounts AND Mercury for business accounts in a single run"
    status: failed
    reason: "fetch_mercury_balances is imported but never called. resolve_variable_amounts passes empty dict {} to resolve_balance instead of live Mercury data. Mercury balances are always zero/absent in bill-map output."
    artifacts:
      - path: "zoho_calendar_payments.py"
        issue: "Line 289: resolve_balance(acct_config, monarch_balances, {}) — hard-coded empty dict for mercury_balances. fetch_mercury_balances is imported on line 49 but no call site exists in the file."
    missing:
      - "Call fetch_mercury_balances() inside resolve_variable_amounts (or a new fetch-balances step) and pass the result to resolve_balance instead of {}"
      - "This likely needs to be: mercury_balances = fetch_mercury_balances() added alongside the existing monarch_balances = await fetch_monarch_balances() call, then resolve_balance(acct_config, monarch_balances, mercury_balances)"
---

# Phase 1: Calendar Parsing + Bill Mapping Verification Report

**Phase Goal:** The system can extract upcoming payments from Zoho Calendar, identify which funding account pays each bill, distinguish fixed vs variable amounts, and fetch current balances from both Monarch Money and Mercury
**Verified:** 2026-05-04T13:26:52Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

The four Success Criteria from ROADMAP.md are the authoritative must-haves for this phase.

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Running the tool pulls Zoho Calendar events and extracts bill name, amount, and due date from event titles | VERIFIED | `parse_event_title` exists (line 71), `TITLE_PATTERN` regex on line 67, `process_events` calls it (line 168), `due_date` extracted from `dateandtime.start` (line 190). 9 title-parsing tests pass. |
| 2 | Each extracted payment is matched to a funding (debit) account using the account identifier in the event notes field | VERIFIED | `parse_event_notes` (line 81) parses "Fund: X" field. `resolve_account` (line 143) maps to account ID. `process_events` (line 152) wires both. `fund_account_id` field populated in payment dict. 6 nickname tests + 4 process_events tests pass. |
| 3 | Variable-amount payments are visually flagged as estimates in output | VERIFIED | `display_grouped_payments` (line 224) appends `~estimate` flag when `p.get("is_variable")` is True (line 244). `parse_event_notes` sets `is_variable=True` on "VARIABLE" keyword (line 103-104). Tests cover this behavior. |
| 4 | Balances are fetched from Monarch Money for personal accounts AND Mercury for business accounts in a single run | FAILED | `fetch_mercury_balances` is imported (line 49) but never called anywhere in the file. `resolve_variable_amounts` (line 263) calls only `fetch_monarch_balances()` (line 270) and passes hard-coded empty dict `{}` to `resolve_balance` (line 289) in place of Mercury data. Mercury accounts produce no balance data. |

**Score:** 3/4 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `zoho_calendar_payments.py` | Full CLI with parsing, balance fetching, grouped display, optional calendar update | VERIFIED (partial) | All required functions present and wired. Mercury fetch missing — see gap. 662 lines. Contains: `TITLE_PATTERN`, `parse_event_title`, `parse_event_notes`, `build_nickname_lookup`, `resolve_account`, `process_events`, `load_payments_config`, `update_event_title`, `display_grouped_payments`, `resolve_variable_amounts`, `--bill-map` flag, `--update-calendar` flag, `asyncio.run(`, `abs(balance)`, `sys.exit(1)` on errors. |
| `tests/test_parsing.py` | Unit tests for all parsing functions | VERIFIED | 258 lines, 28 tests across 4 classes. All 28 pass. Covers title parsing, notes parsing, nickname resolution, process_events collect-and-report. |
| `payments.yaml` | Account nickname mappings | VERIFIED | 35 accounts, all have `nicknames:` list field. 122 total nicknames. |
| `Taskfile.yml` | New task entry for forecast/bill-map command | VERIFIED | `bill-map:` task (line 24) and `bill-map:month:` task (line 29) both present. Both invoke `python zoho_calendar_payments.py --bill-map`. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `zoho_calendar_payments.py:main` | `zoho_calendar_payments.py:process_events` | function call | WIRED | Line 633: `payments, errors = process_events(events, config)` |
| `zoho_calendar_payments.py` | `coverage_report.py:fetch_monarch_balances` | import and call | WIRED | Import line 49; called line 270 inside `resolve_variable_amounts` |
| `zoho_calendar_payments.py` | `coverage_report.py:fetch_mercury_balances` | import and call | NOT_WIRED | Imported line 49, but zero call sites in the file. Empty dict passed to `resolve_balance` instead. |
| `zoho_calendar_payments.py:main` | `zoho_calendar_payments.py:display_grouped_payments` | function call | WIRED | Line 651: `display_grouped_payments(payments, errors)` |
| `zoho_calendar_payments.py` | `zoho_calendar_payments.py:update_event_title` | conditional call | WIRED | Lines 639-648: called when `--update-calendar` flag and `amount_source == "monarch"` |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `display_grouped_payments` | `payments` list | `process_events` -> Zoho Calendar API via `fetch_events` | Yes — real API events transformed through parse chain | FLOWING |
| `resolve_variable_amounts` | `monarch_balances` | `fetch_monarch_balances()` (Monarch Money API) | Yes — live API call | FLOWING |
| `resolve_variable_amounts` | mercury data (for `resolve_balance` 3rd arg) | Hard-coded `{}` | No — empty dict, no Mercury API call ever made | DISCONNECTED |

### Behavioral Spot-Checks

Step 7b: SKIPPED for live API calls (Zoho, Monarch, Mercury require credentials). Automated static checks run instead.

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Parsing functions importable | `python3 -c "from zoho_calendar_payments import parse_event_title, parse_event_notes, build_nickname_lookup, resolve_account, process_events"` | Exits 1 — coverage_report.py import fires at module load. Import guard prints error. | SKIP (import side effect) |
| All unit tests pass | `python -m pytest tests/test_parsing.py -v` | 28 passed in 0.63s | PASS |
| payments.yaml nicknames valid | `python3 -c "import yaml; ..."` (full assertion) | 35 accounts, all have nicknames, 122 total | PASS |
| Taskfile has bill-map entries | `grep "bill-map:" Taskfile.yml` | Lines 24 and 29 found | PASS |
| --bill-map and --update-calendar in help | Inferred from argparse definitions at lines 591-599 | Both flags defined | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| MAP-01 | 01-01, 01-02 | Extract bill-to-account mapping from Zoho Calendar event notes | SATISFIED | `parse_event_notes` parses "Fund: X" field; `resolve_account` maps to account ID; `process_events` wires both into payment dicts with `fund_account_id` |
| MAP-02 | 01-01, 01-02 | Parse bill name and amount from event title ("Name - $Amount") | SATISFIED | `parse_event_title` with `TITLE_PATTERN` regex; 9 tests pass covering simple, commas, cents, zero, extra spaces |
| MAP-03 | 01-01, 01-02 | Variable-amount payments flagged as dynamic | SATISFIED | `is_variable` flag set by "VARIABLE" keyword in notes; `~estimate` shown in `display_grouped_payments`; test coverage confirmed |
| FCST-04 | 01-02 | Fetch real-time balances from Monarch Money (personal) AND Mercury (business) — separate sources | BLOCKED | Monarch: fetched. Mercury: imported but never called. `resolve_variable_amounts` passes `{}` as mercury_balances to `resolve_balance`. Mercury accounts always resolve to no balance. |

**Orphaned requirements check:** No additional requirements map to Phase 1 in REQUIREMENTS.md beyond MAP-01, MAP-02, MAP-03, FCST-04. All four are accounted for.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `payments.yaml` | 143, 179, 188, 198 | `monarch_match: null  # TODO: confirm Monarch name` | Info | Affects Monarch balance resolution for those 4 accounts only; does not block Mercury gap or parsing logic |
| `zoho_calendar_payments.py` | 289 | `resolve_balance(acct_config, monarch_balances, {})` | Blocker | Hard-coded empty dict for mercury_balances — Mercury accounts never get real balances. This is the root cause of the FCST-04 gap. |

### Human Verification Required

#### 1. Live End-to-End Bill Map Output

**Test:** Run `task bill-map -- --days 30` with valid Zoho Calendar credentials and structured event notes (format: "Fund: X | Source: Y | VARIABLE")
**Expected:** Output shows "BILL MAP" header, payments grouped by funding account ID, variable payments show "~estimate" tag, parse errors listed at bottom
**Why human:** Requires live Zoho credentials and calendar events with structured notes; cannot verify API connectivity or output formatting programmatically

#### 2. Mercury Balance Integration (After Gap is Fixed)

**Test:** After fixing the `fetch_mercury_balances` call gap, run `task bill-map -- --days 30` with a calendar event that has a variable payment sourced from a Mercury account (one with a `mercury_id` set in payments.yaml)
**Expected:** The variable payment amount is replaced with the actual Mercury account balance (not the calendar event's placeholder amount)
**Why human:** Requires live Mercury API key and a payments.yaml account with valid `mercury_id` to confirm the end-to-end flow works

### Gaps Summary

One gap blocks full goal achievement. The ROADMAP Success Criterion #4 and requirement FCST-04 both explicitly require Mercury balances to be fetched alongside Monarch in the same run. The implementation imports `fetch_mercury_balances` but never calls it. `resolve_variable_amounts` only fetches Monarch and passes an empty dict `{}` to `resolve_balance`, meaning any business account with a `mercury_id` will silently get no balance data.

**Root cause:** A single missing call and variable assignment in `resolve_variable_amounts`. The fix is straightforward: add `mercury_balances = fetch_mercury_balances()` alongside the existing Monarch fetch, then pass `mercury_balances` instead of `{}` to `resolve_balance`.

**Impact:** Personal accounts (Monarch) work correctly. Business accounts (Mercury) always fall through with no balance, so variable payments for business accounts keep their calendar placeholder amounts rather than real balances.

---

_Verified: 2026-05-04T13:26:52Z_
_Verifier: Claude (gsd-verifier)_
