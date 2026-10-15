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
import uuid
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
DEVICE_UUID_FILE = SESSION_FILE.parent / "device_uuid"

# Monarch's login endpoint rejects clients it considers out of date
# ("Please update to the latest version of the app to continue login").
# The upstream library identifies itself as a generic old client, so we
# present the same client headers the Monarch web app sends. Bump the
# version via MONARCH_CLIENT_VERSION in .env if Monarch raises the bar again
# (DevTools -> any api.monarch.com request -> Monarch-Client-Version).
DEFAULT_CLIENT_VERSION = "v1.0.5310"


def device_uuid():
    """Device id sent as Device-Uuid.

    Precedence: MONARCH_DEVICE_UUID in .env (copy the value your browser sends,
    DevTools -> Request Headers -> Device-Uuid, or run
    localStorage.getItem('monarchDeviceUUID') in the console) so Monarch treats
    this script as an already-trusted device and skips the email code; else a
    per-machine id persisted in .mm/device_uuid; else a fresh one.
    """
    env = os.environ.get("MONARCH_DEVICE_UUID", "").strip()
    if env:
        return env
    try:
        existing = DEVICE_UUID_FILE.read_text().strip()
        if existing:
            return existing
    except OSError:
        pass
    new = str(uuid.uuid4())
    try:
        DEVICE_UUID_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEVICE_UUID_FILE.write_text(new)
    except OSError:
        pass
    return new


def web_client_headers():
    """Headers the Monarch web app sends; required to pass the login version gate."""
    return {
        "Client-Platform": "web",
        "Monarch-Client": "monarch-core-web-app-graphql",
        "Monarch-Client-Version": os.environ.get("MONARCH_CLIENT_VERSION", DEFAULT_CLIENT_VERSION),
        "Device-Uuid": device_uuid(),
        "Origin": "https://app.monarch.com",
        "Referer": "https://app.monarch.com/",
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"),
    }
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


MFA_MARKERS = ("mfa", "multi-factor", "multi factor", "two-factor", "2fa", "totp", "otp", "one-time",
               "verification code")
# Monarch answers a login from a client that does not declare supports_email_otp
# with this text; it is the email-OTP challenge in disguise.
UPDATE_APP_TEXT = "update to the latest version of the app"


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
    if status == 403 and (any(m in text for m in MFA_MARKERS) or UPDATE_APP_TEXT in text):
        kind = "email" if ("email" in text or UPDATE_APP_TEXT in text) else "totp"
        label = detail or code
        if code and code.lower() not in label.lower():
            label = f"{code}: {label}"
        return "mfa", f"{kind}|{label}"
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
    if getattr(mm, "_headers", None) is None:
        mm._headers = {}
    mm._headers.update(web_client_headers())

    email = input("Email: ").strip()
    password = masked_input("Password: ")
    payload = {
        "username": email,
        "password": password,
        "trusted_device": True,
        "supports_mfa": True,
        "supports_email_otp": True,
        "supports_recaptcha": True,
    }
    status, body = await _post_login(mm, payload)
    kind, info = classify_login_response(status, body)

    if kind == "mfa":
        channel, label = info.split("|", 1)
        if channel == "email":
            print(f"Monarch sent a one-time code to {email}. ({label})")
            code = input("Email code: ").strip()
            payload["email_otp"] = code
        else:
            print(f"Monarch requires your authenticator code. ({label})")
            code = input("Two Factor Code: ").strip()
            payload["totp"] = code
        status, body = await _post_login(mm, payload)
        kind, info = classify_login_response(status, body)

    if kind == "ok":
        mm.set_token(info)
        mm._headers["Authorization"] = f"Token {info}"
        mm.save_session()
        print(f"Session saved to {SESSION_FILE}")
        return

    if kind == "mfa":
        # second challenge after a code was submitted: the code was wrong/expired
        print(f"Error: Monarch did not accept the code. {info.split('|', 1)[1]}", file=sys.stderr)
        sys.exit(1)

    print(f"Error: Monarch login failed. {info}", file=sys.stderr)
    if status == 403:
        print("Monarch refused this login. To skip device verification entirely, copy the "
              "Device-Uuid header your browser sends (DevTools -> any api.monarch.com request -> "
              "Request Headers) into MONARCH_DEVICE_UUID in finance-tools/.env and re-run.",
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
        return mm

    token, source = resolve_token()
    if not token:
        print(f"Warning: no Monarch credentials (no session file, MONARCH_TOKEN unset). {LOGIN_HINT}",
              file=sys.stderr)
        return None
    return MonarchMoney(token=token)
