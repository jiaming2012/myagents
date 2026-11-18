"""Vendors on Zoho Books expenses: merchants.yaml `vendor` flows through the classifiers."""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mercury_classify as mp  # noqa: E402
from zoho_books_recon import classify_feed, expense_vendor, vendor_names  # noqa: E402

CFG = {
    "merchants": [
        {"pattern": "PayrollTS", "account": "6360", "vendor": "OnPay"},
        {"pattern": "OnPay", "account": "6450", "vendor": "OnPay"},
        {"pattern": "Harris Teeter", "account": "5000", "vendor": "Harris Teeter"},
        {"pattern": "Jamal Cole", "account": "3200"},
        {"pattern": "Mystery Shop", "account": "6350"},
    ],
    "boa_merchants": [
        {"pattern": "VERIZON WIRELESS", "account": "6390", "vendor": "Verizon Wireless"},
        {"pattern": "OVERDRAFT ITEM FEE", "account": "6050", "vendor": "Bank of America"},
    ],
    "boa_transfers": {},
    "transfers": {},
    "skip": [],
}


def _txn(name, amount, desc=""):
    return {"id": "t1", "amount": amount, "counterpartyName": name, "bankDescription": desc, "postedAt": "2026-11-01T12:00:00Z"}


def test_classify_carries_the_vendor_and_none_without_one():
    _, coded, _, _ = mp.classify([_txn("Harris Teeter", -12.5), _txn("Jamal Cole", -100)], CFG, "x")
    assert coded[0][2:] == ("5000", "Harris Teeter")
    assert coded[1][2:] == ("3200", None)


def test_payroll_patterns_share_one_vendor():
    _, coded, _, _ = mp.classify([_txn("OnPay Inc", -50, "OnPay Inc; PayrollTS")], CFG, "x")
    assert coded[0][2:] == ("6360", "OnPay")


def test_vendor_names_are_distinct_and_sorted():
    assert vendor_names(CFG) == ["Bank of America", "Harris Teeter", "OnPay", "Verizon Wireless"]


def test_expense_vendor_matches_script_and_rule_descriptions():
    assert expense_vendor(CFG, "Harris Teeter | HARRIS TEETER 123; Merchant name: Harris Teeter") == ("Harris Teeter", "Harris Teeter")
    assert expense_vendor(CFG, "VERIZON WIRELESS DES:PAYMENTS INDN:JAMAL COLE CO ID:1234 PPD") == ("Verizon Wireless", "VERIZON WIRELESS")
    assert expense_vendor(CFG, "Mystery Shop | MYSTERY SHOP 1") == (None, "Mystery Shop")   # pattern without a vendor
    assert expense_vendor(CFG, "Something new") == (None, None)
    assert expense_vendor(CFG, None) == (None, None)


def test_expense_vendor_drops_the_account_holder_segment():
    # the ACH descriptor names the owner; the owner pattern must not claim a vendor debit
    assert expense_vendor(CFG, "OVERDRAFT ITEM FEE INDN:JAMAL COLE CO ID:9 PPD")[0] == "Bank of America"


def test_feed_lines_carry_the_vendor_and_returns_inherit_it():
    lines = [
        {"transaction_id": "a", "date": "2026-03-02", "amount": 20.85, "debit_or_credit": "credit",
         "payee": "", "description": "VERIZON WIRELESS DES:PAYMENTS"},
        {"transaction_id": "b", "date": "2026-03-04", "amount": 20.85, "debit_or_credit": "debit",
         "payee": "", "description": "RETURN OF POSTED CHECK / ITEM (RECEIVED ON 03-02)"},
    ]
    plan = {ln["transaction_id"]: (k, p) for ln, k, p in classify_feed(lines, CFG, "BoA: Business Checking (1778)")}
    assert plan["a"] == ("coded", ("VERIZON WIRELESS DES:PAYMENTS", "6390", False, "Verizon Wireless"))
    assert plan["b"][1][1:] == ("6390", True, "Verizon Wireless")


def test_live_merchants_yaml_gives_every_expense_pattern_a_vendor():
    cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "merchants.yaml").read_text())
    bare = [m["pattern"] for k in ("merchants", "boa_merchants") for m in cfg[k]
            if not m.get("vendor") and not m["account"].startswith(("3", "4"))]
    assert bare == [], f"expense patterns without a vendor: {bare}"
