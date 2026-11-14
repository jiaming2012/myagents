"""monarch_signed_balance: one sign convention regardless of the data provider."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coverage_report import monarch_signed_balance  # noqa: E402


def test_plaid_card_unchanged():
    # Plaid reports owed negative; display is owed positive; convention is owed negative.
    acct = {"currentBalance": -5729.28, "displayBalance": 5729.28, "isAsset": False}
    assert monarch_signed_balance(acct) == -5729.28


def test_finicity_card_corrected_by_display_balance():
    # Finicity (Discover) reports owed POSITIVE; after the operator's invert in Monarch the
    # display balance is owed positive; the convention value must come out negative.
    acct = {"currentBalance": 1403.12, "displayBalance": 1403.12, "isAsset": False}
    assert monarch_signed_balance(acct) == -1403.12


def test_asset_uses_display_balance_as_is():
    acct = {"currentBalance": 480.57, "displayBalance": 480.57, "isAsset": True}
    assert monarch_signed_balance(acct) == 480.57


def test_loan_is_negative():
    acct = {"currentBalance": -10544.83, "displayBalance": 10544.83, "isAsset": False}
    assert monarch_signed_balance(acct) == -10544.83


def test_zero_card_never_prints_negative_zero():
    acct = {"currentBalance": 0.0, "displayBalance": -0.0, "isAsset": False}
    assert str(monarch_signed_balance(acct)) == "0.0"


def test_missing_display_balance_falls_back_to_raw():
    assert monarch_signed_balance({"currentBalance": -12.5}) == -12.5
    assert monarch_signed_balance({}) == 0
