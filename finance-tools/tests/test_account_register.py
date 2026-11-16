"""Unit tests for the pure parts of account_register.py. Render tests are named by
the rule they pin (RND-*, see the module docstring and
docs/proposals/registry-renders-sheet/design.md)."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from account_register import (  # noqa: E402
    ID_COLUMN,
    books_accounts,
    card_terms,
    check_registry_ids,
    check_registry_vs_live,
    check_sheet_vs_render,
    check_sources,
    last4_of,
    membership_errors,
    monarch_accounts,
    plan_migration,
    plan_render,
    registry_accounts,
    render,
    rendered_accounts,
    run_checks,
    sheet_accounts,
)

TODAY = date(2026, 11, 16)


def kinds(errors):
    return sorted(e["kind"] for e in errors)


HEADERS = ["Name", "Status", "Company", "Account Last 4", "Type", "Debit Balance", "Credit Balance",
           "Credit Limit", "Due Date", "AutoPay Type", "AutoPay Date", "APR", "notes", ID_COLUMN, "As of"]


def row(idx, name, typ, last4="", rid=None, **extra):
    r = {h: "" for h in HEADERS}
    r.update({"row_index": idx, "Name": name, "Type": typ, "Account Last 4": last4, "Status": "active",
              ID_COLUMN: rid or ""})
    r.update(extra)
    return r


CONFIG = {
    "accounts": [
        {"id": "cap1-4354", "name": "Recurring ACH", "last4": "4354", "type": "depository", "institution": "Capital One",
         "category": "personal", "monarch_match": "Recurring ACH (...4354)"},
        {"id": "boa-5153", "name": "Plat 1", "last4": "5153", "type": "credit", "institution": "Bank of America",
         "category": "personal", "monarch_match": "BankAmericard Platinum Plus Mastercard (...5153)"},
        {"id": "chase-7667", "name": "Chase Ink", "last4": "7667", "type": "credit", "institution": "Chase",
         "category": "business", "zoho_match": "Chase Ink ••7667"},
        {"id": "mercury-0551", "name": "Operations old", "last4": "0551", "type": "depository", "institution": "Mercury",
         "category": "business", "inactive": True, "zoho_match": "Operations ••0551 (closed)"},
        {"id": "truist-loan", "name": "Auto Loan", "last4": "1001", "type": "loan",
         "category": "personal", "monarch_match": "Auto Loan (...1001)"},
    ],
    "payments": [
        {"name": "Plat 1 autopay", "pays_account": "boa-5153", "autopay": True, "autopay_type": "min",
         "day_of_month": 19, "funding_account": "cap1-4354"},
    ],
}

DEBTS = {"debts": [{"account": "boa-5153", "apr": 24.99, "credit_limit": 3000, "due_day": 18},
                   {"account": "chase-7667", "apr": 23.6}],
         "promos": [{"account": "chase-7667", "apr": 9.99, "credit_limit": 5000}]}

SHEET = [   # a fully rendered sheet as of yesterday
    row(2, "Recurring ACH", "Personal Debit", "4354", "cap1-4354", Company="Capital One",
        **{"Debit Balance": 100, "As of": "2026-11-15"}),
    row(3, "Plat 1", "Personal Credit", "5153", "boa-5153", Company="Bank of America",
        **{"Credit Balance": 5603, "As of": "2026-11-15", "AutoPay Type": "min", "AutoPay Date": 19,
           "Credit Limit": 3000, "APR": 24.99, "Due Date": 18, "notes": "ask about limit increase"}),
    row(4, "Chase Ink", "Business Credit", "7667", "chase-7667", Company="Chase",
        **{"Credit Balance": 5708.07, "As of": "2026-11-15", "AutoPay Type": "none", "Credit Limit": 5000,
           "APR": 9.99}),
    row(5, "Operations old", "Business Debit", "0551", "mercury-0551", Company="Mercury", Status="closed"),
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


def live():
    return {"personal": monarch_accounts(MONARCH), "business": books_accounts(BOOKS)}


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


def test_RND_KEY_sheet_rows_join_by_registry_id_and_unregistered_rows_are_errors():
    rows = SHEET + [row(6, "Wex Health", "Personal Debit", ""), row(7, "Etrade", "Investment", "9503"),
                    row(8, "Dup", "Personal Debit", "4354", "cap1-4354")]
    accounts, errors = sheet_accounts(rows)
    assert [a["id"] for a in accounts] == ["cap1-4354", "boa-5153", "chase-7667", "mercury-0551"]
    assert accounts[0]["balance"] == 100 and accounts[1]["balance"] == 5603 and accounts[3]["closed"]
    assert kinds(errors) == ["sheet_duplicate_id", "sheet_unregistered_row"]
    assert "Wex Health" in errors[0]["message"]


def test_RND_SCOPE_registry_accounts_skip_loans_and_carry_ids():
    accts = registry_accounts(CONFIG)
    assert [a["id"] for a in accts] == ["cap1-4354", "boa-5153", "chase-7667", "mercury-0551"]
    assert accts[3]["closed"] is True and accts[2]["match"] == "Chase Ink ••7667"


def test_RND_CLOSED_closed_on_starts_the_retirement_clock():
    cfg = {"accounts": [
        {"id": "a", "name": "Open", "type": "credit", "category": "personal"},
        {"id": "b", "name": "Closed no date", "type": "credit", "category": "personal", "status": "closed"},
        {"id": "c", "name": "Closed recent", "type": "credit", "category": "personal", "status": "closed",
         "closed_on": "2026-10-20"},
        {"id": "d", "name": "Closed long ago", "type": "credit", "category": "personal", "status": "closed",
         "closed_on": "2026-09-01"},
    ]}
    assert [a["id"] for a in rendered_accounts(registry_accounts(cfg), TODAY)] == ["a", "b", "c"]


def test_RND_ORDER_registry_order_with_closed_last():
    cfg = {"accounts": [
        {"id": "z", "name": "Z", "type": "credit", "category": "personal", "status": "closed"},
        {"id": "m", "name": "M", "type": "depository", "category": "business"},
        {"id": "a", "name": "A", "type": "credit", "category": "personal"},
    ]}
    assert [a["id"] for a in rendered_accounts(registry_accounts(cfg), TODAY)] == ["m", "a", "z"]
    assert [a["id"] for a in rendered_accounts(registry_accounts(cfg), TODAY, category="personal")] == ["a", "z"]


def test_monarch_accounts_drops_hidden_and_out_of_scope():
    assert sorted(a["key"] for a in monarch_accounts(MONARCH)) == ["4354", "5153"]


def test_books_accounts_skips_cash_and_marks_closed():
    assert [(a["key"], a["closed"]) for a in books_accounts(BOOKS)] == [("7667", False), ("0551", True)]


def test_monarch_credit_balances_are_sign_flipped_to_amount_owed():
    assert {a["key"]: a["balance"] for a in monarch_accounts(MONARCH)} == {"4354": 100.0, "5153": 5603.0}


def test_monarch_display_balance_wins_over_raw_balance():
    accts = monarch_accounts({"Discover it Card (...9035)": {"balance": 1403.12, "display_balance": 1403.12,
                                                             "type": "credit", "hidden": False, "deactivated": None},
                              "Zero (...0000)": {"balance": 0.0, "display_balance": -0.0, "type": "credit",
                                                 "hidden": False, "deactivated": None}})
    assert [(a["key"], a["balance"]) for a in accts] == [("9035", 1403.12), ("0000", 0.0)]
    assert str(accts[1]["balance"]) == "0.0"


# --- registry and live checks --------------------------------------------------

def test_RND_KEY_registry_ids_must_be_unique():
    cfg = {"accounts": [{"id": "x", "name": "One", "type": "credit", "category": "personal"},
                        {"id": "x", "name": "Two", "type": "credit", "category": "personal"},
                        {"name": "No id", "type": "credit", "category": "personal"}]}
    assert kinds(check_registry_ids(registry_accounts(cfg))) == ["registry_duplicate_id", "registry_no_id"]


def test_registry_vs_monarch_unclaimed_and_missing():
    registry = registry_accounts(CONFIG)
    lv = monarch_accounts({**MONARCH, "New Card (...4242)": {"balance": 0, "type": "credit", "hidden": False,
                                                              "deactivated": None}})
    assert kinds(check_registry_vs_live(registry, lv, "personal")) == ["live_not_in_registry"]
    registry[0]["match"] = "Nope (...4354)"
    assert kinds(check_registry_vs_live(registry, lv, "personal")) == ["live_not_in_registry",
                                                                         "live_not_in_registry", "match_not_found"]


def test_registry_vs_books_last4_and_closed():
    registry = registry_accounts(CONFIG)
    registry[2]["key"] = "1234"
    registry[3]["closed"] = False
    assert kinds(check_registry_vs_live(registry, books_accounts(BOOKS), "business")) == ["last4_mismatch", "live_closed"]


def test_missing_match_name_is_an_error_only_when_open():
    registry = registry_accounts(CONFIG)
    registry[2]["match"] = None
    assert kinds(check_registry_vs_live(registry, books_accounts(BOOKS), "business")) == [
        "live_not_in_registry", "registry_no_match_name"]


def test_sources_and_category_narrowing():
    assert kinds(check_sources({"sheet": "ok", "monarch": "HTTP 401"})) == ["source_unavailable"]
    assert run_checks(CONFIG, sheet_rows=SHEET, monarch={}, books=BOOKS, category="business", debts=DEBTS,
                      today=TODAY) == []
    assert run_checks(CONFIG, sheet_rows=None, monarch=None, books=None) == []


# --- the render ------------------------------------------------------------------

def test_RND_DIFF_a_date_cell_zoho_reformatted_still_compares_equal():
    """The render writes As of as 2026-11-16; Zoho stores a date and returns 11/16/2026."""
    rows = [dict(SHEET[0], **{"As of": "11/15/2026"})] + SHEET[1:]
    plan = plan_render(CONFIG, DEBTS, live(), rows, today=TODAY)
    assert [c for c in plan["changes"] if c["column"] == "As of"] == []


def test_RND_DIFF_a_rendered_sheet_plans_no_changes():
    plan = plan_render(CONFIG, DEBTS, live(), SHEET, today=TODAY)
    assert plan["errors"] == [] and plan["retired"] == [] and plan["fit_ok"]
    assert [c for c in plan["changes"] if c["column"] != "As of"] == []
    assert [r["id"] for r in plan["rows"]] == ["cap1-4354", "boa-5153", "chase-7667", "mercury-0551"]


def test_RND_TYPE_and_identity_columns_come_from_the_registry():
    plan = plan_render(CONFIG, DEBTS, live(), SHEET, today=TODAY)
    cells = plan["rows"][3]["cells"]
    assert (cells["Type"], cells["Status"], cells["Company"], cells["Account Last 4"], cells[ID_COLUMN]) == (
        "Business Debit", "closed", "Mercury", "0551", "mercury-0551")


def test_RND_TERMS_card_terms_from_payments_and_debts():
    assert card_terms(CONFIG, DEBTS, "boa-5153", "credit") == {
        "AutoPay Type": "min", "AutoPay Date": 19, "Credit Limit": 3000, "APR": 24.99, "Due Date": 18}
    # promo overrides apr and limit; no paying entry -> autopay none
    assert card_terms(CONFIG, DEBTS, "chase-7667", "credit") == {
        "AutoPay Type": "none", "AutoPay Date": "", "Credit Limit": 5000, "APR": 9.99, "Due Date": ""}
    assert all(v == "" for v in card_terms(CONFIG, DEBTS, "cap1-4354", "debit").values())


def test_RND_BAL_live_balance_is_written_and_kept_when_source_is_down():
    rows = [row(2, "Recurring ACH", "Personal Debit", "4354", "cap1-4354", Company="Capital One",
                **{"Debit Balance": 1})] + SHEET[1:]
    plan = plan_render(CONFIG, DEBTS, live(), rows, today=TODAY)
    bal = [c for c in plan["changes"] if c["column"] == "Debit Balance"]
    assert [(c["row"], c["old"], c["new"], c["value"]) for c in bal] == [(2, "1.00", "100.00", 100.0)]
    # As of moves only with the balance it dates: row 2 gets today's date, the unchanged rows keep theirs
    assert [(c["row"], c["new"]) for c in plan["changes"] if c["column"] == "As of"] == [(2, "2026-11-16")]
    # Monarch down: the stale 1 stays, no change planned for it
    plan = plan_render(CONFIG, DEBTS, {"business": books_accounts(BOOKS)}, rows, today=TODAY)
    assert [c for c in plan["changes"] if c["row"] == 2] == []


def test_RND_OWN_operator_cells_move_with_the_row_and_are_never_generated():
    cfg = {**CONFIG, "accounts": [CONFIG["accounts"][1], CONFIG["accounts"][0]] + CONFIG["accounts"][2:]}
    plan = plan_render(cfg, DEBTS, live(), SHEET, today=TODAY)                # Plat 1 now renders first
    assert [r["id"] for r in plan["rows"]][:2] == ["boa-5153", "cap1-4354"]
    notes = [c for c in plan["changes"] if c["column"] == "notes"]
    assert [(c["row"], c["old"], c["new"], c["moved"]) for c in notes] == [
        (2, "", "ask about limit increase", True), (3, "ask about limit increase", "", True)]


def test_RND_KEY_a_renamed_account_keeps_its_row():
    cfg = {**CONFIG, "accounts": [dict(CONFIG["accounts"][1], name="Amex Platinum")] + CONFIG["accounts"][:1]
           + CONFIG["accounts"][2:]}
    plan = plan_render(cfg, DEBTS, live(), SHEET, today=TODAY)
    assert len(plan["rows"]) == 4 and plan["retired"] == []
    assert [c["new"] for c in plan["changes"] if c["column"] == "Name" and c["row"] == 2] == ["Amex Platinum"]


def test_RND_FIT_refuses_when_the_gap_is_too_small():
    cfg = {**CONFIG, "accounts": CONFIG["accounts"] + [
        {"id": f"new-{i}", "name": f"New {i}", "type": "credit", "category": "personal"} for i in range(3)]}
    plan = plan_render(cfg, DEBTS, live(), SHEET, today=TODAY, gap_rows=2)
    assert plan["extra_rows_needed"] == 3 and plan["fit_ok"] is False and plan["totals_row"] == 8
    assert plan_render(cfg, DEBTS, live(), SHEET, today=TODAY, gap_rows=3)["fit_ok"]
    assert plan_render(cfg, DEBTS, live(), SHEET, today=TODAY, gap_rows=None)["fit_ok"]


def test_RND_ORDER_rows_past_the_new_end_are_retired():
    cfg = {**CONFIG, "accounts": CONFIG["accounts"][:2] + CONFIG["accounts"][4:]}   # business accounts gone
    plan = plan_render(cfg, DEBTS, live(), SHEET, today=TODAY)
    assert [(r["row"], r["id"]) for r in plan["retired"]] == [(4, "chase-7667"), (5, "mercury-0551")]


def test_RND_KEY_a_duplicate_id_is_reported_but_does_not_block_the_render():
    """What a half-finished render leaves: the account already at its new row, the old
    row not yet overwritten. The full render rewrites both positions."""
    rows = SHEET + [dict(SHEET[1], row_index=6)]                 # Plat 1 twice (rows 3 and 6)
    plan = plan_render(CONFIG, DEBTS, live(), rows, today=TODAY)
    assert plan["errors"] == [] and kinds(plan["warnings"]) == ["sheet_duplicate_id"]
    assert [(r["row"], r["action"]) for r in plan["retired"]] == [(6, "delete")]
    assert kinds(check_sheet_vs_render(plan)) == ["sheet_drift", "sheet_duplicate_id"]


def test_RND_KEY_unregistered_rows_and_missing_columns_block_the_render():
    rows = SHEET + [row(6, "Wex Health", "Personal Debit", "")]
    assert kinds(plan_render(CONFIG, DEBTS, live(), rows, today=TODAY)["errors"]) == ["sheet_unregistered_row"]
    no_id = [{k: v for k, v in r.items() if k != ID_COLUMN} for r in SHEET]
    assert kinds(plan_render(CONFIG, DEBTS, live(), no_id, today=TODAY)["errors"]) == [
        "sheet_missing_column", "sheet_unregistered_row", "sheet_unregistered_row",
        "sheet_unregistered_row", "sheet_unregistered_row"]


def test_check_sheet_vs_render_classifies_balance_and_drift():
    rows = [row(2, "Recurring ACH", "Personal Debit", "4354", "cap1-4354", Company="Capital One",
                **{"Debit Balance": 1, "As of": "2026-11-15"}),
            row(3, "Plat 1", "Personal Credit", "5153", "boa-5153", Company="BofA",
                **{"Credit Balance": 5603, "As of": "2026-11-15", "AutoPay Type": "min", "AutoPay Date": 19,
                   "Credit Limit": 3000, "APR": 24.99, "Due Date": 18})] + SHEET[2:]
    errors = check_sheet_vs_render(plan_render(CONFIG, DEBTS, live(), rows, today=TODAY))
    assert kinds(errors) == ["balance_mismatch", "sheet_drift"]
    assert "Company" in [e for e in errors if e["kind"] == "sheet_drift"][0]["message"]
    assert kinds(check_sheet_vs_render(plan_render(CONFIG, DEBTS, live(), rows, today=TODAY), balances=False)) == [
        "sheet_drift"]
    assert kinds(membership_errors(errors)) == []


def test_run_checks_happy_path_is_clean():
    assert run_checks(CONFIG, sheet_rows=SHEET, monarch=MONARCH, books=BOOKS, debts=DEBTS, today=TODAY,
                      source_status={"sheet": "ok", "monarch": "ok", "books": "ok"}) == []


def test_render_writes_differing_cells_by_row_and_deletes_retired_rows(monkeypatch):
    import zoho_sheet
    written, deleted = [], []
    monkeypatch.setattr(zoho_sheet, "header_columns", lambda sid, tab: {h: i + 1 for i, h in enumerate(HEADERS)})
    monkeypatch.setattr(zoho_sheet, "write_row",
                        lambda sid, tab, r, data, columns: written.append((r, data)) or 1)
    monkeypatch.setattr(zoho_sheet, "write_cells", lambda *a: pytest.fail("render writes whole rows"))
    monkeypatch.setattr(zoho_sheet, "delete_rows", lambda sid, tab, a, b: deleted.append((a, b)) or (b - a + 1))
    monkeypatch.setattr(zoho_sheet, "blank_rows_below", lambda sid, tab, r: 5)
    cfg = {**CONFIG, "accounts": CONFIG["accounts"][:2] + CONFIG["accounts"][4:]}
    rows = [row(2, "Recurring ACH", "Personal Debit", "4354", "cap1-4354", Company="Capital One",
                **{"Debit Balance": 1})] + SHEET[1:]
    inputs = dict(config=cfg, sheet_rows=rows, monarch=MONARCH, books=BOOKS, category=None, source_status={},
                  debts=DEBTS)
    log = []
    assert render(inputs, "sheet-id", dry_run=True, log=log.append, today=TODAY) == 0 and written == []
    assert "  would write row 2 'Recurring ACH' Debit Balance: 1.00 -> 100.00" in log
    n = render(inputs, "sheet-id", log=log.append, today=TODAY)
    assert n == 1 and [r for r, _ in written] == [2]
    full = written[0][1]                      # the whole target row, not just the two changed cells
    assert (full["Name"], full["Debit Balance"], full["As of"], full[ID_COLUMN]) == (
        "Recurring ACH", 100.0, "2026-11-16", "cap1-4354")
    assert deleted == [(4, 5)]
    assert log[-1] == "  render: 2 row(s), 1 row(s) written (2 cell(s) changed), 0 row(s) blanked, 2 row(s) deleted."


def test_render_refuses_on_unregistered_row_and_on_no_fit(monkeypatch):
    import zoho_sheet
    monkeypatch.setattr(zoho_sheet, "write_row", lambda *a: pytest.fail("must not write"))
    monkeypatch.setattr(zoho_sheet, "blank_rows_below", lambda sid, tab, r: 0)
    log = []
    inputs = dict(config=CONFIG, sheet_rows=SHEET + [row(6, "Wex Health", "Personal Debit", "")], monarch=MONARCH,
                  books=BOOKS, category=None, source_status={}, debts=DEBTS)
    assert render(inputs, "sheet-id", log=log.append, today=TODAY) == 0 and "blocked" in log[0]
    cfg = {**CONFIG, "accounts": CONFIG["accounts"] + [{"id": "new", "name": "New", "type": "credit",
                                                        "category": "personal"}]}
    inputs = dict(inputs, config=cfg, sheet_rows=SHEET)
    log.clear()
    assert render(inputs, "sheet-id", log=log.append, today=TODAY) == 0
    assert log[0].startswith("  render refused: the block needs 1 more row(s) above row 6")


# --- the totals row, pass-through rows, manual feeds -------------------------------

def totals(idx):
    r = {h: "" for h in HEADERS}
    r.update({"row_index": idx, "Debit Balance": 21699.69, "Credit Balance": 21959.45, "Credit Limit": 54595})
    return r


def test_RND_FIT_a_totals_row_inside_the_block_is_never_written_and_bounds_the_render():
    # rows: 2 Recurring, 3 Plat 1, 4 TOTALS, 5 Chase Ink, 6 Operations old  (accounts below the totals)
    rows = [SHEET[0], SHEET[1], totals(4), dict(SHEET[2], row_index=5), dict(SHEET[3], row_index=6)]
    plan = plan_render(CONFIG, DEBTS, live(), rows, today=TODAY)
    assert plan["errors"] == [] and plan["totals_row"] == 4
    assert plan["fit_ok"] is False and plan["extra_rows_needed"] == 2      # 4 accounts need rows 2-5
    assert all(c["row"] != 4 for c in plan["changes"])
    # drop the two business accounts: the block fits in rows 2-3, rows 5-6 are deleted, row 4 untouched
    cfg = {**CONFIG, "accounts": CONFIG["accounts"][:2] + CONFIG["accounts"][4:]}
    plan = plan_render(cfg, DEBTS, live(), rows, today=TODAY)
    assert plan["fit_ok"] and [(r["row"], r["action"]) for r in plan["retired"]] == [(5, "delete"), (6, "delete")]
    # with a spare row above the totals, a retired row above it is blanked, not deleted
    rows2 = [SHEET[0], SHEET[1], dict(SHEET[2], row_index=4), totals(5)]
    plan = plan_render(cfg, DEBTS, live(), rows2, today=TODAY)
    assert [(r["row"], r["action"]) for r in plan["retired"]] == [(4, "blank")]


def test_RND_SCOPE_investment_and_loan_rows_pass_through_after_the_accounts():
    rows = [SHEET[0], row(3, "ECSI - Heartland", "Loan", notes="student loan"), dict(SHEET[1], row_index=4),
            dict(SHEET[2], row_index=5), dict(SHEET[3], row_index=6), row(7, "Bera 401k", "Investment", Company="Principal")]
    plan = plan_render(CONFIG, DEBTS, live(), rows, today=TODAY)
    assert plan["errors"] == []
    assert [(r["row"], r["name"]) for r in plan["rows"]] == [
        (2, "Recurring ACH"), (3, "Plat 1"), (4, "Chase Ink"), (5, "Operations old"),
        (6, "ECSI - Heartland"), (7, "Bera 401k")]
    moved = {(c["row"], c["column"]): c["new"] for c in plan["changes"] if c["moved"]}
    assert moved[(6, "notes")] == "student loan" and moved[(6, "Type")] == "Loan"


def test_manual_feed_accounts_need_no_match_and_keep_the_sheet_balance():
    cfg = {**CONFIG, "accounts": CONFIG["accounts"] + [
        {"id": "wex-hsa", "name": "Wex Health", "type": "depository", "category": "personal", "feed": "manual"}]}
    registry = registry_accounts(cfg)
    assert registry[-1]["manual"] and kinds(check_registry_vs_live(registry, monarch_accounts(MONARCH), "personal")) == []
    rows = SHEET + [row(6, "Wex Health", "Personal Debit", "", "wex-hsa", Company="Wex Health",
                        **{"Debit Balance": 5954.43})]
    plan = plan_render(cfg, DEBTS, live(), rows, today=TODAY)
    wex = [r for r in plan["rows"] if r["id"] == "wex-hsa"][0]
    assert wex["row"] == 5 and wex["cells"]["Debit Balance"] == 5954.43      # open accounts first, balance kept


def test_plan_migration_never_falls_back_to_name_when_the_row_has_a_last4():
    rows = [row(2, "Chase Ink", "Business Credit", "0474"),       # Cap One, same name as nothing; stays unmapped
            row(3, "Chase Ink", "Business Credit", "")]           # no last4: name is allowed
    mapped, unmapped = plan_migration(CONFIG, rows)
    assert [(m["row"], m["id"]) for m in mapped] == [(3, "chase-7667")]
    assert [u["row"] for u in unmapped] == [2]


# --- migration ----------------------------------------------------------------------

def test_plan_migration_maps_by_last4_then_name_and_lists_the_rest():
    rows = [row(2, "Recurring ACH", "Personal Debit", "4354"),         # by last4
            row(3, "Plat 1", "Personal Credit", "21153"),              # bad last4, unique name
            row(4, "Chase Ink", "Business Credit", "7667", "chase-7667"),   # already mapped
            row(5, "Mercury", "Personal Debit", ""),                   # nothing
            row(6, "Recurring ACH", "Personal Debit", "4354")]         # duplicate of row 2
    mapped, unmapped = plan_migration(CONFIG, rows)
    assert [(m["row"], m["id"]) for m in mapped] == [(2, "cap1-4354")]
    # row 3 has a last4 that matches nothing, so its name is NOT used; row 6 duplicates row 2's account
    assert [(u["row"], u["reason"][:12]) for u in unmapped] == [(3, "no registry "), (5, "no registry "),
                                                               (6, "already mapp")]
