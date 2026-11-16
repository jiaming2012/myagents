# Proposal: payments.yaml is the single source; the Chart of Accounts is rendered from it

Change slug: `registry-renders-sheet`
Status: DRAFT, awaiting operator sign-off (2026-11-16)
Layout: OpenSpec change (proposal, spec delta, design, tasks). The myagents repo has no
`openspec/` yet; after `/spec-init` this folder moves to `openspec/changes/registry-renders-sheet/`
unchanged.

## Why

The account register (`finance-tools/account_register.py`, built 2026-10-19) treats the
Bills sheet's Chart of Accounts and `payments.yaml` as two peers that must agree, and
proves it with a three-sided check: sheet vs registry, registry vs Monarch, registry vs
Zoho Books. Five facts about every account (name, company, last four, type, status) are
therefore typed twice, once in each place, and every disagreement is an error a person
resolves by hand.

What that cost on 2026-11-16, one day of use:

- 27 of the 39 original membership errors were double-entry artifacts: rows missing a
  last four, rows missing a Type, five-digit keys on the Amex rows, and four duplicate
  rows created when the tool added accounts that already existed under slightly
  different hand-typed identities.
- Two silent failures in the sync were found only because the sheet was the thing being
  reconciled: rows appended where the reader never looks, and balance writes keyed on a
  column Zoho cannot match. Both exist because the tool edits a hand-maintained table
  instead of owning it.
- The per-account facts that make the tools useful (nicknames, hub role, match names,
  which account pays which card, APR, limits) cannot live in a sheet row anyway, so the
  registry is already the richer side and the sheet can never replace it.

## What changes

1. **`payments.yaml` (with `debts.yaml` for card terms) is the only place account
   identity and terms are entered.** The sheet's identity columns are never edited by
   hand again.
2. **The Chart of Accounts becomes a rendering.** `task finance:accounts:sync` writes the
   registry into the tab: one row per registry account in registry order, identity
   columns and live balances filled by the tool, closed accounts rendered with
   `Status = closed` rather than deleted. Rows for accounts the registry no longer lists
   are removed from the rendered block.
3. **A `Registry Id` column becomes the join key**, replacing the last four. This also
   fixes the two accounts that cannot carry a unique last four today (Lafayette Share
   Savings, which shares the credit union's member number, and Schwab, where Monarch shows
   three digits).
4. **The membership check shrinks to two sides**: registry vs Monarch (personal) and
   registry vs Zoho Books (business). "Sheet vs registry" stops being an error class; the
   render makes it true, and reports what it changed.
5. **Operator columns on the sheet are either absorbed into the registry or declared
   operator-owned.** The design lists every column with its owner. Card terms (autopay
   type and day, APR, credit limit, minimum) already have registry homes in the payments
   section and `debts.yaml` and are rendered from there. Free-text `notes` and `site`
   stay operator-owned and are preserved across renders.

## What does not change

- Transactions, categorisation and reconciliation (`zoho_books_recon.py`, Monarch audit).
- The Investments and Loans tabs (out of the register's scope since 2026-10-19).
- The Recurring Charges tab.
- The live-source checks: match names must resolve one-to-one, balances must agree to the
  cent, closed on one side only is still an error.
- The Sheet Data API's limits: no row insert, so the rendered block must fit above the
  totals row; the render stops and says so when it would not.

## Decisions the operator must make before implementation

| # | Decision | Recommendation |
|---|---|---|
| D1 | Which sheet columns move into the registry, which stay operator-owned, which are dropped | Per the design's ownership table: identity and card terms rendered; `notes`, `site` operator-owned; `Recurring`, `Routing Number`, `Target Balance`, `Amount Due`, `Last Paid`, statement-date columns dropped from the render (left untouched if present, never written) |
| D2 | The Credit Cards tab | Retire it. It is a third copy of card identity and payment day; everything on it is now in the registry and renders into Chart of Accounts |
| D3 | Row order | Registry order (the file groups by institution), closed accounts last |
| D4 | Closed accounts | Rendered with Status `closed` for one full statement cycle after closing, then dropped from the render; the registry entry keeps `status: closed` forever |
| D5 | Totals row | Left exactly where it is; the render refuses rather than move it. If the register outgrows the gap, the operator inserts rows above the totals once |

## Impact

- `finance-tools/account_register.py`: `--sync` becomes a render; `check_sheet_vs_registry`
  and `plan_additions` are deleted; `sheet_accounts` reads `Registry Id`.
- `finance-tools/zoho_sheet.py`: gains `render_block` (write rows 2..N by position, blank the
  rows past N up to the gap), built on the positioned writes added 2026-11-16.
- `finance-tools/payments.yaml`: no schema change for identity; card-term fields that the
  sheet had and the registry lacked are added where the design says.
- `finance-tools/Taskfile.yml`: `accounts:sync` description changes; `accounts:check`
  loses `--no-sheet` (the sheet is no longer a check input) and gains `--no-render`.
- `finance-tools/docs/account-register-plan.md`: superseded for membership; balances
  section still applies.
- `weekly_health.py` section 6: reports live-source errors and "rendered n rows" only.
- Tests: the sheet-vs-registry tests go; render tests come in (pure planning function
  plus one positioned-write test per behavior).

## Risks

- **Overwriting a hand edit.** Mitigated by the ownership table: the tool writes only the
  columns it owns, and logs every cell it changed with old and new value.
- **The totals row.** Its formulas cannot be read through the API and its sums do not
  match the column sums, so it is never touched. The render fits above it or stops.
- **Rate limits.** One positioned write per rendered cell; about 60 accounts × 8 owned
  columns on a full render. Mitigated by writing only cells that differ from the current
  sheet content, which the render reads first.
- **Identity migration.** Existing rows have no `Registry Id`. A one-time migration maps
  rows to registry ids by last four and name, writes the id column, and lists the rows it
  could not map for the operator to delete or register.
