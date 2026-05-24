#!/usr/bin/env python3
"""
Fetch and display upcoming events from a Zoho Calendar "Payments" calendar.

Required environment variables (or set in .env file):
    ZOHO_CLIENT_ID       - OAuth2 client ID from https://api-console.zoho.com/
    ZOHO_CLIENT_SECRET   - OAuth2 client secret
    ZOHO_REFRESH_TOKEN   - OAuth2 refresh token (offline access)
    ZOHO_CALENDAR_ID     - Unique ID of the Payments calendar

Usage:
    python zoho_calendar_payments.py              # Next 7 days (default)
    python zoho_calendar_payments.py --days 14    # Next 14 days
    python zoho_calendar_payments.py --days 1     # Today only
    python zoho_calendar_payments.py --no-cache   # Bypass cache
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: 'pyyaml' package is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

try:
    import requests
except ImportError:
    print("Error: 'requests' package is required. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv is optional; env vars can be set directly
    pass

try:
    from coverage_report import fetch_monarch_balances, resolve_balance
except ImportError:
    print("Error: coverage_report.py must be in the same directory", file=sys.stderr)
    sys.exit(1)

try:
    from xero_balances import fetch_xero_balances
except ImportError:
    fetch_xero_balances = None


ZOHO_TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"
ZOHO_CALENDAR_API_BASE = "https://calendar.zoho.com/api/v1"

# Cache TTLs (seconds)
TOKEN_TTL = 50 * 60       # 50 minutes (tokens expire at 60)
EVENT_LIST_TTL = 15 * 60  # 15 minutes
EVENT_DETAIL_TTL = 24 * 60 * 60  # 24 hours

CACHE_DIR = Path(__file__).parent / ".cache"

# --- Parsing ---

TITLE_PATTERN = re.compile(r'^(.+?)\s*-\s*\$?([\d,]+(?:\.\d{2})?)\s*$')
PAYMENTS_FILE = Path(__file__).parent / "payments.yaml"


def parse_event_title(title):
    """Parse 'Name - $Amount' from event title. Raises ValueError on mismatch."""
    match = TITLE_PATTERN.match(title.strip())
    if not match:
        raise ValueError(f"Title does not match 'Name - $Amount' format: '{title}'")
    name = match.group(1).strip()
    amount = float(match.group(2).replace(',', ''))
    return {"name": name, "amount": amount}


def parse_event_notes(description):
    """Parse structured notes from event description.

    Format: Fund: <account> [| Source: <account>] [| VARIABLE]
    Returns dict with fund_account, source_account (optional), is_variable, no_funding.
    Raises ValueError if description is empty/None or missing Fund: field.
    """
    if not description or not description.strip():
        raise ValueError("Missing description/notes field (required)")

    text = description.strip()

    # Check for explicit no-funding-account marker (D-08)
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


def load_payments_config():
    """Load the payment registry from payments.yaml."""
    with open(PAYMENTS_FILE) as f:
        return yaml.safe_load(f)


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
        # Add explicit nicknames from nicknames field
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


def process_events(events, config):
    """Process calendar events into structured payment dicts.

    Implements collect-and-report error handling (D-03):
    processes all events, collects errors, returns both.

    Returns:
        (payments_list, errors_list) tuple
    """
    lookup = build_nickname_lookup(config)
    payments = []
    errors = []

    for event in events:
        # Parse title
        try:
            parsed = parse_event_title(event.get("title", ""))
        except ValueError as e:
            errors.append(f"Title parse error for '{event.get('title', '?')}': {e}")
            continue

        # Parse notes/description
        try:
            notes = parse_event_notes(event.get("description", ""))
        except ValueError as e:
            errors.append(f"Notes parse error for '{event.get('title', '?')}': {e}")
            continue

        # Resolve fund account to ID (skip for no_funding events)
        fund_account_id = None
        if notes.get("fund_account") and not notes.get("no_funding"):
            try:
                fund_account_id = resolve_account(notes["fund_account"], lookup)
            except ValueError as e:
                errors.append(f"Account resolve error for '{event.get('title', '?')}': {e}")
                continue

        # Parse due date from event
        due_date = event.get("dateandtime", {}).get("start")

        payment = {
            "name": parsed["name"],
            "amount": parsed["amount"],
            "fund_account": notes["fund_account"],
            "fund_account_id": fund_account_id,
            "source_account": notes["source_account"],
            "is_variable": notes["is_variable"],
            "no_funding": notes["no_funding"],
            "event": event,
            "due_date": due_date,
        }
        payments.append(payment)

    return payments, errors


def update_event_title(access_token, calendar_id, event_uid, new_title, etag):
    """Update a Zoho Calendar event title (for --update-calendar flag). Per D-12."""
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


def display_grouped_payments(payments, errors, balances=None):
    """Display payments grouped by funding account. Per D-16."""
    print(f"\n{'=' * 60}")
    print(f"  BILL MAP -- {datetime.now().strftime('%a %b %d, %Y')}")
    print(f"{'=' * 60}")

    grouped = {}
    no_funding = []
    for p in payments:
        if p.get("no_funding"):
            no_funding.append(p)
        else:
            key = p.get("fund_account_id", "UNMATCHED")
            grouped.setdefault(key, []).append(p)

    for acct_id in sorted(grouped.keys()):
        acct_payments = grouped[acct_id]
        print(f"\n  {acct_id}")
        print(f"  {'─' * 50}")
        for p in sorted(acct_payments, key=lambda x: x.get("due_date") or ""):
            flag = " ~estimate" if p.get("is_variable") else ""
            print(f"    {p['due_date']}  {p['name']}: ${p['amount']:,.2f}{flag}")

    if no_funding:
        print(f"\n  NO FUNDING ACCOUNT (informational)")
        print(f"  {'─' * 50}")
        for p in no_funding:
            print(f"    {p['due_date']}  {p['name']}: ${p['amount']:,.2f}")

    total = sum(p["amount"] for p in payments)
    print(f"\n  {'─' * 50}")
    print(f"  Total: ${total:,.2f} across {len(payments)} payment(s)")

    if errors:
        print(f"\n  ERRORS ({len(errors)}):", file=sys.stderr)
        for err in errors:
            print(f"    - {err}", file=sys.stderr)


async def resolve_variable_amounts(payments, config):
    """For variable payments, replace title amount with real Monarch balance. Per D-11."""
    # Only fetch if there are variable payments
    variable = [p for p in payments if p.get("is_variable") and p.get("source_account")]
    if not variable:
        return payments

    monarch_balances = await fetch_monarch_balances()
    xero_balances = fetch_xero_balances() if fetch_xero_balances else {}

    # Build lookup from source_account nickname to account config
    nickname_lookup = build_nickname_lookup(config)

    for p in variable:
        source = p["source_account"]
        # Find the account in config to get monarch_match
        try:
            acct_id = resolve_account(source, nickname_lookup)
        except ValueError:
            continue  # Can't resolve source account, keep estimate

        # Find account config
        acct_config = next((a for a in config.get("accounts", []) if a["id"] == acct_id), None)
        if not acct_config:
            continue

        # Try to get balance from Monarch
        balance, _ = resolve_balance(acct_config, monarch_balances, xero_balances)
        if balance is not None:
            p["amount"] = abs(balance)  # Pitfall 6: credit cards are negative
            p["amount_source"] = "monarch"

    return payments


# --- Cache ---

def _cache_path(key):
    """Return the file path for a cache key."""
    safe = hashlib.md5(key.encode()).hexdigest()
    return CACHE_DIR / f"{safe}.json"


def cache_get(key, ttl):
    """Read a cached value if it exists and hasn't expired."""
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - data.get("ts", 0) > ttl:
        return None
    return data.get("value")


def cache_set(key, value):
    """Write a value to the cache."""
    CACHE_DIR.mkdir(exist_ok=True)
    path = _cache_path(key)
    path.write_text(json.dumps({"ts": time.time(), "value": value}))


# --- Zoho API ---

def get_required_env(name):
    """Get a required environment variable or exit with a clear error."""
    value = os.environ.get(name)
    if not value:
        print(
            f"Error: Missing required environment variable: {name}\n"
            f"Set it in your shell or in a .env file.\n"
            f"See script docstring for all required variables.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def get_access_token(client_id, client_secret, refresh_token, use_cache=True):
    """Exchange a refresh token for an access token via Zoho OAuth2."""
    cache_key = f"token:{client_id}"
    if use_cache:
        cached = cache_get(cache_key, TOKEN_TTL)
        if cached:
            return cached

    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }

    try:
        resp = requests.post(ZOHO_TOKEN_URL, data=payload, timeout=15)
    except requests.RequestException as e:
        print(f"Error: Failed to connect to Zoho auth server: {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        print(
            f"Error: Zoho auth returned HTTP {resp.status_code}\n"
            f"Response: {resp.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    data = resp.json()
    if "access_token" not in data:
        print(
            f"Error: No access_token in Zoho auth response.\n"
            f"Response: {json.dumps(data, indent=2)}",
            file=sys.stderr,
        )
        sys.exit(1)

    token = data["access_token"]
    cache_set(cache_key, token)
    return token


def _api_get(url, headers, params=None):
    """Make a GET request to Zoho API with standard error handling."""
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.RequestException as e:
        print(f"Error: Failed to connect to Zoho Calendar API: {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code == 401:
        print(
            "Error: Authentication failed (401). Your refresh token may be expired.\n"
            "Generate a new one at https://api-console.zoho.com/",
            file=sys.stderr,
        )
        sys.exit(1)

    if resp.status_code == 404:
        print(
            f"Error: Calendar not found (404). Check ZOHO_CALENDAR_ID.",
            file=sys.stderr,
        )
        sys.exit(1)

    if resp.status_code != 200:
        print(
            f"Error: Zoho Calendar API returned HTTP {resp.status_code}\n"
            f"Response: {resp.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    return resp.json()


def fetch_event_detail(access_token, calendar_id, uid, use_cache=True):
    """Fetch a single event's detail, with caching."""
    cache_key = f"event:{calendar_id}:{uid}"
    if use_cache:
        cached = cache_get(cache_key, EVENT_DETAIL_TTL)
        if cached:
            return cached

    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    detail_url = f"{ZOHO_CALENDAR_API_BASE}/calendars/{calendar_id}/events/{uid}"
    detail = _api_get(detail_url, headers)
    detail_events = detail.get("events", [])
    result = detail_events[0] if detail_events else None

    if result:
        cache_set(cache_key, result)
    return result


def fetch_events(access_token, calendar_id, start_date, end_date, use_cache=True):
    """Fetch events from a Zoho Calendar within a date range, including descriptions."""
    # Check event list cache
    range_key = f"list:{calendar_id}:{start_date.strftime('%Y%m%d')}:{end_date.strftime('%Y%m%d')}"
    if use_cache:
        cached = cache_get(range_key, EVENT_LIST_TTL)
        if cached:
            return cached

    url = f"{ZOHO_CALENDAR_API_BASE}/calendars/{calendar_id}/events"
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    params = {
        "range": json.dumps({
            "start": start_date.strftime("%Y%m%dT000000Z"),
            "end": end_date.strftime("%Y%m%dT235959Z"),
        }),
    }

    data = _api_get(url, headers, params)
    events = data.get("events", [])

    # List endpoint omits descriptions — fetch each event's detail
    detailed = []
    for event in events:
        uid = event.get("uid", "")
        if uid:
            detail = fetch_event_detail(access_token, calendar_id, uid, use_cache)
            if detail:
                merged = detail.copy()
                # Detail returns original series date; preserve instance date from list
                merged["dateandtime"] = event.get("dateandtime", merged.get("dateandtime", {}))
                detailed.append(merged)
            else:
                detailed.append(event)
        else:
            detailed.append(event)

    cache_set(range_key, detailed)
    return detailed


# --- Display ---

def parse_zoho_datetime(dt_str):
    """Parse a Zoho datetime string into a Python datetime.

    Zoho returns dates in formats like '20260410T140000Z' or '20260410T140000+0530'.
    Falls back to returning the raw string if parsing fails.
    """
    for fmt in ("%Y%m%d", "%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(dt_str, fmt)
        except (ValueError, TypeError):
            continue
    return dt_str


def format_event(event):
    """Format a single calendar event for terminal display."""
    title = event.get("title", "(no title)")

    # Parse start/end times
    start_raw = event.get("dateandtime", {}).get("start", "")
    end_raw = event.get("dateandtime", {}).get("end", "")
    is_all_day = event.get("isallday", False)

    start_dt = parse_zoho_datetime(start_raw)
    end_dt = parse_zoho_datetime(end_raw)

    if is_all_day:
        if isinstance(start_dt, datetime):
            date_str = start_dt.strftime("%a %b %d, %Y")
        else:
            date_str = str(start_raw)
        time_str = "All day"
    else:
        if isinstance(start_dt, datetime):
            date_str = start_dt.strftime("%a %b %d, %Y")
            start_time = start_dt.strftime("%I:%M %p")
            if isinstance(end_dt, datetime):
                end_time = end_dt.strftime("%I:%M %p")
                time_str = f"{start_time} - {end_time}"
            else:
                time_str = start_time
        else:
            date_str = str(start_raw)
            time_str = ""

    # Description (first line, stripped and truncated)
    description = event.get("description", "").strip().split("\n")[0].strip()
    if description and len(description) > 100:
        description = description[:97] + "..."

    return {
        "title": title,
        "date": date_str,
        "time": time_str,
        "description": description,
    }


def display_events(events, days):
    """Display formatted events to stdout."""
    if not events:
        print(f"\nNo upcoming events found in the next {days} day(s).")
        return

    print(f"\n{'=' * 60}")
    print(f"  Payments Calendar - Next {days} Day(s)")
    print(f"  {datetime.now().strftime('%a %b %d, %Y')}")
    print(f"{'=' * 60}\n")

    for i, event in enumerate(events, 1):
        fmt = format_event(event)
        print(f"  [{i}] {fmt['title']}")
        print(f"      Date: {fmt['date']}")
        if fmt["time"]:
            print(f"      Time: {fmt['time']}")
        if fmt["description"]:
            print(f"      Note: {fmt['description']}")
        print()

    print(f"{'=' * 60}")
    print(f"  Total: {len(events)} event(s)")
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch and display upcoming events from a Zoho Calendar 'Payments' calendar.",
        epilog=(
            "Required environment variables:\n"
            "  ZOHO_CLIENT_ID       OAuth2 client ID\n"
            "  ZOHO_CLIENT_SECRET   OAuth2 client secret\n"
            "  ZOHO_REFRESH_TOKEN   OAuth2 refresh token\n"
            "  ZOHO_CALENDAR_ID     Payments calendar ID\n"
            "\n"
            "Set these in your shell or in a .env file in the project root."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to look ahead (default: 7)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass cache and fetch fresh data",
    )
    parser.add_argument(
        "--bill-map",
        action="store_true",
        help="Show structured bill-to-account mapping (default mode)",
    )
    parser.add_argument(
        "--update-calendar",
        action="store_true",
        help="Write real amounts back to Zoho Calendar for variable payments",
    )
    args = parser.parse_args()

    if args.days < 1:
        print("Error: --days must be at least 1.", file=sys.stderr)
        sys.exit(1)

    use_cache = not args.no_cache

    # Load configuration
    client_id = get_required_env("ZOHO_CLIENT_ID")
    client_secret = get_required_env("ZOHO_CLIENT_SECRET")
    refresh_token = get_required_env("ZOHO_REFRESH_TOKEN")
    calendar_id = get_required_env("ZOHO_CALENDAR_ID")

    # Authenticate
    print("Authenticating with Zoho...", end=" ", flush=True)
    access_token = get_access_token(client_id, client_secret, refresh_token, use_cache)
    print("OK")

    # Fetch events
    start_date = datetime.now(tz=None)
    end_date = start_date + timedelta(days=args.days)

    print(f"Fetching events from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}...", end=" ", flush=True)
    events = fetch_events(access_token, calendar_id, start_date, end_date, use_cache)
    print("OK")

    # Display -- bill-map mode or legacy event list
    if args.bill_map or args.update_calendar:
        # Load payments config
        config = load_payments_config()

        # Parse events into structured payments
        payments, errors = process_events(events, config)

        # Resolve variable payment amounts from Monarch
        payments = asyncio.run(resolve_variable_amounts(payments, config))

        # Optionally update calendar with real amounts
        if args.update_calendar:
            for p in payments:
                if p.get("amount_source") == "monarch" and p.get("event"):
                    event = p["event"]
                    new_title = f"{p['name']} - ${p['amount']:,.2f}"
                    try:
                        update_event_title(access_token, calendar_id, event["uid"], new_title, event.get("etag", ""))
                        print(f"  Updated: {new_title}")
                    except RuntimeError as e:
                        errors.append(f"Calendar update failed for {p['name']}: {e}")

        # Display grouped output
        display_grouped_payments(payments, errors)

        # Exit non-zero if any errors (D-03)
        if errors:
            sys.exit(1)
    else:
        display_events(events, args.days)


if __name__ == "__main__":
    main()
