# Plan: the Bills sheet as the account register, enforced one-to-one

Status: proposal, 2026-10-19. Nothing here is built yet.

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
`Credit Balance` / an `As of` column into the matched sheet rows (Sheet Data API
`records.update`, scope `ZohoSheet.dataAPI.UPDATE` already held), then runs the check.
Membership errors are never auto-fixed: a missing or extra account is always a human
decision, so sync refuses to write if any membership check fails.

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
| 0 | Spike: read the converted native sheet by its WorkDrive id through the Sheet Data API (`worksheet.list`, records fetch) with the existing token. | A `zoho_sheet.py --records <id> --sheet "Chart of Accounts"` prints the rows. If the API refuses WorkDrive-native files, fall back to WorkDrive download + xlsx parse and record that in the runbook. |
| 1 | Membership check: `account_register.py` with checks 1–4, `task accounts:check`, `--offline`. Registry gains `zoho_books_match`. | First run lists every real gap (Mercury rows, untyped rows, Books sub-accounts, Xero-era tab). Operator fixes the sheet until it exits 0. |
| 2 | Balance check (check 5) and `task accounts:sync`. | `accounts:sync && accounts:check` exits 0; a hand-edited balance in the sheet makes `accounts:check` exit 1. |
| 3 | Wire into `finance:sync-status`; retire `Yumyum Bank Accounts`; tests for every check function on fixture data. | `task finance:sync-status` fails loudly when the register drifts. |

Phase 0 is small and gates everything; phases 1 and 2 are each one sitting.

## Decisions needed before phase 1

1. **Business granularity.** One register row per Zoho Books bank account (recommended,
   strict one-to-one) or one row per institution with sub-accounts summed?
2. **Investments.** Monarch tracks them; are they in scope for the personal check, or
   excluded until the `Investments` tab is rebuilt?
3. **Balance truth for business.** Zoho Books book balance (recommended, it is what the
   books say) or the bank feed balance? The two differ whenever lines are uncategorised,
   and `zoho_books_recon.py` already flags that difference.
4. **Where this is tracked.** `.planning/` has a closed v1.0 milestone; this would be
   milestone v1.1 if the GSD workflow is to track it, or it stays a docs-only plan like
   the October Zoho work.

## Out of scope

Transactions, categorisation and reconciliation stay in `zoho_books_recon.py` and the
Monarch audit. This plan covers only the list of accounts and their current balances.
