#!/usr/bin/env python3
"""
Account connection health for the two balance sources: Monarch Money and Zoho Books.

Answers "which of my accounts are disconnected or out of sync?" and, in --warn
mode, prints a short banner so that every command presenting numbers can say
up front which of those numbers rest on a stale feed.

Checks
  Monarch  credential.updateRequired / disconnectedFromDataProviderAt  -> error
           displayLastUpdatedAt older than --monarch-stale-days (3)    -> warning
           (manual, hidden, deactivated and sync-disabled accounts are "parked"
           and only listed in the full report)
  Zoho     active bank/credit-card account with no bank feed            -> warning
           refresh_status != completed, mfa_required, consent expired   -> error
           feeds_last_refresh_date older than --zoho-stale-days (2)     -> warning
           book balance != bank balance, or uncategorized feed lines     -> warning

Modes
  python sync_status.py                 full report: tables + findings
  python sync_status.py --warn          banner only (stderr); always exits 0
  python sync_status.py --fail-on error gate for task flows: exit 1 on an error
  python sync_status.py --json          machine-readable findings
  python sync_status.py --fresh         ignore the cache

Both sources are cached in .sync_status_cache.json for --ttl minutes (30) so a
chain of tasks pays for one Monarch login and one Zoho call, not one per step.
Zoho has a 1,000 calls/day cap on the trial tier; this uses one call per refresh.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_a, **_k):
        return False

HERE = Path(__file__).parent
# Secrets and caches live in DATA_DIR so a run from a git worktree still uses the
# one durable copy in the main checkout (same convention as zoho_books_recon.py).
DATA_DIR = Path(os.getenv("FINANCE_TOOLS_DATA", HERE))
load_dotenv(DATA_DIR / ".env")
load_dotenv(HERE / ".env")

CACHE_FILE = DATA_DIR / ".sync_status_cache.json"
ZOHO_BASE = "https://www.zohoapis.com/books/v3"
ZOHO_TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"
ZOHO_TOKEN_CACHE = DATA_DIR / ".zoho_books_token.json"

DEFAULT_TTL_MINUTES = 30
ERROR_TTL_MINUTES = 5
MONARCH_STALE_DAYS = 3
ZOHO_STALE_DAYS = 2
# A no-feed account is kept current by importing its monthly statement; one missed
# cycle shows up as a newest-transaction date older than this.
STATEMENT_STALE_DAYS = 35

MONARCH_FIX = "Open Monarch -> Accounts -> the institution -> Reconnect / re-enter credentials."
ZOHO_FEED_FIX = "Zoho Books -> Banking -> the account -> Connect bank feed (or import a statement)."
ZOHO_REFRESH_FIX = "Zoho Books -> Banking -> the account -> Refresh feed; re-authorize if prompted."


# --- helpers -----------------------------------------------------------------


def _parse_date(value):
    """ISO datetime/date string -> date, or None."""
    if not value:
        return None
    s = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None


def _age_days(value, today):
    d = _parse_date(value)
    return None if d is None else (today - d).days


def _money(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _finding(source, severity, subject, message, fix=""):
    return {"source": source, "severity": severity, "subject": subject,
            "message": message, "fix": fix}


# --- Monarch -----------------------------------------------------------------


def slim_monarch_account(a):
    """Keep only what the rules need, so the cache stays small."""
    cred = a.get("credential") or {}
    inst = a.get("institution") or {}
    cred_inst = cred.get("institution") or {}
    return {
        "name": a.get("displayName") or "(unnamed)",
        "type": (a.get("type") or {}).get("name", ""),
        "institution": inst.get("name") or cred_inst.get("name") or "",
        "balance": a.get("currentBalance", 0),
        "updated": a.get("displayLastUpdatedAt") or a.get("updatedAt"),
        "is_manual": bool(a.get("isManual")),
        "hidden": bool(a.get("isHidden")),
        "deactivated": a.get("deactivatedAt"),
        "sync_disabled": bool(a.get("syncDisabled")),
        "credential_id": cred.get("id"),
        "update_required": bool(cred.get("updateRequired")),
        "disconnected_at": cred.get("disconnectedFromDataProviderAt"),
        "provider": cred.get("dataProvider") or a.get("dataProvider") or "",
        "institution_status": cred_inst.get("status"),
    }


def monarch_is_parked(a):
    return a["is_manual"] or a["hidden"] or bool(a["deactivated"]) or a["sync_disabled"]


def monarch_findings(accounts, today=None, stale_days=MONARCH_STALE_DAYS):
    """Rules over slimmed Monarch accounts. One finding per broken credential."""
    today = today or datetime.now(timezone.utc).date()
    live = [a for a in accounts if not monarch_is_parked(a)]

    by_cred = {}
    for a in live:
        key = a["credential_id"] or f"acct:{a['name']}"
        by_cred.setdefault(key, []).append(a)

    findings = []
    for group in by_cred.values():
        # Connection-level problems: one finding per credential, naming every account on it.
        first = group[0]
        if not (first["disconnected_at"] or first["update_required"]):
            # Healthy credential: staleness is judged per account, because one credential
            # can carry a dead duplicate next to live siblings (Apple Cash, Oct 2026).
            for a in group:
                age = _age_days(a["updated"], today)
                if age is not None and age > stale_days:
                    bal = f" (${abs(_money(a['balance'])):,.2f})" if a["balance"] not in (None, 0) else ""
                    findings.append(_finding(
                        "monarch", "warning", f"{a['institution'] or a['provider']} · {a['name']}{bal}",
                        f"stale: last sync {_parse_date(a['updated'])} ({age}d ago), no connection error reported",
                        "Refresh the connection in Monarch; if this is a duplicate of a live account, "
                        "hide or delete it."))
            continue
        names = ", ".join(sorted(a["name"] for a in group))
        label = f"{first['institution'] or first['provider']} · {names}"
        ages = [x for x in (_age_days(a["updated"], today) for a in group) if x is not None]
        last = min((_parse_date(a["updated"]) for a in group if _parse_date(a["updated"])), default=None)
        when = f"last sync {last} ({max(ages)}d ago)" if last and ages else "never synced"
        if first["disconnected_at"]:
            findings.append(_finding("monarch", "error", label,
                                     f"disconnected from {first['provider'] or 'the data provider'} "
                                     f"on {str(first['disconnected_at'])[:10]}; {when}", MONARCH_FIX))
        else:
            findings.append(_finding("monarch", "error", label,
                                     f"needs re-authentication in Monarch; {when}", MONARCH_FIX))
    findings.sort(key=lambda f: (f["severity"] != "error", f["subject"]))
    return findings


async def fetch_monarch_accounts():
    from monarch_client import get_monarch_client, is_auth_error, auth_error_message
    mm = await get_monarch_client(False)
    if mm is None:
        raise RuntimeError("no Monarch session (run: task finance:balances:login)")
    try:
        data = await mm.get_accounts()
    except Exception as e:
        if is_auth_error(e):
            raise RuntimeError(auth_error_message()) from e
        raise
    return [slim_monarch_account(a) for a in data.get("accounts", [])]


# --- Zoho Books --------------------------------------------------------------


def slim_zoho_account(a):
    consent = a.get("consent_info") or {}
    return {
        "id": a.get("account_id"),
        "name": a.get("account_name") or "(unnamed)",
        "type": a.get("account_type") or "",
        "active": bool(a.get("is_active", True)),
        "book_balance": _money(a.get("balance")),
        "bank_balance": _money(a.get("bank_balance")),
        "uncategorized": int(a.get("uncategorized_transactions") or 0),
        "feed_status": a.get("feed_status") or "",
        "refresh_status": a.get("refresh_status") or "",
        "last_refresh": a.get("feeds_last_refresh_date") or "",
        "mfa_required": bool(a.get("mfa_required")),
        "consent_expired": consent.get("is_consent_expired") in (True, "true", "True", 1, "1"),
        # Newest posted transaction (filled in for no-feed accounts, which are kept
        # current by statement imports instead of a feed). None = not looked up.
        "last_txn": a.get("_last_txn"),
    }


def zoho_has_feed(a):
    return bool(a["refresh_status"] or a["last_refresh"])


def zoho_is_parked(a):
    return not a["active"] or a["type"] not in ("bank", "credit_card")


def zoho_findings(accounts, today=None, stale_days=ZOHO_STALE_DAYS,
                  statement_stale_days=STATEMENT_STALE_DAYS):
    today = today or datetime.now(timezone.utc).date()
    findings = []
    for a in accounts:
        if zoho_is_parked(a):
            continue
        name = a["name"]
        if not zoho_has_feed(a):
            # Kept current by statement imports (e.g. the closed Chase Ink being paid
            # down, Oct 2026): judge it by its newest posted transaction, not by the feed.
            if abs(a["book_balance"]) < 0.01:
                findings.append(_finding("zoho", "warning", name,
                                         "no bank feed and a $0.00 balance: paid off?",
                                         "Mark the account inactive in Zoho Books and retire its statement-import reminder."))
                continue
            last = a.get("last_txn")
            age = _age_days(last, today)
            if not last:
                findings.append(_finding("zoho", "warning", name,
                                         f"no bank feed and no posted transactions found; book balance "
                                         f"${a['book_balance']:,.2f} can drift unnoticed", ZOHO_FEED_FIX))
            elif age is not None and age > statement_stale_days:
                findings.append(_finding("zoho", "warning", name,
                                         f"statement import overdue: newest transaction {last} ({age}d ago); "
                                         f"book balance ${a['book_balance']:,.2f} is behind",
                                         "Import the latest statement: Zoho Books -> Banking -> the account -> Import Statement."))
            continue
        if a["mfa_required"]:
            findings.append(_finding("zoho", "error", name, "feed is waiting on MFA", ZOHO_REFRESH_FIX))
        if a["consent_expired"]:
            findings.append(_finding("zoho", "error", name, "bank consent expired", ZOHO_REFRESH_FIX))
        if a["refresh_status"] and a["refresh_status"] != "completed":
            findings.append(_finding("zoho", "error", name,
                                     f"last feed refresh status '{a['refresh_status']}'"
                                     + (f" ({a['feed_status']})" if a["feed_status"] else ""),
                                     ZOHO_REFRESH_FIX))
        age = _age_days(a["last_refresh"], today)
        if age is not None and age > stale_days:
            findings.append(_finding("zoho", "warning", name,
                                     f"feed last refreshed {a['last_refresh']} ({age}d ago)", ZOHO_REFRESH_FIX))
        diff = round(a["book_balance"] - a["bank_balance"], 2)
        if abs(diff) >= 0.01:
            findings.append(_finding("zoho", "warning", name,
                                     f"book ${a['book_balance']:,.2f} vs bank ${a['bank_balance']:,.2f} "
                                     f"(diff {diff:+,.2f})",
                                     "Categorize/match the open feed lines: task finance:zoho:recon"))
        elif a["uncategorized"]:
            findings.append(_finding("zoho", "warning", name,
                                     f"{a['uncategorized']} uncategorized feed line(s)",
                                     "task finance:zoho:recon"))
    findings.sort(key=lambda f: (f["severity"] != "error", f["subject"]))
    return findings


def _zoho_access_token():
    import requests
    if ZOHO_TOKEN_CACHE.exists():
        try:
            c = json.loads(ZOHO_TOKEN_CACHE.read_text())
            if c.get("exp", 0) > time.time() + 60:
                return c["access_token"]
        except (ValueError, KeyError):
            pass
    refresh = os.getenv("ZOHO_BOOKS_REFRESH_TOKEN")
    if not refresh or not os.getenv("ZOHO_BOOKS_ORG_ID"):
        raise RuntimeError("ZOHO_BOOKS_REFRESH_TOKEN / ZOHO_BOOKS_ORG_ID not set in finance-tools/.env")
    r = requests.post(ZOHO_TOKEN_URL, data={
        "grant_type": "refresh_token",
        "client_id": os.getenv("ZOHO_CLIENT_ID"),
        "client_secret": os.getenv("ZOHO_CLIENT_SECRET"),
        "refresh_token": refresh,
    }, timeout=20).json()
    if "access_token" not in r:
        raise RuntimeError(f"Zoho token error: {r}")
    ZOHO_TOKEN_CACHE.write_text(json.dumps({"access_token": r["access_token"], "exp": time.time() + 3300}))
    return r["access_token"]


def fetch_zoho_accounts():
    import requests
    token = _zoho_access_token()
    r = requests.get(f"{ZOHO_BASE}/bankaccounts", timeout=60,
                     params={"organization_id": os.getenv("ZOHO_BOOKS_ORG_ID")},
                     headers={"Authorization": f"Zoho-oauthtoken {token}"})
    if r.status_code == 401:
        ZOHO_TOKEN_CACHE.unlink(missing_ok=True)
        token = _zoho_access_token()
        r = requests.get(f"{ZOHO_BASE}/bankaccounts", timeout=60,
                         params={"organization_id": os.getenv("ZOHO_BOOKS_ORG_ID")},
                         headers={"Authorization": f"Zoho-oauthtoken {token}"})
    if r.status_code >= 300:
        raise RuntimeError(f"Zoho /bankaccounts -> {r.status_code}: {r.text[:200]}")
    raw = r.json().get("bankaccounts", [])
    # One extra call per active no-feed account: its newest posted transaction, so a
    # statement-maintained account is judged by import recency rather than nagged about.
    for a in raw:
        probe = slim_zoho_account(a)
        if zoho_is_parked(probe) or zoho_has_feed(probe) or abs(probe["book_balance"]) < 0.01:
            continue
        try:
            t = requests.get(f"{ZOHO_BASE}/banktransactions", timeout=60,
                             params={"organization_id": os.getenv("ZOHO_BOOKS_ORG_ID"), "account_id": a["account_id"],
                                     "per_page": 1, "sort_column": "date", "sort_order": "D"},
                             headers={"Authorization": f"Zoho-oauthtoken {token}"})
            txns = t.json().get("banktransactions", []) if t.status_code < 300 else []
            a["_last_txn"] = txns[0].get("date") if txns else ""
        except Exception:  # noqa: BLE001 - leave as None: "not looked up"
            a["_last_txn"] = None
    return [slim_zoho_account(a) for a in raw]


# --- cache + collect ---------------------------------------------------------


def _load_cache():
    try:
        return json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    try:
        CACHE_FILE.write_text(json.dumps(cache, indent=1, default=str))
    except OSError:
        pass


def _cache_fresh(entry, ttl_minutes, now):
    if not entry or not entry.get("fetched_at"):
        return False
    try:
        fetched = datetime.fromisoformat(entry["fetched_at"])
    except ValueError:
        return False
    ttl = ERROR_TTL_MINUTES if entry.get("error") else ttl_minutes
    return (now - fetched).total_seconds() < ttl * 60


async def collect(fresh=False, ttl_minutes=DEFAULT_TTL_MINUTES, sources=("monarch", "zoho"),
                  monarch_stale_days=MONARCH_STALE_DAYS, zoho_stale_days=ZOHO_STALE_DAYS):
    """Fetch (or reuse cached) account lists from both sources and apply the rules.

    Returns {"checked_at", "monarch": {...}, "zoho": {...}, "findings": [...]}.
    Each source dict has accounts (slim), error (str|None), fetched_at, from_cache.
    """
    now = datetime.now(timezone.utc)
    cache = _load_cache()
    result = {"checked_at": now.isoformat(), "findings": []}

    async def _get(source, fetcher):
        entry = cache.get(source) or {}
        if not fresh and _cache_fresh(entry, ttl_minutes, now):
            return {**entry, "from_cache": True}
        try:
            accounts = await fetcher() if asyncio.iscoroutinefunction(fetcher) else \
                await asyncio.to_thread(fetcher)
            entry = {"fetched_at": now.isoformat(), "accounts": accounts, "error": None}
        except Exception as e:  # noqa: BLE001 - any outage is reported, never raised
            entry = {"fetched_at": now.isoformat(), "accounts": [], "error": f"{type(e).__name__}: {str(e)[:200]}"}
        cache[source] = entry
        return {**entry, "from_cache": False}

    tasks = {}
    if "monarch" in sources:
        tasks["monarch"] = _get("monarch", fetch_monarch_accounts)
    if "zoho" in sources:
        tasks["zoho"] = _get("zoho", fetch_zoho_accounts)
    got = await asyncio.gather(*tasks.values())
    for source, entry in zip(tasks.keys(), got):
        result[source] = entry
    _save_cache(cache)

    today = now.date()
    if "monarch" in result:
        m = result["monarch"]
        if m["error"]:
            result["findings"].append(_finding("monarch", "error", "Monarch Money",
                                               f"could not be checked: {m['error']}",
                                               "task finance:balances:login"))
        else:
            result["findings"] += monarch_findings(m["accounts"], today, monarch_stale_days)
    if "zoho" in result:
        z = result["zoho"]
        if z["error"]:
            result["findings"].append(_finding("zoho", "error", "Zoho Books",
                                               f"could not be checked: {z['error']}",
                                               "check ZOHO_* in finance-tools/.env"))
        else:
            result["findings"] += zoho_findings(z["accounts"], today, zoho_stale_days)
    return result


# --- output ------------------------------------------------------------------


class Color:
    RED = "\033[91m"
    YELLOW = "\033[93m"
    GREEN = "\033[92m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    @classmethod
    def disable(cls):
        for k in ("RED", "YELLOW", "GREEN", "DIM", "BOLD", "RESET"):
            setattr(cls, k, "")


def _checked_label(result):
    stamps = [result[s]["fetched_at"] for s in ("monarch", "zoho") if s in result and result[s].get("fetched_at")]
    if not stamps:
        return "not checked"
    oldest = min(datetime.fromisoformat(s) for s in stamps)
    mins = int((datetime.now(timezone.utc) - oldest).total_seconds() // 60)
    return "checked just now" if mins < 1 else f"checked {mins} min ago"


def warn_lines(result, color=True):
    """The banner other commands print before their numbers. [] when all is well."""
    c = Color if color else type("NoColor", (), {k: "" for k in vars(Color) if k.isupper()})
    findings = result["findings"]
    errors = [f for f in findings if f["severity"] == "error"]
    if not findings:
        counts = []
        if "monarch" in result and not result["monarch"].get("error"):
            live = [a for a in result["monarch"]["accounts"] if not monarch_is_parked(a)]
            counts.append(f"{len({a['credential_id'] for a in live})} Monarch connections")
        if "zoho" in result and not result["zoho"].get("error"):
            feeds = [a for a in result["zoho"]["accounts"] if not zoho_is_parked(a) and zoho_has_feed(a)]
            counts.append(f"{len(feeds)} Zoho feeds")
        return [f"{c.GREEN}✓{c.RESET} sync check: {' and '.join(counts) or 'nothing'} current "
                f"{c.DIM}({_checked_label(result)}){c.RESET}"]
    tone = c.RED if errors else c.YELLOW
    lines = [f"{tone}{c.BOLD}⚠  SYNC WARNING{c.RESET}{tone} — {len(findings)} account connection issue(s)"
             f"{c.RESET} {c.DIM}({_checked_label(result)}){c.RESET}"]
    for f in findings:
        mark = f"{c.RED}✗{c.RESET}" if f["severity"] == "error" else f"{c.YELLOW}!{c.RESET}"
        lines.append(f"   {mark} {f['source']:<7} {f['subject']}: {f['message']}")
    lines.append(f"   {c.DIM}Numbers drawn from these accounts may be stale. "
                 f"Detail and fixes: task finance:sync-status{c.RESET}")
    return lines


def banner_html(result):
    """The same banner as warn_lines, as an HTML block for the emailed reports."""
    findings = result.get("findings", [])
    if not findings:
        return ('<p style="color: #16a34a; margin: 0 0 16px 0; font-size: 13px;">'
                f'&#10003; Account sync: all live connections current ({_checked_label(result)})</p>')
    errors = any(f["severity"] == "error" for f in findings)
    bg, border, fg = ("#fee2e2", "#dc2626", "#991b1b") if errors else ("#fef9c3", "#d97706", "#92400e")
    rows = "".join(
        f'<li style="margin: 2px 0;"><b>{f["source"]}</b> &middot; {f["subject"]}: {f["message"]}</li>'
        for f in findings)
    return (f'<div style="background: {bg}; border-left: 4px solid {border}; color: {fg}; '
            f'padding: 10px 12px; margin: 0 0 16px 0; font-size: 13px;">'
            f'<b>&#9888; Sync warning</b> &mdash; {len(findings)} account connection issue(s) '
            f'({_checked_label(result)}). Numbers drawn from these accounts may be stale.'
            f'<ul style="margin: 6px 0 0 0; padding-left: 18px;">{rows}</ul></div>')


def inject_banner_html(html, result):
    """Insert banner_html right after the opening <body ...> tag (or at the top if none)."""
    banner = banner_html(result)
    i = html.find("<body")
    if i < 0:
        return banner + html
    j = html.find(">", i)
    return html[:j + 1] + "\n" + banner + html[j + 1:]


def print_full_report(result, color=True):
    c = Color
    print(f"\n{c.BOLD}ACCOUNT SYNC STATUS{c.RESET}  {c.DIM}{_checked_label(result)}{c.RESET}")

    if "monarch" in result:
        m = result["monarch"]
        src = "cache" if m.get("from_cache") else "live"
        print(f"\n{c.BOLD}Monarch Money{c.RESET} {c.DIM}({src}){c.RESET}")
        if m["error"]:
            print(f"  {c.RED}unavailable: {m['error']}{c.RESET}")
        else:
            print(f"  {'account':<48} {'institution':<22} {'last sync':<11} {'age':>5}  status")
            for a in sorted(m["accounts"], key=lambda a: (monarch_is_parked(a), a["institution"], a["name"])):
                age = _age_days(a["updated"], datetime.now(timezone.utc).date())
                if monarch_is_parked(a):
                    why = "manual" if a["is_manual"] else "deactivated" if a["deactivated"] else \
                          "hidden" if a["hidden"] else "sync off"
                    status = f"{c.DIM}parked ({why}){c.RESET}"
                elif a["disconnected_at"]:
                    status = f"{c.RED}disconnected{c.RESET}"
                elif a["update_required"]:
                    status = f"{c.RED}re-auth needed{c.RESET}"
                elif age is not None and age > MONARCH_STALE_DAYS:
                    status = f"{c.YELLOW}stale{c.RESET}"
                else:
                    status = f"{c.GREEN}ok{c.RESET}"
                last = str(_parse_date(a["updated"]) or "-")
                print(f"  {a['name'][:48]:<48} {a['institution'][:22]:<22} {last:<11} "
                      f"{'' if age is None else age:>5}  {status}")

    if "zoho" in result:
        z = result["zoho"]
        src = "cache" if z.get("from_cache") else "live"
        print(f"\n{c.BOLD}Zoho Books{c.RESET} {c.DIM}({src}){c.RESET}")
        if z["error"]:
            print(f"  {c.RED}unavailable: {z['error']}{c.RESET}")
        else:
            print(f"  {'account':<48} {'last refresh':<12} {'book':>12} {'bank':>12} {'uncat':>5}  status")
            for a in sorted(z["accounts"], key=lambda a: (zoho_is_parked(a), a["name"])):
                if zoho_is_parked(a):
                    why = "inactive" if not a["active"] else a["type"]
                    status = f"{c.DIM}parked ({why}){c.RESET}"
                elif not zoho_has_feed(a):
                    last_age = _age_days(a.get("last_txn"), datetime.now(timezone.utc).date())
                    if abs(a["book_balance"]) < 0.01:
                        status = f"{c.YELLOW}no feed, $0: retire?{c.RESET}"
                    elif last_age is None:
                        status = f"{c.YELLOW}no feed, no txns{c.RESET}"
                    elif last_age > STATEMENT_STALE_DAYS:
                        status = f"{c.YELLOW}statement overdue ({last_age}d){c.RESET}"
                    else:
                        status = f"{c.GREEN}ok (statements, last {a['last_txn']}){c.RESET}"
                elif a["mfa_required"] or a["consent_expired"] or (a["refresh_status"] and a["refresh_status"] != "completed"):
                    status = f"{c.RED}feed broken{c.RESET}"
                elif abs(a["book_balance"] - a["bank_balance"]) >= 0.01 or a["uncategorized"]:
                    status = f"{c.YELLOW}unreconciled{c.RESET}"
                else:
                    status = f"{c.GREEN}ok{c.RESET}"
                print(f"  {a['name'][:48]:<48} {a['last_refresh'] or '-':<12} {a['book_balance']:>12,.2f} "
                      f"{a['bank_balance']:>12,.2f} {a['uncategorized']:>5}  {status}")

    print()
    findings = result["findings"]
    if not findings:
        print(f"{c.GREEN}All live connections current.{c.RESET}")
        return
    print(f"{c.BOLD}Findings{c.RESET}")
    for f in findings:
        tone = c.RED if f["severity"] == "error" else c.YELLOW
        print(f"  {tone}{f['severity']:<7}{c.RESET} {f['source']:<7} {f['subject']}")
        print(f"          {f['message']}")
        if f["fix"]:
            print(f"          {c.DIM}fix: {f['fix']}{c.RESET}")


def main():
    p = argparse.ArgumentParser(description="Monarch + Zoho Books account connection health")
    p.add_argument("--warn", action="store_true",
                   help="banner only, to stderr; exit 0 always (run before commands that show numbers)")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.add_argument("--fresh", action="store_true", help="ignore the cache")
    p.add_argument("--ttl", type=int, default=int(os.getenv("SYNC_STATUS_TTL_MINUTES", DEFAULT_TTL_MINUTES)),
                   help=f"cache lifetime in minutes (default {DEFAULT_TTL_MINUTES})")
    p.add_argument("--fail-on", choices=["error", "warning"],
                   help="exit 1 when a finding of this severity or worse exists (gate for task flows)")
    p.add_argument("--source", choices=["monarch", "zoho"], action="append",
                   help="check only this source (repeatable)")
    p.add_argument("--monarch-stale-days", type=int, default=MONARCH_STALE_DAYS)
    p.add_argument("--zoho-stale-days", type=int, default=ZOHO_STALE_DAYS)
    p.add_argument("--no-color", action="store_true")
    args = p.parse_args()

    color = not args.no_color and (sys.stderr.isatty() if args.warn else sys.stdout.isatty())
    if not color:
        Color.disable()

    result = asyncio.run(collect(fresh=args.fresh, ttl_minutes=args.ttl,
                                 sources=tuple(args.source) if args.source else ("monarch", "zoho"),
                                 monarch_stale_days=args.monarch_stale_days,
                                 zoho_stale_days=args.zoho_stale_days))

    if args.json:
        print(json.dumps(result, indent=1, default=str))
    elif args.warn:
        print("\n".join(warn_lines(result, color)), file=sys.stderr, flush=True)
    else:
        print_full_report(result, color)

    if args.fail_on:
        bad = {"error": ("error",), "warning": ("error", "warning")}[args.fail_on]
        if any(f["severity"] in bad for f in result["findings"]):
            sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:  # noqa: BLE001
        # In --warn mode the check must never block the command it precedes.
        if "--warn" in sys.argv:
            print(f"⚠  sync check skipped: {type(e).__name__}: {str(e)[:160]}", file=sys.stderr)
            sys.exit(0)
        raise
