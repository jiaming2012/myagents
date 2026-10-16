"""Unit tests for the pure calculation functions in rewards.py."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from rewards import (  # noqa: E402
    apply_earn,
    balance_check,
    build_report,
    credit_usage,
    earn_summary,
    expiring_credits,
    load_registry,
    load_rewards,
    period_bounds,
    periods_in_year,
    resolve_cards,
    signup_progress,
)

PLAT = "Platinum Card® (...2004)"
TODAY = date(2026, 10, 15)


def _txn(d, amount, merchant=None, description="", category=None, account=PLAT, pending=False, id=None):
    return {
        "id": id or f"{d}-{merchant}-{amount}",
        "date": d, "amount": amount, "merchant": merchant, "description": description,
        "category": category, "account_name": account, "pending": pending,
    }


def _card():
    return {
        "account": "amex-platinum-2004",
        "program": "points", "currency": "MR", "cents_per_point": 1.0,
        "annual_fee": 895, "anniversary": None,
        "earn": [
            {"name": "Flights", "rate": 5, "merchant": ["DELTA AIR", "AMEX TRAVEL"],
             "cap": {"amount": 1000, "period": "annual"}},
            {"name": "Everything else", "rate": 1},
        ],
        "credits": [
            {"name": "Lululemon", "amount": 75, "period": "quarterly", "merchant": ["LULULEMON"]},
            {"name": "Uber Cash", "amount": 15, "period": "monthly", "merchant": ["UBER"]},
        ],
        "signup_bonus": None,
        "balances": [],
        "_account": {"id": "amex-platinum-2004", "name": "Platinum Card", "monarch_match": PLAT},
    }


EXCLUDE = {"category": ["Credit Card Payment"], "merchant": ["AUTOPAY"]}


# --- periods -----------------------------------------------------------------

def test_period_bounds_quarterly():
    assert period_bounds("quarterly", date(2026, 5, 3)) == (date(2026, 4, 1), date(2026, 6, 30))
    assert period_bounds("quarterly", date(2026, 12, 31)) == (date(2026, 10, 1), date(2026, 12, 31))


def test_period_bounds_monthly_and_semiannual():
    assert period_bounds("monthly", date(2026, 2, 10)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert period_bounds("semiannual", date(2026, 7, 1)) == (date(2026, 7, 1), date(2026, 12, 31))


def test_periods_in_year_counts():
    assert len(periods_in_year("monthly", 2026)) == 12
    assert [p[0] for p in periods_in_year("quarterly", 2026)] == ["2026 Q1", "2026 Q2", "2026 Q3", "2026 Q4"]
    assert periods_in_year("annual", 2026) == [("2026", date(2026, 1, 1), date(2026, 12, 31))]


# --- earn --------------------------------------------------------------------

def test_first_matching_rule_wins_and_base_rate_applies():
    txns = [
        _txn("2026-03-01", -200.0, merchant="Delta Air Lines"),
        _txn("2026-03-02", -50.0, merchant="Whole Foods"),
    ]
    allocs = apply_earn(_card(), txns)
    assert [(a["rule"], a["points"]) for a in allocs] == [("Flights", 1000.0), ("Everything else", 50.0)]


def test_merchant_match_falls_back_to_description():
    txns = [_txn("2026-03-01", -100.0, merchant=None, description="AMEX TRAVEL 12345")]
    assert apply_earn(_card(), txns)[0]["rule"] == "Flights"


def test_cap_spills_to_base_rate_within_period():
    txns = [
        _txn("2026-01-10", -800.0, merchant="Delta Air Lines"),
        _txn("2026-02-10", -400.0, merchant="Delta Air Lines"),   # 200 under cap, 200 over
        _txn("2027-01-10", -400.0, merchant="Delta Air Lines"),   # new annual period, cap resets
    ]
    allocs = apply_earn(_card(), txns)
    assert allocs[0]["points"] == 4000.0
    assert allocs[1]["points"] == 200 * 5 + 200 * 1
    assert allocs[1]["capped_spend"] == 200.0
    assert allocs[2]["points"] == 2000.0


def test_refund_claws_back_points():
    txns = [_txn("2026-03-01", 40.0, merchant="Whole Foods")]
    assert apply_earn(_card(), txns)[0]["points"] == -40.0


def test_earn_summary_totals_and_effective_rate():
    card = _card()
    card["cents_per_point"] = 2.0
    allocs = apply_earn(card, [
        _txn("2026-03-01", -100.0, merchant="Delta Air Lines"),
        _txn("2026-03-02", -100.0, merchant="Whole Foods"),
    ])
    s = earn_summary(card, allocs)
    assert s["total_points"] == 600.0
    assert s["total_value"] == 12.0
    assert s["effective_rate"] == pytest.approx(0.06)
    assert [r["rule"] for r in s["rows"]] == ["Flights", "Everything else"]


# --- credits -----------------------------------------------------------------

def test_credit_statuses_across_quarters():
    txns = [
        _txn("2026-01-15", -75.0, merchant="lululemon"),            # Q1 used
        _txn("2026-04-15", -30.0, merchant="Lululemon Athletica"),  # Q2 partial
        # Q3 nothing -> missed
        _txn("2026-10-02", -20.0, merchant="LULULEMON"),            # Q4 open
        _txn("2026-10-05", 20.0, description="LULULEMON STATEMENT CREDIT"),
    ]
    out = credit_usage(_card(), txns, 2026, TODAY)
    lulu = out[0]
    statuses = [(p["label"], p["status"]) for p in lulu["periods"]]
    assert statuses == [("2026 Q1", "used"), ("2026 Q2", "partial"), ("2026 Q3", "missed"), ("2026 Q4", "open")]
    assert lulu["periods"][1]["remaining"] == 45.0
    assert lulu["periods"][3]["posted"] == 20.0
    assert lulu["periods"][3]["days_left"] == (date(2026, 12, 31) - TODAY).days
    assert lulu["year_used"] == 125.0
    assert lulu["year_available"] == 300.0


def test_credit_spend_capped_at_amount():
    txns = [_txn("2026-01-15", -500.0, merchant="lululemon")]
    lulu = credit_usage(_card(), txns, 2026, TODAY)[0]
    assert lulu["periods"][0]["spend"] == 500.0
    assert lulu["periods"][0]["used"] == 75.0


def test_future_periods_not_reported():
    out = credit_usage(_card(), [], 2026, date(2026, 2, 1))
    uber = out[1]
    assert [p["label"] for p in uber["periods"]] == ["2026-01", "2026-02"]
    assert [p["status"] for p in uber["periods"]] == ["missed", "open"]


# --- sign-up bonus, balances -------------------------------------------------

def test_signup_progress():
    card = _card()
    card["signup_bonus"] = {"opened": date(2026, 9, 1), "spend_target": 8000, "deadline": date(2027, 3, 1), "bonus": 175000}
    txns = [_txn("2026-09-10", -3000.0, merchant="Whole Foods"), _txn("2026-08-01", -999.0, merchant="Before open")]
    sub = signup_progress(card, txns, TODAY)
    assert sub["spend"] == 3000.0
    assert sub["remaining"] == 5000.0
    assert sub["status"] == "in_progress"
    assert sub["daily_needed"] == pytest.approx(5000 / (date(2027, 3, 1) - TODAY).days)


def test_balance_expected_and_drift():
    card = _card()
    card["balances"] = [
        {"as_of": date(2026, 6, 30), "points": 10000},
        {"as_of": date(2026, 9, 30), "points": 10600},
    ]
    allocs = apply_earn(card, [
        _txn("2026-08-01", -500.0, merchant="Whole Foods"),   # 500 pts between readings
        _txn("2026-10-10", -100.0, merchant="Whole Foods"),   # 100 pts after latest
    ])
    bal = balance_check(card, allocs, TODAY)
    assert bal["earned_since"] == 100.0
    assert bal["expected_now"] == 10700.0
    assert bal["computed_delta"] == 500.0
    assert bal["actual_delta"] == 600.0
    assert bal["drift"] == 100.0


# --- end to end ---------------------------------------------------------------

def test_build_report_filters_card_excludes_payments_and_pending():
    rewards_cfg = {"exclude": EXCLUDE, "cards": [{k: v for k, v in _card().items() if k != "_account"}]}
    registry = {"amex-platinum-2004": _card()["_account"]}
    txns = [
        _txn("2026-03-01", -100.0, merchant="Whole Foods"),
        _txn("2026-03-02", 2000.0, merchant="Payment", category="Credit Card Payment"),
        _txn("2026-03-03", 300.0, description="AUTOPAY PAYMENT - THANK YOU"),
        _txn("2026-03-04", -100.0, merchant="Whole Foods", pending=True),
        _txn("2026-03-05", -100.0, merchant="Whole Foods", account="Some Other Card"),
        _txn("2025-12-31", -100.0, merchant="Whole Foods"),           # prior year
    ]
    report = build_report(rewards_cfg, registry, txns, 2026, TODAY)
    card = report["cards"][0]
    assert card["transactions"] == 1
    assert card["earn"]["total_points"] == 100.0
    assert card["total_value"] == pytest.approx(1.0 + 0.0)
    assert card["net_value"] == pytest.approx(1.0 + 0.0 - 895.0)


# --- expiring credits -----------------------------------------------------------

def test_expiring_groups_open_credits_by_deadline_soonest_first():
    card = _card()
    card["credits"].append({"name": "Walmart+", "amount": 12.95, "period": "monthly",
                            "merchant": ["WALMART+"], "enrolled": False})
    txns = [_txn("2026-10-03", -14.91, merchant="Uber")]           # $0.09 of Uber Cash left
    credits = credit_usage(card, txns, 2026, TODAY)
    groups = expiring_credits(credits, TODAY)
    assert [g["end"] for g in groups] == ["2026-10-31", "2026-12-31"]
    assert [g["days_left"] for g in groups] == [16, 77]
    assert groups[0]["items"] == [{"name": "Uber Cash", "remaining": pytest.approx(0.09)}]
    assert groups[1]["items"] == [{"name": "Lululemon", "remaining": 75.0}]
    assert groups[1]["total"] == 75.0
    names = {i["name"] for g in groups for i in g["items"]}
    assert "Walmart+" not in names, "unenrolled credits are not 'expiring'"


def test_expiring_omits_fully_used_open_periods():
    card = _card()
    txns = [_txn("2026-10-03", -20.0, merchant="Uber"), _txn("2026-10-04", -80.0, merchant="Lululemon")]
    credits = credit_usage(card, txns, 2026, TODAY)
    assert expiring_credits(credits, TODAY) == []


def test_report_summary_and_expiring_render():
    from rewards import render_text
    card = {k: v for k, v in _card().items() if k != "_account"}
    card["points_expire"] = "never while open"
    rewards_cfg = {"exclude": EXCLUDE, "cards": [card]}
    registry = {"amex-platinum-2004": _card()["_account"]}
    txns = [_txn("2026-03-01", -100.0, merchant="Whole Foods")]
    report = build_report(rewards_cfg, registry, txns, 2026, TODAY)
    r = report["cards"][0]
    assert r["expiring_total"] == pytest.approx(90.0)             # $75 Lululemon Q4 + $15 Uber Oct
    text = render_text(report, TODAY)
    assert "Total earned:         $1.00  (points + credits)" in text
    assert "MR expire:       never while open" in text
    assert " 16 days  by 2026-10-31      $15.00" in text
    assert " 77 days  by 2026-12-31      $75.00" in text
    assert "total on the table                  $90.00" in text


def test_build_report_unknown_card_fails_loudly():
    rewards_cfg = {"exclude": {}, "cards": [{"account": "nope", "earn": []}]}
    with pytest.raises(SystemExit):
        build_report(rewards_cfg, {}, [], 2026, TODAY)


def test_shipped_rewards_yaml_resolves_against_registry():
    """Every card in rewards.yaml must point at a real payments.yaml account,
    have a base rule last, and have valid credit periods."""
    cfg = load_rewards()
    cards = resolve_cards(cfg, load_registry())
    assert cards, "rewards.yaml has no cards"
    for card in cards:
        assert card["_account"]["type"] == "credit"
        last = card["earn"][-1]
        assert not last.get("merchant") and not last.get("category"), "last earn rule must be the base rate"
        for c in card.get("credits") or []:
            assert c["period"] in ("monthly", "quarterly", "semiannual", "annual")
            assert c.get("merchant"), f"credit {c['name']} has no merchant patterns"


# --- posted statement credits -------------------------------------------------

def test_posted_credit_counts_as_use_when_merchant_unrecognized():
    card = _card()
    card["credits"] = [{"name": "Resy", "amount": 100, "period": "quarterly",
                        "merchant": ["RESY"], "posted": ["PLATINUM RESY CREDIT"]}]
    txns = [
        _txn("2026-05-01", -120.0, merchant="Some Bistro"),                       # not recognized
        _txn("2026-05-16", 100.0, merchant="Resy", description="Platinum Resy Credit"),
    ]
    q2 = credit_usage(card, txns, 2026, TODAY)[0]["periods"][1]
    assert q2["spend"] == 0.0
    assert q2["posted"] == 100.0
    assert q2["used"] == 100.0
    assert q2["status"] == "used"


def test_posted_credit_sums_partial_lines():
    card = _card()
    card["credits"] = [{"name": "Digital", "amount": 25, "period": "monthly",
                        "merchant": ["HULU"], "posted": ["PLATINUM DIGITAL ENTERTAINMENT CREDIT"]}]
    txns = [
        _txn("2026-02-06", 7.46, description="Platinum Digital Entertainment Credit", id="a"),
        _txn("2026-02-06", 15.99, description="Platinum Digital Entertainment Credit", id="b"),
        _txn("2026-02-10", 1.55, description="Platinum Digital Entertainment Credit", id="c"),
    ]
    feb = credit_usage(card, txns, 2026, TODAY)[0]["periods"][1]
    assert feb["posted"] == pytest.approx(25.0)
    assert feb["status"] == "used"


def test_posted_credit_does_not_claw_back_points():
    card = _card()
    card["credits"][0]["posted"] = ["PLATINUM LULULEMON CREDIT"]
    rewards_cfg = {"exclude": EXCLUDE, "cards": [{k: v for k, v in card.items() if k != "_account"}]}
    registry = {"amex-platinum-2004": card["_account"]}
    txns = [
        _txn("2026-01-10", -100.0, merchant="lululemon"),
        _txn("2026-01-17", 75.0, merchant="Lululemon Athletica", description="Platinum Lululemon Credit"),
        _txn("2026-01-20", 10.0, merchant="lululemon", description="refund"),   # a real refund
    ]
    report = build_report(rewards_cfg, registry, txns, 2026, TODAY)
    assert report["cards"][0]["earn"]["total_points"] == 90.0
