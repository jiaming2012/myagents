#!/usr/bin/env python3
"""finance-tools as a Clearspan Source: a Model Context Protocol server over stdio,
started by Clearspan as a child process (clearspan ADR 0005).

It offers `key_indicators`, the figures Clearspan watches, and wraps the read-only
commands as tools. Nothing here writes to Monarch, Zoho or the registry.

Key Indicators:
  sync_errors         connections that could not be checked or are broken, from
                      sync_status (findings of severity "error")
  days_to_shortfall   days until the first day a bank account's projected balance goes
                      below zero in the payment forecast; the horizon (30) when none

Usage:
    python mcp_server.py                     # speaks MCP on stdin/stdout; logs to stderr

Register it in Clearspan with command `python`, arguments `mcp_server.py`, and this
directory. Secrets, the Monarch session and the caches live in DATA_DIR
($FINANCE_TOOLS_DATA, default this directory), like the other commands here.

Monarch and Zoho are asked at most once per CACHE_TTL_MINUTES (30), five when the
last attempt failed, so Clearspan's five-minute polling never becomes a login storm.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

HERE = Path(__file__).parent
DATA_DIR = Path(os.getenv("FINANCE_TOOLS_DATA", HERE))
CACHE_FILE = DATA_DIR / ".clearspan_source_cache.json"
CACHE_TTL_MINUTES = 30
ERROR_TTL_MINUTES = 5
# A source that does not answer in this long is an outage for this round: the figure is
# left out and the attempt is retried after ERROR_TTL_MINUTES. The Monarch client has no
# timeout of its own, and a tool call that never returns would hold Clearspan's report.
SOURCE_TIMEOUT_SECONDS = 120
HORIZON_DAYS = 30

READ_ONLY = ToolAnnotations(read_only_hint=True)  # readOnlyHint on the wire


# --- the figures (pure; tested) ------------------------------------------------------


def sync_error_count(result: dict | None) -> int | None:
    """How many sync_status findings are errors; None when there is no result."""
    if result is None:
        return None
    return sum(1 for f in result.get("findings", []) if f.get("severity") == "error")


def days_to_shortfall(forecast: dict | None, horizon: int = HORIZON_DAYS, today: date | None = None) -> int | None:
    """Days from today until the first payment that takes a bank account below zero,
    walking each account's payments in date order from its current balance. The
    horizon when no account goes negative. None when there is no forecast, or when
    every account's balance is unknown (nothing to project from)."""
    if forecast is None:
        return None
    today = today or date.today()
    first: int | None = None
    known = False
    for acct in forecast.get("accounts", []):
        if acct.get("severity") == "unknown":
            continue
        known = True
        running = acct.get("current_balance", 0.0)
        for p in sorted(acct.get("payments", []), key=lambda x: x["due_date"]):
            running -= p.get("amount", 0.0)
            if running < 0:
                due = p["due_date"]
                due = due.date() if isinstance(due, datetime) else due
                days = max(0, (due - today).days)
                first = days if first is None else min(first, days)
                break
    if not known:
        return None
    return horizon if first is None else first


def figures(sync_result: dict | None, forecast: dict | None, now: datetime,
            forecast_at: datetime | None = None, horizon: int = HORIZON_DAYS) -> list[dict[str, Any]]:
    """The key_indicators answer, in the shape Clearspan reads. A figure whose source
    could not be read is left out rather than invented."""
    out: list[dict[str, Any]] = []
    errors = sync_error_count(sync_result)
    if errors is not None:
        out.append({"name": "sync_errors", "value": errors, "unit": "connections",
                    "as_of": sync_result.get("checked_at") or now.isoformat()})
    days = days_to_shortfall(forecast, horizon, now.date())
    if days is not None:
        out.append({"name": "days_to_shortfall", "value": days, "unit": "days",
                    "as_of": (forecast_at or now).isoformat()})
    return out


def upcoming(forecast: dict) -> list[dict[str, Any]]:
    """Every scheduled bank draw and card charge in the forecast, soonest first."""
    rows = []
    for acct in forecast.get("accounts", []):
        for p in acct.get("payments", []):
            rows.append({"name": p["name"], "amount": p["amount"], "due_date": _day(p["due_date"]),
                         "funding_account": acct["id"], "kind": "bank draw"})
    for c in forecast.get("card_charges", []):
        rows.append({"name": c["name"], "amount": c["amount"], "due_date": _day(c["due_date"]),
                     "funding_account": c["card_id"], "kind": "card charge"})
    rows.sort(key=lambda r: (r["due_date"], r["name"]))
    return rows


def _day(d) -> str:
    return d.date().isoformat() if isinstance(d, datetime) else str(d)


# --- the cache -----------------------------------------------------------------------


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(cache, indent=1, default=str))
    except OSError:
        pass


def cache_fresh(entry: dict | None, now: datetime, ttl_minutes: int = CACHE_TTL_MINUTES,
                error_ttl_minutes: int = ERROR_TTL_MINUTES) -> bool:
    if not entry or not entry.get("computed_at"):
        return False
    try:
        at = datetime.fromisoformat(entry["computed_at"])
    except ValueError:
        return False
    ttl = error_ttl_minutes if entry.get("error") else ttl_minutes
    return (now - at) < timedelta(minutes=ttl)


async def cached(key: str, compute, now: datetime | None = None, timeout: float = SOURCE_TIMEOUT_SECONDS) -> dict:
    """{"computed_at", "value", "error"}: the cached entry while fresh, else a fresh
    computation, which is stored. An exception, or no answer within `timeout` seconds,
    is stored as the entry's error."""
    now = now or datetime.now(timezone.utc)
    cache = _load_cache()
    entry = cache.get(key)
    if cache_fresh(entry, now):
        return entry
    try:
        value = await asyncio.wait_for(compute(), timeout)
        entry = {"computed_at": now.isoformat(), "value": value, "error": None}
    except asyncio.TimeoutError:
        entry = {"computed_at": now.isoformat(), "value": None, "error": f"no answer within {timeout:.0f}s"}
    except Exception as e:  # noqa: BLE001 - an outage is a figure left out, never a crash
        entry = {"computed_at": now.isoformat(), "value": None, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    cache[key] = entry
    _save_cache(cache)
    return entry


# --- the sources ---------------------------------------------------------------------


async def _sync_status(fresh: bool = False) -> dict:
    from sync_status import collect
    return await collect(fresh=fresh)


async def _balances() -> dict:
    """Monarch and Zoho balances, cached; the Zoho status rides along."""
    async def fetch():
        from coverage_report import fetch_monarch_balances
        from zoho_balances import fetch_with_status
        monarch = await fetch_monarch_balances()
        zoho, zoho_status = await asyncio.to_thread(fetch_with_status)
        return {"monarch": monarch, "zoho": zoho, "zoho_status": zoho_status}
    return await cached("balances", fetch)


def _forecast(balances: dict | None, days: int) -> dict:
    from coverage_report import load_payments_yaml
    from payment_forecast import build_forecast
    config = load_payments_yaml()
    monarch = (balances or {}).get("monarch") or {}
    zoho = (balances or {}).get("zoho") or {}
    return build_forecast(config, monarch, zoho, days=days, warn_missing=False)


def _json(v: Any) -> str:
    return json.dumps(v, default=str)


# --- the server ----------------------------------------------------------------------

# Warnings only: at INFO the Monarch client's GraphQL library logs every response body,
# balances included, and Clearspan echoes a Source's stderr to its console.
server = MCPServer("finance-tools", version="0.1", log_level="WARNING")
logging.getLogger("gql").setLevel(logging.WARNING)


@server.tool(description="The figures this Source reports: sync_errors (connections sync_status "
             "finds broken or uncheckable) and days_to_shortfall (days until the payment forecast "
             "first takes a bank account below zero; 30 when it never does in the 30-day horizon). "
             "A figure whose source cannot be read is left out.", structured_output=False)
async def key_indicators() -> str:
    with contextlib.redirect_stdout(sys.stderr):
        now = datetime.now(timezone.utc)
        sync = await cached("sync_status", _sync_status)
        bal = await _balances()
        forecast = _forecast(bal["value"], HORIZON_DAYS) if bal["value"] is not None else None
        at = datetime.fromisoformat(bal["computed_at"]) if bal["value"] is not None else None
        return _json(figures(sync["value"], forecast, now, forecast_at=at))


@server.tool(description="Which Monarch connections and Zoho Books bank feeds are disconnected, "
             "stale or uncheckable, with the fix for each. The same as `task finance:sync-status "
             "-- --json`. Cached 30 minutes; fresh=true asks both sources again.",
             annotations=READ_ONLY, structured_output=False)
async def sync_status(fresh: bool = False) -> str:
    with contextlib.redirect_stdout(sys.stderr):
        return _json(await asyncio.wait_for(_sync_status(fresh=fresh), SOURCE_TIMEOUT_SECONDS))


@server.tool(description="The payment forecast: each bank account's current balance, its scheduled "
             "draws in the next `days` days (1-365, default 30), its projected balance and severity "
             "(ok, warning below min_balance, error below zero, unknown when the balance could not "
             "be fetched), plus card charges and totals. The same as `task finance:forecast`. "
             "Balances are cached 30 minutes.", annotations=READ_ONLY, structured_output=False)
async def payment_forecast(days: int = 30) -> str:
    if not 1 <= days <= 365:
        raise ValueError("days must be between 1 and 365")
    with contextlib.redirect_stdout(sys.stderr):
        bal = await _balances()
        if bal["value"] is None:
            raise RuntimeError(f"balances unavailable: {bal['error']}")
        out = _forecast(bal["value"], days)
        out["zoho_status"] = bal["value"].get("zoho_status")
        out["balances_as_of"] = bal["computed_at"]
        return _json(out)


@server.tool(description="Everything scheduled to leave an account in the next `days` days "
             "(default 7), from payments.yaml alone: deterministic, no logins. The same as "
             "`task finance:due`.", annotations=READ_ONLY, structured_output=False)
async def upcoming_payments(days: int = 7) -> str:
    if not 1 <= days <= 365:
        raise ValueError("days must be between 1 and 365")
    with contextlib.redirect_stdout(sys.stderr):
        return _json(upcoming(_forecast(None, days)))


if __name__ == "__main__":
    server.run("stdio")
