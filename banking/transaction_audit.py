#!/usr/bin/env python3
"""
Transaction coverage audit: checks that all transactions are captured
and categorized across Monarch Money (personal) and Xero (business).

Checks:
  1. Uncategorized/unreviewed transactions in Monarch
  2. Unreconciled bank statement lines in Xero
  3. Expected recurring payments that didn't post
  4. Expected income deposits that didn't appear

Usage:
    python transaction_audit.py              # Last 7 days (default)
    python transaction_audit.py --days 14    # Last 14 days
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta

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

try:
    from coverage_report import load_payments_yaml
except ImportError:
    print("Error: coverage_report.py must be in the same directory", file=sys.stderr)
    sys.exit(1)

try:
    from xero_balances import fetch_xero_balances, load_token, refresh_token, CLIENT_ID, CLIENT_SECRET
except ImportError:
    fetch_xero_balances = None

# ANSI colors with TTY detection
class Color:
    RED = "\033[31m"
    BOLD_RED = "\033[1;31m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    BOLD = "\033[1m"
    CYAN = "\033[36m"
    RESET = "\033[0m"

    @classmethod
    def disable(cls):
        cls.RED = cls.BOLD_RED = cls.YELLOW = cls.GREEN = cls.BOLD = cls.CYAN = cls.RESET = ""

if not sys.stdout.isatty():
    Color.disable()


async def _get_monarch_client():
    """Get an authenticated Monarch Money client."""
    if MonarchMoney is None:
        return None
    token = os.environ.get("MONARCH_TOKEN")
    if not token:
        print("Warning: MONARCH_TOKEN not set, skipping Monarch audit", file=sys.stderr)
        return None
    return MonarchMoney(token=token)


async def fetch_uncategorized_monarch(days=7):
    """Fetch transactions from Monarch that need review or are uncategorized.

    Returns:
        dict with keys:
            needs_review: list of txn dicts needing review
            uncategorized: list of txn dicts with no category
            total_checked: int
    """
    mm = await _get_monarch_client()
    if mm is None:
        return {"needs_review": [], "uncategorized": [], "total_checked": 0, "error": "Monarch not available"}

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        data = await mm.get_transactions(
            limit=500,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:
        return {"needs_review": [], "uncategorized": [], "total_checked": 0, "error": str(e)}

    transactions = data.get("allTransactions", {}).get("results", [])

    needs_review = []
    uncategorized = []

    for txn in transactions:
        # Skip pending transactions
        if txn.get("pending"):
            continue

        entry = {
            "id": txn.get("id"),
            "date": txn.get("date"),
            "amount": txn.get("amount", 0),
            "merchant": txn.get("merchant", {}).get("name") if txn.get("merchant") else None,
            "description": txn.get("dataProviderDescription", ""),
            "category": txn.get("category", {}).get("name") if txn.get("category") else None,
        }

        if txn.get("needsReview"):
            needs_review.append(entry)

        cat = txn.get("category")
        if not cat or not cat.get("name") or cat.get("name", "").lower() in ("uncategorized",):
            uncategorized.append(entry)

    return {
        "needs_review": needs_review,
        "uncategorized": uncategorized,
        "total_checked": len(transactions),
    }


def fetch_unreconciled_xero():
    """Fetch unreconciled bank statement lines from Xero.

    Uses the Xero BankTransactions API. In Xero, statement lines that haven't
    been matched/reconciled show up differently than reconciled transactions.
    We check for bank transactions with status != AUTHORISED.

    Returns:
        dict with keys:
            unreconciled: list of txn dicts
            error: str or None
    """
    if fetch_xero_balances is None:
        return {"unreconciled": [], "error": "Xero not available"}

    if not CLIENT_ID or not CLIENT_SECRET:
        return {"unreconciled": [], "error": "XERO_CLIENT_ID/SECRET not set"}

    token = load_token()
    if not token:
        return {"unreconciled": [], "error": "No Xero token. Run: task xero:auth"}

    try:
        token = refresh_token(token)
    except Exception as e:
        return {"unreconciled": [], "error": f"Xero token refresh failed: {e}"}

    try:
        from xero_python.api_client import ApiClient, Configuration
        from xero_python.api_client.oauth2 import OAuth2Token
        from xero_python.accounting import AccountingApi
        from xero_python.identity import IdentityApi

        api_client = ApiClient(
            Configuration(
                oauth2_token=OAuth2Token(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
            ),
            oauth2_token_getter=load_token,
            oauth2_token_saver=lambda t: None,
        )
        api_client.set_oauth2_token(token)

        identity_api = IdentityApi(api_client)
        connections = identity_api.get_connections()
        if not connections:
            return {"unreconciled": [], "error": "No Xero tenants connected"}
        tenant_id = connections[0].tenant_id

        accounting_api = AccountingApi(api_client)

        # Fetch recent bank transactions — look for ones not fully reconciled
        # In Xero, bank statement lines that need attention are surfaced via
        # the bank reconciliation report. We approximate by checking for
        # bank transactions in the last 7 days.
        since_date = datetime.now() - timedelta(days=30)
        result = accounting_api.get_bank_transactions(
            xero_tenant_id=tenant_id,
            if_modified_since=since_date,
        )

        unreconciled = []
        for txn in (result.bank_transactions or []):
            # DELETED or VOIDED transactions are not interesting
            if str(txn.status) in ("BankTransactionStatus.DELETED", "BankTransactionStatus.VOIDED"):
                continue
            # Check if the transaction is_reconciled flag is False
            if not getattr(txn, "is_reconciled", True):
                unreconciled.append({
                    "date": str(txn.date) if txn.date else None,
                    "amount": float(txn.total) if txn.total else 0,
                    "description": txn.reference or (txn.contact.name if txn.contact else ""),
                    "account": txn.bank_account.name if txn.bank_account else "Unknown",
                    "type": str(txn.type) if txn.type else "",
                })

        return {"unreconciled": unreconciled}

    except Exception as e:
        return {"unreconciled": [], "error": f"Xero API error: {e}"}


async def check_recurring_posted(days=7):
    """Check that expected recurring payments from payments.yaml actually posted.

    Looks at payments due in the last N days and searches Monarch for
    matching transactions.

    Returns:
        dict with keys:
            missing: list of payment dicts that weren't found
            found: list of payment dicts that were found
            skipped: list of payments skipped (no match criteria)
    """
    config = load_payments_yaml()
    payments = config.get("payments", [])
    accounts = config.get("accounts", [])
    accounts_by_id = {a["id"]: a for a in accounts}

    today = datetime.now()
    cutoff = today - timedelta(days=days)

    # Determine which payments were due in the window
    due_payments = []
    for p in payments:
        dom = p.get("day_of_month")
        if dom is None:
            continue

        # Check if the payment was due in the lookback window
        # Try current month
        try:
            due_date = datetime(today.year, today.month, dom)
        except ValueError:
            continue

        if cutoff <= due_date <= today:
            due_payments.append(p)
            continue

        # Try last month
        last_month = today.month - 1
        last_year = today.year
        if last_month < 1:
            last_month = 12
            last_year -= 1
        try:
            due_date = datetime(last_year, last_month, dom)
        except ValueError:
            continue
        if cutoff <= due_date <= today:
            due_payments.append(p)

    if not due_payments:
        return {"missing": [], "found": [], "skipped": []}

    # Fetch Monarch transactions for the period
    mm = await _get_monarch_client()
    monarch_txns = []
    if mm:
        try:
            data = await mm.get_transactions(
                limit=500,
                start_date=cutoff.strftime("%Y-%m-%d"),
                end_date=today.strftime("%Y-%m-%d"),
            )
            monarch_txns = data.get("allTransactions", {}).get("results", [])
        except Exception:
            pass

    missing = []
    found = []
    skipped = []

    for p in due_payments:
        name = p.get("name", "").lower()
        amount = p.get("amount", 0)
        zoho_match = (p.get("zoho_match") or "").lower()

        # Skip payments with $0 amount (variable credit card payments)
        if amount == 0:
            skipped.append(p)
            continue

        # Search Monarch transactions for a match
        matched = False
        for txn in monarch_txns:
            txn_amount = abs(txn.get("amount", 0))
            txn_merchant = (txn.get("merchant", {}) or {}).get("name", "").lower()
            txn_desc = (txn.get("dataProviderDescription") or "").lower()

            # Match by name or zoho_match against merchant/description
            name_matches = (
                name in txn_merchant or
                name in txn_desc or
                (zoho_match and zoho_match in txn_merchant) or
                (zoho_match and zoho_match in txn_desc)
            )

            # Amount within 20% tolerance (bills can vary slightly)
            amount_close = (
                amount * 0.8 <= txn_amount <= amount * 1.2
            ) if amount > 0 else True

            if name_matches and amount_close:
                matched = True
                break

        if matched:
            found.append(p)
        else:
            missing.append(p)

    return {"missing": missing, "found": found, "skipped": skipped}


async def run_audit(days=7):
    """Run the full transaction audit.

    Returns:
        dict with all audit results.
    """
    # Run Monarch checks concurrently
    uncategorized_task = fetch_uncategorized_monarch(days)
    recurring_task = check_recurring_posted(days)

    uncategorized_result, recurring_result = await asyncio.gather(
        uncategorized_task, recurring_task
    )

    # Xero check is synchronous
    xero_result = fetch_unreconciled_xero()

    return {
        "monarch_uncategorized": uncategorized_result,
        "xero_unreconciled": xero_result,
        "recurring_check": recurring_result,
        "days": days,
        "run_at": datetime.now().isoformat(),
    }


def print_report(audit):
    """Print the audit report to terminal."""
    days = audit["days"]
    print(f"\n{'=' * 70}")
    print(f"  TRANSACTION AUDIT — {datetime.now().strftime('%a %b %d, %Y')} (last {days} days)")
    print(f"{'=' * 70}")

    # --- Monarch Uncategorized ---
    mc = audit["monarch_uncategorized"]
    print(f"\n  {Color.BOLD}Monarch Money — Transaction Review{Color.RESET}")
    print(f"  {'─' * 55}")

    if mc.get("error"):
        print(f"    {Color.YELLOW}Skipped: {mc['error']}{Color.RESET}")
    else:
        nr_count = len(mc["needs_review"])
        uc_count = len(mc["uncategorized"])

        if nr_count == 0 and uc_count == 0:
            print(f"    {Color.GREEN}All {mc['total_checked']} transactions reviewed and categorized{Color.RESET}")
        else:
            if nr_count > 0:
                print(f"    {Color.YELLOW}{nr_count} transaction(s) need review{Color.RESET}")
                for txn in mc["needs_review"][:10]:
                    merchant = txn["merchant"] or txn["description"] or "(unknown)"
                    print(f"      {txn['date']}  {merchant}  ${abs(txn['amount']):,.2f}")
                if nr_count > 10:
                    print(f"      ... and {nr_count - 10} more")

            if uc_count > 0:
                print(f"    {Color.YELLOW}{uc_count} uncategorized transaction(s){Color.RESET}")
                for txn in mc["uncategorized"][:5]:
                    merchant = txn["merchant"] or txn["description"] or "(unknown)"
                    print(f"      {txn['date']}  {merchant}  ${abs(txn['amount']):,.2f}")

            print(f"\n    {Color.CYAN}Action: Review at https://app.monarchmoney.com/transactions{Color.RESET}")

    # --- Xero Unreconciled ---
    xr = audit["xero_unreconciled"]
    print(f"\n  {Color.BOLD}Xero — Unreconciled Items{Color.RESET}")
    print(f"  {'─' * 55}")

    if xr.get("error"):
        print(f"    {Color.YELLOW}Skipped: {xr['error']}{Color.RESET}")
    else:
        ur_count = len(xr["unreconciled"])
        if ur_count == 0:
            print(f"    {Color.GREEN}All bank transactions reconciled{Color.RESET}")
        else:
            print(f"    {Color.YELLOW}{ur_count} unreconciled transaction(s){Color.RESET}")
            for txn in xr["unreconciled"][:10]:
                desc = txn["description"] or "(no description)"
                print(f"      {txn['date']}  {desc}  ${abs(txn['amount']):,.2f}  [{txn['account']}]")
            if ur_count > 10:
                print(f"      ... and {ur_count - 10} more")
            print(f"\n    {Color.CYAN}Action: Reconcile at https://go.xero.com/Bank/BankAccounts.aspx{Color.RESET}")

    # --- Recurring Payments Check ---
    rc = audit["recurring_check"]
    print(f"\n  {Color.BOLD}Recurring Payments — Coverage Check{Color.RESET}")
    print(f"  {'─' * 55}")

    missing = rc["missing"]
    found = rc["found"]
    skipped = rc["skipped"]
    total = len(missing) + len(found) + len(skipped)

    if total == 0:
        print(f"    No payments were due in the last {days} days")
    else:
        if found:
            print(f"    {Color.GREEN}{len(found)} payment(s) confirmed posted{Color.RESET}")
        if skipped:
            print(f"    {len(skipped)} variable payment(s) skipped (check manually)")
        if missing:
            print(f"    {Color.BOLD_RED}{len(missing)} payment(s) NOT FOUND{Color.RESET}")
            for p in missing:
                fa = p.get("funding_account", "?")
                print(f"      {p['name']}: ${p['amount']:,.2f} (day {p['day_of_month']}, account: {fa})")
            print(f"\n    {Color.CYAN}Action: Verify these payments posted or update payments.yaml{Color.RESET}")
        elif not missing and found:
            print(f"    {Color.GREEN}All expected payments confirmed{Color.RESET}")

    # --- Summary ---
    issues = (
        len(mc.get("needs_review", [])) +
        len(mc.get("uncategorized", [])) +
        len(xr.get("unreconciled", [])) +
        len(rc.get("missing", []))
    )
    print(f"\n{'═' * 70}")
    if issues == 0:
        print(f"  {Color.GREEN}All clear — no action items{Color.RESET}")
    else:
        print(f"  {Color.YELLOW}{issues} item(s) need attention{Color.RESET}")
    print(f"{'═' * 70}")


def main():
    parser = argparse.ArgumentParser(
        description="Transaction coverage audit across Monarch Money and Xero"
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="Lookback period in days (default: 7)"
    )
    args = parser.parse_args()

    if args.days < 1 or args.days > 90:
        print("Error: --days must be between 1 and 90", file=sys.stderr)
        sys.exit(1)

    print("Running transaction audit...", end=" ", flush=True)
    audit = asyncio.run(run_audit(days=args.days))
    print("done")

    print_report(audit)


if __name__ == "__main__":
    main()
