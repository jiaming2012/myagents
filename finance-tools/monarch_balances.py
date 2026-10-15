#!/usr/bin/env python3
"""
Fetch and display account balances from Monarch Money.

First run requires interactive login (email + password + MFA if enabled).
Session is saved to .mm/mm_session.pickle and is used by every finance
script; MONARCH_TOKEN in .env is only a fallback when no session exists.

Usage:
    python monarch_balances.py              # All accounts
    python monarch_balances.py --login      # Force re-login
    python monarch_balances.py --type credit  # Filter by type (depository, credit, investment, loan, other)
"""

import argparse
import asyncio
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from monarchmoney import MonarchMoney
    from monarchmoney.monarchmoney import MonarchMoneyEndpoints
except ImportError:
    print("Error: 'monarchmoney' package is required. Install with: pip install monarchmoney", file=sys.stderr)
    sys.exit(1)

# Library still uses old URL; Monarch migrated to api.monarch.com
MonarchMoneyEndpoints.BASE_URL = "https://api.monarch.com"

from monarch_client import get_monarch_client, is_auth_error, auth_error_message


async def fetch_and_display(force_login=False, account_type=None):
    """Fetch accounts and display balances."""
    mm = await get_monarch_client(force_login)
    if mm is None:
        sys.exit(1)

    try:
        data = await mm.get_accounts()
    except Exception as e:
        if is_auth_error(e):
            print(f"Error: {auth_error_message()}", file=sys.stderr)
            sys.exit(1)
        raise
    accounts = data.get("accounts", [])

    if account_type:
        accounts = [a for a in accounts if a.get("type", {}).get("name", "").lower() == account_type.lower()]

    if not accounts:
        print("\nNo accounts found.")
        return

    # Group by account type
    grouped = {}
    for acct in accounts:
        acct_type = acct.get("type", {}).get("name", "Other")
        grouped.setdefault(acct_type, []).append(acct)

    print(f"\n{'=' * 60}")
    print(f"  Monarch Money - Account Balances")
    print(f"{'=' * 60}")

    net_worth = 0
    for acct_type, accts in sorted(grouped.items()):
        print(f"\n  {acct_type}")
        print(f"  {'-' * 50}")
        for acct in sorted(accts, key=lambda a: a.get("displayName", "")):
            name = acct.get("displayName", "(unnamed)")
            balance = acct.get("currentBalance", 0)
            institution = acct.get("institution", {}).get("name", "")

            net_worth += balance

            # Format balance with color hint for negative
            bal_str = f"${abs(balance):,.2f}"
            if balance < 0:
                bal_str = f"-{bal_str}"

            label = f"{name}"
            if institution:
                label = f"{name} ({institution})"

            print(f"    {label:<40} {bal_str:>12}")

    print(f"\n  {'=' * 50}")
    nw_str = f"${abs(net_worth):,.2f}"
    if net_worth < 0:
        nw_str = f"-{nw_str}"
    print(f"    {'Net Worth':<40} {nw_str:>12}")
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch and display account balances from Monarch Money.",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Force re-login (ignore saved session)",
    )
    parser.add_argument(
        "--type",
        dest="account_type",
        help="Filter by account type (e.g., depository, credit, investment, loan)",
    )
    args = parser.parse_args()

    asyncio.run(fetch_and_display(force_login=args.login, account_type=args.account_type))


if __name__ == "__main__":
    main()
