#!/usr/bin/env python3
"""Reconcile the Mercury business accounts in Zoho Books, in one shot.

Plan: docs/zoho-books-recon-plan.md. Mirrors mercury_precode.py (same
merchants.yaml, same classifier) with Zoho Books as the sink. The Zoho bank
feed already holds every 2026 Mercury posting as an uncategorized line; this
script pairs each Mercury transaction with its feed line and then categorizes
or matches the line:

  internal transfer   -> transfer_fund categorization from the first side seen,
                         the other side's feed line is matched to it
  spend / refund      -> expense (or expense refund) to the merchants.yaml code
  owner draw (3200)   -> owner drawings / contribution
  Toast payout        -> journal per Mercury credit with toast_xero's split lines
                         (approach A: match feed line to the journal;
                          approach B: deposit to 1320 clearing + journal from 1320)
  unmatched merchant  -> left uncategorized, listed for review

Idempotency: every document carries reference_number "mercury:<txn-id>"; a
checkpoint file (.zoho_recon_state.json) records what was done so re-runs skip
it. A Mercury txn whose feed line is no longer uncategorized is treated as done.

Usage:
    python zoho_books_recon.py                         # dry run, 2026-01-01 -> today
    python zoho_books_recon.py --toast toast.json      # include Toast journals in the plan
    python zoho_books_recon.py --apply --toast toast.json
    python zoho_books_recon.py --apply --spike         # S-1: two Toast payouts only
    python zoho_books_recon.py --reconcile             # monthly reconciliation records

Credentials: ZOHO_CLIENT_ID/SECRET, ZOHO_BOOKS_REFRESH_TOKEN, ZOHO_BOOKS_ORG_ID
in finance-tools/.env; MERCURY_API_TOKEN from finance-tools/.env or the yumyums
xero .env (same fallback as mercury_precode.py).
"""
import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml
from dotenv import load_dotenv

HERE = Path(__file__).parent
# Secrets, token cache and the checkpoint live in DATA_DIR so a run from a git
# worktree still uses (and updates) the one durable copy in the main checkout.
DATA_DIR = Path(os.getenv("FINANCE_TOOLS_DATA", HERE))
load_dotenv(DATA_DIR / ".env")
load_dotenv(HERE / ".env")
load_dotenv(Path.home() / "projects/yumyums/accounting/xero/.env")

import mercury_precode as mp  # noqa: E402  (classify, merchant_name, fetch_mercury)

BASE = "https://www.zohoapis.com/books/v3"
TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"
TOKEN_CACHE = DATA_DIR / ".zoho_books_token.json"
STATE_FILE = DATA_DIR / ".zoho_recon_state.json"
EASTERN = ZoneInfo("America/New_York")
START_DEFAULT = "2026-01-01"
OPENING_DATE = "2025-12-31"

# Xero bank-account names (merchants.yaml) -> Zoho bank account names.
# Feed accounts exist already; the other two are created without a feed.
BANK_NAMES = {
    "Mercury: Operations (4613)": "Operations ••4613",
    "Mercury: Outgoing ACH (5396)": "Outgoing ACH ••5396",
    "Mercury: Profit (7818)": "Taxes ••7818",
    "Mercury: Operations (0551) * Closed": "Operations ••0551 (closed)",
    # BoA accounts are feed-linked (2026-10-16); names are Zoho's own for the feed
    "BoA: Business Checking (1778)": "Business Adv Fundamentals",
    "BoA: CORP Account (2119)": "CORP Account - Business Adv Customized Cash Rewards - 2119",
    # own accounts with no feed in Zoho, created by the script so transfers have a far side
    "Chase: Ink (7667)": "Chase Ink ••7667",
    "Bluevine: Checking * Closed": "Bluevine Checking (closed)",
}
BANK_TYPES = {"Chase Ink ••7667": "credit_card"}   # Zoho account_type when the script creates it (default bank)
INCOME_PREFIX = "4"                                  # codes 4xxx: money in is a deposit to income, not a refund
CLOSING_FILE = HERE / "closing_balances.yaml"   # operator-supplied statement balances (feed-only accounts)

# Chart of accounts to seed: code -> (name, zoho account_type).
# Stock Zoho accounts that stand in for a Xero code just get the code added.
STOCK = {"5000": "Cost of Goods Sold", "3200": "Drawings", "3100": "Owner's Equity", "4000": "Sales",
         "6050": "Bank Fees and Charges", "6055": "Credit Card Charges"}
CHART = {
    "1100": ("Cash on Hand", "other_current_asset"),
    "1310": ("Other Receivables", "other_current_asset"),
    "1320": ("Toast Split Payout Clearing", "other_current_asset"),
    "1330": ("Third-Party Tender Receivable", "other_current_asset"),
    "2050": ("Customer Deposits", "other_current_liability"),
    "2130": ("Payroll Liabilities", "other_current_liability"),
    "2230": ("Sales Tax", "other_current_liability"),
    "3100": ("Owner's Equity", "equity"),
    "3200": ("Owner's Draws", "equity"),
    "4000": ("Sale of Goods", "income"),
    "4200": ("Other Revenues", "income"),
    "4300": ("Returns and Allowances", "income"),
    "5000": ("Cost of Goods Sold", "cost_of_goods_sold"),
    "5200": ("COGS - Supplies", "cost_of_goods_sold"),
    "6000": ("Advertising", "expense"),
    "6040": ("Merchant Fees", "expense"),
    "6050": ("Bank Fees and Charges", "expense"),      # overdraft item fees, monthly maintenance fee
    "6055": ("Credit Card Charges", "expense"),        # card finance charges, late payment fees
    "6041": ("Toast Withholdings", "expense"),
    "6060": ("Cleaning and Maintenance", "expense"),
    "6090": ("Contract Labor", "expense"),
    "6140": ("Equipment & Tools", "expense"),
    "6160": ("Licenses and Fees", "expense"),
    "6210": ("Insurance - Other", "expense"),
    "6240": ("Reimbursements", "expense"),
    "6250": ("Office Expense", "expense"),
    "6270": ("Postage and Delivery", "expense"),
    "6280": ("Printing and Stationery", "expense"),
    "6300": ("Rent - Real Estate", "expense"),
    "6310": ("Rent - Other", "expense"),
    "6340": ("Software & Web", "expense"),
    "6350": ("Supplies", "expense"),
    "6360": ("Taxes - Payroll", "expense"),
    "6390": ("Telephone and Internet", "expense"),
    "6430": ("Utilities", "expense"),
    "6440": ("Vehicle Expense", "expense"),
    "6450": ("Wages and Salaries", "expense"),
}
EQUITY_CODES = {"3100", "3200"}


# --- Zoho client -------------------------------------------------------------


class Zoho:
    def __init__(self):
        self.org = os.getenv("ZOHO_BOOKS_ORG_ID")
        if not self.org or not os.getenv("ZOHO_BOOKS_REFRESH_TOKEN"):
            sys.exit("ZOHO_BOOKS_REFRESH_TOKEN / ZOHO_BOOKS_ORG_ID not set in finance-tools/.env")
        self.access = self._token()
        self.calls = 0
        self.capped = False

    def _token(self):
        if TOKEN_CACHE.exists():
            c = json.loads(TOKEN_CACHE.read_text())
            if c.get("exp", 0) > time.time() + 60:
                return c["access_token"]
        r = requests.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "client_id": os.getenv("ZOHO_CLIENT_ID"),
            "client_secret": os.getenv("ZOHO_CLIENT_SECRET"),
            "refresh_token": os.getenv("ZOHO_BOOKS_REFRESH_TOKEN"),
        }, timeout=20).json()
        if "access_token" not in r:
            sys.exit(f"Zoho token error: {r}")
        TOKEN_CACHE.write_text(json.dumps({"access_token": r["access_token"], "exp": time.time() + 3300}))
        return r["access_token"]

    def req(self, method, path, params=None, body=None):
        params = dict(params or {})
        params["organization_id"] = self.org
        for attempt in range(6):
            self.calls += 1
            r = requests.request(method, f"{BASE}{path}", params=params, json=body, timeout=60,
                                 headers={"Authorization": f"Zoho-oauthtoken {self.access}"})
            if r.status_code == 429:
                if '"code":45' in r.text.replace(" ", ""):
                    # daily cap for the org (1,000 calls/day on the trial tier): no point retrying
                    self.capped = True
                    raise ZohoError(f"{method} {path}: Zoho daily API cap reached — resume tomorrow")
                print(f"  429 on {method} {path}: {r.text[:160]} (attempt {attempt + 1})", flush=True)
                time.sleep(30 * (attempt + 1))
                continue
            if r.status_code == 401:
                TOKEN_CACHE.unlink(missing_ok=True)
                self.access = self._token()
                continue
            try:
                d = r.json()
            except ValueError:
                d = {"raw": r.text[:500]}
            if r.status_code >= 300:
                raise ZohoError(f"{method} {path} -> {r.status_code}: {json.dumps(d)[:500]}")
            time.sleep(1.0)  # ~60 req/min, well under the 100/min cap
            return d
        raise ZohoError(f"{method} {path}: retries exhausted")

    def get(self, path, **p):
        return self.req("GET", path, p)

    def post(self, path, body=None, **p):
        return self.req("POST", path, p, body)

    def put(self, path, body=None, **p):
        return self.req("PUT", path, p, body)

    def delete(self, path, **p):
        return self.req("DELETE", path, p)

    def pages(self, path, key, **p):
        page = 1
        while True:
            d = self.get(path, page=page, per_page=200, **p)
            yield from d.get(key, [])
            if not d.get("page_context", {}).get("has_more_page"):
                return
            page += 1


class ZohoError(RuntimeError):
    pass


# --- helpers ------------------------------------------------------------------


def eastern_date(posted_at):
    return datetime.fromisoformat(posted_at.replace("Z", "+00:00")).astimezone(EASTERN).date()


def money(x):
    return Decimal(str(x)).quantize(Decimal("0.01"))


def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=1, sort_keys=True))


def mercury_balances():
    tok = os.getenv("MERCURY_API_TOKEN")
    r = requests.get(f"{mp.MERCURY_API}/accounts", headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    r.raise_for_status()
    return {a["id"]: a for a in r.json()["accounts"]}


# --- chart -------------------------------------------------------------------


def chart_index(z):
    """{code: account_id} and {name: account} for the live chart."""
    accts = list(z.pages("/chartofaccounts", "chartofaccounts"))
    by_code = {a["account_code"]: a for a in accts if a.get("account_code")}
    by_name = {a["account_name"]: a for a in accts}
    return by_code, by_name


def seed_chart(z, needed_codes, apply, log):
    by_code, by_name = chart_index(z)
    plan = []
    for code in sorted(needed_codes):
        if code in by_code:
            continue
        name, atype = CHART[code]
        stock = STOCK.get(code)
        # an existing account with the same name (stock or ours) just gets the code
        existing = by_name.get(stock) if stock and stock in by_name else by_name.get(name)
        if existing is not None and not existing.get("account_code"):
            plan.append(("code", code, existing["account_name"], existing["account_id"]))
        else:
            plan.append(("create", code, name, atype))
    for op in plan:
        if op[0] == "code":
            log(f"  chart: set code {op[1]} on stock account '{op[2]}'")
            if apply:
                z.put(f"/chartofaccounts/{op[3]}", {"account_code": op[1]})
        else:
            log(f"  chart: create {op[1]} {op[2]} ({op[3]})")
            if apply:
                z.post("/chartofaccounts", {"account_name": op[2], "account_code": op[1], "account_type": op[3]})
    if apply and plan:
        by_code, by_name = chart_index(z)
    return by_code, by_name


def bank_index(z):
    return {a["account_name"]: a for a in z.get("/bankaccounts")["bankaccounts"]}


def seed_banks(z, apply, log):
    banks = bank_index(z)
    for xero_name, zoho_name in BANK_NAMES.items():
        if zoho_name not in banks:
            atype = BANK_TYPES.get(zoho_name, "bank")
            log(f"  bank: create '{zoho_name}' ({atype}, no feed)")
            if apply:
                z.post("/bankaccounts", {"account_name": zoho_name, "account_type": atype})
    return bank_index(z) if apply else banks


# --- data --------------------------------------------------------------------


def feed_lines(z, account_id):
    return list(z.pages("/banktransactions", "banktransactions", account_id=account_id,
                        filter_by="Status.Uncategorized", sort_column="date", sort_order="A"))


def pair(mercury, lines):
    """Pair Mercury txns with uncategorized feed lines: exact date first, then ±1 day,
    tie-break by merchant name appearing in the Zoho description. Returns
    {mercury_id: line} plus the unpaired on each side."""
    idx = defaultdict(list)
    for ln in lines:
        d = date.fromisoformat(ln["date"])
        idx[(d, money(ln["amount"]), ln["debit_or_credit"])].append(ln)
    paired, un_m = {}, []
    for t in sorted(mercury, key=lambda t: t["postedAt"]):
        d = eastern_date(t["postedAt"])
        amt = money(abs(t["amount"]))
        dirn = "debit" if t["amount"] > 0 else "credit"
        name = mp.merchant_name(t).lower()
        hit = None
        for delta in (0, 1, -1, 2, -2):
            cands = idx.get((d + timedelta(days=delta), amt, dirn), [])
            if not cands:
                continue
            cands.sort(key=lambda ln: 0 if name and name.split("••")[0].strip()[:8] in (ln.get("description") or "").lower() else 1)
            hit = cands.pop(0)
            break
        if hit:
            paired[t["id"]] = hit
        else:
            un_m.append(t)
    un_z = [ln for c in idx.values() for ln in c]
    return paired, un_m, un_z


def import_statement(z, zid, zoho_name, txns, state, apply, log):
    """No-feed account: push the Mercury history in as a bank statement, once.
    Lines are dated by Mercury's Eastern posting date so nothing straddles a month."""
    key = f"_imported:{zid}"
    if state.get(key):
        return
    # NB: the import endpoint uses the bank-statement convention (credit = money in),
    # the opposite of the listing convention (debit = money in). Verified 2026-10-16.
    rows = [{"transaction_id": t["id"], "date": eastern_date(t["postedAt"]).isoformat(),
             "debit_or_credit": "credit" if t["amount"] > 0 else "debit",
             "amount": float(money(abs(t["amount"]))), "payee": mp.merchant_name(t)[:100],
             "description": (t.get("bankDescription") or mp.merchant_name(t))[:200],
             "reference_number": ref(t)} for t in sorted(txns, key=lambda t: t["postedAt"])]
    if not rows:
        return
    log(f"  statement import for '{zoho_name}': {len(rows)} lines {rows[0]['date']} → {rows[-1]['date']}"
        + ("" if apply else " (dry run)"))
    if not apply:
        return
    body = {"account_id": zid, "start_date": rows[0]["date"], "end_date": rows[-1]["date"], "transactions": rows}
    r = z.post("/bankstatements", body)
    st = r.get("statement") or r.get("bankstatement") or r
    sid = st.get("statement_id") or z.get(f"/bankaccounts/{zid}/statement/lastimported").get("statement", {}).get("statement_id")
    state[key] = {"statement_id": sid, "lines": len(rows)}
    save_state(state)
    # direction sanity check: a money-out Mercury txn must come back as a credit line
    out = next((t for t in txns if t["amount"] < 0), None)
    if out:
        ln = next((x for x in feed_lines(z, zid) if x.get("reference_number") == ref(out)
                   or (x["date"] == eastern_date(out["postedAt"]).isoformat() and money(x["amount"]) == money(abs(out["amount"])))), None)
        if ln and ln.get("debit_or_credit") != "credit":
            sys.exit(f"statement import direction reversed (line {ln['transaction_id']} is {ln.get('debit_or_credit')}); "
                     f"delete statement {st.get('statement_id')} and fix import_statement()")


def load_toast(path):
    if not path:
        return []
    data = json.loads(Path(path).read_text())
    return [d for d in data["deposits"] if not d["has_problems"]]


def pair_toast(toast_txns, deposits):
    """Mercury TOAST credits -> toast_xero deposits by (bank date, amount)."""
    idx = defaultdict(list)
    for dep in deposits:
        idx[(dep["bank_credit_date"] or dep["settle_date"], money(dep["total_payouts"]))].append(dep)
    out, missing = {}, []
    for t in toast_txns:
        key = (eastern_date(t["postedAt"]).isoformat(), money(t["amount"]))
        cands = idx.get(key) or []
        if not cands:
            for delta in (1, -1):
                k2 = ((date.fromisoformat(key[0]) + timedelta(days=delta)).isoformat(), key[1])
                if idx.get(k2):
                    cands = idx[k2]
                    break
        if cands:
            out[t["id"]] = cands.pop(0)
        else:
            missing.append(t)
    return out, missing


# --- Zoho operations ------------------------------------------------------------


def ref(t):
    # Mercury txns: mercury:<txn id>. Feed-only lines carry their own key (boa:<line id>).
    return t.get("ref") or f"mercury:{t['id']}"


def categorize_transfer(z, line, from_id, to_id, t):
    body = {"transaction_type": "transfer_fund", "from_account_id": from_id, "to_account_id": to_id,
            "date": line["date"], "amount": float(money(line["amount"])), "reference_number": ref(t),
            "description": (t.get("bankDescription") or mp.merchant_name(t))[:200]}
    return z.post(f"/banktransactions/uncategorized/{line['transaction_id']}/categorize", body)


def match_line(z, line, want_ref=None, want_type=None, known_id=None):
    """Match an uncategorized line to an existing document (transfer side / journal).
    With known_id (the document this run just created) the candidate lookup is
    skipped: 2 calls per transfer / Toast payout instead of 3."""
    if known_id and want_type:
        try:
            z.post(f"/banktransactions/uncategorized/{line['transaction_id']}/match",
                   {"transactions_to_be_matched": [{"transaction_id": known_id, "transaction_type": want_type}]})
            return {"transaction_id": known_id, "transaction_type": want_type}, []
        except ZohoError as e:
            if z.capped:
                raise
            # fall through to the lookup
    d = z.get(f"/banktransactions/uncategorized/{line['transaction_id']}/match", account_id=line["account_id"])
    cands = d.get("matching_transactions", [])
    pick = None
    for c in cands:
        if want_ref and c.get("reference_number") == want_ref:
            pick = c
            break
    if pick is None and want_type:
        same = [c for c in cands if c.get("transaction_type") == want_type
                and money(c.get("amount", 0)) == money(line["amount"])]
        # identical same-day candidates are interchangeable; Zoho only offers unmatched ones
        same.sort(key=lambda c: 0 if c.get("date") == line["date"] else 1)
        if same:
            pick = same[0]
    if pick is None:
        return None, cands
    z.post(f"/banktransactions/uncategorized/{line['transaction_id']}/match",
           {"transactions_to_be_matched": [{"transaction_id": pick["transaction_id"],
                                            "transaction_type": pick["transaction_type"]}]})
    return pick, cands


def categorize_expense(z, line, account_id, bank_id, t, name):
    body = {"account_id": account_id, "paid_through_account_id": bank_id, "date": line["date"],
            "amount": float(money(line["amount"])), "reference_number": ref(t),
            "description": f"{name} | {(t.get('bankDescription') or '')[:150]}"}
    return z.post(f"/banktransactions/uncategorized/{line['transaction_id']}/categorize/expenses", body)


def categorize_generic(z, line, ttype, from_id, to_id, t, name, account_id=None):
    body = {"transaction_type": ttype, "from_account_id": from_id, "to_account_id": to_id,
            "date": line["date"], "amount": float(money(line["amount"])), "reference_number": ref(t),
            "description": f"{name} | {(t.get('bankDescription') or '')[:150]}"}
    if account_id:
        body["account_id"] = account_id
    return z.post(f"/banktransactions/uncategorized/{line['transaction_id']}/categorize", body)


def journal_lines(dep, by_code, bank_or_clearing_id, total):
    """toast_xero line items -> Zoho journal lines. Positive amounts credit the
    account (sales, tax, tips), negative amounts debit it (fees, cash, discounts)."""
    items = [{"account_id": bank_or_clearing_id, "debit_or_credit": "debit", "amount": float(total),
              "description": "Toast payout"}]
    for li in dep["line_items"]:
        amt = money(li["amount"])
        if amt == 0:
            continue
        items.append({"account_id": by_code[li["code"]]["account_id"],
                      "debit_or_credit": "credit" if amt > 0 else "debit",
                      "amount": float(abs(amt)), "description": li["category"]})
    return items


def post_toast_journal(z, dep, by_code, debit_account_id, line, t):
    # idempotent: a journal with this reference may exist from an interrupted run
    found = z.get("/journals", reference_number=ref(t)).get("journals", [])
    found = [j for j in found if j.get("reference_number") == ref(t)]
    if found:
        return z.get(f"/journals/{found[0]['journal_id']}")["journal"]
    total = money(dep["total_payouts"])
    body = {"journal_date": line["date"], "reference_number": ref(t),
            "notes": f"Toast payout settled {dep['settle_date']} for sales {', '.join(dep['sales_dates'])}",
            "line_items": journal_lines(dep, by_code, debit_account_id, total)}
    return z.post("/journals", body)["journal"]


# --- monthly reconciliation records ---------------------------------------------


def month_ends(start, end):
    d = date.fromisoformat(start).replace(day=1)
    last = date.fromisoformat(end)
    while True:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        me = nxt - timedelta(days=1)
        if me > last:
            return
        yield d, me
        d = nxt


def reconcile(z, accounts, openings, apply, log):
    """One reconciliation record per full month: closing balance from Mercury
    (opening + posted txns through month end), transactions = every categorized
    or matched document dated in the month. Zoho reports the difference."""
    log("## Monthly reconciliation records")
    log("| account | month | Zoho closing | Mercury closing | diff | docs | status |")
    log("|---|---|---|---|---|---|---|")
    # Zoho reconciles strictly by its own document dates: the opening of a period is
    # the sum of reconciled documents dated before it, and a document dated inside
    # the period must be selected in that period. A transfer Mercury posts late on
    # the 31st (Eastern) is dated the 1st by the feed, so Zoho's month-end differs
    # from Mercury's by exactly those straddlers; they are listed under the table.
    mercury_date = {t["id"]: eastern_date(t["postedAt"]) for a in accounts for t in a["txns_2026"]}
    for acct in accounts:
        zid = acct["zoho_id"]
        name = BANK_NAMES[acct["xero_bank_account"]]
        existing = {r.get("start_date"): r for r in z.get(f"/bankaccounts/{zid}/reconciliations").get("reconciliations", [])}
        docs = list(z.pages(f"/bankaccounts/{zid}/transactions", "banktransactions"))
        docs = [d for d in docs if d.get("status") not in ("uncategorized", "excluded")]
        for d in docs:
            mid = (d.get("reference_number") or "").removeprefix("mercury:")
            d["_mdate"] = mercury_date.get(mid) or date.fromisoformat(d["date"])
            d["_signed"] = money(d.get("amount", 0)) * (1 if d.get("debit_or_credit") == "debit" else -1)
            d["_rec"] = d.get("reconcile_status") == "reconciled"
        mbal = openings[zid]
        for first, last in month_ends(START_DEFAULT, date.today().isoformat()):
            # Mercury month-end, Eastern dates; the first month starts at OPENING_DATE
            # because a late-Dec-31 posting sits in the 2026 fetch (UTC) and was netted
            # out of the opening balance.
            lo = date.fromisoformat(OPENING_DATE) if first.isoformat() == START_DEFAULT else first
            mbal += sum(money(t["amount"]) for t in acct["txns_2026"] if lo <= eastern_date(t["postedAt"]) <= last)
            sel = [d for d in docs if not d["_rec"] and lo.isoformat() <= d["date"] <= last.isoformat()]
            z_open = sum(d["_signed"] for d in docs if d["_rec"] and d["date"] < lo.isoformat())
            z_close = z_open + sum(d["_signed"] for d in sel)
            straddle = [d for d in sel if not (first <= d["_mdate"] <= last)] + \
                       [d for d in docs if d["_rec"] and first <= d["_mdate"] <= last and not (first.isoformat() <= d["date"] <= last.isoformat())]
            if first.isoformat() in existing:
                r = existing[first.isoformat()]
                log(f"| {name} | {first:%Y-%m} | {r.get('closing_balances')} | {mbal} | {money(r.get('closing_balances', 0)) - mbal} | — | exists: {r.get('status')} |")
                for d in docs:
                    if r.get("status") == "reconciled" and first.isoformat() <= d["date"] <= last.isoformat():
                        d["_rec"] = True
                continue
            if not sel and acct.get("closed") and first.isoformat() > acct["closed"]:
                continue  # closed account, nothing after the closure
            if not apply:
                log(f"| {name} | {first:%Y-%m} | {z_close} | {mbal} | {z_close - mbal} | {len(sel)} | dry run |")
            else:
                # the API wants a plain list of ids here (the documented object form is rejected)
                body = {"start_date": first.isoformat(), "end_date": last.isoformat(),
                        "closing_balance": float(z_close), "save_option": "reconcile",
                        "transactions_to_be_reconciled": [d["transaction_id"] for d in sel]}
                try:
                    r = z.post(f"/bankaccounts/{zid}/reconciliations", body)
                    rec = r.get("reconciliation", r)
                    log(f"| {name} | {first:%Y-%m} | {z_close} | {mbal} | {z_close - mbal} | {len(sel)} | {rec.get('status', r.get('message'))} |")
                except ZohoError as e:
                    log(f"| {name} | {first:%Y-%m} | {z_close} | {mbal} | {z_close - mbal} | {len(sel)} | FAILED {str(e)[-100:]} |")
                    break  # later months depend on this one
            for d in sel:
                d["_rec"] = True
            for d in straddle:
                log(f"    straddle: Zoho {d['date']} / Mercury {d['_mdate']} {d['_signed']} {d.get('transaction_type')}")


# --- repair: re-run match-first for transfers created from one account --------------


def undo_created_transfers(z, state, from_id, log):
    """For every transfer document this script created from account `from_id`'s
    side, uncategorize the statement line (which deletes the document) and match the
    line to the document the other side already had; re-create only if Zoho offers
    no candidate. Used after the 0551 import created duplicates of 5396's transfers."""
    todo = [(m, rec) for m, rec in state.items()
            if isinstance(rec, dict) and rec.get("op") == "transfer_fund" and rec.get("from") == from_id]
    log(f"## Undo {len(todo)} transfers created from {from_id[-6:]}")
    n_match = n_keep = n_fail = 0
    for mid, rec in todo:
        line_id, to = rec["line"], rec["to"]
        try:
            z.post(f"/banktransactions/{line_id}/uncategorize")
        except ZohoError as e:
            if z.capped:
                raise
            log(f"  {mid[:8]}: uncategorize failed ({str(e)[-70:]}); checking whether the line is already free")
        line = next((ln for ln in feed_lines(z, from_id) if ln["transaction_id"] == line_id), None)
        if line is None:
            log(f"  {mid[:8]}: line {line_id} not found after uncategorize")
            n_fail += 1
            continue
        pick, cands = match_line(z, line, want_type="transfer_fund")
        if pick is None:
            t = {"id": mid, "bankDescription": line.get("description"), "counterpartyName": line.get("payee")}
            categorize_transfer(z, line, from_id, to, t)
            n_keep += 1
        else:
            state[mid] = {"op": "match", "line": line_id, "to": pick["transaction_id"]}
            n_match += 1
        save_state(state)
    log(f"  matched {n_match}, re-created {n_keep}, failed {n_fail}\n")


# --- feed-only accounts (Bank of America): the feed is the data -------------------
#
# No bank API behind these accounts, so each uncategorized line is classified on
# its own payee/description: merchants.yaml `boa_transfers` (other side of a
# transfer), then `boa_merchants`, then the shared `merchants`. Documents carry
# reference boa:<statement line id> and the checkpoint uses the same key.
# "RETURN OF POSTED CHECK / ITEM (RECEIVED ON MM-DD)" lines are the bank bouncing
# a debit: they inherit that debit's classification and post as its refund.

RETURN_MARK = "RETURN OF POSTED CHECK"


def line_signed(line):
    """Signed amount in Mercury's convention (+ = money into the business).
    Bank feed: debit = money in. Card feed: debit = payment (owed goes down),
    credit = charge (money out of the business). Verified on the 2119 feed 2026-10-17."""
    amt = money(line["amount"])
    return amt if line["debit_or_credit"] == "debit" else -amt


def line_name(line):
    """Review-list name: the feed's payee when set, else the description with the
    bank's numbers, masks and dates stripped."""
    if line.get("payee"):
        return line["payee"]
    words = [w for w in (line.get("description") or "").split()
             if not any(ch.isdigit() for ch in w) and "XXXX" not in w and w != "#"]
    return " ".join(words[:6]) or "?"


def fake_txn(line):
    """Shape a feed line like a Mercury txn so categorize_* / ref() work unchanged."""
    return {"id": line["transaction_id"], "ref": f"boa:{line['transaction_id']}",
            "amount": float(line_signed(line)), "postedAt": f"{line['date']}T12:00:00Z",
            "bankDescription": line.get("description") or "", "counterpartyName": line.get("payee") or ""}


def classify_feed(lines, cfg, own_name):
    """-> [(line, kind, payload)]: transfer -> other account's Xero name;
    coded -> (name, code, is_return); unmatched -> name."""
    by_key = defaultdict(list)
    for ln in lines:
        by_key[(ln["date"], money(ln["amount"]), ln["debit_or_credit"])].append(ln)
    cls = {}
    for ln in lines:
        desc = ln.get("description") or ""
        if RETURN_MARK in desc:
            continue
        # ACH descriptors name the account holder ("INDN:JAMAL COLE CO ID:…"): drop that
        # segment so the owner pattern in merchants: cannot claim vendor debits
        hay = f"{ln.get('payee') or ''} {re.sub(r'INDN:.*?CO ID:', '', desc)}".lower()
        target = next((acct for pat, acct in cfg.get("boa_transfers", {}).items()
                       if pat.lower() in hay and acct != own_name), None)
        if target:
            cls[ln["transaction_id"]] = ("transfer", target)
            continue
        m = next((m for m in cfg.get("boa_merchants", []) + cfg.get("merchants", [])
                  if m["pattern"].lower() in hay), None)
        cls[ln["transaction_id"]] = ("coded", (line_name(ln), m["account"], False)) if m else ("unmatched", line_name(ln))
    used = set()
    for ln in lines:
        desc = ln.get("description") or ""
        if RETURN_MARK not in desc:
            continue
        orig = None
        if "RECEIVED ON " in desc:
            d0 = f"{ln['date'][:4]}-{desc.split('RECEIVED ON ')[-1][:5]}"
            opposite = "credit" if ln["debit_or_credit"] == "debit" else "debit"
            orig = next((o for o in by_key.get((d0, money(ln["amount"]), opposite), [])
                         if o["transaction_id"] not in used), None)
        if orig is None:
            cls[ln["transaction_id"]] = ("unmatched", f"RETURN of ? {line_name(ln)}")
            continue
        used.add(orig["transaction_id"])
        k, p = cls[orig["transaction_id"]]
        if k == "coded":
            cls[ln["transaction_id"]] = ("coded", (f"RETURN of {p[0]}", p[1], True))
        elif k == "transfer":
            cls[ln["transaction_id"]] = ("transfer", p)     # money comes back: a transfer the other way
        else:
            cls[ln["transaction_id"]] = ("unmatched", f"RETURN of {p}")
    return [(ln, *cls[ln["transaction_id"]]) for ln in lines]


def load_closings():
    data = yaml.safe_load(CLOSING_FILE.read_text()) if CLOSING_FILE.exists() else None
    return {k: {str(kk): vv for kk, vv in (v or {}).items()} for k, v in (data or {}).items()}


def post_opening_journal(z, zid, bal, note):
    """Opening balance as a journal (bank <-> Opening Balance Offset); bal is the
    account's own balance (positive = debit balance, so a card's amount owed is negative)."""
    refno = f"opening:{zid}"
    found = [j for j in z.get("/journals", reference_number=refno).get("journals", []) if j.get("reference_number") == refno]
    if found:
        return found[0]["journal_id"]
    _, by_name = chart_index(z)
    offset = by_name["Opening Balance Offset"]["account_id"]
    r = z.post("/journals", {"journal_date": OPENING_DATE, "reference_number": refno, "notes": note,
                             "line_items": [
                                 {"account_id": zid, "debit_or_credit": "debit" if bal >= 0 else "credit", "amount": float(abs(bal))},
                                 {"account_id": offset, "debit_or_credit": "credit" if bal >= 0 else "debit", "amount": float(abs(bal))}]})
    return r["journal"]["journal_id"]


def feed_only(z, cfg, state, by_code, banks, apply, limit, log):
    """Classify every uncategorized line of the feed-only accounts and (apply)
    post: opening journals from closing_balances.yaml, transfers between the two
    accounts (create from the money-out side, match the other), coded lines,
    returned items as refunds. Returns {zoho_id: (account cfg, plan)}."""
    accounts = cfg.get("zoho_feed_only_accounts") or sys.exit("merchants.yaml has no zoho_feed_only_accounts")
    closings = load_closings()
    log("## Feed-only accounts (Bank of America)")
    log("| account | uncategorized | transfers | coded | unmatched | in state | feed balance | implied opening 2025-12-31 | statement opening |")
    log("|---|---|---|---|---|---|---|---|---|")
    plans = {}
    for acct in accounts:
        name = acct["xero_bank_account"]
        b = banks[BANK_NAMES[name]]
        zid = acct["zoho_id"] = b["account_id"]
        acct["kind"] = acct.get("kind", "bank")
        lines = feed_lines(z, zid)
        plan = classify_feed(lines, cfg, name)
        plans[zid] = (acct, plan)
        kinds = defaultdict(int)
        for _, k, _ in plan:
            kinds[k] += 1
        net = sum(line_signed(ln) for ln in lines)
        feed_bal = money(b["bank_balance"])
        # Zoho's book balance = everything already posted (+ the opening journal, once posted).
        # Bank: book is signed like the lines. Card: Zoho shows the amount owed, i.e. -(debit - credit).
        ob = money(state.get("_opening", {}).get(zid, 0))        # bank: +balance; card: -owed
        book = money(b["balance"])
        if acct["kind"] == "bank":
            acct["implied_opening"] = feed_bal - net - (book - ob)
        else:
            acct["implied_opening"] = feed_bal + net - (book + ob)
        acct["statement_opening"] = closings.get(name, {}).get(OPENING_DATE)
        in_state = sum(1 for ln, _, _ in plan if f"boa:{ln['transaction_id']}" in state)
        log(f"| {b['account_name'][:44]} | {len(lines)} | {kinds['transfer']} | {kinds['coded']} | {kinds['unmatched']} | {in_state} "
            f"| {feed_bal} | {acct['implied_opening']} | {acct['statement_opening'] if acct['statement_opening'] is not None else '—'} |")
    log("  (implied opening = today's feed balance less the feed's 2026 net; equals the Dec 2025 statement closing only if the feed is complete)\n")

    for zid, (acct, plan) in plans.items():
        agg = defaultdict(lambda: [0, Decimal(0)])
        for ln, k, p in plan:
            label = f"↔ {p}" if k == "transfer" else (f"{p[1]} {p[0]}" if k == "coded" else p)
            agg[(k, label)][0] += 1
            agg[(k, label)][1] += line_signed(ln)
        log(f"### {BANK_NAMES[acct['xero_bank_account']]}")
        for (k, label), (n, s) in sorted(agg.items(), key=lambda kv: (kv[0][0], -kv[1][0])):
            hint = f'   # suggested: - {{pattern: "{label[:30]}", account: "????"}}' if k == "unmatched" else ""
            log(f"- {k:9} {label}: {n} lines, net {s}{hint}")
        log()
    if not apply:
        return plans

    writes, failures = 0, []
    key = lambda ln: f"boa:{ln['transaction_id']}"  # noqa: E731
    budget = lambda: z.capped or (limit and writes >= limit)  # noqa: E731

    def done(ln, rec):
        state[key(ln)] = rec
        save_state(state)

    # opening journals, only from operator-supplied statement balances
    posted_ob = state.setdefault("_opening", {})
    for zid, (acct, _) in plans.items():
        stmt = acct["statement_opening"]
        if stmt is None or zid in posted_ob:
            continue
        bal = money(stmt) if acct["kind"] == "bank" else -money(stmt)
        try:
            jid = post_opening_journal(z, zid, bal, f"Opening balance as of {OPENING_DATE} (Bank of America statement)")
            posted_ob[zid] = str(bal)
            save_state(state)
            log(f"opening balance for {zid[-6:]} = {bal} posted as journal {jid}")
        except ZohoError as e:
            failures.append(("opening", None, str(e)))

    # transfers between the feed-only accounts: money-out side creates, the other matches
    bank_id = {xn: banks[zn]["account_id"] for xn, zn in BANK_NAMES.items() if zn in banks}
    groups = defaultdict(lambda: {"out": [], "in": []})
    for zid, (acct, plan) in plans.items():
        for ln, k, p in plan:
            if k != "transfer":
                continue
            other = bank_id.get(p)
            if other is None:
                failures.append(("transfer", ln, f"no Zoho bank account for '{p}'"))
                continue
            frm, to, side = (zid, other, "out") if line_signed(ln) < 0 else (other, zid, "in")
            groups[(ln["date"], str(money(ln["amount"])), frm, to)][side].append(ln)
    for gkey in sorted(groups):
        g = groups[gkey]
        _, _, frm, to = gkey
        creators, matchers = g["out"], g["in"]
        if (not creators or not matchers) and frm in plans and to in plans:
            # one side only, the other account is in this run: its line was posted in an
            # earlier run (or by hand) and is no longer uncategorized. MATCH first; create
            # only when Zoho offers no candidate — a create here duplicates the document.
            for ln in creators + matchers:
                if key(ln) in state or budget():
                    continue
                try:
                    pick, cands = match_line(z, ln, want_type="transfer_fund")
                    if pick is None:
                        resp = categorize_transfer(z, ln, frm, to, fake_txn(ln))
                        doc = resp.get("banktransaction") or resp.get("transaction") or {}
                        done(ln, {"op": "transfer_fund", "line": ln["transaction_id"], "from": frm, "to": to, "doc": doc.get("transaction_id")})
                    else:
                        done(ln, {"op": "match", "line": ln["transaction_id"], "to": pick["transaction_id"]})
                    writes += 1
                except ZohoError as e:
                    failures.append(("transfer", ln, str(e)))
                    log(f"  FAIL one-sided transfer {ln['date']} {ln['amount']}: {e}")
            continue
        if not creators:
            creators, matchers = matchers, []
        pairs = list(zip(creators, matchers + [None] * len(creators)))
        for c, m in pairs:
            if key(c) in state or budget():
                continue
            try:
                if m is not None and key(m) in state:        # partner already posted: match, never duplicate
                    pick, cands = match_line(z, c, want_type="transfer_fund", known_id=state[key(m)].get("doc"))
                    if pick is None:
                        failures.append(("transfer", c, f"partner posted but no match candidate ({len(cands)})"))
                        continue
                    done(c, {"op": "match", "line": c["transaction_id"], "to": pick["transaction_id"]})
                else:
                    resp = categorize_transfer(z, c, frm, to, fake_txn(c))
                    doc = resp.get("banktransaction") or resp.get("transaction") or {}
                    done(c, {"op": "transfer_fund", "line": c["transaction_id"], "from": frm, "to": to, "doc": doc.get("transaction_id")})
                writes += 1
            except ZohoError as e:
                failures.append(("transfer", c, str(e)))
                log(f"  FAIL transfer {c['date']} {c['amount']}: {e}")
        for c, m in pairs:
            if m is None or key(m) in state or budget():
                continue
            if key(c) not in state:
                failures.append(("transfer-match", m, "creator side not posted, cannot match"))
                continue
            try:
                pick, cands = match_line(z, m, want_ref=f"boa:{c['transaction_id']}", want_type="transfer_fund",
                                         known_id=state[key(c)].get("doc"))
                if pick is None:
                    failures.append(("transfer-match", m, f"no match candidate ({len(cands)})"))
                    continue
                done(m, {"op": "match", "line": m["transaction_id"], "to": pick["transaction_id"]})
                writes += 1
            except ZohoError as e:
                failures.append(("transfer-match", m, str(e)))
                log(f"  FAIL match {m['date']} {m['amount']}: {e}")

    # coded lines (expenses, refunds of returned items, owner contributions / draws)
    for zid, (acct, plan) in plans.items():
        for ln, k, p in sorted(plan, key=lambda e: e[0]["date"]):
            if k != "coded" or key(ln) in state or budget():
                continue
            name, code, _ = p
            acc = by_code[code]["account_id"]
            t, s = fake_txn(ln), line_signed(ln)
            try:
                if code in EQUITY_CODES and s < 0:
                    categorize_generic(z, ln, "owner_drawings", zid, acc, t, name)
                    op = "owner_drawings"
                elif code in EQUITY_CODES:
                    categorize_generic(z, ln, "owner_contribution", acc, zid, t, name)
                    op = "owner_contribution"
                elif code.startswith(INCOME_PREFIX) and s > 0:
                    categorize_generic(z, ln, "deposit", acc, zid, t, name)   # cash sales -> income
                    op = "deposit"
                elif s < 0:
                    categorize_expense(z, ln, acc, zid, t, name)
                    op = "expense"
                else:
                    categorize_generic(z, ln, "expense_refund", acc, zid, t, name, account_id=acc)
                    op = "expense_refund"
                done(ln, {"op": op, "line": ln["transaction_id"], "account": code, "bank": zid})
                writes += 1
            except ZohoError as e:
                failures.append((k, ln, str(e)))
                log(f"  FAIL coded {ln['date']} {s} {name}: {e}")

    log("\n## Verification")
    if z.capped:
        log("Zoho daily API cap reached — run stopped early; re-run tomorrow (state is checkpointed).")
    log("| account | Zoho book balance | Zoho feed balance | opening posted | uncategorized left |")
    log("|---|---|---|---|---|")
    banks = bank_index(z) if not z.capped else banks
    for zid, (acct, _) in plans.items():
        b = banks[BANK_NAMES[acct["xero_bank_account"]]]
        ob = posted_ob.get(zid, "no (statement balance missing)")
        flag = "" if money(b["balance"]) == money(b["bank_balance"]) else "  ← differs"
        log(f"| {b['account_name'][:44]} | {b['balance']} | {b['bank_balance']} | {ob} | {b['uncategorized_transactions']} |{flag}")
    if failures:
        log(f"\n### Failures ({len(failures)})")
        for kind, ln, msg in failures:
            log(f"- {kind} {ln['date'] if ln else ''} {ln['amount'] if ln else ''}: {msg[:300]}")
    log(f"\nwrites: {writes}, Zoho calls: {z.calls}")
    return plans


def reconcile_feed_only(z, plans, apply, log):
    """One record per full month, closing = the statement balance the operator put in
    closing_balances.yaml (`<Xero bank name>: {YYYY-MM: balance}`, a card's balance
    being the amount owed). Posted only when Zoho's own closing equals it."""
    closings = load_closings()
    log("## Monthly reconciliation records (statement closings from closing_balances.yaml)")
    log("| account | month | Zoho closing | statement | diff | docs | status |")
    log("|---|---|---|---|---|---|---|")
    for zid, (acct, _) in plans.items():
        name = acct["xero_bank_account"]
        zname = BANK_NAMES[name]
        stmts = closings.get(name, {})
        existing = {r.get("start_date"): r for r in z.get(f"/bankaccounts/{zid}/reconciliations").get("reconciliations", [])}
        docs = [d for d in z.pages(f"/bankaccounts/{zid}/transactions", "banktransactions")
                if d.get("status") not in ("uncategorized", "excluded")]
        for d in docs:
            d["_signed"] = money(d.get("amount", 0)) * (1 if d.get("debit_or_credit") == "debit" else -1)
            d["_rec"] = d.get("reconcile_status") == "reconciled"
        for first, last in month_ends(START_DEFAULT, date.today().isoformat()):
            lo = date.fromisoformat(OPENING_DATE) if first.isoformat() == START_DEFAULT else first
            sel = [d for d in docs if not d["_rec"] and lo.isoformat() <= d["date"] <= last.isoformat()]
            z_close = sum(d["_signed"] for d in docs if d["_rec"] and d["date"] < lo.isoformat()) + sum(d["_signed"] for d in sel)
            z_bal = z_close if acct["kind"] == "bank" else -z_close     # card: report as amount owed
            stmt = stmts.get(f"{first:%Y-%m}")
            if first.isoformat() in existing:
                r = existing[first.isoformat()]
                log(f"| {zname[:30]} | {first:%Y-%m} | {r.get('closing_balances')} | {stmt} | — | — | exists: {r.get('status')} |")
                for d in docs:
                    if r.get("status") == "reconciled" and first.isoformat() <= d["date"] <= last.isoformat():
                        d["_rec"] = True
                continue
            if stmt is None:
                log(f"| {zname[:30]} | {first:%Y-%m} | {z_bal} | — | — | {len(sel)} | no statement balance |")
            elif money(stmt) != z_bal:
                log(f"| {zname[:30]} | {first:%Y-%m} | {z_bal} | {stmt} | {z_bal - money(stmt)} | {len(sel)} | MISMATCH, not posted |")
            elif not apply:
                log(f"| {zname[:30]} | {first:%Y-%m} | {z_bal} | {stmt} | 0.00 | {len(sel)} | dry run |")
            else:
                body = {"start_date": first.isoformat(), "end_date": last.isoformat(), "closing_balance": float(z_bal),
                        "save_option": "reconcile", "transactions_to_be_reconciled": [d["transaction_id"] for d in sel]}
                try:
                    r = z.post(f"/bankaccounts/{zid}/reconciliations", body)
                    log(f"| {zname[:30]} | {first:%Y-%m} | {z_bal} | {stmt} | 0.00 | {len(sel)} | {r.get('reconciliation', r).get('status', r.get('message'))} |")
                except ZohoError as e:
                    log(f"| {zname[:30]} | {first:%Y-%m} | {z_bal} | {stmt} | 0.00 | {len(sel)} | FAILED {str(e)[-100:]} |")
                    break
            for d in sel:
                d["_rec"] = True


# --- bank rules for future feed lines ------------------------------------------


def seed_rules(z, cfg, accounts, by_code, apply, log, banks=None):
    """One withdrawal rule per merchants.yaml pattern so future feed lines
    auto-categorize. Transfers and Toast payouts stay script-driven (a rule on
    both sides of a transfer would post it twice; Toast needs the journal).
    The BoA block (`boa_merchants`) gets its own rules on the feed-only accounts;
    deposit-side patterns (owner contributions, 3100) stay script-driven."""
    existing = {r["rule_name"] for r in z.get("/bankaccounts/rules").get("rules", [])}
    wanted = [("Mercury", ",".join(a["zoho_id"] for a in accounts), m) for m in cfg["merchants"]] if accounts else []
    boa = cfg.get("zoho_feed_only_accounts") or []
    if boa and banks:
        boa_ids = ",".join(banks[BANK_NAMES[a["xero_bank_account"]]]["account_id"] for a in boa)
        wanted += [("BoA", boa_ids, m) for m in cfg.get("boa_merchants", []) if m["account"] != "3100"]
    log("## Bank rules")
    n = 0
    for prefix, account_ids, m in wanted:
        code = m["account"]
        # Zoho rejects punctuation such as ':' and '>' in rule names
        name = re.sub(r"[^A-Za-z0-9 ]", "", f"{prefix} {m['pattern']} to {code}")[:50]
        if name in existing:
            continue
        record_as = "owner_drawings" if code in EQUITY_CODES else "expense"
        body = {"rule_name": name, "rule_category": "selected_accounts", "account_ids": account_ids,
                "apply_to": "withdrawals", "criteria_type": "and",
                "criterion": [{"field": "description", "comparator": "contains", "value": m["pattern"]}],
                "record_as": record_as, "account_id": by_code[code]["account_id"],
                "auto_categorize": "autocategorize", "reference_number": "from_statement"}
        log(f"  rule: {name}" + ("" if apply else " (dry run)"))
        if apply:
            try:
                z.post("/bankaccounts/rules", body)
                n += 1
            except ZohoError as e:
                log(f"    FAILED: {str(e)[:200]}")
                if n == 0:
                    log("    stopping after first failure; fix the body shape and re-run --rules")
                    return
    log(f"  {n} rules created\n")


# --- main --------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write to Zoho (default: dry run)")
    ap.add_argument("--start", default=START_DEFAULT)
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--toast", help="toast_xero deposits JSON (from the toast dump)")
    ap.add_argument("--spike", action="store_true", help="S-1: only two Toast payouts (one clean, one split)")
    ap.add_argument("--toast-approach", choices=["A", "B"], default="A",
                    help="A = journal on the bank account + match; B = deposit to 1320 + journal from 1320")
    ap.add_argument("--limit", type=int, default=0, help="stop after N writes (apply)")
    ap.add_argument("--reconcile", action="store_true", help="create monthly reconciliation records")
    ap.add_argument("--rules", action="store_true", help="create bank rules from merchants.yaml")
    ap.add_argument("--feed-only", action="store_true",
                    help="Bank of America: classify/post the uncategorized feed lines of zoho_feed_only_accounts (no Mercury)")
    ap.add_argument("--undo-created-transfers", metavar="ZOHO_ACCOUNT_ID",
                    help="repair: re-match transfers this script created from that account's side")
    ap.add_argument("--report", default=str(HERE / "docs/zoho-books-recon-report.md"))
    args = ap.parse_args()
    apply = args.apply
    if args.feed_only and args.report == ap.get_default("report"):
        args.report = str(HERE / "docs/zoho-books-boa-report.md")
    out = []

    def log(s=""):
        print(s)
        out.append(s)

    cfg = yaml.safe_load((HERE / "merchants.yaml").read_text())
    z = Zoho()
    state = load_state()
    if args.undo_created_transfers:
        undo_created_transfers(z, state, args.undo_created_transfers, log)
        Path(args.report).write_text("\n".join(out))
        return
    log(f"# Zoho Books recon {'APPLY' if apply else 'DRY RUN'} — {date.today().isoformat()} ({args.start} → {args.end})\n")

    # --- chart + banks ---
    needed = set(m["account"] for m in cfg["merchants"]) | {"1320", "3100"}
    needed |= {m["account"] for m in cfg.get("boa_merchants", [])}
    deposits = load_toast(args.toast)
    for dep in deposits:
        needed |= {li["code"] for li in dep["line_items"]}
    missing_defs = [c for c in needed if c not in CHART]
    if missing_defs:
        sys.exit(f"codes without a CHART definition: {missing_defs}")
    log("## Chart of accounts / bank accounts")
    by_code, by_name = seed_chart(z, needed, apply, log)
    banks = seed_banks(z, apply, log)
    bank_id = {xn: banks[zn]["account_id"] for xn, zn in BANK_NAMES.items() if zn in banks}
    log(f"  {len(by_code)} coded accounts, banks: {', '.join(BANK_NAMES[k] for k in bank_id)}\n")

    # --- feed-only accounts (BoA): no Mercury, the feed is the data ---
    if args.feed_only:
        plans = feed_only(z, cfg, state, by_code, banks, apply and not args.reconcile and not args.rules, args.limit, log)
        if args.rules:
            seed_rules(z, cfg, [], by_code, apply, log, banks=banks)
        if args.reconcile:
            reconcile_feed_only(z, plans, apply, log)
        Path(args.report).write_text("\n".join(out))
        log(f"{'applied' if apply else 'dry run only'} — report at {args.report}; Zoho calls: {z.calls}")
        return

    # --- Mercury + feed ---
    mbal = mercury_balances()
    accounts = cfg["accounts"] + cfg.get("zoho_only_accounts", [])
    plan = []          # (account_cfg, mercury txn, feed line, kind, payload)
    unmatched = []     # (account, t, name)
    unpaired_m, unpaired_z = [], []
    toast_txns = []
    log("## Mercury ↔ feed pairing")
    log("| account | Mercury posted | feed uncategorized | paired | Mercury unpaired | feed unpaired | opening 2025-12-31 |")
    log("|---|---|---|---|---|---|---|")
    openings = {}
    for acct in accounts:
        zoho_name = BANK_NAMES[acct["xero_bank_account"]]
        zid = banks[zoho_name]["account_id"]
        acct["zoho_id"] = zid
        txns = mp.fetch_mercury(acct["mercury_account_id"], args.start, args.end)
        if acct.get("statement_import"):
            import_statement(z, zid, zoho_name, txns, state, apply, log)
        lines = feed_lines(z, zid)
        paired, un_m, un_z = pair(txns, lines)
        # Mercury txns whose line is already gone are done (state or UI); report only if unknown
        un_m_unknown = [t for t in un_m if t["id"] not in state]
        unpaired_m += [(acct, t) for t in un_m_unknown]
        unpaired_z += [(acct, ln) for ln in un_z]
        cur = mbal[acct["mercury_account_id"]]["currentBalance"]
        all_2026 = mp.fetch_mercury(acct["mercury_account_id"], START_DEFAULT, date.today().isoformat())
        acct["txns_2026"] = all_2026
        openings[zid] = money(cur) - sum(money(t["amount"]) for t in all_2026)
        log(f"| {zoho_name} | {len(txns)} | {len(lines)} | {len(paired)} | {len(un_m_unknown)} | {len(un_z)} | {openings[zid]} |")

        transfers, coded, skipped, unm = mp.classify(txns, cfg, acct["xero_bank_account"])
        for t, target in transfers:
            if t["id"] in paired:
                plan.append((acct, t, paired[t["id"]], "transfer", target))
        for t, name, code in coded:
            if t["id"] in paired:
                plan.append((acct, t, paired[t["id"]], "coded", (name, code)))
        for t, why in skipped:
            if t["id"] in paired:
                toast_txns.append((acct, t, paired[t["id"]]))
        for t, name in unm:
            if t["id"] in paired:
                unmatched.append((acct, t, name))
    log()

    # --- Toast pairing ---
    toast_map, toast_missing = pair_toast([t for _, t, _ in toast_txns], deposits) if deposits else ({}, [t for _, t, _ in toast_txns])
    log(f"## Toast payouts: {len(toast_txns)} Mercury credits, {len(toast_map)} with a toast_xero deposit, "
        f"{len(toast_missing)} without (left uncategorized)")
    if args.spike:
        clean = next((t for _, t, _ in toast_txns if t["id"] in toast_map and not toast_map[t["id"]]["split_remainder"]), None)
        split = next((t for _, t, _ in toast_txns if t["id"] in toast_map and toast_map[t["id"]]["split_remainder"]), None)
        keep = {x["id"] for x in (clean, split) if x}
        toast_txns = [x for x in toast_txns if x[1]["id"] in keep]
        log(f"  SPIKE: {len(toast_txns)} payouts selected (clean={clean and eastern_date(clean['postedAt'])}, "
            f"split-remainder={split and eastern_date(split['postedAt'])})")
        # plus one sample of every other write shape: an internal transfer pair,
        # an expense, an expense refund, an owner draw
        sample, seen = [], set()
        pair_key = None
        for e in sorted(plan, key=lambda p: p[1]["postedAt"]):
            acct, t, line, kind, payload = e
            if kind == "transfer":
                other = bank_id.get(payload)
                if other is None or other not in {a["zoho_id"] for a in accounts}:
                    continue
                frm, to = (acct["zoho_id"], other) if t["amount"] < 0 else (other, acct["zoho_id"])
                k = (line["date"], str(money(line["amount"])), frm, to)
                if pair_key is None:
                    pair_key = k
                if k == pair_key:
                    sample.append(e)
                continue
            mname, code = payload
            shape = ("equity" if code in EQUITY_CODES else "expense") + ("-in" if t["amount"] > 0 else "-out")
            if shape not in seen:
                seen.add(shape)
                sample.append(e)
        plan = sample
        log(f"  SPIKE: {len(plan)} sample lines: " + ", ".join(f"{k}:{mp.merchant_name(t)} {t['amount']:.2f}" for _, t, _, k, _ in plan))
    log()

    # --- transfer grouping: one document per internal transfer, count-aware ---
    # key (date, amount, from, to) -> {"out": [...], "in": [...]} plan entries.
    # Internal transfers (both ends have a feed) appear once per side; the
    # money-out side creates the transfer_fund, the money-in side matches it.
    # Transfers to/from a bank with no feed (0551, BoA) have one side only.
    feed_ids = {a["zoho_id"] for a in accounts}
    groups = defaultdict(lambda: {"out": [], "in": []})
    no_bank = defaultdict(int)
    for entry in plan:
        acct, t, line, kind, payload = entry
        if kind != "transfer":
            continue
        own = acct["zoho_id"]
        other = bank_id.get(payload)
        if other is None:
            no_bank[payload] += 1      # bank not created yet (dry run) -> counted, created on apply
            continue
        if t["amount"] < 0:
            frm, to, side = own, other, "out"
        else:
            frm, to, side = other, own, "in"
        groups[(line["date"], str(money(line["amount"])), frm, to)][side].append(entry)
    for g in groups.values():
        for side in ("out", "in"):
            g[side].sort(key=lambda e: e[1]["id"])
    internal = [k for k in groups if k[2] in feed_ids and k[3] in feed_ids]
    uneven = [k for k in internal if len(groups[k]["out"]) != len(groups[k]["in"])]
    n_docs = sum(max(len(g["out"]), len(g["in"])) for g in groups.values())

    # --- summary of the plan ---
    kinds = defaultdict(int)
    for acct, t, line, kind, payload in plan:
        kinds[kind] += 1
    log("## Plan")
    log(f"- transfers: {kinds['transfer']} lines -> {n_docs} transfer documents "
        f"({len(internal)} internal keys, {len(groups) - len(internal)} one-sided keys, {len(uneven)} uneven)")
    for k in uneven[:20]:
        g = groups[k]
        log(f"  uneven {k[0]} {k[1]} {k[2][-6:]}->{k[3][-6:]}: out={len(g['out'])} in={len(g['in'])}")
    for name, n in no_bank.items():
        log(f"  {n} transfer lines wait for bank '{BANK_NAMES.get(name, name)}' to be created (apply)")
    log(f"- coded spends/refunds: {kinds['coded']}")
    log(f"- Toast journals: {len([1 for _, t, _ in toast_txns if t['id'] in toast_map])} (approach {args.toast_approach})")
    log(f"- unmatched merchants (left uncategorized): {len(unmatched)}")
    log(f"- already done (in state): {sum(1 for _, t, _, _, _ in plan if t['id'] in state)}")
    if unpaired_m:
        log(f"\n### Mercury txns with no feed line ({len(unpaired_m)})")
        for acct, t in unpaired_m[:40]:
            log(f"- {BANK_NAMES[acct['xero_bank_account']]} {eastern_date(t['postedAt'])} {t['amount']:>10.2f} {mp.merchant_name(t)}")
    if unpaired_z:
        log(f"\n### Feed lines with no Mercury txn ({len(unpaired_z)})")
        for acct, ln in unpaired_z[:40]:
            log(f"- {BANK_NAMES[acct['xero_bank_account']]} {ln['date']} {ln['debit_or_credit']} {ln['amount']:>10.2f} {ln.get('description','')[:70]}")
    if unmatched:
        log(f"\n### Unmatched merchants ({len(unmatched)}) — add to merchants.yaml or leave")
        agg = defaultdict(lambda: [0, Decimal(0)])
        for acct, t, name in unmatched:
            agg[name][0] += 1
            agg[name][1] += money(t["amount"])
        for name, (n, s) in sorted(agg.items(), key=lambda kv: -kv[1][0]):
            log(f"- {name}: {n} txns, net {s}   # suggested: - {{pattern: \"{name[:30]}\", account: \"????\"}}")
    if toast_missing:
        log(f"\n### Toast credits without a toast_xero deposit ({len(toast_missing)})")
        for t in toast_missing[:40]:
            log(f"- {eastern_date(t['postedAt'])} {t['amount']:>10.2f}  (export the sales day: task toast:export)")
    log()

    if args.reconcile or args.rules:
        if args.rules:
            seed_rules(z, cfg, accounts, by_code, apply, log)
        if args.reconcile:
            reconcile(z, accounts, openings, apply, log)
        Path(args.report).write_text("\n".join(out))
        return

    if not apply:
        Path(args.report).write_text("\n".join(out))
        log(f"dry run only — report at {args.report}; Zoho calls: {z.calls}")
        return

    # --- APPLY ---
    writes = 0
    failures = []

    def done(t, rec):
        state[t["id"]] = rec
        save_state(state)

    def budget():
        return z.capped or (args.limit and writes >= args.limit)

    # opening balances (once per account; the Zoho entity is one record, so PUT the full set)
    posted_ob = state.get("_opening", {})
    if not args.spike and any(zid not in posted_ob for zid in openings):
        currency_id = banks[BANK_NAMES[accounts[0]["xero_bank_account"]]].get("currency_id")
        ob = {"date": OPENING_DATE, "accounts": [
            {"account_id": zid, "debit_or_credit": "debit" if bal >= 0 else "credit", "exchange_rate": 1,
             "currency_id": currency_id, "amount": float(abs(bal))}
            for zid, bal in openings.items()]}
        try:
            existing = z.get("/settings/openingbalances").get("opening_balance") or {}
            if existing.get("opening_balance_id"):
                # the record cannot change once a reconciliation includes it: fall back
                # to one journal per missing account (bank <- Opening Balance Offset)
                _, by_name_now = chart_index(z)
                offset = by_name_now["Opening Balance Offset"]["account_id"]
                for zid, bal in openings.items():
                    if zid in posted_ob:
                        continue
                    refno = f"opening:{zid}"
                    if not any(j.get("reference_number") == refno for j in z.get("/journals", reference_number=refno).get("journals", [])):
                        z.post("/journals", {"journal_date": OPENING_DATE, "reference_number": refno,
                                             "notes": "Opening balance as of 2025-12-31 (Mercury)",
                                             "line_items": [
                                                 {"account_id": zid, "debit_or_credit": "debit" if bal >= 0 else "credit", "amount": float(abs(bal))},
                                                 {"account_id": offset, "debit_or_credit": "credit" if bal >= 0 else "debit", "amount": float(abs(bal))}]})
                    posted_ob[zid] = str(bal)
                    log(f"opening balance for {zid[-6:]} = {bal} posted as a journal ({refno})")
            else:
                z.post("/settings/openingbalances", ob)
                posted_ob = {k: str(v) for k, v in openings.items()}
                log(f"opening balances posted as of {OPENING_DATE}: " + ", ".join(f"{k[-6:]}={v}" for k, v in openings.items()))
            state["_opening"] = posted_ob
            save_state(state)
        except ZohoError as e:
            failures.append(("opening", None, str(e)))
            log(f"opening balances FAILED: {e}")

    # transfers first: creators, then the matching side, group by group
    for key in sorted(groups, key=lambda k: (k[0], k[1])):
        g = groups[key]
        _, _, frm, to = key
        creators, matchers = g["out"], g["in"]
        if (not creators or not matchers) and frm in feed_ids and to in feed_ids:
            # internal transfer whose other side was posted in an earlier run (or by
            # hand): this side must MATCH the existing document, never create a second
            # one. Only if Zoho offers no candidate does it create.
            for acct, t, line, kind, payload in creators + matchers:
                if t["id"] in state or budget():
                    continue
                try:
                    pick, cands = match_line(z, line, want_type="transfer_fund")
                    if pick is None:
                        categorize_transfer(z, line, frm, to, t)
                        done(t, {"op": "transfer_fund", "line": line["transaction_id"], "from": frm, "to": to})
                    else:
                        done(t, {"op": "match", "line": line["transaction_id"], "to": pick["transaction_id"]})
                    writes += 1
                except ZohoError as e:
                    failures.append((kind, t, str(e)))
            continue
        if not creators:                      # money-in from a no-feed bank: in side creates
            creators, matchers = matchers, []
        extra = matchers[len(creators):]      # uneven internal keys: extras create too
        matchers = matchers[:len(creators)]
        for acct, t, line, kind, payload in creators + extra:
            if t["id"] in state or budget():
                continue
            try:
                resp = categorize_transfer(z, line, frm, to, t)
                doc = resp.get("banktransaction") or resp.get("transaction") or {}
                done(t, {"op": "transfer_fund", "line": line["transaction_id"], "from": frm, "to": to,
                         "doc": doc.get("transaction_id")})
                writes += 1
            except ZohoError as e:
                failures.append((kind, t, str(e)))
                log(f"  FAIL transfer {line['date']} {t['amount']:.2f}: {e}")
        for (acct, t, line, kind, payload), (_, ct, _, _, _) in zip(matchers, creators):
            if t["id"] in state or budget():
                continue
            if ct["id"] not in state:
                failures.append((kind, t, "creator side not posted, cannot match"))
                continue
            try:
                pick, cands = match_line(z, line, want_ref=f"mercury:{ct['id']}", want_type="transfer_fund",
                                         known_id=state[ct["id"]].get("doc"))
                if pick is None:
                    failures.append((kind, t, f"no match candidate for transfer ({len(cands)} cands)"))
                    continue
                done(t, {"op": "match", "line": line["transaction_id"], "to": pick["transaction_id"]})
                writes += 1
            except ZohoError as e:
                failures.append((kind, t, str(e)))
                log(f"  FAIL match {line['date']} {t['amount']:.2f}: {e}")

    # coded spends / refunds / owner draws
    for acct, t, line, kind, payload in sorted(plan, key=lambda p: p[1]["postedAt"]):
        if kind != "coded" or t["id"] in state or budget():
            continue
        own = acct["zoho_id"]
        name = mp.merchant_name(t)
        try:
            if True:
                mname, code = payload
                acc = by_code[code]["account_id"]
                if code in EQUITY_CODES:
                    if t["amount"] < 0:
                        categorize_generic(z, line, "owner_drawings", own, acc, t, mname)
                        done(t, {"op": "owner_drawings", "line": line["transaction_id"]})
                    else:
                        categorize_generic(z, line, "owner_contribution", acc, own, t, mname)
                        done(t, {"op": "owner_contribution", "line": line["transaction_id"]})
                elif t["amount"] < 0:
                    categorize_expense(z, line, acc, own, t, mname)
                    done(t, {"op": "expense", "line": line["transaction_id"], "account": code})
                else:
                    categorize_generic(z, line, "expense_refund", acc, own, t, mname, account_id=acc)
                    done(t, {"op": "expense_refund", "line": line["transaction_id"], "account": code})
            writes += 1
        except ZohoError as e:
            failures.append((kind, t, str(e)))
            log(f"  FAIL {kind} {eastern_date(t['postedAt'])} {t['amount']:.2f} {name}: {e}")

    # Toast payouts
    clearing = by_code["1320"]["account_id"]
    for acct, t, line in toast_txns:
        if t["id"] in state or t["id"] not in toast_map or budget():
            continue
        dep = toast_map[t["id"]]
        own = acct["zoho_id"]
        try:
            if args.toast_approach == "A":
                j = post_toast_journal(z, dep, by_code, own, line, t)
                pick, cands = match_line(z, line, want_ref=ref(t), want_type="journal", known_id=j.get("journal_id"))
                if pick is None:
                    types = sorted({c.get("transaction_type") for c in cands})
                    log(f"  Toast A: journal {j['journal_id']} not offered as match ({len(cands)} cands, types {types}); deleting journal")
                    z.delete(f"/journals/{j['journal_id']}")
                    failures.append(("toast-A", t, f"journal not matchable; candidate types {types}"))
                    continue
                done(t, {"op": "toast-journal-match", "line": line["transaction_id"], "journal": j["journal_id"]})
            else:
                categorize_generic(z, line, "deposit", clearing, own, t, "Toast payout")
                j = post_toast_journal(z, dep, by_code, clearing, line, t)
                done(t, {"op": "toast-clearing-journal", "line": line["transaction_id"], "journal": j["journal_id"]})
            writes += 1
            log(f"  Toast {args.toast_approach}: {line['date']} {dep['total_payouts']} "
                f"{'split-remainder' if dep['split_remainder'] else 'clean'} -> journal {j['journal_id']}")
        except ZohoError as e:
            failures.append(("toast", t, str(e)))
            log(f"  FAIL toast {line['date']} {t['amount']:.2f}: {e}")

    # --- verify ---
    log("\n## Verification")
    if z.capped:
        log("Zoho daily API cap reached — run stopped early; re-run `--apply` tomorrow (state is checkpointed).")
    log("| account | Zoho book balance | Zoho feed balance | Mercury current | uncategorized left |")
    log("|---|---|---|---|---|")
    banks = bank_index(z) if not z.capped else banks
    for acct in accounts:
        b = banks[BANK_NAMES[acct["xero_bank_account"]]]
        cur = mbal[acct["mercury_account_id"]]["currentBalance"]
        flag = "" if money(b["balance"]) == money(cur) else "  ← differs"
        log(f"| {b['account_name']} | {b['balance']} | {b['bank_balance']} | {cur} | {b['uncategorized_transactions']} |{flag}")
    # closed accounts: once they sit at 0.00 with nothing uncategorized, hide them
    for acct in accounts:
        b = banks[BANK_NAMES[acct["xero_bank_account"]]]
        if acct.get("closed") and b.get("is_active") and money(b["balance"]) == 0 and not b.get("uncategorized_transactions"):
            try:
                z.post(f"/bankaccounts/{b['account_id']}/inactive")
                log(f"{b['account_name']}: balance 0.00, marked inactive")
            except ZohoError as e:
                log(f"{b['account_name']}: could not mark inactive: {str(e)[-120:]}")
    if failures:
        log(f"\n### Failures ({len(failures)})")
        for kind, t, msg in failures:
            when = eastern_date(t["postedAt"]) if t else ""
            amt = f"{t['amount']:.2f}" if t else ""
            log(f"- {kind} {when} {amt}: {msg[:300]}")
    log(f"\nwrites: {writes}, Zoho calls: {z.calls}")
    Path(args.report).write_text("\n".join(out))


if __name__ == "__main__":
    main()
