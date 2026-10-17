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


def build_debts(config, debts_cfg, monarch_by_name, zoho_by_name):
    """Every live liability with APR, minimum, funding account and provenance flags."""
    overrides = {d["account"]: d for d in debts_cfg.get("debts", []) if d.get("account")}
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
        carried = o.get("carried", not paid_in_full)
        if owed is None:
            notes.append(f"{a.get('name', a['id'])}: balance unknown (no live source) — skipped")
            continue
        if owed < 0.005:
            continue
        assumed = []
        apr = o.get("apr")
        if apr is None:
            apr = ASSUMED_APR.get(a["type"], 19.99)
            assumed.append("apr")
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
    rows = headroom_by_account(forecast, config)
    return {"config": config, "debts_cfg": debts_cfg, "sync": sync, "debts": debts, "notes": notes,
            "headroom": rows, "pools": pool_headroom(rows), "days": days}


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

    # 4. outlook
    b, w = result["baseline"], result["with_plan"]
    print(f"\n{C.BOLD}4. Outlook{C.RESET}")
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
