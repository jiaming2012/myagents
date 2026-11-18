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
  pair:monarch         the other side of the move sits in another Monarch account -> Transfer   (0.95)
  pair:mercury-business the other side left a Mercury business account (Mercury API) -> Yumyums (0.95)
  rule:<name>          local rule in data/categorize/rules.yaml       (rule's own confidence, default 0.95)
  monarch-rule:<id>    a rule Monarch itself stores, whose action matches  (0.90)
  history              the merchant's past reviewed transactions agree  (share of agreeing txns, scaled)
  monarch-default      whatever Monarch/Plaid already assigned           (0.50)
  ai                   Claude's pick among the household's categories   (model-reported)

AI step: modelled on sales-processor's payroll classify step. The run writes ai_request.json, invokes
the `claude` CLI (interactive at a terminal, -p when unattended) with a prompt that reads that file and
writes ai_proposals.json, then validates the verdicts. No API key; --no-ai skips it.

Pairing step: a deposit whose bank text says "wire" or "transfer" names no sender, so no text rule can
tell a move between the household's own accounts from income. The other side of the move can: a row of
the same amount, opposite sign, within a couple of days, in another account. Monarch holds that other
side for every linked account; the Mercury business accounts are not linked, so they are read through
the Mercury API (MERCURY_API_TOKEN, as mercury_classify.py does). Tunable under `pairing:` in rules.yaml.

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
def _rel(p):
    """Path for messages: relative to the tool when it lives there, absolute otherwise (tests, odd data dirs)."""
    try:
        return Path(p).relative_to(HERE)
    except ValueError:
        return Path(p)


RULES_FILE = DATA_DIR / "rules.yaml"
MONARCH_RULES_FILE = DATA_DIR / "monarch_rules.json"
RUNS_DIR = DATA_DIR / "runs"
TEMPLATE = HERE / "templates" / "categorize_review.html"

# Passed to `claude --model` when set; empty means the CLI's own default, as sales-processor does.
AI_MODEL = os.environ.get("CATEGORIZE_AI_MODEL", "").strip()

# Field set taken from the web app's Web_GetTransactionRules request (DevTools capture, 2026-11-17).
# merchantCriteria is the legacy merchant clause; newer rules use merchantNameCriteria (merchant name)
# and originalStatementCriteria (the bank's raw description). None of the public client libraries
# select the latter two, which is why those rules looked criteria-less at first.
RULES_QUERY = """
query GetTransactionRules {
  transactionRules {
    id order merchantCriteriaUseOriginalStatement
    merchantCriteria { operator value }
    merchantNameCriteria { operator value }
    originalStatementCriteria { operator value }
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


MERCHANTS_FILE = HERE / "merchants.yaml"

PAIRING_DEFAULTS = {
    "words": ["wire", "transfer", "xfer", "own account", "mercury"],  # rows worth pairing
    "window_days": 2,            # how far apart the two sides of a move may post
    "confidence": 0.95,
    "transfer_category": "Transfer",
    "mercury_business": {"category": "Yumyums", "accounts": []},  # accounts: Mercury API ids; [] = merchants.yaml
}


def load_pairing_config(path=RULES_FILE):
    """The `pairing:` block of rules.yaml over PAIRING_DEFAULTS; `pairing: false` turns the step off."""
    doc = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
    block = doc.get("pairing", {})
    if block is False:
        return None
    cfg = json.loads(json.dumps(PAIRING_DEFAULTS))
    for k, v in (block or {}).items():
        if k == "mercury_business" and isinstance(v, dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def mercury_business_accounts(cfg, merchants_file=MERCHANTS_FILE):
    """Mercury API account ids to search: the config's own list, else the accounts merchants.yaml reconciles."""
    ids = (cfg.get("mercury_business") or {}).get("accounts") or []
    if ids:
        return list(ids)
    if merchants_file.exists():
        doc = yaml.safe_load(merchants_file.read_text()) or {}
        return [a["mercury_account_id"] for a in doc.get("accounts", []) if a.get("mercury_account_id")]
    return []


def monarch_rules_as_local(monarch_rules):
    """Translate Monarch's stored rules into the local rule shape so one matcher serves both.

    Monarch operators seen in the wild: text `contains` / `eq`, amount `eq` / `gt` / `lt` / `between`.
    Several criteria in one list are any-of (a merchant cannot equal two strings at once); different
    criteria fields are and-ed. Unknown operators make the rule never match and are reported so the
    mapping can be extended.
    """
    out = []
    for r in monarch_rules:
        if not r.get("setCategoryAction"):
            continue  # only category-setting rules matter here
        match = {}
        legacy = r.get("merchantCriteria") or []
        if legacy:
            specs = [{_merchant_op(c.get("operator")): c.get("value", "")} for c in legacy]
            key = "statement" if r.get("merchantCriteriaUseOriginalStatement") else "merchant"
            match[key] = specs if len(specs) > 1 else specs[0]
        names = r.get("merchantNameCriteria") or []
        if names:
            specs = [{_merchant_op(c.get("operator")): c.get("value", "")} for c in names]
            match["merchant"] = specs if len(specs) > 1 else specs[0]
        stmts = r.get("originalStatementCriteria") or []
        if stmts:
            specs = [{_merchant_op(c.get("operator")): c.get("value", "")} for c in stmts]
            match["statement"] = specs if len(specs) > 1 else specs[0]
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
            # No criteria the query knows about. An empty match would fire on everything, so the rule
            # is kept for the report but never matches.
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
    """spec like {"contains": "x"} against text, case-insensitive; one operator per spec.

    A list of specs is any-of, the way Monarch treats several values on one criterion.
    """
    if isinstance(spec, list):
        return any(_text_match(s, text) for s in spec)
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
        on_statement = isinstance(spec, dict) and spec.get("use_original_statement")
        text = txn.get("plaidName") if on_statement else (txn.get("merchant") or {}).get("name")
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


def propose(txn, local_rules, monarch_rules, categories, history_txns, pair=None):
    """All candidates for one transaction, best first. `pair` is pair_candidate()'s verdict, if any;
    it goes first so that at equal confidence the evidence of an actual counterpart beats a text rule."""
    cands = []
    if pair:
        cands.append(dict(pair))
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


# --------------------------------------------------------------------------- transfer pairing
#
# Counterpart shape shared by both sources:
#   {"id", "amount" (negative = money out, as Monarch), "date" YYYY-MM-DD, "account_id", "account", "source"}
# source is "monarch" (a linked account) or "mercury-business" (read through the Mercury API).

def wants_pairing(txn, cfg):
    """Rows worth pairing: a non-zero amount and transfer wording in the bank text or merchant name."""
    if not cfg or not float(txn.get("amount") or 0):
        return False
    hay = f"{txn.get('plaidName') or ''} {(txn.get('merchant') or {}).get('name') or ''}".lower()
    return any(str(w).lower() in hay for w in cfg.get("words", []))


def monarch_counterparts(txns):
    out = []
    for t in txns:
        acct = t.get("account") or {}
        out.append({"id": t.get("id"), "amount": float(t.get("amount") or 0), "date": t["date"],
                    "account_id": acct.get("id"), "account": acct.get("displayName") or "", "source": "monarch"})
    return out


def mercury_counterparts(txns, account_id, account_label):
    """Mercury API rows (amount negative = money out, like Monarch; dated by postedAt, else createdAt)."""
    out = []
    for t in txns:
        when = (t.get("postedAt") or t.get("createdAt") or "")[:10]
        if not when:
            continue
        out.append({"id": t.get("id"), "amount": float(t.get("amount") or 0), "date": when,
                    "account_id": f"mercury:{account_id}", "account": account_label, "source": "mercury-business"})
    return out


def pair_candidate(txn, counterparts, cfg, categories):
    """The other side of a move, if one exists -> a candidate; None when nothing pairs.

    A counterpart has the same absolute amount, the opposite sign, another account, and a date within
    window_days. The nearest date wins and a Monarch counterpart outranks a Mercury one: a sum seen
    leaving a linked account is a transfer whatever the business accounts show. A Monarch counterpart
    makes the row a Transfer; a Mercury business counterpart makes a deposit the business category
    (income to the household). Money leaving for the business account is left alone: that is an owner
    contribution, whose category is not this step's call."""
    amt = float(txn.get("amount") or 0)
    when = date.fromisoformat(txn["date"])
    window = int(cfg.get("window_days", 2))
    own = (txn.get("account") or {}).get("id")
    best = None
    for c in counterparts:
        if c.get("account_id") == own or c.get("id") == txn.get("id"):
            continue
        if abs(abs(c["amount"]) - abs(amt)) > 0.005 or (c["amount"] < 0) == (amt < 0):
            continue
        if c["source"] != "monarch" and amt < 0:
            continue
        gap = abs((date.fromisoformat(c["date"]) - when).days)
        if gap > window:
            continue
        rank = (0 if c["source"] == "monarch" else 1, gap)
        if best is None or rank < best[0]:
            best = (rank, c)
    if best is None:
        return None
    c = best[1]
    if c["source"] == "monarch":
        cat_name, source = cfg.get("transfer_category", "Transfer"), "pair:monarch"
    else:
        cat_name, source = (cfg.get("mercury_business") or {}).get("category"), "pair:mercury-business"
    cat = categories.resolve(cat_name) if cat_name else None
    if not cat:
        return None
    return {"category_id": cat["id"], "category_name": cat["name"], "confidence": float(cfg.get("confidence", 0.95)),
            "source": source, "reason": f"other side of the move: {c['amount']:+,.2f} on {c['date']} in {c['account']}"}


def pairing_window(txns, cfg):
    """[lo, hi] ISO dates covering the rows to pair, padded by window_days on each side."""
    w = timedelta(days=int(cfg.get("window_days", 2)))
    days = [date.fromisoformat(t["date"]) for t in txns]
    return (min(days) - w).isoformat(), (max(days) + w).isoformat()


async def fetch_monarch_counterparts(mm, start, end):
    """Every Monarch transaction in the window, all accounts, reviewed or not."""
    out, offset = [], 0
    while True:
        d = await mm.get_transactions(limit=100, offset=offset, start_date=start, end_date=end)
        rows = d["allTransactions"]["results"]
        out.extend(rows)
        offset += len(rows)
        if len(rows) < 100:
            break
        if offset >= 2000:
            print(f"  warning: pairing window {start}..{end} holds more than 2000 rows; later ones not searched",
                  file=sys.stderr)
            break
    return out


def fetch_mercury_counterparts(account_ids, start, end):
    """Rows on the Mercury business accounts over the window, via the Mercury API. Problems warn and skip."""
    if not account_ids:
        return []
    try:
        import mercury_classify as mc
    except ImportError as e:
        print(f"  warning: Mercury pairing skipped: {e}", file=sys.stderr)
        return []
    token = os.getenv("MERCURY_API_TOKEN")
    if not token:
        print("  warning: Mercury pairing skipped: MERCURY_API_TOKEN not set", file=sys.stderr)
        return []
    labels = {}
    try:
        r = mc.requests.get(f"{mc.MERCURY_API}/accounts", headers={"Authorization": f"Bearer {token}"}, timeout=30)
        r.raise_for_status()
        labels = {a["id"]: a.get("nickname") or a.get("name") or a["id"] for a in r.json().get("accounts", [])}
    except Exception as e:
        print(f"  warning: Mercury account names unavailable: {e}", file=sys.stderr)
    out = []
    for aid in account_ids:
        try:
            rows = mc.fetch_mercury(aid, start, end)
        except (SystemExit, Exception) as e:
            print(f"  warning: Mercury pairing skipped for {labels.get(aid, aid)}: {e}", file=sys.stderr)
            continue
        out += mercury_counterparts(rows, aid, f"Mercury {labels.get(aid, aid)}")
    return out


# --------------------------------------------------------------------------- AI step (claude CLI)
#
# Same shape as sales-processor's payroll classify step (internal/classify): the pipeline writes a
# request file, invokes the `claude` CLI with a self-contained prompt that reads that file and writes
# a proposals file, waits for the CLI to exit, then validates the proposals against the request.
# No API key: the CLI uses the operator's Claude Code login. The intermediate files are the contract,
# so an operator can hand-edit ai_proposals.json before the review page is built.

AI_REQUEST_FILE = "ai_request.json"
AI_PROPOSALS_FILE = "ai_proposals.json"


def write_ai_request(run_dir, run_id, items, categories):
    """Snapshot of the uncertain items + the category list for Claude to read."""
    req = {
        "run_id": run_id,
        "categories": categories.public(),
        "transactions": [{
            "id": it["id"], "date": it["date"], "amount": it["amount"], "merchant": it["merchant"],
            "statement": it["plaid_name"], "account": it["account"], "current_category": it["current_category"],
            "notes": it["notes"],
            "signals": [{"category": c["category_name"], "source": c["source"], "confidence": c["confidence"],
                         "reason": c["reason"]} for c in it["candidates"]],
        } for it in items],
    }
    path = run_dir / AI_REQUEST_FILE
    path.write_text(json.dumps(req, indent=2))
    return path


def ai_prompt(request_path, proposals_path):
    """Self-contained prompt: each `claude` invocation is a fresh session with no prior context."""
    return f"""You are categorizing personal-finance transactions for a household's Monarch Money account.

Read this JSON file: {request_path}
It has shape: {{ run_id, categories[], transactions[] }}. Each transaction carries signals[]: category guesses
from local rules, Monarch's stored rules, and the merchant's past reviewed transactions (history:n/total means
n of the merchant's last total reviewed transactions carried that category).

Write your verdicts to: {proposals_path}
Use this exact shape:
{{
  "run_id": "<copy from input>",
  "proposals": [
    {{
      "transaction_id": "<transactions[].id from the request>",
      "category_id": "<one of categories[].id from the request>",
      "category_name": "<the matching categories[].name>",
      "confidence": 0.0,
      "reasoning": "<one sentence>"
    }}
  ]
}}

RULES:
1. Produce EXACTLY ONE proposal per transaction in the request. No more, no fewer.
2. category_id MUST be drawn from the request's categories[] list. Do not invent ids or names.
3. Negative amounts are money out, positive amounts are money in. Match the category's type (expense / income / transfer).
4. Weigh the signals: a strong merchant history usually wins; the current category may be a Plaid guess.
   When nothing fits with confidence, pick the closest category and report a low confidence (below 0.6); the
   human reviewer decides those.
5. Output VALID JSON only in the file: no markdown fences, no commentary.
6. After you have written the proposals file, your final message to the user MUST be exactly:
   "Classification complete — please exit (Ctrl+D or /exit) so monarch_categorize can resume the run."
   Do nothing else after writing the file; the parent process is blocked on this CLI exiting.
"""


def load_ai_proposals(path, items, categories, model_label="claude-cli"):
    """Validate Claude's proposals file against the request. Returns ({txn_id: pick}, error_or_None).

    Unknown transaction ids and unknown categories are errors, like sales-processor's Apply; a missing
    proposal for an item is reported but the other verdicts are kept.
    """
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        return {}, f"could not read {path}: {e}"
    wanted = {it["id"] for it in items}
    picks, problems = {}, []
    for p in doc.get("proposals") or []:
        tid = str(p.get("transaction_id", ""))
        if tid not in wanted:
            problems.append(f"proposal for unknown transaction {tid}")
            continue
        cat = categories.resolve(p.get("category_id") or "") or categories.resolve(p.get("category_name") or "")
        if not cat:
            problems.append(f"transaction {tid}: unknown category {p.get('category_id')!r} / {p.get('category_name')!r}")
            continue
        try:
            conf = max(0.0, min(1.0, float(p.get("confidence", 0))))
        except (TypeError, ValueError):
            conf = 0.0
        picks[tid] = {"category_id": cat["id"], "category_name": cat["name"], "confidence": conf,
                      "source": f"ai:{model_label}", "reason": str(p.get("reasoning") or "")}
    missing = wanted - set(picks)
    if missing:
        problems.append(f"no proposal for {len(missing)} transaction(s): {', '.join(sorted(missing))}")
    return picks, ("; ".join(problems) or None)


def ai_pick(run_dir, run_id, items, categories):
    """The analyze step: request file -> `claude` CLI -> proposals file. Never raises."""
    import shutil
    import subprocess

    if shutil.which("claude") is None:
        return {}, "`claude` CLI not found on PATH (install Claude Code or run with --no-ai)"
    request_path = write_ai_request(run_dir, run_id, items, categories)
    proposals_path = run_dir / AI_PROPOSALS_FILE
    try:
        proposals_path.unlink()  # a leftover from an interrupted run must not pass as fresh
    except FileNotFoundError:
        pass
    cmd = ["claude"]
    if not sys.stdin.isatty():
        cmd.append("-p")  # unattended (cron, background job): print mode instead of the interactive session
    if AI_MODEL:
        cmd += ["--model", AI_MODEL]
    cmd.append(ai_prompt(request_path, proposals_path))
    print(f"  invoking `claude` to classify {len(items)} transaction(s); exit the session when it reports completion")
    try:
        rc = subprocess.run(cmd, stdout=sys.stdout, stderr=sys.stderr).returncode
    except OSError as e:
        return {}, f"could not start `claude`: {e}"
    if rc != 0:
        return {}, f"`claude` exited with status {rc}"
    if not proposals_path.exists():
        return {}, f"Claude did not write {proposals_path.name}; re-run or use --no-ai"
    return load_ai_proposals(proposals_path, items, categories, AI_MODEL or "claude-cli")


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
        print(f"Pulled {len(rules)} rules from Monarch -> {_rel(MONARCH_RULES_FILE)}")
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

        pairing = None if args.no_pairing else load_pairing_config()
        pair_ids = {t["id"] for t in txns if wants_pairing(t, pairing)} if pairing else set()
        counterparts, pair_stats = [], {"rows": len(pair_ids), "monarch": 0, "mercury_business": 0, "window": None}
        if pair_ids:
            lo, hi = pairing_window([t for t in txns if t["id"] in pair_ids], pairing)
            counterparts = monarch_counterparts(await fetch_monarch_counterparts(mm, lo, hi))
            pair_stats["monarch"] = len(counterparts)
            business = fetch_mercury_counterparts(mercury_business_accounts(pairing), lo, hi)
            pair_stats["mercury_business"] = len(business)
            pair_stats["window"] = [lo, hi]
            counterparts += business
            print(f"Pairing: {len(pair_ids)} transfer-like row(s); searched {pair_stats['monarch']} Monarch and "
                  f"{pair_stats['mercury_business']} Mercury business row(s) in {lo}..{hi}")

        history_cache = {}
        items = []
        for t in txns:
            mname = (t.get("merchant") or {}).get("name") or ""
            if mname not in history_cache:
                history_cache[mname] = await fetch_merchant_history(mm, mname, args.history)
            pair = pair_candidate(t, counterparts, pairing, cats) if t["id"] in pair_ids else None
            cands = propose(t, local_rules, monarch_rules, cats, history_cache[mname], pair=pair)
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

        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = RUNS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "transactions.json").write_text(json.dumps(txns, indent=2))

        uncertain = [it for it in items if not it["proposed"] or it["proposed"]["confidence"] < args.ai_threshold]
        ai_error = None
        if uncertain and not args.no_ai:
            print(f"AI step: {len(uncertain)} low-confidence transaction(s) -> {_rel(run_dir) / AI_REQUEST_FILE}")
            picks, ai_error = ai_pick(run_dir, run_id, uncertain, cats)
            for it in uncertain:
                p = picks.get(it["id"])
                if p:
                    it["candidates"].insert(0, p)
                    it["proposed"] = dict(p)
            if ai_error:
                print(f"  AI step incomplete: {ai_error}\n  Rows without a verdict stay flagged for the reviewer.", file=sys.stderr)
        for it in items:
            it["needs_human"] = (not it["proposed"]) or it["proposed"]["confidence"] < args.ai_threshold
        run = {
            "run_id": run_id, "generated_at": datetime.now().isoformat(timespec="seconds"),
            "selection": {"limit": args.limit, "days": args.days, "since": args.since, "until": args.until, "all": args.all},
            "ai": {"model": AI_MODEL or "claude-cli", "threshold": args.ai_threshold,
                   "asked": len(uncertain) if not args.no_ai else 0, "error": ai_error},
            "pairing": pair_stats if pairing else None,
            "categories": cats.public(),
            "items": items,
        }
        (run_dir / "proposals.json").write_text(json.dumps(run, indent=2))
        html = TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(run))
        (run_dir / "review.html").write_text(html)

        print(f"\nRun {run_id} -> {_rel(run_dir)}/")
        print(f"{'date':10} {'amount':>10}  {'merchant':28} {'current':20} -> {'proposed':20} {'conf':>5}  source")
        for it in items:
            p = it["proposed"] or {"category_name": "?", "confidence": 0, "source": "none"}
            flag = " *" if it["needs_human"] else ""
            print(f"{it['date']:10} {it['amount']:>10.2f}  {it['merchant'][:28]:28} {it['current_category'][:20]:20} -> "
                  f"{p['category_name'][:20]:20} {p['confidence']:>5.2f}  {p['source']}{flag}")
        n_h = sum(it["needs_human"] for it in items)
        print(f"\n{len(items)} proposed, {n_h} need a human pick (*). Review: {run_dir / 'review.html'}")
        print("Then: click 'Approve and copy JSON' on the review page, "
              "task finance:categorize:approve -- latest, task finance:categorize:apply -- latest --execute")
        if args.open:
            import webbrowser
            webbrowser.open((run_dir / "review.html").resolve().as_uri())
    asyncio.run(go())


def resolve_run_dir(name):
    """A run id, or 'latest' for the newest run folder."""
    if name == "latest":
        runs = sorted(p for p in RUNS_DIR.iterdir() if p.is_dir()) if RUNS_DIR.exists() else []
        if not runs:
            sys.exit("No runs yet: task finance:categorize")
        return runs[-1]
    run_dir = RUNS_DIR / name
    if not run_dir.exists():
        sys.exit(f"No such run: {run_dir}")
    return run_dir


def read_clipboard():
    """The clipboard text (macOS pbpaste; Linux xclip/wl-paste). Returns None when unavailable."""
    import shutil
    import subprocess
    for cmd in (["pbpaste"], ["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"]):
        if shutil.which(cmd[0]):
            try:
                return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
            except (OSError, subprocess.CalledProcessError):
                return None
    return None


def save_approved_from_clipboard(run_dir, run_id):
    """Write the review page's 'Approve and copy JSON' payload to <run>/approved.json after checking it."""
    text = read_clipboard()
    if not text:
        sys.exit("Clipboard is empty or unreadable. On the review page click 'Approve and copy JSON'.")
    try:
        doc = json.loads(text)
    except ValueError:
        sys.exit("Clipboard does not hold JSON. On the review page click 'Approve and copy JSON' first.")
    if not isinstance(doc, dict) or "decisions" not in doc:
        sys.exit("Clipboard JSON is not an approved-decisions export (no 'decisions' key).")
    if doc.get("run_id") and doc["run_id"] != run_id:
        sys.exit(f"Clipboard holds decisions for run {doc['run_id']}, not {run_id}.")
    path = run_dir / "approved.json"
    path.write_text(json.dumps(doc, indent=2))
    print(f"Saved {len(doc['decisions'])} decision(s) -> {_rel(path)}")
    return path


def load_approved(run_dir, path):
    """approved.json: {"decisions": {txn_id: {"category_id", "category_name", "apply": bool}}}.

    With no path given, <run>/approved.json is used when it exists; otherwise every proposal is treated
    as approved as proposed (useful for dry-run previews).
    """
    run = json.loads((run_dir / "proposals.json").read_text())
    if not path and (run_dir / "approved.json").exists():
        path = run_dir / "approved.json"
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
    run_dir = resolve_run_dir(args.run)
    run_id = run_dir.name
    if args.from_clipboard:
        args.approved = str(save_approved_from_clipboard(run_dir, run_id))
    run, decisions = load_approved(run_dir, args.approved)
    plan = plan_updates(run, decisions, mark_reviewed=not args.no_mark_reviewed)
    if args.approved:
        src = args.approved
    elif (run_dir / "approved.json").exists():
        src = "approved.json"
    else:
        src = "proposals as-is (nothing approved yet)"
    print(f"Run {run_id}: {len(plan)} update(s) from {src}")
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
        print(f"\nDry run. To send these to Monarch: task finance:categorize:apply -- {args.run} --execute")
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
        print(f"\nApplied {ok}/{len(plan)}; log: {_rel(log)}")
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
    rn.add_argument("--no-pairing", action="store_true", help="skip the transfer pairing step (Monarch + Mercury API)")
    rn.add_argument("--ai-threshold", type=float, default=0.8, help="confidence below which the AI is asked (default 0.8)")
    rn.add_argument("--history", type=int, default=25, help="past transactions per merchant to inspect (default 25)")
    rn.add_argument("--open", action="store_true", help="open the review page in the browser when done")

    ra = sub.add_parser("apply", help="send approved decisions to Monarch")
    ra.add_argument("run", help="run id (folder name under data/categorize/runs/), or 'latest'")
    ra.add_argument("--approved", help="approved.json exported from the review page (default: <run>/approved.json if present)")
    ra.add_argument("--from-clipboard", action="store_true",
                    help="take the review page's 'Approve and copy JSON' payload from the clipboard and save it as <run>/approved.json")
    ra.add_argument("--execute", action="store_true", help="actually update Monarch (default: dry run)")
    ra.add_argument("--no-mark-reviewed", action="store_true", help="leave the needs-review flag as is")

    args = ap.parse_args()
    if MonarchMoney is None:
        sys.exit("monarchmoney / gql not installed: task finance:install")
    {"rules": cmd_rules_pull, "run": cmd_run, "apply": cmd_apply}[args.cmd](args)


if __name__ == "__main__":
    main()
