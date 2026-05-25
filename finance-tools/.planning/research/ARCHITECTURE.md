# Architecture Patterns

**Domain:** Personal finance payment forecasting CLI
**Researched:** 2026-05-03

## Recommended Architecture

### Overview

The forecasting module should be a **new orchestrator script** (`payment_forecast.py`) that imports shared logic extracted from the existing scripts, rather than bolting forecast logic onto `coverage_report.py`. The current codebase has three independent scripts with duplicated patterns (env loading, balance fetching, display formatting). The forecast feature is the right moment to extract shared modules without rewriting existing scripts -- the existing scripts continue to work as-is while the new forecast script uses extracted helpers.

```
                   CLI Entry Points
            ┌──────────┬──────────┬──────────────┐
            │ monarch  │ zoho     │ coverage     │
            │ balances │ calendar │ report       │
            │ .py      │ .py      │ .py          │
            └──────────┴──────────┴──────────────┘
                                        │
            ┌───────────────────────────────────────┐
            │       payment_forecast.py             │
            │  (NEW orchestrator entry point)        │
            │                                       │
            │  1. Load bill_mapping.yaml            │
            │  2. Fetch Zoho events (dates)         │
            │  3. Match events → bills → accounts   │
            │  4. Fetch balances (Monarch+Mercury)  │
            │  5. Project balances forward           │
            │  6. Detect shortfalls                 │
            │  7. Render report / send email        │
            └───────────┬───────────────────────────┘
                        │ imports
            ┌───────────┴───────────────────────────┐
            │         lib/  (shared modules)         │
            │                                       │
            │  balances.py   — Monarch + Mercury    │
            │  zoho.py       — Calendar + caching   │
            │  config.py     — YAML loading, env    │
            │  cache.py      — TTL file cache       │
            │  display.py    — Terminal formatting   │
            └───────────────────────────────────────┘
                        │ reads
            ┌───────────┴───────────────────────────┐
            │         Configuration Files            │
            │                                       │
            │  payments.yaml      — accounts, bills │
            │  bill_mapping.yaml  — bill→account    │
            │  .env               — API credentials │
            └───────────────────────────────────────┘
```

### Why This Shape

1. **Existing scripts must not break.** The three existing CLI scripts are working and in use. Extracting shared code into `lib/` and having existing scripts import from there (or continue working standalone until a future refactor) preserves stability.

2. **The forecast is a new workflow, not a patch.** The coverage report uses `payments.yaml` day-of-month math. The forecast uses Zoho Calendar as the date authority. These are fundamentally different date-resolution strategies and should not share a code path for due-date calculation.

3. **Bill matching is the hard new problem.** Matching a Zoho Calendar event title ("Progressive Insurance Due") to a `payments.yaml` entry ("Progressive (Insurance)") to an account is fuzzy string matching. This logic deserves its own module, not burial inside a script.

## Component Boundaries

| Component | Responsibility | Inputs | Outputs |
|-----------|---------------|--------|---------|
| `payment_forecast.py` | Orchestrate the full forecast pipeline | CLI args (--days, --output) | Terminal report, optional email |
| `lib/zoho.py` | Zoho Calendar auth + event fetching with cache | Credentials, date range | List of calendar events (dicts) |
| `lib/balances.py` | Unified balance resolution from Monarch + Mercury | Credentials, account definitions | Dict of account_id -> balance |
| `lib/matcher.py` | Match Zoho events to payments.yaml entries | Events list, payments config | List of matched (event, payment, account) tuples |
| `lib/projector.py` | Forward-project balances given upcoming payments | Current balances, matched payments with dates | Timeline of projected balances per account |
| `lib/config.py` | Load YAML configs, validate, merge | File paths | Validated config dicts |
| `lib/cache.py` | TTL-based JSON file cache (extracted from zoho script) | Cache key, TTL, value | Cached/fresh value |
| `lib/display.py` | Terminal report formatting, summary generation | Projection results, shortfall list | Formatted string / stdout |
| `lib/alerts.py` | Email alert via Gmail when shortfalls detected | Shortfall data, email config | Email sent (or dry-run output) |
| `bill_mapping.yaml` | Declares which debit account funds each bill | (config file) | (config file) |

### What Does NOT Belong in This Architecture

- **No database.** File-based config and caching is the established pattern. Adding SQLite or similar adds complexity with no benefit for a single-user CLI tool.
- **No web server.** CLI-first, email as secondary output. No Flask/FastAPI.
- **No scheduling daemon.** Use `cron` or `task` runner for daily runs. The tool is stateless -- run it, get output, done.
- **No shared state between runs.** Each invocation is independent. No "last run" tracking needed.

## Data Flow

### Forecast Pipeline (the new core workflow)

```
Step 1: LOAD CONFIG
  bill_mapping.yaml ──→ { bill_name: { funding_account, amount_override } }
  payments.yaml     ──→ { accounts[], payments[], transfer_rules }

Step 2: FETCH CALENDAR (date authority)
  Zoho Calendar API ──→ [ { title, start_date, description } ]
  (cached 15 min)

Step 3: MATCH EVENTS TO BILLS
  For each Zoho event:
    1. Try zoho_match field from payments.yaml (exact prefix match)
    2. Fallback: fuzzy title match against payment names
    3. If matched: resolve funding_account from bill_mapping.yaml
    4. If unmatched: flag as "unknown bill"
  Output: [ { event, payment, funding_account, due_date, amount } ]

Step 4: FETCH BALANCES (balance authority)
  Monarch Money API ──→ { display_name: balance }
  Mercury API       ──→ { account_id: balance }
  Resolve: account_id → balance (Mercury first, then Monarch)

Step 5: PROJECT FORWARD
  For each funding account:
    starting_balance = current_balance
    For each payment sorted by due_date:
      projected_balance -= payment.amount
      record (date, payment_name, projected_balance)
    Flag shortfall if projected_balance < 0 at any point

Step 6: RENDER
  Terminal: Per-account timeline with balance waterfall
  Email (optional): Summary with shortfall alerts only
```

### Key Data Structures

```python
# Matched payment (output of matcher)
MatchedPayment = {
    "event_title": str,       # from Zoho: "Progressive Insurance Due"
    "payment_name": str,      # from payments.yaml: "Progressive (Insurance)"
    "amount": float,          # resolved: from bill_mapping override or payments.yaml
    "due_date": datetime,     # from Zoho Calendar event date (NOT day_of_month)
    "funding_account_id": str,# from bill_mapping: "boa-business-1778"
    "autopay": bool | None,   # from payments.yaml
    "match_confidence": str,  # "exact" | "fuzzy" | "manual"
}

# Account projection (output of projector)
AccountProjection = {
    "account_id": str,
    "account_name": str,
    "current_balance": float,
    "payments": [              # sorted by due_date
        {"date": datetime, "name": str, "amount": float, "balance_after": float}
    ],
    "lowest_balance": float,
    "shortfall": bool,
    "shortfall_date": datetime | None,
}
```

## Patterns to Follow

### Pattern 1: Extract-on-Use for Shared Code

**What:** When building `payment_forecast.py`, extract functions from existing scripts into `lib/` modules. Leave the existing scripts importing from `lib/` OR keep them standalone initially and migrate later.

**When:** Phase 1 should extract `cache.py` and `config.py` (low risk). Phase 2 extracts `balances.py` and `zoho.py` (API-touching code, test first).

**Why:** Avoids a big-bang refactor. Each extraction is testable in isolation.

```python
# lib/cache.py -- extracted from zoho_calendar_payments.py lines 52-78
# Identical logic, just moved to importable location

# lib/balances.py -- extracted from coverage_report.py lines 95-167
# resolve_balance(), fetch_monarch_balances(), fetch_mercury_balances()
```

### Pattern 2: Zoho-Match Field as Primary Matcher

**What:** `payments.yaml` already has a `zoho_match` field on each payment. Use this as the primary matching key: compare event title prefix against `zoho_match` value.

**When:** Always try `zoho_match` first. Only fall back to fuzzy matching when `zoho_match` is null.

**Why:** The codebase already seeds this field. It is deterministic and user-controllable. Fuzzy matching is a fallback, not the primary strategy.

```python
def match_event_to_payment(event_title: str, payments: list) -> dict | None:
    # Exact prefix match on zoho_match field
    for p in payments:
        zm = p.get("zoho_match")
        if zm and event_title.lower().startswith(zm.lower()):
            return p
    # Fuzzy fallback (optional, phase 2)
    return None
```

### Pattern 3: Bill Mapping as Overlay Config

**What:** `bill_mapping.yaml` extends (not replaces) `payments.yaml`. It adds funding_account overrides and amount overrides for bills that are currently `null` in payments.yaml.

**When:** Loading config: merge bill_mapping onto payments data. Bill mapping wins for `funding_account` when both are set.

**Why:** Many payments in `payments.yaml` have `funding_account: null` and need review. The bill mapping is where those decisions get recorded, without editing the original payments.yaml (which was seeded from external sources).

```yaml
# bill_mapping.yaml
mappings:
  - payment: "BoA Platinum Plus 1 (min)"
    funding_account: cap1-recurring-4354
    amount_override: null  # use payments.yaml amount

  - payment: "Chase Credit Card"
    funding_account: cap1-recurring-4354
    amount_override: 156.00
```

### Pattern 4: Async Balance Fetching

**What:** Fetch Monarch and Mercury balances concurrently using `asyncio.gather()`.

**When:** Always, during the balance-fetch step.

**Why:** `coverage_report.py` already uses async for Monarch. Mercury is sync but fast. Wrapping Mercury in `asyncio.to_thread()` keeps the pattern consistent and shaves a few seconds off execution.

```python
async def fetch_all_balances(config):
    monarch_task = fetch_monarch_balances()
    mercury_task = asyncio.to_thread(fetch_mercury_balances)
    monarch, mercury = await asyncio.gather(monarch_task, mercury_task)
    return monarch, mercury
```

## Anti-Patterns to Avoid

### Anti-Pattern 1: Monolithic Forecast Script

**What:** Putting all forecast logic (Zoho fetch, balance fetch, matching, projection, display) into a single 500-line script.

**Why bad:** This is exactly how the three existing scripts grew -- each is self-contained and duplicates cache/env/API patterns. The forecast script is the most complex workflow yet and will be unmaintainable as a single file.

**Instead:** Use the `lib/` module structure from the start. The orchestrator script should be ~50 lines of glue code.

### Anti-Pattern 2: Modifying payments.yaml Programmatically

**What:** Having the forecast tool write back to `payments.yaml` (e.g., updating amounts from API data).

**Why bad:** `payments.yaml` was seeded from external sources and is human-edited. Programmatic writes risk corrupting comments, formatting, and manual notes. The file has extensive inline comments that YAML round-tripping will destroy.

**Instead:** Read-only access to `payments.yaml`. Use `bill_mapping.yaml` for overrides.

### Anti-Pattern 3: Real-Time Balance in Projection Math

**What:** Fetching balance once, then treating it as the balance at a future date.

**Why bad:** A balance fetched today is a point-in-time snapshot. Payments already processed but not yet reflected, pending deposits, and other transactions make the balance unreliable for projection beyond 7-14 days.

**Instead:** Document the "balance staleness" assumption clearly. Show "as of" timestamp on projections. For 30+ day projections, add a disclaimer that accuracy decreases with time horizon.

## Suggested Build Order

The architecture has clear dependency layers that dictate build order:

### Phase 1: Foundation (shared modules + config)

Build first because everything depends on these:

1. **`lib/cache.py`** -- Extract from `zoho_calendar_payments.py`. Zero-risk refactor.
2. **`lib/config.py`** -- YAML loading for `payments.yaml` + new `bill_mapping.yaml`.
3. **`bill_mapping.yaml`** -- Define the schema. Populate funding_account for the ~12 payments currently set to `null`.
4. **Tests for config loading** -- Validate YAML schema, test merge logic.

**Depends on:** Nothing. Can start immediately.

### Phase 2: Data fetching (extracted + tested)

Build second because projection needs data:

5. **`lib/zoho.py`** -- Extract Zoho auth + event fetching from `zoho_calendar_payments.py`.
6. **`lib/balances.py`** -- Extract balance resolution from `coverage_report.py`.
7. **`lib/matcher.py`** -- Match Zoho events to payments using `zoho_match` field.
8. **Integration tests** -- Verify extracted modules produce same output as original scripts.

**Depends on:** Phase 1 (config, cache).

### Phase 3: Forecast engine (the new logic)

Build third -- this is the core new value:

9. **`lib/projector.py`** -- Forward-project balances per funding account over time horizon.
10. **`lib/display.py`** -- Terminal report with balance waterfall per account.
11. **`payment_forecast.py`** -- Orchestrator CLI entry point.
12. **Taskfile.yml entries** -- `task forecast`, `task forecast:week`, `task forecast:month`.

**Depends on:** Phase 2 (data fetching, matching).

### Phase 4: Alerts + daily automation

Build last -- depends on forecast working correctly:

13. **`lib/alerts.py`** -- Gmail email alerts via existing MCP integration.
14. **Daily summary mode** -- `--email` flag that sends report instead of printing.
15. **Cron/schedule integration** -- Document how to run daily.

**Depends on:** Phase 3 (working forecast output to format into email).

### Dependency Graph

```
Phase 1: cache.py, config.py, bill_mapping.yaml
    │
    ▼
Phase 2: zoho.py, balances.py, matcher.py
    │
    ▼
Phase 3: projector.py, display.py, payment_forecast.py
    │
    ▼
Phase 4: alerts.py, email mode, scheduling
```

## Integration Strategy with Existing Scripts

The three existing scripts (`monarch_balances.py`, `zoho_calendar_payments.py`, `coverage_report.py`) should remain functional throughout. Two approaches, in order of preference:

**Approach A (recommended): Parallel operation.** New `lib/` modules are written fresh, extracting logic but not modifying existing scripts. Existing scripts continue to work as standalone. Over time, they can optionally be updated to import from `lib/` to reduce duplication.

**Approach B (deferred): Refactor existing scripts.** After the forecast tool is stable, update the three existing scripts to import from `lib/` instead of having inline implementations. This is a cleanup step, not a prerequisite.

Approach A is recommended because it eliminates risk of breaking working tools during development. The duplication between `lib/` and existing scripts is temporary and acceptable.

## Directory Structure After Build

```
banking/
├── lib/                        # NEW: shared modules
│   ├── __init__.py
│   ├── cache.py               # TTL file cache (extracted)
│   ├── config.py              # YAML config loading
│   ├── zoho.py                # Zoho Calendar API client (extracted)
│   ├── balances.py            # Monarch + Mercury balance fetching (extracted)
│   ├── matcher.py             # Event-to-payment matching
│   ├── projector.py           # Balance forward-projection
│   ├── display.py             # Terminal report formatting
│   └── alerts.py              # Gmail email alerts
├── payment_forecast.py         # NEW: forecast CLI entry point
├── bill_mapping.yaml           # NEW: bill-to-funding-account config
├── zoho_calendar_payments.py   # UNCHANGED (existing)
├── monarch_balances.py         # UNCHANGED (existing)
├── coverage_report.py          # UNCHANGED (existing)
├── payments.yaml               # UNCHANGED (existing)
├── test_integration.py         # EXISTING
├── test_forecast.py            # NEW: forecast tests
├── test_matcher.py             # NEW: matcher unit tests
├── Taskfile.yml                # UPDATED: add forecast tasks
├── requirements.txt            # UNCHANGED (no new deps needed)
└── .env                        # UNCHANGED
```

## Scalability Considerations

Not applicable in the traditional sense (this is a single-user CLI tool), but relevant concerns:

| Concern | Now (1 user) | If Accounts Grow (50+ accounts) | If Forecast Horizon Grows (90+ days) |
|---------|-------------|--------------------------------|--------------------------------------|
| API rate limits | Not an issue (~5 calls per run) | Zoho event detail fetching could hit limits if many events | More events to fetch, cache helps |
| Execution time | <5 seconds | Balance resolution loop is O(n) per account, fine at 50 | Same -- projection is O(payments * accounts), trivial |
| Config complexity | payments.yaml is manageable | YAML gets unwieldy past 50 accounts -- consider splitting | No impact |
| Projection accuracy | Reasonable for 7-14 days | Same | Degrades significantly past 30 days -- document this |

## Sources

- Existing codebase analysis (primary source -- all architecture decisions are grounded in the actual code patterns found in `coverage_report.py`, `zoho_calendar_payments.py`, `monarch_balances.py`, and `payments.yaml`)
- `payments.yaml` structure analysis -- 12 of 20 payments have `funding_account: null`, confirming the need for `bill_mapping.yaml` as an overlay config

---

*Architecture research: 2026-05-03*
