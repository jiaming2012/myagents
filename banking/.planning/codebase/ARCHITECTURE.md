# Architecture

**Analysis Date:** 2026-05-03

## Pattern Overview

**Overall:** Multi-service CLI agent with external API integration and local caching layer

**Key Characteristics:**
- Modular single-file services (each command is a standalone Python script)
- External API-driven: Zoho Calendar, Monarch Money, Mercury Bank APIs
- Event-driven workflow: fetch → process → display/report
- Local file-based caching with TTL-based invalidation
- Declarative payment configuration in YAML format

## Layers

**Entry Points (CLI):**
- Purpose: Command-line interfaces that serve specific business needs
- Location: Root directory (scripts at `/Users/jamal/projects/myagents/banking/*.py`)
- Contains: `main()` functions with argparse argument parsing, environment loading
- Depends on: Helper functions within same file, external libraries, `.env` configuration
- Used by: Direct shell invocation via `task` commands or manual execution

**External API Integration:**
- Purpose: Communicate with third-party financial services
- Services:
  - Zoho Calendar API (`https://calendar.zoho.com/api/v1`) - Calendar event source
  - Zoho OAuth (`https://accounts.zoho.com/oauth/v2/token`) - Token management
  - Monarch Money API (`https://api.monarch.com`) - Account aggregation
  - Mercury API (`https://api.mercury.com/api/v1`) - Bank balance queries
- Implementation: Direct HTTP requests via `requests` library
- Auth patterns: OAuth2 refresh tokens (Zoho), API key bearer tokens (Mercury), token-based auth (Monarch)

**Caching Layer:**
- Purpose: Reduce API calls and improve response time
- Location: `/Users/jamal/projects/myagents/banking/.cache/` (MD5-hashed filenames)
- TTLs:
  - Token cache: 50 minutes (Zoho tokens expire at 60min)
  - Event list cache: 15 minutes
  - Event detail cache: 24 hours
- Mechanism: JSON file storage with timestamp validation in `cache_get()` and `cache_set()` functions
- Used by: `zoho_calendar_payments.py` (token, event lists, event details)

**Data Storage:**
- Purpose: Define payment obligations and account metadata
- Location: `payments.yaml` - declarative registry of accounts and payment obligations
- Structure:
  - `accounts[]`: Account definitions with Mercury IDs, Monarch matches, metadata
  - `payments[]`: Payment schedule with amount, funding account, due dates
  - `transfer_rules`: Coverage window thresholds for reporting

**Processing & Computation:**
- Purpose: Transform raw API data into business insights
- Functions:
  - `get_upcoming_payments()` - Filter payments by date window
  - `resolve_balance()` - Match account to current balance from Monarch/Mercury
  - `format_event()` - Transform Zoho event into display format
  - `fetch_event_detail()` - Enrich event with full description from Zoho

**Presentation:**
- Purpose: Format and display results to terminal
- Functions:
  - `display_events()` - Terminal table for calendar events
  - `print_report()` - Coverage analysis with balance/due comparison
  - Account grouping by type (depository, credit, investment, loan)

## Data Flow

**Payments Workflow (zoho_calendar_payments.py):**

1. Load environment (Zoho credentials from `.env`)
2. Authenticate: refresh_token → access_token (cached 50min)
3. Fetch events: Query Zoho Calendar API for date range (cached 15min)
4. Enrich: For each event, fetch full detail including description (cached 24hr)
5. Transform: Parse Zoho datetime, extract title/time/description
6. Display: Terminal table with formatted events

**Coverage Report Workflow (coverage_report.py):**

1. Load `payments.yaml` configuration
2. Fetch balances (parallel sources):
   - Monarch Money API: All tracked accounts
   - Mercury APIs (personal + business): Bank accounts
3. Calculate upcoming: Filter payments by window (7/14/30 days)
4. Resolve balances: Match each account to current balance (Mercury > Monarch)
5. Compute coverage: balance - due_amount for each account/window
6. Display: Report with alerts for shortfalls, unassigned payments

**Balances Workflow (monarch_balances.py):**

1. Load credentials: MONARCH_TOKEN from environment or saved session
2. Authenticate: Load persisted session from `.mm/mm_session.pickle` or interactive login
3. Fetch accounts: Query Monarch Money `/accounts` endpoint
4. Group by type: Depository, Credit, Investment, Loan
5. Calculate: Sum balances to compute net worth
6. Display: Grouped table with per-account and total net worth

**State Management:**

- **Session state**: Monarch Money session pickled to `.mm/mm_session.pickle` (persistent login)
- **Cache state**: JSON files in `.cache/` with timestamp (ephemeral, TTL-based)
- **Configuration state**: `payments.yaml` as single source of truth for payment definitions

## Key Abstractions

**Account:**
- Purpose: Represents a financial account (bank, credit card, investment)
- Examples: `payments.yaml` lines 19-80 (Mercury accounts defined)
- Fields: id, name, institution, type (depository/credit/investment/loan), category (personal/business), mercury_id, monarch_match
- Used by: Coverage report to match balances to payment obligations

**Payment:**
- Purpose: Represents a recurring payment obligation
- Fields: name, amount, day_of_month, funding_account (account id), autopay, notes
- Computed: due_date (derived from day_of_month + current/next month)
- Used by: Coverage report to forecast cash needs

**Balance Resolution:**
- Purpose: Unified view of account balance across sources
- Strategy: Cascade lookup (Mercury API → Monarch Money → fuzzy name match)
- Rationale: Mercury API is authoritative for Mercury accounts; Monarch aggregates other banks

**Event:**
- Purpose: Zoho Calendar event (payment reminder)
- Source: Zoho Calendar API list endpoint (basic) + detail endpoint (full with description)
- Fields: title, dateandtime (start/end), description, uid, isallday
- Transformation: Parsed to display format with human-readable date/time

## Entry Points

**`zoho_calendar_payments.py`:**
- Location: `/Users/jamal/projects/myagents/banking/zoho_calendar_payments.py`
- Triggers: `task payments`, `task payments:today`, `task payments:month`, or direct Python invocation
- Responsibilities:
  - CLI argument parsing (--days, --no-cache)
  - Load Zoho credentials from environment
  - Authenticate and fetch calendar events
  - Cache management for tokens and event data
  - Display formatted event list

**`coverage_report.py`:**
- Location: `/Users/jamal/projects/myagents/banking/coverage_report.py`
- Triggers: `task report`, `task report:week`, or direct invocation
- Responsibilities:
  - Load payment registry from YAML
  - Fetch balances from Monarch and Mercury in parallel
  - Calculate payment coverage for multiple windows (7/14/30 days)
  - Identify shortfalls and alert on coverage gaps

**`monarch_balances.py`:**
- Location: `/Users/jamal/projects/myagents/banking/monarch_balances.py`
- Triggers: `task balances`, `task balances:login`, or direct invocation
- Responsibilities:
  - Session persistence for Monarch Money login
  - Account enumeration and balance query
  - Grouping accounts by financial type
  - Net worth calculation

**`test_integration.py`:**
- Location: `/Users/jamal/projects/myagents/banking/test_integration.py`
- Triggers: `task test` or `pytest test_integration.py`
- Responsibilities:
  - Verify Zoho payments script returns events
  - Validate output format (regex match on "Total: N event(s)")

## Error Handling

**Strategy:** Fail fast with clear error messages to stderr; distinguish between configuration errors and transient failures

**Patterns:**

- **Missing environment variables**: Print required var name and exit(1) in `get_required_env()`
- **Authentication failures**: HTTP 401 → suggest token refresh; HTTP 404 → check calendar ID
- **Network errors**: Catch `requests.RequestException`, print error, exit(1)
- **JSON parse errors**: Catch `json.JSONDecodeError`, log to stderr, continue or fail depending on context
- **File operations**: `OSError` on cache read → return None (cache miss), `Path.write_text()` for writes (creates dir if needed)
- **Async operation failures**: `asyncio` tasks in `coverage_report.py` catch exceptions, print warnings, continue with empty balances
- **Cache invalidation**: Expired cache silently falls back to API fetch (no error)

## Cross-Cutting Concerns

**Logging:**
- Approach: Mix of `print()` to stdout and `print(..., file=sys.stderr)` to stderr
- Patterns:
  - Status messages: "Authenticating with Zoho...", "Fetching events...", "OK" (no newline, flush=True)
  - Errors: Print to stderr with context (HTTP status, response body, suggestions)
  - Warnings: Monarch/Mercury balance fetch failures logged as warnings, don't abort
  - Reports: Formatted terminal output with box-drawing characters (`╌`, `─`, `=`)

**Validation:**
- Approach: Immediate validation at entry point with fail-fast
- Environment vars: Required vars checked in `get_required_env()` before proceeding
- Arguments: `argparse` validates --days is >= 1 before processing
- API responses: Check HTTP status codes and presence of expected fields (e.g., "access_token" in Zoho response)
- Data format: Zoho datetime parsing tries multiple formats; falls back to raw string if all fail

**Authentication:**
- Zoho: OAuth2 refresh token → access token (50min TTL cache)
- Monarch: Token from env or saved session file (pickle)
- Mercury: HTTP Bearer token in Authorization header
- Pattern: Credentials loaded from `.env` via `python-dotenv`, fallback to environment variables

---

*Architecture analysis: 2026-05-03*
