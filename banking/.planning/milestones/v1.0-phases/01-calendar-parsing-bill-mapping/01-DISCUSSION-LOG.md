# Phase 1: Calendar Parsing + Bill Mapping - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-03
**Phase:** 01-calendar-parsing-bill-mapping
**Areas discussed:** Event parsing rules, Account matching, Variable payment flagging, Output structure

---

## Event Parsing Rules

| Option | Description | Selected |
|--------|-------------|----------|
| Skip and warn | Skip unparseable events, print warning to stderr | |
| Best-effort extract | Try multiple regex patterns, include with amount=None if needed | |
| Strict with examples | Fail loudly if any event doesn't match format | ✓ |

**User's choice:** Strict with examples
**Notes:** None

---

| Option | Description | Selected |
|--------|-------------|----------|
| Dedicated calendar only | Every event on ZOHO_CALENDAR_ID is a bill | ✓ |
| Title prefix convention | Only process matching titles, ignore others | |
| You decide | Claude picks | |

**User's choice:** Dedicated calendar only
**Notes:** None

---

| Option | Description | Selected |
|--------|-------------|----------|
| New module | Create payment_forecast.py as new entry point | |
| Extend existing | Add to zoho_calendar_payments.py | ✓ |
| You decide | Claude picks | |

**User's choice:** Extend existing
**Notes:** None

---

| Option | Description | Selected |
|--------|-------------|----------|
| Halt immediately | Exit on first failure | |
| Collect and report | Process all, report failures, exit non-zero | ✓ |
| You decide | Claude picks | |

**User's choice:** Collect and report
**Notes:** None

---

## Account Matching

| Option | Description | Selected |
|--------|-------------|----------|
| Account name + last4 | Match on last 4 digits | |
| Account ID only | Use payments.yaml ID directly | |
| Free text name | Human-readable name needing resolution | ✓ |

**User's choice:** Free text name
**Notes:** None

---

| Option | Description | Selected |
|--------|-------------|----------|
| Exact substring match | Notes must appear as substring in account name | |
| Lookup table in YAML | Add aliases/notes_match field to payments.yaml | |
| You decide | Claude picks simplest reliable approach | ✓ |

**User's choice:** You decide
**Notes:** None

---

| Option | Description | Selected |
|--------|-------------|----------|
| Required — fail if missing | Every event must have notes with funding account | ✓ |
| Optional — flag as unmatched | Include but flag events without notes | |
| You decide | Claude picks | |

**User's choice:** Required — fail if missing
**Notes:** "Unless the user explicitly states that the account has no funding account"

---

| Option | Description | Selected |
|--------|-------------|----------|
| Special keyword in notes | Use "NONE" or "N/A" in notes to signal no funding | ✓ |
| Prefix in title | Use "[INFO]" prefix in event title | |
| You decide | Claude picks | |

**User's choice:** Special keyword in notes
**Notes:** None

---

## Variable Payment Flagging

| Option | Description | Selected |
|--------|-------------|----------|
| Config list in YAML | Add variable: true field in payments.yaml | |
| Convention in title | Use ~ prefix on amount (e.g., ~$500) | |
| Notes field marker | Add VARIABLE keyword in notes | ✓ (Other) |

**User's choice:** Something in the notes should say that the event is variable. If possible, the script will update the title to reflect the current amount, pulled from Monarch.
**Notes:** Notes field structured as: Fund: <account> | Source: <monarch_account> | VARIABLE

---

| Option | Description | Selected |
|--------|-------------|----------|
| Update calendar title | Write back real amount to Zoho Calendar | |
| Internal only | Use Monarch balance internally, leave calendar unchanged | |
| Both with flag | Internal by default, --update-calendar writes back | ✓ |

**User's choice:** Both with flag
**Notes:** None

---

| Option | Description | Selected |
|--------|-------------|----------|
| Map in payments.yaml | monarch_account field per payment | |
| Match by payment name | Fuzzy match bill name to Monarch account | |
| Notes field reference | Notes include both Fund and Source accounts | ✓ (Other) |

**User's choice:** Calendar event notes include both funding and source account. payments.yaml allows defining account nicknames for resolution.
**Notes:** None

---

## Output Structure

| Option | Description | Selected |
|--------|-------------|----------|
| Table with tabulate | Flat table, one row per payment | |
| Grouped by account | Payments grouped under funding account headings | ✓ |
| You decide | Claude picks | |

**User's choice:** Grouped by account
**Notes:** None

---

| Option | Description | Selected |
|--------|-------------|----------|
| Show balances too | Display current balance alongside payments | |
| Payments only | Defer balance display to Phase 2 | |
| You decide | Claude picks | ✓ |

**User's choice:** You decide
**Notes:** None

---

| Option | Description | Selected |
|--------|-------------|----------|
| In-memory functions | Phase 2 calls Phase 1 function directly | ✓ |
| You decide | Claude picks | |

**User's choice:** In-memory functions
**Notes:** User clarified that payments are assumed paid once calendar date passes — no confirmation tracking needed.

---

## Claude's Discretion

- Account matching implementation strategy (simplest reliable approach)
- Whether to show balances in Phase 1 CLI output

## Deferred Ideas

None
