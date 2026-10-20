"""Unit tests for the pure checks in account_register.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from account_register import (  # noqa: E402
    books_accounts,
    check_balances,
    check_registry_vs_live,
    check_sheet_vs_registry,
    check_sources,
    last4_of,
    membership_errors,
    monarch_accounts,
    plan_sync,
    registry_accounts,
    run_checks,
    sheet_accounts,
    sync,
)


def kinds(errors):
    return sorted(e["kind"] for e in errors)


def row(idx, name, typ, last4="", **extra):
    r = {"row_index": idx, "Name": name, "Type": typ, "Account Last 4": last4,
         "Debit Balance": "", "Credit Balance": "", "notes": ""}
    r.update(extra)
    return r


CONFIG = {"accounts": [
    {"id": "cap1-4354", "name": "Recurring ACH", "last4": "4354", "type": "depository",
     "category": "personal", "monarch_match": "Recurring ACH (...4354)"},
    {"id": "boa-5153", "name": "Plat 1", "last4": "5153", "type": "credit",
     "category": "personal", "monarch_match": "BankAmericard Platinum Plus Mastercard (...5153)"},
    {"id": "chase-7667", "name": "Chase Ink", "last4": "7667", "type": "credit",
     "category": "business", "zoho_books_match": "Chase Ink ••7667"},
    {"id": "mercury-0551", "name": "Operations old", "last4": "0551", "type": "depository",
     "category": "business", "inactive": True, "zoho_books_match": "Operations ••0551 (closed)"},
    {"id": "truist-loan", "name": "Auto Loan", "last4": "1001", "type": "loan",
     "category": "personal", "monarch_match": "Auto Loan (...1001)"},
]}

SHEET = [
    row(2, "Recurring ACH", "Personal Debit", "4354", **{"Debit Balance": 100}),
    row(3, "Plat 1", "Personal Credit", "5153", **{"Credit Balance": 5603}),
    row(4, "Chase Ink", "Business Credit", "7667", **{"Credit Balance": 5708.07}),
    row(5, "Etrade", "Investment", "9503"),
]

MONARCH = {
    "Recurring ACH (...4354)": {"balance": 100, "type": "depository", "hidden": False, "deactivated": None},
    "360 Checking (...4354)": {"balance": 99, "type": "depository", "hidden": True, "deactivated": None},
    "BankAmericard Platinum Plus Mastercard (...5153)": {"balance": -5603, "type": "credit",
                                                          "hidden": False, "deactivated": None},
    "Auto Loan (...1001)": {"balance": -10000, "type": "loan", "hidden": False, "deactivated": None},
    "Roth IRA (...238)": {"balance": 5, "type": "brokerage", "hidden": False, "deactivated": None},
}

BOOKS = [
    {"account_id": "1", "account_name": "Chase Ink ••7667", "account_type": "credit_card", "is_active": True,
     "balance": 5708.07},
    {"account_id": "2", "account_name": "Operations ••0551 (closed)", "account_type": "bank", "is_active": False,
     "balance": 0},
    {"account_id": "3", "account_name": "Petty Cash", "account_type": "cash", "is_active": True, "balance": 0},
]


# --- normalisers -------------------------------------------------------------

def test_last4_patterns():
    assert last4_of("Quicksilver (...0732)") == "0732"
    assert last4_of("Cash Management (...*****0789)") == "0789"
    assert last4_of("BILT (...XXXX-XXXX-XXXX-4607)") == "4607"
    assert last4_of("Chase Ink ••7667") == "7667"
    assert last4_of("WELLS FARGO SIGNIFY ...4637") == "4637"
    assert last4_of("CORP Account - Business Adv - 2119") == "2119"
    assert last4_of("xxxx1778", "Business Adv Fundamentals") == "1778"
    assert last4_of(None, "Apple Card") is None


def test_sheet_accounts_skips_investments_and_flags_bad_rows():
    rows = SHEET + [row(6, "Heartland", ""), row(7, "Mercury", "Business Debit", ""),
                    row(8, "Weird", "Savings", "1111"),
                    row(9, "Old", "Business Debit", "0474", notes="Closed"),
                    row(10, "Dup", "Personal Debit", "4354")]
    accounts, errors = sheet_accounts(rows)
    assert [a["key"] for a in accounts] == ["4354", "5153", "7667", "0474", "4354"]
    assert accounts[0]["balance"] == 100 and accounts[1]["balance"] == 5603
    assert kinds(errors) == ["sheet_duplicate_last4", "sheet_row_closed_in_notes",
                             "sheet_row_no_last4", "sheet_row_type_unknown", "sheet_row_untyped"]


def test_sheet_status_column_closes_a_row():
    accounts, errors = sheet_accounts([row(2, "Old", "Business Debit", "0474", Status="closed", notes="Closed")])
    assert accounts[0]["closed"] is True and errors == []


def test_registry_accounts_scope_and_closed():
    accts = registry_accounts(CONFIG)
    assert [a["key"] for a in accts] == ["4354", "5153", "7667", "0551"]   # loan skipped
    assert accts[3]["closed"] is True
    assert accts[2]["match"] == "Chase Ink ••7667"


def test_monarch_accounts_drops_hidden_and_out_of_scope():
    accts = monarch_accounts(MONARCH)
    assert sorted(a["key"] for a in accts) == ["4354", "5153"]


def test_books_accounts_skips_cash_and_marks_closed():
    accts = books_accounts(BOOKS)
    assert [(a["key"], a["closed"]) for a in accts] == [("7667", False), ("0551", True)]


# --- checks ------------------------------------------------------------------

def test_happy_path_is_clean():
    assert run_checks(CONFIG, sheet_rows=SHEET, monarch=MONARCH, books=BOOKS,
                      source_status={"sheet": "ok", "monarch": "ok", "books": "ok"}) == []


def test_sheet_vs_registry_both_directions():
    sheet, _ = sheet_accounts(SHEET[:1] + [row(9, "Extra", "Personal Credit", "9999")])
    errors = check_sheet_vs_registry(sheet, registry_accounts(CONFIG))
    assert kinds(errors) == ["registry_not_in_sheet", "registry_not_in_sheet", "sheet_not_in_registry"]


def test_closed_on_one_side_only():
    sheet, _ = sheet_accounts(SHEET + [row(6, "Operations old", "Business Debit", "0551")])
    errors = check_sheet_vs_registry(sheet, registry_accounts(CONFIG))
    assert kinds(errors) == ["closed_mismatch"]


def test_registry_vs_monarch_unclaimed_and_missing():
    registry = registry_accounts(CONFIG)
    live = monarch_accounts({**MONARCH,
                             "New Card (...4242)": {"balance": 0, "type": "credit", "hidden": False,
                                                    "deactivated": None}})
    assert kinds(check_registry_vs_live(registry, live, "personal")) == ["live_not_in_registry"]
    registry[0]["match"] = "Nope (...4354)"
    assert kinds(check_registry_vs_live(registry, live, "personal")) == ["live_not_in_registry",
                                                                           "live_not_in_registry",
                                                                           "match_not_found"]


def test_registry_vs_books_last4_and_closed():
    registry = registry_accounts(CONFIG)
    registry[2]["key"] = "1234"                       # wrong last4 in the registry
    registry[3]["closed"] = False                     # registry says open, Books says closed
    errors = check_registry_vs_live(registry, books_accounts(BOOKS), "business")
    assert kinds(errors) == ["last4_mismatch", "live_closed"]


def test_missing_match_name_is_an_error_only_when_open():
    registry = registry_accounts(CONFIG)
    registry[2]["match"] = None
    assert kinds(check_registry_vs_live(registry, books_accounts(BOOKS), "business")) == [
        "live_not_in_registry", "registry_no_match_name"]


# --- balances and sync ----------------------------------------------------------

def test_monarch_credit_balances_are_sign_flipped_to_amount_owed():
    by_key = {a["key"]: a["balance"] for a in monarch_accounts(MONARCH)}
    assert by_key == {"4354": 100.0, "5153": 5603.0}


def test_monarch_display_balance_wins_over_raw_balance():
    # Discover via Finicity: raw +1403.12, display balance fixed by the operator's invert
    # setting in Monarch. The display balance is used as is, no flip.
    accts = monarch_accounts({"Discover it Card (...9035)": {"balance": 1403.12, "display_balance": 1403.12,
                                                             "type": "credit", "hidden": False, "deactivated": None},
                              "Zero (...0000)": {"balance": 0.0, "display_balance": -0.0, "type": "credit",
                                                 "hidden": False, "deactivated": None}})
    assert [(a["key"], a["balance"]) for a in accts] == [("9035", 1403.12), ("0000", 0.0)]
    assert str(accts[1]["balance"]) == "0.0"


def test_balances_equal_is_clean_and_mismatch_is_an_error():
    sheet, _ = sheet_accounts(SHEET)
    registry = registry_accounts(CONFIG)
    assert check_balances(sheet, registry, monarch_accounts(MONARCH), "personal") == []
    books = books_accounts(BOOKS)
    assert check_balances(sheet, registry, books, "business") == []
    blank, _ = sheet_accounts([row(4, "Chase Ink", "Business Credit", "7667")])
    assert kinds(check_balances(blank, registry, books, "business")) == ["sheet_balance_missing"]
    sheet2, _ = sheet_accounts([row(2, "Recurring ACH", "Personal Debit", "4354", **{"Debit Balance": 99.99})])
    errors = check_balances(sheet2, registry, monarch_accounts(MONARCH), "personal")
    assert kinds(errors) == ["balance_mismatch"] and "99.99" in errors[0]["message"]


def test_run_checks_includes_balances_unless_disabled():
    rows = [row(2, "Recurring ACH", "Personal Debit", "4354", **{"Debit Balance": 1})] + SHEET[1:]
    assert kinds(run_checks(CONFIG, sheet_rows=rows, monarch=MONARCH, books=BOOKS)) == ["balance_mismatch"]
    assert run_checks(CONFIG, sheet_rows=rows, monarch=MONARCH, books=BOOKS, balances=False) == []


def test_plan_sync_writes_only_what_differs():
    sheet, _ = sheet_accounts([row(2, "Recurring ACH", "Personal Debit", "4354", **{"Debit Balance": 1}),
                               row(3, "Plat 1", "Personal Credit", "5153", **{"Credit Balance": 5603})])
    writes = plan_sync(sheet, registry_accounts(CONFIG), monarch_accounts(MONARCH), "personal")
    assert [(w["criteria"], w["data"], w["old"], w["new"]) for w in writes] == [
        ('"Account Last 4"="4354"', {"Debit Balance": 100.0}, 1.0, 100.0)]
    # with an As of column every matched row is refreshed, equal or not
    writes = plan_sync(sheet, registry_accounts(CONFIG), monarch_accounts(MONARCH), "personal",
                       as_of="2026-10-19", has_as_of_column=True)
    assert [w["data"] for w in writes] == [{"Debit Balance": 100.0, "As of": "2026-10-19"},
                                           {"Credit Balance": 5603.0, "As of": "2026-10-19"}]


def test_sync_refuses_while_membership_errors_exist():
    log = []
    inputs = dict(config=CONFIG, sheet_rows=SHEET + [row(9, "Extra", "Personal Credit", "9999")],
                  monarch=MONARCH, books=BOOKS, category=None, source_status={})
    assert sync(inputs, "sheet-id", dry_run=True, log=log.append) == 0
    assert log and "refused" in log[0]


def test_sync_dry_run_plans_but_writes_nothing():
    log = []
    rows = [row(2, "Recurring ACH", "Personal Debit", "4354", **{"Debit Balance": 1})] + SHEET[1:]
    inputs = dict(config=CONFIG, sheet_rows=rows, monarch=MONARCH, books=BOOKS, category=None,
                  source_status={})
    assert sync(inputs, "sheet-id", dry_run=True, log=log.append) == 0
    assert [l for l in log if "would write" in l] == [
        "  would write sheet row 2 'Recurring ACH': 1.00 -> 100.00"]


def test_membership_errors_excludes_balance_kinds():
    errors = [{"kind": "balance_mismatch"}, {"kind": "sheet_not_in_registry"}, {"kind": "sheet_balance_missing"}]
    assert [e["kind"] for e in membership_errors(errors)] == ["sheet_not_in_registry"]


def test_sources_and_category_narrowing():
    assert kinds(check_sources({"sheet": "ok", "monarch": "HTTP 401"})) == ["source_unavailable"]
    # business-only run never looks at Monarch even if given
    errors = run_checks(CONFIG, sheet_rows=SHEET, monarch={}, books=BOOKS, category="business")
    assert errors == []
    # a None side is skipped entirely (--offline / --no-sheet)
    assert run_checks(CONFIG, sheet_rows=None, monarch=None, books=None) == []
