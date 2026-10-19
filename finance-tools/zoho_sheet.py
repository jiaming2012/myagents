#!/usr/bin/env python3
"""
Zoho Sheet: mint a Sheet-scoped refresh token and upload a workbook file.

Why a separate token: Zoho refresh tokens are scope-bound. The Calendar token
(ZohoCalendar.*) and the Books token (ZohoBooks.fullaccess.all) are both rejected by
Zoho Sheet with error 2403 "OAuth scope is not valid". Zoho Sheet needs
ZohoSheet.dataAPI.UPDATE (and READ for listing).

Credentials (finance-tools/.env, or $FINANCE_TOOLS_DATA/.env from a git worktree):
    ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET   shared with the Calendar and Books tools
    ZOHO_SHEET_REFRESH_TOKEN              written by --auth

Usage:
    python zoho_sheet.py --auth <grant code> [--redirect-uri <uri>]
        Exchange a one-time grant code (API console → Self Client → Generate Code,
        scope ZohoSheet.dataAPI.UPDATE,ZohoSheet.dataAPI.READ) for a refresh token
        and store it as ZOHO_SHEET_REFRESH_TOKEN in .env.
    python zoho_sheet.py --upload exports/bills-google-sheet-2026-08-17.xlsx --name Bills
        Create a new Zoho Sheet workbook from the file; prints resource id and URL.
    python zoho_sheet.py --list
        List the workbooks visible to the token (sanity check after --auth).
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).parent


def _data_dir():
    """Where .env and the token cache live.

    $FINANCE_TOOLS_DATA wins. Otherwise this directory, unless it has no .env and we are
    inside a git worktree, in which case fall back to finance-tools/ of the main checkout
    (.env is gitignored and only exists there)."""
    env = os.getenv("FINANCE_TOOLS_DATA")
    if env:
        return Path(env)
    if (HERE / ".env").exists():
        return HERE
    try:
        import subprocess
        common = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--git-common-dir"],
                                capture_output=True, text=True, check=True).stdout.strip()
        main = Path(common).resolve().parent / HERE.name
        if (main / ".env").exists():
            return main
    except Exception:
        pass
    return HERE


DATA_DIR = _data_dir()
load_dotenv(DATA_DIR / ".env")
load_dotenv(HERE / ".env")

TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"
SHEET_BASE = "https://sheet.zoho.com/api/v2"
SCOPE = "ZohoSheet.dataAPI.UPDATE,ZohoSheet.dataAPI.READ"
TOKEN_CACHE = DATA_DIR / ".zoho_sheet_token.json"
ENV_FILE = DATA_DIR / ".env"
ENV_KEY = "ZOHO_SHEET_REFRESH_TOKEN"


def _client():
    cid, sec = os.getenv("ZOHO_CLIENT_ID"), os.getenv("ZOHO_CLIENT_SECRET")
    if not cid or not sec:
        sys.exit("ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET not set in finance-tools/.env")
    return cid, sec


# --- auth ---------------------------------------------------------------------


def auth(code, redirect_uri=None):
    """Exchange a grant code for a refresh token and persist it in .env."""
    cid, sec = _client()
    data = {"grant_type": "authorization_code", "client_id": cid, "client_secret": sec, "code": code}
    if redirect_uri:
        data["redirect_uri"] = redirect_uri
    r = requests.post(TOKEN_URL, data=data, timeout=20).json()
    if "refresh_token" not in r:
        sys.exit(f"Zoho token exchange failed: {r}\n"
                 "Grant codes expire in minutes; generate a fresh one with scope "
                 f"{SCOPE} and retry.")
    granted = r.get("scope", "")
    if "ZohoSheet.dataAPI.UPDATE" not in granted:
        sys.exit(f"Token granted with scope {granted!r}; it needs {SCOPE}. "
                 "Generate the code again with the Sheet scope.")
    _write_env(ENV_KEY, r["refresh_token"])
    TOKEN_CACHE.unlink(missing_ok=True)
    print(f"Saved {ENV_KEY} to {ENV_FILE} (scope: {granted})")


def _write_env(key, value):
    text = ENV_FILE.read_text() if ENV_FILE.exists() else ""
    line = f"{key}={value}"
    if re.search(rf"^{key}=.*$", text, flags=re.M):
        text = re.sub(rf"^{key}=.*$", line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + ("\n" if text else "") + line + "\n"
    ENV_FILE.write_text(text)


def access_token():
    if TOKEN_CACHE.exists():
        c = json.loads(TOKEN_CACHE.read_text())
        if c.get("exp", 0) > time.time() + 60:
            return c["access_token"]
    rt = os.getenv(ENV_KEY)
    if not rt:
        sys.exit(f"{ENV_KEY} not set. Run: task zoho:sheet:auth -- <grant code>  "
                 f"(API console → Self Client → Generate Code, scope {SCOPE})")
    cid, sec = _client()
    r = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token", "client_id": cid, "client_secret": sec, "refresh_token": rt,
    }, timeout=20).json()
    if "access_token" not in r:
        sys.exit(f"Zoho token error: {r}")
    TOKEN_CACHE.write_text(json.dumps({"access_token": r["access_token"], "exp": time.time() + 3300}))
    return r["access_token"]


def _headers():
    return {"Authorization": f"Zoho-oauthtoken {access_token()}"}


# --- Sheet API ----------------------------------------------------------------


def list_workbooks():
    r = requests.post(f"{SHEET_BASE}/workbooks", data={"method": "workbook.list"},
                      headers=_headers(), timeout=30)
    d = _json(r)
    books = d.get("workbooks", [])
    if not books:
        print("No workbooks visible to this token.")
    for b in books:
        print(f"{b.get('resource_id')}  {b.get('workbook_name')}  {b.get('spreadsheet_url', '')}")
    return books


def upload(path, name=None):
    path = Path(path)
    if not path.exists():
        sys.exit(f"{path} not found")
    name = name or path.stem
    with path.open("rb") as fh:
        r = requests.post(f"{SHEET_BASE}/upload",
                          data={"method": "workbook.upload", "workbook_name": name},
                          files={"file": (path.name, fh)},
                          headers=_headers(), timeout=120)
    d = _json(r)
    print(json.dumps(d, indent=2))
    rid, url = d.get("resource_id"), d.get("spreadsheet_url")
    if rid:
        print(f"\nCreated Zoho Sheet workbook {name!r}: {url or rid}")
    return d


def _json(r):
    try:
        d = r.json()
    except ValueError:
        sys.exit(f"HTTP {r.status_code}: {r.text[:400]}")
    if r.status_code >= 400 or d.get("status") == "failure" or "error_code" in d:
        if r.status_code == 401:
            TOKEN_CACHE.unlink(missing_ok=True)
        sys.exit(f"Zoho Sheet error HTTP {r.status_code}: {json.dumps(d)}")
    return d


# --- CLI ----------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--auth", metavar="GRANT_CODE", help="exchange a grant code for ZOHO_SHEET_REFRESH_TOKEN")
    g.add_argument("--upload", metavar="FILE", help="xlsx/csv/ods to upload as a new workbook")
    g.add_argument("--list", action="store_true", help="list workbooks")
    ap.add_argument("--name", help="workbook name for --upload (default: file stem)")
    ap.add_argument("--redirect-uri", help="only for a server-based OAuth client")
    a = ap.parse_args()
    if a.auth:
        auth(a.auth, a.redirect_uri)
    elif a.upload:
        upload(a.upload, a.name)
    else:
        list_workbooks()


if __name__ == "__main__":
    main()
