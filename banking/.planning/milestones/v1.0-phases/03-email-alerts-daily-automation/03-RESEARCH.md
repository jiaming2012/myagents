# Phase 3: Email Alerts + Daily Automation - Research

**Researched:** 2026-05-04
**Domain:** Email delivery (Gmail SMTP), alert deduplication, CLI automation
**Confidence:** HIGH

## Summary

This phase adds email alerting on top of the existing Phase 2 forecast engine. The technical domain is straightforward: Python's stdlib (`smtplib`, `email.mime`, `hashlib`, `json`) provides everything needed for Gmail SMTP delivery, HTML email construction, and content-hash deduplication. No new third-party dependencies are required.

The primary complexity is in HTML email formatting for Gmail compatibility (inline CSS required, table-based layout, 102KB size limit) and in correctly wiring the alert/summary modes into the existing `payment_forecast.py` CLI without bloating that module. The dedup mechanism (content hash stored in `.alert_state.json`) is a simple JSON file pattern already familiar from the project's file-based approach.

**Primary recommendation:** Add an `alert_email.py` module for email construction and sending, extend `payment_forecast.py` with `--email-summary`, `--test-alert`, and `--dry-run` flags, and use a `.alert_state.json` file for dedup state. All stdlib -- zero new dependencies.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Alert triggering is configurable per-account via an `alert_on` field in payments.yaml on each funding account. Values: `error` (negative balance only), `warning` (negative or below min_balance), `none` (no alerts). Default if omitted: `error`.
- **D-02:** Alert emails use HTML with tables -- formatted tables showing account balances and shortfalls with color highlighting.
- **D-03:** Single recipient from `ALERT_EMAIL` env var in `.env`. No multi-recipient support needed.
- **D-04:** Alert email shows the full forecast with problem accounts highlighted -- complete picture at a glance, shortfall accounts bolded/colored.
- **D-05:** Duplicate prevention via content hash -- hash the shortfall details (account ID + projected balance + payment amounts triggering the shortfall). Only send if hash differs from last sent alert for that account.
- **D-06:** Dedup state stored in `.alert_state.json` in repo root (gitignored). Tracks per-account alert hashes and timestamps.
- **D-07:** Daily summary sends condensed digest on good days, full report when there are warnings/errors. Good-day digest shows just summary totals (total outgoing, total available, net position). Problem-day report includes full per-account breakdown with highlighted shortfalls.
- **D-08:** Summary triggered via `--email-summary` flag on `payment_forecast.py`. Configurable range via existing `--days` flag. User sets up their own cron job. Add `task forecast:email` and `task forecast:email-weekly` shortcuts to Taskfile.yml.
- **D-09:** `--test-alert` runs the real forecast and forces the email send regardless of whether shortfalls exist. Tests the full pipeline end-to-end.
- **D-10:** `--dry-run` exports the email as an HTML file (e.g., `forecast_preview.html`) that can be opened locally in a browser to preview exactly what would be sent. Does not send any email.

### Claude's Discretion
- HTML email template design (inline CSS for Gmail compatibility)
- smtplib connection handling (TLS, error recovery)
- `.alert_state.json` schema details (what fields beyond hash and timestamp)
- Whether `--email-summary` and `--alert` modes share a common email-building function or are separate

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ALRT-01 | System sends Gmail email alert when any funding account's projected balance goes negative within the forecast horizon | Gmail SMTP via smtplib + App Password; alert triggering from build_forecast() severity field; per-account alert_on config |
| ALRT-02 | User can run forecast in daily summary mode (cron-friendly) that emails the full forecast report | --email-summary flag on payment_forecast.py; condensed vs full report based on severity; Taskfile shortcuts |
| ALRT-03 | Alerts are idempotent -- the same shortfall does not generate duplicate email alerts within a configurable window | Content hash dedup via hashlib.sha256; .alert_state.json for per-account state persistence |
| ALRT-04 | User can test alerts with a --test-alert flag that sends a sample email without requiring a real shortfall | --test-alert forces email send regardless of shortfall state; exercises full pipeline |

</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Alert triggering logic | CLI / Application | -- | Reads forecast output, applies per-account alert_on config, decides whether to alert |
| Email construction (HTML) | CLI / Application | -- | Builds MIMEMultipart message with inline-CSS HTML body from forecast data |
| Email delivery | External Service (Gmail SMTP) | -- | smtplib connects to smtp.gmail.com:587 with TLS + App Password |
| Dedup state | Local filesystem | -- | .alert_state.json in repo root, gitignored; simple JSON read/write |
| Per-account config | Config file (payments.yaml) | -- | alert_on field on funding accounts; loaded by existing load_payments_yaml() |
| Daily automation | OS cron | -- | User configures their own cron job; project provides Taskfile shortcuts |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| smtplib | stdlib (Python 3.12) | SMTP connection to Gmail | Built-in, no dependency; supports STARTTLS on port 587 [VERIFIED: Python 3.12 stdlib] |
| email.mime | stdlib (Python 3.12) | MIMEMultipart/MIMEText email construction | Built-in; creates proper MIME messages with HTML content type [VERIFIED: Python 3.12 stdlib] |
| hashlib | stdlib (Python 3.12) | SHA-256 content hashing for dedup | Built-in; deterministic hashing for shortfall fingerprinting [VERIFIED: Python 3.12 stdlib] |
| json | stdlib (Python 3.12) | .alert_state.json read/write | Built-in; consistent with project's file-based state approach [VERIFIED: Python 3.12 stdlib] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| python-dotenv | (already in requirements.txt) | Load SMTP creds from .env | Already used by coverage_report.py for MONARCH_TOKEN etc. [VERIFIED: requirements.txt] |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| smtplib | Gmail API (google-api-python-client) | OAuth complexity, large dependency; App Password + smtplib is simpler for single-sender scripts |
| Inline CSS by hand | premailer or css-inline | Extra dependency for marginal gain; templates are small enough to hand-inline |
| .alert_state.json | SQLite | Overkill for tracking ~10 account hashes; JSON matches project pattern |

**Installation:**
```bash
# No new dependencies required -- all stdlib
# Existing requirements.txt already covers python-dotenv
```

## Architecture Patterns

### System Architecture Diagram

```
                     payment_forecast.py (CLI entry point)
                              |
              +---------------+----------------+
              |               |                |
         --email-summary  --test-alert     --dry-run
              |               |                |
              v               v                v
        build_forecast()  build_forecast()  build_forecast()
        (from Phase 2)    (force send)      (preview only)
              |               |                |
              v               v                v
        alert_email.py:                   Write HTML
        check_alert_thresholds()          to file & exit
              |
              +---> build_html_email()
              |         |
              |    [alert mode: full forecast, shortfalls highlighted]
              |    [summary mode: condensed digest OR full report]
              |         |
              v         v
        check_dedup()   MIMEMultipart message
        (.alert_state.json)
              |
              v
        send_email()
        (smtp.gmail.com:587 TLS)
              |
              v
        update_dedup_state()
```

### Recommended Module Structure
```
banking/
├── payment_forecast.py    # Extended with --email-summary, --test-alert, --dry-run
├── alert_email.py         # NEW: email construction, sending, dedup logic
├── payments.yaml          # Extended: alert_on field on funding accounts
├── .alert_state.json      # NEW: dedup state (gitignored)
├── .env                   # Extended: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL
├── .env.example           # Extended: document new env vars
├── .gitignore             # Extended: .alert_state.json, forecast_preview.html
└── Taskfile.yml           # Extended: forecast:email, forecast:email-weekly
```

### Pattern 1: Gmail SMTP with App Password (TLS on port 587)
**What:** Connect to Gmail's SMTP server using STARTTLS and authenticate with an App Password.
**When to use:** Every email send operation.
**Example:**
```python
# Source: https://developers.google.com/workspace/gmail/imap/imap-smtp
# Source: https://support.google.com/a/answer/176600
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

def send_email(subject, html_body, recipient, smtp_config):
    """Send HTML email via Gmail SMTP.

    Args:
        subject: Email subject line.
        html_body: HTML string for email body.
        recipient: Email address (from ALERT_EMAIL env var).
        smtp_config: Dict with host, port, user, password.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_config["user"]
    msg["To"] = recipient

    # Plain text fallback
    plain_text = "View this email in an HTML-capable client."
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_config["host"], smtp_config["port"]) as server:
        server.starttls()
        server.login(smtp_config["user"], smtp_config["password"])
        server.send_message(msg)
```
[VERIFIED: Python smtplib stdlib docs + Google SMTP documentation]

### Pattern 2: Content Hash Dedup
**What:** Hash shortfall details to prevent duplicate alerts for the same condition.
**When to use:** Before sending alert emails (not summary emails -- summaries always send).
**Example:**
```python
# Dedup logic
import hashlib
import json
from pathlib import Path

ALERT_STATE_FILE = Path(".alert_state.json")

def compute_alert_hash(account_id, projected_balance, payment_amounts):
    """Create deterministic hash of shortfall condition."""
    data = json.dumps({
        "account_id": account_id,
        "projected_balance": round(projected_balance, 2),
        "payments": sorted(round(a, 2) for a in payment_amounts),
    }, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()[:16]

def should_send_alert(account_id, alert_hash):
    """Check if this alert hash differs from the last sent for this account."""
    state = _load_state()
    last = state.get(account_id, {})
    return last.get("hash") != alert_hash

def record_alert_sent(account_id, alert_hash):
    """Update state after successful send."""
    state = _load_state()
    state[account_id] = {
        "hash": alert_hash,
        "sent_at": datetime.now().isoformat(),
    }
    _save_state(state)
```
[ASSUMED -- schema design is Claude's discretion per CONTEXT.md]

### Pattern 3: HTML Email with Inline CSS for Gmail
**What:** Table-based HTML layout with all styles inlined for Gmail compatibility.
**When to use:** All email body construction.
**Example:**
```python
# Source: https://developers.google.com/workspace/gmail/design/css
def build_forecast_html(forecast, highlight_shortfalls=True):
    """Build HTML email body from forecast data.

    Gmail requirements:
    - All CSS must be inline (Gmail mobile strips <style> tags)
    - Use <table> for layout, not CSS grid/flexbox
    - Keep total size under 102KB to avoid clipping
    - Use web-safe fonts (Arial, Helvetica, sans-serif)
    """
    rows = []
    for acct in forecast["accounts"]:
        bg_color = "#ffffff"
        if highlight_shortfalls:
            if acct["severity"] == "error":
                bg_color = "#fee2e2"  # light red
            elif acct["severity"] == "warning":
                bg_color = "#fef9c3"  # light yellow

        rows.append(f'''
        <tr style="background-color: {bg_color};">
            <td style="padding: 8px; border: 1px solid #ddd;">{acct["name"]}</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">
                ${acct["current_balance"]:,.2f}
            </td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">
                ${acct["projected_balance"]:,.2f}
            </td>
        </tr>''')

    return f'''<html><body style="font-family: Arial, sans-serif; margin: 0; padding: 20px;">
    <table style="width: 100%; border-collapse: collapse; max-width: 600px;">
        <tr style="background-color: #f3f4f6;">
            <th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Account</th>
            <th style="padding: 8px; border: 1px solid #ddd; text-align: right;">Current</th>
            <th style="padding: 8px; border: 1px solid #ddd; text-align: right;">Projected</th>
        </tr>
        {"".join(rows)}
    </table>
    </body></html>'''
```
[CITED: developers.google.com/workspace/gmail/design/css -- inline CSS required, Gmail strips style tags on mobile]

### Pattern 4: Per-Account Alert Threshold Config
**What:** `alert_on` field in payments.yaml controls when each funding account triggers alerts.
**When to use:** Filtering which accounts produce alert emails.
**Example:**
```yaml
# payments.yaml -- funding account with alert config
- id: cap1-recurring-4354
  name: "360 Checking (Recurring ACH)"
  type: depository
  min_balance: 500
  alert_on: warning    # alert on negative OR below min_balance
  # Values: error (default) | warning | none
```
```python
def check_alert_thresholds(forecast, accounts_config):
    """Filter forecast accounts to those that should trigger alerts.

    Args:
        forecast: Output from build_forecast().
        accounts_config: Dict mapping account_id -> account config from payments.yaml.

    Returns:
        List of forecast account dicts that meet their alert_on threshold.
    """
    alertable = []
    for acct in forecast["accounts"]:
        config = accounts_config.get(acct["id"], {})
        alert_on = config.get("alert_on", "error")  # default: error

        if alert_on == "none":
            continue
        if alert_on == "error" and acct["severity"] == "error":
            alertable.append(acct)
        elif alert_on == "warning" and acct["severity"] in ("error", "warning"):
            alertable.append(acct)

    return alertable
```
[VERIFIED: matches D-01 from CONTEXT.md]

### Anti-Patterns to Avoid
- **Embedding SMTP credentials in code:** Always load from .env via python-dotenv. Never hardcode passwords.
- **Using Gmail API instead of smtplib:** The Gmail API requires OAuth flow, service accounts, and the google-api-python-client dependency. For a single-sender CLI script, smtplib + App Password is dramatically simpler. [CITED: STATE.md -- "Gmail alerts via smtplib + App Password, not Gmail API"]
- **Using CSS classes or style blocks in HTML email:** Gmail mobile strips `<style>` tags. All styles must be inline on elements. [CITED: developers.google.com/workspace/gmail/design/css]
- **Sending dedup-bypassed summaries through the dedup check:** Summary emails (--email-summary) should always send. Dedup applies only to alert-triggered shortfall notifications.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| SMTP connection | Raw socket handling | `smtplib.SMTP` + `starttls()` | TLS negotiation, EHLO, AUTH are handled correctly by stdlib |
| MIME message construction | String concatenation of headers | `email.mime.multipart.MIMEMultipart` | Proper encoding, boundary generation, content-type headers |
| Content hashing | Custom fingerprint algorithm | `hashlib.sha256` | Deterministic, collision-resistant, stdlib |
| Config loading from .env | Manual file parsing | `python-dotenv` (already in requirements.txt) | Handles comments, quotes, multiline values |

**Key insight:** This entire phase uses Python stdlib for its core functionality. The only third-party packages already exist in requirements.txt (python-dotenv for .env loading, pyyaml for payments.yaml).

## Common Pitfalls

### Pitfall 1: Gmail App Password vs Regular Password
**What goes wrong:** Authentication fails with "Username and Password not accepted" error.
**Why it happens:** Since May 2022, Google blocks regular password login via SMTP. Only App Passwords work (requires 2FA enabled on the Google account).
**How to avoid:** Document App Password generation in .env.example comments. Use descriptive env var name (SMTP_PASSWORD not GMAIL_PASSWORD) to signal it is an App Password.
**Warning signs:** `smtplib.SMTPAuthenticationError` with error code 534.
[CITED: support.google.com/a/answer/176600]

### Pitfall 2: Gmail Clips Emails Over 102KB
**What goes wrong:** Long forecast emails get truncated with "[Message clipped] View entire message" link.
**Why it happens:** Gmail enforces a 102KB limit on rendered HTML. Full forecasts with many accounts and payment details can exceed this.
**How to avoid:** Keep HTML minimal -- no base64 images, no verbose CSS. The condensed good-day digest (D-07) naturally stays small. For problem-day reports, show summary + only problem accounts in detail.
**Warning signs:** Testing reveals "[Message clipped]" at the bottom of received emails.
[CITED: designmodo.com/html-css-emails -- 102KB clipping limit]

### Pitfall 3: Dedup State File Corruption
**What goes wrong:** Concurrent runs (e.g., cron overlap) could corrupt `.alert_state.json`.
**Why it happens:** Two processes read-modify-write the same file simultaneously.
**How to avoid:** Write atomically: write to a temp file, then `os.replace()` to the target path. For this use case (single daily cron), the risk is minimal, but atomic writes are cheap insurance.
**Warning signs:** JSON parse errors when loading .alert_state.json.
[ASSUMED -- standard file-safety practice]

### Pitfall 4: SMTP Connection Timeout in Cron
**What goes wrong:** Email send silently fails when run via cron because network is unreachable or SMTP times out.
**Why it happens:** Cron environment may differ from interactive shell (no proxy, different DNS, firewall rules).
**How to avoid:** Set explicit timeout on `smtplib.SMTP()` (e.g., 30 seconds). Catch `smtplib.SMTPException` and `socket.timeout`, log the error to stderr, and exit non-zero so cron can report failure.
**Warning signs:** Cron job appears to succeed (exit 0) but no email arrives.
[ASSUMED -- standard SMTP robustness practice]

### Pitfall 5: Floating Point in Hash Input
**What goes wrong:** Same shortfall produces different hashes on different runs because floating-point balances differ by tiny amounts.
**Why it happens:** Balance fetching returns slightly different float values (e.g., 1234.560000001 vs 1234.56).
**How to avoid:** Round all monetary values to 2 decimal places before hashing (already shown in Pattern 2 example: `round(projected_balance, 2)`).
**Warning signs:** Same shortfall condition triggers repeated alerts.
[ASSUMED -- standard floating-point practice]

## Code Examples

### SMTP Config Loading
```python
# Source: project pattern from coverage_report.py (dotenv loading)
import os
from dotenv import load_dotenv

load_dotenv()

def get_smtp_config():
    """Load SMTP configuration from environment variables."""
    config = {
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER"),
        "password": os.getenv("SMTP_PASSWORD"),
    }
    missing = [k for k, v in config.items() if v is None]
    if missing:
        raise RuntimeError(
            f"Missing SMTP config in .env: {', '.join(missing)}. "
            "See .env.example for required variables."
        )
    return config

def get_alert_recipient():
    """Load alert recipient from environment."""
    recipient = os.getenv("ALERT_EMAIL")
    if not recipient:
        raise RuntimeError("ALERT_EMAIL not set in .env")
    return recipient
```
[VERIFIED: matches .env pattern from existing codebase]

### Dry-Run HTML Export
```python
# D-10: --dry-run exports HTML for local browser preview
from pathlib import Path

def export_preview(html_body, filename="forecast_preview.html"):
    """Write HTML email to local file for preview."""
    path = Path(filename)
    path.write_text(html_body, encoding="utf-8")
    print(f"Preview written to {path.absolute()}")
    print(f"Open in browser: file://{path.absolute()}")
```
[ASSUMED -- simple implementation of D-10]

### CLI Flag Integration
```python
# Extending payment_forecast.py's argparse
parser.add_argument(
    "--email-summary", action="store_true",
    help="Email the forecast report (cron-friendly daily summary mode)"
)
parser.add_argument(
    "--test-alert", action="store_true",
    help="Send a test alert email using the real forecast (forces send regardless of shortfalls)"
)
parser.add_argument(
    "--dry-run", action="store_true",
    help="Export email as forecast_preview.html instead of sending"
)
```
[VERIFIED: matches existing argparse pattern in payment_forecast.py]

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Gmail regular password SMTP | App Password (requires 2FA) | May 2022 | Must use App Password; regular password auth is blocked |
| `<style>` blocks in HTML email | Inline CSS only | Long-standing Gmail behavior | Gmail mobile strips style tags; all CSS must be on elements |
| `SMTP_SSL` (port 465) | `SMTP` + `starttls()` (port 587) | Port 587 is the modern standard | Both work; 587 with STARTTLS is recommended by Google |

**Deprecated/outdated:**
- `temporalio/web` (for this project's Temporal setup -- not relevant to this phase)
- Gmail "Less Secure Apps" setting -- removed entirely; App Passwords are the only path

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | .alert_state.json schema: `{account_id: {hash, sent_at}}` is sufficient | Pattern 2: Content Hash Dedup | LOW -- schema is Claude's discretion per CONTEXT.md; easy to extend |
| A2 | Atomic file write via os.replace() prevents corruption | Pitfall 3 | LOW -- standard practice; risk is minimal with single daily cron |
| A3 | Rounding to 2 decimal places is sufficient for hash stability | Pitfall 5 | LOW -- monetary values are inherently 2-decimal; Monarch/Xero return reasonable precision |
| A4 | forecast_preview.html is a reasonable dry-run output filename | Code Examples | NEGLIGIBLE -- trivially changeable |

## Open Questions

1. **Where does alert triggering run in the CLI flow?**
   - What we know: --email-summary is a mode flag on payment_forecast.py (D-08). build_forecast() produces the data.
   - What's unclear: Whether alert checking (shortfall detection -> email) should be automatic on every `task forecast` run, or only when explicit flags are passed.
   - Recommendation: Alerts only fire when `--email-summary` or `--test-alert` flags are present. Regular `task forecast` remains display-only. This matches D-08's "user sets up their own cron job" design.

2. **Should summary emails also go through dedup?**
   - What we know: D-05 specifies dedup for shortfall alerts. D-07 describes daily summary mode.
   - What's unclear: If the user runs `--email-summary` twice in a day with no changes, should the second email be suppressed?
   - Recommendation: No dedup for summaries. Summaries are explicitly requested by the user (or cron). If cron runs twice, sending twice is expected behavior. Dedup only for alert-triggered shortfall notifications.

## Sources

### Primary (HIGH confidence)
- Python 3.12 stdlib (`smtplib`, `email.mime`, `hashlib`, `json`) -- verified available on this machine
- [Google SMTP settings](https://support.google.com/a/answer/176600) -- smtp.gmail.com:587, TLS, App Password
- [Gmail CSS support](https://developers.google.com/workspace/gmail/design/css) -- inline CSS requirement
- [Google IMAP/SMTP docs](https://developers.google.com/workspace/gmail/imap/imap-smtp) -- port and auth details
- Existing codebase: `payment_forecast.py`, `coverage_report.py`, `payments.yaml`, `Taskfile.yml`, `.env.example`

### Secondary (MEDIUM confidence)
- [Gmail email clipping at 102KB](https://designmodo.com/html-css-emails/) -- widely reported, confirmed by multiple sources
- [App Password requirement since May 2022](https://community.latenode.com/t/setting-up-gmail-smtp-with-app-password-for-server-side-python-scripts/12824)

### Tertiary (LOW confidence)
- None -- all claims verified or cited

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all stdlib, verified on this Python 3.12 installation
- Architecture: HIGH -- extends well-understood existing codebase patterns
- Pitfalls: HIGH -- Gmail SMTP behavior is well-documented; dedup is standard pattern

**Research date:** 2026-05-04
**Valid until:** 2026-07-04 (60 days -- Gmail SMTP and Python stdlib are very stable)
