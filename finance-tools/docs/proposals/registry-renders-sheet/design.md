# Design: registry-renders-sheet

## 0. Decisions (operator, 2026-11-16: recommendations accepted as written)

| # | Decision |
|---|---|
| D1 | Identity, balances and card terms are rendered from the registry; `notes` and `site` stay operator-owned; `Recurring`, `Routing Number`, `Target Balance`, `Amount Due`, `Last Paid`, `Last Statement End Date`, `Days in Billing Cycle`, `Next Statement Date`, `Scheduled Payments` are never written |
| D2 | The Credit Cards tab is retired by hand after the first clean render |
| D3 | Registry order, closed accounts last |
| D4 | Closed accounts render with Status `closed` for one statement cycle after closing, then drop from the render; the registry entry keeps `status: closed` |
| D5 | The totals row is never touched; the render refuses when the block would reach it |

## 1. Column ownership

Every column on Chart of Accounts gets exactly one owner. The tool writes only the columns
it owns; it never reads a tool-owned column as input (the registry is the input) and never
writes an operator-owned column.

| Sheet column | Owner | Source of the rendered value | Note |
|---|---|---|---|
| Registry Id | tool | `accounts[].id` | New column, the join key. Hidden or narrow is fine |
| Name | tool | `accounts[].name` | |
| Company | tool | `accounts[].institution` | |
| Account Last 4 | tool | `accounts[].last4` or blank | Display only; no longer a key |
| Type | tool | `category` × `type` → Personal/Business Debit/Credit | Loans and investments are not rendered |
| Status | tool | `active`, or `closed` when `status: closed` / `inactive: true` | |
| Debit Balance / Credit Balance | tool | live Monarch display balance (personal) or Zoho Books book balance (business) | Blank when the live source is unavailable; never a stale copy |
| As of | tool | today's date when a balance was written | Added if missing |
| AutoPay Type | tool | the `payments[]` entry whose `pays_account` is this id: `autopay_type` (`min`/`full`), else `none` | Credit rows only |
| AutoPay Date | tool | same entry's `day_of_month` | Credit rows only |
| Credit Limit | tool | `debts.yaml` entry for this id: `credit_limit` | New field on ordinary debts (today only promo entries carry it) |
| APR | tool | `debts.yaml` `apr`, rendered with `(assumed)` when omitted | |
| Due Date | tool | `debts.yaml` `due_day` | New field; the sheet's value is the seed |
| notes | operator | — | Preserved verbatim across renders |
| site | operator | — | Preserved; could move to `accounts[].site` later |
| Recurring, Routing Number, Target Balance, Amount Due, Last Paid, Last Statement End Date, Days in Billing Cycle, Next Statement Date, Scheduled Payments | operator (legacy) | — | Never written. Operator may delete the columns; the render does not depend on them |

Registry additions implied by the table, all optional fields:

- `debts.yaml` entries: `credit_limit`, `due_day` on any card (today `credit_limit` exists
  only under promotional APRs).
- No change to `payments.yaml` identity fields. `last4` may stay `null` where the
  institution gives no unique four digits.

## 2. The render

`task finance:accounts:sync` (name kept; description changes) does, in order:

1. **Gather**: registry, `debts.yaml`, Monarch, Zoho Books, and the current sheet block
   (rows 2 .. last record row, via `records.fetch`, plus the header map).
2. **Check live sources** (unchanged): every open personal entry resolves to exactly one
   active Monarch account and vice versa; same for business entries and Books. Errors are
   printed; balances for erroring accounts are rendered blank, not skipped. The render
   itself is not blocked by live-source errors, because identity does not depend on them.
3. **Plan rows**: one target row per rendered registry account, in registry order, closed
   accounts last (D3, D4). For each, the owned cells per section 1.
4. **Match existing rows** by `Registry Id`. Rows that match keep their operator-owned
   cells. Rows with no id are legacy: the one-time migration (section 4) handles them;
   after migration an id-less row inside the block is an error, not something to guess at.
5. **Fit check**: the block needs rows 2 .. 1+N. The first non-blank row below the
   current block that is not part of the block (the totals row) must be at or below row
   2+N. Otherwise stop before writing anything: "insert k blank rows above the totals row".
6. **Write**: for each target row, write only the owned cells whose current content
   differs (string compare after formatting). Rows that existed but are no longer rendered
   are blanked, and the block compacted by deleting the blank rows inside it
   (`worksheet.rows.delete`), so `records.fetch` keeps seeing one contiguous block.
7. **Report**: every cell changed, as `row N 'Name' Column: old -> new`, then counts:
   rendered, unchanged, added, retired. `--dry-run` prints the same without writing.

### Why by position and not by criteria

Probed live on 2026-11-16 (see `docs/account-register-plan.md`, phase 2 correction):
`worksheet.records.add` appends below the tab's last used row, which on this tab is below
the totals; a `records.update` criteria on a digit-string column never matches. Positioned
writes (`worksheet.csvdata.set` by row and column) are the only reliable primitive, and
`zoho_sheet.insert_record`, `write_cells`, `delete_rows`, `header_columns`,
`last_record_row` and `row_is_blank` already wrap them. `render_block` composes those.

## 3. What the check becomes

`task finance:accounts:check` reports:

- `match_not_found`, `match_ambiguous`, `live_not_in_registry`, `live_claimed_twice`,
  `live_closed`, `registry_closed_live_open`, `last4_mismatch`, `kind_mismatch`,
  `registry_no_match_name`, `registry_duplicate_id` (new; replaces duplicate last4),
  `source_unavailable`: all unchanged in meaning.
- `balance_mismatch`: the sheet's rendered balance differs from live (only possible when
  someone typed over it, or the render is stale). Fixed by rendering.
- `sheet_drift` (new): a tool-owned cell differs from what the registry renders. One
  error per row, listing the columns. Fixed by rendering. Replaces `sheet_not_in_registry`,
  `registry_not_in_sheet`, `sheet_row_no_last4`, `sheet_row_untyped`,
  `sheet_row_type_unknown`, `closed_mismatch`, `sheet_credit_autopay`,
  `sheet_row_closed_in_notes`, all of which are now impossible after a render.
- `sheet_unregistered_row` (new): a row inside the block with no `Registry Id`. The
  operator deletes it or adds the account to the registry.

`weekly_health.py` section 6 shows live-source errors and a one-line render status.

## 4. One-time migration

A `--migrate` run (one-off; no dedicated task target, run by hand once):

1. Add the `Registry Id` column if absent (header write at column 24, or the first free
   column).
2. For each existing row: match a registry account by last four and owner, else by
   normalised name and institution. Write the id. Print the mapping.
3. Print the rows it could not map (today: Coinbase, Apple, Bilt ×2, Bluevine, Wex Health,
   Navy Federal 3274, Mercury 8478, the "Pure Craft" Mercury row, ECSI ×2, CRI, Bera 401k,
   the stray row 54). The operator either registers each or deletes the row before the
   first render.
4. Seed `debts.yaml` `credit_limit` / `due_day` from the sheet's Credit Limit and Due Date
   columns where the registry has nothing, printing what it seeded. This is the only time
   the tool reads an operator column.

The first real render runs only when `--migrate` reports zero unmapped rows.

## 5. Rules, named

So the logic is reviewable outside the code (see
`~/projects/statemate/study/08-business-logic-and-decision-tables.md`), each rule has an
id that appears in the spec, in the implementing function's docstring, and in a test name:

| Id | Rule |
|---|---|
| RND-SCOPE | Render depository and credit accounts only |
| RND-ORDER | Registry order; closed accounts last |
| RND-CLOSED | Closed = `status: closed` or `inactive: true`; rendered as Status `closed`, dropped after one statement cycle |
| RND-TYPE | Type = owner × kind, the four sheet types |
| RND-KEY | Join by `Registry Id`, never by last four or name |
| RND-OWN | Write only tool-owned columns; never read them as input |
| RND-DIFF | Write only cells whose content differs |
| RND-FIT | Refuse the whole render if the block would reach the totals row |
| RND-BAL | Balance = Monarch display balance (personal) or Books book balance (business); blank when unavailable |
| RND-TERMS | AutoPay from the paying `payments[]` entry; limit, APR, due day from `debts.yaml` |
