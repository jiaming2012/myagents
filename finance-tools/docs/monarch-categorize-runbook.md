# Monarch categorize — end-to-end runbook (this Mac)

Five pastes and two clicks. Every command uses `latest`, so nothing has to be typed by hand.

## Once per terminal

```bash
cd /Users/jamal/projects/myagents/.claude/worktrees/monarch-categorize-spike
export FINANCE_TOOLS_DATA=/Users/jamal/projects/myagents/finance-tools
```

The export points the worktree at the main checkout's Monarch login (`finance-tools/.mm/`), so no
re-login is needed. After the branch is merged into `main`, drop the export and run from
`/Users/jamal/projects/myagents` instead.

## 1. Refresh the rules stored in Monarch (optional, seconds)

```bash
task finance:categorize:rules
```

Expect: `Pulled 87 rules from Monarch`, `86 translated into matcher form, 0 have criteria the query cannot see`.

## 2. Run the categorizer

```bash
task finance:categorize -- --limit 5 --open
```

What happens:

- Five needs-review transactions are fetched and scored against local rules, Monarch's rules, and
  merchant history.
- Rows under the 0.8 threshold go to Claude: a Claude Code session opens in this terminal. **Click
  Yes** when it asks to write `ai_proposals.json`. When it prints "Classification complete — please
  exit", **type `/exit`**. The run resumes by itself.
- The review page opens in the browser (`--open`). The terminal prints the proposal table and the
  run id.

Skip the Claude step with `task finance:categorize:no-ai -- --limit 5 --open`. Other selections:
`-- --days 7`, `-- --since 2026-11-01`, add `--all` to include already-reviewed rows.

## 3. Review in the browser

On the page that opened:

1. Change any category from its dropdown; tick or untick **Apply** per row. Rows flagged "needs a
   pick" start unticked.
2. **Click Approve and copy JSON.** The status line confirms the count and the copy.

(As a local file the page has no saved state and no Ask Claude button; both only exist when the
page is published as a claude.ai artifact.)

## 4. Bring the approval into the run and preview

```bash
task finance:categorize:approve -- latest
```

Expect: `Saved N decision(s) -> data/categorize/runs/<id>/approved.json`, then the dry-run table
of exactly what will change (category moves and "mark reviewed"), ending in `Dry run.`

## 5. Write to Monarch

```bash
task finance:categorize:apply -- latest --execute
```

Expect: `Applied N/N; log: data/categorize/runs/<id>/apply.log`. Then check Monarch: those
transactions no longer show in Needs Review and carry the chosen categories. A re-run of step 2
should fetch a different set, since the applied rows are reviewed now.

## If something goes wrong

| Symptom | Fix |
|---------|-----|
| `Monarch rejected the saved credentials (401)` | `task finance:balances:login` from `/Users/jamal/projects/myagents`, then retry |
| `` `claude` CLI not found on PATH `` | install Claude Code, or use `task finance:categorize:no-ai` |
| `Claude did not write ai_proposals.json` | re-run step 2, or use the no-ai task |
| `Clipboard does not hold JSON` | on the review page click Approve and copy JSON again, then retry step 4 |
| `Clipboard holds decisions for run X, not Y` | the page open in the browser is an older run; open `data/categorize/runs/<latest>/review.html` |
| Want to change a verdict after step 4 | edit `data/categorize/runs/<id>/approved.json` (set `"apply": false` or a new `category_id`), then step 5 |

Unit tests for the matcher, the AI file contract and the apply planner:

```bash
cd finance-tools && python -m pytest tests/test_monarch_categorize.py -q
```
