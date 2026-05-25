# Phase 1: Calendar Parsing + Bill Mapping - Research

**Researched:** 2026-05-03
**Domain:** Zoho Calendar API parsing, Monarch Money / Mercury balance fetching, YAML-based account mapping
**Confidence:** HIGH

## Summary

Phase 1 extends the existing `zoho_calendar_payments.py` script to parse structured data from Zoho Calendar event titles and descriptions, map each payment to a funding account via a nickname lookup in `payments.yaml`, flag variable-amount payments, and fetch real-time balances from both Monarch Money and Mercury in a single run.

The codebase already has working integrations for all three external APIs (Zoho Calendar, Monarch Money, Mercury). The primary work is adding structured parsing logic, account resolution, and variable-payment handling on top of existing, proven code. No new external libraries are required -- the existing `requests`, `monarchmoney`, and `pyyaml` stack covers everything.

**Primary recommendation:** Extend `zoho_calendar_payments.py` with three new capabilities (title parsing, notes-field account mapping, variable-balance lookup) and add nickname/alias fields to `payments.yaml` accounts. Reuse `coverage_report.py`'s balance-fetching functions directly.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Strict parsing -- event titles MUST match "Name - $Amount" format. Non-matching events cause a collected failure (not silent skip).
- **D-02:** Dedicated calendar assumption -- ZOHO_CALENDAR_ID points to a payments-only calendar. Every event on it is treated as a bill.
- **D-03:** Fail mode is collect-and-report -- process all events, collect failures, then exit non-zero with a summary of which events failed and why.
- **D-04:** New parsing logic extends `zoho_calendar_payments.py` (not a new module). Keep one file per data source.
- **D-05:** Notes field contains structured data with "Fund:" prefix identifying the funding account (free-text name).
- **D-06:** Account name resolution uses nickname/alias mappings defined in payments.yaml. Claude's discretion on exact matching strategy.
- **D-07:** Funding account in notes is REQUIRED. Missing notes = strict failure, reported alongside parse errors.
- **D-08:** Exception: a special keyword (e.g., "NONE" or "N/A") in notes explicitly marks an event as having no funding account.
- **D-09:** Variable payments identified by a "VARIABLE" keyword in the event notes field.
- **D-10:** Notes field is structured: `Fund: <account> | Source: <monarch_account> | VARIABLE` -- contains funding account, source account (for balance lookup), and variable marker.
- **D-11:** For variable payments, the tool pulls the real current balance from Monarch using the "Source:" account nickname, replacing the estimate in the title.
- **D-12:** Calendar title update is opt-in via `--update-calendar` flag. Default is read-only.
- **D-13:** Account nicknames in payments.yaml map human-readable names to actual Monarch account identifiers.
- **D-14:** Data flows between Phase 1 and Phase 2 as in-memory Python dicts (function calls, no intermediate file).
- **D-15:** Payments are assumed paid once their calendar date passes -- no confirmation tracking.
- **D-16:** CLI output groups payments by funding account (heading per account, bills listed under each).

### Claude's Discretion
- Account matching implementation detail (substring vs alias lookup -- simplest reliable approach)
- Whether Phase 1 CLI shows current balances alongside payments or defers that to Phase 2

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MAP-01 | System extracts bill-to-account mapping from Zoho Calendar event notes field | Notes field = Zoho API "description" field. Structured format `Fund: <account>` parsed with simple string splitting. Existing `fetch_event_detail()` already retrieves descriptions. |
| MAP-02 | System parses bill name and amount from Zoho Calendar event title | Title format "Name - $Amount" parsed with regex. Existing code already has `event.get("title")`. |
| MAP-03 | Variable-amount payments flagged as dynamic | "VARIABLE" keyword in description field triggers flag. Variable payments pull real balance from Monarch via Source field. |
| FCST-04 | Forecast pulls real-time balances from Monarch Money and Mercury | `fetch_monarch_balances()` and `fetch_mercury_balances()` already exist in `coverage_report.py`. Import and reuse directly. |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Calendar event parsing | CLI script (local) | -- | Single-user CLI tool, no server |
| Account name resolution | CLI script (local) | YAML config file | Nicknames stored in payments.yaml, resolved at runtime |
| Balance fetching (Monarch) | External API (Monarch) | CLI script (local) | Async API call, results used locally |
| Balance fetching (Mercury) | External API (Mercury) | CLI script (local) | Sync API call, results used locally |
| Calendar write-back | External API (Zoho) | CLI script (local) | Optional PUT to update event title |
| Variable payment flagging | CLI script (local) | -- | Pure logic based on parsed description field |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| requests | 2.32.3 | HTTP calls to Zoho and Mercury APIs | Already installed, used throughout codebase [VERIFIED: pip3 show] |
| monarchmoney | 0.1.15 | Monarch Money API client (async) | Already installed, used in coverage_report.py and monarch_balances.py [VERIFIED: pip3 show] |
| pyyaml | 6.0.3 | Parse payments.yaml | Already installed [VERIFIED: pip3 show] |
| python-dotenv | (installed) | Load .env for API credentials | Already used in all scripts [VERIFIED: codebase] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| python-dateutil | 2.9.0 | Date parsing extensions | Already installed, available if needed for date edge cases [VERIFIED: pip3 show] |
| re (stdlib) | -- | Regex for title parsing | Always -- parse "Name - $Amount" format |
| argparse (stdlib) | -- | CLI argument handling | Already used in existing script |
| asyncio (stdlib) | -- | Run Monarch async calls | Already used in coverage_report.py |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Simple string split for notes | Regex for notes | String split on " | " is simpler and sufficient for the fixed format |
| Nickname dict in payments.yaml | Separate nicknames.yaml | Extra file contradicts D-04 pattern (one file per data source); payments.yaml already has account definitions |

**Installation:**
```bash
# No new packages needed. Existing requirements.txt covers everything.
pip install -r requirements.txt
```

**Version verification:** All versions confirmed via `pip3 show` on the local environment. No new dependencies to install. [VERIFIED: pip3 show]

## Architecture Patterns

### System Architecture Diagram

```
Zoho Calendar API                   Monarch Money API          Mercury API
      |                                    |                       |
      | GET events (title + description)   | get_accounts()        | GET /accounts
      v                                    v                       v
+------------------------------------------------------------------+
|                  zoho_calendar_payments.py                        |
|                                                                   |
|  1. fetch_events() -----> raw events list                        |
|                              |                                    |
|  2. parse_event_title() --> {name, amount}                       |
|                              |                                    |
|  3. parse_event_notes() --> {fund_account, source_account,       |
|                               is_variable}                        |
|                              |                                    |
|  4. resolve_funding_account() --> match nickname to               |
|     (uses payments.yaml)         account ID                       |
|                              |                                    |
|  5. For VARIABLE: fetch balance from Monarch/Mercury             |
|     (reuses coverage_report functions)                            |
|                              |                                    |
|  6. Build structured payment dicts                                |
|     (in-memory, returned to caller for Phase 2)                   |
|                              |                                    |
|  7. CLI output: group by funding account                          |
|                              |                                    |
|  8. Optional: --update-calendar --> PUT to Zoho API               |
+------------------------------------------------------------------+
                              |
                              v
                    payments.yaml (accounts + nicknames)
```

### Recommended Project Structure
```
banking/
├── zoho_calendar_payments.py  # Extended with parsing + mapping (D-04)
├── coverage_report.py         # Existing -- balance functions reused
├── monarch_balances.py        # Existing -- unchanged
├── payments.yaml              # Extended with account nicknames (D-13)
├── Taskfile.yml               # New task entry for forecast
├── requirements.txt           # Unchanged
├── .env                       # API credentials
└── .cache/                    # File-based API response cache
```

### Pattern 1: Collect-and-Report Error Handling (D-03)
**What:** Process all events, collect parse/validation errors into a list, then report all failures at the end and exit non-zero if any occurred.
**When to use:** Always -- this is the locked error handling strategy.
**Example:**
```python
# Source: D-03 decision
def process_events(events, config):
    payments = []
    errors = []

    for event in events:
        try:
            parsed = parse_event_title(event.get("title", ""))
        except ValueError as e:
            errors.append(f"Title parse error for '{event.get('title', '?')}': {e}")
            continue

        try:
            notes = parse_event_notes(event.get("description", ""))
        except ValueError as e:
            errors.append(f"Notes parse error for '{event.get('title', '?')}': {e}")
            continue

        payments.append({**parsed, **notes, "event": event})

    return payments, errors
```

### Pattern 2: Title Parsing with Regex (D-01)
**What:** Parse "Name - $Amount" from event titles using a regex with named groups.
**When to use:** Every event title.
**Example:**
```python
import re

TITLE_PATTERN = re.compile(r'^(.+?)\s*-\s*\$?([\d,]+(?:\.\d{2})?)\s*$')

def parse_event_title(title):
    """Parse 'Name - $Amount' from event title. Raises ValueError on mismatch."""
    match = TITLE_PATTERN.match(title.strip())
    if not match:
        raise ValueError(f"Title does not match 'Name - $Amount' format: '{title}'")
    name = match.group(1).strip()
    amount = float(match.group(2).replace(',', ''))
    return {"name": name, "amount": amount}
```

### Pattern 3: Notes Field Parsing (D-10)
**What:** Parse structured notes: `Fund: <account> | Source: <monarch_account> | VARIABLE`
**When to use:** Every event's description field.
**Example:**
```python
def parse_event_notes(description):
    """Parse structured notes from event description.

    Format: Fund: <account> [| Source: <account>] [| VARIABLE]
    Returns dict with fund_account, source_account (optional), is_variable.
    """
    if not description or not description.strip():
        raise ValueError("Missing description/notes field (required)")

    text = description.strip()

    # Check for explicit no-funding-account marker
    if text.upper() in ("NONE", "N/A"):
        return {"fund_account": None, "source_account": None, "is_variable": False,
                "no_funding": True}

    parts = [p.strip() for p in text.split("|")]
    result = {"fund_account": None, "source_account": None, "is_variable": False,
              "no_funding": False}

    for part in parts:
        if part.upper() == "VARIABLE":
            result["is_variable"] = True
        elif part.upper().startswith("FUND:"):
            result["fund_account"] = part[5:].strip()
        elif part.upper().startswith("SOURCE:"):
            result["source_account"] = part[7:].strip()

    if result["fund_account"] is None and not result["no_funding"]:
        raise ValueError(f"No 'Fund:' field found in notes: '{text}'")

    return result
```

### Pattern 4: Account Nickname Resolution (D-06, D-13)
**What:** Map free-text account names from calendar notes to account IDs in payments.yaml using a nickname/alias lookup.
**When to use:** After parsing notes, before building payment dict.
**Recommendation (Claude's discretion):** Use a simple case-insensitive dictionary lookup. Nicknames are defined per-account in payments.yaml. Build a lookup dict at startup: `{nickname.lower(): account_id}`. This is simpler and more predictable than substring matching.
**Example:**
```python
def build_nickname_lookup(config):
    """Build {nickname: account_id} from payments.yaml accounts."""
    lookup = {}
    for account in config.get("accounts", []):
        acct_id = account["id"]
        # Always add the account ID itself
        lookup[acct_id.lower()] = acct_id
        # Add display name
        if account.get("name"):
            lookup[account["name"].lower()] = acct_id
        # Add last4 variants (e.g., "7667", "Chase 7667")
        if account.get("last4"):
            lookup[account["last4"]] = acct_id
            if account.get("institution"):
                lookup[f"{account['institution'].lower()} {account['last4']}"] = acct_id
        # Add explicit nicknames from new field
        for nick in account.get("nicknames", []):
            lookup[nick.lower()] = acct_id
    return lookup

def resolve_account(name, lookup):
    """Resolve a free-text account name to an account ID."""
    key = name.strip().lower()
    if key in lookup:
        return lookup[key]
    raise ValueError(f"Unknown account nickname: '{name}'. "
                     f"Add it to payments.yaml account nicknames.")
```

### Pattern 5: Reusing Balance Fetchers (FCST-04)
**What:** Import and call `fetch_monarch_balances()` and `fetch_mercury_balances()` from `coverage_report.py`.
**When to use:** When variable payments need real balances, and for showing current balances.
**Example:**
```python
# Source: existing coverage_report.py pattern
from coverage_report import fetch_monarch_balances, fetch_mercury_balances, resolve_balance

async def get_all_balances():
    monarch = await fetch_monarch_balances()
    mercury = fetch_mercury_balances()
    return monarch, mercury
```

### Anti-Patterns to Avoid
- **Silent skipping:** D-01/D-03 explicitly forbid silently skipping unparseable events. Every failure must be collected and reported.
- **Intermediate files between phases:** D-14 says data flows as in-memory dicts. Do not write JSON/CSV between Phase 1 and Phase 2.
- **Separate module for parsing:** D-04 says extend `zoho_calendar_payments.py`, not create a new file.
- **Hard-coded account mappings:** Use payments.yaml nicknames, not if/elif chains in code.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Monarch Money API client | Custom GraphQL client | `monarchmoney` library (0.1.15) | Already handles auth, session, GraphQL queries [VERIFIED: installed] |
| Mercury API auth | Custom OAuth flow | Simple Bearer token via `requests` | Mercury uses API keys, not OAuth [VERIFIED: existing code] |
| Zoho OAuth token refresh | Custom token management | Existing `get_access_token()` function | Already handles caching, error handling [VERIFIED: codebase] |
| YAML parsing | Custom config parser | PyYAML `safe_load()` | Already used throughout codebase [VERIFIED: codebase] |

**Key insight:** All three API integrations already exist and work. This phase is purely about adding parsing/mapping logic on top, not building new API clients.

## Common Pitfalls

### Pitfall 1: Zoho "description" vs "notes" terminology
**What goes wrong:** Zoho Calendar API calls the event body text "description", not "notes". The CONTEXT.md refers to "notes field" but the actual API field is `description`.
**Why it happens:** Natural language confusion between Zoho's field naming and common calendar terminology.
**How to avoid:** Always use `event.get("description", "")` when accessing the structured data. In user-facing messages and YAML comments, "notes" is fine, but code must use "description".
**Warning signs:** Empty string returned when accessing `event.get("notes")`.
[VERIFIED: existing code in `zoho_calendar_payments.py` line 282 uses `event.get("description", "")`]

### Pitfall 2: Monarch Money base URL migration
**What goes wrong:** The monarchmoney library defaults to the old Monarch Money URL; actual API is at `api.monarch.com`.
**Why it happens:** Library hasn't been updated.
**How to avoid:** The codebase already patches this: `MonarchMoneyEndpoints.BASE_URL = "https://api.monarch.com"` -- keep doing this.
**Warning signs:** 404 or connection errors from Monarch.
[VERIFIED: coverage_report.py line 38, monarch_balances.py line 33]

### Pitfall 3: Zoho event detail requires separate API call
**What goes wrong:** The list events endpoint does NOT return description/notes. You must call the individual event detail endpoint.
**Why it happens:** Zoho optimizes list responses by omitting large fields.
**How to avoid:** Already handled -- `fetch_events()` calls `fetch_event_detail()` for each event. This is cached for 24 hours.
**Warning signs:** Empty descriptions when you only use list endpoint data.
[VERIFIED: zoho_calendar_payments.py lines 214-228]

### Pitfall 4: Zoho PUT requires etag
**What goes wrong:** Updating an event title (for `--update-calendar` flag) fails without the `etag` field.
**Why it happens:** Zoho uses optimistic concurrency control.
**How to avoid:** The event detail response includes an `etag` field. Store it and send it back with the PUT request.
**Warning signs:** 400 or 412 error on PUT.
[CITED: https://www.zoho.com/calendar/help/api/put-update-event.html]

### Pitfall 5: 12 of 20 payments have null funding_account
**What goes wrong:** Running the tool before populating payments.yaml with nicknames will produce many "unknown account" errors.
**Why it happens:** Initial data seeding was incomplete.
**How to avoid:** Treat this as expected -- the collect-and-report pattern (D-03) will surface these. User needs to populate funding_account fields and calendar event descriptions.
**Warning signs:** High error count on first run.
[VERIFIED: payments.yaml -- counted null funding_account entries]

### Pitfall 6: Credit card balances are negative in Monarch
**What goes wrong:** Monarch reports credit card balances as negative numbers (what you owe). Using the raw number for a "variable payment amount" would produce a negative payment.
**Why it happens:** Accounting convention -- liabilities are negative.
**How to avoid:** Use `abs(balance)` when replacing variable payment amounts with Monarch balance data.
**Warning signs:** Negative dollar amounts in output.
[VERIFIED: coverage_report.py lines 220-222 already handles this with `abs(balance)`]

## Code Examples

### Zoho Calendar PUT (update event title)
```python
# Source: https://www.zoho.com/calendar/help/api/put-update-event.html
def update_event_title(access_token, calendar_id, event_uid, new_title, etag):
    """Update a Zoho Calendar event title (for --update-calendar flag)."""
    url = f"{ZOHO_CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_uid}"
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    params = {
        "eventdata": json.dumps({
            "title": new_title,
            "etag": etag,
        })
    }
    resp = requests.put(url, headers=headers, params=params, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to update event: HTTP {resp.status_code}: {resp.text}")
    return resp.json()
```

### payments.yaml nickname extension
```yaml
# New field: nicknames (list of alternative names for account resolution)
accounts:
  - id: chase-ink-7667
    name: "Chase Ink"
    institution: "Chase"
    last4: "7667"
    nicknames: ["Chase 7667", "Ink 7667", "Chase Ink"]
    # ... existing fields ...
```

### CLI output grouped by funding account (D-16)
```python
def display_grouped_payments(payments, errors):
    """Display payments grouped by funding account."""
    grouped = {}
    for p in payments:
        key = p.get("fund_account_id", "UNMATCHED")
        grouped.setdefault(key, []).append(p)

    for acct_id, acct_payments in sorted(grouped.items()):
        print(f"\n  {acct_id}")
        print(f"  {'─' * 40}")
        for p in acct_payments:
            flag = " ~estimate" if p.get("is_variable") else ""
            print(f"    {p['name']}: ${p['amount']:,.2f}{flag}")

    if errors:
        print(f"\n  ERRORS ({len(errors)}):")
        for err in errors:
            print(f"    - {err}", file=sys.stderr)
        sys.exit(1)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `monarchmoney` with old URL | Patch `BASE_URL` to `api.monarch.com` | Mid-2025 | Already done in codebase; library still ships old URL [VERIFIED: codebase] |
| `temporalio/web` for Temporal UI | `temporalio/ui` | 3 years ago | Not relevant to Phase 1 but noted in CLAUDE.md |

**Deprecated/outdated:**
- monarchmoney 0.1.15 may be behind latest; the `MonarchMoneyEndpoints.BASE_URL` patch is a known workaround [VERIFIED: pip3 show + codebase]

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Zoho Calendar event detail response includes an `etag` field needed for PUT updates | Code Examples | --update-calendar flag would fail; would need to find etag from a different endpoint |
| A2 | Monarch `get_accounts()` returns `currentBalance` as a numeric field (negative for credit cards) | Pitfall 6 | Variable payment balance lookup would return wrong data type |
| A3 | The Zoho PUT endpoint accepts `eventdata` as a query parameter (not request body) | Code Examples | Calendar update would fail with 400; may need to send as form data or JSON body instead |

## Open Questions

1. **Are all payments actually in the Zoho Calendar yet?**
   - What we know: 12 of 20 payments in payments.yaml have `zoho_match: null` and notes saying "From Google Sheet - not yet in Zoho Calendar"
   - What's unclear: Whether the user has added these to the calendar since the initial data seeding
   - Recommendation: Phase 1 only processes what it finds in the calendar. Missing payments are not errors -- they simply won't appear. The user needs to add them to the calendar with the structured title/notes format before they can be processed.

2. **Exact Zoho PUT request format**
   - What we know: The docs show `eventdata` as a JSON parameter, and `etag` is mandatory
   - What's unclear: Whether `eventdata` goes as a query parameter, form field, or JSON body (docs are ambiguous) [ASSUMED]
   - Recommendation: Try query parameter first (matching the docs example); if that fails, try JSON body. The `--update-calendar` flag is opt-in so this can be tested iteratively.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | Everything | Yes | 3.12.2 | -- |
| requests | Zoho + Mercury API | Yes | 2.32.3 | -- |
| monarchmoney | Monarch balance fetch | Yes | 0.1.15 | -- |
| pyyaml | payments.yaml parsing | Yes | 6.0.3 | -- |
| python-dotenv | .env loading | Yes | (installed) | -- |
| python-dateutil | Date parsing | Yes | 2.9.0 | -- |
| task (Taskfile) | CLI runner | Yes | 3.41.0 | Direct python invocation |
| tabulate | Table formatting (STATE.md mention) | No | -- | Not needed for Phase 1; use manual formatting |
| Zoho Calendar API | Event fetching + optional update | Yes (via credentials) | v1 | -- |
| Monarch Money API | Balance lookup | Yes (via MONARCH_TOKEN) | -- | -- |
| Mercury API | Business balance lookup | Yes (via API keys) | v1 | -- |

**Missing dependencies with no fallback:**
- None

**Missing dependencies with fallback:**
- `tabulate` not installed but not needed for Phase 1 output (manual string formatting matches existing codebase pattern)

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Yes | OAuth2 refresh tokens (Zoho), API keys (Mercury), session tokens (Monarch) -- all via env vars, never hardcoded |
| V3 Session Management | No | CLI tool, no user sessions |
| V4 Access Control | No | Single-user tool |
| V5 Input Validation | Yes | Strict title/notes parsing with explicit format validation (D-01) |
| V6 Cryptography | No | No encryption needed |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| API credential exposure in .env | Information Disclosure | .env in .gitignore (already done), env vars loaded at runtime |
| Cached tokens on disk (.cache/) | Information Disclosure | .cache in .gitignore, file-based cache with TTL expiry |
| Malformed calendar data injection | Tampering | Strict regex parsing (D-01) rejects non-matching titles |

## Sources

### Primary (HIGH confidence)
- Codebase files: `zoho_calendar_payments.py`, `coverage_report.py`, `monarch_balances.py`, `payments.yaml` -- read and analyzed directly
- `pip3 show` output for all package versions
- `.env.example` and `Taskfile.yml` for project configuration

### Secondary (MEDIUM confidence)
- [Zoho Calendar PUT Update Event API](https://www.zoho.com/calendar/help/api/put-update-event.html) -- endpoint format, etag requirement, parameters
- [Zoho Calendar Events API](https://www.zoho.com/calendar/help/api/events-api.html) -- event field overview
- [monarchmoney GitHub](https://github.com/hammem/monarchmoney) -- library capabilities

### Tertiary (LOW confidence)
- Zoho PUT `eventdata` parameter format (query param vs body) -- docs example suggests query param but this is ambiguous [ASSUMED: A3]

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already installed and working in existing code
- Architecture: HIGH -- extending existing working code, not building new integrations
- Pitfalls: HIGH -- identified from direct code analysis and API documentation
- Zoho PUT format: MEDIUM -- docs consulted but parameter delivery format is ambiguous

**Research date:** 2026-05-03
**Valid until:** 2026-06-03 (stable -- no fast-moving dependencies)
