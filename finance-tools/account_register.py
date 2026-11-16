#!/usr/bin/env python3
"""
Account register: payments.yaml is the register; the Bills sheet is rendered from it.

payments.yaml `accounts:` is the only place an account's identity is entered (id,
name, institution, last4, type, category, status). debts.yaml adds a card's terms
(APR, credit limit, due day) and the `payments:` section its autopay. Monarch
Money holds the live personal balances, Zoho Books the business ones. The native
Zoho Sheet "Bills" (tab `Chart of Accounts`) is what the operator looks at: this
module RENDERS the registry into it and never reads identity back out of it.

    payments.yaml + debts.yaml  --render-->  Chart of Accounts
    payments.yaml  <--one-to-one-->  Monarch (personal) / Zoho Books (business)

Checks (every finding is an error; `main()` prints them and exits 1 if any):

  1. registry well formed      - ids unique (RND-KEY)
  2. registry <-> Monarch      - every open personal entry's monarch_match resolves
                                 to exactly one active Monarch account and every
                                 active Monarch depository/credit account is
                                 claimed by exactly one entry
  3. registry <-> Zoho Books   - the same for business entries via zoho_match
  4. sheet == render           - every tool-owned cell of the Chart of Accounts
                                 equals what the registry renders (sheet_drift,
                                 balance_mismatch); every row in the block carries
                                 a Registry Id (sheet_unregistered_row)

Rules, each named so it can be found in the spec
(docs/proposals/registry-renders-sheet/), here, and in a test name:

  RND-SCOPE  depository and credit accounts only (loans and investments are out)
  RND-ORDER  registry order; closed accounts last
  RND-CLOSED closed = status: closed or inactive: true; rendered as Status
             `closed`, dropped from the render RETIRE_CLOSED_AFTER_DAYS after
             `closed_on` (kept forever while closed_on is unset)
  RND-TYPE   Type = owner x kind: Personal/Business Debit/Credit
  RND-KEY    rows join to accounts by the Registry Id column, never by last4 or name
  RND-OWN    the tool writes only the columns it owns (OWNED_COLUMNS); operator
             columns are copied along when a row moves and never generated
  RND-DIFF   only cells whose content differs are written, each one logged
  RND-FIT    the render refuses, writing nothing, if the block would reach the
             first non-blank row below it (the totals row)
  RND-BAL    balance = Monarch display balance (personal) or Books book balance
             (business); left as it is when the live source is unavailable
  RND-TERMS  AutoPay Type/Date from the payments entry that pays the card;
             Credit Limit, APR, Due Date from debts.yaml (promo overrides)

`--sync` renders: it writes the differing cells by sheet row and column
(zoho_sheet.write_cells) and deletes rows the registry no longer renders, so the
block worksheet.records.fetch reads stays contiguous. `--migrate` is the one-time
step that adds the Registry Id column and fills it for existing rows.

Usage:
    python account_register.py                    # all checks
    python account_register.py --category business
    python account_register.py --offline          # registry + sheet only (no live balances)
    python account_register.py --no-sheet         # registry <-> live only
    python account_register.py --no-balances      # skip balance_mismatch
    python account_register.py --sync [--dry-run] # render the registry into the sheet, then check
    python account_register.py --migrate [--dry-run]
    python account_register.py --json
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import date

CATEGORIES = ("personal", "business")
SHEET_TAB = "Chart of Accounts"
DEFAULT_SHEET_ID = "efajyb8b15e86a20a48e0b3edab6cf4fb18c5"   # Bills, WorkDrive folder "finance"
SHEET_ID_ENV = "ZOHO_BILLS_SHEET_ID"

# sheet Type -> (owner, kind). Anything else is out of scope (Investment) or unknown.
SHEET_TYPES = {
    "Personal Debit": ("personal", "debit"),
    "Personal Credit": ("personal", "credit"),
    "Business Debit": ("business", "debit"),
    "Business Credit": ("business", "credit"),
}
SHEET_TYPE_OF = {v: k for k, v in SHEET_TYPES.items()}             # (owner, kind) -> sheet Type
OUT_OF_SCOPE_SHEET_TYPES = {"Investment", "Loan"}   # kept on the sheet, never checked; rendered after the accounts
REGISTRY_TYPES = {"depository": "debit", "credit": "credit"}        # loan/investment skipped
MONARCH_TYPES = {"depository": "debit", "credit": "credit"}         # brokerage/loan/other skipped
BOOKS_TYPES = {"bank": "debit", "credit_card": "credit"}            # cash (Petty Cash etc.) skipped

MATCH_FIELD = {"personal": "monarch_match", "business": "zoho_match"}
SOURCE_NAME = {"personal": "Monarch", "business": "Zoho Books"}

ID_COLUMN = "Registry Id"
KEY_COLUMN = "Account Last 4"
AS_OF_COLUMN = "As of"
BALANCE_COLUMN = {"debit": "Debit Balance", "credit": "Credit Balance"}
IDENTITY_COLUMNS = (ID_COLUMN, "Name", "Company", KEY_COLUMN, "Type", "Status")
TERM_COLUMNS = ("AutoPay Type", "AutoPay Date", "Credit Limit", "APR", "Due Date")
BALANCE_COLUMNS = ("Debit Balance", "Credit Balance", AS_OF_COLUMN)
OWNED_COLUMNS = IDENTITY_COLUMNS + TERM_COLUMNS + BALANCE_COLUMNS          # RND-OWN
OPTIONAL_COLUMNS = {AS_OF_COLUMN}          # rendered only when the sheet has the column
RETIRE_CLOSED_AFTER_DAYS = 35              # RND-CLOSED: one statement cycle

RENDER_KINDS = {"balance_mismatch", "sheet_drift"}   # fixed by --sync
NON_MEMBERSHIP_KINDS = RENDER_KINDS


def _num(v):
    """Sheet cell -> float or None ('' and text stay None; '-12.5' and 1826 parse)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


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


def _acct(key, name, owner, kind, closed=False, balance=None, match=None, ref="", institution=None,
          row=None, id=None, closed_on=None, cells=None, manual=False):
    """One account as seen by one side. `row` is the 1-based sheet row and `cells`
    the raw sheet record (sheet side only); `id` the registry id (registry and
    sheet sides); `manual` marks a registry account with `feed: manual`, which
    has no live source: its balance is whatever the sheet says and it is exempt
    from registry_no_match_name."""
    return {"key": key, "name": name, "owner": owner, "kind": kind, "closed": bool(closed),
            "balance": balance, "match": match, "ref": ref, "institution": institution,
            "row": row, "id": id, "closed_on": closed_on, "cells": cells, "manual": bool(manual)}


def is_totals_row(row):
    """A row inside the records block with no Name, Type or Registry Id but with a
    number in it: the sheet's totals row. Never written, never moved; the render
    must end above it."""
    if any(str(row.get(c) or "").strip() for c in ("Name", "Type", ID_COLUMN)):
        return False
    return any(_num(v) is not None for k, v in row.items() if k != "row_index")


# ---------------------------------------------------------------------------
# Normalisers: each side -> list of account dicts (pure)
# ---------------------------------------------------------------------------

def sheet_accounts(rows, category=None):
    """Chart of Accounts rows (Sheet Data API records: header-keyed dicts with
    row_index) -> (accounts, errors). Rows join to the registry by Registry Id
    (RND-KEY); a row in the block without one is `sheet_unregistered_row`, two
    rows with the same id `sheet_duplicate_id`. Investment rows are skipped.
    `category` is accepted for symmetry with the other normalisers but the sheet
    does not know owners reliably, so it is ignored here and applied by the
    planner."""
    accounts, errors, seen = [], [], {}
    for row in rows:
        ref = f"sheet row {row.get('row_index', '?')}"
        name = str(row.get("Name") or "").strip()
        typ = str(row.get("Type") or "").strip()
        if typ in OUT_OF_SCOPE_SHEET_TYPES or is_totals_row(row):
            continue
        acct_id = str(row.get(ID_COLUMN) or "").strip()
        if not acct_id:
            errors.append(_err("sheet_unregistered_row",
                               f"{ref} '{name}' has no {ID_COLUMN}.",
                               "Add the account to payments.yaml and run --migrate, or delete the row."))
            continue
        if acct_id in seen:
            errors.append(_err("sheet_duplicate_id",
                               f"{ref} '{name}' and {seen[acct_id]} both carry {ID_COLUMN} {acct_id}.",
                               "Delete one of the rows; the render keeps one row per account."))
            continue
        seen[acct_id] = ref
        owner, kind = SHEET_TYPES.get(typ, (None, None))
        closed = str(row.get("Status") or "").strip().casefold() == "closed"
        balance = _num(row.get(BALANCE_COLUMN[kind])) if kind else None
        accounts.append(_acct(str(row.get(KEY_COLUMN) or "").strip() or None, name, owner, kind, closed=closed,
                              balance=balance, ref=ref, row=row.get("row_index"), id=acct_id, cells=row))
    return accounts, errors


def registry_accounts(config):
    """payments.yaml accounts -> account dicts in file order (RND-ORDER); depository
    and credit only (RND-SCOPE). Closed = `status: closed` or `inactive: true`
    (RND-CLOSED); `closed_on` (YYYY-MM-DD) starts the retirement clock."""
    out = []
    for a in config.get("accounts", []):
        kind = REGISTRY_TYPES.get(a.get("type"))
        owner = a.get("category")
        if kind is None or owner not in CATEGORIES:
            continue
        closed = str(a.get("status") or "").casefold() == "closed" or bool(a.get("inactive"))
        closed_on = a.get("closed_on")
        if closed_on is not None and not isinstance(closed_on, date):
            try:
                closed_on = date.fromisoformat(str(closed_on))
            except ValueError:
                closed_on = None
        out.append(_acct(a.get("last4") or None, a.get("name", a.get("id")), owner, kind,
                         closed=closed, match=a.get(MATCH_FIELD[owner]), ref=f"registry {a.get('id')}",
                         institution=a.get("institution"), id=a.get("id"), closed_on=closed_on,
                         manual=str(a.get("feed") or "").casefold() == "manual"))
    return out


def monarch_accounts(balances):
    """coverage_report.fetch_monarch_balances() shape ({display name: {balance,
    type, hidden, deactivated}}) -> active depository/credit accounts. Hidden or
    deactivated connections are dropped: they are Monarch's stale duplicates.
    Balance = Monarch's display balance (what the app shows: a card's owed amount,
    positive, with any per-account sign inversion applied), which is the sheet's
    convention. Older callers that only carry the raw `balance` get it
    sign-flipped for cards instead (the provider reports owed amounts negative)."""
    out = []
    for name, d in (balances or {}).items():
        kind = MONARCH_TYPES.get(d.get("type"))
        if kind is None or d.get("hidden") or d.get("deactivated"):
            continue
        bal = _num(d.get("display_balance"))
        if bal is None:
            bal = _num(d.get("balance"))
            if bal is not None and kind == "credit":
                bal = -bal
        if bal is not None:
            bal = bal or 0.0           # `or 0.0` turns -0.0 into 0.0
        out.append(_acct(last4_of(name), name, "personal", kind, balance=bal, ref="monarch"))
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

def check_registry_ids(registry):
    """RND-KEY: every account has an id and no two share one."""
    seen, errors = {}, []
    for a in registry:
        if not a["id"]:
            errors.append(_err("registry_no_id", f"{a['ref']} '{a['name']}' has no id.", "Give every account an id."))
            continue
        if a["id"] in seen:
            errors.append(_err("registry_duplicate_id",
                               f"payments.yaml: id {a['id']} appears twice: '{seen[a['id']]}' and '{a['name']}'.",
                               "ids must be unique; rename one."))
        else:
            seen[a["id"]] = a["name"]
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
            if not ra["closed"] and not ra["manual"]:
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
                               "Set status: closed in payments.yaml."))
        elif ra["closed"] and not la["closed"]:
            errors.append(_err("registry_closed_live_open",
                               f"{ra['ref']} '{ra['name']}' is closed in payments.yaml but open in {source}."))
        if la["key"] and ra["key"] and la["key"] != ra["key"]:
            errors.append(_err("last4_mismatch",
                               f"{ra['ref']} '{ra['name']}': registry last4 {ra['key']} but {source} "
                               f"'{la['name']}' shows {la['key']}.",
                               "Correct last4 in payments.yaml."))
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
                               f"Add it to payments.yaml accounts: with {field}: {la['name']!r}."))
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


# ---------------------------------------------------------------------------
# The render (pure planning)
# ---------------------------------------------------------------------------

def _live_hit(ra, live):
    """The single open live account `ra`'s match name resolves to, else None."""
    if not live or not ra["match"]:
        return None
    want = norm_name(ra["match"])
    hits = [a for a in live if norm_name(a["name"]) == want and not a["closed"]]
    return hits[0] if len(hits) == 1 else None


def card_terms(config, debts, account_id, kind):
    """RND-TERMS: the term columns for one account. Debit rows render blank. For a
    card: AutoPay Type/Date from the `payments:` entry whose pays_account is the
    card (`none` when no entry pays it); Credit Limit, APR, Due Date from the
    debts.yaml entry (a promo entry overrides apr and credit_limit while present)."""
    terms = {c: "" for c in TERM_COLUMNS}
    if kind != "credit":
        return terms
    terms["AutoPay Type"] = "none"
    for p in (config or {}).get("payments", []) or []:
        if p.get("pays_account") == account_id and p.get("autopay"):
            terms["AutoPay Type"] = str(p.get("autopay_type") or "").strip() or "none"
            terms["AutoPay Date"] = p.get("day_of_month") if p.get("day_of_month") is not None else ""
            break
    found = {}
    for d in (debts or {}).get("debts", []) or []:
        if d.get("account") == account_id:
            found.update({k: v for k, v in d.items() if v is not None})
    for d in (debts or {}).get("promos", []) or []:
        if d.get("account") == account_id:
            found.update({k: v for k, v in d.items() if v is not None and k in ("apr", "credit_limit")})
    terms["Credit Limit"] = found.get("credit_limit", "")
    terms["APR"] = found.get("apr", "")
    terms["Due Date"] = found.get("due_day", "")
    return terms


def _cell_text(v):
    """Canonical text of a cell for COMPARISON only: numbers to 2 places (so 5,
    5.0, '$5.00' and '0621' vs 621 compare equal), everything else stripped text,
    None -> ''. Writes send the raw value, never this."""
    if v is None:
        return ""
    s = str(v).strip()
    if s == "":
        return ""
    n = _num(v)
    if n is not None:
        return f"{n:.2f}"
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)     # Zoho hands a date cell back as 11/16/2026
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return s


def rendered_accounts(registry, today, category=None):
    """RND-SCOPE, RND-ORDER, RND-CLOSED: the accounts to render, in order."""
    today = today or date.today()
    opened, closed = [], []
    for ra in registry:
        if category and ra["owner"] != category:
            continue
        if not ra["closed"]:
            opened.append(ra)
        elif ra["closed_on"] is None or (today - ra["closed_on"]).days <= RETIRE_CLOSED_AFTER_DAYS:
            closed.append(ra)
    return opened + closed


def plan_render(config, debts, live_by_owner, sheet_rows, today=None, category=None, gap_rows=None):
    """What `--sync` would do, with no I/O. Returns
      rows:     [{row, id, name, cells}]       the target block, one per rendered account
      changes:  [{row, id, name, column, old, new}]   cells that differ (RND-DIFF)
      retired:  [{row, id, name}]              block rows past the new end, to delete
      extra_rows_needed, fit_ok               RND-FIT against `gap_rows` (None = unknown)
      errors:   sheet_unregistered_row / sheet_duplicate_id / sheet_missing_column,
                any of which blocks the render.
    A category other than None only narrows the check; the render itself always
    covers every account, because row order is global."""
    today = today or date.today()
    registry = registry_accounts(config)
    sheet, errors = sheet_accounts(sheet_rows or [])
    # A duplicate Registry Id is what a half-finished render leaves behind (the account
    # already moved up, its old row not yet overwritten); a full render rewrites every
    # position and so resolves it. Reported by the check, never a reason to refuse.
    warnings = [e for e in errors if e["kind"] == "sheet_duplicate_id"]
    errors = [e for e in errors if e["kind"] != "sheet_duplicate_id"]
    headers = []
    for row in sheet_rows or []:
        for k in row:
            if k != "row_index" and k not in headers:
                headers.append(k)
    missing = [c for c in OWNED_COLUMNS if c not in headers and c not in OPTIONAL_COLUMNS]
    if missing and sheet_rows:
        errors.append(_err("sheet_missing_column", f"Chart of Accounts has no column {missing}.",
                           "Run --migrate (adds Registry Id) or add the column in the sheet."))
    existing = {a["id"]: a for a in sheet}
    current = {row["row_index"]: row for row in (sheet_rows or []) if "row_index" in row}
    # The totals row can sit INSIDE the fetched block (when rows were added below it);
    # it is never written or moved, and the render must end above it.
    totals_row = next((i for i in sorted(current) if is_totals_row(current[i])), None)
    passthrough = [current[i] for i in sorted(current)
                   if str(current[i].get("Type") or "").strip() in OUT_OF_SCOPE_SHEET_TYPES]
    rows, changes = [], []

    def diff(pos, acct_id, name, cells, operator_row=False):
        if totals_row is not None and pos >= totals_row:
            return                      # never a write at or past the totals row (the fit check refuses)
        cur = current.get(pos, {})
        for col in headers:
            old, new = _cell_text(cur.get(col)), _cell_text(cells.get(col))
            if old != new:
                changes.append({"row": pos, "id": acct_id, "name": name, "column": col,
                                "old": old, "new": new, "value": cells.get(col, ""),
                                "moved": operator_row or col not in OWNED_COLUMNS})

    pos = 2
    for ra in rendered_accounts(registry, today, category):
        prev = existing.get(ra["id"])
        cells = dict(prev["cells"]) if prev else {}
        cells.pop("row_index", None)
        cells.update({
            ID_COLUMN: ra["id"], "Name": ra["name"], "Company": ra["institution"] or "",
            KEY_COLUMN: ra["key"] or "", "Type": SHEET_TYPE_OF[(ra["owner"], ra["kind"])],
            "Status": "closed" if ra["closed"] else "active",
        })
        cells.update(card_terms(config, debts, ra["id"], ra["kind"]))
        cur = current.get(pos, {})
        la = _live_hit(ra, (live_by_owner or {}).get(ra["owner"]))
        if la and la["balance"] is not None:                                   # RND-BAL
            col = BALANCE_COLUMN[ra["kind"]]
            cells[col] = round(la["balance"], 2)
            cells[BALANCE_COLUMN["credit" if ra["kind"] == "debit" else "debit"]] = ""
            # As of is the date the balance was last written, so it moves only with the balance
            if AS_OF_COLUMN in headers and (_cell_text(cur.get(col)) != _cell_text(cells[col])
                                            or not _cell_text(cells.get(AS_OF_COLUMN))):
                cells[AS_OF_COLUMN] = today.isoformat()
        rows.append({"row": pos, "id": ra["id"], "name": ra["name"], "cells": cells})
        diff(pos, ra["id"], ra["name"], cells)
        pos += 1
    for row in passthrough:      # Investment / Loan rows: every cell kept, placed after the accounts
        cells = {k: v for k, v in row.items() if k != "row_index"}
        rows.append({"row": pos, "id": "", "name": str(row.get("Name") or ""), "cells": cells, "passthrough": True})
        diff(pos, "", str(row.get("Name") or ""), cells, operator_row=True)
        pos += 1
    new_end = pos - 1
    old_end = max(current) if current else 1
    retired = []
    for i in range(new_end + 1, old_end + 1):
        if i not in current or i == totals_row:
            continue
        retired.append({"row": i, "id": str(current[i].get(ID_COLUMN) or ""), "name": current[i].get("Name", ""),
                        "action": "delete" if (totals_row is None or i > totals_row) else "blank"})
    if totals_row is not None:
        extra = max(0, new_end - (totals_row - 1))
        fit_ok = extra == 0
    else:
        extra = max(0, new_end - old_end)
        fit_ok = gap_rows is None or extra <= gap_rows
        totals_row = (old_end + 1 + gap_rows) if gap_rows is not None else None
    return {"rows": rows, "changes": changes, "retired": retired, "extra_rows_needed": extra,
            "fit_ok": fit_ok, "errors": errors, "warnings": warnings, "totals_row": totals_row}


def check_sheet_vs_render(plan, balances=True):
    """RND-DIFF as a check: one `sheet_drift` per row whose identity, term or
    operator cells differ from the render (or that is missing / no longer
    rendered), one `balance_mismatch` per row whose only differences are balance
    cells. Both are fixed by --sync."""
    errors = list(plan["errors"]) + list(plan.get("warnings", []))
    if any(e["kind"] == "sheet_missing_column" for e in errors):
        return errors            # not migrated yet: per-row drift would be noise
    by_row = {}
    for c in plan["changes"]:
        by_row.setdefault((c["row"], c["name"]), []).append(c)
    for (row, name), cs in sorted(by_row.items()):
        cols = [c["column"] for c in cs]
        if all(c in BALANCE_COLUMNS for c in cols):
            if balances:
                detail = "; ".join(f"{c['column']} {c['old'] or 'empty'} -> {c['new'] or 'empty'}" for c in cs
                                   if c["column"] != AS_OF_COLUMN) or "As of"
                errors.append(_err("balance_mismatch", f"sheet row {row} '{name}': {detail}.",
                                   "Run accounts:sync to write the live balance."))
        else:
            errors.append(_err("sheet_drift",
                               f"sheet row {row} '{name}': {', '.join(cols)} differ from the registry render.",
                               "Run accounts:sync; identity and terms are edited in payments.yaml / debts.yaml."))
    for r in plan["retired"]:
        errors.append(_err("sheet_drift", f"sheet row {r['row']} '{r['name']}' is no longer rendered.",
                           "Run accounts:sync to remove it."))
    return errors


def run_checks(config, sheet_rows=None, monarch=None, books=None, category=None, source_status=None,
               balances=True, debts=None, today=None):
    """All checks over pre-fetched data. A side passed as None is skipped (its
    checks are not run), which is how --offline and --no-sheet work."""
    errors = check_sources(source_status)
    registry = [a for a in registry_accounts(config) if not category or a["owner"] == category]
    errors += check_registry_ids(registry)
    live_by_owner = {}
    for owner, raw, normalise in (("personal", monarch, monarch_accounts), ("business", books, books_accounts)):
        if raw is None or category not in (None, owner):
            continue
        live_by_owner[owner] = normalise(raw)
        errors += check_registry_vs_live(registry, live_by_owner[owner], owner)
    if sheet_rows is not None:
        # The render is always global (row order is), so plan it whole and narrow the
        # findings to the category afterwards.
        plan = plan_render(config, debts, live_by_owner, sheet_rows, today=today)
        if category:
            owner_of = {a["id"]: a["owner"] for a in registry_accounts(config)}
            plan = {**plan, "changes": [c for c in plan["changes"] if owner_of.get(c["id"]) == category],
                    "retired": [r for r in plan["retired"] if owner_of.get(r["id"]) == category]}
        errors += check_sheet_vs_render(plan, balances=balances)
    return errors


def membership_errors(errors):
    return [e for e in errors if e["kind"] not in NON_MEMBERSHIP_KINDS]


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


def _gather_gap(sheet_rows, sheet_id):
    """Blank rows between the block and the next non-blank row (the totals), or
    None when that cannot be read."""
    try:
        from zoho_sheet import blank_rows_below
        last = max(r.get("row_index", 1) for r in sheet_rows) if sheet_rows else 1
        return blank_rows_below(sheet_id, SHEET_TAB, last + 1)
    except Exception:
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


def _gather_debts():
    try:
        from debt_plan import load_debts_yaml
        return load_debts_yaml()
    except Exception:
        return {"debts": [], "promos": [], "plan": {}}


async def gather(category=None, offline=False, no_sheet=False, sheet_id=None):
    from coverage_report import load_payments_yaml
    config = load_payments_yaml()
    status = {}
    sheet_id = sheet_id or os.getenv(SHEET_ID_ENV, DEFAULT_SHEET_ID)
    sheet_rows = None if no_sheet else _gather_sheet(status, sheet_id)
    monarch = books = None
    if not offline:
        if category in (None, "personal"):
            monarch = await _gather_monarch(status)
        if category in (None, "business"):
            books = _gather_books(status)
    return dict(config=config, sheet_rows=sheet_rows, monarch=monarch, books=books,
                category=category, source_status=status, debts=_gather_debts()), sheet_id


def _live_sides(inputs):
    """{owner: normalised live accounts} for the sides that were gathered and are in scope."""
    live = {}
    for owner, raw, normalise in (("personal", inputs["monarch"], monarch_accounts),
                                  ("business", inputs["books"], books_accounts)):
        if raw is not None and inputs["category"] in (None, owner):
            live[owner] = normalise(raw)
    return live


# ---------------------------------------------------------------------------
# The render and the migration (I/O)
# ---------------------------------------------------------------------------

def render(inputs, sheet_id, dry_run=False, log=print, today=None):
    """Write the registry into the Chart of Accounts: differing cells by row and
    column (RND-DIFF), then delete rows past the new end so the block stays
    contiguous. Refuses, writing nothing, on any planning error or when the block
    would not fit (RND-FIT). Returns the number of cells written."""
    rows = inputs["sheet_rows"]
    if rows is None:
        log("  render needs the sheet (no --no-sheet).")
        return 0
    if inputs["category"]:
        log("  render covers every account; --category only narrows the check. Nothing written.")
        return 0
    gap = _gather_gap(rows, sheet_id)
    plan = plan_render(inputs["config"], inputs.get("debts"), _live_sides(inputs), rows, today=today, gap_rows=gap)
    if plan["errors"]:
        log(f"  render blocked by {len(plan['errors'])} sheet problem(s):")
        for e in plan["errors"]:
            log(f"    {e['message']}")
        return 0
    if not plan["fit_ok"]:
        log(f"  render refused: the block needs {plan['extra_rows_needed']} more row(s) above row "
            f"{plan['totals_row']} (the totals). Insert {plan['extra_rows_needed']} blank row(s) above it "
            f"in the sheet, then re-run. Nothing written.")
        return 0
    verb = "would write" if dry_run else "write"
    for c in plan["changes"]:
        log(f"  {verb} row {c['row']} '{c['name']}' {c['column']}: {c['old'] or 'empty'} -> {c['new'] or 'empty'}"
            + (" (moved)" if c["moved"] else ""))
    blanks = [r for r in plan["retired"] if r["action"] == "blank"]
    deletes = [r for r in plan["retired"] if r["action"] == "delete"]
    for r in plan["retired"]:
        log(f"  {'would ' if dry_run else ''}{r['action']} row {r['row']} '{r['name']}' (no longer at this position)")
    n_rows = len(plan["rows"])
    if not plan["changes"] and not plan["retired"]:
        log(f"  render: {n_rows} row(s), nothing to change.")
        return 0
    if dry_run:
        log(f"  render (dry run): {n_rows} row(s), {len(plan['changes'])} cell(s) differ, "
            f"{len(blanks)} row(s) to blank, {len(deletes)} row(s) to delete.")
        return 0
    # One call per changed ROW (the whole target row, operator cells included as
    # copied), not one per cell: 626 cell writes tripped Zoho's per-minute limit.
    from zoho_sheet import blank_row, delete_rows, header_columns, write_row
    columns = header_columns(sheet_id, SHEET_TAB)
    target = {r["row"]: r["cells"] for r in plan["rows"]}
    changed_rows = sorted({c["row"] for c in plan["changes"]})
    n = 0
    for row_idx in changed_rows:
        n += write_row(sheet_id, SHEET_TAB, row_idx, target[row_idx], columns)
    for r in blanks:
        blank_row(sheet_id, SHEET_TAB, r["row"], max(columns.values()))
    if deletes:                                   # contiguous: everything below the totals row
        delete_rows(sheet_id, SHEET_TAB, deletes[0]["row"], deletes[-1]["row"])
    log(f"  render: {n_rows} row(s), {n} row(s) written ({len(plan['changes'])} cell(s) changed), "
        f"{len(blanks)} row(s) blanked, {len(deletes)} row(s) deleted.")
    return n


def plan_migration(config, sheet_rows):
    """One-time mapping of existing rows (no Registry Id) to registry accounts: by
    owner + last4, else by normalised name + institution. Returns (mapped, unmapped)
    as [{row, name, id}] / [{row, name, reason}]. Pure."""
    registry = registry_accounts(config)
    by_key = {(a["owner"], a["key"]): a for a in registry if a["key"]}
    by_name = {}
    for a in registry:
        by_name.setdefault(norm_name(a["name"]), []).append(a)
    mapped, unmapped = [], []
    taken = {str(r.get(ID_COLUMN) or "").strip() for r in sheet_rows if r.get(ID_COLUMN)}
    for row in sheet_rows:
        if str(row.get(ID_COLUMN) or "").strip():
            continue
        name, typ = str(row.get("Name") or "").strip(), str(row.get("Type") or "").strip()
        if typ in OUT_OF_SCOPE_SHEET_TYPES or is_totals_row(row):
            continue
        owner, _ = SHEET_TYPES.get(typ, (None, None))
        key = str(row.get(KEY_COLUMN) or "").strip() or None
        hit = by_key.get((owner, key)) if owner and key else None
        if hit is None and not key:
            # name is a fallback only for rows with no last4: a row whose last4 matches
            # nothing is a different account, not a misspelt one (Cap One 0474 vs the
            # Lafayette checking share the name "Business Basic Checking")
            cands = [a for a in by_name.get(norm_name(name), []) if not owner or a["owner"] == owner]
            hit = cands[0] if len(cands) == 1 else None
        if hit is None or hit["id"] in taken:
            reason = "already mapped to another row" if hit else "no registry account with this last4 or name"
            unmapped.append({"row": row.get("row_index"), "name": name, "reason": reason})
            continue
        taken.add(hit["id"])
        mapped.append({"row": row.get("row_index"), "name": name, "id": hit["id"]})
    return mapped, unmapped


def migrate(inputs, sheet_id, dry_run=False, log=print):
    """Add the Registry Id (and As of) columns if missing and fill Registry Id on
    rows it can map; list the rest. Also prints debts.yaml lines suggested by the
    sheet's Credit Limit / Due Date cells, the one time the tool reads operator
    columns. Returns the number of rows mapped."""
    rows = inputs["sheet_rows"] or []
    if not rows:
        log("  migrate: no sheet rows.")
        return 0
    headers = [k for k in rows[0] if k != "row_index"]
    from zoho_sheet import add_column, header_columns, write_cells
    for col in (ID_COLUMN, AS_OF_COLUMN):
        if col not in headers:
            log(f"  {'would add' if dry_run else 'add'} column {col!r}")
            if not dry_run:
                add_column(sheet_id, SHEET_TAB, col)
    mapped, unmapped = plan_migration(inputs["config"], rows)
    for m in mapped:
        log(f"  {'would set' if dry_run else 'set'} row {m['row']} '{m['name']}' {ID_COLUMN} = {m['id']}")
    for u in unmapped:
        log(f"  UNMAPPED row {u['row']} '{u['name']}': {u['reason']} -> register it or delete the row")
    debts = inputs.get("debts") or {}
    known = {d.get("account") for d in debts.get("debts", []) if d.get("credit_limit") or d.get("due_day")}
    by_row = {r.get("row_index"): r for r in rows}
    seeds = []
    for m in mapped:
        r = by_row.get(m["row"], {})
        if m["id"] in known:
            continue
        limit = _num(r.get("Credit Limit"))
        due = re.sub(r"\D", "", str(r.get("Due Date") or ""))        # the sheet says "12th"; the registry wants 12
        if limit or due:
            seeds.append(f"  - account: {m['id']}" + (f"\n    credit_limit: {limit:.0f}" if limit else "")
                         + (f"\n    due_day: {int(due)}" if due else ""))
    if seeds:
        log("  debts.yaml lines suggested from the sheet (paste under debts:, then delete nothing):")
        for s in seeds:
            log(s)
    if dry_run or not mapped:
        return 0
    columns = header_columns(sheet_id, SHEET_TAB)
    for m in mapped:
        write_cells(sheet_id, SHEET_TAB, m["row"], {ID_COLUMN: m["id"]}, columns)
    log(f"  migrate: {len(mapped)} row(s) mapped, {len(unmapped)} unmapped.")
    return len(mapped)


async def run_all(category=None, offline=False, no_sheet=False, sheet_id=None, balances=True,
                  do_sync=False, do_migrate=False, dry_run=False):
    inputs, sheet_id = await gather(category, offline, no_sheet, sheet_id)
    if do_migrate:
        if migrate(inputs, sheet_id, dry_run=dry_run) and not dry_run:
            inputs, _ = await gather(category, offline, no_sheet, sheet_id)
    if do_sync:
        if render(inputs, sheet_id, dry_run=dry_run) and not dry_run:
            inputs, _ = await gather(category, offline, no_sheet, sheet_id)   # re-read what we wrote
    return run_checks(balances=balances, **inputs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def format_errors(errors, color=True):
    if not errors:
        return "  Register is one-to-one and the sheet is rendered."
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
    p = argparse.ArgumentParser(description="Render payments.yaml into the Bills sheet and prove it agrees with Monarch/Zoho Books.")
    p.add_argument("--category", choices=CATEGORIES, help="only this owner's accounts (checks only)")
    p.add_argument("--offline", action="store_true", help="skip Monarch and Zoho Books (no balances)")
    p.add_argument("--no-sheet", action="store_true", help="skip the sheet (registry <-> live only)")
    p.add_argument("--sheet-id", help=f"Zoho Sheet resource id (default ${SHEET_ID_ENV} or {DEFAULT_SHEET_ID})")
    p.add_argument("--no-balances", action="store_true", help="do not report balance_mismatch")
    p.add_argument("--sync", action="store_true", help="render the registry into the sheet, then check")
    p.add_argument("--migrate", action="store_true",
                   help="one-time: add the Registry Id column and fill it on rows that map to the registry")
    p.add_argument("--dry-run", action="store_true", help="with --sync / --migrate: show the writes, change nothing")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    a = p.parse_args()
    errors = asyncio.run(run_all(category=a.category, offline=a.offline, no_sheet=a.no_sheet,
                                 sheet_id=a.sheet_id, balances=not a.no_balances,
                                 do_sync=a.sync, do_migrate=a.migrate, dry_run=a.dry_run))
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
