# Monarch transaction categorization — plan and spike notes

Goal: categorize repeating Monarch Money transactions quickly and accurately, send only the unfamiliar
or low-confidence ones to an AI model, let a human review and correct everything on one page, and
write back to Monarch only after approval.

Status (2026-11-17): spike works end to end on 5 transactions except the AI call, which needs an
Anthropic credential on this machine (see Open items).

## Pipeline

| Step | Command | What it does |
|------|---------|--------------|
| 1. Rules | `task finance:categorize:rules` | Snapshots the rules stored inside Monarch to `data/categorize/monarch_rules.json` |
| 2. Run | `task finance:categorize -- --limit 50` or `-- --days 7` or `-- --since 2026-11-01` | Fetches the group, proposes a category per transaction, writes `data/categorize/runs/<run-id>/` |
| 3. Review | open `review.html` or the published artifact | Human edits picks, ticks what to apply, presses Approve |
| 4. Apply | `task finance:categorize:apply -- <run-id> --approved approved.json --execute` | Pushes category changes and clears needs-review; dry run without `--execute` |

Selection defaults to needs-review transactions only; `--all` includes reviewed ones.

### Confidence ladder (highest candidate wins)

| Source | Confidence | Where it comes from |
|--------|-----------|---------------------|
| `rule:<name>` | rule's own, default 0.95 | `data/categorize/rules.yaml`, first match wins |
| `monarch-rule:<id>` | 0.90 | Monarch's stored rules, translated into the same matcher |
| `history:n/total` | share × min(1, total/3) | the merchant's past reviewed transactions in Monarch |
| `monarch-default` | 0.50 | whatever Monarch or Plaid already assigned |
| `ai:<model>` | model-reported | one structured-output call for every item below the threshold |

Items whose best candidate is below `--ai-threshold` (default 0.8) go to the model; if the model is
unavailable they stay flagged "needs a human pick" and the review page offers an Ask Claude button
that uses the artifact's own sampling capability instead.

### Data folder `finance-tools/data/categorize/`

- `rules.yaml` — local rules in a Monarch-like shape (merchant / statement / amount / account criteria,
  category action, confidence). Committed.
- `monarch_rules.json` — raw snapshot of Monarch's rules plus pull time. Committed.
- `runs/<run-id>/` — `transactions.json` (raw), `proposals.json` (candidates and the pick per item),
  `review.html` (self-contained review page), `apply.log` after an execute. Gitignored.

### Review page

Self-contained HTML built from `templates/categorize_review.html` with the run embedded. It works
as a local file and as a published artifact. As an artifact it declares the `db`, `user` and
`sample` capabilities:

- every edit is saved to `runs/<run-id>/decisions/<transaction-id>` ({category_id, category_name, apply});
- Approve writes `runs/<run-id>` = {status: approved, approved_at, count};
- Copy approved JSON puts the same decisions on the clipboard in the shape `apply --approved` reads;
- Ask Claude on a flagged row asks the model from inside the page (no API key on the machine needed).

Carrying decisions from the artifact to the apply step: paste the copied JSON into `approved.json`,
or have Claude read the artifact's data store and write the file.

## Spike findings

- **Monarch's rules are readable.** `GetTransactionRules` over the GraphQL API returns 87 rules for this
  household. Every field the web app selects resolves except `mergeTransactionAction`.
- **86 rules set a category.** 61 translate cleanly (merchant eq/contains, amount eq/gt/lt/between,
  account, current category). 25 come back with every known criteria field null even though some
  have `recentApplicationCount > 0`, so their criteria live in a field no public query selects.
  They are kept in the snapshot but never matched. The monarchmoney-enhanced fork selects the same
  five criteria fields, so it does not know the extra field either.
- **Merchant history is a strong signal.** Citi 23/24 past transactions agree (0.96); Google Fiber and
  Navy Federal sit at 3/4 and 4/6 and were correctly handed to the AI/human tier.
- **Writes exist.** `update_transaction(category_id=…, needs_review=False)` is the apply primitive; the
  spike exercised it in dry-run only.

## Open items

1. **AI credential.** The only Anthropic CLI profile (`admin`) is OAuth without a refresh token, so
   the SDK cannot authenticate. Fix with `ant auth login` (stores a usable profile) or
   `ANTHROPIC_API_KEY` in `finance-tools/.env`. Model defaults to `claude-opus-5-5`
   (`CATEGORIZE_AI_MODEL` overrides).
2. **Hidden rule criteria.** Capture the web app's current `GetTransactionRules` request in DevTools
   (Network tab, filter `graphql`) and add the missing criteria field to `RULES_QUERY`.
3. **Rule actions beyond category.** Monarch rules can also rename the merchant, add tags, hide from
   reports, or split. Local rules only set a category for now.
4. **Promote learned rules.** After a few runs, merchants the AI or the human picked consistently
   should be offered as new `rules.yaml` entries (or created in Monarch via `createTransactionRuleV2`).
5. **Selection by account** (`--account 8206`) and a scheduled weekly run that publishes the review
   page and leaves a Todoist task.
