"""Unit tests for the pure functions in debt_plan.py (no network)."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from debt_plan import (  # noqa: E402
    amortized_payment,
    build_debts,
    headroom_by_account,
    monarch_lookup,
    months_until,
    owed_balance,
    pool_headroom,
    promo_action_line,
    promo_statuses,
    simulate,
    solve_monthly_extra,
    xero_shaped_from_zoho,
)

START = date(2026, 11, 1)


def debt(id, balance, apr, min_payment, category="personal", carried=True):
    return {"id": id, "name": id, "institution": "", "type": "credit", "category": category,
            "balance": balance, "source": "monarch", "apr": apr, "min_payment": min_payment,
            "funding_account": None, "carried": carried, "paid_in_full": not carried, "assumed": [], "notes": ""}


# --- simulation --------------------------------------------------------------


def test_single_debt_zero_apr_pays_off_in_balance_over_min_months():
    r = simulate([debt("a", 1000, 0.0, 100)], start=START)
    assert r["months"] == 10
    assert r["total_interest"] == 0.0
    assert r["payoff"] == {"a": 10}
    assert r["schedule"][0]["date"] == "2026-11"


def test_interest_accrues_monthly_at_apr_over_12():
    r = simulate([debt("a", 1200, 12.0, 1300)], start=START)
    # one month: 1% of 1200 = 12 interest, then paid in full
    assert r["months"] == 1
    assert r["total_interest"] == 12.0


def test_minimum_below_interest_never_finishes():
    r = simulate([debt("a", 5000, 24.0, 50)], start=START)   # interest 100/mo > 50 min
    assert r["months"] is None
    assert r["unfinished"] == ["a"]


def test_avalanche_targets_highest_apr_first_and_rolls_minimums():
    debts = [debt("low", 1000, 10.0, 50), debt("high", 1000, 30.0, 50)]
    r = simulate(debts, monthly_extra=500, start=START)
    assert r["payoff"]["high"] < r["payoff"]["low"]
    # with rollover the second debt gets the first's minimum too
    r_no = simulate(debts, monthly_extra=500, rollover=False, start=START)
    assert r_no["months"] >= r["months"]


def test_snowball_targets_smallest_balance_first():
    debts = [debt("big", 5000, 30.0, 100), debt("small", 500, 5.0, 25)]
    r = simulate(debts, monthly_extra=300, strategy="snowball", start=START)
    assert r["payoff"]["small"] < r["payoff"]["big"]


def test_extra_reduces_interest_and_months():
    debts = [debt("a", 5000, 24.0, 150)]
    base = simulate(debts, 0, start=START)
    plan = simulate(debts, 400, start=START)
    assert plan["months"] < base["months"]
    assert plan["total_interest"] < base["total_interest"]


def test_lump_sum_only_hits_its_own_category():
    debts = [debt("biz", 1000, 20.0, 50, category="business"), debt("per", 1000, 20.0, 50, category="personal")]
    r = simulate(debts, 0, lump_sum={"business": 1000}, start=START)
    assert r["payoff"]["biz"] == 1
    assert r["payoff"]["per"] > 1


def test_uncarried_debts_are_ignored():
    r = simulate([debt("full", 2000, 25.0, 2000, carried=False)], start=START)
    assert r["months"] is None and r["payoff"] == {} and r["schedule"] == []


def test_solver_finds_monthly_extra_for_target():
    debts = [debt("a", 6000, 20.0, 120)]
    need = solve_monthly_extra(debts, 12, start=START)
    assert need is not None and need > 0
    ok = simulate(debts, need, start=START)
    assert ok["months"] <= 12
    tight = simulate(debts, need - 25, start=START)
    assert tight["months"] is None or tight["months"] > 12


def test_months_until():
    assert months_until("2027-11", START) == 12
    assert months_until("2026-11", START) == 0


# --- inputs ------------------------------------------------------------------


def test_monarch_lookup_prefers_live_copy_over_dead_duplicate():
    accts = [
        {"name": "Apple Cash", "balance": 1.11, "type": "depository", "hidden": False, "deactivated": None, "updated": "2025-01-23"},
        {"name": "Apple Cash", "balance": 6.96, "type": "depository", "hidden": False, "deactivated": None, "updated": "2026-10-16"},
        {"name": "Old Card", "balance": 0, "type": "credit", "hidden": True, "deactivated": "2025-05-16", "updated": "2025-01-01"},
        {"name": "Old Card", "balance": -50, "type": "credit", "hidden": False, "deactivated": None, "updated": "2026-10-16"},
    ]
    m = monarch_lookup(accts)
    assert m["Apple Cash"]["balance"] == 6.96
    assert m["Old Card"]["balance"] == -50


def test_owed_balance_from_zoho_then_monarch_then_last4():
    zoho = {"Chase Ink ••7667": {"book_balance": 5708.07, "type": "credit_card"}}
    monarch = {"BankAmericard (...5153)": {"balance": -5494.7}, "Discover it Card (...9035)": {"balance": 1403.12}}
    assert owed_balance({"zoho_match": "Chase Ink ••7667"}, monarch, zoho) == (5708.07, "zoho")
    assert owed_balance({"monarch_match": "BankAmericard (...5153)"}, monarch, zoho) == (5494.7, "monarch")
    assert owed_balance({"monarch_match": "Discover it Card (...9035)"}, monarch, zoho) == (0.0, "monarch")  # credit balance
    assert owed_balance({"last4": "5153"}, monarch, zoho) == (5494.7, "monarch")
    assert owed_balance({"last4": "0000"}, monarch, zoho) == (None, None)


def test_xero_shaped_from_zoho_negates_credit_cards():
    config = {"accounts": [
        {"id": "boa", "xero_account_id": "BoA: Business Checking (1778)", "zoho_match": "Business Adv Fundamentals"},
        {"id": "corp", "xero_account_id": "BoA: CORP", "zoho_match": "CORP"},
        {"id": "nomap", "xero_account_id": "X"},
    ]}
    zoho = {"Business Adv Fundamentals": {"book_balance": 52.16, "type": "bank"}, "CORP": {"book_balance": 1651.98, "type": "credit_card"}}
    out = xero_shaped_from_zoho(config, zoho)
    assert out["BoA: Business Checking (1778)"]["balance"] == 52.16
    assert out["BoA: CORP"]["balance"] == -1651.98
    assert "X" not in out


def test_build_debts_enriches_from_registry_and_overrides():
    config = {
        "accounts": [
            {"id": "card-min", "name": "Min Card", "type": "credit", "category": "personal", "monarch_match": "Min Card"},
            {"id": "card-full", "name": "Full Card", "type": "credit", "category": "personal", "monarch_match": "Full Card"},
            {"id": "loan", "name": "Loan", "type": "loan", "category": "personal", "monarch_match": "Loan"},
            {"id": "biz", "name": "Biz Card", "type": "credit", "category": "business", "zoho_match": "Biz"},
            {"id": "closed", "name": "Closed", "type": "credit", "inactive": True, "monarch_match": "Closed"},
            {"id": "ghost", "name": "Ghost", "type": "credit", "monarch_match": "Nope"},
            {"id": "zero", "name": "Zero", "type": "credit", "monarch_match": "Zero"},
        ],
        "payments": [
            {"name": "min pay", "amount": 137.0, "pays_account": "card-min", "funding_account": "chk", "autopay_type": "min"},
            {"name": "full pay", "amount": 0.0, "pays_account": "card-full", "funding_account": "chk", "autopay_type": "full"},
            {"name": "loan pay", "amount": 702.91, "pays_account": "loan", "funding_account": "chk"},
        ],
    }
    monarch = {"Min Card": {"balance": -5494.7}, "Full Card": {"balance": -4411.26}, "Loan": {"balance": -10544.83},
               "Closed": {"balance": -491.3}, "Zero": {"balance": 0.0}}
    zoho = {"Biz": {"book_balance": 5708.07, "type": "credit_card"}}
    debts_cfg = {"debts": [{"account": "biz", "apr": 23.6, "min_payment": 170, "funding_account": "boa"},
                           {"account": "loan", "apr": 7.5}]}
    debts, notes = build_debts(config, debts_cfg, monarch, zoho)
    by = {d["id"]: d for d in debts}
    assert set(by) == {"card-min", "card-full", "loan", "biz"}
    assert by["card-min"]["min_payment"] == 137.0 and by["card-min"]["funding_account"] == "chk"
    assert by["card-min"]["assumed"] == ["apr"] and by["card-min"]["apr"] == 24.99
    assert by["card-full"]["carried"] is False and by["card-full"]["paid_in_full"] is True
    assert by["loan"]["apr"] == 7.5 and by["loan"]["min_payment"] == 702.91 and by["loan"]["assumed"] == []
    assert by["biz"]["source"] == "zoho" and by["biz"]["balance"] == 5708.07 and by["biz"]["funding_account"] == "boa"
    assert debts[0]["id"] == "card-min"           # sorted by APR desc
    assert any("Ghost" in n for n in notes)


def test_carried_override_puts_full_card_in_plan():
    config = {"accounts": [{"id": "c", "name": "C", "type": "credit", "monarch_match": "C"}],
              "payments": [{"name": "p", "amount": 0.0, "pays_account": "c", "funding_account": "k", "autopay_type": "full"}]}
    debts, _ = build_debts(config, {"debts": [{"account": "c", "carried": True, "apr": 28.0}]}, {"C": {"balance": -100}}, {})
    assert debts[0]["carried"] is True and debts[0]["assumed"] == ["min"] and debts[0]["min_payment"] == 25.0


# --- headroom ----------------------------------------------------------------


def _fc(id, name, bal, payments, floor, source="monarch"):
    return {"id": id, "name": name, "current_balance": bal, "balance_source": source, "stale": False,
            "payments": [{"name": f"p{i}", "amount": a, "due_date": date(2026, 11, 5)} for i, a in enumerate(payments)],
            "projected_balance": (bal or 0) - sum(payments), "min_balance": floor}


def test_headroom_and_pools():
    config = {"accounts": [{"id": "p1", "category": "personal"}, {"id": "b1", "category": "business"},
                           {"id": "b2", "category": "business"}, {"id": "u", "category": "personal"}]}
    forecast = {"accounts": [
        _fc("p1", "Recurring ACH", 3068.53, [137, 78, 702.91, 615.64], 500),
        _fc("b1", "BoA Fundamentals", 52.16, [170, 10], 500),
        _fc("b2", "Outgoing ACH", 450.18, [0], 0),
        _fc("u", "Mercury Personal", None, [], 500, source="unknown"),
    ]}
    rows = headroom_by_account(forecast, config)
    by = {r["id"]: r for r in rows}
    assert by["p1"]["headroom"] == round(3068.53 - 1533.55 - 500, 2) and by["p1"]["status"] == "ok"
    assert by["b1"]["status"] == "overdraft" and by["b1"]["headroom"] < 0
    assert by["u"]["headroom"] is None and by["u"]["status"] == "unknown"
    pools = pool_headroom(rows)
    assert pools["personal"]["safe"] == by["p1"]["headroom"]
    assert pools["personal"]["unknown"] == ["Mercury Personal"]
    assert pools["business"]["safe"] == 450.18
    assert [r["id"] for r in pools["business"]["at_risk"]] == ["b1"]


# --- promotional rates -------------------------------------------------------

TODAY = date(2026, 11, 14)
PROMO_CFG = {"promos": [{"account": "citi", "apr": 9.99, "until": date(2027, 7, 1), "standard_apr": 28.74,
                         "activate_by": date(2026, 12, 28), "activated": True, "credit_limit": 10000,
                         "target_utilization": 30}]}
PROMO_CONFIG = {
    "accounts": [{"id": "citi", "name": "Strata", "institution": "Citi", "last4": "8281", "type": "credit",
                  "category": "personal", "monarch_match": "Citi Strata Card (...8281)"},
                 {"id": "boa", "name": "BoA Plat", "type": "credit", "category": "personal", "monarch_match": "BoA"}],
    "payments": [{"name": "Citi (full)", "pays_account": "citi", "autopay_type": "full", "amount": 556.68,
                  "funding_account": "cap1"}],
}
MONARCH = {"Citi Strata Card (...8281)": {"balance": -1000.0}, "BoA": {"balance": -4000.0}}


def test_promo_card_simulates_at_promo_rate_then_standard_rate():
    d = debt("citi", 1000, 9.99, 10)
    d.update(promo_until=date(2027, 1, 1), apr_after=28.74)
    r = simulate([d], start=START)
    # Nov + Dec at 9.99%, Jan at 28.74%
    assert r["schedule"][0]["interest"] == round(1000 * 9.99 / 1200, 2)
    assert r["schedule"][2]["interest"] > r["schedule"][1]["interest"] * 2.5


def test_promo_rate_end_retargets_avalanche():
    promo = debt("citi", 1000, 9.99, 25)
    promo.update(promo_until=date(2027, 2, 1), apr_after=28.74)
    other = debt("boa", 5000, 24.99, 100)
    r = simulate([promo, other], monthly_extra=300, start=START)
    # once Citi jumps to 28.74% it outranks the 24.99% card and is cleared first
    assert r["payoff"]["citi"] < r["payoff"]["boa"]


def test_build_debts_carries_promo_card_despite_full_autopay():
    debts, _ = build_debts(PROMO_CONFIG, PROMO_CFG, MONARCH, {}, today=TODAY)
    citi = next(d for d in debts if d["id"] == "citi")
    assert citi["carried"] and citi["apr"] == 9.99 and citi["apr_after"] == 28.74
    assert citi["promo_until"] == date(2027, 7, 1)


def test_build_debts_ignores_expired_promo():
    debts, _ = build_debts(PROMO_CONFIG, PROMO_CFG, MONARCH, {}, today=date(2027, 7, 1))
    citi = next(d for d in debts if d["id"] == "citi")
    assert not citi["carried"] and citi["promo_until"] is None


def test_amortized_payment():
    assert amortized_payment(1200, 0.0, 12) == 100.0
    assert amortized_payment(0, 9.99, 3) == 0.0
    assert 3030 < amortized_payment(3000, 9.99, 3) * 3 < 3060


def test_promo_status_fill_phase_room_cap_and_target():
    debts, _ = build_debts(PROMO_CONFIG, PROMO_CFG, MONARCH, {}, today=TODAY)
    [s] = promo_statuses(PROMO_CONFIG, PROMO_CFG, MONARCH, {}, debts, today=TODAY)
    assert s["phase"] == "fill"
    assert s["cap"] == 3000.0 and s["room"] == 2000.0 and s["utilization"] == 10.0
    assert s["stop_adding"] == date(2027, 4, 1)
    assert s["payoff_months"] == 3 and s["payoff_balance"] == 3000.0
    assert s["target"]["id"] == "boa"
    assert s["est_savings"] > 0
    assert s["autopay_full"]          # registry still says pay in full
    assert "autopay" in promo_action_line(s)


def test_promo_status_payoff_phase_and_unknown_limit():
    cfg = {"promos": [dict(PROMO_CFG["promos"][0], credit_limit=None)]}
    config = dict(PROMO_CONFIG, payments=[])
    [s] = promo_statuses(config, cfg, MONARCH, {}, [], today=TODAY)
    assert s["room"] is None and "credit_limit" in promo_action_line(s)
    [s] = promo_statuses(config, PROMO_CFG, MONARCH, {}, [], today=date(2027, 5, 10))
    assert s["phase"] == "payoff" and s["payoff_months"] == 2
    assert s["payoff_payment"] == amortized_payment(1000.0, 9.99, 2)
    assert "no new charges" in promo_action_line(s)


def test_promo_status_room_line_when_autopay_fixed():
    config = dict(PROMO_CONFIG, payments=[])
    [s] = promo_statuses(config, PROMO_CFG, MONARCH, {}, [], today=TODAY)
    assert "$2,000.00 of room" in promo_action_line(s)
