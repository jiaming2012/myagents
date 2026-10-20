#!/usr/bin/env python3
"""
Account register check: is the Bills sheet one-to-one with reality?

The native Zoho Sheet "Bills" (tab `Chart of Accounts`) is the register the
operator looks at. payments.yaml `accounts:` is the machine registry every other
script uses. Monarch Money holds the personal accounts, Zoho Books the business
ones. This module proves, on every run, that the three layers agree:

    Chart of Accounts rows  <->  payments.yaml accounts  <->  Monarch / Zoho Books

"One-to-one" is checked on OPEN accounts, both directions, per owner:

  1. sheet rows well formed   - every row has a Type (Personal/Business x
                                Debit/Credit), a last4, and no duplicate last4
  2. sheet <-> registry        - same set of last4s per owner; a row or entry
                                marked closed on one side must be closed on both
  3. registry <-> Monarch      - every personal entry's monarch_match resolves to
                                exactly one active Monarch account, and every
                                active Monarch depository/credit account is
                                claimed by exactly one entry
  4. registry <-> Zoho Books   - the same for business entries via
                                zoho_books_match and Books' bank/credit_card
                                accounts

Every finding is an error (there is no warning tier for a register); `main()`
prints them and exits 1 if any exist. Balances are NOT compared yet (phase 2 of
docs/account-register-plan.md).

Scope (docs/account-register-plan.md, decisions of 2026-10-19): investments are
excluded until the Investments tab is rebuilt. Loans are also excluded for now:
the sheet keeps them on a separate `Loans` tab with its own shape. Only
depository/credit (sheet: Personal/Business Debit/Credit) accounts are checked.

Usage:
    python account_register.py                    # all four checks
    python account_register.py --category business
    python account_register.py --offline          # sheet <-> registry only
    python account_register.py --no-sheet         # registry <-> live only
    python account_register.py --json
"""

import argparse
import asyncio
import json
import os
import re
import sys

CATEGORIES = ("personal", "business")
SHEET_TAB = "Chart of Accounts"
DEFAULT_SHEET_ID = "efajyb8b15e86a20a48e0b3edab6cf4fb18c5"   # Bills, WorkDrive folder "finance"
SHEET_ID_ENV = "ZOHO_BILLS_SHEET_ID"

# sheet Type -> (owner, kind). Anything else is out of scope (Investment) or an error (blank).
SHEET_TYPES = {
    "Personal Debit": ("personal", "debit"),
    "Personal Credit": ("personal", "credit"),
    "Business Debit": ("business", "debit"),
    "Business Credit": ("business", "credit"),
}
OUT_OF_SCOPE_SHEET_TYPES = {"Investment"}
REGISTRY_TYPES = {"depository": "debit", "credit": "credit"}        # loan/investment skipped
MONARCH_TYPES = {"depository": "debit", "credit": "credit"}         # brokerage/loan/other skipped
BOOKS_TYPES = {"bank": "debit", "credit_card": "credit"}            # cash (Petty Cash etc.) skipped

MATCH_FIELD = {"personal": "monarch_match", "business": "zoho_books_match"}
SOURCE_NAME = {"personal": "Monarch", "business": "Zoho Books"}


def norm_name(s):
    """Casefold, drop symbols like ®/™, collapse whitespace (same rule as registry_gaps)."""
    if not s:
        return ""
    out = "".join(ch for ch in str(s) if ch.isalnum() or ch in " ().-_*•")
    return " ".join(out.casefold().split())


_LAST4_PATTERNS = (
    re.compile(r"\(\.\.\.[^)]*?([A-Za-z0-9]{4})\)"),   # Monarch "(...5153)", "(...*****0789)", "(...XXXX-4607)"
    re.compile(r"••\s*([A-Za-z0-9]{4})"),             # Zoho Books "Chase Ink ••7667"
    re.compile(r"\.\.\.\s*([A-Za-z0-9]{4})\b"),       # "...4637"
    re.compile(r"[-–]\s*([0-9]{4})\s*$"),             # "... Cash Rewards - 2119"
    re.compile(r"x{2,}([0-9]{4})$", re.I),            # account_number "xxxx1778"
)


def last4_of(*texts):
    """First last4-looking token found in any of the texts, else None."""
    for t in texts:
        if not t:
            continue
        for pat in _LAST4_PATTERNS:
            m = pat.search(str(t))
            if m:
                return m.group(1)
    return None


def _err(kind, message, fix=""):
    return {"severity": "error", "kind": kind, "message": message, "fix": fix}


def _acct(key, name, owner, kind, closed=False, balance=None, match=None, ref=""):
    return {"key": key, "name": name, "owner": owner, "kind": kind, "closed": bool(closed),
            "balance": balance, "match": match, "ref": ref}


# ---------------------------------------------------------------------------
# Normalisers: each side -> list of account dicts (pure)
# ---------------------------------------------------------------------------

def sheet_accounts(rows):
    """Chart of Accounts rows (Sheet Data API records: header-keyed dicts with
    row_index) -> (accounts, errors). Investment rows are skipped; rows with no
    Type, an unknown Type, or no last4 are errors. Closed = Status column says
    closed (case-insensitive); a 'Closed' note alone is an error so the operator
    adds the column rather than relying on free text."""
    accounts, errors = [], []
    for row in rows:
        ref = f"sheet row {row.get('row_index', '?')}"
        name = str(row.get("Name") or "").strip()
        typ = str(row.get("Type") or "").strip()
        if typ in OUT_OF_SCOPE_SHEET_TYPES:
            continue
        if not typ:
            errors.append(_err("sheet_row_untyped",
                               f"{ref} '{name}': no Type.",
                               "Set Type to Personal/Business Debit/Credit, Investment, or delete the row."))
            continue
        if typ not in SHEET_TYPES:
            errors.append(_err("sheet_row_type_unknown",
                               f"{ref} '{name}': Type {typ!r} is not one of {sorted(SHEET_TYPES)}."))
            continue
        owner, kind = SHEET_TYPES[typ]
        status = str(row.get("Status") or "").strip().casefold()
        closed = status == "closed"
        notes = str(row.get("notes") or row.get("Notes") or "")
        if not closed and "closed" in notes.casefold():
            errors.append(_err("sheet_row_closed_in_notes",
                               f"{ref} '{name}': notes say closed but there is no Status column / value.",
                               "Add a Status column to Chart of Accounts and set it to 'closed'."))
        key = str(row.get("Account Last 4") or "").strip() or None
        if key is None and not closed:
            errors.append(_err("sheet_row_no_last4",
                               f"{ref} '{name}' ({typ}): no Account Last 4.",
                               "Fill Account Last 4; it is the join key to the registry."))
            continue
        bal_col = "Credit Balance" if kind == "credit" else "Debit Balance"
        bal = row.get(bal_col)
        balance = float(bal) if isinstance(bal, (int, float)) or (isinstance(bal, str) and bal.strip().lstrip("-").replace(".", "", 1).isdigit()) else None
        accounts.append(_acct(key, name, owner, kind, closed=closed, balance=balance, ref=ref))
    errors += _duplicate_keys(accounts, "sheet_duplicate_last4", "Chart of Accounts")
    return accounts, errors


def registry_accounts(config):
    """payments.yaml accounts -> account dicts (depository/credit only; loans and
    investments are out of scope). Closed = `status: closed` or `inactive: true`."""
    out = []
    for a in config.get("accounts", []):
        kind = REGISTRY_TYPES.get(a.get("type"))
        owner = a.get("category")
        if kind is None or owner not in CATEGORIES:
            continue
        closed = str(a.get("status") or "").casefold() == "closed" or bool(a.get("inactive"))
        out.append(_acct(a.get("last4") or None, a.get("name", a.get("id")), owner, kind,
                         closed=closed, match=a.get(MATCH_FIELD[owner]), ref=f"registry {a.get('id')}"))
    return out


def monarch_accounts(balances):
    """coverage_report.fetch_monarch_balances() shape ({display name: {balance,
    type, hidden, deactivated}}) -> active depository/credit accounts. Hidden or
    deactivated connections are dropped: they are Monarch's stale duplicates."""
    out = []
    for name, d in (balances or {}).items():
        kind = MONARCH_TYPES.get(d.get("type"))
        if kind is None or d.get("hidden") or d.get("deactivated"):
            continue
        out.append(_acct(last4_of(name), name, "personal", kind, balance=d.get("balance"),
                         ref="monarch"))
    return out


def books_accounts(bankaccounts):
    """Zoho Books /bankaccounts records -> bank and credit-card accounts. Closed =
    is_active false or '(closed)' in the name. Petty Cash / Undeposited Funds
    (type cash) are Books system accounts, not bank accounts, and are skipped."""
    out = []
    for a in bankaccounts or []:
        kind = BOOKS_TYPES.get(a.get("account_type"))
        if kind is None:
            continue
        name = a.get("account_name", "")
        closed = (a.get("is_active") is False) or "(closed)" in name.casefold()
        out.append(_acct(last4_of(a.get("account_number"), name), name, "business", kind,
                         closed=closed, balance=a.get("balance"), ref=f"books {a.get('account_id')}"))
    return out


# ---------------------------------------------------------------------------
# Checks (pure)
# ---------------------------------------------------------------------------

def _duplicate_keys(accounts, kind, where):
    seen, errors = {}, []
    for a in accounts:
        if a["closed"] or not a["key"]:
            continue
        k = (a["owner"], a["key"])
        if k in seen:
            errors.append(_err(kind, f"{where}: last4 {a['key']} ({a['owner']}) appears twice: "
                                     f"'{seen[k]['name']}' and '{a['name']}'.",
                               "last4 must be unique per owner; fix one of them."))
        else:
            seen[k] = a
    return errors


def check_sheet_vs_registry(sheet, registry, category=None):
    """Open sheet rows and open registry entries must be the same set of last4s
    per owner. Closed on one side only is also an error."""
    errors = []
    for owner in CATEGORIES:
        if category and owner != category:
            continue
        s = {a["key"]: a for a in sheet if a["owner"] == owner and a["key"]}
        r = {a["key"]: a for a in registry if a["owner"] == owner and a["key"]}
        for key in sorted(set(s) | set(r)):
            sa, ra = s.get(key), r.get(key)
            if sa and not ra:
                if not sa["closed"]:
                    errors.append(_err("sheet_not_in_registry",
                                       f"{sa['ref']} '{sa['name']}' ({owner} {key}) is not in payments.yaml.",
                                       "Add the account to payments.yaml accounts:, or mark the row closed."))
            elif ra and not sa:
                if not ra["closed"]:
                    errors.append(_err("registry_not_in_sheet",
                                       f"{ra['ref']} '{ra['name']}' ({owner} {key}) is not in the Chart of Accounts.",
                                       "Add a row to the sheet, or set status: closed in payments.yaml."))
            elif sa["closed"] != ra["closed"]:
                side = "sheet" if sa["closed"] else "registry"
                errors.append(_err("closed_mismatch",
                                   f"{owner} {key} '{ra['name']}': closed in the {side} only.",
                                   "Close it on both sides or neither."))
            elif sa["kind"] != ra["kind"]:
                errors.append(_err("kind_mismatch",
                                   f"{owner} {key} '{ra['name']}': sheet says {sa['kind']}, registry says {ra['kind']}."))
    errors += _duplicate_keys(registry, "registry_duplicate_last4", "payments.yaml")
    return errors


def check_registry_vs_live(registry, live, owner):
    """Every open registry entry of `owner` must resolve, by its match name, to
    exactly one open live account; every open live account must be claimed by
    exactly one registry entry. The matched live account's last4 (when it shows
    one) must agree with the registry's."""
    source = SOURCE_NAME[owner]
    field = MATCH_FIELD[owner]
    errors = []
    live_by_name = {}
    for a in live:
        live_by_name.setdefault(norm_name(a["name"]), []).append(a)
    claimed = {}
    for ra in (a for a in registry if a["owner"] == owner):
        if not ra["match"]:
            if not ra["closed"]:
                errors.append(_err("registry_no_match_name",
                                   f"{ra['ref']} '{ra['name']}': no {field}.",
                                   f"Set {field} to the exact {source} account name."))
            continue
        hits = live_by_name.get(norm_name(ra["match"]), [])
        if len(hits) != 1:
            if ra["closed"] and not hits:
                continue
            errors.append(_err("match_not_found" if not hits else "match_ambiguous",
                               f"{ra['ref']} '{ra['name']}': {field} {ra['match']!r} matches "
                               f"{len(hits)} {source} account(s).",
                               f"Use the exact {source} name; it must match one account."))
            continue
        la = hits[0]
        if la["closed"] and not ra["closed"]:
            errors.append(_err("live_closed",
                               f"{ra['ref']} '{ra['name']}': {source} shows '{la['name']}' closed.",
                               "Set status: closed in payments.yaml and in the sheet."))
        elif ra["closed"] and not la["closed"]:
            errors.append(_err("registry_closed_live_open",
                               f"{ra['ref']} '{ra['name']}' is closed in payments.yaml but open in {source}."))
        if la["key"] and ra["key"] and la["key"] != ra["key"]:
            errors.append(_err("last4_mismatch",
                               f"{ra['ref']} '{ra['name']}': registry last4 {ra['key']} but {source} "
                               f"'{la['name']}' shows {la['key']}.",
                               "Correct last4 in payments.yaml (and the sheet row)."))
        if la["kind"] != ra["kind"]:
            errors.append(_err("kind_mismatch",
                               f"{ra['ref']} '{ra['name']}': registry type {ra['kind']}, {source} {la['kind']}."))
        claimed.setdefault(id(la), []).append(ra)
    for la in live:
        if la["closed"]:
            continue
        n = len(claimed.get(id(la), []))
        if n == 0:
            errors.append(_err("live_not_in_registry",
                               f"{source} account '{la['name']}' ({la['kind']}"
                               f"{', last4 ' + la['key'] if la['key'] else ''}) is not in payments.yaml.",
                               f"Add it to payments.yaml accounts: with {field}: {la['name']!r}, and a sheet row."))
        elif n > 1:
            errors.append(_err("live_claimed_twice",
                               f"{source} account '{la['name']}' is matched by "
                               f"{', '.join(r['ref'] for r in claimed[id(la)])}."))
    return errors


def check_sources(source_status):
    """A source that could not be reached is an error: silence is not agreement."""
    errors = []
    for name, status in (source_status or {}).items():
        if status != "ok":
            errors.append(_err("source_unavailable", f"{name}: {status}",
                               "Fix the connection or re-run with --offline / --no-sheet."))
    return errors


def run_checks(config, sheet_rows=None, monarch=None, books=None, category=None, source_status=None):
    """All checks over pre-fetched data. A side passed as None is skipped (its
    checks are not run), which is how --offline and --no-sheet work."""
    errors = check_sources(source_status)
    registry = [a for a in registry_accounts(config) if not category or a["owner"] == category]
    if sheet_rows is not None:
        sheet, sheet_errors = sheet_accounts(sheet_rows)
        sheet = [a for a in sheet if not category or a["owner"] == category]
        errors += sheet_errors
        errors += check_sheet_vs_registry(sheet, registry, category)
    else:
        errors += _duplicate_keys(registry, "registry_duplicate_last4", "payments.yaml")
    if monarch is not None and category in (None, "personal"):
        errors += check_registry_vs_live(registry, monarch_accounts(monarch), "personal")
    if books is not None and category in (None, "business"):
        errors += check_registry_vs_live(registry, books_accounts(books), "business")
    return errors


# ---------------------------------------------------------------------------
# Data gathering (I/O)
# ---------------------------------------------------------------------------

def _gather_sheet(source_status, sheet_id):
    try:
        from zoho_sheet import records
        rows = records(sheet_id, SHEET_TAB)
        source_status["sheet"] = "ok" if rows else "no rows returned"
        return rows
    except SystemExit as e:        # zoho_sheet exits on API/auth errors
        source_status["sheet"] = str(e)[:160]
    except Exception as e:
        source_status["sheet"] = f"{type(e).__name__}: {e}"[:160]
    return None


async def _gather_monarch(source_status):
    try:
        from coverage_report import fetch_monarch_balances
        from monarch_client import MonarchAuthError
        try:
            balances = await fetch_monarch_balances()
            source_status["monarch"] = "ok" if balances else "no accounts returned"
            return balances
        except MonarchAuthError as e:
            source_status["monarch"] = str(e)
        except Exception as e:
            source_status["monarch"] = f"{type(e).__name__}: {e}"[:160]
    except ImportError as e:
        source_status["monarch"] = f"import failed: {e}"
    return None


def _gather_books(source_status):
    try:
        import zoho_books_recon as zb
        try:
            z = zb.Zoho()
            accts = z.get("/bankaccounts")["bankaccounts"]
            source_status["books"] = "ok"
            return accts
        except SystemExit as e:
            source_status["books"] = str(e)[:160]
        except Exception as e:
            source_status["books"] = f"{type(e).__name__}: {e}"[:160]
    except ImportError as e:
        source_status["books"] = f"import failed: {e}"
    return None


async def run_all(category=None, offline=False, no_sheet=False, sheet_id=None):
    from coverage_report import load_payments_yaml
    config = load_payments_yaml()
    status = {}
    sheet_rows = None if no_sheet else _gather_sheet(status, sheet_id or os.getenv(SHEET_ID_ENV, DEFAULT_SHEET_ID))
    monarch = books = None
    if not offline:
        if category in (None, "personal"):
            monarch = await _gather_monarch(status)
        if category in (None, "business"):
            books = _gather_books(status)
    return run_checks(config, sheet_rows=sheet_rows, monarch=monarch, books=books,
                      category=category, source_status=status)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def format_errors(errors, color=True):
    if not errors:
        return "  Register is one-to-one."
    red = "\033[31m" if color else ""
    dim = "\033[2m" if color else ""
    reset = "\033[0m" if color else ""
    lines = []
    for e in sorted(errors, key=lambda e: (e["kind"], e["message"])):
        lines.append(f"  {red}ERROR{reset} [{e['kind']}] {e['message']}")
        if e.get("fix"):
            lines.append(f"        {dim}fix: {e['fix']}{reset}")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="Prove the Bills sheet, payments.yaml and Monarch/Zoho Books agree.")
    p.add_argument("--category", choices=CATEGORIES, help="only this owner's accounts")
    p.add_argument("--offline", action="store_true", help="skip Monarch and Zoho Books (sheet <-> registry only)")
    p.add_argument("--no-sheet", action="store_true", help="skip the sheet (registry <-> live only)")
    p.add_argument("--sheet-id", help=f"Zoho Sheet resource id (default ${SHEET_ID_ENV} or {DEFAULT_SHEET_ID})")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    a = p.parse_args()
    errors = asyncio.run(run_all(category=a.category, offline=a.offline, no_sheet=a.no_sheet,
                                 sheet_id=a.sheet_id))
    if a.json:
        print(json.dumps(errors, indent=2))
    else:
        scope = f"  [{a.category}]" if a.category else ""
        print(f"\nACCOUNT REGISTER CHECK{scope}  ({len(errors)} error(s))")
        print("-" * 70)
        print(format_errors(errors, color=sys.stdout.isatty()))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
