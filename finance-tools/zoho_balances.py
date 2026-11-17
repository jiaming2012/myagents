#!/usr/bin/env python3
"""Business balances from Zoho Books, in the shape the forecast engine consumes.

Zoho Books is the business ledger (it replaced Xero on 2026-11-17). The registry
(payments.yaml) links a business account to its Zoho bank account through
`zoho_match`, the exact Zoho account name; this module turns the account list
that sync_status.py already fetches into {zoho name: {balance, type, updated, ...}}
so coverage_report.resolve_balance can vouch for business accounts the same way
Monarch vouches for personal ones.

Sign convention follows Monarch: a liability (credit card) is negative. Zoho
reports a card's balance positive = owed, so it is negated here.

Usage:
    python zoho_balances.py        # print every live Zoho bank balance
"""
import sys

from sync_status import fetch_zoho_accounts, zoho_is_parked


def balances_from_accounts(accounts):
    """Slim Zoho accounts (sync_status.slim_zoho_account) -> {name: entry}.

    entry = {"balance": signed book balance, "type": Zoho account_type,
             "updated": feeds_last_refresh_date or None, "uncategorized": int,
             "active": bool}. Parked (inactive) accounts are left out: nobody should
    fund a payment from a closed account.
    """
    out = {}
    for a in accounts or []:
        if zoho_is_parked(a):
            continue
        bal = float(a.get("book_balance") or 0.0)
        if a.get("type") == "credit_card" and bal:
            bal = -bal  # owed -> negative; a zero stays +0.0
        out[a["name"]] = {
            "balance": bal,
            "type": a.get("type", ""),
            "updated": a.get("last_refresh") or None,
            "uncategorized": int(a.get("uncategorized") or 0),
            "active": bool(a.get("active", True)),
        }
    return out


def uncategorized_lines(accounts):
    """[{account, count}] for live accounts whose bank feed still has unbooked lines."""
    return [{"account": a["name"], "count": int(a.get("uncategorized") or 0)}
            for a in accounts or []
            if not zoho_is_parked(a) and int(a.get("uncategorized") or 0) > 0]


def fetch_zoho_balances():
    """Live fetch (one Zoho API call plus one per statement-maintained account)."""
    return balances_from_accounts(fetch_zoho_accounts())


def fetch_with_status():
    """(balances, status): status is "ok" or the reason the source is unavailable.
    An unavailable source yields {} so callers report "unknown", never $0."""
    try:
        return fetch_zoho_balances(), "ok"
    except SystemExit as e:
        return {}, f"exited with code {e.code}"
    except Exception as e:  # noqa: BLE001 - any outage is reported, never raised
        return {}, f"{type(e).__name__}: {str(e)[:160]}"


def main():
    balances, status = fetch_with_status()
    if status != "ok":
        print(f"Zoho Books unavailable: {status}", file=sys.stderr)
        return 1
    for name, e in sorted(balances.items()):
        flag = f"  ({e['uncategorized']} uncategorized)" if e["uncategorized"] else ""
        print(f"{name:60} {e['balance']:>12,.2f}  {e['type']}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
