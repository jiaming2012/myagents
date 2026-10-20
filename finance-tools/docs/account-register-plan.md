# Plan: the Bills sheet as the account register, enforced one-to-one

Status: agreed 2026-10-19 (decisions below). Nothing here is built yet.

## Goal

The native Zoho Sheet "Bills" (converted from Bills.xlsx in the WorkDrive folder on
2026-10-19) becomes the register of record for accounts: the place the operator looks to
see every account and its balance. A script proves, on every run, that the register is
one-to-one with the systems that actually hold the money:

| Register rows        | Must match exactly one of                     | Balance source        |
|----------------------|-----------------------------------------------|-----------------------|
| personal accounts    | Monarch Money accounts                        | Monarch `currentBalance` |
| business accounts    | Zoho Books bank accounts (`/bankaccounts`)    | Zoho Books `balance` (book) |

"One-to-one" means both directions: every register row maps to exactly one live account,
and every live account maps to exactly one register row. Any break, in either direction,
and any balance difference, is an **error**: the script prints it and exits 1. There is no
warning tier for this check.

## What exists today (facts the plan builds on)

- **Sheet.** Tab `Chart of Accounts` has 43 rows with `Type` (Personal/Business ×
  Debit/Credit, or Investment), `Account Last 4`, `Debit Balance` (G) and `Credit Balance`
  (H). The balances are 2023 numbers. Three rows have no `Type` (Heartland, Nelnet, Bera
  401k). "Mercury" appears twice with no last4 (personal row 3, business row 26). Closed
  accounts are only marked in free-text notes ("Closed" on Capital One 0474). Tab
  `C.C. - Zero Balance` carries a second copy of card balances, and tab
  `Yumyum Bank Accounts` lists 26 Xero-era business account names with reconciliation dates.
- **Registry.** `payments.yaml` `accounts:` has 39 entries, each with `last4`,
  `category: personal|business`, `monarch_match` (Monarch display name or null) and, for
  business, `xero_account_id`. No Zoho Books link, no balances. The forecast and gaps
  scripts key off this file.
- **Monarch.** `task balances` (`monarch_balances.py`) returns displayName, type,
  institution and `currentBalance`; `coverage_report.py` already normalises Monarch accounts
  and matches on last4.
- **Zoho Books.** `zoho_books_recon.py` `bank_index()` reads `/bankaccounts`; each record has
  `account_name`, `balance` (book) and `bank_balance` (feed). Names carry the last4, e.g.
  "Chase Ink ••7667", "LFCU Business Checking ••7011". Mercury is several sub-accounts.
- **Check pattern.** `registry_gaps.py` (`task gaps`): pure check functions over pre-fetched
  data, a list of `{severity, kind, message, fix}`, printed, `sys.exit(1)` if any.
- **Sheet API.** `zoho_sheet.py` lists and uploads only. The token already has
  `ZohoSheet.dataAPI.READ`. The converted native workbook does **not** appear in
  `workbook.list`, so its resource id comes from the WorkDrive URL and reading it through
  the Sheet Data API has to be proven first.

## Design

### Join key

`institution + last4`, both sides. Names drift; last4 does not. Every open register row
must have a last4 (the two Mercury rows are the first errors the check will raise).
Where a live account has no last4 (some investments), the registry's `monarch_match`
or a new `zoho_books_match` name is the fallback key, and the register row must carry
the same name.

### One register, three layers

```
Zoho Sheet "Chart of Accounts"   ← what the operator sees and edits
        ↕ one-to-one
payments.yaml accounts:           ← machine registry the forecast already uses
        ↕ one-to-one
Monarch (personal) / Zoho Books (business)   ← truth for balances
```

The sheet and the YAML stay in sync by the same check: a row in one and not the other is
an error. This keeps the forecast, gaps and alert scripts working off YAML while the
sheet stays the human view. (Alternative: drop the YAML `accounts:` block and read the
sheet directly. Rejected for now: every other script would need the Sheet API and a token
at run time, and offline runs would stop working.)

### The check (`task accounts:check`)

New script `account_register.py`, same shape as `registry_gaps.py`:

1. Fetch: sheet rows (Sheet Data API records fetch on the `Chart of Accounts` tab), YAML
   accounts, Monarch accounts, Zoho Books `/bankaccounts`.
2. Normalise each into `{key, owner, name, balance, status}`.
3. Checks, each a pure function returning errors:
   - `sheet_rows_well_formed`: every row has Type, Status, last4 (or a match name);
     no duplicate keys.
   - `sheet_vs_registry`: set equality on keys, split by owner.
   - `personal_vs_monarch`: open personal keys == Monarch active, non-hidden accounts.
   - `business_vs_books`: open business keys == Zoho Books bank accounts (closed-in-Books
     accounts must be `Status: closed` in the register, and vice versa).
   - `balances`: per matched pair, sheet balance == live balance to the cent. Credit rows
     compare `Credit Balance` to the amount owed (positive); debit rows compare
     `Debit Balance`.
4. Print `ERROR [kind] message` + `fix:` lines; exit 1 on any error, 0 otherwise.

Flags: `--category personal|business` (same as gaps), `--offline` (skip live sources,
only sheet-vs-registry), `--json`.

### Keeping balances equal: `task accounts:sync`

A strict balance check will fail every day unless something writes live balances into
the sheet. `accounts:sync` fetches the same live data, writes `Debit Balance` /
`Credit Balance` into the matched sheet rows (Sheet Data API `worksheet.records.update`,
criteria on `Account Last 4`, scope `ZohoSheet.dataAPI.UPDATE` already held), then
re-reads the sheet and runs the check. If the tab has an `As of` column, today's date is
written there too and every matched row is refreshed; without it only rows whose balance
differs are touched. Membership errors are never auto-fixed: a missing or extra account
is always a human decision, so sync refuses to write if any membership check fails.
Sign convention: the sheet's `Credit Balance` is the amount owed, positive. Monarch
reports cards negative, so the check flips the sign; Zoho Books already reports cards
as owed.

Recommended cadence: `accounts:check` inside `task finance:sync-status` (the banner that
runs before every numbers task), `accounts:sync` on demand or from the keep-warm launchd
agent once trusted.

### Sheet schema changes (operator does these in Zoho Sheet)

- `Chart of Accounts` gains `Status` (open/closed) and `As of` (date the balance was
  written). `Type` is already the owner split; the three untyped rows get a Type or go.
- Last4 filled for every open row (Mercury ×2, Coinbase, Apple, Bilt, Bluevine, M&T
  Savings).
- Business rows become one row per Zoho Books bank account (Mercury sub-accounts, LFCU
  ×2, Wells Signify), not one "Mercury" line. That is what one-to-one means on the Books
  side.
- `C.C. - Zero Balance` balances become formulas pointing at `Chart of Accounts` (or the
  check ignores that tab). Two hand-typed copies of a balance cannot both be one-to-one.
- `Yumyum Bank Accounts` is retired or regenerated from Zoho Books; its 26 Xero-era names
  are the old chart, not today's.

### Registry changes

- `zoho_books_match:` on business accounts (the Books `account_name`), mirroring
  `monarch_match`. `xero_account_id` stays until Xero is fully retired.
- `status: closed` replaces today's `inactive: true` so the word matches the sheet.

## Phases

| # | Deliverable | Done when |
|---|-------------|-----------|
| 0 | **DONE 2026-10-19.** Read the converted native sheet by its WorkDrive id through the Sheet Data API with the existing token. | `task zoho:sheet:worksheets -- efajyb8b15e86a20a48e0b3edab6cf4fb18c5` lists the tabs and `task zoho:sheet:records -- <id> --sheet "Chart of Accounts"` prints 43 rows keyed by header, formulas evaluated, each with `row_index`. The id survived the xlsx-to-native conversion. The native workbook now has four tabs: Chart of Accounts, Credit Cards (was C.C. - Zero Balance), Recurring Charges (was Recurring), Loans; the Yumyum Bank Accounts, Plan, Investments, Propane and Notes tabs are gone, which already settles two of the schema changes below. |
| 1 | **DONE 2026-10-19.** Membership check: `account_register.py` with checks 1–4, `task accounts:check` (`--category`, `--offline`, `--no-sheet`, `--json`), 13 unit tests in `tests/test_account_register.py`. Registry gained `zoho_books_match` on every business account (Lafayette checking's last4 corrected 3617 → 7011 per Books). | First live run: 39 errors, listed under "First run" below. Operator fixes the sheet and registry until it exits 0. |
| 2 | **DONE 2026-10-19.** Balance check (check 5, on by default, `--no-balances` to skip) and `task accounts:sync` (`--dry-run`, `--category`), 7 more unit tests. Sheet writes go through `worksheet.records.update` with criteria `"Account Last 4"="<last4>"` (verified live with a no-op write). | Live dry run: sync refused (39 membership errors), then 16 `balance_mismatch` on the rows that already link, e.g. Recurring ACH 1826 in the sheet vs 4853.21 in Monarch. Once membership is clean, `task accounts:sync` writes the balances and re-checks; a hand-edited balance then makes `accounts:check` exit 1. |
| 3 | Wire into `finance:sync-status`; retire `Yumyum Bank Accounts`; tests for every check function on fixture data. | `task finance:sync-status` fails loudly when the register drifts. |

Phase 0 is small and gates everything; phases 1 and 2 are each one sitting.

### First run of `task accounts:check` (2026-10-19, 39 errors)

Scope as built: depository and credit accounts only. Investment rows are skipped
(decision 2) and loans are skipped too, because the sheet keeps them on the separate
`Loans` tab with its own shape; a Loans ↔ Monarch-loans check is a later addition.
Monarch's hidden and deactivated connections are ignored (they are its stale duplicates,
e.g. "360 Checking (...4354)" next to the live "Recurring ACH (...4354)"). Closed live
accounts are ignored unless the registry lists them, in which case both must say closed.

| Kind | Count | What it means / who fixes it |
|------|------:|------------------------------|
| registry_not_in_sheet | 13 | Registry accounts with no sheet row: Mercury personal 6343 and 5237, Mercury business 4613/5396/7818, LFCU 7011/3617 (business) and 0621 (personal), Amex 1008/2004, Fidelity 0789, Wells 8694 and 4637. **Sheet:** add the rows (one per Zoho Books account, decision 1). |
| sheet_row_no_last4 | 7 | Coinbase, Apple, Bilt, Mercury ×2, Bluevine, M&T Savings. **Sheet:** fill Account Last 4 (or close the row). |
| sheet_not_in_registry | 5 | Sheet rows the registry lacks: Amex rows carry 5-digit "21008"/"72004" (registry has 1008/2004), M&T 2002/2010, Capital One 0474 (closed). **Sheet:** fix the Amex last4s, close 0474; **registry:** decide whether M&T is still real. |
| live_not_in_registry | 5 | Monarch has Investor Checking ...585, Mercury Personal Checking 6343 and Profit 5237 (registry has them but with `monarch_match: null`), LFCU Share Savings 0621, Mercury Vacation Fun 9620. **Registry:** set the match names / add the accounts. |
| sheet_row_untyped | 3 | Heartland, Nelnet, Bera 401k. **Sheet:** type them or delete (Heartland/Nelnet belong on Loans). |
| registry_no_match_name | 3 | BoA 5819 has no Zoho Books bank account at all; Mercury personal 6343/5237 have no `monarch_match`. |
| sheet_row_closed_in_notes | 1 | Capital One 0474 says "Closed" in notes. **Sheet:** add a `Status` column, value `closed`. |
| match_not_found | 1 | `CREDIT CARD (...7006)` is deactivated in Monarch; registry chase-wholefds-7006 should be closed. |
| registry_closed_live_open | 1 | navyfed-visa is `inactive` in the registry but open in Monarch. |

Running it: `task accounts:check` from the main checkout (`.env`, the Monarch session in
`.mm/` and the Books token cache live there; from a worktree set
`FINANCE_TOOLS_DATA=~/projects/myagents/finance-tools`).

## Decisions (operator, 2026-10-19)

1. **Business granularity:** one register row per Zoho Books bank account. Mercury
   sub-accounts, LFCU checking and savings, and Wells Signify each get their own row.
2. **Investments:** excluded from the personal check until the `Investments` tab is
   rebuilt. Rows with `Type = Investment` are skipped by every check; Monarch investment
   accounts are likewise ignored, so they cannot appear as "extra" on the Monarch side.
3. **Business balance truth:** the Zoho Books book balance (`balance`), not the bank feed
   (`bank_balance`). Feed-versus-book differences stay the job of `zoho_books_recon.py`.
4. **Tracking:** docs-only, like the October Zoho work. No GSD milestone is opened;
   progress is recorded in this file and the runbooks.

The converted native workbook kept the xlsx's id, `efajyb8b15e86a20a48e0b3edab6cf4fb18c5`
(phase 0 confirmed it reads through the Sheet Data API).

## Out of scope

Transactions, categorisation and reconciliation stay in `zoho_books_recon.py` and the
Monarch audit. This plan covers only the list of accounts and their current balances.
