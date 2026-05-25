---
phase: 03-email-alerts-daily-automation
reviewed: 2026-05-04T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - alert_email.py
  - payment_forecast.py
  - Taskfile.yml
  - .env.example
  - .gitignore
  - payments.yaml
findings:
  critical: 0
  warning: 4
  info: 4
  total: 8
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-05-04
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Phase 3 introduces email alerting (`alert_email.py`), wires it into `payment_forecast.py` via `--email-summary`, `--test-alert`, and `--dry-run` flags, and adds Taskfile tasks for the new modes. The implementation is generally solid: SMTP config validation, content-hash dedup, atomic state writes, and HTML rendering are all correct. Four issues warrant attention before relying on this in production cron.

The most impactful finding is a typo in `payments.yaml` that silently drops the "Northwest (Mail Forwarding)" payment from every forecast — its `funding_account` ID has a leading `n` that makes it non-existent. The second-most impactful is a double-email logic issue: on a bad day, `--email-summary` sends both a daily summary (which already contains the full alert table) AND a separate shortfall alert, resulting in two emails per run.

---

## Warnings

### WR-01: Typo in `payments.yaml` silently drops Northwest payment from forecast

**File:** `payments.yaml:466`
**Issue:** The `funding_account` value is `nmercury-biz-operations-0551` — it has a leading `n`. No account with that ID exists, so `build_forecast()` hits the `if not account: continue` branch (payment_forecast.py:258) and silently omits this payment entirely. The forecast will undercount outgoing payments for the operations account.

**Fix:**
```yaml
- name: "Northwest (Mail Forwarding)"
  amount: 20.00
  day_of_month: 16
  funding_account: mercury-biz-operations-0551   # remove leading 'n'
```

---

### WR-02: Double email sent on alert days in `--email-summary` mode

**File:** `payment_forecast.py:578-607`
**Issue:** When `--email-summary` runs and there are shortfalls, the code sends two emails: first the daily summary (line 586), which already renders the full alert table via `build_summary_html` → `build_alert_html` for problem days, then a second "SHORTFALL ALERT" email (line 602) with the same content. The recipient gets duplicate emails on every bad day.

**Fix:** Either suppress the secondary shortfall alert when `--email-summary` has already sent a full-report summary, or skip the summary email and only send the shortfall alert on bad days. The simplest fix:

```python
if args.email_summary:
    subject, html_body = build_summary_html(forecast)

    if args.dry_run:
        export_preview(html_body)
    else:
        send_email(subject, html_body, recipient, smtp_config)
        print(f"Summary email sent to {recipient}")

        # Only check dedup and record state -- do NOT send a second alert email.
        # build_summary_html already produces a full alert table on bad days.
        alertable = check_alert_thresholds(forecast, accounts_config)
        for acct in alertable:
            payment_amounts = [p["amount"] for p in acct["payments"]]
            alert_hash = compute_alert_hash(acct["id"], acct["projected_balance"], payment_amounts)
            record_alert_sent(acct["id"], alert_hash)
```

---

### WR-03: Dedup state read and write are not atomic — duplicate sends possible under concurrent execution

**File:** `alert_email.py:296-315`
**Issue:** `should_send_alert` reads the state file and `record_alert_sent` reads it again and writes it back. Between those two calls the process can be interrupted (or a second instance started), resulting in two processes both reading "not sent" and both sending the alert. The dedup guarantee is broken.

This is low-probability with a daily cron job, but a corrupt or stale `.alert_state.json` (e.g., from `Ctrl-C` between send and record) will also reset suppression for all accounts.

**Fix:** Combine the check and record into a single operation using a file lock, or load state once at the top of the email block and pass it through:

```python
def check_and_record_alerts(alertable, accounts_config):
    """Atomically load state, filter already-sent alerts, record new ones."""
    state = _load_state()
    to_send = []
    for acct in alertable:
        payment_amounts = [p["amount"] for p in acct["payments"]]
        alert_hash = compute_alert_hash(acct["id"], acct["projected_balance"], payment_amounts)
        if state.get(acct["id"], {}).get("hash") != alert_hash:
            to_send.append((acct, alert_hash))

    # Record all at once before returning (send happens after)
    for acct, alert_hash in to_send:
        state[acct["id"]] = {"hash": alert_hash, "sent_at": datetime.now().isoformat()}
    if to_send:
        _save_state(state)

    return to_send
```

---

### WR-04: Dedup subject line counts all alertable accounts, not just deduplicated ones

**File:** `payment_forecast.py:593`
**Issue:** `alert_subject` is built using `len(alertable)` (all accounts meeting threshold), but only a subset (`accounts_to_alert`) actually passes the dedup check. If two of three accounts were already alerted, the subject says "3 account(s) need attention" but only one account's alert is new. Misleading to the recipient.

**Fix:**
```python
# Build subject after dedup filtering, not before
if accounts_to_alert:
    alert_subject = f"SHORTFALL ALERT -- {len(accounts_to_alert)} account(s) need attention"
    send_email(alert_subject, alert_html, recipient, smtp_config)
```

Note: `alert_html` at line 592 is built from the full `alertable` list, so the email body shows all alertable accounts (not just new ones). This may or may not be intentional — if the intent is "show all current problems", it's fine; if the intent is "show only new problems", `alert_html` should also be filtered.

---

## Info

### IN-01: Silent discard of corrupt `.alert_state.json` could reset dedup on any JSON error

**File:** `alert_email.py:269-272`
**Issue:** `_load_state` catches `json.JSONDecodeError` and silently returns `{}`. A corrupt state file (partial write, manual edit mistake) causes all dedup history to be silently reset, potentially re-sending previously suppressed alerts.

**Fix:** Log a warning to stderr before returning the empty fallback:
```python
except (FileNotFoundError, json.JSONDecodeError) as e:
    if not isinstance(e, FileNotFoundError):
        print(f"Warning: .alert_state.json is corrupt, resetting dedup state: {e}", file=sys.stderr)
    return {}
```

---

### IN-02: `xero_balances` fetch has no error handling — exception crashes the run

**File:** `payment_forecast.py:548`
**Issue:** If `fetch_xero_balances()` raises any exception (network error, auth failure, etc.), it propagates uncaught and crashes the entire forecast run, including the CLI output. The Monarch balance fetch on line 547 has the same pattern. At minimum, Xero errors should be caught and degraded gracefully since many accounts don't use Xero.

**Fix:**
```python
try:
    xero_balances = fetch_xero_balances() if fetch_xero_balances else {}
except Exception as e:
    print(f"Warning: Could not fetch Xero balances ({e}), continuing without them", file=sys.stderr)
    xero_balances = {}
```

---

### IN-03: `--dry-run` preview file written relative to CWD — unpredictable location when run from cron

**File:** `alert_email.py:357`
**Issue:** `export_preview` writes to `Path(filename).resolve()`, which resolves relative to the process's current working directory. When invoked from cron (where CWD is often `/` or the user's home directory), `forecast_preview.html` ends up somewhere unexpected rather than next to the script.

**Fix:** Write the preview file next to the script, consistent with how `ALERT_STATE_FILE` and `PAYMENTS_FILE` are anchored:
```python
path = (Path(__file__).parent / filename).resolve()
```

---

### IN-04: `.env.example` lists Mercury API keys but not Xero credentials; Mercury is being replaced

**File:** `.env.example:6-7`
**Issue:** `MERCURY_BUSINESS_API_KEY` and `MERCURY_PERSONAL_API_KEY` are documented in `.env.example`, but Mercury is being replaced by Xero for business balances. The Xero credentials (`XERO_CLIENT_ID`, `XERO_CLIENT_SECRET`, `XERO_TENANT_ID`, and the token path) are not documented, making onboarding harder.

**Fix:** Add Xero credential placeholders and add a comment noting the Mercury keys are legacy/personal-only:
```
# Xero (business account balances — replaces Mercury for business accounts)
XERO_CLIENT_ID=
XERO_CLIENT_SECRET=
XERO_TENANT_ID=

# Mercury (personal accounts only — business accounts use Xero)
MERCURY_PERSONAL_API_KEY=
```

---

_Reviewed: 2026-05-04_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
