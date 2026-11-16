# Spec delta: account-register

## MODIFIED Requirements

### Requirement: The registry is the single source of account identity (RND-KEY, RND-OWN)

`payments.yaml` `accounts:` SHALL be the only place an account's identity (id, name,
institution, last four, type, category, status) is entered. The Chart of Accounts tab
SHALL carry a `Registry Id` column, and the tool SHALL join sheet rows to accounts by that
column only.

#### Scenario: a renamed account

- GIVEN registry account `amex-platinum-2004` is renamed from "Platinum Card" to "Amex Platinum"
- WHEN the sync runs
- THEN the sheet row whose Registry Id is `amex-platinum-2004` has Name "Amex Platinum"
- AND no second row is created

#### Scenario: an account with no unique last four

- GIVEN registry account `lafayette-savings-0621s` has `last4: null`
- WHEN the sync runs
- THEN the account has a rendered row, keyed by its id, with Account Last 4 blank

### Requirement: The sync renders the registry into the sheet (RND-SCOPE, RND-ORDER, RND-TYPE, RND-CLOSED)

The sync SHALL write one row per depository or credit registry account, in registry order
with closed accounts last, with tool-owned columns filled from the registry and the live
sources. It SHALL NOT prompt per account.

#### Scenario: a new registry account

- GIVEN `payments.yaml` gains account `schwab-investor-checking-585`
- WHEN the sync runs
- THEN a row for it appears at the end of the open accounts with Name, Company, Type and Status filled
- AND the run reports it as added

#### Scenario: a closed account

- GIVEN registry account `chase-wholefds-7006` has `status: closed`
- WHEN the sync runs
- THEN its row is rendered with Status `closed` among the closed rows
- AND the row is not deleted

### Requirement: Operator-owned columns are preserved (RND-OWN)

The sync SHALL NOT write any column not listed as tool-owned in the design, and SHALL
carry those cells unchanged when a row moves.

#### Scenario: notes survive a re-order

- GIVEN row "Robinhood" has notes "ask about limit increase"
- AND a new account is added above it in registry order
- WHEN the sync runs
- THEN the Robinhood row, at its new position, still has those notes

### Requirement: Only differing cells are written (RND-DIFF)

The sync SHALL read the current block first and write only cells whose content differs,
reporting each change as old -> new.

#### Scenario: nothing changed

- GIVEN the sheet already equals the render
- WHEN the sync runs
- THEN no write is made
- AND the report says "rendered N rows, 0 changed"

### Requirement: The render fits above the totals row or does nothing (RND-FIT)

Before writing, the sync SHALL verify the rendered block fits in the blank rows above the
first non-block row (the totals row). If it does not, it SHALL write nothing and say how
many rows to insert.

#### Scenario: the gap is too small

- GIVEN 7 blank rows above the totals row and 9 rows to add
- WHEN the sync runs
- THEN no cell is written
- AND the message is "insert 2 blank rows above the totals row (row 62), then re-run"

### Requirement: Balances come from the live sources (RND-BAL)

For every rendered row the sync SHALL write the live balance (Monarch display balance for
personal accounts, Zoho Books book balance for business) and the As of date, and SHALL
leave the balance blank when the live source is unavailable or the match is unresolved.

#### Scenario: live source down

- GIVEN Monarch returns an auth error
- WHEN the sync runs
- THEN identity columns are still rendered
- AND personal Debit/Credit Balance cells are left as they were, with `source_unavailable` reported

### Requirement: Card terms render from the registry (RND-TERMS)

AutoPay Type and AutoPay Date SHALL come from the `payments[]` entry whose `pays_account`
is the card; Credit Limit, APR and Due Date SHALL come from the card's `debts.yaml` entry.

#### Scenario: a card with no paying entry

- GIVEN credit account `bilt-mastercard-4607` has no `payments[]` entry paying it
- WHEN the sync runs
- THEN AutoPay Type renders as `none`
- AND `task finance:gaps` reports the missing payment entry (unchanged behavior)

## REMOVED Requirements

### Requirement: Sheet and registry must agree on identity

Reason: the sheet no longer holds hand-entered identity. The error kinds
`sheet_not_in_registry`, `registry_not_in_sheet`, `sheet_row_no_last4`,
`sheet_row_untyped`, `sheet_row_type_unknown`, `closed_mismatch`, `sheet_credit_autopay`
and `sheet_row_closed_in_notes` are removed.

### Requirement: The sync offers to add rows one yes/no at a time

Reason: rows are rendered, not offered. `--add` is removed; `--dry-run` remains.

## ADDED Requirements

### Requirement: Unregistered rows inside the block are errors

A row inside the rendered block with no `Registry Id` SHALL be reported as
`sheet_unregistered_row` and SHALL block the render until the operator registers or
deletes it.

#### Scenario: a hand-added row

- GIVEN the operator types a new row "Wex Health" into the block with no Registry Id
- WHEN the sync runs
- THEN the run stops with `sheet_unregistered_row` for that row
- AND nothing is written
