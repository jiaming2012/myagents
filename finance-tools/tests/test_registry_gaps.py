"""Unit tests for the pure checks in registry_gaps.py."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from registry_gaps import (  # noqa: E402
    check_calendar_coverage,
    check_liabilities_without_payment,
    check_monarch_stale,
    check_monarch_unknown_liabilities,
    check_registry_closed_in_monarch,
    check_sources,
    check_xero_staleness,
    norm_name,
    payment_pays_account,
    run_checks,
)
from coverage_report import resolve_balance  # noqa: E402


def _config():
    return {
        "accounts": [
            {"id": "chk-1", "name": "Checking", "type": "depository"},
            {"id": "card-a", "name": "Card A", "type": "credit", "nicknames": ["Alpha Card"],
             "monarch_match": "Card A (...1111)"},
            {"id": "card-b", "name": "Card B", "type": "credit", "nicknames": ["Bravo"],
             "monarch_match": "Card B (...2222)"},
            {"id": "card-old", "name": "Old Card", "type": "credit", "inactive": True,
             "monarch_match": "Old Card (...9999)"},
            {"id": "loan-1", "name": "Auto Loan", "type": "loan", "nicknames": ["Truist Auto"],
             "monarch_match": "Auto Loan (...3333)"},
            {"id": "loan-2", "name": "Personal Loan", "type": "loan",
             "monarch_match": "Personal Loan (...4444)"},
        ],
        "payments": [
            {"name": "Alpha Card (full)", "day_of_month": 5, "autopay_type": "full",
             "funding_account": "chk-1", "zoho_match": "Alpha"},
            {"name": "Truist Auto Loan", "day_of_month": 18, "autopay_type": None,
             "funding_account": "chk-1", "zoho_match": "Truist"},
            {"name": "Mystery", "day_of_month": 1, "autopay_type": "full",
             "funding_account": "chk-1", "pays_account": "card-b", "zoho_match": None},
            {"name": "Netflix", "day_of_month": 9, "autopay_type": None,
             "funding_account": "chk-1", "zoho_match": "Netflix"},
        ],
    }


def test_payment_pays_account_explicit_and_heuristic():
    c = _config()
    accts = c["accounts"]
    assert payment_pays_account(c["payments"][0], accts) == "card-a"      # nickname + autopay_type
    assert payment_pays_account(c["payments"][1], accts) == "loan-1"      # loan: nickname only
    assert payment_pays_account(c["payments"][2], accts) == "card-b"      # explicit pays_account
    assert payment_pays_account(c["payments"][3], accts) is None          # plain bill


def test_liabilities_without_payment_skips_inactive_and_covered():
    gaps = check_liabilities_without_payment(_config())
    ids = {g["message"].split("(")[1].split(",")[0] for g in gaps}
    assert ids == {"loan-2"}
    assert gaps[0]["severity"] == "error"
    assert "pays_account: loan-2" in gaps[0]["fix"]


def test_monarch_unknown_liabilities_ignores_known_hidden_deactivated_and_assets():
    monarch = {
        "Card A (...1111)": {"type": "credit", "balance": -10, "hidden": False, "deactivated": None},
        "New Visa (...5555)": {"type": "credit", "balance": -4411.26, "hidden": False, "deactivated": None},
        "Dead Card": {"type": "credit", "balance": 0, "hidden": True, "deactivated": "2026-01-01"},
        "Savings": {"type": "depository", "balance": 100, "hidden": False, "deactivated": None},
    }
    gaps = check_monarch_unknown_liabilities(_config(), monarch)
    assert len(gaps) == 1
    assert "New Visa (...5555)" in gaps[0]["message"]
    assert "$4,411.26" in gaps[0]["message"]
    assert check_monarch_unknown_liabilities(_config(), None) == []


def test_norm_name_ignores_symbols_and_case():
    assert norm_name("Platinum Card\u00ae (...2004)") == norm_name("platinum card (...2004)")
    assert norm_name("  Delta SkyMiles\u00ae  Gold Card ") == "delta skymiles gold card"
    assert norm_name(None) == ""


def test_monarch_unknown_liabilities_matches_through_symbols():
    cfg = _config()
    cfg["accounts"][1]["monarch_match"] = "Card A (...1111)"
    monarch = {"Card A\u00ae (...1111)": {"type": "credit", "balance": -5, "hidden": False, "deactivated": None}}
    assert check_monarch_unknown_liabilities(cfg, monarch) == []


def test_monarch_stale_balance():
    today = date(2026, 10, 15)
    monarch = {
        "Card A (...1111)": {"type": "credit", "balance": -10, "hidden": True, "deactivated": None,
                             "updated": "2025-03-23T04:00:00+00:00"},
        "Card B (...2222)": {"type": "credit", "balance": 0, "hidden": False, "deactivated": None,
                             "updated": "2026-10-14T09:00:00+00:00"},
    }
    gaps = check_monarch_stale(_config(), monarch, threshold_days=7, today=today)
    assert len(gaps) == 1 and "Card A" in gaps[0]["message"] and "2025-03-23" in gaps[0]["message"]
    assert gaps[0]["severity"] == "error"


def test_resolve_balance_never_returns_silent_zero_when_source_is_down():
    acct = {"id": "x", "monarch_match": "Card A (...1111)", "last4": "1111"}
    assert resolve_balance(acct, {}, {}) == (None, None)
    monarch = {"Card A\u00ae (...1111)": {"balance": -42.0, "hidden": False, "deactivated": None}}
    assert resolve_balance(acct, monarch, {}) == (-42.0, "monarch")
    # last4 fallback prefers the active copy over a hidden/deactivated duplicate
    monarch = {
        "Old (...1111)": {"balance": 480.57, "hidden": True, "deactivated": None},
        "Live (...1111)": {"balance": 421.53, "hidden": False, "deactivated": None},
    }
    acct2 = {"id": "y", "monarch_match": "Nope (...1111)", "last4": "1111"}
    assert resolve_balance(acct2, monarch, {}) == (421.53, "monarch")
    # Xero: whole source empty -> unknown; source up but account absent -> 0.0
    biz = {"id": "b", "xero_account_id": "Mercury: Ops"}
    assert resolve_balance(biz, {}, {}) == (None, None)
    assert resolve_balance(biz, {}, {"Other": {"balance": 1.0}}) == (0.0, "xero")


def test_registry_closed_in_monarch():
    monarch = {
        "Card A (...1111)": {"type": "credit", "balance": -10, "hidden": False, "deactivated": None},
        "Card B (...2222)": {"type": "credit", "balance": 0, "hidden": True, "deactivated": "2026-02-28T00:00"},
        # Auto Loan (...3333) missing entirely; Personal Loan present
        "Personal Loan (...4444)": {"type": "loan", "balance": -20000, "hidden": False, "deactivated": None},
    }
    gaps = check_registry_closed_in_monarch(_config(), monarch)
    msgs = "\n".join(g["message"] for g in gaps)
    assert "Card B" in msgs and "deactivated in Monarch on 2026-02-28" in msgs
    assert "Auto Loan" in msgs and "not found in Monarch" in msgs
    assert "Old Card" not in msgs  # inactive accounts are skipped
    # hidden-but-live accounts are NOT closed
    monarch["Card B (...2222)"]["deactivated"] = None
    assert all("Card B" not in g["message"] for g in check_registry_closed_in_monarch(_config(), monarch))
    assert all(g["severity"] == "warning" for g in gaps)


def test_calendar_coverage():
    titles = ["Alpha Card - $0", "Truist - $702.91", "Spotify - $12.78"]
    gaps = check_calendar_coverage(_config(), titles)
    names = {g["message"].split("'")[1] for g in gaps}
    assert names == {"Mystery", "Netflix"}
    assert check_calendar_coverage(_config(), None) == []


def test_sources():
    gaps = check_sources({"monarch": "ok", "xero": "no Xero organisation connected", "zoho": "ok"})
    assert len(gaps) == 1 and gaps[0]["kind"] == "source_unavailable"
    assert "xero" in gaps[0]["message"] and "9091" in gaps[0]["fix"]
    assert check_sources(None) == []


def test_xero_staleness():
    today = date(2026, 10, 15)
    latest = {"Fresh": date(2026, 10, 12), "Stale": date(2026, 9, 1), "Empty": None}
    gaps = check_xero_staleness(latest, threshold_days=10, today=today)
    names = {g["message"].split("'")[1] for g in gaps}
    assert names == {"Stale", "Empty"}
    assert "44d ago" in "\n".join(g["message"] for g in gaps)
    assert check_xero_staleness({}) == []


def test_run_checks_offline_only_uses_registry():
    gaps = run_checks(_config())
    assert {g["kind"] for g in gaps} == {"no_payment_entry"}
