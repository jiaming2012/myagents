# Domain Pitfalls

**Domain:** Personal finance payment forecasting (CLI, calendar + balance APIs)
**Project:** Banking Payment Forecaster
**Researched:** 2026-05-03

---

## Critical Pitfalls

Mistakes that cause incorrect forecasts, missed shortfall alerts, or silent data corruption.

### Pitfall 1: Stale Balance Snapshot Treated as Current Truth

**What goes wrong:** The forecast fetches Monarch/Mercury balances once at report time, but those balances reflect the last bank sync -- often 4-24 hours old. Pending transactions (holds, ACH in transit, autopay already debited) are invisible. The forecast says "you have $2,400" when the real available balance is $1,800 after a pending debit card hold.

**Why it happens:** Aggregator APIs (Monarch Money, Plaid-backed services) report the "current balance" or "posted balance," not "available balance." Mercury's API reports `currentBalance` which may or may not include pending. Developers treat the number as ground truth without understanding the lag.

**Consequences:** False "all clear" on shortfall checks. User trusts the forecast, doesn't transfer funds, and a payment bounces or an account overdraws.

**Prevention:**
- Display the balance fetch timestamp prominently in every report (e.g., "Balance as of: May 3 06:12 AM via Monarch").
- Add a `--stale-threshold` flag (default 6 hours). If the Monarch sync timestamp is older, print a warning: "Balance data may be stale (last sync 14h ago)."
- For Mercury accounts, compare `currentBalance` vs `availableBalance` if the API provides both. Surface the difference.
- Never say "Surplus: $X" without a qualifier. Say "Projected surplus (based on balances as of HH:MM)."

**Detection:** User reports a shortfall that the forecast missed. Or: Monarch balance doesn't change across multiple report runs separated by hours.

**Phase:** Address in Phase 1 (unified forecast CLI). Every balance display must include source and timestamp from day one.

---

### Pitfall 2: Dual Source of Truth for Payment Dates

**What goes wrong:** `payments.yaml` has `day_of_month` fields. Zoho Calendar has event dates. The project intends Zoho Calendar to be authoritative (per PROJECT.md), but `coverage_report.py` currently reads from `payments.yaml` only. When someone updates a due date in Zoho but not in `payments.yaml` (or vice versa), the forecast uses the wrong date.

**Why it happens:** The codebase was built incrementally -- `payments.yaml` was seeded from Zoho Calendar once (April 2026) and then used standalone. The new forecast tool plans to use Zoho Calendar as the date authority, but the bill-to-account mapping still needs `payments.yaml`. Two systems, two date sources, no enforcement of which one wins.

**Consequences:** Forecast shows payment due on the 15th; actual due date in Zoho is the 10th. Five-day gap means shortfall detection fires too late (or not at all if the window is 7 days and the payment falls outside it).

**Prevention:**
- Zoho Calendar is the ONLY source for "when is this due." `payments.yaml` stores account mappings, amounts, autopay status -- but NOT authoritative due dates.
- When building the forecast, join Zoho events to `payments.yaml` entries by a shared key (event title or a custom `zoho_event_id` field). If a payment exists in `payments.yaml` but has no matching Zoho event, flag it as "unscheduled" rather than silently using `day_of_month`.
- Remove `day_of_month` from `payments.yaml` once Zoho integration is live, or demote it to "fallback_day" with an explicit log message when the fallback is used.

**Detection:** Run a reconciliation check: for every payment in `payments.yaml`, verify a matching Zoho event exists within the next 60 days. Log mismatches.

**Phase:** Address in Phase 1 (unified forecast CLI). This is a foundational design decision -- getting it wrong means every downstream feature inherits the confusion.

---

### Pitfall 3: Month-Boundary Date Math Bugs

**What goes wrong:** The existing `get_upcoming_payments()` function (coverage_report.py:51-92) constructs due dates from `day_of_month` integers. When `day_of_month=31` and the month has 30 days, it catches `ValueError` and jumps to next month -- but then the next month might also not have 31 days (February). The code has a `continue` on line 84 that silently drops payments when the next month also fails.

**Why it happens:** Calendar math is deceptively hard. The naive approach of `datetime(year, month, day)` throws `ValueError` for invalid dates (Feb 29 in non-leap years, Feb 30/31 ever, Apr/Jun/Sep/Nov 31). The exception handler doesn't cascade properly.

**Consequences:** A payment due on the 31st of every month gets silently skipped in February (28 or 29 days), April (30 days), June, September, and November. The forecast omits the payment entirely instead of clamping to the last day of the month. User misses the shortfall.

**Prevention:**
- Use `calendar.monthrange(year, month)` to clamp `day_of_month` to the actual last day: `min(day_of_month, monthrange(year, month)[1])`.
- Or use `dateutil.relativedelta` which handles this natively.
- Add explicit unit tests for: day 29 in Feb (leap and non-leap), day 30 in Feb, day 31 in Apr/Jun/Sep/Nov, day 31 in Dec (year rollover).
- If migrating to Zoho Calendar as date authority, this specific bug becomes less relevant for monthly payments -- but remains relevant for any "every Nth day" logic in transfer rules or reminders.

**Detection:** Run the forecast for February with a payment on day 31. If it doesn't appear, the bug is live.

**Phase:** Fix in Phase 1. This is a correctness bug in existing code that carries forward into the new forecast tool if `day_of_month` logic is reused.

---

### Pitfall 4: Variable-Amount Payments Treated as Fixed

**What goes wrong:** `payments.yaml` stores fixed `amount` values, but many real bills are variable: credit card minimum payments, utility bills, insurance premiums that adjust annually. The forecast projects $150/month for a credit card payment when the actual minimum due is $380 this month because of a large purchase.

**Why it happens:** At seed time, amounts were snapshot from one month's data. Variable bills don't have a stable amount. Credit card payments in particular depend on the current statement balance, which changes every billing cycle.

**Consequences:** Shortfall calculation is wrong in both directions. If the actual amount is higher than the stored amount, a shortfall goes undetected. If lower, surplus is understated (less dangerous but still inaccurate). Over a 30-day window with multiple variable payments, errors compound.

**Prevention:**
- Add an `amount_type` field to payments: `fixed`, `variable`, `minimum_plus`. For `variable` payments, the amount in YAML is a "typical" estimate, and the forecast should flag it with a warning marker.
- For credit cards specifically: pull the "last statement balance" or "minimum payment due" from Monarch Money's account data (if available) rather than using the static YAML amount.
- Display variable payments differently in the report: "$150 (est.)" vs "$150" for fixed.
- Add a `--conservative` flag that uses the higher of: YAML amount, or last 3 months' average (if historical data is available).

**Detection:** Compare forecast amounts against actual bank debits after the fact. If they diverge by more than 20% consistently, the payment is variable and the YAML amount is stale.

**Phase:** Phase 2 (after basic forecast works). Phase 1 can use static amounts with a "variable" flag as a visual warning. Phase 2 adds dynamic amount lookup.

---

### Pitfall 5: Ignoring Payment Ordering Within a Day

**What goes wrong:** Multiple payments due on the same day from the same funding account. The forecast checks "total due vs. balance" but doesn't account for intra-day ordering. If the funding account has $1,000, Payment A is $600 (autopay fires at 6 AM), and Payment B is $500 (autopay fires at noon), the forecast says "total $1,100 vs $1,000 = shortfall of $100." But the real problem is worse: Payment A succeeds, leaving $400, then Payment B fails with insufficient funds -- the user needs to know WHICH payment will fail.

**Why it happens:** Personal finance tools typically aggregate by day or by window, not by individual payment sequence. Banks process payments in an order the user can't fully control (ACH batch timing, credit card autopay schedules).

**Consequences:** Generic "shortfall of $100" alert is less actionable than "Payment B to Capital One will likely fail because Payment A to NFCU processes first." User might prioritize the wrong payment to defer.

**Prevention:**
- When a shortfall exists, list the payments in likely processing order (autopay before manual, smaller before larger is a common bank ordering).
- Show a "waterfall" view: starting balance, minus payment 1, equals interim balance, minus payment 2, equals interim balance... first negative number = the failure point.
- Don't over-engineer this in Phase 1. A simple warning "Multiple payments due same day -- review order" is sufficient initially.

**Detection:** User reports a specific payment failed despite the forecast showing "sufficient total balance."

**Phase:** Phase 2 or 3. Phase 1 flags multi-payment days. Phase 2 adds waterfall view.

---

## Moderate Pitfalls

### Pitfall 6: Zoho Calendar Event Title Matching is Fragile

**What goes wrong:** The forecast needs to match Zoho Calendar event titles to `payments.yaml` entries. Titles like "Capital One Payment" in Zoho must match a `name` field in YAML. If someone edits the event title in Zoho (adds "- June" suffix, fixes a typo, changes capitalization), the match breaks silently.

**Prevention:**
- Use a stable identifier, not the title. Add a `zoho_uid` field to `payments.yaml` that maps to the Zoho Calendar event UID. UIDs are immutable even when titles change.
- Fallback: fuzzy match on title with a confidence threshold, logging when confidence is below 90%.
- Run a "reconciliation" check that lists unmatched Zoho events and unmatched YAML entries.

**Phase:** Phase 1. This is part of the core Zoho-to-YAML join logic.

---

### Pitfall 7: Weekend and Holiday Date Blindness

**What goes wrong:** A bill is due on Saturday the 15th. The bank won't process it until Monday the 17th (or processes it Friday the 13th for autopay). The forecast treats the 15th literally, so the shortfall window is off by 1-2 days.

**Prevention:**
- For autopay payments, assume the bank processes on the business day BEFORE the due date (conservative approach).
- Add an optional `business_days_only: true` flag per payment that adjusts due dates to the nearest prior business day.
- Don't try to model federal holidays in Phase 1 -- weekday-only adjustment (Mon-Fri) catches 90% of cases.

**Phase:** Phase 2. Phase 1 uses literal dates. Phase 2 adds business day adjustment for autopay payments.

---

### Pitfall 8: Credit Card Balance Semantics Confusion

**What goes wrong:** Credit card "balance" from Monarch Money is the OWED amount (e.g., $2,300 owed). The PAYMENT amount is what comes out of the debit/funding account. These are different numbers: the user might pay the minimum ($35), the statement balance ($1,800), or the full balance ($2,300). The forecast must track the payment from the FUNDING account perspective, not the credit card balance.

**Why it happens:** The existing code (coverage_report.py:220-222) already distinguishes credit cards ("$X owed") but the shortfall math on line 217 (`surplus = balance - total_due`) uses the funding account balance minus payment amounts. If someone accidentally puts the credit card balance as the "amount" in YAML, the forecast thinks $2,300 is leaving the checking account when only $35 (minimum) actually will.

**Prevention:**
- In `payments.yaml`, the `amount` for a credit card payment should be the typical PAYMENT amount (minimum, statement, or autopay setting), NOT the card's balance.
- Add a `payment_strategy` field for credit cards: `minimum`, `statement_balance`, `full_balance`, `fixed`. This documents intent and can eventually drive dynamic amount lookup.
- Validate that no payment `amount` exceeds the funding account's typical balance (sanity check at load time).

**Phase:** Phase 1 validation. Ensure YAML data is correct before building forecast logic on top of it.

---

### Pitfall 9: Silent Failure When APIs Are Down

**What goes wrong:** Monarch Money API is unreachable (maintenance, rate limit, token expired). Mercury API returns 403. The current code prints a warning to stderr and continues with empty balances. The forecast runs, shows "Balance: unknown" for every account, and produces a report that LOOKS complete but has zero useful shortfall detection.

**Prevention:**
- Distinguish between "partial failure" (one API down, others work) and "total failure" (no balance data at all). If total failure, exit with error code rather than printing a useless report.
- Add a `--require-balances` flag (default: true) that refuses to produce a forecast without at least one balance source returning data.
- Cache the last successful balance fetch. If live fetch fails, use cached data with a prominent "USING CACHED DATA FROM [timestamp]" warning.

**Phase:** Phase 1. A forecast without balance data is worse than no forecast (false confidence).

---

### Pitfall 10: Email Alerts Without Idempotency

**What goes wrong:** The daily summary runs on a cron schedule. If it runs twice (cron misconfiguration, manual re-run), the user gets duplicate shortfall alerts. Or worse: if the alert email fails to send, no retry mechanism exists, and the user misses a critical shortfall.

**Prevention:**
- Track alert state in a simple file (`.cache/last_alert.json`) with the date and shortfall details. Don't re-send if the same shortfall was already alerted today.
- Add a `--dry-run` flag that shows what would be emailed without sending.
- Log every alert attempt (sent, failed, skipped-duplicate) to a file for audit.

**Phase:** Phase 3 (email alerts). Build idempotency from the start of the alert feature.

---

## Minor Pitfalls

### Pitfall 11: Timezone Mismatch Between Calendar and Local System

**What goes wrong:** Zoho Calendar events have timezone metadata. The local system running the CLI might be in a different timezone. A payment "due on May 15" in Eastern time shows up as "May 14 at 11 PM" in Pacific time if naive datetime comparison is used.

**Prevention:** Normalize all dates to the user's configured timezone (or UTC internally, display in local). Use timezone-aware datetime objects throughout.

**Phase:** Phase 1. Use `zoneinfo` (Python 3.9+) from the start.

---

### Pitfall 12: Cache Poisoning from Bad API Responses

**What goes wrong:** Zoho API returns an error response (rate limit HTML page, partial JSON). The cache stores it as valid data. Subsequent requests within the TTL get the poisoned cache entry, not fresh data.

**Prevention:** Validate API responses before caching. Check for expected fields (e.g., Zoho events should have `title` and `dateandtime`). Only cache responses that pass validation.

**Phase:** Phase 1. The caching layer already exists -- add validation before `cache_set()`.

---

### Pitfall 13: Payments.yaml Schema Drift

**What goes wrong:** New fields are added to `payments.yaml` (e.g., `zoho_uid`, `amount_type`, `payment_strategy`) but existing entries don't get updated. The codebase handles missing fields with `.get()` defaults, so old entries silently use fallback behavior. Over time, half the entries have rich metadata and half are bare-bones, creating inconsistent forecast quality.

**Prevention:**
- Add schema validation (Pydantic model or JSON Schema) that runs at load time.
- Define required vs optional fields explicitly. New required fields get a migration step: a script that adds the field with a `TODO` value to all existing entries.
- Version the schema (add a `schema_version: 2` top-level field) so the loader can detect outdated files.

**Phase:** Phase 1 (schema validation). Each subsequent phase that adds fields includes a migration step.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Unified forecast CLI (Phase 1) | Dual date source confusion (Zoho vs YAML) | Establish Zoho as sole date authority; YAML stores mappings only |
| Unified forecast CLI (Phase 1) | Stale balance data producing false all-clear | Display balance timestamps; add staleness warnings |
| Unified forecast CLI (Phase 1) | Month-boundary date bugs carried from existing code | Clamp day-of-month to actual month length; add edge case tests |
| Bill-to-account mapping (Phase 1) | Fragile Zoho event title matching | Use Zoho event UIDs, not titles, as the join key |
| Shortfall detection (Phase 1) | Variable amounts producing wrong shortfall math | Flag variable-amount payments visually; don't mix fixed and estimated |
| Shortfall detection (Phase 1) | No balance data = useless report | Require at least one balance source; fail loudly if all APIs are down |
| Per-account projections (Phase 2) | Payment ordering within a day not modeled | Add waterfall view showing sequential balance impact |
| Per-account projections (Phase 2) | Weekend/holiday date shifts | Adjust autopay dates to prior business day |
| Email alerts (Phase 3) | Duplicate alerts on re-runs | Track alert state; deduplicate by date + shortfall signature |
| Email alerts (Phase 3) | Silent send failures with no retry | Log all send attempts; add retry with backoff |
| Daily scheduled runs (Phase 3) | Cron runs during API maintenance window | Add health check before report; skip gracefully with alert |

---

## Sources

- Codebase analysis: `coverage_report.py`, `zoho_calendar_payments.py`, `payments.yaml`
- Known issues: `.planning/codebase/CONCERNS.md` (2026-05-03 audit)
- [Monarch Money recurring bill tracking](https://help.monarch.com/hc/en-us/articles/4890751141908-Tracking-Recurring-Expenses-and-Bills) -- weekend date adjustment patterns
- [Anaplan: Common Financial Forecasting Mistakes](https://www.anaplan.com/blog/five-common-financial-forecasting-mistakes-and-how-to-avoid/) -- stale data and irregular expense pitfalls
- [Phoenix Strategy Group: Cash Flow Forecasting Pitfalls](https://www.phoenixstrategy.group/blog/avoid-cash-flow-forecasting-pitfalls) -- optimistic assumptions and update frequency
- [Sourcery: Race Conditions in Financial Transaction Processing](https://www.sourcery.ai/vulnerabilities/race-condition-financial-transactions) -- check-then-act patterns with stale balance data

---

*Pitfalls audit: 2026-05-03*
