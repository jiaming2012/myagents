# Phase 1: Calendar Parsing + Bill Mapping - Context

**Gathered:** 2026-05-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Parse Zoho Calendar events into structured payment data, map each payment to a funding account via event notes, distinguish fixed vs variable amounts (pulling real balances from Monarch for variable ones), and fetch current balances from both Monarch Money and Mercury in a single run.

</domain>

<decisions>
## Implementation Decisions

### Event Parsing Rules
- **D-01:** Strict parsing — event titles MUST match "Name - $Amount" format. Non-matching events cause a collected failure (not silent skip).
- **D-02:** Dedicated calendar assumption — ZOHO_CALENDAR_ID points to a payments-only calendar. Every event on it is treated as a bill.
- **D-03:** Fail mode is collect-and-report — process all events, collect failures, then exit non-zero with a summary of which events failed and why.
- **D-04:** New parsing logic extends `zoho_calendar_payments.py` (not a new module). Keep one file per data source.

### Account Matching
- **D-05:** Notes field contains structured data with "Fund:" prefix identifying the funding account (free-text name).
- **D-06:** Account name resolution uses nickname/alias mappings defined in payments.yaml. Claude's discretion on exact matching strategy (substring, alias list, etc.) — simplest reliable approach.
- **D-07:** Funding account in notes is REQUIRED. Missing notes = strict failure, reported alongside parse errors.
- **D-08:** Exception: a special keyword (e.g., "NONE" or "N/A") in notes explicitly marks an event as having no funding account (informational/self-funded).

### Variable Payment Flagging
- **D-09:** Variable payments identified by a "VARIABLE" keyword in the event notes field.
- **D-10:** Notes field is structured: `Fund: <account> | Source: <monarch_account> | VARIABLE` — contains both funding account, source account (for balance lookup), and variable marker.
- **D-11:** For variable payments, the tool pulls the real current balance from Monarch using the "Source:" account nickname, replacing the estimate in the title.
- **D-12:** Calendar title update is opt-in via `--update-calendar` flag. Default is read-only (use Monarch balance internally only).
- **D-13:** Account nicknames in payments.yaml map human-readable names (e.g., "Amex") to actual Monarch account identifiers.

### Output Structure
- **D-14:** Data flows between Phase 1 and Phase 2 as in-memory Python dicts (function calls, no intermediate file).
- **D-15:** Payments are assumed paid once their calendar date passes — no confirmation tracking.
- **D-16:** CLI output groups payments by funding account (heading per account, bills listed under each).

### Claude's Discretion
- Account matching implementation detail (substring vs alias lookup — simplest reliable approach)
- Whether Phase 1 CLI shows current balances alongside payments or defers that to Phase 2

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing Implementation
- `zoho_calendar_payments.py` — Current Zoho Calendar integration (parsing will be extended here)
- `coverage_report.py` — Current balance fetching from Monarch + Mercury (resolve_balance pattern)
- `monarch_balances.py` — Monarch Money API integration
- `payments.yaml` — Payment registry with account definitions (will be extended with nicknames)

### Configuration
- `.env.example` — Required environment variables for all API integrations
- `Taskfile.yml` — Task runner definitions (new task entry needed)

### Requirements
- `.planning/REQUIREMENTS.md` — MAP-01, MAP-02, MAP-03, FCST-04 are the target requirements

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `zoho_calendar_payments.py:get_access_token()` — OAuth2 token refresh (reuse as-is)
- `zoho_calendar_payments.py:cache_get()/cache_set()` — File-based caching with TTL
- `zoho_calendar_payments.py:parse_zoho_datetime()` — Date parsing for Zoho format strings
- `coverage_report.py:resolve_balance()` — Balance resolution from Monarch/Mercury (pattern to follow)
- `coverage_report.py:fetch_monarch_balances()` — Async Monarch fetch (reuse directly)
- `coverage_report.py:fetch_mercury_balances()` — Sync Mercury fetch (reuse directly)

### Established Patterns
- Single-file scripts with `main()` entry point and argparse
- `print()` to stdout for output, stderr for errors/warnings
- Constants in SCREAMING_SNAKE_CASE at top of file
- Async for Monarch, sync for Mercury
- File-based `.cache/` with TTL for API responses

### Integration Points
- `payments.yaml` — needs new `nicknames` or `aliases` field per account
- `Taskfile.yml` — needs new task entry for the forecast/parsing command
- `zoho_calendar_payments.py` — extend with structured parsing and account mapping functions

</code_context>

<specifics>
## Specific Ideas

- Notes field format: `Fund: Chase 7667 | Source: Amex | VARIABLE`
- Variable payments pull real balance from Monarch to replace title estimate
- `--update-calendar` flag enables write-back to Zoho Calendar with real amounts
- payments.yaml gets account nickname mappings for resolving free-text names

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-calendar-parsing-bill-mapping*
*Context gathered: 2026-05-03*
