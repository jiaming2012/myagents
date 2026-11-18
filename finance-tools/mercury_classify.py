#!/usr/bin/env python3
"""Mercury API reader + merchant classifier shared by the Zoho Books recon.

Reads posted transactions from the Mercury API and splits them, per
merchants.yaml, into internal transfers / coded spends / skipped (Toast) /
unmatched. zoho_books_recon.py turns the result into Zoho Books documents.

(Until 2026-11-17 this lived in the Xero pre-coder, mercury_precode.py, which
posted the same classification into Xero; Zoho Books replaced Xero and the
pre-coder and its launchd agent were retired.)

Mercury auth: MERCURY_API_TOKEN, read from finance-tools/.env or, as fallback,
the toast_xero checkout's .env (~/projects/yumyums/accounting/xero/.env).
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")
load_dotenv(HERE.parent / ".env")
load_dotenv(Path.home() / "projects/yumyums/accounting/xero/.env")  # toast_xero checkout

MERCURY_API = "https://api.mercury.com/api/v1"


# --- Mercury -----------------------------------------------------------------


def fetch_mercury(account_id, start, end):
    token = os.getenv("MERCURY_API_TOKEN")
    if not token:
        sys.exit("MERCURY_API_TOKEN not set (finance-tools/.env or the toast_xero checkout's .env)")
    headers = {"Authorization": f"Bearer {token}"}
    txns, offset = [], 0
    while True:
        r = requests.get(
            f"{MERCURY_API}/account/{account_id}/transactions",
            headers=headers,
            params={"start": start, "end": end, "limit": 500, "offset": offset, "order": "asc"},
        )
        r.raise_for_status()
        batch = r.json()["transactions"]
        txns.extend(batch)
        if len(batch) < 500:
            break
        offset += 500
    # only money that actually moved
    return [t for t in txns if t["status"] == "sent"]


def merchant_name(t):
    return (t.get("counterpartyNickname") or t.get("counterpartyName")
            or (t.get("bankDescription") or "").split(";")[0].strip() or "Unknown")


# --- Classification ----------------------------------------------------------


def classify(txns, cfg, own_bank_name):
    """Split one account's Mercury txns into transfers / coded / skipped / unmatched.
    coded = (txn, merchant name, account code, vendor name or None).

    `own_bank_name` is the account's short bank name in merchants.yaml
    (`bank_account`), so a transfer never targets its own side."""
    transfers, coded, skipped, unmatched = [], [], [], []
    for t in txns:
        name = merchant_name(t)
        haystack = f"{name} {t.get('bankDescription') or ''}".lower()
        direction = "credit" if t["amount"] > 0 else "debit"

        rule = next(
            (s for s in cfg.get("skip", [])
             if s["pattern"].lower() in haystack
             and s.get("direction", direction) == direction),
            None,
        )
        if rule:
            skipped.append((t, rule["reason"]))
            continue

        # Transfers match on the counterparty name only: ACH descriptors embed
        # the company name ("OnPay Inc; PayrollTS; Yumyums Kitchen LLC"), so
        # matching the description would misroute vendor debits as transfers.
        cp_name = (t.get("counterpartyNickname") or t.get("counterpartyName") or "").lower()
        target = next(
            (acct for pat, acct in cfg.get("transfers", {}).items()
             if pat.lower() in cp_name and acct != own_bank_name),
            None,
        )
        if target:
            transfers.append((t, target))
            continue

        m = next(
            (m for m in cfg.get("merchants", [])
             if m["pattern"].lower() in haystack),
            None,
        )
        if m:
            coded.append((t, name, m["account"], m.get("vendor")))
        else:
            unmatched.append((t, name))
    return transfers, coded, skipped, unmatched
