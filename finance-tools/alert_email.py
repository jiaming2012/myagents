#!/usr/bin/env python3
"""
Email alert module for payment forecast.

Builds HTML emails from build_forecast() output, sends via Gmail SMTP,
deduplicates alerts using content hashing, and supports dry-run preview.

Usage (standalone preview):
    python alert_email.py --preview
"""

import hashlib
import json
import os
import smtplib
import socket
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ALERT_STATE_FILE = Path(__file__).parent / ".alert_state.json"
PAYMENTS_FILE = Path(__file__).parent / "payments.yaml"


def get_smtp_config():
    """Load SMTP configuration from environment variables.

    Returns:
        dict with keys: host, port, user, password

    Raises:
        RuntimeError: If SMTP_USER or SMTP_PASSWORD is not set.
    """
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = os.environ.get("SMTP_PORT", "587")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")

    missing = []
    if not user:
        missing.append("SMTP_USER")
    if not password:
        missing.append("SMTP_PASSWORD")
    if missing:
        raise RuntimeError(f"Missing required SMTP environment variables: {', '.join(missing)}")

    return {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password,
    }


def get_alert_recipient():
    """Load alert email recipient from environment.

    Returns:
        str: Email address for alert delivery.

    Raises:
        RuntimeError: If ALERT_EMAIL is not set.
    """
    recipient = os.environ.get("ALERT_EMAIL")
    if not recipient:
        raise RuntimeError("Missing required environment variable: ALERT_EMAIL")
    return recipient


def check_alert_thresholds(forecast, accounts_config):
    """Filter forecast accounts by their alert_on threshold config.

    Args:
        forecast: dict from build_forecast().
        accounts_config: dict mapping account_id -> account dict from payments.yaml.

    Returns:
        list of forecast account dicts that meet their alert threshold.
    """
    alertable = []
    for acct in forecast.get("accounts", []):
        acct_id = acct.get("id", "")
        config = accounts_config.get(acct_id, {})
        alert_on = config.get("alert_on", "error")
        severity = acct.get("severity", "ok")

        if alert_on == "none":
            continue
        elif alert_on == "warning" and severity in ("error", "warning"):
            alertable.append(acct)
        elif alert_on == "error" and severity == "error":
            alertable.append(acct)

    return alertable


def build_alert_html(forecast, alertable_accounts):
    """Build HTML email with full forecast table, highlighting alertable accounts.

    Args:
        forecast: dict from build_forecast().
        alertable_accounts: list of account dicts that triggered alerts.

    Returns:
        str: Complete HTML email body.
    """
    alertable_ids = {a["id"] for a in alertable_accounts}

    # Severity-based row colors
    def _row_bg(acct):
        if acct["id"] in alertable_ids:
            if acct["severity"] == "error":
                return "#fee2e2"
            elif acct["severity"] == "warning":
                return "#fef9c3"
        return "#ffffff"

    def _row_weight(acct):
        if acct["id"] in alertable_ids and acct["severity"] == "error":
            return "font-weight: bold;"
        return ""

    # Build account rows
    rows_html = ""
    for acct in forecast.get("accounts", []):
        bg = _row_bg(acct)
        weight = _row_weight(acct)
        total_outgoing = sum(p["amount"] for p in acct.get("payments", []))
        rows_html += (
            f'<tr style="background: {bg}; {weight}">'
            f'<td style="padding: 8px; border: 1px solid #ddd;">{acct["name"]}</td>'
            f'<td style="padding: 8px; border: 1px solid #ddd; text-align: right;">${acct["current_balance"]:,.2f}</td>'
            f'<td style="padding: 8px; border: 1px solid #ddd; text-align: right;">${total_outgoing:,.2f}</td>'
            f'<td style="padding: 8px; border: 1px solid #ddd; text-align: right;">${acct["projected_balance"]:,.2f}</td>'
            f'</tr>\n'
        )

    summary = forecast.get("summary", {})

    html = f"""<html>
<body style="font-family: Arial, Helvetica, sans-serif; margin: 0; padding: 20px; background: #f9fafb;">
<div style="max-width: 600px; margin: 0 auto;">
<h2 style="color: #1f2937; margin-bottom: 16px;">Payment Forecast - {datetime.now().strftime('%b %d, %Y')}</h2>

<table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
<tr style="background: #f3f4f6;">
<th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Account</th>
<th style="padding: 8px; border: 1px solid #ddd; text-align: right;">Current Balance</th>
<th style="padding: 8px; border: 1px solid #ddd; text-align: right;">Outgoing</th>
<th style="padding: 8px; border: 1px solid #ddd; text-align: right;">Projected Balance</th>
</tr>
{rows_html}</table>

<table style="width: 100%; border-collapse: collapse;">
<tr style="background: #f3f4f6;">
<th style="padding: 8px; border: 1px solid #ddd; text-align: left;" colspan="2">Summary</th>
</tr>
<tr>
<td style="padding: 8px; border: 1px solid #ddd;">Total Outgoing</td>
<td style="padding: 8px; border: 1px solid #ddd; text-align: right;">${summary.get('total_outgoing', 0):,.2f}</td>
</tr>
<tr>
<td style="padding: 8px; border: 1px solid #ddd;">Total Available</td>
<td style="padding: 8px; border: 1px solid #ddd; text-align: right;">${summary.get('total_available', 0):,.2f}</td>
</tr>
<tr style="font-weight: bold;">
<td style="padding: 8px; border: 1px solid #ddd;">Net Position</td>
<td style="padding: 8px; border: 1px solid #ddd; text-align: right;">${summary.get('net_position', 0):,.2f}</td>
</tr>
</table>

</div>
</body>
</html>"""

    return html


def build_summary_html(forecast):
    """Build daily summary email - condensed for good days, full for problem days.

    Args:
        forecast: dict from build_forecast().

    Returns:
        tuple: (subject, html_body) where subject includes appropriate prefix.
    """
    has_problems = any(
        acct.get("severity") != "ok"
        for acct in forecast.get("accounts", [])
    )

    if has_problems:
        # Problem day: full report with problem accounts highlighted
        problem_accounts = [
            a for a in forecast.get("accounts", [])
            if a.get("severity") != "ok"
        ]
        html = build_alert_html(forecast, problem_accounts)
        subject = f"Forecast ALERT - {datetime.now().strftime('%b %d, %Y')}"
    else:
        # Good day: condensed digest
        summary = forecast.get("summary", {})
        html = f"""<html>
<body style="font-family: Arial, Helvetica, sans-serif; margin: 0; padding: 20px; background: #f9fafb;">
<div style="max-width: 600px; margin: 0 auto;">
<h2 style="color: #16a34a; margin-bottom: 8px;">All accounts healthy</h2>
<p style="color: #6b7280; margin-bottom: 16px;">Payment Forecast - {datetime.now().strftime('%b %d, %Y')}</p>

<table style="width: 100%; border-collapse: collapse;">
<tr style="background: #f3f4f6;">
<th style="padding: 8px; border: 1px solid #ddd; text-align: left;" colspan="2">Summary</th>
</tr>
<tr>
<td style="padding: 8px; border: 1px solid #ddd;">Total Outgoing</td>
<td style="padding: 8px; border: 1px solid #ddd; text-align: right;">${summary.get('total_outgoing', 0):,.2f}</td>
</tr>
<tr>
<td style="padding: 8px; border: 1px solid #ddd;">Total Available</td>
<td style="padding: 8px; border: 1px solid #ddd; text-align: right;">${summary.get('total_available', 0):,.2f}</td>
</tr>
<tr style="font-weight: bold;">
<td style="padding: 8px; border: 1px solid #ddd;">Net Position</td>
<td style="padding: 8px; border: 1px solid #ddd; text-align: right;">${summary.get('net_position', 0):,.2f}</td>
</tr>
</table>

</div>
</body>
</html>"""
        subject = f"Forecast Summary - {datetime.now().strftime('%b %d, %Y')}"

    return (subject, html)


def compute_alert_hash(account_id, projected_balance, payment_amounts):
    """Compute a content hash for deduplication of alerts.

    Args:
        account_id: str account identifier.
        projected_balance: float projected balance.
        payment_amounts: list of float payment amounts.

    Returns:
        str: First 16 chars of SHA-256 hex digest.
    """
    data = json.dumps({
        "account_id": account_id,
        "projected_balance": round(projected_balance, 2),
        "payments": sorted(round(a, 2) for a in payment_amounts),
    }, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def _load_state():
    """Load alert state from JSON file.

    Returns:
        dict: Alert state, or empty dict if file missing/invalid.
    """
    try:
        return json.loads(ALERT_STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state):
    """Save alert state atomically via temp file + os.replace().

    Args:
        state: dict to persist as JSON.
    """
    tmp_path = ALERT_STATE_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state, indent=2))
    os.replace(str(tmp_path), str(ALERT_STATE_FILE))


def should_send_alert(account_id, alert_hash):
    """Check if an alert should be sent based on dedup state.

    Args:
        account_id: str account identifier.
        alert_hash: str content hash from compute_alert_hash().

    Returns:
        bool: True if alert should be sent (new or changed).
    """
    state = _load_state()
    entry = state.get(account_id)
    if entry is None:
        return True
    return entry.get("hash") != alert_hash


def record_alert_sent(account_id, alert_hash):
    """Record that an alert was sent for dedup tracking.

    Args:
        account_id: str account identifier.
        alert_hash: str content hash from compute_alert_hash().
    """
    state = _load_state()
    state[account_id] = {
        "hash": alert_hash,
        "sent_at": datetime.now().isoformat(),
    }
    _save_state(state)


def check_and_record_alerts(alertable):
    """Atomically check dedup state and record new alerts in one pass.

    Loads state once, identifies which accounts have new/changed alerts,
    records them all, then saves state. This avoids the race condition
    of separate should_send_alert + record_alert_sent calls.

    Args:
        alertable: list of account dicts with 'id', 'projected_balance', 'payments'.

    Returns:
        list of (acct, alert_hash) tuples for accounts that need alerting.
    """
    state = _load_state()
    to_send = []
    for acct in alertable:
        payment_amounts = [p["amount"] for p in acct["payments"]]
        alert_hash = compute_alert_hash(acct["id"], acct["projected_balance"], payment_amounts)
        entry = state.get(acct["id"])
        if entry is None or entry.get("hash") != alert_hash:
            to_send.append((acct, alert_hash))

    # Record all at once before returning
    for acct, alert_hash in to_send:
        state[acct["id"]] = {
            "hash": alert_hash,
            "sent_at": datetime.now().isoformat(),
        }
    if to_send:
        _save_state(state)

    return to_send


def send_email(subject, html_body, recipient, smtp_config):
    """Send an HTML email via SMTP with TLS.

    Args:
        subject: str email subject line.
        html_body: str HTML email body.
        recipient: str recipient email address.
        smtp_config: dict from get_smtp_config().

    Raises:
        smtplib.SMTPException: On SMTP errors.
        socket.timeout: On connection timeout.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_config["user"]
    msg["To"] = recipient

    # Plain text fallback
    msg.attach(MIMEText("View this email in an HTML-capable client.", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_config["host"], smtp_config["port"], timeout=30) as server:
            server.starttls()
            server.login(smtp_config["user"], smtp_config["password"])
            server.send_message(msg)
    except (smtplib.SMTPException, socket.timeout) as e:
        print(f"Error sending email: {e}", file=sys.stderr)
        raise


def export_preview(html_body, filename="forecast_preview.html"):
    """Write HTML email to a local file for dry-run preview.

    Args:
        html_body: str HTML content to write.
        filename: str output filename (default: forecast_preview.html).
    """
    path = Path(filename).resolve()
    path.write_text(html_body)
    print(f"Preview written to {path}")
    print(f"Open in browser: file://{path}")
