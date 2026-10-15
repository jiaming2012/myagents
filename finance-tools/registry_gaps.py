#!/usr/bin/env python3
"""
Registry gap check: is payments.yaml complete and consistent with reality?

The forecast can only warn about what the registry knows. This module finds
what the registry does NOT know, by comparing it against:

  1. itself      - credit/loan accounts with no payment entry
  2. Monarch     - liability accounts Monarch has that the registry lacks,
                   and registry accounts Monarch says are closed
  3. Zoho        - payment entries with no calendar event in the next 31 days
  4. the sources - Monarch/Xero auth or connection failures (a silent empty
                   balance is a gap, not a success)
  5. Xero        - bank accounts whose latest transaction is older than N days
                   (the business books are behind, so business liabilities
                   are under-reported)

Each check returns a list of gap dicts: {"severity": "error"|"warning",
"kind": str, "message": str, "fix": str}. `run_all()` gathers live data and
runs every check; `main()` prints them and exits 1 if any exist.

Usage:
    python registry_gaps.py              # all checks (needs Monarch/Xero/Zoho creds)
    python registry_gaps.py --offline    # registry-only checks, no network
    python registry_gaps.py --no-calendar
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta

XERO_STALE_DAYS = 10
MONARCH_STALE_DAYS = 7
CALENDAR_WINDOW_DAYS = 31


def norm_name(s):
    """Normalize an account display name for matching: casefold, drop symbols like ®/™,
    collapse whitespace. 'Platinum Card® (...2004)' == 'Platinum Card (...2004)'."""
    if not s:
        return ""
    out = "".join(ch for ch in str(s) if ch.isalnum() or ch in " ().-_*")
    return " ".join(out.casefold().split())


# ---------------------------------------------------------------------------
# Pure checks (no I/O) -- unit-testable
# ---------------------------------------------------------------------------

def _gap(severity, kind, message, fix=""):
    return {"severity": severity, "kind": kind, "message": message, "fix": fix}


def _liability_accounts(config):
    return [a for a in config.get("accounts", [])
            if a.get("type") in ("credit", "loan") and not a.get("inactive")]


def payment_pays_account(payment, accounts):
    """Return the credit/loan account id a payment pays down, or None.

    Explicit `pays_account` wins. Otherwise mirror the forecast heuristic:
    credit cards need an autopay_type and a name/nickname match; loans need
    only a name/nickname match.
    """
    explicit = payment.get("pays_account")
    if explicit:
        return explicit
    name = (payment.get("name") or "").lower()
    for acct in accounts:
        t = acct.get("type")
        if t not in ("credit", "loan"):
            continue
        if t == "credit" and not payment.get("autopay_type"):
            continue
        candidates = [acct.get("name", "")] + list(acct.get("nicknames", []))
        if any(c and c.lower() in name for c in candidates):
            return acct["id"]
    return None


def check_liabilities_without_payment(config):
    """Every active credit/loan account should have at least one payment entry."""
    accounts = config.get("accounts", [])
    covered = {payment_pays_account(p, accounts) for p in config.get("payments", [])}
    gaps = []
    for acct in _liability_accounts(config):
        if acct["id"] not in covered:
            gaps.append(_gap(
                "error", "no_payment_entry",
                f"{acct.get('name', acct['id'])} ({acct['id']}, {acct['type']}) has no payment entry; "
                f"its due date can never appear in a forecast.",
                "Add a payments: entry with pays_account: " + acct["id"] +
                " (or set inactive: true on the account if it is closed).",
            ))
    return gaps


def check_monarch_unknown_liabilities(config, monarch_accounts):
    """Monarch credit/loan accounts (active) that no registry account maps to."""
    if not monarch_accounts:
        return []
    known = {norm_name(a.get("monarch_match")) for a in config.get("accounts", []) if a.get("monarch_match")}
    gaps = []
    for name, info in sorted(monarch_accounts.items()):
        if info.get("type") not in ("credit", "loan"):
            continue
        if info.get("deactivated"):
            continue
        if norm_name(name) in known:
            continue
        owed = abs(info.get("balance") or 0.0)
        gaps.append(_gap(
            "error", "monarch_unknown_liability",
            f"Monarch has {info['type']} account '{name}' (owing ${owed:,.2f}) that is not in payments.yaml.",
            f"Add an accounts: entry with monarch_match: \"{name}\", then a payment entry for it.",
        ))
    return gaps


def check_registry_closed_in_monarch(config, monarch_accounts):
    """Registry accounts whose Monarch counterpart is missing, hidden or deactivated."""
    if not monarch_accounts:
        return []
    gaps = []
    for acct in config.get("accounts", []):
        if acct.get("inactive"):
            continue
        match = acct.get("monarch_match")
        if not match:
            continue
        by_norm = {norm_name(k): v for k, v in monarch_accounts.items()}
        info = by_norm.get(norm_name(match))
        # "hidden" only hides an account from Monarch's list; its balance still
        # resolves, so it is not a gap. Deactivated (closed) or missing is.
        if info is None:
            why = "not found in Monarch (check monarch_match against the exact Monarch name)"
        elif info.get("deactivated"):
            why = f"deactivated in Monarch on {str(info['deactivated'])[:10]}"
        else:
            continue
        gaps.append(_gap(
            "warning", "registry_account_closed",
            f"{acct.get('name', acct['id'])} ({acct['id']}) is {why}; its balance will never resolve.",
            "Set inactive: true on the account (and drop its payment entry) or fix monarch_match.",
        ))
    return gaps


def check_monarch_stale(config, monarch_accounts, threshold_days=MONARCH_STALE_DAYS, today=None):
    """Registry accounts whose Monarch balance has not been updated in N days.

    Caught in Oct 2026: the registry pointed at an old, hidden Capital One
    connection whose balances were last updated in March 2025, so the forecast
    silently used 19-month-old numbers for the main bill-pay account.
    """
    if not monarch_accounts:
        return []
    today = today or datetime.now().date()
    by_norm = {norm_name(k): v for k, v in monarch_accounts.items()}
    gaps = []
    for acct in config.get("accounts", []):
        if acct.get("inactive") or not acct.get("monarch_match"):
            continue
        info = by_norm.get(norm_name(acct["monarch_match"]))
        if not info or info.get("deactivated"):
            continue  # reported by check_registry_closed_in_monarch
        updated = info.get("updated")
        if not updated:
            continue
        try:
            upd = datetime.fromisoformat(str(updated).replace("Z", "+00:00")).date()
        except ValueError:
            continue
        age = (today - upd).days
        if age > threshold_days:
            gaps.append(_gap(
                "error", "monarch_stale_balance",
                f"{acct.get('name', acct['id'])} ({acct['id']}) -> Monarch '{acct['monarch_match']}' "
                f"last updated {upd} ({age}d ago); the forecast is using a stale balance.",
                "Fix the connection in Monarch, or point monarch_match at the live account "
                "(Monarch may hold an old hidden copy next to the live one).",
            ))
    return gaps


def check_calendar_coverage(config, event_titles):
    """Payment entries with no Zoho event whose title mentions them."""
    if event_titles is None:
        return []
    titles = [t.lower() for t in event_titles]
    gaps = []
    for p in config.get("payments", []):
        needles = [n.lower() for n in (p.get("zoho_match"), p.get("name")) if n]
        if not needles:
            continue
        if any(n in t for n in needles for t in titles):
            continue
        gaps.append(_gap(
            "warning", "no_calendar_event",
            f"'{p['name']}' (day {p.get('day_of_month')}) has no Zoho Payments event in the next "
            f"{CALENDAR_WINDOW_DAYS} days.",
            "Create the recurring calendar event, or set zoho_match to the event's name.",
        ))
    return gaps


def check_sources(source_status):
    """Auth/connection failures are gaps: an empty balance set is not 'all clear'."""
    gaps = []
    for source, status in sorted((source_status or {}).items()):
        if status == "ok":
            continue
        gaps.append(_gap(
            "error", "source_unavailable",
            f"{source} unavailable: {status}. Balances and detections from it are missing, "
            f"so every '{source}'-backed account looks fine when it may not be.",
            {"monarch": "Run: task finance:balances:login",
             "xero": "Re-consent: cd ~/projects/yumyums/accounting/xero && python app.py, "
                     "then open http://localhost:9091/login"}.get(source, ""),
        ))
    return gaps


def check_xero_staleness(latest_by_account, threshold_days=XERO_STALE_DAYS, today=None):
    """Xero bank accounts with no transaction in the last N days."""
    if not latest_by_account:
        return []
    today = today or datetime.now().date()
    gaps = []
    for name, latest in sorted(latest_by_account.items()):
        if latest is None:
            age = None
        else:
            age = (today - latest).days
        if age is None or age > threshold_days:
            if latest is None:
                # No transaction ever -- an empty shell, not a reconciliation backlog.
                when = "never"
                fix = ("Import this account's transactions in Xero, or archive the account "
                       "there if it is not in use.")
            else:
                when = f"{latest} ({age}d ago)"
                fix = "Reconcile/import this account in Xero."
            gaps.append(_gap(
                "warning", "xero_stale",
                f"Xero bank account '{name}': latest transaction {when}; books are behind, "
                f"business liabilities are under-reported.",
                fix,
            ))
    return gaps


def run_checks(config, monarch_accounts=None, event_titles=None, source_status=None,
               xero_latest=None):
    gaps = []
    gaps += check_sources(source_status)
    gaps += check_liabilities_without_payment(config)
    gaps += check_monarch_unknown_liabilities(config, monarch_accounts)
    gaps += check_registry_closed_in_monarch(config, monarch_accounts)
    gaps += check_monarch_stale(config, monarch_accounts)
    gaps += check_calendar_coverage(config, event_titles)
    gaps += check_xero_staleness(xero_latest)
    return gaps


# ---------------------------------------------------------------------------
# Data gathering (I/O)
# ---------------------------------------------------------------------------

async def gather(config, offline=False, calendar=True):
    """Collect live inputs for run_checks, recording each source's status."""
    source_status = {}
    monarch_accounts = None
    xero_latest = None
    event_titles = None

    if offline:
        return dict(config=config, monarch_accounts=None, event_titles=None,
                    source_status=None, xero_latest=None)

    # Monarch
    try:
        from coverage_report import fetch_monarch_balances
        from monarch_client import MonarchAuthError
        try:
            monarch_accounts = await fetch_monarch_balances()
            source_status["monarch"] = "ok" if monarch_accounts else "no accounts returned"
        except MonarchAuthError as e:
            source_status["monarch"] = str(e)
        except Exception as e:  # network etc.
            source_status["monarch"] = f"{type(e).__name__}: {e}"[:160]
    except ImportError as e:
        source_status["monarch"] = f"import failed: {e}"

    # Xero
    try:
        from xero_balances import xero_connection_status, fetch_xero_latest_activity
        status = xero_connection_status()
        source_status["xero"] = status
        if status == "ok":
            try:
                xero_latest = fetch_xero_latest_activity()
            except Exception as e:
                source_status["xero"] = f"activity fetch failed: {e}"[:160]
    except ImportError as e:
        source_status["xero"] = f"import failed: {e}"

    # Zoho calendar
    if calendar:
        try:
            import os
            from zoho_calendar_payments import get_access_token, fetch_events
            needed = ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN", "ZOHO_CALENDAR_ID")
            if all(os.environ.get(k) for k in needed):
                token = get_access_token(os.environ["ZOHO_CLIENT_ID"], os.environ["ZOHO_CLIENT_SECRET"],
                                         os.environ["ZOHO_REFRESH_TOKEN"])
                start = datetime.now()
                events = fetch_events(token, os.environ["ZOHO_CALENDAR_ID"], start,
                                      start + timedelta(days=CALENDAR_WINDOW_DAYS))
                event_titles = [e.get("title", "") for e in events]
                source_status["zoho"] = "ok"
            else:
                source_status["zoho"] = "credentials not set (calendar check skipped)"
        except Exception as e:
            source_status["zoho"] = f"{type(e).__name__}: {e}"[:160]

    return dict(config=config, monarch_accounts=monarch_accounts, event_titles=event_titles,
                source_status=source_status, xero_latest=xero_latest)


async def run_all(offline=False, calendar=True):
    from coverage_report import load_payments_yaml
    config = load_payments_yaml()
    inputs = await gather(config, offline=offline, calendar=calendar)
    return run_checks(**inputs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def format_gaps(gaps, color=True):
    if not gaps:
        return "  No registry gaps found."
    red = "\033[31m" if color else ""
    yellow = "\033[33m" if color else ""
    dim = "\033[2m" if color else ""
    reset = "\033[0m" if color else ""
    lines = []
    for g in sorted(gaps, key=lambda g: (g["severity"] != "error", g["kind"])):
        tag = f"{red}ERROR{reset}" if g["severity"] == "error" else f"{yellow}WARN {reset}"
        lines.append(f"  {tag} [{g['kind']}] {g['message']}")
        if g.get("fix"):
            lines.append(f"        {dim}fix: {g['fix']}{reset}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Find what payments.yaml does not know about.")
    parser.add_argument("--offline", action="store_true", help="Registry-only checks, no network")
    parser.add_argument("--no-calendar", action="store_true", help="Skip the Zoho calendar check")
    args = parser.parse_args()

    gaps = asyncio.run(run_all(offline=args.offline, calendar=not args.no_calendar))
    errors = sum(1 for g in gaps if g["severity"] == "error")
    print(f"\nREGISTRY GAP CHECK  ({len(gaps)} gap(s), {errors} error(s))")
    print("-" * 70)
    print(format_gaps(gaps, color=sys.stdout.isatty()))
    sys.exit(1 if gaps else 0)


if __name__ == "__main__":
    main()
