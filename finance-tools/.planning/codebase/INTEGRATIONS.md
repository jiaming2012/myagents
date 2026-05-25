# External Integrations

**Analysis Date:** 2026-05-03

## APIs & External Services

**Zoho Calendar:**
- Payment event scheduling and retrieval
  - SDK/Client: Zoho Calendar REST API (via `requests`)
  - Auth: OAuth2 refresh token flow
  - Base URL: `https://calendar.zoho.com/api/v1`
  - Auth env vars: `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`
  - Calendar ID env var: `ZOHO_CALENDAR_ID`
  - Implementation: `zoho_calendar_payments.py`

**Monarch Money:**
- Account aggregation and balance tracking
  - SDK/Client: `monarchmoney` Python package
  - Auth: Token-based (`MONARCH_TOKEN` env var) or interactive session login
  - Base URL: `https://api.monarch.com` (library override in `monarch_balances.py` line 33)
  - Session persistence: `.mm/mm_session.pickle` (for saved sessions)
  - Implementation: `monarch_balances.py`, `coverage_report.py`

**Mercury:**
- Business and personal bank account management
  - SDK/Client: Mercury REST API (via `requests`)
  - Auth: Bearer token in Authorization header
  - Base URL: `https://api.mercury.com/api/v1`
  - Auth env vars: `MERCURY_BUSINESS_API_KEY`, `MERCURY_PERSONAL_API_KEY` (separate keys for separate workspaces)
  - Endpoint: `/accounts` - retrieves list of accounts with current balance
  - Implementation: `coverage_report.py` (lines 119-145)

## Data Storage

**Databases:**
- None - No persistent database backend

**File Storage:**
- Local filesystem only
  - `.cache/` - Transient JSON cache files (TTL-based expiry)
  - `.mm/` - Monarch Money session pickle file (if using session-based auth)
  - `payments.yaml` - YAML registry of payment metadata (accounts, payment names, amounts, due dates)

**Caching:**
- File-based in-process caching via `.cache/` directory
  - Key format: MD5 hash of cache key
  - Value format: JSON with timestamp and cached data
  - No external cache service (Redis, Memcached, etc.)

## Authentication & Identity

**Auth Provider:**
- Zoho OAuth2 - Refresh token flow
  - Endpoint: `https://accounts.zoho.com/oauth/v2/token`
  - Grant type: `refresh_token`
  - Token TTL: 60 minutes (cached for 50 minutes in code)
  - Implementation: `zoho_calendar_payments.py:get_access_token()` (lines 97-137)

**Session Management:**
- Monarch Money: Dual mode
  - Token-based: `MONARCH_TOKEN` env var (recommended)
  - Session-based: Pickled session file at `.mm/mm_session.pickle` (interactive login fallback)

**Credential Storage:**
- Environment variables (`.env` file)
  - Never committed (listed in `.gitignore`)
  - Template provided in `.env.example`

## Monitoring & Observability

**Error Tracking:**
- None detected - Errors logged to stderr

**Logs:**
- stdout/stderr only (print-based logging)
- No structured logging or external log aggregation

## CI/CD & Deployment

**Hosting:**
- None (local scripts)

**CI Pipeline:**
- None detected

**Testing:**
- pytest integration test: `test_integration.py`
  - Verifies Zoho Calendar integration returns events
  - Command: `task test` or `python -m pytest test_integration.py -v`

## Environment Configuration

**Required env vars:**
- `ZOHO_CLIENT_ID` - OAuth2 client ID for Zoho
- `ZOHO_CLIENT_SECRET` - OAuth2 client secret for Zoho
- `ZOHO_REFRESH_TOKEN` - OAuth2 refresh token for offline Zoho access
- `ZOHO_CALENDAR_ID` - Unique ID of the Payments calendar in Zoho

**Optional env vars:**
- `MONARCH_TOKEN` - Monarch Money API token (if not using saved session)
- `MERCURY_BUSINESS_API_KEY` - Mercury API key for business accounts
- `MERCURY_PERSONAL_API_KEY` - Mercury API key for personal accounts (separate workspace)

**Secrets location:**
- `.env` file in project root (not committed)
- Populated from `.env.example` template

## Webhooks & Callbacks

**Incoming:**
- None detected

**Outgoing:**
- None detected

## API Rate Limits & Constraints

**Zoho Calendar:**
- Token TTL: 60 minutes (code caches for 50 minutes)
- Event list cache: 15 minutes (to reduce API calls)
- Event detail cache: 24 hours

**Monarch Money:**
- SDK handles rate limiting internally
- Session-based auth requires periodic re-authentication if session expires

**Mercury:**
- Standard REST API (no documented rate limits in code)

## Data Sources Summary

| Source | Purpose | Auth Type | Polling/Real-time |
|--------|---------|-----------|-------------------|
| Zoho Calendar | Payment event registry | OAuth2 refresh token | Polled (cached 15min) |
| Monarch Money | Account balance aggregation | Token or session | Polled (async fetch) |
| Mercury | Business/personal account balances | Bearer token | Polled (sync HTTP) |
| payments.yaml | Payment metadata (accounts, amounts, due dates) | File-based | Static file |

---

*Integration audit: 2026-05-03*
