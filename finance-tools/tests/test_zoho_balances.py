"""zoho_balances: Zoho Books account lists become forecast-shaped business balances."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zoho_balances import balances_from_accounts, uncategorized_lines  # noqa: E402


def _acct(name, balance, type_="bank", active=True, uncategorized=0, last_refresh="2026-11-17"):
    return {"id": "1", "name": name, "type": type_, "active": active, "book_balance": balance,
            "bank_balance": balance, "uncategorized": uncategorized, "feed_status": "", "refresh_status": "",
            "last_refresh": last_refresh, "mfa_required": False, "consent_expired": False, "last_txn": None}


def test_balances_follow_the_monarch_sign_convention():
    out = balances_from_accounts([
        _acct("Operations ••4613", 1205.74),
        _acct("CORP Account - 2119", 1651.98, type_="credit_card", uncategorized=2),
    ])
    assert out["Operations ••4613"]["balance"] == 1205.74
    assert out["Operations ••4613"]["updated"] == "2026-11-17"
    assert out["CORP Account - 2119"]["balance"] == -1651.98      # owed -> negative, like Monarch
    assert out["CORP Account - 2119"]["uncategorized"] == 2


def test_parked_accounts_are_left_out_and_empty_input_is_empty():
    out = balances_from_accounts([_acct("Operations ••0551 (closed)", 0.0, active=False),
                                  _acct("Taxes ••7818", 0.34)])
    assert list(out) == ["Taxes ••7818"]
    assert balances_from_accounts(None) == {}
    assert balances_from_accounts([]) == {}


def test_uncategorized_lines_lists_only_live_accounts_with_open_lines():
    rows = uncategorized_lines([
        _acct("Operations ••4613", 1.0, uncategorized=3),
        _acct("Taxes ••7818", 1.0, uncategorized=0),
        _acct("Old (closed)", 1.0, active=False, uncategorized=9),
    ])
    assert rows == [{"account": "Operations ••4613", "count": 3}]
