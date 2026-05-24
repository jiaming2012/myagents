# Technology Stack

**Project:** Banking Payment Forecaster - Forecasting + Email Alerts Milestone
**Researched:** 2026-05-03
**Scope:** New libraries needed for payment forecasting and email alerts. Does NOT re-document existing stack (monarchmoney, requests, pyyaml, python-dotenv, gql).

## Recommended Stack

### Existing (No Changes)

These are already in `requirements.txt` and stay as-is:

| Technology | Purpose | Notes |
|------------|---------|-------|
| `monarchmoney` | Monarch Money account balances | Async client, already integrated |
| `requests` | Zoho Calendar + Mercury HTTP calls | Already integrated |
| `pyyaml` | YAML config parsing | Already used for `payments.yaml` |
| `python-dotenv` | `.env` credential loading | Already integrated |
| `argparse` (stdlib) | CLI argument parsing | Already used in `coverage_report.py` |

### New Dependencies

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| `python-dateutil` | 2.9.0.post0 | Date recurrence and horizon calculation | `rrule` handles "next occurrence of day-of-month X" correctly across month boundaries, leap years, and 28/30/31-day edge cases. The existing `get_upcoming_payments()` in `coverage_report.py` has manual month-rollover logic with `try/except ValueError` -- `rrule(MONTHLY, bymonthday=X)` replaces that with one line. Also useful for generating forecast timelines. | HIGH |
| `tabulate` | 0.9.0 | CLI table formatting | The existing report uses manual `f-string` formatting with pad widths. Forecast output needs aligned columns for date/amount/balance/status across variable-width data. `tabulate` handles this with `tablefmt="simple"` or `"pipe"`. Zero config, no learning curve. | HIGH |
| `smtplib` + `email` (stdlib) | N/A (stdlib) | Gmail email alerts | Built into Python. Gmail SMTP with App Password (port 587 + STARTTLS) is the simplest, most reliable path. No external dependency. App Passwords still work as of 2025 and are Google's recommended approach for automated scripts with 2FA enabled. | HIGH |

### What NOT to Add

| Technology | Why Not |
|------------|---------|
| `typer` or `click` | The project already uses `argparse` in `coverage_report.py`. Adding a CLI framework for a tool with 3-4 flags is unnecessary churn. Stay consistent with existing code. |
| `gmail` (PyPI package) | Abandoned (last update 2013). Use stdlib `smtplib` + `email.mime`. |
| `yagmail` | Convenience wrapper around smtplib, but adds a dependency for something that takes ~15 lines of stdlib code. Not worth it for a single "send alert" function. |
| Gmail API (`google-api-python-client`) | Requires Google Cloud project setup, OAuth2 consent screen, credentials.json, token refresh flow. Massive overkill for sending one email per day. `smtplib` + App Password does the same thing in 15 lines with zero GCP setup. |
| `schedule` or `APScheduler` | The project says "run on schedule" for daily summaries. Use cron (macOS `launchd` or Linux `crontab`) -- the OS scheduler. Adding a Python scheduling library means running a persistent daemon, which contradicts the CLI-first design. |
| `pandas` | Tempting for tabular data, but the forecast is a simple list of (date, payment, amount, projected_balance) tuples. `pandas` is 30MB+ and would be the heaviest dependency by far for something that needs a `for` loop and basic arithmetic. |
| `jinja2` | For email HTML templates. The alert email is a plain-text summary (same as CLI output). If HTML formatting is ever needed, it can be added later. YAGNI. |
| `rich` | Beautiful terminal output, but the existing codebase uses plain `print()` with manual formatting. Introducing `rich` would create a style inconsistency. `tabulate` is the minimal upgrade. |
| Database (SQLite, etc.) | PROJECT.md explicitly says "No database: File-based config and caching only." The forecast is computed fresh each run from live API data. No persistence needed. |

## Email Architecture Decision

**Use `smtplib` with Gmail App Password**, not the Gmail API or MCP integration.

Rationale:
1. PROJECT.md mentions "Gmail via existing MCP integration" but MCP is a Claude Code tool, not something a standalone Python script can call. The CLI needs to send email independently.
2. Gmail App Passwords are Google's supported mechanism for automated scripts when 2FA is enabled.
3. Zero external dependencies -- `smtplib` and `email.mime` are Python stdlib.
4. Setup: generate a 16-digit App Password at https://myaccount.google.com/apppasswords, store in `.env` as `GMAIL_APP_PASSWORD`.

Required new env vars:
```
GMAIL_ADDRESS=user@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
ALERT_RECIPIENT=user@gmail.com   # can be same as sender
```

## Date Handling Architecture Decision

**Use `python-dateutil.rrule`** for all "next occurrence" calculations, replacing the manual month-rollover logic.

Current pain in `coverage_report.py` (lines 51-92):
```python
# This try/except/if chain handles month boundaries poorly
try:
    due = datetime(current_year, current_month, dom)
except ValueError:
    if current_month == 12:
        due = datetime(current_year + 1, 1, dom)
    else:
        due = datetime(current_year, current_month + 1, dom)
```

Replacement with dateutil:
```python
from dateutil.rrule import rrule, MONTHLY
from datetime import datetime

def next_occurrence(day_of_month, after=None):
    """Next date when day_of_month occurs, handling short months."""
    after = after or datetime.now()
    return rrule(MONTHLY, bymonthday=day_of_month, dtstart=after, count=1)[0]
```

This handles February 29, months with 30/31 days, and year boundaries correctly.

## CLI Output Decision

**Use `tabulate` for forecast tables only.** Keep existing report format in `coverage_report.py` unchanged (don't refactor working code).

New forecast output example:
```
Forecast: Next 30 Days (as of May 03, 2026)

Date       Payment              Amount    From Account          Projected Balance
---------  -------------------  --------  --------------------  -----------------
May 05     Amex Autopay         $1,250    Mercury Personal      $4,320 -> $3,070
May 10     Mortgage             $2,100    Mercury Personal      $3,070 -> $970
May 15     Car Insurance        $180      Mercury Personal      $970 -> $790
May 22     Student Loan         $450      Mercury Personal      $790 -> $340

*** ALERT: Mercury Personal projected to drop below $500 after May 10 ***
```

## Updated requirements.txt

```
requests
python-dotenv
pyyaml
monarchmoney
gql<4
pytest
python-dateutil>=2.9.0
tabulate>=0.9.0
```

Only two new external packages. Everything else is stdlib or already present.

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Email sending | `smtplib` (stdlib) | Gmail API | GCP project setup overhead for sending 1 email/day |
| Email sending | `smtplib` (stdlib) | `yagmail` | Extra dependency for 15 lines of code savings |
| Date math | `python-dateutil` | Manual `datetime` logic | Already broken for edge cases in existing code |
| Date math | `python-dateutil` | `arrow` or `pendulum` | Heavier; `dateutil` is the standard and only `rrule` is needed |
| CLI tables | `tabulate` | `rich` | Style mismatch with existing codebase |
| CLI tables | `tabulate` | Manual `f-strings` | Breaks on variable-width account names |
| CLI framework | `argparse` (keep) | `typer` | Unnecessary migration for 3-4 flags |
| Scheduling | OS cron / launchd | `APScheduler` | CLI tool, not a daemon |

## Sources

- [python-dateutil PyPI -- v2.9.0.post0](https://pypi.org/project/python-dateutil/)
- [dateutil rrule documentation](https://dateutil.readthedocs.io/en/stable/rrule.html)
- [tabulate PyPI -- v0.9.0](https://pypi.org/project/tabulate/)
- [Gmail App Passwords -- Google Account](https://myaccount.google.com/apppasswords)
- [Mailtrap -- Python Send Email Gmail Tutorial 2026](https://mailtrap.io/blog/python-send-email-gmail/)
- [Real Python -- Sending Emails With Python](https://realpython.com/python-send-email/)

---

*Stack research: 2026-05-03*
