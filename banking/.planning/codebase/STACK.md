# Technology Stack

**Analysis Date:** 2026-05-03

## Languages

**Primary:**
- Python 3.x - All application and scripting code

## Runtime

**Environment:**
- Python 3.x (inferred from usage)

**Package Manager:**
- pip
- Lockfile: Not present (requirements.txt used instead)

## Frameworks

**Core:**
- None (vanilla Python scripts)

**CLI/Task Running:**
- Task (Taskfile.yml) - Command runner for developer tasks

**Testing:**
- pytest - Unit/integration testing framework

**HTTP Client:**
- requests - Synchronous HTTP requests for REST APIs

**GraphQL:**
- gql<4 - GraphQL client (used by monarchmoney SDK)

**Data Serialization:**
- PyYAML - YAML parsing for payment registry (`payments.yaml`)

## Key Dependencies

**Critical:**
- `monarchmoney` - Python SDK for Monarch Money API (account aggregation)
  - Provides async client for fetching account balances
  - Handles session management and interactive login
  - Version pinned implicitly in requirements.txt
- `requests` - HTTP client for Zoho Calendar and Mercury APIs
- `python-dotenv` - Environment variable management from `.env` files
- `pyyaml` - Payment registry configuration parsing

**Infrastructure:**
- `gql<4` - GraphQL client library (transitive dependency via monarchmoney)
- `pytest` - Test framework and runner

## Configuration

**Environment:**
- `.env` file (local, not committed)
  - Populated from `.env.example` template
  - Contains OAuth2 tokens and API keys
- Environment variable names documented in `.env.example`:
  - `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`, `ZOHO_CALENDAR_ID`
  - `MONARCH_TOKEN`
  - `MERCURY_BUSINESS_API_KEY`, `MERCURY_PERSONAL_API_KEY`

**Build:**
- No build system (pure Python scripts)
- Task runner: `Taskfile.yml` for local development tasks

## Platform Requirements

**Development:**
- Python 3.x
- pip for dependency installation
- Task runner (optional, for convenience)

**Production:**
- Python 3.x runtime
- Network access to:
  - Zoho OAuth token endpoint (`https://accounts.zoho.com/oauth/v2/token`)
  - Zoho Calendar API (`https://calendar.zoho.com/api/v1`)
  - Monarch Money API (`https://api.monarch.com`)
  - Mercury API (`https://api.mercury.com/api/v1`)

## Caching

**Local Caching:**
- File-based JSON cache in `.cache/` directory
- Used by `zoho_calendar_payments.py` for:
  - OAuth tokens (50-minute TTL)
  - Event lists (15-minute TTL)
  - Event details (24-hour TTL)
- Implemented with MD5 hashing of cache keys for safe file naming

---

*Stack analysis: 2026-05-03*
