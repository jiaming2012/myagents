#!/usr/bin/env python3
"""Pre-code Mercury transactions into Xero for reconciliation.

Reads posted transactions from the Mercury API for every account listed in
merchants.yaml, classifies them, and creates coded Xero transactions so
reconciliation becomes confirm-clicking instead of hand-coding:

  - internal transfers / own-account wires -> Xero BankTransfers
  - spends (and non-Toast refunds)         -> Spend/Receive Money BankTransactions
  - Toast payouts                          -> skipped (toast_xero owns those)
  - unmatched merchants                    -> never posted, listed for review

Idempotency: every created transaction carries Reference "mercury:<txn-id>".
Existing Xero transactions in the window are fetched first; anything already
referenced is skipped, and unreferenced same-date same-amount matches are
consumed count-aware instead of duplicated. An internal transfer seen from
both accounts' sides in one run is only created once.

Usage:
    python mercury_precode.py                          # dry run, full backlog window
    python mercury_precode.py --days 45                # dry run, trailing window
    python mercury_precode.py --days 45 --apply --email   # weekly agent mode

Installed as a launchd agent: ~/Library/LaunchAgents/com.jamal.mercury-precode.plist
(weekly, Monday 09:45; logs to ~/Library/Logs/mercury-precode.log). --email sends
a report via alert_email.py only when something needs attention: transactions
created, merchants unmatched, or failures.

Xero auth: shared token store .xero_token.json (symlink to the yumyums file).
Mercury auth: MERCURY_API_TOKEN, read from finance-tools/.env or, as fallback,
~/projects/yumyums/accounting/xero/.env (where the live token lives).
"""

import argparse
import html
import json
import os
import sys
import traceback
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")
load_dotenv(HERE.parent / ".env")  # TODOIST_API_TOKEN lives at the myagents root
load_dotenv(Path.home() / "projects/yumyums/accounting/xero/.env")

TOKEN_FILE = HERE / ".xero_token.json"
TOKEN_URL = "https://identity.xero.com/connect/token"
XERO_API = "https://api.xero.com/api.xro/2.0"
MERCURY_API = "https://api.mercury.com/api/v1"

# Statement lines in the reconcile queue start 2025-09-21; earlier Mercury
# activity has nothing to match against. Correctness bar is 2026-01-01
# (books before that lived in QuickBooks), but the full backlog was
# pre-coded 2026-09-19 so the whole queue can clear.
DEFAULT_START = "2025-09-21"

BATCH_SIZE = 50


# --- Xero client -------------------------------------------------------------


class Xero:
    def __init__(self):
        client_id = os.getenv("XERO_CLIENT_ID")
        client_secret = os.getenv("XERO_CLIENT_SECRET")
        if not client_id or not client_secret:
            sys.exit("XERO_CLIENT_ID/XERO_CLIENT_SECRET not set")
        token = json.loads(TOKEN_FILE.read_text())
        resp = requests.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": token["refresh_token"]},
            auth=(client_id, client_secret),
        )
        if resp.status_code == 400 and resp.json().get("error") == "invalid_grant":
            sys.exit(
                "Xero refresh token expired. Re-authorize: "
                "cd ~/projects/yumyums/accounting/xero && python app.py"
            )
        resp.raise_for_status()
        token = resp.json()
        TOKEN_FILE.write_text(json.dumps(token))
        self.headers = {
            "Authorization": f"Bearer {token['access_token']}",
            "Accept": "application/json",
        }
        conns = requests.get("https://api.xero.com/connections", headers=self.headers).json()
        self.headers["Xero-tenant-id"] = conns[0]["tenantId"]

    def get(self, endpoint, **params):
        r = requests.get(f"{XERO_API}/{endpoint}", headers=self.headers, params=params)
        r.raise_for_status()
        return r.json()

    def put(self, endpoint, payload):
        r = requests.put(
            f"{XERO_API}/{endpoint}",
            headers={**self.headers, "Content-Type": "application/json"},
            params={"summarizeErrors": "false"},
            data=json.dumps(payload),
        )
        if r.status_code not in (200, 400):
            r.raise_for_status()
        return r.json()

    def bank_accounts(self):
        accts = self.get("Accounts", where='Type=="BANK"')["Accounts"]
        return {a["Name"]: a["AccountID"] for a in accts}

    def account_codes(self):
        accts = self.get("Accounts")["Accounts"]
        return {a["Code"]: a["Name"] for a in accts if a.get("Code")}

    def bank_transactions(self, bank_account_id, start, end):
        """All non-deleted BankTransactions on one bank account in [start, end]."""
        where = (
            f'BankAccount.AccountID==Guid("{bank_account_id}")'
            f"&&Date>=DateTime({_dt_args(start)})"
            f"&&Date<=DateTime({_dt_args(end)})"
            '&&Status!="DELETED"'
        )
        out, page = [], 1
        while True:
            batch = self.get("BankTransactions", where=where, page=page)["BankTransactions"]
            out.extend(batch)
            if len(batch) < 100:
                return out
            page += 1

    def bank_transfers(self, start, end):
        where = (
            f"Date>=DateTime({_dt_args(start)})"
            f"&&Date<=DateTime({_dt_args(end)})"
        )
        return self.get("BankTransfers", where=where)["BankTransfers"]


def _dt_args(iso_date):
    """'2025-09-21' -> '2025,9,21' (Xero's where parser rejects leading zeros)."""
    return ",".join(str(int(p)) for p in iso_date.split("-"))


def xero_date(iso):
    """Xero returns /Date(1234567890000+0000)/ (midnight UTC) -- convert to YYYY-MM-DD."""
    if iso.startswith("/Date("):
        ms = int(iso[6:].split("+")[0].split("-")[0].rstrip(")/"))
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()
    return iso[:10]


# --- Mercury -----------------------------------------------------------------


def fetch_mercury(account_id, start, end):
    token = os.getenv("MERCURY_API_TOKEN")
    if not token:
        sys.exit("MERCURY_API_TOKEN not set (finance-tools/.env or yumyums xero/.env)")
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
    """Split one account's Mercury txns into transfers / coded / skipped / unmatched."""
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
            coded.append((t, name, m["account"]))
        else:
            unmatched.append((t, name))
    return transfers, coded, skipped, unmatched


# --- Dedup against existing Xero state ----------------------------------------


def spend_index(xero, bank_account_id, start, end):
    """Index one account's existing spends by mercury ref and (date, amount) count.

    Signatures are counts, consumed as they match, so two genuine same-day
    same-amount Mercury txns aren't both skipped when Xero holds only one.
    SPEND/RECEIVE-TRANSFER rows are sides of BankTransfers and are indexed
    there, not as spend candidates.
    """
    refs, sigs = set(), defaultdict(int)
    for bt in xero.bank_transactions(bank_account_id, start, end):
        ref = bt.get("Reference") or ""
        if ref.startswith("mercury:"):
            refs.add(ref)
        if bt["Type"].endswith("-TRANSFER"):
            continue
        sign = -1 if bt["Type"].startswith("SPEND") else 1
        sigs[(xero_date(bt["Date"]), round(sign * bt["Total"], 2))] += 1
    return refs, sigs


def transfer_index(xero, start, end):
    """Index all existing BankTransfers by mercury ref and (date, amount, from, to) count."""
    refs, sigs = set(), defaultdict(int)
    for tr in xero.bank_transfers(start, end):
        ref = tr.get("Reference") or ""
        if ref.startswith("mercury:"):
            refs.add(ref)
        key = (
            xero_date(tr["Date"]),
            round(tr["Amount"], 2),
            tr["FromBankAccount"]["AccountID"],
            tr["ToBankAccount"]["AccountID"],
        )
        sigs[key] += 1
    return refs, sigs


# --- Todoist -----------------------------------------------------------------


def file_todoist_task(cfg, content, description):
    """File an action task in the Bookkeeping Todoist project.

    Skips silently when no token/project is configured, and skips creation
    when an open task with the same content already exists (a still-open
    task from last week's run already has the operator's attention).
    """
    token = os.getenv("TODOIST_API_TOKEN", "").strip('"')
    project_id = cfg.get("todoist_project_id")
    if not token or not project_id:
        return False
    headers = {"Authorization": f"Bearer {token}"}
    existing = requests.get(
        "https://api.todoist.com/api/v1/tasks",
        headers=headers, params={"project_id": project_id},
    )
    existing.raise_for_status()
    if any(t["content"] == content for t in existing.json().get("results", [])):
        return False
    requests.post(
        "https://api.todoist.com/api/v1/tasks",
        headers=headers,
        json={
            "content": content,
            "description": description,
            "project_id": project_id,
            "due_string": "today",
            "priority": 3,
        },
    ).raise_for_status()
    return True


# --- Email -------------------------------------------------------------------


def send_report(subject, lines):
    from alert_email import get_alert_recipient, get_smtp_config, send_email

    body = "<br>\n".join(html.escape(line).replace("  ", "&nbsp;&nbsp;") for line in lines)
    send_email(
        subject=subject,
        html_body=f'<html><body style="font-family: Menlo, monospace; font-size: 12px;">'
                  f"{body}</body></html>",
        recipient=get_alert_recipient(),
        smtp_config=get_smtp_config(),
    )


# --- Main --------------------------------------------------------------------


def run(args):
    cfg = yaml.safe_load(Path(args.config).read_text())

    report = []

    def say(line=""):
        print(line)
        report.append(line)

    say(f"Window: {args.start} -> {args.end}   mode: {'APPLY' if args.apply else 'dry run'}")

    xero = Xero()
    banks = xero.bank_accounts()
    codes = xero.account_codes()
    id_to_name = {v: k for k, v in banks.items()}

    for acct in cfg["accounts"]:
        if acct["xero_bank_account"] not in banks:
            sys.exit(f"Xero bank account not found: {acct['xero_bank_account']}")
    for target in cfg.get("transfers", {}).values():
        if target not in banks:
            sys.exit(f"transfers: target Xero bank account not found: {target}")

    xfer_refs, xfer_sigs = transfer_index(xero, args.start, args.end)
    planned_xfers = defaultdict(int)   # transfers queued this run, keyed like xfer_sigs
    matched_mirror = defaultdict(int)  # sig matches consumed by one side; lets the
                                       # other side of the same Mercury pair match too

    new_txns, new_xfers, dupes, skipped, unmatched = [], [], [], [], []

    for acct in cfg["accounts"]:
        bank_name = acct["xero_bank_account"]
        bank_id = banks[bank_name]
        txns = fetch_mercury(acct["mercury_account_id"], args.start, args.end)
        transfers, coded, skipped_a, unmatched_a = classify(txns, cfg, bank_name)
        skipped.extend(skipped_a)
        unmatched.extend(unmatched_a)
        say(f"{bank_name}: {len(txns)} Mercury txns "
            f"({len(coded)} coded, {len(transfers)} transfers, "
            f"{len(skipped_a)} skipped, {len(unmatched_a)} unmatched)")

        bad = {code for _, _, code in coded if code not in codes}
        if bad:
            sys.exit(f"merchants.yaml uses unknown account codes: {sorted(bad)}")

        refs, sigs = spend_index(xero, bank_id, args.start, args.end)
        for t, name, code in coded:
            ref = f"mercury:{t['id']}"
            day, amt = t["postedAt"][:10], round(t["amount"], 2)
            if ref in refs:
                dupes.append((t, name, "already posted (mercury ref)"))
            elif sigs[(day, amt)] > 0:
                sigs[(day, amt)] -= 1
                dupes.append((t, name, "existing spend/receive same date+amount"))
            else:
                new_txns.append({
                    "Type": "SPEND" if amt < 0 else "RECEIVE",
                    "Contact": {"Name": name},
                    "Date": day,
                    "Reference": ref,
                    "Url": t["dashboardLink"],
                    "BankAccount": {"AccountID": bank_id},
                    "LineAmountTypes": "NoTax",
                    "LineItems": [{
                        "Description": (t.get("bankDescription") or name)[:250],
                        "Quantity": 1,
                        "UnitAmount": abs(amt),
                        "AccountCode": code,
                        "TaxType": "NONE",
                    }],
                })

        for t, target_name in transfers:
            ref = f"mercury:{t['id']}"
            day, amt = t["postedAt"][:10], round(abs(t["amount"]), 2)
            frm, to = (bank_id, banks[target_name]) if t["amount"] < 0 else (banks[target_name], bank_id)
            key = (day, amt, frm, to)
            if ref in xfer_refs:
                dupes.append((t, target_name, "already posted (mercury ref)"))
            elif xfer_sigs[key] > 0:
                xfer_sigs[key] -= 1
                matched_mirror[key] += 1
                dupes.append((t, target_name, "existing transfer same date+amount+direction"))
            elif matched_mirror[key] > 0:
                matched_mirror[key] -= 1
                dupes.append((t, target_name, "mirror side of existing transfer"))
            elif planned_xfers[key] > 0:
                planned_xfers[key] -= 1
                dupes.append((t, target_name, "other side already queued this run"))
            else:
                planned_xfers[key] += 1
                new_xfers.append({
                    "FromBankAccount": {"AccountID": frm},
                    "ToBankAccount": {"AccountID": to},
                    "Amount": amt,
                    "Date": day,
                    "Reference": ref,
                })
    say()

    # --- Report ---
    def block(title, rows, cap=15):
        if rows:
            say(f"{title} ({len(rows)}):")
            for r in rows[:cap]:
                say(f"  {r}")
            if len(rows) > cap:
                say(f"  ... and {len(rows) - cap} more")
            say()

    verb = "Creating" if args.apply else "WOULD CREATE"
    by_code = defaultdict(lambda: [0, 0.0])
    for bt in new_txns:
        li = bt["LineItems"][0]
        sign = -1 if bt["Type"] == "SPEND" else 1
        by_code[li["AccountCode"]][0] += 1
        by_code[li["AccountCode"]][1] += sign * li["UnitAmount"]
    say(f"{verb} {len(new_txns)} Spend/Receive Money:")
    for code in sorted(by_code):
        n, total = by_code[code]
        say(f"  {code} {codes[code]:<38} {n:4d} txns  {total:12,.2f}")
    say()

    by_pair = defaultdict(lambda: [0, 0.0])
    for x in new_xfers:
        pair = f"{id_to_name[x['FromBankAccount']['AccountID']]} -> {id_to_name[x['ToBankAccount']['AccountID']]}"
        by_pair[pair][0] += 1
        by_pair[pair][1] += x["Amount"]
    say(f"{verb} {len(new_xfers)} Bank Transfers:")
    for pair in sorted(by_pair):
        n, total = by_pair[pair]
        say(f"  {pair:<70} {n:4d}  {total:12,.2f}")
    say()

    block("SKIPPED", [f"{t['postedAt'][:10]} {t['amount']:>10.2f}  {merchant_name(t)} -- {why}"
                      for t, why in skipped], cap=5)
    block("ALREADY IN XERO (skipped as duplicates)",
          [f"{t['postedAt'][:10]} {t['amount']:>10.2f}  {name} -- {why}" for t, name, why in dupes])
    block("UNMATCHED -- not posted, add to merchants.yaml to include",
          [f"{t['postedAt'][:10]} {t['amount']:>10.2f}  {name} | {(t.get('bankDescription') or '')[:60]}"
           for t, name in unmatched], cap=100)

    failures = 0
    if args.apply:
        for i in range(0, len(new_txns), BATCH_SIZE):
            chunk = new_txns[i:i + BATCH_SIZE]
            result = xero.put("BankTransactions", {"BankTransactions": chunk})
            for item in result.get("BankTransactions", []):
                errs = item.get("ValidationErrors")
                if errs:
                    failures += 1
                    say(f"FAILED {item.get('Reference')}: {errs[0]['Message']}")
            say(f"  posted BankTransactions {i + 1}-{i + len(chunk)}")

        for i in range(0, len(new_xfers), BATCH_SIZE):
            chunk = new_xfers[i:i + BATCH_SIZE]
            result = xero.put("BankTransfers", {"BankTransfers": chunk})
            for item in result.get("BankTransfers", []):
                errs = item.get("ValidationErrors")
                if errs:
                    failures += 1
                    say(f"FAILED transfer {item.get('Date')} {item.get('Amount')}: {errs[0]['Message']}")
            say(f"  posted BankTransfers {i + 1}-{i + len(chunk)}")

        say(f"\nDone: {len(new_txns) + len(new_xfers) - failures} created, {failures} failed.")
    else:
        say("Dry run only. Re-run with --apply to create the transactions above.")

    if args.apply and (unmatched or failures):
        try:
            if unmatched:
                filed = file_todoist_task(
                    cfg,
                    "Map unmatched Mercury merchants in merchants.yaml",
                    "The weekly pre-code run found transactions it could not "
                    "code (details in the report email). Add patterns to "
                    "finance-tools/merchants.yaml; the next weekly run "
                    "(45-day lookback) posts them automatically.\n\n"
                    + "\n".join(
                        f"{t['postedAt'][:10]}  {t['amount']:>10.2f}  {name}"
                        for t, name in unmatched[:20]),
                )
                if filed:
                    say("Filed Todoist task: unmatched merchants.")
            if failures:
                filed = file_todoist_task(
                    cfg,
                    "Investigate Mercury pre-code failures",
                    f"{failures} transaction(s) failed to post in the weekly "
                    "run — see the report email and "
                    "~/Library/Logs/mercury-precode.log.",
                )
                if filed:
                    say("Filed Todoist task: failures.")
        except Exception as e:
            say(f"Todoist task filing failed (non-fatal): {e}")

    if args.email:
        created = len(new_txns) + len(new_xfers)
        if created or unmatched or failures:
            bits = []
            if args.apply and created:
                bits.append(f"{created} created")
            elif created:
                bits.append(f"{created} pending (dry run)")
            if unmatched:
                bits.append(f"{len(unmatched)} unmatched")
            if failures:
                bits.append(f"{failures} FAILED")
            send_report(f"Mercury->Xero pre-code: {', '.join(bits)}", report)
            say("Report emailed.")
        else:
            say("Nothing to report -- no email sent.")

    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--days", type=int, help="trailing window: start = today - N days")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--apply", action="store_true", help="create transactions (default: dry run)")
    ap.add_argument("--email", action="store_true",
                    help="email the report via alert_email.py when action is needed")
    ap.add_argument("--config", default=HERE / "merchants.yaml", type=Path)
    args = ap.parse_args()
    if args.days:
        args.start = (date.today() - timedelta(days=args.days)).isoformat()

    try:
        return run(args)
    except SystemExit:
        raise
    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        if args.email:
            try:
                send_report("FAILED: Mercury->Xero pre-code run crashed", err.splitlines())
            except Exception as mail_err:
                print(f"alert email also failed: {mail_err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
