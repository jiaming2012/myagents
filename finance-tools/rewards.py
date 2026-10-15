#!/usr/bin/env python3
"""Deterministic credit card rewards tracker.

Reads earn rules and statement credits from rewards.yaml, card identities from
payments.yaml, and transactions from Monarch Money (or a saved snapshot), then
reports for each card:

  * points/cash earned per earn rule, with caps applied
  * statement credit usage per period (which quarters you missed)
  * sign-up bonus progress
  * expected rewards balance vs. the balance you typed in from the issuer

No LLM, no heuristics: same transactions + same rules = same report.

Usage:
    python rewards.py                      # all cards, current calendar year, live pull
    python rewards.py --card amex-platinum-2004
    python rewards.py --offline            # reuse the last snapshot, no network
    python rewards.py --as-of 2026-09-30   # deterministic "today" for period math
    python rewards.py --json               # machine-readable output
"""

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
REWARDS_PATH = SCRIPT_DIR / "rewards.yaml"
REGISTRY_PATH = SCRIPT_DIR / "payments.yaml"
SNAPSHOT_PATH = SCRIPT_DIR / ".rewards_snapshot.json"

PERIOD_MONTHS = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_rewards(path=REWARDS_PATH):
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("exclude", {})
    cfg.setdefault("cards", [])
    return cfg


def load_registry(path=REGISTRY_PATH):
    """Return {account_id: account dict} from payments.yaml."""
    with open(path) as f:
        reg = yaml.safe_load(f) or {}
    return {a["id"]: a for a in reg.get("accounts", [])}


def resolve_cards(rewards_cfg, registry, only=None):
    """Attach the payments.yaml account to each rewards card. Fails loudly on
    an unknown account id so a typo never silently reports zero."""
    cards = []
    for card in rewards_cfg["cards"]:
        acct_id = card.get("account")
        if only and acct_id not in only:
            continue
        acct = registry.get(acct_id)
        if acct is None:
            raise SystemExit(f"rewards.yaml: account '{acct_id}' is not in payments.yaml")
        cards.append({**card, "_account": acct})
    if only:
        missing = set(only) - {c["account"] for c in cards}
        if missing:
            raise SystemExit(f"rewards.yaml has no card for: {', '.join(sorted(missing))}")
    return cards


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

def normalize_txn(raw):
    """Flatten a Monarch transaction (or a snapshot row) to the fields we use."""
    if "account_name" in raw:          # already normalized (snapshot)
        return raw
    merchant = (raw.get("merchant") or {}).get("name")
    category = (raw.get("category") or {}).get("name")
    account = (raw.get("account") or {}).get("displayName")
    return {
        "id": raw.get("id"),
        "date": raw.get("date"),
        "amount": float(raw.get("amount") or 0),
        "merchant": merchant,
        "description": raw.get("plaidName") or raw.get("dataProviderDescription") or "",
        "category": category,
        "account_name": account,
        "pending": bool(raw.get("pending")),
    }


def txn_date(txn):
    d = txn["date"]
    return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])


def _haystack(txn):
    return f"{txn.get('merchant') or ''}\n{txn.get('description') or ''}".upper()


def matches_merchant(patterns, txn):
    if not patterns:
        return False
    hay = _haystack(txn)
    return any(p.upper() in hay for p in patterns)


def matches_category(names, txn):
    if not names:
        return False
    cat = (txn.get("category") or "").strip().lower()
    return cat in {n.strip().lower() for n in names}


def is_excluded(exclude, txn):
    return matches_category(exclude.get("category"), txn) or matches_merchant(exclude.get("merchant"), txn)


def card_transactions(card, txns, exclude):
    """Settled, non-excluded transactions on this card, oldest first."""
    name = card["_account"].get("monarch_match")
    out = [t for t in txns
           if t.get("account_name") == name and not t.get("pending") and not is_excluded(exclude, t)]
    return sorted(out, key=lambda t: (txn_date(t), t.get("id") or ""))


# ---------------------------------------------------------------------------
# Periods (calendar-based)
# ---------------------------------------------------------------------------

def period_bounds(kind, day):
    """(start, end) inclusive calendar period of `kind` containing `day`."""
    months = PERIOD_MONTHS[kind]
    start_month = ((day.month - 1) // months) * months + 1
    start = date(day.year, start_month, 1)
    end_month = start_month + months
    end = (date(day.year + 1, 1, 1) if end_month > 12 else date(day.year, end_month, 1)) - timedelta(days=1)
    return start, end


def period_label(kind, start):
    if kind == "monthly":
        return start.strftime("%Y-%m")
    if kind == "quarterly":
        return f"{start.year} Q{(start.month - 1) // 3 + 1}"
    if kind == "semiannual":
        return f"{start.year} H{(start.month - 1) // 6 + 1}"
    return str(start.year)


def periods_in_year(kind, year):
    """All (label, start, end) periods of `kind` in a calendar year."""
    out = []
    day = date(year, 1, 1)
    while day.year == year:
        start, end = period_bounds(kind, day)
        out.append((period_label(kind, start), start, end))
        day = end + timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Earn rules
# ---------------------------------------------------------------------------

def rule_matches(rule, txn):
    if not rule.get("merchant") and not rule.get("category"):
        return True                      # base rate
    return matches_merchant(rule.get("merchant"), txn) or matches_category(rule.get("category"), txn)


def apply_earn(card, txns):
    """Allocate each transaction to the first matching earn rule.

    Returns a list of allocations: {txn, rule, spend, points, capped_spend}.
    Spend is positive for purchases, negative for refunds (points claw back).
    Caps are tracked per rule per calendar period; spend above the cap earns
    the base (last) rule's rate.
    """
    rules = card.get("earn") or []
    if not rules:
        return []
    base_rate = float(rules[-1].get("rate", 0))
    cap_used = defaultdict(float)        # (rule name, period label) -> spend counted
    allocations = []
    for txn in txns:
        spend = -float(txn["amount"])
        rule = next((r for r in rules if rule_matches(r, txn)), rules[-1])
        rate = float(rule.get("rate", 0))
        cap = rule.get("cap")
        under_cap, over_cap = spend, 0.0
        if cap and spend > 0:
            start, _ = period_bounds(cap["period"], txn_date(txn))
            key = (rule["name"], period_label(cap["period"], start))
            room = max(0.0, float(cap["amount"]) - cap_used[key])
            under_cap = min(spend, room)
            over_cap = spend - under_cap
            cap_used[key] += under_cap
        points = under_cap * rate + over_cap * base_rate
        allocations.append({
            "txn": txn, "rule": rule["name"], "spend": spend,
            "points": points, "capped_spend": over_cap,
        })
    return allocations


def earn_summary(card, allocations):
    by_rule = defaultdict(lambda: {"spend": 0.0, "points": 0.0, "capped_spend": 0.0, "count": 0})
    for a in allocations:
        s = by_rule[a["rule"]]
        s["spend"] += a["spend"]
        s["points"] += a["points"]
        s["capped_spend"] += a["capped_spend"]
        s["count"] += 1
    order = [r["name"] for r in card.get("earn") or []]
    rows = [{"rule": name, **by_rule[name]} for name in order if name in by_rule]
    total_points = sum(r["points"] for r in rows)
    total_spend = sum(r["spend"] for r in rows)
    cpp = float(card.get("cents_per_point", 1.0))
    return {
        "rows": rows,
        "total_spend": total_spend,
        "total_points": total_points,
        "total_value": total_points * cpp / 100.0,
        "effective_rate": (total_points * cpp / 100.0 / total_spend) if total_spend else 0.0,
    }


# ---------------------------------------------------------------------------
# Statement credits
# ---------------------------------------------------------------------------

def is_posted_credit(credit, txn):
    """True for the issuer's statement credit line for this benefit (positive
    amount matching `posted` patterns, falling back to `merchant`)."""
    if txn["amount"] <= 0:
        return False
    return matches_merchant(credit.get("posted") or credit.get("merchant"), txn)


def is_any_posted_credit(card, txn):
    return any(is_posted_credit(c, txn) for c in card.get("credits") or [])


def credit_usage(card, txns, year, today):
    """Per credit, per period of the year up to today: qualifying spend, amount
    used (capped), posted credit seen, and a status.

    status: used | partial | missed | open | future
    """
    out = []
    for credit in card.get("credits") or []:
        kind = credit["period"]
        amount = float(credit["amount"])
        periods = []
        for label, start, end in periods_in_year(kind, year):
            if start > today:
                continue
            in_period = [t for t in txns if start <= txn_date(t) <= end]
            spend = sum(-t["amount"] for t in in_period
                        if t["amount"] < 0 and matches_merchant(credit.get("merchant"), t))
            posted = sum(t["amount"] for t in in_period if is_posted_credit(credit, t))
            # A posted statement credit is proof the benefit was used even when
            # the qualifying merchant posts under a name we do not recognize.
            used = min(amount, max(spend, posted))
            is_open = end >= today
            if used >= amount - 0.005:
                status = "used"
            elif is_open:
                status = "open"
            elif used > 0:
                status = "partial"
            else:
                status = "missed"
            periods.append({
                "label": label, "start": start.isoformat(), "end": end.isoformat(),
                "spend": spend, "used": used, "remaining": max(0.0, amount - used),
                "posted": posted, "status": status,
                "days_left": (end - today).days if is_open else 0,
            })
        out.append({
            "name": credit["name"], "amount": amount, "period": kind,
            "enrolled": credit.get("enrolled", True), "note": credit.get("note"),
            "periods": periods,
            "year_used": sum(p["used"] for p in periods),
            "year_available": amount * len(periods),
        })
    return out


# ---------------------------------------------------------------------------
# Sign-up bonus, fees, balances
# ---------------------------------------------------------------------------

def signup_progress(card, txns, today):
    sub = card.get("signup_bonus")
    if not sub:
        return None
    opened = sub["opened"] if isinstance(sub["opened"], date) else date.fromisoformat(str(sub["opened"]))
    deadline = sub["deadline"] if isinstance(sub["deadline"], date) else date.fromisoformat(str(sub["deadline"]))
    spend = sum(-t["amount"] for t in txns if opened <= txn_date(t) <= deadline and t["amount"] < 0)
    target = float(sub["spend_target"])
    days_left = (deadline - today).days
    remaining = max(0.0, target - spend)
    return {
        "opened": opened.isoformat(), "deadline": deadline.isoformat(),
        "spend": spend, "target": target, "remaining": remaining,
        "days_left": days_left, "bonus": sub.get("bonus"),
        "daily_needed": (remaining / days_left) if days_left > 0 else None,
        "status": "met" if remaining == 0 else ("expired" if days_left < 0 else "in_progress"),
    }


def balance_check(card, allocations, today):
    """Compare typed-in issuer balances to computed earnings."""
    readings = card.get("balances") or []
    if not readings:
        return None
    def _d(v):
        return v if isinstance(v, date) else date.fromisoformat(str(v))
    readings = sorted(({"as_of": _d(r["as_of"]), "points": float(r["points"])} for r in readings),
                      key=lambda r: r["as_of"])
    latest = readings[-1]
    since = sum(a["points"] for a in allocations if txn_date(a["txn"]) > latest["as_of"])
    result = {
        "as_of": latest["as_of"].isoformat(), "reported": latest["points"],
        "earned_since": since, "expected_now": latest["points"] + since,
    }
    if len(readings) >= 2:
        prev = readings[-2]
        between = sum(a["points"] for a in allocations
                      if prev["as_of"] < txn_date(a["txn"]) <= latest["as_of"])
        actual_delta = latest["points"] - prev["points"]
        result["previous_as_of"] = prev["as_of"].isoformat()
        result["computed_delta"] = between
        result["actual_delta"] = actual_delta
        result["drift"] = actual_delta - between
    return result


def fee_info(card, today):
    fee = card.get("annual_fee")
    if fee is None:
        return None
    anniv = card.get("anniversary")
    info = {"annual_fee": float(fee), "anniversary": None, "days_until": None}
    if anniv:
        anniv = anniv if isinstance(anniv, date) else date.fromisoformat(str(anniv))
        nxt = anniv.replace(year=today.year)
        if nxt < today:
            nxt = anniv.replace(year=today.year + 1)
        info["anniversary"] = nxt.isoformat()
        info["days_until"] = (nxt - today).days
    return info


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def analyze_card(card, txns, exclude, year, today):
    ctx = card_transactions(card, txns, exclude)
    earnable = [t for t in ctx if not is_any_posted_credit(card, t)]
    in_year = [t for t in earnable if txn_date(t).year == year]
    allocations = apply_earn(card, in_year)
    credits = credit_usage(card, ctx, year, today)
    earn = earn_summary(card, allocations)
    credits_used = sum(c["year_used"] for c in credits if c["enrolled"])
    credits_available = sum(c["year_available"] for c in credits if c["enrolled"])
    fee = fee_info(card, today)
    return {
        "account": card["account"],
        "name": card["_account"].get("name"),
        "program": card.get("program"),
        "currency": card.get("currency", "pts"),
        "cents_per_point": float(card.get("cents_per_point", 1.0)),
        "year": year,
        "transactions": len(in_year),
        "earn": earn,
        "credits": credits,
        "credits_used": credits_used,
        "credits_available": credits_available,
        "signup_bonus": signup_progress(card, ctx, today),
        "balance": balance_check(card, apply_earn(card, earnable), today),
        "fee": fee,
        "net_value": earn["total_value"] + credits_used - (fee["annual_fee"] if fee else 0.0),
        "warnings": [f"not enrolled: {c['name']}" for c in credits if not c["enrolled"]],
    }


def _money(x):
    return f"${x:,.2f}"


def render_text(report, today):
    lines = []
    for r in report["cards"]:
        cur = r["currency"]
        lines.append("=" * 72)
        lines.append(f"{r['name']}  ({r['account']})  --  {r['year']} through {today.isoformat()}")
        lines.append("=" * 72)
        lines.append(f"Transactions in year: {r['transactions']}")
        lines.append("")
        lines.append("EARN")
        for row in r["earn"]["rows"]:
            capped = f"  (over cap: {_money(row['capped_spend'])})" if row["capped_spend"] else ""
            lines.append(f"  {row['rule']:<44} {_money(row['spend']):>12}  {row['points']:>10,.0f} {cur}{capped}")
        e = r["earn"]
        lines.append(f"  {'TOTAL':<44} {_money(e['total_spend']):>12}  {e['total_points']:>10,.0f} {cur}"
                     f"  = {_money(e['total_value'])} at {r['cents_per_point']:.2f} cpp"
                     f"  ({e['effective_rate'] * 100:.2f}% effective)")
        lines.append("")
        lines.append("STATEMENT CREDITS")
        for c in r["credits"]:
            flag = "" if c["enrolled"] else "  [NOT ENROLLED]"
            lines.append(f"  {c['name']}  ({_money(c['amount'])} {c['period']}){flag}")
            for p in c["periods"]:
                tail = {
                    "used": "used",
                    "partial": f"PARTIAL, {_money(p['remaining'])} left unused",
                    "missed": "MISSED",
                    "open": f"open, {_money(p['remaining'])} left, {p['days_left']} days",
                }[p["status"]]
                posted = f", credit posted {_money(p['posted'])}" if p["posted"] else ""
                lines.append(f"      {p['label']:<9} spend {_money(p['spend']):>10}  {tail}{posted}")
            lines.append(f"      year: {_money(c['year_used'])} of {_money(c['year_available'])}")
        lines.append(f"  Credits used: {_money(r['credits_used'])} of {_money(r['credits_available'])} enrolled")
        lines.append("")
        sub = r["signup_bonus"]
        if sub:
            lines.append("SIGN-UP BONUS")
            daily = f", need {_money(sub['daily_needed'])}/day" if sub["daily_needed"] else ""
            lines.append(f"  {_money(sub['spend'])} of {_money(sub['target'])} by {sub['deadline']}"
                         f"  ({sub['status']}, {sub['days_left']} days left{daily})")
            lines.append("")
        bal = r["balance"]
        if bal:
            lines.append("BALANCE")
            lines.append(f"  Reported {bal['reported']:,.0f} {cur} as of {bal['as_of']}; "
                         f"earned since {bal['earned_since']:,.0f}; expected now {bal['expected_now']:,.0f}")
            if "drift" in bal:
                lines.append(f"  {bal['previous_as_of']} -> {bal['as_of']}: issuer delta {bal['actual_delta']:,.0f}, "
                             f"computed {bal['computed_delta']:,.0f}, drift {bal['drift']:+,.0f}")
            lines.append("")
        fee = r["fee"]
        if fee:
            when = f" (next {fee['anniversary']}, {fee['days_until']} days)" if fee["anniversary"] else " (anniversary unknown)"
            lines.append(f"FEE  {_money(fee['annual_fee'])}{when}")
        lines.append(f"NET  earn {_money(r['earn']['total_value'])} + credits {_money(r['credits_used'])}"
                     f" - fee {_money(fee['annual_fee'] if fee else 0)} = {_money(r['net_value'])}")
        for w in r["warnings"]:
            lines.append(f"WARNING  {w}")
        lines.append("")
    return "\n".join(lines)


def build_report(rewards_cfg, registry, txns, year, today, only=None):
    cards = resolve_cards(rewards_cfg, registry, only)
    txns = [normalize_txn(t) for t in txns]
    return {
        "as_of": today.isoformat(),
        "year": year,
        "cards": [analyze_card(c, txns, rewards_cfg["exclude"], year, today) for c in cards],
    }


# ---------------------------------------------------------------------------
# Monarch pull + snapshot
# ---------------------------------------------------------------------------

async def fetch_transactions(start, end, page=500):
    """Pull every settled transaction in [start, end] from Monarch, all accounts."""
    from monarch_client import get_monarch_client
    mm = await get_monarch_client()
    if mm is None:
        raise SystemExit("Monarch not available; run with --offline to use the last snapshot")
    out, offset = [], 0
    while True:
        data = await mm.get_transactions(limit=page, offset=offset,
                                         start_date=start.isoformat(), end_date=end.isoformat())
        rows = data.get("allTransactions", {}).get("results", [])
        out.extend(normalize_txn(r) for r in rows)
        if len(rows) < page:
            return out
        offset += page


def write_snapshot(path, txns, start, end):
    path.write_text(json.dumps({
        "pulled_at": datetime.now().isoformat(timespec="seconds"),
        "start": start.isoformat(), "end": end.isoformat(),
        "transactions": txns,
    }, indent=1, default=str))


def read_snapshot(path):
    if not path.exists():
        raise SystemExit(f"no snapshot at {path}; run once without --offline")
    snap = json.loads(path.read_text())
    return snap["transactions"], snap


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--card", action="append", help="account id from payments.yaml (repeatable)")
    ap.add_argument("--year", type=int, help="calendar year to report (default: year of --as-of)")
    ap.add_argument("--as-of", help="YYYY-MM-DD treated as today (default: today)")
    ap.add_argument("--offline", action="store_true", help="use the last snapshot, no network")
    ap.add_argument("--snapshot", default=str(SNAPSHOT_PATH), help="snapshot path")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args(argv)

    today = date.fromisoformat(args.as_of) if args.as_of else date.today()
    year = args.year or today.year
    start, end = date(year, 1, 1), max(today, date(year, 12, 31))
    snapshot = Path(args.snapshot)

    if args.offline:
        txns, snap = read_snapshot(snapshot)
        print(f"(offline: snapshot from {snap['pulled_at']}, {snap['start']}..{snap['end']})", file=sys.stderr)
    else:
        txns = asyncio.run(fetch_transactions(start, end))
        write_snapshot(snapshot, txns, start, end)

    report = build_report(load_rewards(), load_registry(), txns, year, today, only=args.card)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_text(report, today))


if __name__ == "__main__":
    main()
