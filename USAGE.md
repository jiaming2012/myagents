# myagents — usage & daily playbook

Personal command-line tools, all driven through [Task](https://taskfile.dev/).
Run `task` (no args) from this directory for the full list.

```
myagents/
├── productivity-tools/   Python — iCloud calendar + Todoist
├── email-tools/          Go     — Gmail (+ Zoho) cleanup, inbox triage
└── finance-tools/        Python — Monarch, Xero, payment forecasting
```

Tasks are namespaced by tool: `task productivity:…`, `task email:…`,
`task finance:…`. Several follow the same pattern: **pull → analyze
(Claude) → apply (Python/Go)**. Claude only ever proposes changes to a
JSON file in `.cache/`; you review before any `apply` step hits a live
system.

---

## Capabilities

### productivity — iCloud Calendar + Todoist

**Todoist**

| Task | What it does |
|------|--------------|
| `productivity:todoist:pull` | Fetch today / tomorrow / overdue tasks + tasks completed today → `.cache/todoist/snapshot.json` |
| `productivity:todoist:discuss` | Pull + interactive Claude REPL: feasibility read on today's plan, INBOX TRIAGE of unsorted captures, and constraint-shaping conversation. Read-only by default. |
| `productivity:todoist:discuss -- --triage` | Same as `discuss`, but Claude ends the session by writing reshuffle proposals to `.cache/todoist/reschedules.json` — review, then run `todoist:reschedule` |
| `productivity:todoist:evening` | Pull + Claude reviews the day: DONE vs NOT DONE, proposes reschedules for incomplete non-recurring tasks, suggests `grp_*` labels for natural batches → writes `.cache/todoist/reschedules.json` |
| `productivity:todoist:view` | Interactive TUI over `reschedules.json` — scope tabs (All / Inbox / Overdue / Labeled / Recurring), batch tabs (per `grp_*`), per-item checkbox + detail pane, `u` applies selected directly to Todoist (no `reschedule` call needed) |
| `productivity:todoist:reschedule` | Non-interactive batch apply of the reviewed proposals (priority order, p1 first); merges new labels with existing ones; archives the file on success |

**Inbox project convention.** Todoist's default "Inbox" project is the
unsorted bucket — anything captured quickly without a project or date
lands there. `discuss` surfaces these as an explicit **INBOX TRIAGE**
section so they do not silently rot. For each Inbox item Claude will
ask whether to (a) assign a project, (b) set a real due date, (c) add
a `grp_*` batch label, or (d) complete or delete. In `--triage` mode
those decisions land in `reschedules.json` as `new_due` and
`add_labels` records — project moves still happen manually in the
Todoist UI for now.

**iCloud Calendar (events sync from iOS via iCloud)**

| Task | What it does |
|------|--------------|
| `productivity:calendars:pull` | Dump events in a window as JSON + cache snapshot. Vars: `DAYS=14`, `FROM=2026-06-04 TO=2026-06-08`, `CALENDAR=Home` |
| `productivity:calendars:list` | List calendars with writable / read-only status |
| `productivity:calendars:apply` | Apply `create` / `update` / `delete` proposals from `.cache/calendars/proposals.json` (Claude writes; you review; this commits) |

### email — Gmail cleanup + inbox triage (Go)

| Task | What it does |
|------|--------------|
| `email:inbox` | Full pipeline — download → Claude classifies (topic / urgency / action items) → TUI viewer |
| `email:inbox:download` | Download new email from all configured accounts |
| `email:inbox:analyze` | Claude reads `.cache/inbox/emails.json` and writes `insights.json` |
| `email:inbox:view` | TUI to browse classified email |
| `email:inbox:status` | Progress of download / analysis |
| `email:cleanup` | Interactive prompt to delete old / promo email |
| `email:cleanup:dry` | Preview what cleanup would delete (no prompts, no writes) |
| `email:cleanup:recent` | Cleanup over the last 7 days |
| `email:cleanup:old` | Cleanup over emails older than 30 days |
| `email:auth:gmail EMAIL=user@gmail.com` | One-time OAuth for a Gmail account |
| `email:auth:zoho` | One-time OAuth for Zoho |

### finance — Monarch, Xero, payment forecasting (Python)

| Task | What it does |
|------|--------------|
| `finance:balances` | Monarch Money account balances |
| `finance:payments:today` | Today's scheduled payments |
| `finance:payments` / `:month` | Payment events for next 7 / 30 days |
| `finance:forecast` / `:week` / `:month` | Payment forecast over a window |
| `finance:forecast:email` / `:email-weekly` | Email the forecast summary |
| `finance:bill-map` / `:month` | Bills mapped to funding accounts with balances |
| `finance:budget` | Monarch budget + goals tracker |
| `finance:health` | Weekly financial health check (all checks) |
| `finance:health:email` / `:preview` | Email or HTML preview of the health report |
| `finance:audit` | 7-day transaction coverage audit |
| `finance:report` / `:week` | Coverage report |
| `finance:balances:login` | Re-login to Monarch (interactive) |
| `finance:xero:auth` | Xero OAuth flow |

---

## Daily playbook

A morning check-in (5 min) and an evening wrap-up (10 min). Both lean on
the same pattern: see the state, let Claude shape it, you commit.

### Morning — orient and plan (≈5 min)

```bash
# 1. What did the world send overnight?
task email:inbox             # download + Claude triage + TUI to skim urgent items

# 2. What's already on the calendar today?
task productivity:calendars:pull DAYS=1

# 3. What did I commit to doing?
task productivity:todoist:discuss -- --triage
#   → Claude opens an interactive feasibility chat:
#     - opening read: overdue debt, p1+p2 count, hour budget vs ~6h day
#     - INBOX TRIAGE: unsorted captures — pick a project, a date,
#       a grp_* label, or complete/delete
#     - then asks what constraints you want to apply today
#     (push back if the plan is unrealistic — Claude has the counts)
#   → ends by writing reshuffle proposals to
#     productivity-tools/.cache/todoist/reschedules.json. 

# 4. Review + apply. Two ways — pick one:
task productivity:todoist:view          # interactive TUI: skim, deselect anything wrong, hit `u` to apply
task productivity:todoist:reschedule    # or: non-interactive batch apply of the whole file
```

Optional, depending on the day:

```bash
task finance:payments:today  # if today is a known bill day
task finance:balances        # quick "am I solvent" check before spending
```

### During the day — quick conversational changes

These are not on a schedule — invoke through Claude as needed.

- **Schedule something on the calendar**: ask Claude in this repo's
  conversation ("schedule lunch with Alex Friday at 12"). Claude writes
  to `.cache/calendars/proposals.json`. Review the file, then:
  ```bash
  task productivity:calendars:apply
  ```
- **Capture a one-off task**: add directly in Todoist (or via the
  Todoist MCP if you have it wired up).

### Evening — close the loop (≈10 min)

```bash
# 1. Review what landed today and decide what slips
task productivity:todoist:evening
#   → Claude prints:
#     - DONE TODAY (grouped by project, [pN] tagged)
#     - NOT DONE (sorted p1→p4)
#     - BATCHES (proposed grp_* groupings across today/tomorrow/overdue)
#     - RESCHEDULES (proposed new dates by priority)
#   → Writes .cache/todoist/reschedules.json

# 2. Review + apply. Either:
task productivity:todoist:view
#   → TUI loads reschedules.json + enriches from snapshot.json. Tab through
#     batches, hit space to drop anything you disagree with, `u` to apply
#     selected — POSTs land per-item with live progress. Failed items stay
#     in the file with an error marker so you can fix and re-run.

# ...or for the whole file at once, no interactive review:
task productivity:todoist:reschedule
#   → Applies p1 first; rescheduled tasks move; new grp_* labels merge
#     with existing labels; file archived to reschedules.applied.json

# 4. (Weekly, e.g. Sunday night) Money + inbox sweep.
task finance:health           # full health snapshot in the terminal
task finance:health:email     # or send it to your inbox
task email:cleanup:dry        # preview what's safe to delete
task email:cleanup            # commit if the preview looks right
```

---

## The pull → analyze → apply pattern

Three tools follow the same review-loop shape so nothing automated ever
mutates a live system without you reading the proposal first:

| Domain   | pull                       | analyze (Claude)                | proposal file                                  | apply                           |
|----------|----------------------------|---------------------------------|-----------------------------------------------|---------------------------------|
| Todoist  | `todoist:pull`             | `todoist:analyze:evening` or `todoist:discuss -- --triage` | `.cache/todoist/reschedules.json`             | `todoist:view` (interactive) or `todoist:reschedule` (batch) |
| Calendar | `calendars:pull`           | conversational ("schedule X")   | `.cache/calendars/proposals.json`             | `calendars:apply`               |
| Email    | `inbox:download`           | `inbox:analyze`                 | `.cache/inbox/insights.json` (read-only meta) | (no destructive apply — view in TUI) |

If validation fails on apply, the proposal file is **not** archived —
fix it and re-run.

---

## Setup notes

- All env vars live in `/Users/jamal/projects/myagents/.env` (shared
  across tools). Each tool has a `.env.example` documenting what it
  needs.
- Python tools (`productivity-tools/`, `finance-tools/`) install into
  per-tool `.venv/`. Run `task <tool>:install` once.
- Go tool (`email-tools/`) builds on demand via `go run ./cmd/...`.
- `.cache/` and `.venv/` are gitignored; safe to delete and rebuild any
  time.
