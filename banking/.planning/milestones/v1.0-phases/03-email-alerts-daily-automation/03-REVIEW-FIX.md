---
phase: 03-email-alerts-daily-automation
fixed_at: 2026-05-05T00:00:00Z
review_path: .planning/phases/03-email-alerts-daily-automation/03-REVIEW.md
iteration: 1
findings_in_scope: 4
fixed: 4
skipped: 0
status: all_fixed
---

# Phase 03: Code Review Fix Report

**Fixed at:** 2026-05-05
**Source review:** .planning/phases/03-email-alerts-daily-automation/03-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 4
- Fixed: 4
- Skipped: 0

## Fixed Issues

### WR-01: Typo in `payments.yaml` silently drops Northwest payment from forecast

**Files modified:** `payments.yaml`
**Commit:** 03a0919
**Applied fix:** Removed leading `n` from `nmercury-biz-operations-0551` -> `mercury-biz-operations-0551` so the Northwest (Mail Forwarding) payment is correctly matched to its funding account and included in forecast calculations.

### WR-02: Double email sent on alert days in `--email-summary` mode

**Files modified:** `payment_forecast.py`
**Commit:** a8e7047
**Applied fix:** Removed the separate shortfall alert email send in `--email-summary` mode. The daily summary already includes the full alert table on bad days, so now we only record dedup state for alertable accounts without sending a duplicate email.

### WR-03: Dedup state read and write are not atomic -- duplicate sends possible under concurrent execution

**Files modified:** `alert_email.py`, `payment_forecast.py`
**Commit:** 5de8324
**Applied fix:** Added `check_and_record_alerts()` function in `alert_email.py` that loads state once, identifies accounts with new/changed alerts, and saves all entries in a single atomic write. Updated `payment_forecast.py` to import and use this function instead of separate `should_send_alert`/`record_alert_sent` calls.

### WR-04: Dedup subject line counts all alertable accounts, not just deduplicated ones

**Files modified:** `payment_forecast.py`
**Commit:** a8e7047
**Applied fix:** This issue was resolved as part of the WR-02 fix. The problematic code path (building `alert_subject` from `len(alertable)` then sending via `accounts_to_alert`) was entirely removed since the duplicate shortfall email is no longer sent. The only remaining SHORTFALL ALERT subject is in the `--test-alert` path which intentionally bypasses dedup.

---

_Fixed: 2026-05-05_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
