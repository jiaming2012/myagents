# Tasks: registry-renders-sheet

Ordered. Each task is one commit with the trailer `OpenSpec-Change: registry-renders-sheet`.
Nothing writes to the live sheet before task 7.

Status 2026-11-16: tasks 1 to 6 done on branch `worktree-registry-renders-sheet-impl`
(tasks 3 to 6 in one commit; `render_block` became `render()` in account_register.py over
the existing `write_cells` / `delete_rows` / `blank_rows_below` primitives, all proven on the
sandbox and the live gap row). `--migrate --dry-run` run against the live sheet: 35 rows map,
19 do not (listed by the run) and must be registered or deleted before task 7.

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
