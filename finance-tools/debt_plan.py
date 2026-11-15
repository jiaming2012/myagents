#!/usr/bin/env python3
"""
Debt paydown planner: pay debt down aggressively without overdrafting the accounts
that make the payments.

Three questions, in order:
  1. What do I owe?        Live balances (Monarch for personal, Zoho Books for business)
                           for every registry account of type credit/loan, enriched with
                           APR and minimum payment from debts.yaml.
  2. What can I send NOW?  Per funding account: balance - every scheduled debit in the
                           next --days (from payments.yaml, via the forecast engine) -
                           the account's min_balance floor = headroom. Negative headroom
                           means an autopay is already at risk. Headroom is pooled per
                           category (personal money pays personal debt, business pays
                           business) and pointed at the avalanche target.
  3. How fast can I be done? Month-by-month simulation: minimums only vs minimums +
                           --monthly-extra, avalanche (highest APR first; --snowball for
                           smallest balance first), paid-off minimums rolling into the
                           next target. --by YYYY-MM solves for the monthly extra needed.

Headroom is deliberately conservative: income is not modeled, so it is "what is
sitting in the account today beyond what is already spoken for". Money you can
safely send this instant, not a forecast of next month.

Promotional rates (debts.yaml `promos:`) are honored: the card plans at the promo APR
until the promo ends, then at its standard APR, and section 4 says how much more of
your everyday spending to put on it (up to a credit-utilization cap), when to stop, and
what monthly payment clears it before the rate jumps.

APR / minimum come from debts.yaml. A debt missing there is still listed, with an
ASSUMED APR (cards 24.99%, loans 9.99%) so the plan runs; fill the file in to make
the numbers yours. Cards whose registry payment is autopay_type: full are "paid in
full monthly" and left out of the paydown unless debts.yaml marks them carried: true.

    python debt_plan.py                         # debts, headroom, this month's move, min-only outlook
    python debt_plan.py --monthly-extra 600     # plan with $600/month extra
    python debt_plan.py --by 2028-06            # extra needed per month to be debt-free by then
    python debt_plan.py --snowball --json
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).parent
DATA_DIR = Path(os.getenv("FINANCE_TOOLS_DATA", HERE))
DEBTS_FILE = DATA_DIR / "debts.yaml" if (DATA_DIR / "debts.yaml").exists() else HERE / "debts.yaml"

ASSUMED_APR = {"credit": 24.99, "loan": 9.99}
ASSUMED_MIN_PCT = 0.02          # 2% of balance when nothing better is known
ASSUMED_MIN_FLOOR = 25.0
MAX_MONTHS = 600
PROMO_DEFAULT_UTILIZATION = 30.0   # % of the card's limit; see debts.yaml promos for why
PROMO_STOP_MONTHS_BEFORE = 3       # stop adding this many months before the promo ends


# --- inputs ------------------------------------------------------------------


def load_debts_yaml(path=DEBTS_FILE):
    if not Path(path).exists():
        return {"debts": [], "plan": {}}
    d = yaml.safe_load(Path(path).read_text()) or {}
    d.setdefault("debts", [])
    d.setdefault("plan", {})
    return d


def monarch_lookup(monarch_accounts):
    """sync_status slim Monarch accounts -> the {name: {...}} dict the forecast engine reads.

    Same display name can exist twice (dead duplicate next to the live one); keep the
    live one, exactly like coverage_report.fetch_monarch_balances.
    """
    out = {}
    for a in monarch_accounts:
        entry = {"balance": a.get("balance", 0), "type": a.get("type", ""), "hidden": a.get("hidden"),
                 "deactivated": a.get("deactivated"), "updated": a.get("updated")}
        existing = out.get(a["name"])
        parked = bool(entry["hidden"] or entry["deactivated"])
        if existing is None or (not parked and (existing["hidden"] or existing["deactivated"])):
            out[a["name"]] = entry
        elif not parked and not (existing["hidden"] or existing["deactivated"]):
            # two live copies: keep the most recently updated
            if str(entry["updated"] or "") > str(existing["updated"] or ""):
                out[a["name"]] = entry
    return out


def zoho_lookup(zoho_accounts):
    return {a["name"]: a for a in zoho_accounts}


def xero_shaped_from_zoho(config, zoho_by_name):
    """Let the forecast engine see business balances: registry accounts that carry both
    xero_account_id and zoho_match get Zoho's book balance under their Xero key.
    Credit cards are negated to match the Monarch "liability is negative" convention."""
    out = {}
    for a in config.get("accounts", []):
        zm, xid = a.get("zoho_match"), a.get("xero_account_id")
        if zm and xid and zm in zoho_by_name:
            bal = zoho_by_name[zm]["book_balance"]
            if zoho_by_name[zm].get("type") == "credit_card":
                bal = -bal
            out[xid] = {"balance": bal, "type": zoho_by_name[zm].get("type", "")}
    return out


def owed_balance(account, monarch_by_name, zoho_by_name):
    """Positive amount owed on a credit/loan account, with its source. (None, None) if unknown."""
    zm = account.get("zoho_match")
    if zm and zm in zoho_by_name:
        return max(0.0, float(zoho_by_name[zm]["book_balance"])), "zoho"
    mm = account.get("monarch_match")
    if mm and mm in monarch_by_name:
        bal = float(monarch_by_name[mm]["balance"] or 0)
        return (-bal if bal < 0 else 0.0), "monarch"
    last4 = account.get("last4")
    if last4 and monarch_by_name:
        cands = [(n, d) for n, d in monarch_by_name.items() if last4 in n]
        cands.sort(key=lambda nd: (bool(nd[1].get("deactivated")), bool(nd[1].get("hidden"))))
        if cands:
            bal = float(cands[0][1]["balance"] or 0)
            return (-bal if bal < 0 else 0.0), "monarch"
    return None, None


def build_debts(config, debts_cfg, monarch_by_name, zoho_by_name, today=None):
    """Every live liability with APR, minimum, funding account and provenance flags."""
    overrides = {d["account"]: d for d in debts_cfg.get("debts", []) if d.get("account")}
    promos = active_promos(debts_cfg, today)
    pays = {}
    for p in config.get("payments", []):
        if p.get("pays_account"):
            pays.setdefault(p["pays_account"], []).append(p)

    debts, notes = [], []
    for a in config.get("accounts", []):
        if a.get("type") not in ("credit", "loan") or a.get("inactive"):
            continue
        owed, source = owed_balance(a, monarch_by_name, zoho_by_name)
        o = overrides.get(a["id"], {})
        reg = pays.get(a["id"], [])
        paid_in_full = any(p.get("autopay_type") == "full" for p in reg)
        promo = promos.get(a["id"])
        carried = o.get("carried", True if promo else not paid_in_full)
        if owed is None:
            notes.append(f"{a.get('name', a['id'])}: balance unknown (no live source) — skipped")
            continue
        if owed < 0.005:
            continue
        assumed = []
        apr = o.get("apr")
        if apr is None and promo and promo.get("standard_apr") is not None:
            apr = promo["standard_apr"]
        if apr is None:
            apr = ASSUMED_APR.get(a["type"], 19.99)
            assumed.append("apr")
        apr_after, promo_until = None, None
        if promo:
            apr_after, apr, promo_until = float(apr), float(promo["apr"]), _as_date(promo["until"])
        min_payment = o.get("min_payment")
        if min_payment is None:
            amounts = [p.get("amount", 0) for p in reg if p.get("autopay_type") != "full" and p.get("amount")]
            if amounts:
                min_payment = float(max(amounts))
            else:
                min_payment = max(ASSUMED_MIN_FLOOR, round(owed * ASSUMED_MIN_PCT, 2))
                assumed.append("min")
        funding = o.get("funding_account") or next((p.get("funding_account") for p in reg if p.get("funding_account")), None)
        debts.append({
            "id": a["id"], "name": a.get("name", a["id"]), "institution": a.get("institution", ""),
            "type": a["type"], "category": a.get("category", "personal"),
            "balance": round(owed, 2), "source": source, "apr": float(apr),
            "min_payment": float(min(min_payment, owed)), "funding_account": funding,
            "carried": bool(carried), "paid_in_full": paid_in_full and not o.get("carried"),
            "assumed": assumed, "notes": o.get("notes", ""),
            "promo_until": promo_until, "apr_after": apr_after,
        })
    debts.sort(key=lambda d: (-d["apr"], -d["balance"]))
    return debts, notes


# --- headroom ----------------------------------------------------------------


def headroom_by_account(forecast, config):
    """Per depository funding account: what can leave today without breaching the floor."""
    cat = {a["id"]: a.get("category", "personal") for a in config.get("accounts", [])}
    rows = []
    for acct in forecast.get("accounts", []):
        if acct.get("balance_source") in (None, "unknown"):
            rows.append({**_hr_base(acct, cat), "headroom": None, "status": "unknown"})
            continue
        headroom = acct["projected_balance"] - acct["min_balance"]
        status = "ok" if headroom >= 0 else ("overdraft" if acct["projected_balance"] < 0 else "below floor")
        rows.append({**_hr_base(acct, cat), "headroom": round(headroom, 2), "status": status})
    rows.sort(key=lambda r: (r["category"], -(r["headroom"] or -1e12)))
    return rows


def _hr_base(acct, cat):
    return {
        "id": acct["id"], "name": acct["name"], "category": cat.get(acct["id"], "personal"),
        "balance": acct.get("current_balance"), "committed": sum(p["amount"] for p in acct.get("payments", [])),
        "floor": acct.get("min_balance", 0), "projected": acct.get("projected_balance"),
        "stale": acct.get("stale"), "payments": acct.get("payments", []),
    }


def pool_headroom(rows):
    """Safe lump sum per category = sum of positive headroom; also the accounts at risk."""
    pools = {}
    for r in rows:
        p = pools.setdefault(r["category"], {"safe": 0.0, "at_risk": [], "unknown": []})
        if r["headroom"] is None:
            p["unknown"].append(r["name"])
        elif r["headroom"] > 0:
            p["safe"] += r["headroom"]
        else:
            p["at_risk"].append(r)
    for p in pools.values():
        p["safe"] = round(p["safe"], 2)
    return pools


# --- simulation --------------------------------------------------------------


def order_key(strategy):
    if strategy == "snowball":
        return lambda d: (d["balance"], -d["apr"])
    return lambda d: (-d["apr"], d["balance"])


def simulate(debts, monthly_extra=0.0, strategy="avalanche", rollover=True, lump_sum=None, start=None):
    """Month-by-month paydown of the carried debts.

    lump_sum: {category: amount} applied in month 1 to that pool's target.
    Returns {"months", "total_interest", "payoff": {id: month_index}, "schedule": [...]}.
    """
    start = start or date.today().replace(day=1)
    live = [dict(d) for d in debts if d["carried"] and d["balance"] > 0.005]
    for d in live:
        d["_bal"] = d["balance"]
    payoff, schedule, total_interest = {}, [], 0.0
    extra_pool = float(monthly_extra)
    lump = dict(lump_sum or {})
    key = order_key(strategy)

    for month in range(1, MAX_MONTHS + 1):
        open_debts = [d for d in live if d["_bal"] > 0.005]
        if not open_debts:
            break
        paid_this_month = 0.0
        interest_this_month = 0.0
        month_start = _add_months(start, month - 1)
        for d in live:
            if d.get("promo_until") and month_start >= d["promo_until"]:
                d["apr"], d["promo_until"] = d["apr_after"], None   # promo over: standard rate, re-targeted
        for d in open_debts:
            i = d["_bal"] * d["apr"] / 1200.0
            d["_bal"] += i
            interest_this_month += i
            pay = min(d["_bal"], d["min_payment"])
            d["_bal"] -= pay
            paid_this_month += pay
        total_interest += interest_this_month

        extra = extra_pool
        for d in sorted(open_debts, key=key):
            if extra <= 0.005:
                break
            take = min(extra, d["_bal"])
            d["_bal"] -= take
            extra -= take
            paid_this_month += take
        if month == 1 and lump:
            for category, amount in lump.items():
                amount = float(amount or 0)
                for d in sorted((x for x in open_debts if x["category"] == category), key=key):
                    if amount <= 0.005:
                        break
                    take = min(amount, d["_bal"])
                    d["_bal"] -= take
                    amount -= take
                    paid_this_month += take

        for d in open_debts:
            if d["_bal"] <= 0.005 and d["id"] not in payoff:
                payoff[d["id"]] = month
                if rollover:
                    extra_pool += d["min_payment"]
        schedule.append({"month": month, "date": _add_months(start, month - 1).isoformat()[:7],
                         "paid": round(paid_this_month, 2), "interest": round(interest_this_month, 2),
                         "remaining": round(sum(d["_bal"] for d in live), 2)})
    months = max(payoff.values()) if payoff and all(d["_bal"] <= 0.005 for d in live) else None
    return {"months": months, "total_interest": round(total_interest, 2), "payoff": payoff,
            "schedule": schedule, "unfinished": [d["id"] for d in live if d["_bal"] > 0.005]}


def _add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def months_until(target_ym, start=None):
    start = start or date.today().replace(day=1)
    y, m = (int(x) for x in target_ym.split("-")[:2])
    return (y - start.year) * 12 + (m - start.month)


def solve_monthly_extra(debts, target_months, strategy="avalanche", rollover=True, lump_sum=None, start=None):
    """Smallest monthly extra (to the nearest dollar) that clears every carried debt within target_months."""
    if target_months <= 0:
        return None
    lo, hi = 0.0, 1.0
    while hi < 1e7:
        r = simulate(debts, hi, strategy, rollover, lump_sum, start)
        if r["months"] is not None and r["months"] <= target_months:
            break
        hi *= 2
    else:
        return None
    for _ in range(40):
        mid = (lo + hi) / 2
        r = simulate(debts, mid, strategy, rollover, lump_sum, start)
        if r["months"] is not None and r["months"] <= target_months:
            hi = mid
        else:
            lo = mid
    return round(hi + 0.499)


# --- promotional rates ---------------------------------------------------------


def _as_date(v):
    if v is None or isinstance(v, date):
        return v
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()


def active_promos(debts_cfg, today=None):
    """{account id: promo entry} for promos that have not ended yet."""
    today = today or date.today()
    out = {}
    for p in debts_cfg.get("promos") or []:
        until = _as_date(p.get("until"))
        if p.get("account") and p.get("apr") is not None and until and today < until:
            out[p["account"]] = p
    return out


def amortized_payment(balance, apr, months):
    """Level monthly payment that takes balance to zero in `months` at `apr`."""
    if balance <= 0.005:
        return 0.0
    months = max(1, months)
    r = apr / 1200.0
    if r == 0:
        return round(balance / months, 2)
    return round(balance * r / (1 - (1 + r) ** -months), 2)


def _months_between(a, b):
    """Monthly payments left from date a until date b: one per calendar month a..b-1."""
    return (b.year - a.year) * 12 + (b.month - a.month)


def promo_statuses(config, debts_cfg, monarch_by_name, zoho_by_name, debts=None, today=None):
    """One dict per active promo: how much more spending the card can take (up to the
    utilization cap), the phase (fill vs pay off), the payment that clears it before the
    rate jumps, and which debt the freed cash should go to. Pure: nothing is fetched."""
    today = today or date.today()
    accounts = {a["id"]: a for a in config.get("accounts", [])}
    out = []
    for acct_id, p in active_promos(debts_cfg, today).items():
        a = accounts.get(acct_id)
        if not a:
            continue
        owed, _src = owed_balance(a, monarch_by_name, zoho_by_name)
        until = _as_date(p["until"])
        apr = float(p["apr"])
        limit = float(p["credit_limit"]) if p.get("credit_limit") else None
        pct = float(p.get("target_utilization", PROMO_DEFAULT_UTILIZATION))
        stop = _as_date(p.get("stop_adding")) or _add_months(until.replace(day=1), -PROMO_STOP_MONTHS_BEFORE)
        phase = "fill" if today < stop else "payoff"
        cap = round(limit * pct / 100.0, 2) if limit else None
        room = round(max(0.0, cap - (owed or 0.0)), 2) if cap is not None and owed is not None else None
        util = round(100.0 * (owed or 0.0) / limit, 1) if limit and owed is not None else None
        # In the fill phase assume the card reaches the cap by the stop date.
        payoff_from = stop if phase == "fill" else today
        payoff_months = max(1, _months_between(payoff_from, until))
        payoff_balance = cap if phase == "fill" else owed
        if phase == "fill" and cap is None:
            payoff_balance = None             # can't size the payoff without the limit
        payoff_payment = amortized_payment(payoff_balance, apr, payoff_months) if payoff_balance is not None else None
        autopay_full = [x for x in config.get("payments", [])
                        if x.get("pays_account") == acct_id and x.get("autopay_type") == "full"]
        others = sorted((d for d in (debts or []) if d["carried"] and d["id"] != acct_id
                         and d["category"] == a.get("category", "personal") and d["apr"] > apr),
                        key=lambda d: -d["apr"])
        target = others[0] if others else None
        est = None
        if target and room:
            # rough: the shifted dollars cost the promo rate instead of the target's rate
            # from now until the promo ends
            est = round(room * (target["apr"] - apr) / 100.0 * max(0, _months_between(today, until)) / 12.0, 2)
        out.append({
            "account": acct_id, "name": a.get("name", acct_id), "institution": a.get("institution", ""),
            "last4": a.get("last4", ""), "apr": apr, "standard_apr": p.get("standard_apr"),
            "until": until, "days_left": (until - today).days, "activate_by": _as_date(p.get("activate_by")),
            "activated": bool(p.get("activated", True)), "balance": owed, "credit_limit": limit,
            "target_utilization": pct, "cap": cap, "room": room, "utilization": util,
            "stop_adding": stop, "phase": phase, "payoff_months": payoff_months,
            "payoff_balance": round(payoff_balance, 2) if payoff_balance is not None else None,
            "payoff_payment": payoff_payment,
            "autopay_full": autopay_full, "target": target, "est_savings": est,
        })
    return out


def promo_action_line(s):
    """One line for the weekly health action items, or None when nothing is asked."""
    card = f"{s['institution']} {s['name']} {s['last4']}".strip()
    if not s["activated"]:
        return f"PROMO: activate the {s['apr']:.2f}% offer on {card} by {s['activate_by']}"
    if s["autopay_full"]:
        return (f"PROMO: {card} autopay still pays the statement in full — switch it to the minimum payment "
                f"or the {s['apr']:.2f}% promo saves nothing → task finance:debt")
    if s["phase"] == "fill":
        if s["room"] is None:
            return f"PROMO: add credit_limit for {card} to debts.yaml so the spending cap can be computed"
        if s["room"] > 0:
            return (f"PROMO: put everyday spending on {card} — {_m(s['room'])} of room at {s['apr']:.2f}% "
                    f"until {s['stop_adding']:%b %d} → task finance:debt")
        return None
    return (f"PROMO: no new charges on {card}; pay {_m(s['payoff_payment'])}/mo to clear "
            f"{_m(s['balance'])} before {s['until']:%b %d, %Y} ({s['standard_apr']}% after)")


# --- orchestration -----------------------------------------------------------


async def gather(days=30, fresh=False):
    from coverage_report import load_payments_yaml
    from payment_forecast import build_forecast
    from sync_status import collect

    config = load_payments_yaml()
    debts_cfg = load_debts_yaml()
    sync = await collect(fresh=fresh)
    monarch_by_name = monarch_lookup(sync.get("monarch", {}).get("accounts", []) if not sync.get("monarch", {}).get("error") else [])
    zoho_by_name = zoho_lookup(sync.get("zoho", {}).get("accounts", []) if not sync.get("zoho", {}).get("error") else [])
    xero_shaped = xero_shaped_from_zoho(config, zoho_by_name)
    forecast = build_forecast(config, monarch_by_name, xero_shaped, days=days, warn_missing=False)
    debts, notes = build_debts(config, debts_cfg, monarch_by_name, zoho_by_name)
    promos = promo_statuses(config, debts_cfg, monarch_by_name, zoho_by_name, debts)
    rows = headroom_by_account(forecast, config)
    return {"config": config, "debts_cfg": debts_cfg, "sync": sync, "debts": debts, "notes": notes,
            "promos": promos, "headroom": rows, "pools": pool_headroom(rows), "days": days}


def plan(data, monthly_extra=None, strategy="avalanche", rollover=True, by=None, use_headroom=True):
    debts = data["debts"]
    plan_cfg = data["debts_cfg"].get("plan", {})
    if monthly_extra is None:
        monthly_extra = float(plan_cfg.get("monthly_extra", 0) or 0)
    strategy = strategy or plan_cfg.get("strategy", "avalanche")
    lump = {c: p["safe"] for c, p in data["pools"].items()} if use_headroom else None
    key = order_key(strategy)
    targets = {}
    for cat in sorted({d["category"] for d in debts if d["carried"]}):
        cands = sorted((d for d in debts if d["carried"] and d["category"] == cat), key=key)
        if cands:
            targets[cat] = cands[0]
    baseline = simulate(debts, 0.0, strategy, rollover, None)
    with_plan = simulate(debts, monthly_extra, strategy, rollover, lump)
    needed = None
    if by:
        needed = solve_monthly_extra(debts, months_until(by), strategy, rollover, lump)
    return {"strategy": strategy, "monthly_extra": monthly_extra, "lump": lump or {}, "targets": targets, "rollover": rollover,
            "baseline": baseline, "with_plan": with_plan, "by": by, "needed_for_by": needed,
            "interest_saved": round(baseline["total_interest"] - with_plan["total_interest"], 2)}


# --- output ------------------------------------------------------------------


class C:
    RED, YEL, GRN, DIM, BOLD, RESET = "\033[91m", "\033[93m", "\033[92m", "\033[2m", "\033[1m", "\033[0m"

    @classmethod
    def off(cls):
        for k in ("RED", "YEL", "GRN", "DIM", "BOLD", "RESET"):
            setattr(cls, k, "")


def _m(x):
    return f"${x:,.2f}" if x is not None else "?"


def _when(months, start=None):
    if months is None:
        return "never (payments do not cover interest)"
    d = _add_months(start or date.today().replace(day=1), months - 1)
    yrs, mo = divmod(months, 12)
    span = f"{yrs}y {mo}m" if yrs else f"{mo}m"
    return f"{d.strftime('%b %Y')} ({span})"


def print_report(data, result):
    debts, pools, rows = data["debts"], data["pools"], data["headroom"]
    acct_name = {a["id"]: a.get("name", a["id"]) for a in data["config"].get("accounts", [])}
    print(f"\n{C.BOLD}DEBT PAYDOWN PLAN{C.RESET}  {C.DIM}{datetime.now().strftime('%b %d, %Y')} · "
          f"{result['strategy']} · headroom horizon {data['days']}d{C.RESET}")

    # 1. debts
    carried = [d for d in debts if d["carried"]]
    full = [d for d in debts if not d["carried"]]
    print(f"\n{C.BOLD}1. What you owe{C.RESET} — {len(carried)} carried, {_m(sum(d['balance'] for d in carried))} total")
    print(f"  {'debt':<36} {'cat':<8} {'balance':>11} {'APR':>7} {'min/mo':>9} {'funded from':<26} alone")
    for d in carried:
        solo = simulate([d], 0.0)
        flag = f" {C.YEL}(assumed {', '.join(d['assumed'])}){C.RESET}" if d["assumed"] else ""
        print(f"  {d['name'][:36]:<36} {d['category']:<8} {_m(d['balance']):>11} {d['apr']:>6.2f}% {_m(d['min_payment']):>9} "
              f"{acct_name.get(d['funding_account'], d['funding_account'] or '—')[:26]:<26} {_when(solo['months'])}{flag}")
    if full:
        print(f"  {C.DIM}paid in full monthly (not in the plan): "
              + ", ".join(f"{d['name']} {_m(d['balance'])}" for d in full) + C.RESET)
    for n in data["notes"]:
        print(f"  {C.DIM}{n}{C.RESET}")

    # 2. headroom
    print(f"\n{C.BOLD}2. What can leave each funding account today{C.RESET} "
          f"{C.DIM}(balance − debits due in {data['days']}d − floor){C.RESET}")
    print(f"  {'account':<36} {'cat':<8} {'balance':>11} {'committed':>11} {'floor':>8} {'headroom':>11}  status")
    for r in rows:
        tone = C.GRN if r["status"] == "ok" else (C.DIM if r["status"] == "unknown" else C.RED)
        stale = f" {C.YEL}stale balance{C.RESET}" if r.get("stale") else ""
        print(f"  {r['name'][:36]:<36} {r['category']:<8} {_m(r['balance']):>11} {_m(r['committed']):>11} "
              f"{_m(r['floor']):>8} {_m(r['headroom']):>11}  {tone}{r['status']}{C.RESET}{stale}")
    for cat, p in pools.items():
        for r in p["at_risk"]:
            print(f"  {C.RED}✗ {r['name']}: projected {_m(r['projected'])} vs floor {_m(r['floor'])} — "
                  f"top up {_m(-r['headroom'])} before: "
                  + ", ".join(f"{x['name']} {_m(x['amount'])} {x['due_date'].strftime('%m/%d') if hasattr(x['due_date'], 'strftime') else x['due_date']}"
                              for x in sorted(r["payments"], key=lambda x: str(x["due_date"]))[:4]) + C.RESET)

    # 3. this month's move
    print(f"\n{C.BOLD}3. This month's move{C.RESET}")
    for cat in sorted(set(list(pools) + list(result["targets"]))):
        p = pools.get(cat, {"safe": 0.0, "at_risk": [], "unknown": []})
        t = result["targets"].get(cat)
        if not t:
            print(f"  {cat:<8} no carried debt")
            continue
        if p["safe"] > 0:
            print(f"  {cat:<8} {C.GRN}send {_m(p['safe'])}{C.RESET} to {C.BOLD}{t['name']}{C.RESET} "
                  f"({t['apr']:.2f}% APR) on top of its {_m(t['min_payment'])} minimum")
        else:
            print(f"  {cat:<8} {C.YEL}nothing extra is safe{C.RESET}; target stays {t['name']} ({t['apr']:.2f}%)")
        if p["at_risk"]:
            print(f"  {'':<8} {C.RED}first fix the at-risk account(s) above{C.RESET}")
        if p["unknown"]:
            print(f"  {'':<8} {C.DIM}no balance for: {', '.join(p['unknown'])}{C.RESET}")

    # 4. promotional rates
    if data.get("promos"):
        print_promos(data["promos"], acct_name)

    # 5. outlook (4 when there is no promo)
    b, w = result["baseline"], result["with_plan"]
    print(f"\n{C.BOLD}{5 if data.get('promos') else 4}. Outlook{C.RESET}")
    roll = "paid-off minimums roll forward" if result.get("rollover", True) else "no rollover"
    print(f"  minimums only ({roll}): debt-free {_when(b['months'])}, interest {_m(b['total_interest'])}")
    lump_total = sum(result["lump"].values()) if result["lump"] else 0.0
    label = f"+{_m(result['monthly_extra'])}/mo" + (f" and {_m(lump_total)} now" if lump_total else "")
    print(f"  {label:<26} debt-free {_when(w['months'])}, interest {_m(w['total_interest'])} "
          f"{C.GRN}(saves {_m(result['interest_saved'])}){C.RESET}")
    if w["payoff"]:
        order = sorted(w["payoff"].items(), key=lambda kv: kv[1])
        name = {d["id"]: d["name"] for d in debts}
        print("  order: " + " → ".join(f"{name.get(i, i)} ({_when(m).split(' (')[0]})" for i, m in order))
    if result["by"]:
        need = result["needed_for_by"]
        if need is None:
            print(f"  {C.RED}debt-free by {result['by']}: not reachable within a sane budget{C.RESET}")
        else:
            print(f"  {C.BOLD}debt-free by {result['by']}: {_m(need)}/month extra{C.RESET} (on top of minimums"
                  + (f" and the {_m(lump_total)} sent now" if lump_total else "") + ")")
    print(f"\n  {C.DIM}Edit debts.yaml for real APRs/minimums and a monthly_extra; headroom ignores income, "
          f"so it is a floor, not a ceiling.{C.RESET}")


def print_promos(promos, acct_name):
    print(f"\n{C.BOLD}4. Promo-rate spending{C.RESET}")
    for s in promos:
        card = f"{s['institution']} {s['name']} {s['last4']}".strip()
        after = f", then {s['standard_apr']}%" if s["standard_apr"] is not None else ""
        print(f"  {C.BOLD}{card}{C.RESET}: {s['apr']:.2f}% on new purchases until {s['until']:%b %d, %Y}"
              f"{after} — {s['days_left']} days left")
        if not s["activated"]:
            print(f"    {C.RED}not activated — activate it in the card's app by {s['activate_by']}{C.RESET}")
        if s["credit_limit"]:
            print(f"    balance {_m(s['balance'])} of {_m(s['credit_limit'])} limit ({s['utilization']}%) · "
                  f"cap {s['target_utilization']:.0f}% = {_m(s['cap'])} · room {_m(s['room'])}")
        else:
            print(f"    balance {_m(s['balance'])} · {C.YEL}credit_limit unknown — add it under promos in debts.yaml{C.RESET}")
        if s["phase"] == "fill":
            if s["room"]:
                t = s["target"]
                where = f" toward {C.BOLD}{t['name']}{C.RESET} ({t['apr']:.2f}%)" if t else " toward your highest-rate debt"
                print(f"    {C.GRN}→ put everyday spending on this card until it reaches {_m(s['cap'])} "
                      f"({_m(s['room'])} more){C.RESET}, and send the cash you would have spent{where}")
                if s["est_savings"]:
                    print(f"    {C.DIM}worth roughly {_m(s['est_savings'])} of interest if you fill it now and clear it by "
                          f"{s['until']:%b %Y}{C.RESET}")
            elif s["room"] == 0:
                print(f"    {C.YEL}at the {s['target_utilization']:.0f}% cap — keep spending elsewhere until it comes down{C.RESET}")
            print(f"    pay only the minimum until you stop adding on {s['stop_adding']:%b %d, %Y}; then pay "
                  f"{C.BOLD}{_m(s['payoff_payment'])}/mo{C.RESET} for {s['payoff_months']} months to clear "
                  f"{_m(s['payoff_balance'])} before the rate jumps")
        else:
            print(f"    {C.YEL}pay-off phase: no new charges{C.RESET}; pay {C.BOLD}{_m(s['payoff_payment'])}/mo{C.RESET} "
                  f"for {s['payoff_months']} months to clear {_m(s['balance'])} by {s['until']:%b %d}")
        for x in s["autopay_full"]:
            print(f"    {C.RED}✗ autopay \"{x.get('name')}\" pays the statement in full from "
                  f"{acct_name.get(x.get('funding_account'), x.get('funding_account'))} — switch it to the minimum "
                  f"payment in the card's app, then set autopay_type: min in payments.yaml{C.RESET}")


def main():
    p = argparse.ArgumentParser(description="Debt paydown plan that never overdrafts a funding account")
    p.add_argument("--monthly-extra", type=float, help="extra per month on top of minimums (default: debts.yaml plan.monthly_extra)")
    p.add_argument("--by", help="YYYY-MM: solve for the monthly extra needed to be debt-free by then")
    p.add_argument("--snowball", action="store_true", help="smallest balance first (default: highest APR first)")
    p.add_argument("--no-rollover", action="store_true", help="do not roll a paid-off minimum into the next target")
    p.add_argument("--no-lump", action="store_true", help="plan without sending today's headroom")
    p.add_argument("--days", type=int, default=30, help="headroom horizon for scheduled debits (default 30)")
    p.add_argument("--fresh", action="store_true", help="re-query Monarch and Zoho (bypass the sync cache)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-color", action="store_true")
    args = p.parse_args()
    if args.no_color or not sys.stdout.isatty() or args.json:
        C.off()

    data = asyncio.run(gather(days=args.days, fresh=args.fresh))
    result = plan(data, monthly_extra=args.monthly_extra, strategy="snowball" if args.snowball else None,
                  rollover=not args.no_rollover, by=args.by, use_headroom=not args.no_lump)
    if args.json:
        out = {k: v for k, v in data.items() if k not in ("config", "debts_cfg", "sync")}
        out["plan"] = result
        print(json.dumps(out, indent=1, default=str))
    else:
        print_report(data, result)


if __name__ == "__main__":
    main()
