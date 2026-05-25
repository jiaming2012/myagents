# Project Research Summary

**Project:** Banking Payment Forecaster — Forecasting + Email Alerts Milestone
**Domain:** Personal finance payment forecasting (CLI tool, calendar + balance APIs)
**Researched:** 2026-05-03
**Confidence:** HIGH

## Executive Summary

This project extends an existing Python CLI banking tool to add calendar-based payment forecasting and proactive email alerts. The codebase already has three working scripts (Monarch Money balance fetching, Zoho Calendar parsing, and a coverage report) with a solid patterns foundation — async API clients, TTL file caching, YAML config. The research verdict is clear: this is a well-scoped evolution of existing code, not a greenfield build. The path forward is a new `payment_forecast.py` orchestrator backed by extracted shared modules in a `lib/` directory, leaving the three existing scripts untouched.

The recommended approach adds exactly two new external dependencies (`python-dateutil` for correct month-boundary date math, `tabulate` for aligned CLI table output) and uses Python stdlib `smtplib` for Gmail alerts via App Password. This deliberately avoids heavier alternatives (Pandas, Gmail API, APScheduler) that would bloat a tool whose core value is a daily CLI command and an email when something looks wrong. The scheduling mechanism is OS cron, not a Python daemon.

The dominant risk is data correctness, not technical complexity. Three failure modes deserve particular attention: (1) stale Monarch/Mercury balance snapshots showing false surpluses, (2) dual date sources (Zoho Calendar vs `payments.yaml` `day_of_month`) producing wrong due dates, and (3) 12 of 20 payments in `payments.yaml` having `funding_account: null`, which makes the bill-to-account mapping config the true prerequisite for everything else. Get the data right first, then build the forecast on top.

## Key Findings

### Recommended Stack

The existing stack requires no major changes. Only `python-dateutil>=2.9.0` and `tabulate>=0.9.0` are new external additions. The existing `try/except ValueError` month-rollover logic in `coverage_report.py` is a known correctness bug; `dateutil.rrule(MONTHLY, bymonthday=X)` replaces it cleanly. Email delivery via `smtplib` + Gmail App Password requires three new `.env` variables (`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `ALERT_RECIPIENT`) and zero GCP setup.

**Core technologies:**
- `python-dateutil 2.9.0`: MONTHLY rrule for due-date calculation — replaces broken manual month-boundary logic
- `tabulate 0.9.0`: Aligned forecast table output — handles variable-width account names; keep existing coverage_report format unchanged
- `smtplib` (stdlib): Gmail SMTP via App Password — zero dependencies, Google-supported for automated scripts with 2FA
- `asyncio.gather()`: Concurrent Monarch + Mercury balance fetching — already used for Monarch, extend to Mercury via `asyncio.to_thread()`

### Expected Features

**Must have (table stakes):**
- Bill-to-account mapping config (`bill_mapping.yaml`) — 12 of 20 payments are currently `funding_account: null`; nothing works without this
- Calendar event-to-payment matching — Zoho Calendar provides authoritative due dates; YAML provides amounts and account mappings
- Unified forecast CLI (`payment_forecast.py`) — single command: upcoming payments, per-account projected balances, shortfall warnings
- Per-account projected balance waterfall — balance minus upcoming payments in date order
- Shortfall detection and warnings — flag when projected balance goes negative
- Summary totals — total outgoing, available, net position across all accounts
- Chronological payment timeline — date-ordered, not grouped by account
- Graceful handling of missing data — warn and continue, never crash on unknown balance or unmatched event

**Should have (differentiators):**
- Email alerts for projected shortfalls — daily Gmail notification; negative framing ("short $X on Account Y by May 15") is proven more effective
- Daily scheduled forecast — cron wrapping the CLI; depends on email alerts
- Funding gap recommendations — "Transfer $X from Y to Z" using `transfer_rules` from `payments.yaml`; read-only, never executes
- Credit card dynamic balance — for autopay-full cards, use current statement balance from Monarch rather than static YAML amount

**Defer (v2+):**
- What-if scenarios — high complexity; build after the real forecast is validated and trusted
- Historical tracking — needs stable output format; add after CLI output settles
- Multi-window comparison — half-built already; add once core forecast works
- Personal vs business separation — data is tagged; add as a `--category` filter once core is stable

### Architecture Approach

Build `payment_forecast.py` as a new orchestrator (~50 lines of glue) importing from a new `lib/` directory of shared modules. The three existing scripts remain untouched throughout. Phase the extraction: start with low-risk config and cache modules, then extract API clients, then build the novel matching and projection logic. The `bill_mapping.yaml` overlay config extends `payments.yaml` without modifying it (payments.yaml has inline comments that YAML round-tripping destroys).

**Major components:**
1. `lib/config.py` — YAML loading and bill_mapping overlay merge; `bill_mapping.yaml` wins for `funding_account` when both sources define it
2. `lib/matcher.py` — Zoho event-to-payment matching; `zoho_match` prefix field is primary, fuzzy title match is fallback only
3. `lib/projector.py` — Forward balance projection per funding account; produces `AccountProjection` data structures with shortfall flags
4. `lib/balances.py` — Unified Monarch + Mercury balance resolution via `asyncio.gather()`
5. `lib/zoho.py` — Zoho Calendar auth + TTL-cached event fetching (extracted from existing script)
6. `lib/alerts.py` — Gmail email via smtplib; idempotent (tracks last alert in `.cache/last_alert.json`)
7. `lib/display.py` — Terminal report formatting using `tabulate`

### Critical Pitfalls

1. **Stale balance snapshot producing false all-clear** — Display balance fetch timestamp on every report ("Balance as of HH:MM via Monarch"). Add staleness warning if Monarch sync is older than 6 hours. Never say "Surplus: $X" without the qualifier. Distinguish `currentBalance` vs `availableBalance` from Mercury where both are available. Address in Phase 1.

2. **Dual date source confusion** — Zoho Calendar is the ONLY source for when a payment is due. `payments.yaml` stores amounts, accounts, and autopay status — not authoritative due dates. If a payment has no matching Zoho event, flag it as "unscheduled" rather than silently falling back to `day_of_month`. Demote `day_of_month` to a labeled fallback, not the default. Address in Phase 1.

3. **Month-boundary date bug in existing code** — `coverage_report.py` lines 51-92 silently drop payments due on day 31 in short months. Replace with `dateutil.rrule(MONTHLY, bymonthday=X)` or `min(day_of_month, calendar.monthrange(year, month)[1])`. Add unit tests for Feb 28/29, Apr 30, Nov 30. Fix in Phase 1.

4. **Variable-amount payments treated as fixed** — Credit card minimums and utility bills change monthly. Flag `variable` amount type visually in the report ("$150 est."). Phase 1 uses static amounts with a warning marker; Phase 2 adds dynamic lookup from Monarch statement balance.

5. **Silent failure when APIs are down** — Distinguish partial failure from total failure. If all balance APIs fail, exit with error rather than producing a useless zero-balance forecast. Cache last successful balance fetch and surface "USING CACHED DATA FROM [timestamp]" prominently. Address in Phase 1.

## Implications for Roadmap

Based on research, all four research files independently converge on the same four-phase structure. The dependency chain is strict: data must exist before it can be fetched, fetched before it can be matched, matched before it can be projected, projected before it can be alerted.

### Phase 1: Foundation and Forecast Core

**Rationale:** The bill-to-account mapping is the blocking prerequisite for everything. Without it, the forecast cannot attribute payments to accounts. Simultaneously establish the shared module structure and date-authority conventions to avoid inheriting bugs from existing code.
**Delivers:** Working `task forecast` command producing a dated timeline of upcoming payments with per-account projected balances and shortfall warnings. Terminal output only. No email yet.
**Addresses:** Bill-to-account mapping config, calendar event-to-payment matching, unified forecast CLI, per-account projected balance, shortfall detection, summary totals, chronological timeline, graceful missing-data handling.
**Avoids:** Pitfalls 1 (stale balance), 2 (dual date source), 3 (month-boundary bug), 6 (fragile title matching), 8 (credit card balance semantics), 9 (silent API failure), 11 (timezone mismatch), 12 (cache poisoning).
**Build order within phase:** `lib/cache.py` → `lib/config.py` → `bill_mapping.yaml` (data entry) → `lib/zoho.py` → `lib/balances.py` → `lib/matcher.py` → `lib/projector.py` → `lib/display.py` → `payment_forecast.py`.

### Phase 2: Data Quality and Projection Accuracy

**Rationale:** Once the core forecast works, improve accuracy before adding automation. Variable-amount payments, weekend date adjustments, and intra-day ordering are all correctness gaps that would silently corrupt the Phase 3 email alerts if unaddressed.
**Delivers:** More accurate projections — credit card dynamic balance lookup, business-day-adjusted autopay dates, waterfall view showing which specific payment will fail when multiple hit the same day.
**Addresses:** Credit card dynamic balance (differentiator), per-account waterfall view, business day adjustment, multi-payment day ordering.
**Avoids:** Pitfalls 4 (variable amounts), 5 (same-day payment ordering), 7 (weekend date blindness).

### Phase 3: Email Alerts and Daily Automation

**Rationale:** Email alerts depend on a working, accurate forecast. Build automation last so it runs on a validated engine. Idempotency must be built in from the start — duplicate daily runs are guaranteed.
**Delivers:** Daily cron-triggered forecast with Gmail shortfall alerts. Alert state tracking prevents duplicate sends. `--email` flag for manual sends. `--dry-run` flag for testing.
**Addresses:** Email alerts for shortfalls, daily scheduled forecast, funding gap recommendations.
**Avoids:** Pitfalls 10 (email idempotency), duplicate cron runs, silent send failures.
**Uses:** `smtplib` + Gmail App Password; `lib/alerts.py` with `.cache/last_alert.json` state tracking.

### Phase 4: Extended Features (Post-Validation)

**Rationale:** Defer complex features until the core loop is proven useful. What-if scenarios require a stable output format. Historical tracking requires the same. Add only after Phase 3 is running reliably.
**Delivers:** What-if scenario support, historical run tracking, personal vs business separation filter.
**Addresses:** What-if scenarios (high complexity), historical tracking (needs stable format), category filtering.

### Phase Ordering Rationale

- Phase 1 before Phase 2: The matching and projection logic must exist before there is anything worth making more accurate.
- Phase 2 before Phase 3: Email alerts that fire on incorrect projections are worse than no alerts. A false all-clear from a variable-amount bug would erode trust immediately.
- Phase 3 before Phase 4: Automation only makes sense once the underlying tool is trusted. What-if scenarios require a stable output format as the baseline.
- `bill_mapping.yaml` data entry (filling in the 12 null funding_account entries) is the single highest-value first step — it unblocks all subsequent work and is mostly manual YAML editing, not code.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 1, matcher.py:** Zoho Calendar event UID field availability needs verification — research suggested using UIDs rather than titles as the join key, but the actual Zoho API field name needs confirmation against the live API.
- **Phase 2, credit card dynamic balance:** Monarch Money API does not have well-documented "minimum payment due" fields — verify what account metadata Monarch actually returns for credit card accounts before designing this feature.

Phases with standard patterns (skip research-phase):
- **Phase 1, lib/ module extraction:** Straightforward refactoring of existing working code; patterns are clear from codebase analysis.
- **Phase 3, Gmail via smtplib:** Well-documented stdlib pattern with App Password; no research needed.
- **Phase 3, cron scheduling:** Standard OS cron; no research needed.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Grounded in existing codebase; only 2 new deps, both well-established and version-pinned |
| Features | HIGH | Informed by competitive analysis (PocketSmith, Quicken, Monarch), feature dependency graph is explicit |
| Architecture | HIGH | Primary source is the actual codebase; component boundaries and data structures are derived from real code analysis |
| Pitfalls | HIGH | Mix of code-audit findings (existing bugs identified by line number) and domain research from financial forecasting literature |

**Overall confidence:** HIGH

### Gaps to Address

- **Zoho Calendar event UID availability:** The architecture recommends using Zoho event UIDs as the stable join key for matching. The existing `zoho_calendar_payments.py` uses event titles. Verify during Phase 1 matcher implementation whether the Zoho Calendar API returns a stable UID field or whether the `zoho_match` prefix approach must serve as the primary key long-term.

- **Monarch credit card metadata:** Phase 2's credit card dynamic balance feature assumes Monarch returns something usable for "minimum payment due" or "statement balance." This is not verified. If Monarch does not expose this, the feature degrades to "use static YAML amount with a variable flag" — which is the Phase 1 behavior anyway. Not a blocker, but a planning assumption.

- **`bill_mapping.yaml` content:** 12 of 20 payments have `funding_account: null`. Filling these in requires a manual review session with the user. This is not a technical gap but a data gap that blocks Phase 1 completion. Should be the very first task in Phase 1 execution.

## Sources

### Primary (HIGH confidence)
- Codebase analysis (`coverage_report.py`, `zoho_calendar_payments.py`, `monarch_balances.py`, `payments.yaml`) — architecture, existing bugs, data structure
- [python-dateutil 2.9.0 PyPI](https://pypi.org/project/python-dateutil/) — version confirmation
- [tabulate 0.9.0 PyPI](https://pypi.org/project/tabulate/) — version confirmation
- [Gmail App Passwords](https://myaccount.google.com/apppasswords) — smtplib auth approach

### Secondary (MEDIUM confidence)
- [PocketSmith cash flow forecasts](https://www.pocketsmith.com/tour/cash-flow-forecasts/) — feature landscape, what-if scenario complexity
- [Mailtrap Python Gmail 2026](https://mailtrap.io/blog/python-send-email-gmail/) — smtplib + App Password pattern validation
- [Anaplan: Common Financial Forecasting Mistakes](https://www.anaplan.com/blog/five-common-financial-forecasting-mistakes-and-how-to-avoid/) — stale data, irregular expense pitfalls
- [Phoenix Strategy Group: Cash Flow Forecasting Pitfalls](https://www.phoenixstrategy.group/blog/avoid-cash-flow-forecasting-pitfalls) — optimistic assumptions, update frequency

### Tertiary (LOW confidence)
- [AI Overdraft Prediction Research (Meniga)](https://www.meniga.com/resources/ai-in-overdraft-protection/) — negative framing in alerts reduces overdrafts by 9%; interesting but single source, use directionally
- [Sourcery: Race Conditions in Financial Transactions](https://www.sourcery.ai/vulnerabilities/race-condition-financial-transactions) — stale balance check-then-act pattern; applies conceptually

---
*Research completed: 2026-05-03*
*Ready for roadmap: yes*
