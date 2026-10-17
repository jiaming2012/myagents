"""Unit tests for the pure rule functions in sync_status.py (no network)."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sync_status import (  # noqa: E402
    monarch_findings,
    monarch_is_parked,
    slim_monarch_account,
    slim_zoho_account,
    warn_lines,
    zoho_findings,
    zoho_has_feed,
)

TODAY = date(2026, 10, 17)


def m(name, updated, inst="Bank", cred="c1", **over):
    base = {
        "name": name, "type": "credit", "institution": inst, "balance": 0.0, "updated": updated,
        "is_manual": False, "hidden": False, "deactivated": None, "sync_disabled": False,
        "credential_id": cred, "update_required": False, "disconnected_at": None,
        "provider": "PLAID", "institution_status": None,
    }
    base.update(over)
    return base


def z(name, **over):
    base = {
        "name": name, "type": "bank", "active": True, "book_balance": 10.0, "bank_balance": 10.0,
        "uncategorized": 0, "feed_status": "", "refresh_status": "completed",
        "last_refresh": "2026-10-17", "mfa_required": False, "consent_expired": False,
    }
    base.update(over)
    return base


# --- Monarch -----------------------------------------------------------------


def test_monarch_clean_connection_has_no_findings():
    assert monarch_findings([m("Card", "2026-10-17T10:00:00+00:00")], TODAY) == []


def test_monarch_update_required_is_error_grouped_by_credential():
    accts = [
        m("Checking (...0621)", "2026-08-03T10:54:50+00:00", "Lafayette", "lf", update_required=True),
        m("Personal Loan (...0621)", "2026-08-03T10:54:50+00:00", "Lafayette", "lf", update_required=True),
        m("Share Savings (...0621)", "2026-08-03T10:54:50+00:00", "Lafayette", "lf", update_required=True),
    ]
    out = monarch_findings(accts, TODAY)
    assert len(out) == 1
    f = out[0]
    assert f["severity"] == "error"
    assert f["source"] == "monarch"
    assert "Lafayette" in f["subject"]
    assert "Checking (...0621)" in f["subject"] and "Share Savings (...0621)" in f["subject"]
    assert "re-authentication" in f["message"]
    assert "2026-08-03" in f["message"] and "75d ago" in f["message"]


def test_monarch_stale_without_flag_is_warning():
    out = monarch_findings([m("Apple Cash", "2025-01-23T15:56:04+00:00", "Apple", "ap")], TODAY)
    assert [f["severity"] for f in out] == ["warning"]
    assert "stale" in out[0]["message"]


def test_monarch_stale_duplicate_does_not_drag_live_siblings_on_same_credential():
    accts = [
        m("Apple Cash", "2025-01-23T15:56:04+00:00", "Apple", "ap", balance=1.11),
        m("Apple Cash", "2026-10-16T22:46:26+00:00", "Apple", "ap", balance=6.96),
        m("Apple Card", "2026-10-16T22:46:26+00:00", "Apple", "ap", balance=-542.74),
    ]
    out = monarch_findings(accts, TODAY)
    assert len(out) == 1
    assert out[0]["subject"] == "Apple · Apple Cash ($1.11)"
    assert "632d ago" in out[0]["message"]


def test_monarch_within_threshold_is_not_stale():
    out = monarch_findings([m("Card", "2026-10-15T00:00:00+00:00")], TODAY, stale_days=3)
    assert out == []


def test_monarch_parked_accounts_are_ignored():
    accts = [
        m("Old 360", "2025-03-23T00:00:00+00:00", hidden=True),
        m("Closed card", "2026-05-02T00:00:00+00:00", deactivated="2026-05-03", sync_disabled=True),
        m("Cash jar", None, is_manual=True, credential_id=None),
        m("Avant", "2026-01-24T00:00:00+00:00", update_required=True, deactivated="2025-06-12"),
    ]
    assert all(monarch_is_parked(a) for a in accts)
    assert monarch_findings(accts, TODAY) == []


def test_monarch_disconnected_wins_over_update_required():
    out = monarch_findings([m("X", "2026-01-01", update_required=True, disconnected_at="2026-02-01T00:00:00+00:00")], TODAY)
    assert out[0]["severity"] == "error"
    assert "disconnected" in out[0]["message"]


def test_monarch_errors_sort_before_warnings():
    accts = [
        m("Zed stale", "2026-01-01T00:00:00+00:00", "Zed", "z"),
        m("Abe broken", "2026-10-17T00:00:00+00:00", "Abe", "a", update_required=True),
    ]
    out = monarch_findings(accts, TODAY)
    assert [f["severity"] for f in out] == ["error", "warning"]


def test_slim_monarch_account_reads_nested_credential():
    raw = {
        "displayName": "CREDIT CARD (...7006)", "currentBalance": -491.3,
        "displayLastUpdatedAt": "2026-05-22T20:25:36+00:00", "isHidden": False, "isManual": False,
        "syncDisabled": False, "deactivatedAt": None, "type": {"name": "credit"},
        "credential": {"id": "cr", "updateRequired": True, "disconnectedFromDataProviderAt": None,
                       "dataProvider": "PLAID", "institution": {"name": "Chase", "status": "HEALTHY"}},
        "institution": {"name": "Chase"},
    }
    s = slim_monarch_account(raw)
    assert s["update_required"] is True
    assert s["institution"] == "Chase"
    assert s["credential_id"] == "cr"
    assert s["institution_status"] == "HEALTHY"


# --- Zoho --------------------------------------------------------------------


def test_zoho_healthy_feed_has_no_findings():
    assert zoho_findings([z("Operations ••4613")], TODAY) == []


def _no_feed(**over):
    base = dict(type="credit_card", refresh_status="", last_refresh="", book_balance=5708.07, bank_balance=0.0)
    base.update(over)
    return z("Chase Ink ••7667", **base)


def test_zoho_no_feed_with_recent_statement_is_fine():
    acct = _no_feed(last_txn="2026-10-14")
    assert not zoho_has_feed(acct)
    assert zoho_findings([acct], TODAY) == []


def test_zoho_no_feed_with_overdue_statement_is_warning():
    out = zoho_findings([_no_feed(last_txn="2026-08-14")], TODAY, statement_stale_days=35)
    assert len(out) == 1
    assert out[0]["severity"] == "warning"
    assert "statement import overdue" in out[0]["message"]
    assert "64d ago" in out[0]["message"] and "5,708.07" in out[0]["message"]
    assert "Import Statement" in out[0]["fix"]


def test_zoho_no_feed_without_any_transactions_warns_about_drift():
    for last in (None, ""):
        out = zoho_findings([_no_feed(last_txn=last)], TODAY)
        assert len(out) == 1
        assert "no posted transactions" in out[0]["message"]


def test_zoho_no_feed_paid_off_suggests_retiring_the_account():
    out = zoho_findings([_no_feed(book_balance=0.0, last_txn="2026-10-14")], TODAY)
    assert len(out) == 1
    assert "paid off" in out[0]["message"]
    assert "inactive" in out[0]["fix"]


def test_zoho_inactive_and_cash_accounts_are_ignored():
    accts = [
        z("Operations ••0551 (closed)", active=False, refresh_status="", last_refresh=""),
        z("Petty Cash", type="cash", refresh_status="", last_refresh=""),
        z("Undeposited Funds", type="cash", refresh_status="", last_refresh=""),
    ]
    assert zoho_findings(accts, TODAY) == []


def test_zoho_broken_feed_states_are_errors():
    accts = [
        z("A", mfa_required=True),
        z("B", consent_expired=True),
        z("C", refresh_status="failed", feed_status="login_error"),
    ]
    out = zoho_findings(accts, TODAY)
    assert len(out) == 3
    assert all(f["severity"] == "error" for f in out)
    assert "MFA" in out[0]["message"]
    assert "consent" in out[1]["message"]
    assert "failed" in out[2]["message"] and "login_error" in out[2]["message"]


def test_zoho_stale_refresh_is_warning():
    out = zoho_findings([z("Taxes", last_refresh="2026-10-10")], TODAY, stale_days=2)
    assert [f["severity"] for f in out] == ["warning"]
    assert "7d ago" in out[0]["message"]


def test_zoho_book_bank_mismatch_is_warning_with_diff():
    out = zoho_findings([z("Ops", book_balance=100.0, bank_balance=75.5)], TODAY)
    assert len(out) == 1
    assert "+24.50" in out[0]["message"]


def test_zoho_uncategorized_lines_flagged_when_balances_agree():
    out = zoho_findings([z("Ops", uncategorized=3)], TODAY)
    assert len(out) == 1
    assert "3 uncategorized" in out[0]["message"]


def test_slim_zoho_account_parses_consent_flag():
    s = slim_zoho_account({"account_name": "X", "account_type": "bank", "is_active": True,
                           "balance": "1.5", "bank_balance": 1.5, "consent_info": {"is_consent_expired": "true"}})
    assert s["consent_expired"] is True
    assert s["book_balance"] == 1.5


# --- banner ------------------------------------------------------------------


def _result(findings, monarch=None, zoho=None):
    r = {"checked_at": "2026-10-17T12:00:00+00:00", "findings": findings}
    r["monarch"] = {"fetched_at": "2026-10-17T12:00:00+00:00", "accounts": monarch or [], "error": None}
    r["zoho"] = {"fetched_at": "2026-10-17T12:00:00+00:00", "accounts": zoho or [], "error": None}
    return r


def test_warn_lines_clean_is_single_ok_line():
    r = _result([], monarch=[m("A", "2026-10-17", cred="x"), m("B", "2026-10-17", cred="y")], zoho=[z("Q")])
    lines = warn_lines(r, color=False)
    assert len(lines) == 1
    assert lines[0].startswith("✓ sync check")
    assert "2 Monarch connections" in lines[0]
    assert "1 Zoho feeds" in lines[0]


def test_warn_lines_lists_each_finding_and_points_to_detail_task():
    findings = [
        {"source": "monarch", "severity": "error", "subject": "Chase · CREDIT CARD", "message": "needs re-auth", "fix": ""},
        {"source": "zoho", "severity": "warning", "subject": "Chase Ink", "message": "no bank feed", "fix": ""},
    ]
    lines = warn_lines(_result(findings), color=False)
    assert "SYNC WARNING" in lines[0] and "2 account connection issue(s)" in lines[0]
    assert any("✗ monarch" in ln and "needs re-auth" in ln for ln in lines)
    assert any("! zoho" in ln and "no bank feed" in ln for ln in lines)
    assert "task finance:sync-status" in lines[-1]
