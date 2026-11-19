#!/usr/bin/env python3
"""Interest, fees, missed payments and recurring charges for one calendar year,
personal (Monarch) and business (Zoho Books + Mercury + Toast), in one HTML page.

    task finance:fees                       # pull every source for this year, then render
    task finance:fees -- --year 2026        # another year
    task finance:fees:report                # re-render from the last pull (no API calls)

`pull` writes raw dumps to data/fees/<year>/ (gitignored); `report` reads them plus
payments.yaml, debts.yaml and the Bills sheet dump and writes
docs/fees-interest-<year>.html and data/fees/<year>/summary.json.

What counts as a fee or interest (owner's definition, Nov 19 2026): card and loan
interest, late fees, returned-payment / NSF / overdraft fees, maintenance and
dormancy fees, annual card fees, cash-advance fees, foreign-transaction fees and
merchant processing fees (Toast, government card-service fees). Government
licence fees, software subscriptions and the fee-like transfers Bank of America
calls "overdraft protection" are not fees.

Loan interest that is folded into an instalment (auto loan, personal loan, student
loans) is ESTIMATED: average balance x APR x fraction of the year elapsed, with the
APR from the Bills sheet's Loans tab, and cross-checked against payments minus
principal reduction where Monarch has a balance history.
"""
import argparse
import asyncio
import json
import os
import re
import statistics
import subprocess
import sys
import warnings
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import yaml

warnings.filterwarnings("ignore")

HERE = Path(__file__).parent
DATA_DIR = Path(os.getenv("FINANCE_TOOLS_DATA", HERE))     # secrets + caches (a worktree run uses the main checkout)
BILLS_SHEET_ID = os.getenv("ZOHO_BILLS_SHEET_ID", "efajyb8b15e86a20a48e0b3edab6cf4fb18c5")

# --------------------------------------------------------------------------- classification

# Order matters: first match wins. Text is "merchant | original description | zoho account".
FEE_KINDS = [
    ("late_fee",            r"late fee|late payment fee|late charge"),
    ("returned_payment_fee", r"return(ed)? payment fee|returned check charge|return(ed)? item fee|nsf fee|insufficient funds fee"),
    ("overdraft_fee",       r"overdraft item fee|overdraft fee|od fee"),
    ("cash_advance_fee",    r"cash advance fee"),
    ("annual_fee",          r"membership fee|annual fee|annual membership"),
    ("maintenance_fee",     r"maintenance fee|monthly fee|dormancy fee|service charge|inactivity fee"),
    ("foreign_fee",         r"foreign transaction|intl transaction fee"),
    ("merchant_fee",        r"svc fe|merchant fee|processing fee|commissions & fees|payment systems"),
    ("interest",            r"interest|finance charge"),
]
# Things that look like fees but are not (transfers, government fees, subscriptions, payment reversals)
NOT_A_FEE = re.compile(r"overdraft protection|overdraft transfer|tsa |passport|uber \*one|membership uber|"
                       r"returned payment unpaid|chase returned payment|^returned payment$|interest paid|dividend|"
                       r"interest payment|savings interest", re.I)
# Payment reversals: the bank bounced an autopay. Evidence of a missed payment, not a fee.
REVERSAL = re.compile(r"returned payment unpaid|chase returned payment|^returned payment|returned payment$", re.I)

KIND_LABEL = {
    "interest": "Interest", "late_fee": "Late fee", "returned_payment_fee": "Returned-payment fee",
    "overdraft_fee": "Overdraft fee", "cash_advance_fee": "Cash-advance fee", "annual_fee": "Annual fee",
    "maintenance_fee": "Maintenance / dormancy fee", "foreign_fee": "Foreign-transaction fee",
    "merchant_fee": "Merchant processing fee", "other_fee": "Other financial fee",
}
PENALTY_KINDS = {"late_fee", "returned_payment_fee", "overdraft_fee"}     # these are the "missed payment" fees

MONARCH_FEE_CATEGORIES = {"Financial Fees", "Loan Expense"}
MONARCH_INTEREST_CATEGORY = "Interest"
ZOHO_FEE_ACCOUNTS = {"Bank Fees and Charges", "Credit Card Charges", "Merchant Fees", "Interest Expense"}
ZOHO_FEE_PATTERN = re.compile(r"fee|interest|finance charge", re.I)
TOAST_FEE_CODE = "6040"

# Loans whose interest is inside the instalment. sheet: Bills -> Loans tab "Bill Name";
# monarch_account: substring of the Monarch loan account's display name (balance history);
# payment: regex on the payment row's merchant/description in Monarch; registry: payments.yaml id.
LOANS = [
    {"name": "Truist auto loan (...1001)", "sheet": "Truist", "registry": "truist-auto-1001",
     "monarch_account": "Auto Loan 31001", "payment": r"truist"},
    {"name": "Lafayette FCU personal loan (...0621)", "sheet": "Lafayette Federal", "registry": "lafayette-loan-0621",
     "monarch_account": "Personal Loan (...0621)", "payment": r"lafayette", "exclude": r"acctopen|early pay|cash withdrawal",
     "transfer_ok": True},      # Monarch files the in-branch payments from Checking 0621 as Transfer
    {"name": "US Dept of Education (CRI)", "sheet": "CRI", "registry": None,
     "monarch_account": None, "payment": r"dept education|dept of education"},
    {"name": "Perkins loan (Heartland ECSI)", "sheet": "Heartland (perkins)", "registry": None,
     "monarch_account": None, "payment": r"perkins"},
    {"name": "Cornell loan (Heartland ECSI)", "sheet": "Heartland (esci)", "registry": None,
     "monarch_account": None, "payment": r"educational comp|ecsi"},
]

# --------------------------------------------------------------------------- helpers


def money(x):
    return f"${x:,.2f}"


def parse_date(s):
    return date.fromisoformat(str(s)[:10])


def norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def ordinal(n):
    n = int(round(n))
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def load_yaml(name):
    return yaml.safe_load((HERE / name).read_text())


# --------------------------------------------------------------------------- pull


def pull(year, data_dir):
    """Dump every source for `year` into data_dir. Each dump is independent; a failure
    in one is reported and the others still land."""
    start, end = f"{year}-01-01", min(date(year, 12, 31), date.today()).isoformat()
    data_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, FINANCE_TOOLS_DATA=str(DATA_DIR))
    failures = []

    def step(label, fn):
        print(f"-- {label}", flush=True)
        try:
            fn()
        except SystemExit as e:
            failures.append(f"{label}: {e}")
            print(f"   FAILED: {e}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            failures.append(f"{label}: {e}")
            print(f"   FAILED: {e}", file=sys.stderr)

    def monarch():
        sys.path.insert(0, str(HERE))
        import monarch_client as mc

        async def go():
            mm = await mc.get_monarch_client()
            if mm is None:
                raise SystemExit("no Monarch credentials; run: task finance:balances:login")
            out, offset = [], 0
            while True:
                d = await mm.get_transactions(limit=100, offset=offset, start_date=start, end_date=end)
                rows = d["allTransactions"]["results"]
                if not rows:
                    break
                out.extend(rows)
                offset += len(rows)
                if len(rows) < 100:
                    break
            (data_dir / "monarch_transactions.json").write_text(json.dumps(out, indent=1, default=str))
            print(f"   {len(out)} transactions")
            accts = (await mm.get_accounts())["accounts"]
            slim = [{"id": a["id"], "name": a["displayName"], "type": a["type"]["name"], "balance": a.get("currentBalance"),
                     "institution": (a.get("institution") or {}).get("name"), "deactivatedAt": a.get("deactivatedAt"),
                     "updatedAt": a.get("updatedAt"), "hidden": a.get("isHidden")} for a in accts]
            (data_dir / "monarch_accounts.json").write_text(json.dumps(slim, indent=1, default=str))
            hist = {}
            for a in accts:
                if a["type"]["name"] == "loan":
                    h = await mm.get_account_history(a["id"])
                    hist[a["id"]] = {"name": a["displayName"],
                                     "history": [x for x in h if str(x.get("date", "")) >= f"{year - 1}-12-01"]}
            (data_dir / "monarch_loan_history.json").write_text(json.dumps(hist, indent=1, default=str))
            print(f"   {len(slim)} accounts, {len(hist)} loan histories")
        asyncio.run(go())

    def zoho():
        sys.path.insert(0, str(HERE))
        import zoho_books_recon as zr
        z = zr.Zoho()
        banks = z.get("/bankaccounts").get("bankaccounts", [])
        (data_dir / "zoho_banks.json").write_text(json.dumps(banks, indent=1))
        exp = list(z.pages("/expenses", "expenses", date_start=start, date_end=end))
        (data_dir / "zoho_expenses.json").write_text(json.dumps(exp, indent=1))
        bt = []
        for b in banks:
            rows = list(z.pages("/banktransactions", "banktransactions", account_id=b["account_id"],
                                date_start=start, date_end=end, filter_by="Status.All"))
            for r in rows:
                r["_bank"] = b["account_name"]
            bt.extend(rows)
        (data_dir / "zoho_banktxns.json").write_text(json.dumps(bt, indent=1))
        print(f"   {len(banks)} bank accounts, {len(exp)} expenses, {len(bt)} bank lines, {z.calls} API calls")

    def mercury():
        sys.path.insert(0, str(HERE))
        import mercury_classify as m
        cfg = load_yaml("merchants.yaml")
        out = []
        for a in cfg["accounts"]:
            rows = m.fetch_mercury(a["mercury_account_id"], start, end)
            for r in rows:
                r["_bank"] = a["bank_account"]
            out.extend(rows)
        (data_dir / "mercury_transactions.json").write_text(json.dumps(out, indent=1))
        print(f"   {len(out)} transactions")

    def toast():
        src = DATA_DIR / f".toast_preview_{year}.json"
        if not src.exists():
            raise SystemExit(f"{src} missing: run task finance:zoho:toast-dump first")
        (data_dir / "toast_preview.json").write_text(src.read_text())
        print(f"   copied {src.name}")

    def sheet():
        sys.path.insert(0, str(HERE))
        import requests
        import zoho_sheet as zs
        r = requests.post(f"{zs.SHEET_BASE}/{BILLS_SHEET_ID}", data={"method": "worksheet.list"},
                          headers=zs._headers(), timeout=30)
        out = {}
        for s in r.json().get("worksheet_names", []):
            out[s["worksheet_name"]] = zs.records(BILLS_SHEET_ID, s["worksheet_name"])
        (data_dir / "bills_sheet.json").write_text(json.dumps(out, indent=1))
        print(f"   tabs: {', '.join(out)}")

    def sync():
        r = subprocess.run([sys.executable, str(HERE / "sync_status.py"), "--json", "--fresh"], env=env,
                           capture_output=True, text=True, timeout=600)
        if r.returncode not in (0, 1):
            raise RuntimeError(r.stderr[-400:])
        (data_dir / "sync_status.json").write_text(r.stdout)
        print("   saved")

    step("Monarch transactions, accounts, loan histories", monarch)
    step("Zoho Books expenses and bank lines", zoho)
    step("Mercury business accounts", mercury)
    step("Toast payout preview (merchant fees)", toast)
    step("Bills sheet (APRs, due days)", sheet)
    step("sync-status", sync)
    (data_dir / "pulled_at.txt").write_text(datetime.now().isoformat())
    if failures:
        print("\nPull finished with failures:\n  " + "\n  ".join(failures), file=sys.stderr)
    return not failures


# --------------------------------------------------------------------------- load + registry


class Registry:
    """payments.yaml + debts.yaml: account names as Monarch/Zoho show them -> registry ids,
    which card each payment pays and from where, which cards are paid in full."""

    def __init__(self):
        self.cfg = load_yaml("payments.yaml")
        self.debts = load_yaml("debts.yaml")
        self.accounts = {a["id"]: a for a in self.cfg["accounts"]}
        self.by_monarch = {a["monarch_match"]: a["id"] for a in self.cfg["accounts"] if a.get("monarch_match")}
        self.by_zoho = {a["zoho_match"]: a["id"] for a in self.cfg["accounts"] if a.get("zoho_match")}
        self.pays = {}
        for p in self.cfg.get("payments", []):
            if p.get("pays_account"):
                self.pays[p["pays_account"]] = p
        self.apr = {d["account"]: d.get("apr") for d in self.debts.get("debts", []) if d.get("apr")}

    def id_for_monarch(self, display):
        if display in self.by_monarch:
            return self.by_monarch[display]
        for k, v in self.by_monarch.items():         # Monarch sometimes renames; match on last4
            if last4(k) and last4(k) == last4(display):
                return v
        return None

    def id_for_zoho(self, name):
        if name in self.by_zoho:
            return self.by_zoho[name]
        for k, v in self.by_zoho.items():
            if last4(k) and last4(k) == last4(name):
                return v
        return None

    def label(self, acct_id, fallback):
        a = self.accounts.get(acct_id)
        return f'{a["name"]} (...{a["last4"]})' if a and a.get("last4") else (a["name"] if a else fallback)

    def funding(self, acct_id):
        p = self.pays.get(acct_id)
        if not p:
            return None, None
        f = self.accounts.get(p["funding_account"], {})
        return f.get("name", p["funding_account"]), p.get("autopay_type")


def last4(s):
    m = re.findall(r"(\d{4})(?!.*\d{4})", s or "")
    return m[-1] if m else None


def load_dumps(data_dir):
    def j(name, default):
        p = data_dir / name
        return json.loads(p.read_text()) if p.exists() else default
    return {
        "monarch": j("monarch_transactions.json", []),
        "monarch_accounts": j("monarch_accounts.json", []),
        "loan_history": j("monarch_loan_history.json", {}),
        "zoho_expenses": j("zoho_expenses.json", []),
        "zoho_banktxns": j("zoho_banktxns.json", []),
        "zoho_banks": j("zoho_banks.json", []),
        "mercury": j("mercury_transactions.json", []),
        "toast": j("toast_preview.json", {}),
        "sheet": j("bills_sheet.json", {}),
        "sync": j("sync_status.json", {}),
        "pulled_at": (data_dir / "pulled_at.txt").read_text().strip() if (data_dir / "pulled_at.txt").exists() else None,
    }


# --------------------------------------------------------------------------- fee rows


def classify(text):
    for kind, pat in FEE_KINDS:
        if re.search(pat, text, re.I):
            return kind
    return None


def fee_rows(d, reg, year):
    """One row per fee or interest charge, both sides. amount is the cost (positive)."""
    rows = []
    # ---- personal: Monarch
    for t in d["monarch"]:
        if not t["date"].startswith(str(year)) or t.get("hideFromReports"):
            continue
        cat = (t.get("category") or {}).get("name", "")
        merchant = (t.get("merchant") or {}).get("name", "") or ""
        text = f"{merchant} | {t.get('plaidName') or ''}"
        amt = t["amount"]
        if NOT_A_FEE.search(text) or amt >= 0:
            continue
        kind = classify(text)
        if cat == MONARCH_INTEREST_CATEGORY and kind is None:
            kind = "interest"
        if kind is None and cat in MONARCH_FEE_CATEGORIES:
            kind = "other_fee"
        if kind is None:
            continue
        display = t["account"]["displayName"]
        rid = reg.id_for_monarch(display)
        rows.append({"side": "personal", "source": "Monarch", "date": t["date"], "amount": -amt, "kind": kind,
                     "merchant": merchant or t.get("plaidName") or "", "desc": t.get("plaidName") or merchant,
                     "account_id": rid, "account": reg.label(rid, display), "category": cat,
                     "miscategorized": cat not in MONARCH_FEE_CATEGORIES and cat != MONARCH_INTEREST_CATEGORY})
    # ---- business: Zoho expenses
    for e in d["zoho_expenses"]:
        if not e["date"].startswith(str(year)):
            continue
        text = f"{e.get('vendor_name') or ''} | {e.get('description') or ''} | {e.get('account_name') or ''}"
        kind = classify(text) if (e["account_name"] in ZOHO_FEE_ACCOUNTS or ZOHO_FEE_PATTERN.search(text)) else None
        if kind is None and e["account_name"] in ZOHO_FEE_ACCOUNTS:
            kind = "interest" if e["account_name"] == "Credit Card Charges" else "other_fee"
        if kind is None or e["account_name"] == "Licenses and Fees" and kind != "merchant_fee":
            continue
        bank = e.get("paid_through_account_name") or ""
        rid = reg.id_for_zoho(bank)
        rows.append({"side": "business", "source": "Zoho Books", "date": e["date"], "amount": float(e["total"]), "kind": kind,
                     "merchant": e.get("vendor_name") or (e.get("description") or "")[:40], "desc": (e.get("description") or "")[:80],
                     "account_id": rid, "account": reg.label(rid, bank), "category": e["account_name"], "miscategorized": False})
    # ---- business: Toast processing fees (inside the payout journals)
    for dep in d["toast"].get("deposits", []):
        if dep.get("payout_status") != "SENT" or not str(dep.get("settle_date", "")).startswith(str(year)):
            continue
        fee = sum(-float(li["amount"]) for li in dep.get("line_items", []) if li.get("code") == TOAST_FEE_CODE)
        if fee <= 0:
            continue
        rid = "mercury-biz-income-4613"
        rows.append({"side": "business", "source": "Toast journal", "date": dep["settle_date"], "amount": round(fee, 2),
                     "kind": "merchant_fee", "merchant": "Toast", "desc": f"card processing on sales {', '.join(dep.get('sales_dates', []))}",
                     "account_id": rid, "account": reg.label(rid, "Operations 4613"), "category": "Commissions & fees (6040)",
                     "miscategorized": False})
    # ---- Mercury: the bank itself (fees are rare; feeId or fee-like text)
    for m in d["mercury"]:
        text = f"{m.get('counterpartyName') or ''} | {m.get('bankDescription') or ''}"
        if m.get("feeId") or re.search(r"mercury fee|wire fee|service fee", text, re.I):
            rid = reg.id_for_zoho(m["_bank"].split(": ")[-1])
            rows.append({"side": "business", "source": "Mercury", "date": m["postedAt"][:10], "amount": -m["amount"], "kind": "other_fee",
                         "merchant": "Mercury", "desc": text[:80], "account_id": rid, "account": m["_bank"], "category": "Mercury fee",
                         "miscategorized": False})
    rows.sort(key=lambda r: (r["date"], r["account"]))
    return rows


# --------------------------------------------------------------------------- recurrence


SAME_EVENT_DAYS = 4      # two charges this close together are one billing event (purchase + cash interest on one statement)


def cadence_of(dates):
    """('monthly'|'weekly'|'biweekly'|'quarterly'|'frequent'|'irregular'|'once', median gap days).
    Gaps of a few days are folded into one event so a statement that posts two lines
    does not read as a daily charge."""
    dates = sorted(set(dates))                   # two lines on one statement are one billing event
    if len(dates) < 2:
        return "once", None
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    real = [g for g in gaps if g > SAME_EVENT_DAYS]
    if len(real) < len(gaps) / 2:                 # mostly back-to-back charges: daily payouts, a card used every few days
        return "frequent", statistics.median(gaps)
    g = statistics.median(real)
    if 5 <= g <= 9:
        return "weekly", g
    if 12 <= g <= 16:
        return "biweekly", g
    if 25 <= g <= 40:
        return "monthly", g
    if 50 <= g <= 75:
        return "every 2 months", g
    if 80 <= g <= 100:
        return "quarterly", g
    if g < 5:
        return "frequent", g
    return "irregular", g


def day_pattern(dates, cadence):
    if cadence in ("monthly", "every 2 months", "quarterly", "irregular"):
        days = sorted(x.day for x in dates)
        med = statistics.median(days)
        lo, hi = days[len(days) // 4], days[(3 * len(days)) // 4]
        return f"{ordinal(med)}" + (f" ({ordinal(lo)}–{ordinal(hi)})" if hi - lo >= 2 else "")
    if cadence in ("weekly", "biweekly"):
        wd = Counter(x.strftime("%A") for x in dates).most_common(1)[0][0]
        return f"{wd}s"
    return "—"


def missed_months(dates, year, today, from_year_start=False):
    """Monthly items: the billing slots (first date + n x ~30 days, up to today) that no
    charge landed near. Assigning each charge to its nearest slot keeps a bill paid on
    Apr 30 for May from reading as a missed May. With from_year_start, months before the
    first charge count too (a loan instalment owed all year)."""
    if not dates:
        return []
    first = dates[0]
    day = int(statistics.median(x.day for x in dates))
    slots = []
    for m in range(1, 13):                                   # one billing slot per month, on the usual day
        last_day = (date(year, m % 12 + 1, 1) - timedelta(days=1)).day if m < 12 else 31
        slots.append(date(year, m, min(day, last_day)))
    hit = set()
    for x in dates:
        near = min(range(len(slots)), key=lambda i: abs((slots[i] - x).days))
        if abs((slots[near] - x).days) <= 12:
            hit.add(near)
    out = []
    for i, s in enumerate(slots):
        if i in hit or s > today - timedelta(days=SAME_EVENT_DAYS):          # a slot still ahead is not missed yet
            continue
        if not from_year_start and s < first - timedelta(days=12):
            continue
        out.append(s.strftime("%b"))
    return out


def summarize_group(items, year, today, from_year_start=False):
    """items: list of (date, amount, account_label). -> dict with cadence, day, amount, total, accounts."""
    items = sorted(items)
    dates = [i[0] for i in items]
    amts = [i[1] for i in items]
    cadence, gap = cadence_of(dates)
    med = statistics.median(amts)
    fixed = max(amts) - min(amts) <= max(1.0, 0.02 * med)
    acct = Counter(i[2] for i in items).most_common()
    stopped = cadence == "monthly" and (today - dates[-1]).days > 45
    return {
        "count": len(items), "first": dates[0].isoformat(), "last": dates[-1].isoformat(),
        "cadence": cadence, "day": day_pattern(dates, cadence), "gap": gap,
        "amount": med, "fixed": fixed, "min": min(amts), "max": max(amts), "total": sum(amts),
        "account": acct[0][0], "accounts": acct, "stopped": dates[-1].strftime("%b %-d") if stopped else None,
        "missed": missed_months(dates, year, min(today, dates[-1] if stopped else today), from_year_start) if cadence == "monthly" else [],
    }


def recurring_personal(d, reg, fee_ids, year, today):
    groups = defaultdict(list)
    meta = {}
    for t in d["monarch"]:
        if not t["date"].startswith(str(year)) or t["id"] in fee_ids or t["amount"] >= 0 or t.get("hideFromReports"):
            continue
        cat = (t.get("category") or {}).get("name", "")
        if cat in ("Transfer", "Balance Adjustments"):
            continue
        merchant = (t.get("merchant") or {}).get("name") or (t.get("plaidName") or "")
        if REVERSAL.search(f"{merchant} {t.get('plaidName') or ''}"):
            continue
        key = (norm(merchant), cat)
        display = t["account"]["displayName"]
        rid = reg.id_for_monarch(display)
        groups[key].append((parse_date(t["date"]), -t["amount"], reg.label(rid, display)))
        meta.setdefault(key, {"merchant": merchant, "category": cat})
    return finish_groups(groups, meta, year, today)


def recurring_business(d, reg, year, today):
    groups = defaultdict(list)
    meta = {}
    for e in d["zoho_expenses"]:
        if not e["date"].startswith(str(year)) or e["account_name"] in ZOHO_FEE_ACCOUNTS:
            continue
        merchant = e.get("vendor_name") or re.split(r"[;|]", e.get("description") or "")[0].strip()[:40] or "(no vendor)"
        key = (norm(merchant), e["account_name"])
        bank = e.get("paid_through_account_name") or ""
        rid = reg.id_for_zoho(bank)
        groups[key].append((parse_date(e["date"]), float(e["total"]), reg.label(rid, bank)))
        meta.setdefault(key, {"merchant": merchant, "category": e["account_name"]})
    card_ids = {b["account_id"]: b["account_name"] for b in d["zoho_banks"] if b.get("account_type") == "credit_card"}
    for b in d["zoho_banktxns"]:
        if not b["date"].startswith(str(year)) or b["transaction_type"] != "transfer_fund":
            continue
        if b.get("debit_or_credit") != "credit" or b.get("account_type") != "credit_card":
            continue
        if REVERSAL.search(b.get("description") or ""):
            continue
        card = b["_bank"]
        key = (f"payment to {norm(card)}", "Card payment")
        bank = b.get("offset_account_name") or ""
        rid = reg.id_for_zoho(bank)
        groups[key].append((parse_date(b["date"]), float(b["amount"]), reg.label(rid, bank)))
        meta.setdefault(key, {"merchant": f"Payment to {card}", "category": "Card payment"})
    return finish_groups(groups, meta, year, today)


LOAN_CATEGORIES = {"Student Loans", "Loan Repayment", "Auto Payment"}


def merge_prefixes(groups, meta):
    """'Perkins' and 'Perkins Loan', 'Colada Sho' and 'Colada Shop': when one merchant key is a
    prefix of another in the same category, fold the longer into the shorter."""
    keys = sorted(groups, key=lambda k: len(k[0]))
    for i, short in enumerate(keys):
        if len(short[0]) < 5 or short not in groups:
            continue
        for long in keys[i + 1:]:
            if long in groups and long[1] == short[1] and long[0].startswith(short[0]):
                groups[short].extend(groups.pop(long))
                meta.pop(long, None)
    return groups


def finish_groups(groups, meta, year, today):
    out = []
    groups = merge_prefixes(groups, meta)
    for key, items in groups.items():
        if len(items) < 3:
            continue
        s = summarize_group(items, year, today, from_year_start=meta[key]["category"] in LOAN_CATEGORIES)
        if s["cadence"] == "irregular" and not s["fixed"]:
            continue
        out.append({**meta[key], **s})
    out.sort(key=lambda g: -g["total"])
    return out


def recurring_fees(rows, year, today):
    """Recurrence of the fee/interest charges themselves, per kind + account."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["kind"], r["account"], r["side"], r["account_id"])].append((parse_date(r["date"]), r["amount"], r["merchant"]))
    out = []
    for (kind, account, side, aid), items in groups.items():
        s = summarize_group(items, year, today)
        s["merchant"] = s.pop("account")          # summarize_group's third field was the merchant here
        s.pop("accounts")
        out.append({**s, "kind": kind, "account": account, "account_id": aid, "side": side})
    out.sort(key=lambda g: (g["side"], -g["total"]))
    return out


# --------------------------------------------------------------------------- missed payments


def missed_payments(d, rows, reg, year, today, recurring_p):
    """Per account: penalty fees, bounced autopays, interest on a paid-in-full card, skipped instalments."""
    events = defaultdict(list)
    for r in rows:
        if r["kind"] in PENALTY_KINDS:
            events[r["account"]].append((r["date"], f"{KIND_LABEL[r['kind']]} {money(r['amount'])}", r["side"]))
    # bounced autopays, personal (Monarch) and business (Zoho)
    for t in d["monarch"]:
        text = f"{(t.get('merchant') or {}).get('name', '')} {t.get('plaidName') or ''}"
        if t["date"].startswith(str(year)) and REVERSAL.search(text) and t["amount"] < 0:
            display = t["account"]["displayName"]
            events[reg.label(reg.id_for_monarch(display), display)].append(
                (t["date"], f"Payment of {money(-t['amount'])} returned unpaid", "personal"))
    for b in d["zoho_banktxns"]:
        if b["date"].startswith(str(year)) and REVERSAL.search(b.get("description") or "") and b.get("account_type") == "credit_card":
            events[reg.label(reg.id_for_zoho(b["_bank"]), b["_bank"])].append(
                (b["date"], f"Payment of {money(float(b['amount']))} returned unpaid", "business"))
    # interest on a card the registry says is paid in full every month
    for r in rows:
        if r["kind"] == "interest" and r["account_id"] and reg.pays.get(r["account_id"], {}).get("autopay_type") == "full":
            events[r["account"]].append((r["date"], f"Interest {money(r['amount'])} on a pay-in-full card", r["side"]))
    # skipped instalments on monthly loan payments
    gaps = []
    for g in recurring_p:
        if g["category"] in ("Student Loans", "Loan Repayment", "Auto Payment") and g["missed"]:
            gaps.append(g)
    out = []
    for account, ev in events.items():
        ev.sort()
        out.append({"account": account, "side": ev[0][2], "count": len(ev), "events": ev})
    out.sort(key=lambda x: (x["side"], -x["count"]))
    return out, gaps


# --------------------------------------------------------------------------- loan interest estimate


def loan_estimates(d, reg, year, today):
    sheet_loans = {r.get("Bill Name"): r for r in d["sheet"].get("Loans", [])}
    hist_by_name = {v["name"]: v["history"] for v in d["loan_history"].values()}
    start = date(year, 1, 1)
    out = []
    for L in LOANS:
        srow = sheet_loans.get(L["sheet"], {})
        apr = srow.get("APR")
        if apr in (None, "", 0) and L["registry"]:
            apr = reg.apr.get(L["registry"])
        apr = float(apr) if apr not in (None, "") else None
        # payments this year = money that left a bank account for this lender. The lender-side
        # rows in Monarch are not used: Truist mirrors each payment as a debit and a credit and
        # reverses some, Lafayette's include the disbursement.
        pays = []
        for t in d["monarch"]:
            if not t["date"].startswith(str(year)) or t["amount"] >= -1:
                continue
            text = f"{(t.get('merchant') or {}).get('name', '')} {t.get('plaidName') or ''}"
            acct = t["account"]["displayName"]
            if L["monarch_account"] and L["monarch_account"] in acct:
                continue
            if not re.search(L["payment"], text, re.I) or (L.get("exclude") and re.search(L["exclude"], text, re.I)):
                continue
            if (t.get("category") or {}).get("name") == "Transfer" and not L.get("transfer_ok"):
                continue
            pays.append((t["date"], -t["amount"], acct))
        paid = sum(p[1] for p in pays)
        n_pay = len(pays)
        # balance path
        hist = None
        for name, h in hist_by_name.items():
            if L["monarch_account"] and L["monarch_account"] in name:
                hist = sorted((parse_date(x["date"]), -float(x["signedBalance"])) for x in h if x.get("signedBalance") is not None)
        method, avg_bal, bal_start, bal_end, from_day, amort = "", None, None, None, start, None
        if hist:
            pts = [p for p in hist if start <= p[0] <= today and p[1] > 0]
            if pts:
                from_day = pts[0][0]
                avg_bal = statistics.mean(p[1] for p in pts)
                bal_start, bal_end = pts[0][1], pts[-1][1]
                method = "Monarch balance history"
                amort = paid - (bal_start - bal_end)          # payments not explained by principal reduction
        if avg_bal is None:
            b = srow.get("Balance")
            if b not in (None, ""):
                avg_bal = float(b)
                method = "Bills sheet balance (no feed)"
        days = (today - from_day).days + 1
        est = round(avg_bal * apr / 100 * days / 365, 2) if (avg_bal and apr) else None
        implied = round(amort / avg_bal / days * 365 * 100, 2) if (amort is not None and avg_bal and amort > 0) else None
        out.append({"name": L["name"], "apr": apr, "avg_balance": avg_bal, "balance_start": bal_start, "balance_end": bal_end,
                    "from": from_day.isoformat(), "days": days, "estimate": est, "method": method, "paid": paid,
                    "payments": n_pay, "amortization_check": amort, "implied_apr": implied,
                    "funding": Counter(p[2] for p in pays).most_common(1)[0][0] if pays else None,
                    "sheet_payment": srow.get("Payment"), "sheet_due": srow.get("Due Date")})
    return out


# --------------------------------------------------------------------------- coverage


def coverage(d, reg, year, today):
    unlinked = [a for a in reg.cfg["accounts"]
                if not a.get("monarch_match") and not a.get("zoho_match") and a.get("status") != "closed" and not a.get("inactive")]
    first_last = defaultdict(list)
    for t in d["monarch"]:
        first_last[t["account"]["displayName"]].append(t["date"])
    gaps = []
    acct_meta = {a["name"]: a for a in d["monarch_accounts"]}
    for name, dates in first_last.items():
        lo, hi = min(dates), max(dates)
        a = acct_meta.get(name, {})
        note = []
        if lo > f"{year}-01-20":
            note.append(f"feed starts {lo}")
        if hi < (today - timedelta(days=45)).isoformat() and not a.get("deactivatedAt"):
            note.append(f"no transactions since {hi}")
        if a.get("deactivatedAt"):
            note.append(f"deactivated {str(a['deactivatedAt'])[:10]}")
        if note:
            gaps.append({"account": name, "note": "; ".join(note), "n": len(dates)})
    silent = [a["name"] for a in d["monarch_accounts"]
              if a["name"] not in first_last and a["type"] in ("credit", "depository", "loan") and not a.get("deactivatedAt") and not a.get("hidden")]
    findings = d["sync"].get("findings", []) if isinstance(d["sync"], dict) else []
    return {"unlinked": unlinked, "gaps": sorted(gaps, key=lambda g: g["account"]), "silent": sorted(set(silent)),
            "sync_findings": findings, "sync_checked_at": d["sync"].get("checked_at") if isinstance(d["sync"], dict) else None}


# --------------------------------------------------------------------------- report


def build(year, data_dir, today=None):
    today = today or min(date.today(), date(year, 12, 31))
    reg = Registry()
    d = load_dumps(data_dir)
    rows = fee_rows(d, reg, year)
    fee_ids = set()   # Monarch ids already counted as fees (kept out of the census)
    for t in d["monarch"]:
        text = f"{(t.get('merchant') or {}).get('name', '')} | {t.get('plaidName') or ''}"
        cat = (t.get("category") or {}).get("name", "")
        if t["amount"] < 0 and not NOT_A_FEE.search(text) and (classify(text) or cat in MONARCH_FEE_CATEGORIES or cat == MONARCH_INTEREST_CATEGORY):
            fee_ids.add(t["id"])
    rec_p = recurring_personal(d, reg, fee_ids, year, today)
    rec_b = recurring_business(d, reg, year, today)
    rec_f = recurring_fees(rows, year, today)
    missed, instalment_gaps = missed_payments(d, rows, reg, year, today, rec_p)
    loans = loan_estimates(d, reg, year, today)
    cov = coverage(d, reg, year, today)

    def tot(side=None, kinds=None):
        return round(sum(r["amount"] for r in rows if (side is None or r["side"] == side) and (kinds is None or r["kind"] in kinds)), 2)
    by_kind = defaultdict(lambda: {"personal": 0.0, "business": 0.0, "n": 0})
    for r in rows:
        by_kind[r["kind"]][r["side"]] += r["amount"]
        by_kind[r["kind"]]["n"] += 1
    by_account = defaultdict(lambda: {"interest": 0.0, "fees": 0.0, "n": 0, "side": "", "id": None})
    for r in rows:
        b = by_account[r["account"]]
        b["side"], b["id"] = r["side"], r["account_id"]
        b["n"] += 1
        b["interest" if r["kind"] == "interest" else "fees"] += r["amount"]
    summary = {
        "year": year, "through": today.isoformat(), "pulled_at": d["pulled_at"],
        "personal": {"interest": tot("personal", {"interest"}), "fees": tot("personal") - tot("personal", {"interest"}), "total": tot("personal")},
        "business": {"interest": tot("business", {"interest"}), "fees": tot("business") - tot("business", {"interest"}),
                     "merchant_fees": tot("business", {"merchant_fee"}), "total": tot("business")},
        "combined": tot(), "combined_ex_merchant": round(tot() - tot(None, {"merchant_fee"}), 2),
        "loan_interest_estimate": round(sum(l["estimate"] or 0 for l in loans), 2),
        "by_kind": {k: {kk: round(vv, 2) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in by_kind.items()},
        "by_account": {k: {kk: round(vv, 2) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in by_account.items()},
        "miscategorized_in_monarch": sum(1 for r in rows if r.get("miscategorized")),
        "missed": missed, "instalment_gaps": [{k: g[k] for k in ("merchant", "category", "missed", "day", "account")} for g in instalment_gaps],
        "loans": loans, "coverage": {k: v for k, v in cov.items() if k != "unlinked"},
        "unlinked": [a["id"] for a in cov["unlinked"]],
        "rows": len(rows), "recurring_personal": len(rec_p), "recurring_business": len(rec_b),
    }
    return {"summary": summary, "rows": rows, "rec_p": rec_p, "rec_b": rec_b, "rec_f": rec_f, "missed": missed,
            "instalment_gaps": instalment_gaps, "loans": loans, "cov": cov, "reg": reg, "today": today, "year": year}


# --------------------------------------------------------------------------- HTML

CSS = """
<style>
/* Layout: one column of ledger-style sections; summary tiles first, then tables that scroll inside their own frame. */
:root{
  --bg:#f7f6f2; --panel:#ffffff; --ink:#1d2229; --muted:#5f6670; --rule:#d9d6cc; --rule-soft:#ebe8df;
  --accent:#0f5f8a; --accent-soft:#e3eef5; --bad:#9f2f2f; --bad-soft:#f6e4e4; --warn:#8a5a0f; --warn-soft:#f7ecd8; --ok:#2f6f3f;
  --display:"Fraunces",Georgia,"Times New Roman",serif; --body:"Source Sans 3","Segoe UI",system-ui,sans-serif; --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  --bg:#15181c; --panel:#1d2127; --ink:#e8e6df; --muted:#a2a7ae; --rule:#394049; --rule-soft:#2a3038;
  --accent:#6fb3dc; --accent-soft:#1f3341; --bad:#e08484; --bad-soft:#3a2323; --warn:#dcb264; --warn-soft:#3a3020; --ok:#7cc48e; color-scheme:dark } }
:root[data-theme="dark"]{
  --bg:#15181c; --panel:#1d2127; --ink:#e8e6df; --muted:#a2a7ae; --rule:#394049; --rule-soft:#2a3038;
  --accent:#6fb3dc; --accent-soft:#1f3341; --bad:#e08484; --bad-soft:#3a2323; --warn:#dcb264; --warn-soft:#3a3020; --ok:#7cc48e; color-scheme:dark }
body{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:16px;line-height:1.45;margin:0}
.wrap{max-width:1180px;margin:0 auto;padding-block:28px 64px;padding-inline:16px}
h1{font-family:var(--display);font-weight:600;font-size:clamp(28px,4vw,40px);line-height:1.1;margin:0 0 6px;text-wrap:balance}
h2{font-family:var(--display);font-weight:600;font-size:24px;margin:44px 0 10px;text-wrap:balance;border-bottom:2px solid var(--rule);padding-bottom:6px}
h3{font-size:16px;font-weight:600;margin:22px 0 8px;letter-spacing:.02em;text-transform:uppercase;color:var(--muted)}
p{max-width:70ch;margin:6px 0}
.lede{color:var(--muted);font-size:15px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:22px 0 8px}
.tile{background:var(--panel);border:1px solid var(--rule);padding:14px 16px;min-width:0}
.tile .k{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.tile .v{font-family:var(--display);font-size:30px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:4px}
.tile .s{font-size:13px;color:var(--muted);margin-top:2px}
.tile.hot{border-color:var(--bad);background:var(--bad-soft)}
.tile.est{border-style:dashed}
.scroll{overflow-x:auto;border:1px solid var(--rule);background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:14.5px}
th,td{padding:7px 10px;text-align:left;vertical-align:top;border-bottom:1px solid var(--rule-soft)}
th{font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);background:var(--panel);position:sticky;top:0}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.d{white-space:nowrap;font-family:var(--mono);font-size:13px}
tr.total td{font-weight:700;border-top:2px solid var(--rule)}
tr.sub td{color:var(--muted)}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;letter-spacing:.02em;white-space:nowrap}
.pill.bad{background:var(--bad-soft);color:var(--bad)}
.pill.warn{background:var(--warn-soft);color:var(--warn)}
.pill.ok{color:var(--ok);border:1px solid var(--ok)}
.pill.side{background:var(--accent-soft);color:var(--accent)}
.muted{color:var(--muted)}
.mono{font-family:var(--mono);font-size:13px}
details{margin:10px 0}
summary{cursor:pointer;color:var(--accent);font-weight:600}
ul{padding-left:20px;margin:6px 0}
li{margin:3px 0}
code{font-family:var(--mono);font-size:13px;background:var(--accent-soft);padding:1px 5px}
.note{border-left:3px solid var(--accent);padding:6px 12px;background:var(--panel);margin:12px 0;max-width:80ch}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px}
.two > *{min-width:0}
@media (prefers-reduced-motion: reduce){ *{scroll-behavior:auto} }
</style>
"""

FONTS = '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=Source+Sans+3:wght@400;600;700&family=IBM+Plex+Mono:wght@400&display=swap">'


def h(x):
    return escape(str(x)) if x is not None else ""


def cadence_label(g):
    c = g["cadence"]
    if g["count"] == 1:
        return "once"
    if c == "frequent":
        return "several a month"
    if c == "irregular":
        return f"irregular (~{int(g['gap'])}d apart)" if g.get("gap") else "irregular"
    return c


def render(b):
    s, rows, reg, year, today = b["summary"], b["rows"], b["reg"], b["year"], b["today"]
    out = []
    w = out.append
    w(f"<title>Fees &amp; Interest {year}</title>")
    w(FONTS)
    w(CSS)
    w('<div class="wrap">')
    w(f"<h1>Interest, fees and missed payments, {year}</h1>")
    w(f'<p class="lede">January 1 through {today.strftime("%B %-d, %Y")}. Personal side from Monarch, business side from Zoho Books, '
      f'Mercury and the Toast payout journals. Pulled {h((s["pulled_at"] or "")[:16].replace("T", " "))}.</p>')

    # ---- tiles
    w('<div class="tiles">')
    w(f'<div class="tile hot"><div class="k">Personal · interest + fees</div><div class="v">{money(s["personal"]["total"])}</div>'
      f'<div class="s">interest {money(s["personal"]["interest"])} · fees {money(s["personal"]["fees"])}</div></div>')
    w(f'<div class="tile hot"><div class="k">Business · interest + fees</div><div class="v">{money(s["business"]["total"])}</div>'
      f'<div class="s">interest {money(s["business"]["interest"])} · bank/card fees {money(s["business"]["fees"] - s["business"]["merchant_fees"])} · merchant fees {money(s["business"]["merchant_fees"])}</div></div>')
    w(f'<div class="tile"><div class="k">Combined</div><div class="v">{money(s["combined"])}</div>'
      f'<div class="s">{money(s["combined_ex_merchant"])} without Toast processing fees</div></div>')
    w(f'<div class="tile est"><div class="k">Loan interest, estimated</div><div class="v">{money(s["loan_interest_estimate"])}</div>'
      f'<div class="s">inside instalments, not booked as a line; see section 5</div></div>')
    w('</div>')
    w(f'<p class="muted">{s["rows"]} charges found. {s["miscategorized_in_monarch"]} of the personal ones sit in a non-fee Monarch category '
      f'(mostly Bank of America interest filed as "Credit Card Payment") and were found by text, not category.</p>')

    # ---- 1. by kind
    w("<h2>1. What was paid, by kind</h2>")
    w('<div class="scroll"><table><thead><tr><th>Kind</th><th class="n">Personal</th><th class="n">Business</th><th class="n">Total</th><th class="n">Charges</th></tr></thead><tbody>')
    order = ["interest", "late_fee", "returned_payment_fee", "overdraft_fee", "cash_advance_fee", "maintenance_fee", "annual_fee", "foreign_fee", "merchant_fee", "other_fee"]
    for k in order:
        v = s["by_kind"].get(k)
        if not v:
            continue
        w(f'<tr><td>{KIND_LABEL[k]}</td><td class="n">{money(v["personal"])}</td><td class="n">{money(v["business"])}</td>'
          f'<td class="n">{money(v["personal"] + v["business"])}</td><td class="n">{v["n"]}</td></tr>')
    w(f'<tr class="total"><td>Total</td><td class="n">{money(s["personal"]["total"])}</td><td class="n">{money(s["business"]["total"])}</td>'
      f'<td class="n">{money(s["combined"])}</td><td class="n">{s["rows"]}</td></tr>')
    w('</tbody></table></div>')
    pen = sum(v["personal"] + v["business"] for k, v in s["by_kind"].items() if k in PENALTY_KINDS)
    w(f'<p>Late, returned-payment and overdraft fees are the avoidable part: <strong>{money(pen)}</strong> this year. '
      f'They come only from a payment that bounced or arrived late, and section 3 lists each one.</p>')

    # ---- 2. by account with recurrence
    w("<h2>2. Where it comes from, by account</h2>")
    w('<p>Each line is one account and one kind of charge. "Typically" is the median day of month over the year, with the middle half of the dates in parentheses when they spread. '
      'For a card, the funding column is the account the registry says pays that card.</p>')
    for side in ("personal", "business"):
        w(f"<h3>{side}</h3>")
        w('<div class="scroll"><table><thead><tr><th>Account</th><th>Charge</th><th class="n">Total</th><th class="n">Times</th><th>Cadence</th><th>Typically</th><th class="n">Typical amt</th><th>Paid from</th></tr></thead><tbody>')
        for g in b["rec_f"]:
            if g["side"] != side:
                continue
            aid = g["account_id"]
            fund, ap = reg.funding(aid) if aid else (None, None)
            if aid and reg.accounts.get(aid, {}).get("type") == "depository":
                fund = "the account itself"
            amt = money(g["amount"]) if g["fixed"] else f'{money(g["min"])}–{money(g["max"])}'
            cad = cadence_label(g)
            day = "every payout" if g["kind"] == "merchant_fee" else (h(g["day"]) if g["count"] > 1 else h(g["first"]))
            w(f'<tr><td>{h(g["account"])}</td><td>{KIND_LABEL[g["kind"]]}</td><td class="n">{money(g["total"])}</td><td class="n">{g["count"]}</td>'
              f'<td>{cad}</td><td>{day}</td><td class="n">{amt}</td>'
              f'<td>{h(fund or "—")}{(" · autopay " + ap) if ap else ""}</td></tr>')
        w('</tbody></table></div>')

    # ---- 3. missed payments
    w("<h2>3. Missed and bounced payments</h2>")
    w('<p>An account is listed when a late fee, returned-payment fee or overdraft fee was charged, when a payment was returned unpaid, '
      'or when interest posted on a card the registry marks as paid in full. Dates are posting dates.</p>')
    if not b["missed"]:
        w("<p>None found.</p>")
    for side in ("personal", "business"):
        items = [m for m in b["missed"] if m["side"] == side]
        if not items:
            continue
        w(f"<h3>{side}</h3>")
        w('<div class="scroll"><table><thead><tr><th>Account</th><th class="n">Events</th><th>What happened</th></tr></thead><tbody>')
        for m in items:
            ev = "<br>".join(f'<span class="mono">{e[0]}</span> {h(e[1])}' for e in m["events"])
            w(f'<tr><td>{h(m["account"])}</td><td class="n">{m["count"]}</td><td>{ev}</td></tr>')
        w('</tbody></table></div>')
    if b["instalment_gaps"]:
        w("<h3>Loan instalments with skipped months</h3>")
        w('<p>Monthly loan payments seen in Monarch, and the months between the first payment and now with no payment. A later catch-up payment does not remove the gap.</p>')
        w('<div class="scroll"><table><thead><tr><th>Payment</th><th>Usually</th><th class="n">Amount</th><th>From</th><th>Months with no payment</th></tr></thead><tbody>')
        for g in b["instalment_gaps"]:
            w(f'<tr><td>{h(g["merchant"])}</td><td>{h(g["day"])}</td><td class="n">{money(g["amount"])}</td><td>{h(g["account"])}</td>'
              f'<td><span class="pill warn">{", ".join(g["missed"])}</span></td></tr>')
        w('</tbody></table></div>')

    # ---- 4. every charge
    w("<h2>4. Every charge</h2>")
    w(f'<details><summary>Show all {len(rows)} lines</summary><div class="scroll"><table><thead><tr><th>Date</th><th>Side</th><th>Account</th><th>Kind</th><th class="n">Amount</th><th>Description</th><th>Booked as</th></tr></thead><tbody>')
    for r in rows:
        flag = ' <span class="pill warn">miscategorized</span>' if r.get("miscategorized") else ""
        w(f'<tr><td class="d">{r["date"]}</td><td><span class="pill side">{r["side"]}</span></td><td>{h(r["account"])}</td><td>{KIND_LABEL[r["kind"]]}</td>'
          f'<td class="n">{money(r["amount"])}</td><td>{h(r["desc"])}</td><td class="muted">{h(r["category"])}{flag}</td></tr>')
    w('</tbody></table></div></details>')

    # ---- 5. loan estimates
    w("<h2>5. Interest inside loan instalments (estimated)</h2>")
    w('<p>These lenders do not post interest as its own line, so the figure is average balance × APR × the fraction of the year elapsed. '
      'APRs come from the Bills sheet, Loans tab. Where Monarch has a balance history, the amortization check is payments made minus principal reduction; '
      'it should land near the estimate, and a large gap means the APR on the sheet is wrong or the balance feed jumped.</p>')
    w('<div class="scroll"><table><thead><tr><th>Loan</th><th class="n">Sheet APR</th><th class="n">Avg balance</th><th>Since</th><th class="n">Est. interest</th><th class="n">Paid this year</th><th class="n">Amortization check</th><th class="n">Implied APR</th><th>Basis</th><th>Paid from</th></tr></thead><tbody>')
    for L in b["loans"]:
        chk = money(L["amortization_check"]) if L["amortization_check"] is not None else "—"
        imp = f'{L["implied_apr"]}%' if L.get("implied_apr") else "—"
        w(f'<tr><td>{h(L["name"])}</td><td class="n">{L["apr"] if L["apr"] is not None else "?"}%</td><td class="n">{money(L["avg_balance"]) if L["avg_balance"] else "?"}</td>'
          f'<td class="d">{L["from"]}</td><td class="n">{money(L["estimate"]) if L["estimate"] is not None else "?"}</td><td class="n">{money(L["paid"])} ({L["payments"]}×)</td>'
          f'<td class="n">{chk}</td><td class="n">{imp}</td><td class="muted">{h(L["method"])}</td><td>{h(L["funding"] or "—")}</td></tr>')
    w(f'<tr class="total"><td>Total estimated</td><td></td><td></td><td></td><td class="n">{money(s["loan_interest_estimate"])}</td><td></td><td></td><td></td><td></td><td></td></tr>')
    w('</tbody></table></div>')

    # ---- 6. recurring census
    w("<h2>6. Everything that recurs</h2>")
    w('<p>Every merchant with three or more charges this year at a steady rhythm, fees excluded (they are in section 2). '
      '"Typically" is the median day of month for monthly items or the weekday for weekly ones. "Gaps" are months since the first charge with none, which for a bill means a missed one and for a merchant just means you did not go.</p>')
    for side, groups in (("personal", b["rec_p"]), ("business", b["rec_b"])):
        w(f"<h3>{side} · {len(groups)} recurring</h3>")
        w('<div class="scroll"><table><thead><tr><th>Merchant</th><th>Category</th><th>Cadence</th><th>Typically</th><th class="n">Typical amt</th><th class="n">Times</th><th class="n">Year total</th><th>Comes out of</th><th>Gaps</th></tr></thead><tbody>')
        for g in groups:
            amt = money(g["amount"]) if g["fixed"] else f'{money(g["min"])}–{money(g["max"])}'
            accts = h(g["account"]) + (f' <span class="muted">(+{len(g["accounts"]) - 1} other)</span>' if len(g["accounts"]) > 1 else "")
            gaps = f'<span class="pill warn">{", ".join(g["missed"])}</span>' if g["missed"] else ""
            if g.get("stopped"):
                gaps += f' <span class="muted">stopped after {g["stopped"]}</span>'
            w(f'<tr><td>{h(g["merchant"])}</td><td class="muted">{h(g["category"])}</td><td>{cadence_label(g)}</td><td>{h(g["day"])}</td>'
              f'<td class="n">{amt}</td><td class="n">{g["count"]}</td><td class="n">{money(g["total"])}</td><td>{accts}</td><td>{gaps}</td></tr>')
        w('</tbody></table></div>')

    # ---- 7. coverage
    cov = b["cov"]
    w("<h2>7. What this report cannot see</h2>")
    w('<div class="two"><div>')
    w("<h3>Registry accounts with no Monarch or Zoho link</h3>")
    if cov["unlinked"]:
        w("<ul>" + "".join(f'<li><strong>{h(a["name"])}</strong> <span class="muted">({a["id"]}, {a["category"]}, feed: {a.get("feed") or "none"})</span></li>' for a in cov["unlinked"]) + "</ul>")
    else:
        w("<p>None.</p>")
    w("<h3>Monarch accounts with a partial year</h3>")
    if cov["gaps"]:
        w("<ul>" + "".join(f'<li><strong>{h(g["account"])}</strong> <span class="muted">{h(g["note"])} ({g["n"]} rows)</span></li>' for g in cov["gaps"]) + "</ul>")
    if cov["silent"]:
        w("<h3>Monarch accounts with no transactions at all this year</h3>")
        w("<ul>" + "".join(f"<li>{h(x)}</li>" for x in cov["silent"]) + "</ul>")
    w("</div><div>")
    w(f'<h3>Sync status <span class="muted">({h((cov["sync_checked_at"] or "")[:16].replace("T", " "))})</span></h3>')
    if cov["sync_findings"]:
        w("<ul>" + "".join(f'<li><span class="pill {"bad" if f["severity"] == "error" else "warn"}">{f["severity"]}</span> <strong>{h(f["subject"])}</strong>: {h(f["message"])}</li>' for f in cov["sync_findings"]) + "</ul>")
    else:
        w("<p>No findings.</p>")
    w('<div class="note"><p>Verify the links any time with <code>task finance:sync-status -- --fresh</code> (feed health) and '
      '<code>task finance:accounts:check</code> (registry is one-to-one with Monarch and Zoho Books). Re-run this report with <code>task finance:fees</code>.</p></div>')
    w("</div></div>")
    w("</div>")
    return "\n".join(out)


# --------------------------------------------------------------------------- main


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["pull", "report", "all"], nargs="?", default="all")
    p.add_argument("--year", type=int, default=date.today().year)
    p.add_argument("--data", help="dump directory (default data/fees/<year>)")
    p.add_argument("--out", help="HTML path (default docs/fees-interest-<year>.html)")
    p.add_argument("--json", action="store_true", help="print the summary JSON")
    a = p.parse_args()
    data_dir = Path(a.data) if a.data else HERE / "data" / "fees" / str(a.year)
    ok = True
    if a.cmd in ("pull", "all"):
        ok = pull(a.year, data_dir)
    if a.cmd in ("report", "all"):
        b = build(a.year, data_dir)
        out = Path(a.out) if a.out else HERE / "docs" / f"fees-interest-{a.year}.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(b))
        (data_dir / "summary.json").write_text(json.dumps(b["summary"], indent=1, default=str))
        s = b["summary"]
        print(f"{out}\n  personal {money(s['personal']['total'])}  business {money(s['business']['total'])}  combined {money(s['combined'])}"
              f"  loan interest est. {money(s['loan_interest_estimate'])}  charges {s['rows']}")
        if a.json:
            print(json.dumps(s, indent=1, default=str))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
