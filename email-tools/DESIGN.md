# Email Tools — Design Decisions

This document captures architectural decisions, rationale, and pitfalls discovered while building the email-tools pipeline. Future tools in this monorepo that follow a similar async workflow should reference this.

## Architecture: 3-Stage Async Pipeline

### The Pattern

```
download (non-interactive) → analyze (AI, interactive) → view (TUI)
         ↓                          ↓                        ↓
    emails.json              insights.json              BubbleTea
```

Each stage is independently runnable, idempotent, and reads/writes JSON files as the interface between stages. A `status` command reads both files to report progress.

### Why This Pattern

- **Decouples data fetching from AI processing from display** — each stage can be rerun independently without repeating expensive operations
- **AI analysis is the bottleneck** — separating it means you don't re-download 10K emails just because you want to tweak the classification prompt
- **JSON files are debuggable** — you can inspect `emails.json` and `insights.json` directly, edit them, or feed them to other tools
- **Idempotency is critical** — network failures, API rate limits, and interrupted sessions are common. Each stage uses ID-based dedup to skip already-processed items

### Key Decision: Claude Code for AI Analysis (Not the API)

**Decision:** The analyze step launches `claude "prompt"` interactively instead of calling the Anthropic API from Go.

**Why:**
- No separate API key or per-token charges — runs on the existing Claude Code subscription
- Claude Code has access to the filesystem, so it can read the email JSON and write insights JSON directly
- The prompt can reference Go type definitions (`internal/pipeline/types.go`) to ensure correct output format

**Pitfall: `claude -p` (print mode) does not write to stdout when redirected.** We discovered this empirically — `claude -p "prompt" > file.txt` produces an empty file. The `-p` flag generates output tokens but the `result` field in JSON output is empty. This appears to be a fundamental limitation of the CLI when not connected to a TTY. The workaround is to use interactive mode via `claude "prompt"` in the Taskfile, which launches a full session.

**Pitfall: Shell quoting with `$(cat file)`.** Email content contains quotes, special characters, and multi-line text. Using `claude -p "$(cat prompt.txt)"` breaks on these. Piping via `cat file | claude -p -` also produced empty output. The interactive `claude "prompt"` approach avoids this entirely since Claude Code reads the files itself.

## Data Storage

### Location: `.cache/<command>/`

- Follows the existing `.tokens/` convention in the project
- `.cache/` is gitignored
- Each command gets its own subdirectory (e.g., `.cache/inbox/`)

### File Format: JSON with Version Field

```json
{
  "version": 1,
  "created_at": "...",
  "updated_at": "...",
  ...
}
```

- Human-readable and debuggable
- Version field enables future schema migrations
- `updated_at` tracks freshness

### Atomic Writes

All JSON saves go through `atomicWriteJSON()`: write to `.tmp` file, then `os.Rename()`. This prevents corruption if the process is interrupted mid-write.

## Email Fetching

### Gmail API Pagination

**Decision:** The Gmail provider paginates automatically via `NextPageToken` up to a configurable limit.

- The message list endpoint returns only IDs (lightweight), then individual messages are fetched for full details
- Page size is 500 (Gmail API max per page)
- Rate limiting: 100ms between list pages, 200ms pause every 50 detail fetches

**Pitfall: Gmail's `maxResults` is per-page, not total.** Without pagination, you only get up to 500 emails regardless of how many match the query. The provider now follows `NextPageToken` to get all results.

**Pitfall: `FetchEmails` with `Format("full")` is slow at scale.** Each email requires an individual API call to get headers + body. For 10K emails, this takes minutes. Progress output (`\r` overwrite on stderr) is essential for user feedback.

### Email Body

**Decision:** Fetch full plain-text body, truncated to 2000 characters in storage and 1000 characters in AI prompts.

- Bodies give the AI much better classification context (e.g., distinguishing a real payment decline from a phishing email)
- The truncation prevents context overload — most actionable content is in the first 1000 chars
- `extractPlainText()` walks the Gmail MIME tree recursively to find `text/plain` parts, base64-decodes them

### Combined Queries for Cleanup

**Decision:** For Gmail cleanup, combine `older_than` and `category` queries into one using Gmail's OR syntax: `{older_than:180d category:promotions older_than:180d}`.

**Pitfall: Separate queries cause massive duplication.** Running `older_than:180d`, then `category:promotions`, then `category:social` etc. each returns up to the fetch limit. Most results overlap. The combined query lets Gmail handle dedup server-side and produces one pagination cycle instead of four.

### Batch Delete Limit

**Pitfall: Gmail's `BatchModifyMessagesRequest` rejects more than 1000 IDs.** The delete function must chunk into batches of 1000.

## AI Classification

### Prompt Design

Key principles for the classification prompt:

1. **Be explicit about urgency thresholds** — without guidance, the AI marks too many emails as "action_needed". The prompt explicitly states that marketing, newsletters, and sales are always "fyi".
2. **Request structured output** — JSON array, no markdown fences, specific field names matching the Go struct
3. **Include email body** — truncated to 1000 chars in the prompt for better classification without overwhelming context
4. **Request action items** — specific, actionable steps like "Pay $450.10 to Progressive by May 30" rather than vague "review this email"

### Idempotency in Analysis

The analyze step (both the `prepare-chunk` Go helper and the Claude Code interactive approach) builds a set of already-analyzed email IDs from `insights.json` and skips them. This means:

- Interrupting mid-analysis and rerunning picks up where it left off
- Adding new emails (re-running download) only requires analyzing the new ones

## TUI Design

### BubbleTea Patterns

- **Two-level tabs:** Account tabs (row 1) + Category tabs (row 2)
- **Multiple view modes:** Email list, action items view, grouped/flat toggle
- **Filter state:** `hideFYI` defaults to true — most emails are noise, show only actionable items by default
- **Detail pane:** Selected email shows AI summary, why-important, and action items below the list

### Keybinding Conventions

| Key | Action |
|-----|--------|
| j/k | Navigate up/down |
| h/l | Switch account |
| tab/shift+tab | Switch category |
| g | Toggle grouped/flat in All tab |
| f | Toggle FYI visibility |
| t | Action items view |
| space | Toggle selection (action items) |
| a | Select/deselect all (action items) |
| u | Upload to Todoist |
| q | Quit (with confirmation in cleanup) |

### Quit Confirmation

**Decision:** The cleanup TUI prompts "Quit? (y/n)" on `q` press, since accidental quit during a long cleanup session loses selection state. The inbox view does not confirm since it's read-only.

## External Integrations

### Todoist

**Pitfall: Todoist REST API v2 is deprecated (returns 410 Gone).** The current API is the Sync API v1 at `https://api.todoist.com/api/v1/sync`.

- Uses `item_add` command type with `temp_id` and `uuid` (both UUIDs)
- Auth: `Bearer` token in header
- Content-Type: `application/x-www-form-urlencoded` (not JSON)
- `commands` parameter is a JSON array encoded as a form value

**Decision:** Each action item becomes a Todoist task with:
- `content`: the action item text
- `description`: email sender + subject for context

### Environment Variables

All secrets live in `.env` (loaded via godotenv):

| Variable | Purpose |
|----------|---------|
| GMAIL_CLIENT_ID | Gmail OAuth2 |
| GMAIL_CLIENT_SECRET | Gmail OAuth2 |
| ZOHO_CLIENT_ID | Zoho OAuth2 |
| ZOHO_CLIENT_SECRET | Zoho OAuth2 |
| ZOHO_REFRESH_TOKEN | Zoho token refresh |
| ZOHO_ACCOUNT_ID | Zoho mail account |
| TODOIST_API_TOKEN | Todoist task creation |

**Pitfall: `.env` must be loaded before reading env vars.** The `view` command originally didn't call `config.Load()`, so `TODOIST_API_TOKEN` was always empty. Every subcommand that reads env vars must call `config.Load()` first (which triggers godotenv).

## Naming Conventions

**Decision:** Tool directories use `<noun>-tools` naming: `finance-tools`, `email-tools`. Commands within are verbs/nouns: `inbox`, `cleanup`, `auth`.

**Decision:** Taskfile namespacing follows `<tool>:<command>:<subcommand>`:
- `email:inbox:download`
- `email:inbox:analyze`
- `email:inbox:view`
- `email:cleanup`

## Lessons for Future Tools

1. **Start with the data format** — define your JSON manifest types first. Everything else flows from the schema.
2. **Make every stage idempotent** — use ID-based dedup sets. Users will re-run stages constantly.
3. **Atomic writes always** — write to `.tmp`, rename. Never write directly to the output file.
4. **Progress output for anything > 2 seconds** — use `\r` on stderr to overwrite in-place. Users panic at silent pauses.
5. **`claude -p` doesn't work for scripted output capture** — use interactive `claude "prompt"` in Taskfiles instead.
6. **Gmail API limits are everywhere** — 500 per page for list, 1000 for batch modify, individual calls for message details. Paginate and chunk everything.
7. **AI prompts need explicit negative examples** — "marketing is always fyi" prevents over-classification.
8. **Default to hiding noise** — show only actionable items by default, let users toggle to see everything.
