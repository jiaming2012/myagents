"""monarch_categorize: rule matching, Monarch-rule translation, history confidence, apply planning."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json  # noqa: E402

from monarch_categorize import (  # noqa: E402
    Categories, ai_prompt, history_candidate, load_ai_proposals, monarch_rules_as_local, plan_updates, propose,
    rule_matches, write_ai_request,
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


def test_monarch_rule_new_criteria_fields_and_any_of_lists():
    raw = [
        # newer web-app rule: merchant name AND original statement, both must hold
        {"id": "r3", "merchantCriteria": None, "merchantNameCriteria": [{"operator": "eq", "value": "bank of america"}],
         "originalStatementCriteria": [{"operator": "contains", "value": "chk 1778"}],
         "setCategoryAction": {"id": "c-transfer", "name": "Transfer"}},
        # legacy rule with two merchant values: any-of
        {"id": "r4", "merchantCriteria": [{"operator": "eq", "value": "transfer to checking 1778"},
                                           {"operator": "contains", "value": "chk 1778"}],
         "setCategoryAction": {"id": "c-transfer", "name": "Transfer"}},
        # legacy rule flagged to match the original statement
        {"id": "r5", "merchantCriteriaUseOriginalStatement": True,
         "merchantCriteria": [{"operator": "contains", "value": "wire"}],
         "setCategoryAction": {"id": "c-transfer", "name": "Transfer"}},
    ]
    r3, r4, r5 = monarch_rules_as_local(raw)
    assert r3["match"] == {"merchant": {"equals": "bank of america"}, "statement": {"contains": "chk 1778"}}
    boa = txn(merchant={"name": "Bank of America"}, plaidName="Online Banking transfer to CHK 1778")
    assert rule_matches(r3, boa)
    assert not rule_matches(r3, txn(merchant={"name": "Bank of America"}, plaidName="Zelle payment"))
    assert r4["match"] == {"merchant": [{"equals": "transfer to checking 1778"}, {"contains": "chk 1778"}]}
    assert rule_matches(r4, txn(merchant={"name": "Transfer to CHK 1778 confirmation"}))
    assert not rule_matches(r4, txn(merchant={"name": "Transfer to checking"}))
    assert r5["match"] == {"statement": {"contains": "wire"}}
    assert rule_matches(r5, txn(plaidName="Incoming Wire")) and not rule_matches(r5, txn())


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


def _item(i, **over):
    base = {"id": f"t{i}", "date": "2026-11-16", "amount": -5.0, "merchant": "M", "plaid_name": "M*", "account": "A",
            "current_category": "Restaurants & Bars", "notes": "", "candidates": []}
    base.update(over)
    return base


def test_ai_request_and_prompt_are_self_contained(tmp_path):
    items = [_item(1, candidates=[{"category_name": "Groceries", "source": "history:2/3", "confidence": 0.44, "reason": "r"}])]
    req = write_ai_request(tmp_path, "run1", items, CATS)
    doc = json.loads(req.read_text())
    assert doc["run_id"] == "run1" and [c["name"] for c in doc["categories"]] == ["Groceries", "Restaurants & Bars", "Transfer"]
    assert doc["transactions"][0]["signals"][0]["source"] == "history:2/3"
    prompt = ai_prompt(req, tmp_path / "ai_proposals.json")
    assert str(req) in prompt and "ai_proposals.json" in prompt and "EXACTLY ONE proposal" in prompt


def test_load_ai_proposals_validates_like_apply(tmp_path):
    items = [_item(1), _item(2)]
    p = tmp_path / "ai_proposals.json"
    p.write_text(json.dumps({"run_id": "run1", "proposals": [
        {"transaction_id": "t1", "category_id": "c-groceries", "category_name": "Groceries", "confidence": 1.7, "reasoning": "ok"},
        {"transaction_id": "t2", "category_id": "nope", "category_name": "Not A Category", "confidence": 0.9, "reasoning": "x"},
        {"transaction_id": "t9", "category_id": "c-groceries", "category_name": "Groceries", "confidence": 0.9, "reasoning": "x"},
    ]}))
    picks, err = load_ai_proposals(p, items, CATS, "test-model")
    assert list(picks) == ["t1"]
    assert picks["t1"]["confidence"] == 1.0 and picks["t1"]["source"] == "ai:test-model"
    assert "unknown category" in err and "unknown transaction t9" in err and "no proposal for 1" in err


def test_load_ai_proposals_falls_back_to_category_name():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ai_proposals.json"
        p.write_text(json.dumps({"proposals": [{"transaction_id": "t1", "category_id": "", "category_name": "groceries",
                                                 "confidence": "0.8", "reasoning": "name only"}]}))
        picks, err = load_ai_proposals(p, [_item(1)], CATS)
        assert picks["t1"]["category_id"] == "c-groceries" and picks["t1"]["confidence"] == 0.8 and err is None


def test_resolve_latest_and_default_approved_file(tmp_path, monkeypatch):
    import monarch_categorize as mc
    monkeypatch.setattr(mc, "RUNS_DIR", tmp_path)
    (tmp_path / "20260101-000000").mkdir()
    (tmp_path / "20260102-000000").mkdir()
    latest = mc.resolve_run_dir("latest")
    assert latest.name == "20260102-000000"
    assert mc.resolve_run_dir("20260101-000000").name == "20260101-000000"
    proposals = {"items": [{"id": "t1", "proposed": {"category_id": "c-groceries", "category_name": "Groceries"}, "needs_human": False}]}
    (latest / "proposals.json").write_text(json.dumps(proposals))
    _, decisions = mc.load_approved(latest, None)
    assert decisions == {"t1": {"category_id": "c-groceries", "category_name": "Groceries", "apply": True}}
    (latest / "approved.json").write_text(json.dumps({"decisions": {"t1": {"category_id": "c-dining", "category_name": "Restaurants & Bars", "apply": False}}}))
    _, decisions = mc.load_approved(latest, None)  # approved.json wins once it exists
    assert decisions["t1"]["apply"] is False and decisions["t1"]["category_id"] == "c-dining"


def test_save_approved_from_clipboard_checks_payload(tmp_path, monkeypatch):
    import monarch_categorize as mc
    import pytest
    run_dir = tmp_path / "20260102-000000"
    run_dir.mkdir()
    monkeypatch.setattr(mc, "read_clipboard", lambda: "not json")
    with pytest.raises(SystemExit):
        mc.save_approved_from_clipboard(run_dir, "20260102-000000")
    monkeypatch.setattr(mc, "read_clipboard", lambda: json.dumps({"run_id": "other", "decisions": {}}))
    with pytest.raises(SystemExit):
        mc.save_approved_from_clipboard(run_dir, "20260102-000000")
    monkeypatch.setattr(mc, "read_clipboard", lambda: json.dumps({"run_id": "20260102-000000", "decisions": {"t1": {"apply": True}}}))
    path = mc.save_approved_from_clipboard(run_dir, "20260102-000000")
    assert json.loads(path.read_text())["decisions"]["t1"]["apply"] is True


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
