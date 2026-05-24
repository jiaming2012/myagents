# Codebase Structure

**Analysis Date:** 2026-05-03

## Directory Layout

```
banking/
├── .cache/                    # Ephemeral caches (token, event lists, event details)
├── .git/                      # Git repository
├── .mm/                       # Monarch Money session (generated on first login)
├── .planning/                 # GSD planning artifacts
├── __pycache__/              # Python compiled bytecode (ignore)
├── .env                       # Local environment variables (secret, git-ignored)
├── .env.example              # Environment variable template (checked in)
├── .gitignore                # Git ignore rules
├── README.md                 # Project overview and usage
├── Taskfile.yml              # Task runner definitions (via `task` CLI)
├── requirements.txt          # Python package dependencies
├── payments.yaml             # Payment registry: accounts, obligations, rules
├── zoho_calendar_payments.py # CLI: Fetch upcoming payments from Zoho Calendar
├── monarch_balances.py       # CLI: Show account balances from Monarch Money
├── coverage_report.py        # CLI: Payment coverage analysis report
└── test_integration.py       # Integration test: validates payments script
```

## Directory Purposes

**`.cache/`:**
- Purpose: Local TTL-based cache for API responses
- Contains: JSON files with MD5-hashed keys (one file per cache entry)
- Key files: Dynamic (`token:<client_id>`, `list:<calendar_id>:<date_range>`, `event:<calendar_id>:<uid>`)
- Lifecycle: Files expire based on TTL, can be deleted anytime (cache miss = API fetch)
- Not committed to git

**`.mm/`:**
- Purpose: Monarch Money session persistence
- Contains: `mm_session.pickle` - serialized auth session
- Lifecycle: Created on first Monarch Money login, persists across runs
- Not committed to git (in `.gitignore`)

**`.planning/`:**
- Purpose: GSD workflow artifacts (planning and mapping documents)
- Contains: Subdirectories for phases, codebase analysis docs
- Committed: Yes (part of project planning)

**`.env` (not committed):**
- Purpose: Store local credentials and API keys
- Contains: ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN, ZOHO_CALENDAR_ID, MONARCH_TOKEN, MERCURY_*_API_KEY
- Creation: Copy from `.env.example` and fill in your values
- Never commit: Listed in `.gitignore`

## Key File Locations

**Entry Points:**

- `zoho_calendar_payments.py`: Fetch and display upcoming payment events from Zoho Calendar
  - Main function: `main()` (line 320)
  - Arg parsing: 7-day default, --days override, --no-cache flag

- `coverage_report.py`: Analyze payment funding account coverage across time windows
  - Main function: `async main()` (line 276)
  - Arg parsing: --days to show single window instead of 7/14/30

- `monarch_balances.py`: Display account balances from Monarch Money
  - Main function: `main()` (line 116)
  - Arg parsing: --login force re-login, --type filter by account type

- `test_integration.py`: Verify Zoho script returns events
  - Test function: `test_payments_month_returns_events()` (line 13)
  - Runner: pytest compatible or direct Python execution

**Configuration:**

- `requirements.txt`: Python package versions (requests, python-dotenv, pyyaml, monarchmoney, gql, pytest)

- `payments.yaml`: Payment registry with three top-level sections:
  - `accounts[]` (line 19+): Account definitions - Mercury Personal/Business, with Monarch/Mercury IDs
  - `payments[]`: Payment obligations with amount, day_of_month, funding_account
  - `transfer_rules`: Coverage threshold configuration (minimum_coverage_days, preferred_coverage_days)

- `.env.example`: Template listing all required environment variables (line 1-7)

- `Taskfile.yml`: Task definitions for easy CLI invocation
  - `task install` - Install dependencies
  - `task payments` / `payments:today` / `payments:month` - Zoho calendar queries
  - `task report` / `report:week` - Coverage analysis
  - `task balances` / `balances:login` - Monarch Money queries
  - `task test` - Run integration tests

- `README.md`: Project overview, setup instructions, usage examples

**Core Logic (no separate modules):**

- Utility functions are defined within each Python file (no shared modules)
- Cache helpers: `cache_get()`, `cache_set()` in `zoho_calendar_payments.py` (line 60-78)
- API helpers: `_api_get()`, `get_access_token()` in `zoho_calendar_payments.py` (line 140-171)
- Date processing: `parse_zoho_datetime()` in `zoho_calendar_payments.py` (line 236-247)
- Display formatting: `format_event()` in `zoho_calendar_payments.py` (line 250-291), `print_report()` in `coverage_report.py` (line 170-273)
- Business logic: `get_upcoming_payments()` in `coverage_report.py` (line 51-92), `resolve_balance()` in `coverage_report.py` (line 148-167)

**Testing:**

- `test_integration.py`: Single integration test validating script output
  - Location: Root level, alongside application scripts
  - Pattern: Subprocess call to script with capture, regex assertion on output

## Naming Conventions

**Files:**

- CLI scripts: `<verb>_<noun>.py` (zoho_calendar_payments, monarch_balances, coverage_report)
- Tests: `test_<module>.py` (test_integration)
- Config: `<domain>.yaml` (payments.yaml), `Taskfile.yml`
- Docs: `README.md`, uppercase `.PLANNING/codebase/*.md`

**Directories:**

- Hidden config/cache: `.` prefix (`.cache`, `.env`, `.mm`, `.planning`)
- Generated/build: `__pycache__`

**Functions:**

- Public (entry point): `main()`, async `main()` in entry points
- Public (callable): `fetch_*()`, `get_*()`, `load_*()`, `format_*()`, `display_*()`
- Internal (underscore): `_cache_path()`, `_api_get()`
- Async helpers: Prefixed with `async def`

**Variables:**

- Constants: `ZOHO_TOKEN_URL`, `TOKEN_TTL`, `CACHE_DIR`, `PAYMENTS_FILE` (SCREAMING_SNAKE_CASE)
- Configuration dict keys: `client_id`, `refresh_token`, `access_token` (lowercase_with_underscore)
- Local vars: Standard `snake_case`

**YAML Keys:**

- Top-level sections: `accounts`, `payments`, `transfer_rules` (lowercase)
- Account fields: `id`, `name`, `institution`, `last4`, `type`, `category`, `monarch_match`, `mercury_id`, `mercury_key`, `role` (lowercase_with_underscore)
- Payment fields: `name`, `amount`, `day_of_month`, `funding_account`, `autopay`, `notes` (lowercase_with_underscore)

## Where to Add New Code

**New CLI script (new entry point):**
- Location: `/Users/jamal/projects/myagents/banking/<verb>_<noun>.py`
- Template: Copy structure from `zoho_calendar_payments.py` or `monarch_balances.py`
- Requirements:
  - Import docstring explaining purpose and usage
  - `main()` function with `argparse.ArgumentParser`
  - Load `.env` via `python-dotenv`
  - Error handling with `print(..., file=sys.stderr)` and `sys.exit(1)`
  - Display output to stdout
- Register in `Taskfile.yml` with `tasks:` entry

**New payment source integration:**
- Location: Add API calls within entry point script (e.g., new function in `coverage_report.py`)
- Pattern: Create `fetch_<service>_balances()` function following Monarch/Mercury example (lines 95-116)
- Pattern: Call from `async main()` and integrate into `resolve_balance()` logic
- Update: `payments.yaml` with new account fields if needed

**New test:**
- Location: `/Users/jamal/projects/myagents/banking/test_<module>.py`
- Pattern: Pytest-compatible (uses `assert` statements)
- Registration: Run via `task test` (modify `Taskfile.yml` test task if separate test file)

**New cached API endpoint:**
- Pattern: Use existing `cache_get()` and `cache_set()` from `zoho_calendar_payments.py` or refactor to shared module
- Cache key naming: `<type>:<resource_id>:<qualifier>` (e.g., `token:client_id`, `list:calendar:daterange`)
- TTL constants at top of file: `SERVICE_TTL = <seconds>`

**New account type in payments.yaml:**
- Location: Add entry to `accounts[]` with required fields: `id`, `name`, `institution`, `type`, `category`
- Optional fields: `mercury_id`, `monarch_match`, `mercury_key`, `role`
- Reference in payments via `funding_account: <account_id>`

## Special Directories

**`.cache/`:**
- Purpose: Transient cached API responses with TTL expiration
- Generated: Yes (created on first API call)
- Committed: No (in `.gitignore`)
- Manual cleanup: `rm -rf .cache` (safe, will regenerate)

**`.mm/`:**
- Purpose: Monarch Money session file (persistent login credential)
- Generated: Yes (on first Monarch login via interactive prompt)
- Committed: No (not checked into git)
- Manual cleanup: `rm .mm/mm_session.pickle` to force re-login

**`.pytest_cache/`:**
- Purpose: pytest internal cache (test results, coverage)
- Generated: Yes (automatically)
- Committed: No (in `.gitignore`)
- Purpose: Can be deleted

**`__pycache__/`:**
- Purpose: Python bytecode compilation cache
- Generated: Yes (automatically on import)
- Committed: No
- Purpose: Can be deleted

## File Modification Frequency

**Frequently modified (application logic):**
- `zoho_calendar_payments.py` - API integration changes, caching logic
- `coverage_report.py` - Balance resolution, payment filtering, report formatting
- `monarch_balances.py` - Account grouping, display formatting

**Occasionally modified (configuration):**
- `payments.yaml` - New accounts, new payments, threshold adjustments
- `requirements.txt` - Dependency updates
- `Taskfile.yml` - New task definitions

**Rarely modified (stable):**
- `README.md` - Usage docs
- `test_integration.py` - Test assertions

---

*Structure analysis: 2026-05-03*
