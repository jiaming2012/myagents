# Tasks: registry-renders-sheet

Ordered. Each task is one commit with the trailer `OpenSpec-Change: registry-renders-sheet`.
Nothing writes to the live sheet before task 7.

Status 2026-11-16: tasks 1 to 6 done on branch `worktree-registry-renders-sheet-impl`
(tasks 3 to 6 in one commit; `render_block` became `render()` in account_register.py over
the existing `write_cells` / `delete_rows` / `blank_rows_below` primitives, all proven on the
sandbox and the live gap row). `--migrate --dry-run` run against the live sheet: 35 rows map,
19 do not (listed by the run) and must be registered or deleted before task 7.

Status later 2026-11-16 (branch `worktree-registry-migration`): task 7 DONE. The 19 rows were
settled (5 closed-and-unknown or empty rows deleted; ECSI ×2 and CRI retyped `Loan`, Bera 401k
`Investment`, both pass-through types now; Coinbase, Wex Health, Bilt Obsidian, Pure Craft
Mercury and Mercury 8478 registered with `feed: manual`; Bluevine registered closed; Navy
Federal Visa given last4 3274 and reopened), `--migrate` run (columns added, 39 rows mapped),
four name-mismatch rows given ids by hand, debts.yaml seeded. Task 8 is BLOCKED on one UI
action: the totals row sits at row 56 inside the block and the render needs 64 rows (47
accounts + 17 Investment/Loan pass-through rows), so the operator must insert at least 10
blank rows above the totals row in the sheet; the API has no row insert. Then
`task finance:accounts:sync -- --dry-run`, review, `task finance:accounts:sync`.

Status evening 2026-11-16: task 8 DONE (main 133913f). The operator inserted 20 rows; the
first render tripped Zoho's per-minute limit at one call per cell, so the render now writes
one whole row per call and waits out error 2950; a half-finished render's duplicate ids are
reported, not blocking. Full render: 63 rows, 52 written, 496 cells; six rows the insert had
cut off below the totals were deleted by hand afterwards. A second dry run reports "nothing to
change" and `task finance:accounts:check` shows one error, boa-cash-rewards-5819 with no
zoho_match (pre-existing). Remaining: task 9 (retire the Credit Cards tab by hand), task 10
(docs), task 11 (archive after `/spec-init`).

1. **Operator sign-off on D1 to D5** in `proposal.md`. Record the answers at the top of
   `design.md`.
2. **Registry fields.** Add optional `credit_limit` and `due_day` to ordinary `debts.yaml`
   entries (schema comment, loader, one test). No values yet.
3. **Pure render planner.** `plan_render(config, debts, live_by_owner, sheet_block) ->
   {rows, changes, retired, fit}` in `account_register.py`, covering RND-SCOPE, RND-ORDER,
   RND-CLOSED, RND-TYPE, RND-KEY, RND-OWN, RND-DIFF, RND-FIT, RND-BAL, RND-TERMS. One test
   per rule, named by rule id. No I/O.
4. **Sheet primitive.** `zoho_sheet.render_block(resource_id, tab, rows, header_map)`
   built on `write_cells` and `delete_rows`: writes differing cells by position, blanks and
   compacts retired rows, never touches rows beyond the block. Exercised once on the
   orphan sandbox workbook (`efajy8c81…`) and documented in the docstring.
5. **Checks.** Delete `check_sheet_vs_registry` and `plan_additions`; add `sheet_drift`,
   `sheet_unregistered_row`, `registry_duplicate_id`; `sheet_accounts` reads `Registry Id`.
   Update `format_errors`, the `--json` shape, and `weekly_health.py` section 6.
6. **CLI and Taskfile.** `--sync` = render (`--dry-run` kept, `--add` removed);
   `--migrate` added; `--no-sheet` removed from check, `--no-render` added. Update
   `accounts:check` and `accounts:sync` descriptions.
7. **Migration on the live sheet.** Run `--migrate` from the main checkout: add the column,
   write ids, seed `debts.yaml` from Credit Limit / Due Date, print the unmapped rows.
   Operator registers or deletes each unmapped row. Re-run until zero.
8. **First render.** `--sync --dry-run`, review the change list, then `--sync`. Confirm
   `task finance:accounts:check` reports live-source errors only.
9. **Retire the Credit Cards tab** (D2) by hand in Zoho, once the first render is clean.
10. **Docs.** Supersede the membership sections of `docs/account-register-plan.md`;
    add the ownership table to the runbook; update memory notes.
11. **Archive** the change (`/opsx:archive` after `/spec-init`, or move this folder to
    `docs/proposals/archive/` if the repo stays without `openspec/`).
