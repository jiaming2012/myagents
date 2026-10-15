#!/usr/bin/env python3
"""
Shared Monarch Money client factory.

Credential precedence:
  1. --login / force_login=True  -> interactive login, session saved to .mm/
  2. Saved session file           -> .mm/mm_session.pickle (written by the login task)
  3. MONARCH_TOKEN env / .env     -> legacy fallback

The saved session wins over the env token so that `task finance:balances:login`
alone is enough to recover from an expired token; no .env edit required.
"""

import getpass
import os
import pickle
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from monarchmoney import MonarchMoney
    from monarchmoney.monarchmoney import MonarchMoneyEndpoints
    MonarchMoneyEndpoints.BASE_URL = "https://api.monarch.com"
except ImportError:
    MonarchMoney = None

SESSION_FILE = Path(__file__).parent / ".mm" / "mm_session.pickle"
LOGIN_HINT = "Run: task finance:balances:login  (from the myagents root)"


def session_token():
    """Return the token stored in the saved session file, or None."""
    try:
        with open(SESSION_FILE, "rb") as fh:
            data = pickle.load(fh)
        return data.get("token") or None
    except (OSError, EOFError, pickle.UnpicklingError, AttributeError):
        return None


def resolve_token():
    """Return (token, source) using the documented precedence, or (None, None)."""
    token = session_token()
    if token:
        return token, "session"
    token = os.environ.get("MONARCH_TOKEN")
    if token:
        return token, "env"
    return None, None


def is_auth_error(exc):
    """True if an exception from the Monarch client is an HTTP 401."""
    code = getattr(exc, "code", None)
    if code == 401:
        return True
    return "401" in str(exc) and "Unauthorized" in str(exc)


def auth_error_message():
    return f"Monarch rejected the saved credentials (401). {LOGIN_HINT}"


def masked_input(prompt, mask="*"):
    """Read a secret from the terminal, echoing one mask char per keystroke.

    Supports backspace. Falls back to getpass (no echo) when stdin is not a
    TTY or the platform has no termios (e.g. Windows without a POSIX layer).
    """
    if not sys.stdin.isatty():
        return getpass.getpass(prompt)
    try:
        import termios
        import tty
    except ImportError:
        return getpass.getpass(prompt)

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    chars = []
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                break
            if ch == "\x03":  # Ctrl-C
                raise KeyboardInterrupt
            if ch in ("\x7f", "\b"):
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            chars.append(ch)
            sys.stdout.write(mask)
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\n")
        sys.stdout.flush()
    return "".join(chars)


MFA_MARKERS = ("mfa", "multi-factor", "multi factor", "two-factor", "2fa", "totp", "one-time")


def classify_login_response(status, body):
    """Turn a raw /auth/login/ response into ("ok", token) | ("mfa", msg) | ("error", msg).

    The upstream library treats *every* 403 as "MFA required". Monarch also
    returns 403 for device/bot protection and other refusals, so we read the
    body and only ask for a 2FA code when Monarch actually says so.
    """
    detail = ""
    code = ""
    if isinstance(body, dict):
        detail = str(body.get("detail") or body.get("message") or body.get("error") or "")
        code = str(body.get("error_code") or body.get("code") or "")
        if not detail and body:
            detail = str(body)
    elif body:
        detail = str(body)
    detail = detail.strip()[:300]

    if status == 200 and isinstance(body, dict) and body.get("token"):
        return "ok", body["token"]

    text = f"{code} {detail}".lower()
    if status == 403 and any(m in text for m in MFA_MARKERS):
        return "mfa", detail or code
    msg = f"HTTP {status}"
    if code:
        msg += f" [{code}]"
    if detail:
        msg += f": {detail}"
    return "error", msg


async def _post_login(mm, payload):
    """POST to Monarch's login endpoint with the library's headers; return (status, body)."""
    from aiohttp import ClientSession
    headers = dict(getattr(mm, "_headers", None) or {})
    headers.pop("Authorization", None)
    async with ClientSession(headers=headers) as session:
        async with session.post(MonarchMoneyEndpoints.getLoginEndpoint(), json=payload) as resp:
            try:
                body = await resp.json(content_type=None)
            except Exception:
                body = (await resp.text())[:500]
            return resp.status, body


async def interactive_login(mm):
    """Prompt for email, masked password and (only if Monarch asks) the 2FA code.

    Unlike the library's interactive_login, a 403 that is *not* an MFA
    challenge is reported with Monarch's own error text instead of a
    misleading "Two Factor Code" prompt.
    """
    email = input("Email: ").strip()
    password = masked_input("Password: ")
    payload = {
        "password": password,
        "supports_mfa": True,
        "trusted_device": False,
        "username": email,
    }
    status, body = await _post_login(mm, payload)
    kind, info = classify_login_response(status, body)

    if kind == "ok":
        mm.set_token(info)
        mm._headers["Authorization"] = f"Token {info}"
        mm.save_session()
        return

    if kind == "mfa":
        print(f"Monarch requires a second factor: {info}")
        code = input("Two Factor Code: ").strip()
        await mm.multi_factor_authenticate(email, password, code)
        mm.save_session()
        return

    print(f"Error: Monarch login failed. {info}", file=sys.stderr)
    if status == 403:
        print("Monarch refused this login and did not ask for a 2FA code. This is usually "
              "device/bot protection on the unofficial API.\n"
              "Workaround: copy the 'Authorization: Token ...' value from a Monarch web "
              "request in your browser's DevTools into MONARCH_TOKEN in finance-tools/.env.",
              file=sys.stderr)
    sys.exit(1)


async def get_monarch_client(force_login=False):
    """Build an authenticated MonarchMoney client.

    Returns None when the package is missing or no credentials are available
    (callers decide whether that is a warning or fatal). With force_login=True
    the env token is ignored and an interactive login is performed.
    """
    if MonarchMoney is None:
        print("Warning: monarchmoney not installed", file=sys.stderr)
        return None

    if force_login:
        mm = MonarchMoney(session_file=str(SESSION_FILE))
        print("Logging in to Monarch Money...")
        await interactive_login(mm)
        print(f"Session saved to {SESSION_FILE}\n")
        return mm

    token, source = resolve_token()
    if not token:
        print(f"Warning: no Monarch credentials (no session file, MONARCH_TOKEN unset). {LOGIN_HINT}",
              file=sys.stderr)
        return None
    return MonarchMoney(token=token)
