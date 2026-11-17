#!/usr/bin/env python3
"""
Monarch transaction categorizer: fetch -> rules -> history -> AI -> human review -> apply.

Subcommands
  rules pull                 Snapshot the rules stored inside Monarch to data/categorize/monarch_rules.json
  run [selection] [options]  Fetch a configurable group of transactions, propose a category for each,
                             write a run folder with proposals.json + review.html
  apply RUN [--approved F]   Push the approved decisions back to Monarch (dry-run by default)

Selection (pick one; default --days 7):
  --limit N                  the N most recent transactions
  --days N                   transactions dated in the last N days
  --since YYYY-MM-DD [--until YYYY-MM-DD]
  --all                      include transactions Monarch already marks reviewed (default: needs-review only)

Confidence ladder (highest wins; below --ai-threshold the AI model is asked):
  rule:<name>          local rule in data/categorize/rules.yaml       (rule's own confidence, default 0.95)
  monarch-rule:<id>    a rule Monarch itself stores, whose action matches  (0.90)
  history              the merchant's past reviewed transactions agree  (share of agreeing txns, scaled)
  monarch-default      whatever Monarch/Plaid already assigned           (0.50)
  ai                   Claude's pick among the household's categories   (model-reported)

Data folder: data/categorize/ (rules.yaml, monarch_rules.json, runs/<run-id>/ — runs are gitignored).
"""

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from monarchmoney import MonarchMoney
    from monarchmoney.monarchmoney import MonarchMoneyEndpoints
    MonarchMoneyEndpoints.BASE_URL = "https://api.monarch.com"
    from gql import gql
    from monarch_client import get_monarch_client
except ImportError:
    MonarchMoney = None

HERE = Path(__file__).parent
DATA_DIR = HERE / "data" / "categorize"
RULES_FILE = DATA_DIR / "rules.yaml"
MONARCH_RULES_FILE = DATA_DIR / "monarch_rules.json"
RUNS_DIR = DATA_DIR / "runs"
TEMPLATE = HERE / "templates" / "categorize_review.html"

AI_MODEL = os.environ.get("CATEGORIZE_AI_MODEL", "claude-opus-5-5")

# Every field of Monarch's TransactionRuleV2 that resolves for a normal user (spiked 2026-11-17;
# mergeTransactionAction errors server-side and is left out on purpose).
RULES_QUERY = """
query GetTransactionRules {
  transactionRules {
    id order merchantCriteriaUseOriginalStatement
    merchantCriteria { operator value }
    amountCriteria { operator isExpense value valueRange { lower upper } }
    categoryIds accountIds
    categories { id name }
    accounts { id displayName }
    setMerchantAction { id name }
    setCategoryAction { id name }
    addTagsAction { id name }
    linkGoalAction { id name }
    needsReviewByUserAction { id name }
    unassignNeedsReviewByUserAction sendNotificationAction setHideFromReportsAction reviewStatusAction
    recentApplicationCount lastAppliedAt
    splitTransactionsAction { amountType splitsInfo { categoryId merchantName amount } }
  }
}
"""


# --------------------------------------------------------------------------- rules

def load_local_rules(path=RULES_FILE):
    """Rules from rules.yaml, in file order (first match wins)."""
    if not path.exists():
        return []
    doc = yaml.safe_load(path.read_text()) or {}
    return doc.get("rules") or []


def monarch_rules_as_local(monarch_rules):
    """Translate Monarch's stored rules into the local rule shape so one matcher serves both.

    Monarch operators seen in the wild: merchant `contains` / `eq` / `starts_with` / `ends_with`,
    amount `eq` / `gt` / `lt` / `between`. Unknown operators make the rule never match and are
    reported so the mapping can be extended.
    """
    out = []
    for r in monarch_rules:
        if not r.get("setCategoryAction"):
            continue  # only category-setting rules matter here
        match = {}
        mc = r.get("merchantCriteria") or []
        if mc:
            crit = mc[0]
            match["merchant"] = {_merchant_op(crit.get("operator")): crit.get("value", "")}
            if r.get("merchantCriteriaUseOriginalStatement"):
                match["merchant"]["use_original_statement"] = True
        ac = r.get("amountCriteria")
        if ac:
            amt = {}
            if ac.get("isExpense") is not None:
                amt["is_expense"] = bool(ac["isExpense"])
            op, val = ac.get("operator"), ac.get("value")
            if op in ("eq", "equals") and val is not None:
                amt["equals"] = val
            elif op in ("gt", "greater_than") and val is not None:
                amt["greater_than"] = val
            elif op in ("lt", "less_than") and val is not None:
                amt["less_than"] = val
            elif op == "between" and ac.get("valueRange"):
                amt["between"] = [ac["valueRange"].get("lower"), ac["valueRange"].get("upper")]
            elif op is not None:
                amt["_unknown_operator"] = op
            match["amount"] = amt
        if r.get("accountIds"):
            match["account"] = {"id_in": list(r["accountIds"])}
        if r.get("categoryIds"):
            match["current_category"] = {"id_in": list(r["categoryIds"])}
        if not match:
            # Criteria live in a field the query does not fetch (seen on ~25 of 87 rules, Nov 2026).
            # An empty match would fire on everything, so the rule is kept for the report but never matches.
            match = {"_unresolved_criteria": True}
        out.append({
            "name": f"monarch-rule:{r['id']}",
            "match": match,
            "set": {"category": r["setCategoryAction"]["name"], "category_id": r["setCategoryAction"]["id"]},
            "confidence": 0.90,
            "source": "monarch-rule",
        })
    return out


def _merchant_op(op):
    return {"eq": "equals", "equals": "equals", "contains": "contains", "starts_with": "starts_with",
            "startsWith": "starts_with", "ends_with": "ends_with", "endsWith": "ends_with",
            "regex": "regex"}.get(op, f"_unknown:{op}")


def _text_match(spec, text):
    """spec like {"contains": "x"} against text, case-insensitive; one operator per spec."""
    text = (text or "").lower()
    for op, needle in spec.items():
        if op == "use_original_statement":
            continue
        needle_l = str(needle).lower()
        if op == "contains":
            return needle_l in text
        if op == "equals":
            return text == needle_l
        if op == "starts_with":
            return text.startswith(needle_l)
        if op == "ends_with":
            return text.endswith(needle_l)
        if op == "regex":
            return re.search(str(needle), text or "", re.IGNORECASE) is not None
        return False  # unknown operator never matches
    return True


def _amount_match(spec, amount):
    """Monarch amounts: negative = money out. Rule values are absolute."""
    if not spec:
        return True
    if "_unknown_operator" in spec:
        return False
    is_expense = amount < 0
    if "is_expense" in spec and bool(spec["is_expense"]) != is_expense:
        return False
    mag = abs(amount)
    if "equals" in spec and abs(mag - float(spec["equals"])) > 0.005:
        return False
    if "greater_than" in spec and not mag > float(spec["greater_than"]):
        return False
    if "less_than" in spec and not mag < float(spec["less_than"]):
        return False
    if "between" in spec:
        lo, hi = spec["between"]
        if lo is not None and mag < float(lo):
            return False
        if hi is not None and mag > float(hi):
            return False
    return True


def rule_matches(rule, txn):
    """True when every clause of rule['match'] holds for the transaction dict (Monarch shape)."""
    m = rule.get("match") or {}
    if not m or m.get("_unresolved_criteria"):
        return False  # a rule with no (known) criteria must never match everything
    if "merchant" in m:
        spec = m["merchant"]
        text = txn.get("plaidName") if spec.get("use_original_statement") else (txn.get("merchant") or {}).get("name")
        if not _text_match(spec, text):
            return False
    if "statement" in m and not _text_match(m["statement"], txn.get("plaidName")):
        return False
    if "amount" in m and not _amount_match(m["amount"], float(txn.get("amount") or 0)):
        return False
    if "account" in m:
        spec = m["account"]
        acct = txn.get("account") or {}
        if "id_in" in spec and acct.get("id") not in spec["id_in"]:
            return False
        if "contains" in spec and spec["contains"].lower() not in (acct.get("displayName") or "").lower():
            return False
    if "current_category" in m:
        cat = txn.get("category") or {}
        if "id_in" in m["current_category"] and cat.get("id") not in m["current_category"]["id_in"]:
            return False
    return True


def first_matching_rule(rules, txn):
    for r in rules:
        if rule_matches(r, txn):
            return r
    return None


# --------------------------------------------------------------------------- categories

class Categories:
    """Name <-> id lookup over Monarch's category list."""

    def __init__(self, cats):
        self.by_id = {c["id"]: c for c in cats}
        self.by_name = {c["name"].lower(): c for c in cats}

    def resolve(self, name_or_id):
        if name_or_id in self.by_id:
            return self.by_id[name_or_id]
        return self.by_name.get(str(name_or_id).lower())

    def public(self):
        return [{"id": c["id"], "name": c["name"], "group": c["group"]["name"], "type": c["group"]["type"]}
                for c in sorted(self.by_id.values(), key=lambda c: (c["group"]["name"], c["name"]))
                if not c.get("isDisabled")]


# --------------------------------------------------------------------------- proposals

def history_candidate(history_txns, self_id):
    """Category distribution of a merchant's past reviewed transactions -> (candidate or None)."""
    counts = Counter()
    for t in history_txns:
        if t.get("id") == self_id or t.get("needsReview"):
            continue
        cat = t.get("category") or {}
        if cat.get("id"):
            counts[(cat["id"], cat["name"])] += 1
    total = sum(counts.values())
    if not total:
        return None
    (cid, cname), n = counts.most_common(1)[0]
    share = n / total
    confidence = round(share * min(1.0, total / 3), 3)  # fewer than 3 samples never reach full confidence
    return {"category_id": cid, "category_name": cname, "confidence": confidence,
            "source": f"history:{n}/{total}", "reason": f"{n} of {total} past reviewed transactions for this merchant"}


def propose(txn, local_rules, monarch_rules, categories, history_txns):
    """All candidates for one transaction, best first."""
    cands = []
    r = first_matching_rule(local_rules, txn)
    if r:
        cat = categories.resolve(r["set"].get("category_id") or r["set"].get("category"))
        if cat:
            cands.append({"category_id": cat["id"], "category_name": cat["name"],
                          "confidence": float(r.get("confidence", 0.95)), "source": f"rule:{r.get('name', '?')}",
                          "reason": "local rule in rules.yaml"})
    r = first_matching_rule(monarch_rules, txn)
    if r:
        cat = categories.resolve(r["set"].get("category_id") or r["set"].get("category"))
        if cat:
            cands.append({"category_id": cat["id"], "category_name": cat["name"],
                          "confidence": float(r.get("confidence", 0.9)), "source": r["name"],
                          "reason": "rule stored in Monarch"})
    h = history_candidate(history_txns, txn["id"])
    if h:
        cands.append(h)
    cur = txn.get("category") or {}
    if cur.get("id"):
        cands.append({"category_id": cur["id"], "category_name": cur["name"], "confidence": 0.5,
                      "source": "monarch-default", "reason": "category Monarch already assigned"})
    cands.sort(key=lambda c: -c["confidence"])
    return cands


def ai_pick(items, categories, model=AI_MODEL):
    """Ask Claude for the best category of each uncertain item in one call.

    Returns ({txn_id: pick}, error_string_or_None). A credential or API failure never raises:
    the caller leaves those items for the human reviewer.
    """
    try:
        import anthropic
        from pydantic import BaseModel
    except ImportError as e:
        return {}, f"anthropic SDK not installed ({e})"

    class Pick(BaseModel):
        transaction_id: str
        category: str
        confidence: float
        reason: str

    class Picks(BaseModel):
        picks: list[Pick]

    cat_lines = "\n".join(f"- {c['name']}  [{c['group']}]" for c in categories.public())
    txn_lines = []
    for it in items:
        hist = ", ".join(f"{c['category_name']} ({c['source']}, {c['confidence']:.2f})" for c in it["candidates"]) or "none"
        txn_lines.append(
            f"id={it['id']} date={it['date']} amount={it['amount']:.2f} merchant={it['merchant']!r} "
            f"statement={it['plaid_name']!r} account={it['account']!r} current={it['current_category']!r} "
            f"signals: {hist}")
    prompt = (
        "You categorize personal-finance transactions for a household's Monarch Money account. "
        "Negative amounts are money out, positive are money in. Pick exactly one category per transaction "
        "from this list (use the exact name):\n" + cat_lines +
        "\n\nFor each transaction give the best category, a confidence from 0 to 1, and a one-sentence reason. "
        "Weigh the signals: a strong merchant history usually wins; the current category may be a Plaid guess.\n\n"
        "Transactions:\n" + "\n".join(txn_lines))
    try:
        client = anthropic.Anthropic()
        resp = client.messages.parse(model=model, max_tokens=4000, output_format=Picks,
                                     messages=[{"role": "user", "content": prompt}])
        if resp.stop_reason == "refusal":
            return {}, "model declined the request"
        picks = {}
        for p in resp.parsed_output.picks:
            cat = categories.resolve(p.category)
            if cat:
                picks[p.transaction_id] = {"category_id": cat["id"], "category_name": cat["name"],
                                           "confidence": max(0.0, min(1.0, p.confidence)), "source": f"ai:{model}",
                                           "reason": p.reason}
        return picks, None
    except Exception as e:  # auth, network, rate limit, schema: all leave the human in charge
        return {}, f"{type(e).__name__}: {str(e)[:200]}"


# --------------------------------------------------------------------------- monarch I/O

async def fetch_transactions(mm, args):
    """The configurable group: --limit / --days / --since; needs-review only unless --all."""
    kwargs = {}
    if args.since:
        kwargs["start_date"] = args.since
        kwargs["end_date"] = args.until or date.today().isoformat()
    elif args.limit is None:
        days = args.days or 7
        kwargs["start_date"] = (date.today() - timedelta(days=days)).isoformat()
        kwargs["end_date"] = date.today().isoformat()
    want = args.limit or 1000
    page = min(100, want)
    out, offset = [], 0
    while len(out) < want:
        d = await mm.get_transactions(limit=page, offset=offset, **kwargs)
        rows = d["allTransactions"]["results"]
        if not rows:
            break
        for t in rows:
            if args.all or t.get("needsReview"):
                out.append(t)
                if len(out) >= want:
                    break
        offset += len(rows)
        if len(rows) < page or offset >= 2000:
            break
    return out


async def fetch_merchant_history(mm, merchant_name, limit):
    if not merchant_name:
        return []
    try:
        d = await mm.get_transactions(limit=limit, search=merchant_name)
        return d["allTransactions"]["results"]
    except Exception as e:
        print(f"  warning: history lookup failed for {merchant_name!r}: {e}", file=sys.stderr)
        return []


async def pull_monarch_rules(mm):
    r = await mm.gql_call("GetTransactionRules", gql(RULES_QUERY))
    return r["transactionRules"]


# --------------------------------------------------------------------------- commands

def cmd_rules_pull(args):
    async def go():
        mm = await get_monarch_client()
        if mm is None:
            sys.exit(2)
        rules = await pull_monarch_rules(mm)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        MONARCH_RULES_FILE.write_text(json.dumps({"pulled_at": datetime.now().isoformat(timespec="seconds"),
                                                  "rules": rules}, indent=2))
        print(f"Pulled {len(rules)} rules from Monarch -> {MONARCH_RULES_FILE.relative_to(HERE)}")
        local = monarch_rules_as_local(rules)
        unresolved = [r for r in local if r["match"].get("_unresolved_criteria")]
        print(f"  {len(local)} set a category; {len(local) - len(unresolved)} translated into matcher form, "
              f"{len(unresolved)} have criteria the query cannot see (kept, never matched)")
        for r in local:
            m = r["match"]
            if m.get("_unresolved_criteria"):
                continue
            flags = [k for k, v in m.items() if isinstance(v, dict) and any(str(x).startswith("_unknown") for x in list(v) + list(v.values()))]
            print(f"  - {r['name']}: {json.dumps(m)} -> {r['set']['category']}" + (f"  (UNMAPPED: {flags})" if flags else ""))
        if unresolved:
            print("  unresolved: " + ", ".join(f"{r['name'].split(':')[1]} -> {r['set']['category']}" for r in unresolved))
    asyncio.run(go())


def load_monarch_rules():
    if not MONARCH_RULES_FILE.exists():
        return []
    return monarch_rules_as_local(json.loads(MONARCH_RULES_FILE.read_text()).get("rules", []))


def cmd_run(args):
    async def go():
        mm = await get_monarch_client()
        if mm is None:
            sys.exit(2)
        local_rules = load_local_rules()
        monarch_rules = load_monarch_rules()
        cats = Categories((await mm.get_transaction_categories())["categories"])
        txns = await fetch_transactions(mm, args)
        print(f"Fetched {len(txns)} transaction(s); {len(local_rules)} local rule(s), {len(monarch_rules)} Monarch rule(s)")

        history_cache = {}
        items = []
        for t in txns:
            mname = (t.get("merchant") or {}).get("name") or ""
            if mname not in history_cache:
                history_cache[mname] = await fetch_merchant_history(mm, mname, args.history)
            cands = propose(t, local_rules, monarch_rules, cats, history_cache[mname])
            best = cands[0] if cands else None
            items.append({
                "id": t["id"], "date": t["date"], "amount": float(t["amount"]),
                "merchant": mname, "plaid_name": t.get("plaidName") or "",
                "account": (t.get("account") or {}).get("displayName") or "",
                "account_id": (t.get("account") or {}).get("id"),
                "current_category": (t.get("category") or {}).get("name") or "",
                "current_category_id": (t.get("category") or {}).get("id"),
                "needs_review": bool(t.get("needsReview")), "notes": t.get("notes") or "",
                "candidates": cands,
                "proposed": dict(best) if best else None,
            })

        uncertain = [it for it in items if not it["proposed"] or it["proposed"]["confidence"] < args.ai_threshold]
        ai_error = None
        if uncertain and not args.no_ai:
            print(f"Asking {AI_MODEL} about {len(uncertain)} low-confidence transaction(s)...")
            picks, ai_error = ai_pick(uncertain, cats)
            for it in uncertain:
                p = picks.get(it["id"])
                if p:
                    it["candidates"].insert(0, p)
                    it["proposed"] = dict(p)
            if ai_error:
                print(f"  AI step unavailable: {ai_error}\n  Those rows stay flagged for the reviewer.", file=sys.stderr)
        for it in items:
            it["needs_human"] = (not it["proposed"]) or it["proposed"]["confidence"] < args.ai_threshold

        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = RUNS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "transactions.json").write_text(json.dumps(txns, indent=2))
        run = {
            "run_id": run_id, "generated_at": datetime.now().isoformat(timespec="seconds"),
            "selection": {"limit": args.limit, "days": args.days, "since": args.since, "until": args.until, "all": args.all},
            "ai": {"model": AI_MODEL, "threshold": args.ai_threshold, "asked": len(uncertain) if not args.no_ai else 0,
                   "error": ai_error},
            "categories": cats.public(),
            "items": items,
        }
        (run_dir / "proposals.json").write_text(json.dumps(run, indent=2))
        html = TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(run))
        (run_dir / "review.html").write_text(html)

        print(f"\nRun {run_id} -> {run_dir.relative_to(HERE)}/")
        print(f"{'date':10} {'amount':>10}  {'merchant':28} {'current':20} -> {'proposed':20} {'conf':>5}  source")
        for it in items:
            p = it["proposed"] or {"category_name": "?", "confidence": 0, "source": "none"}
            flag = " *" if it["needs_human"] else ""
            print(f"{it['date']:10} {it['amount']:>10.2f}  {it['merchant'][:28]:28} {it['current_category'][:20]:20} -> "
                  f"{p['category_name'][:20]:20} {p['confidence']:>5.2f}  {p['source']}{flag}")
        n_h = sum(it["needs_human"] for it in items)
        print(f"\n{len(items)} proposed, {n_h} need a human pick (*). Review: {run_dir / 'review.html'}")
        print(f"Then: task finance:categorize:apply -- {run_id} --approved <approved.json> [--execute]")
    asyncio.run(go())


def load_approved(run_dir, path):
    """approved.json: {"decisions": {txn_id: {"category_id", "category_name", "apply": bool}}}.

    Without a file, every proposal is treated as approved as proposed (useful for --dry-run previews).
    """
    run = json.loads((run_dir / "proposals.json").read_text())
    if path:
        approved = json.loads(Path(path).read_text())
        decisions = approved.get("decisions") or {}
    else:
        decisions = {it["id"]: {"category_id": it["proposed"]["category_id"], "category_name": it["proposed"]["category_name"],
                                "apply": not it["needs_human"]}
                     for it in run["items"] if it["proposed"]}
    return run, decisions


def plan_updates(run, decisions, mark_reviewed=True):
    """What apply will send to Monarch, one dict per transaction that changes."""
    plan = []
    for it in run["items"]:
        d = decisions.get(it["id"])
        if not d or not d.get("apply"):
            continue
        change_cat = d.get("category_id") and d["category_id"] != it["current_category_id"]
        change_rev = mark_reviewed and it["needs_review"]
        if not (change_cat or change_rev):
            continue
        plan.append({"id": it["id"], "date": it["date"], "amount": it["amount"], "merchant": it["merchant"],
                     "from": it["current_category"], "to": d.get("category_name") if change_cat else it["current_category"],
                     "category_id": d["category_id"] if change_cat else None,
                     "needs_review": False if change_rev else None})
    return plan


def cmd_apply(args):
    run_dir = RUNS_DIR / args.run
    if not run_dir.exists():
        sys.exit(f"No such run: {run_dir}")
    run, decisions = load_approved(run_dir, args.approved)
    plan = plan_updates(run, decisions, mark_reviewed=not args.no_mark_reviewed)
    src = args.approved or "proposals as-is (no approved.json given)"
    print(f"Run {args.run}: {len(plan)} update(s) from {src}")
    for p in plan:
        bits = []
        if p["category_id"]:
            bits.append(f"category {p['from']!r} -> {p['to']!r}")
        if p["needs_review"] is False:
            bits.append("mark reviewed")
        print(f"  {p['date']} {p['amount']:>10.2f} {p['merchant'][:28]:28} {', '.join(bits)}")
    if not plan:
        return
    if not args.execute:
        print("\nDry run. Add --execute to send these to Monarch.")
        return

    async def go():
        mm = await get_monarch_client()
        if mm is None:
            sys.exit(2)
        log = run_dir / "apply.log"
        ok = 0
        with log.open("a") as fh:
            for p in plan:
                kwargs = {}
                if p["category_id"]:
                    kwargs["category_id"] = p["category_id"]
                if p["needs_review"] is False:
                    kwargs["needs_review"] = False
                try:
                    await mm.update_transaction(transaction_id=p["id"], **kwargs)
                    ok += 1
                    fh.write(f"{datetime.now().isoformat(timespec='seconds')} OK {p['id']} {json.dumps(kwargs)}\n")
                except Exception as e:
                    fh.write(f"{datetime.now().isoformat(timespec='seconds')} FAIL {p['id']} {json.dumps(kwargs)} {e}\n")
                    print(f"  FAILED {p['id']}: {e}", file=sys.stderr)
        print(f"\nApplied {ok}/{len(plan)}; log: {log.relative_to(HERE)}")
    asyncio.run(go())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser("rules", help="rules pull: snapshot Monarch's own rules")
    rp.add_argument("action", choices=["pull"])

    rn = sub.add_parser("run", help="fetch + propose + build the review page")
    sel = rn.add_mutually_exclusive_group()
    sel.add_argument("--limit", type=int, help="the N most recent transactions")
    sel.add_argument("--days", type=int, help="transactions in the last N days (default 7)")
    sel.add_argument("--since", help="YYYY-MM-DD")
    rn.add_argument("--until", help="YYYY-MM-DD (with --since)")
    rn.add_argument("--all", action="store_true", help="include already-reviewed transactions")
    rn.add_argument("--no-ai", action="store_true", help="skip the AI step")
    rn.add_argument("--ai-threshold", type=float, default=0.8, help="confidence below which the AI is asked (default 0.8)")
    rn.add_argument("--history", type=int, default=25, help="past transactions per merchant to inspect (default 25)")

    ra = sub.add_parser("apply", help="send approved decisions to Monarch")
    ra.add_argument("run", help="run id (folder name under data/categorize/runs/)")
    ra.add_argument("--approved", help="approved.json exported from the review page")
    ra.add_argument("--execute", action="store_true", help="actually update Monarch (default: dry run)")
    ra.add_argument("--no-mark-reviewed", action="store_true", help="leave the needs-review flag as is")

    args = ap.parse_args()
    if MonarchMoney is None:
        sys.exit("monarchmoney / gql not installed: task finance:install")
    {"rules": cmd_rules_pull, "run": cmd_run, "apply": cmd_apply}[args.cmd](args)


if __name__ == "__main__":
    main()
