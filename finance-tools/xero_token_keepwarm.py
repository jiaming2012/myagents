#!/usr/bin/env python3
"""Daily Xero token keep-warm.

Refreshes the shared OAuth2 token so the refresh token's 60-day non-use
expiry can never be reached. On failure, emails ALERT_EMAIL immediately
via the SMTP config in .env, so a broken auth chain surfaces the day it
breaks instead of the day the books are needed.

Installed as a launchd agent: ~/Library/LaunchAgents/com.jamal.xero-keepwarm.plist
Runs daily and at login. Logs to ~/Library/Logs/xero-keepwarm.log.

The token file is the canonical store shared with yumyums
(finance-tools/.xero_token.json is a symlink to the yumyums copy).
"""

import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# launchd runs with an arbitrary cwd; load env explicitly before importing
# modules that read credentials at import time.
load_dotenv(Path(__file__).parent / ".env")

from xero_balances import load_token, refresh_token  # noqa: E402


def alert_failure(error):
    from alert_email import get_alert_recipient, get_smtp_config, send_email

    html = f"""<html><body style="font-family: Arial, sans-serif;">
<h2 style="color: #b91c1c;">Xero token refresh failed</h2>
<p>The daily keep-warm could not refresh the Xero OAuth2 token at
{datetime.now().strftime('%Y-%m-%d %H:%M')}.</p>
<pre style="background:#f3f4f6; padding:12px;">{error}</pre>
<p>If this says <code>invalid_grant</code>, the refresh token is dead and a
one-time re-consent is needed:<br>
<code>cd ~/projects/yumyums/accounting/xero &amp;&amp; python app.py</code>,
then open <code>http://localhost:9091/login</code>.</p>
</body></html>"""
    send_email(
        subject="ACTION NEEDED: Xero token refresh failed",
        html_body=html,
        recipient=get_alert_recipient(),
        smtp_config=get_smtp_config(),
    )


def main():
    token = load_token()
    if not token:
        msg = "No token file found at the canonical store."
        print(f"{datetime.now().isoformat()} FAIL {msg}")
        alert_failure(msg)
        return 1
    try:
        refresh_token(token)
        print(f"{datetime.now().isoformat()} OK token refreshed")
        return 0
    except Exception as e:
        print(f"{datetime.now().isoformat()} FAIL {e}")
        try:
            alert_failure(e)
        except Exception as mail_err:
            print(f"{datetime.now().isoformat()} FAIL alert email also failed: {mail_err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
