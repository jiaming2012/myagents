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
    from monarchmoney.monarchmoney import MonarchMoneyEndpoints, RequireMFAException
    MonarchMoneyEndpoints.BASE_URL = "https://api.monarch.com"
except ImportError:
    MonarchMoney = None
    RequireMFAException = Exception

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


async def interactive_login(mm):
    """Prompt for email, masked password and (if required) the 2FA code."""
    email = input("Email: ").strip()
    password = masked_input("Password: ")
    try:
        await mm.login(email, password, use_saved_session=False, save_session=True)
    except RequireMFAException:
        code = input("Two Factor Code: ").strip()
        await mm.multi_factor_authenticate(email, password, code)
        mm.save_session()


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
