"""monarch_categorize: rule matching, Monarch-rule translation, history confidence, apply planning."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monarch_categorize import (  # noqa: E402
    Categories, history_candidate, monarch_rules_as_local, plan_updates, propose, rule_matches,
)

CATS = Categories([
    {"id": "c-transfer", "name": "Transfer", "group": {"name": "Transfers", "type": "transfer"}},
    {"id": "c-groceries", "name": "Groceries", "group": {"name": "Food & Dining", "type": "expense"}},
    {"id": "c-dining", "name": "Restaurants & Bars", "group": {"name": "Food & Dining", "type": "expense"}},
])


def txn(**over):
    base = {"id": "t1", "date": "2026-11-16", "amount": -42.10, "plaidName": "TRADER JOE S #123",
            "needsReview": True, "merchant": {"name": "Trader Joe's"},
            "account": {"id": "a1", "displayName": "CASHBACK DEBIT (...8206)"},
            "category": {"id": "c-dining", "name": "Restaurants & Bars"}}
    base.update(over)
    return base


def test_merchant_contains_is_case_insensitive():
    rule = {"match": {"merchant": {"contains": "trader joe"}}}
    assert rule_matches(rule, txn())
    assert not rule_matches(rule, txn(merchant={"name": "Whole Foods"}))


def test_statement_and_amount_clauses():
    rule = {"match": {"statement": {"starts_with": "TRADER"}, "amount": {"is_expense": True, "between": [10, 100]}}}
    assert rule_matches(rule, txn())
    assert not rule_matches(rule, txn(amount=300.0))      # money in
    assert not rule_matches(rule, txn(amount=-150.0))     # outside range


def test_account_contains_and_unknown_operator_never_matches():
    assert rule_matches({"match": {"account": {"contains": "8206"}}}, txn())
    assert not rule_matches({"match": {"merchant": {"_unknown:foo": "x"}}}, txn())


def test_monarch_rule_translation():
    raw = [{"id": "r1", "merchantCriteriaUseOriginalStatement": False,
            "merchantCriteria": [{"operator": "contains", "value": "Trader"}],
            "amountCriteria": {"operator": "lt", "isExpense": True, "value": 500, "valueRange": None},
            "categoryIds": [], "accountIds": ["a1"],
            "setCategoryAction": {"id": "c-groceries", "name": "Groceries"}},
           {"id": "r2", "merchantCriteria": [], "setCategoryAction": None}]  # no category action: skipped
    local = monarch_rules_as_local(raw)
    assert len(local) == 1
    r = local[0]
    assert r["name"] == "monarch-rule:r1"
    assert r["match"] == {"merchant": {"contains": "Trader"}, "amount": {"is_expense": True, "less_than": 500},
                          "account": {"id_in": ["a1"]}}
    assert rule_matches(r, txn())


def test_history_confidence_scales_with_sample_size():
    hist = [txn(id=f"h{i}", needsReview=False, category={"id": "c-groceries", "name": "Groceries"}) for i in range(2)]
    hist.append(txn(id="t1"))  # the transaction itself is ignored
    h = history_candidate(hist, "t1")
    assert h["category_id"] == "c-groceries"
    assert h["confidence"] == round(1.0 * (2 / 3), 3)  # unanimous but only 2 samples
    hist += [txn(id=f"g{i}", needsReview=False, category={"id": "c-groceries", "name": "Groceries"}) for i in range(3)]
    assert history_candidate(hist, "t1")["confidence"] == 1.0


def test_propose_orders_candidates_and_local_rule_wins():
    local = [{"name": "tj", "match": {"merchant": {"contains": "trader"}}, "set": {"category": "Groceries"}}]
    cands = propose(txn(), local, [], CATS, [])
    assert cands[0]["source"] == "rule:tj" and cands[0]["category_id"] == "c-groceries"
    assert cands[-1]["source"] == "monarch-default"


def test_plan_updates_only_changed_or_unreviewed():
    run = {"items": [
        {"id": "t1", "date": "d", "amount": -1.0, "merchant": "A", "current_category": "Restaurants & Bars",
         "current_category_id": "c-dining", "needs_review": True},
        {"id": "t2", "date": "d", "amount": -1.0, "merchant": "B", "current_category": "Groceries",
         "current_category_id": "c-groceries", "needs_review": False},
        {"id": "t3", "date": "d", "amount": -1.0, "merchant": "C", "current_category": "Groceries",
         "current_category_id": "c-groceries", "needs_review": True},
    ]}
    decisions = {"t1": {"category_id": "c-groceries", "category_name": "Groceries", "apply": True},
                 "t2": {"category_id": "c-groceries", "category_name": "Groceries", "apply": True},  # no-op
                 "t3": {"category_id": "c-groceries", "category_name": "Groceries", "apply": False}}  # not approved
    plan = plan_updates(run, decisions)
    assert [p["id"] for p in plan] == ["t1"]
    assert plan[0]["category_id"] == "c-groceries" and plan[0]["needs_review"] is False
    assert plan_updates(run, decisions, mark_reviewed=False)[0]["needs_review"] is None
