#!/usr/bin/env python3
"""
Weekly coverage report: checks if funding accounts have enough
to cover upcoming payments for 7, 14, and 30 days.

Pulls balances from Xero (business) and Monarch Money (personal),
matches against payments.yaml, and flags shortfalls.

Usage:
    python coverage_report.py           # Full report
    python coverage_report.py --days 7  # Only show 7-day window
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

try:
    import requests
except ImportError:
    print("Error: 'requests' package is required.", file=sys.stderr)
    sys.exit(1)

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
    from xero_balances import fetch_xero_balances
except ImportError:
    fetch_xero_balances = None

PAYMENTS_FILE = Path(__file__).parent / "payments.yaml"


def load_payments_yaml():
    """Load the payment registry."""
    with open(PAYMENTS_FILE) as f:
        return yaml.safe_load(f)


def get_upcoming_payments(payments, days):
    """Get payments due within the next N days."""
    today = datetime.now()
    current_day = today.day
    current_month = today.month
    current_year = today.year

    upcoming = []
    for p in payments:
        dom = p.get("day_of_month")
        if dom is None:
            continue

        # Calculate next due date
        # Try this month first, then next month
        try:
            due = datetime(current_year, current_month, dom)
        except ValueError:
            # Day doesn't exist in this month (e.g., 31st in April)
            # Use last day of month
            if current_month == 12:
                due = datetime(current_year + 1, 1, dom)
            else:
                due = datetime(current_year, current_month + 1, dom)

        if due < today:
            # Already passed this month, use next month
            if current_month == 12:
                due = datetime(current_year + 1, 1, dom)
            else:
                try:
                    due = datetime(current_year, current_month + 1, dom)
                except ValueError:
                    continue

        if due <= today + timedelta(days=days):
            upcoming.append({
                **p,
                "due_date": due,
            })

    return upcoming


async def fetch_monarch_balances():
    """Fetch all account balances from Monarch Money."""
    if MonarchMoney is None:
        print("Warning: monarchmoney not installed, skipping balance lookup", file=sys.stderr)
        return {}

    token = os.environ.get("MONARCH_TOKEN")
    if not token:
        print("Warning: MONARCH_TOKEN not set, skipping balance lookup", file=sys.stderr)
        return {}

    mm = MonarchMoney(token=token)
    data = await mm.get_accounts()

    balances = {}
    for acct in data.get("accounts", []):
        name = acct.get("displayName", "")
        balances[name] = {
            "balance": acct.get("currentBalance", 0),
            "type": acct.get("type", {}).get("name", ""),
        }
    return balances


def resolve_balance(account, monarch_balances, xero_balances):
    """Get the current balance for an account from available sources."""
    # Try Xero first (for business accounts with xero_account_id)
    xero_id = account.get("xero_account_id")
    if xero_id and xero_balances is not None:
        if xero_id in xero_balances:
            return xero_balances[xero_id]["balance"], "xero"
        # Account is configured for Xero but not in report — zero balance, not missing
        return 0.0, "xero"

    # Try Monarch
    monarch_match = account.get("monarch_match")
    if monarch_match and monarch_balances is not None:
        if monarch_match in monarch_balances:
            return monarch_balances[monarch_match]["balance"], "monarch"
        return 0.0, "monarch"

    # Partial match on Monarch (displayName contains last4)
    last4 = account.get("last4")
    if last4:
        for name, data in monarch_balances.items():
            if last4 in name:
                return data["balance"], "monarch"

    return None, None


def print_report(config, monarch_balances, xero_balances, windows):
    """Print the coverage report."""
    accounts_by_id = {a["id"]: a for a in config.get("accounts", [])}
    payments = config.get("payments", [])
    rules = config.get("transfer_rules", {})
    min_days = rules.get("minimum_coverage_days", 7)
    pref_days = rules.get("preferred_coverage_days", 14)

    # Group payments by funding account
    payments_by_account = {}
    unassigned = []
    for p in payments:
        fa = p.get("funding_account")
        if fa:
            payments_by_account.setdefault(fa, []).append(p)
        else:
            unassigned.append(p)

    print(f"\n{'=' * 70}")
    print(f"  COVERAGE REPORT — {datetime.now().strftime('%a %b %d, %Y')}")
    print(f"{'=' * 70}")

    alerts = []

    for window in windows:
        print(f"\n  {'─' * 60}")
        print(f"  Next {window} Days")
        print(f"  {'─' * 60}")

        for acct_id, acct_payments in sorted(payments_by_account.items()):
            account = accounts_by_id.get(acct_id)
            if not account:
                continue

            upcoming = get_upcoming_payments(acct_payments, window)
            if not upcoming:
                continue

            total_due = sum(p.get("amount", 0) for p in upcoming)
            balance, source = resolve_balance(account, monarch_balances, xero_balances)

            acct_label = f"{account['name']}"
            if account.get("last4"):
                acct_label += f" (••{account['last4']})"

            print(f"\n    {acct_label}")
            if balance is not None:
                surplus = balance - total_due
                status = "OK" if surplus >= 0 else "SHORTFALL"

                if account.get("type") == "credit":
                    # Credit cards: balance is what you owe, payments come from linked bank
                    print(f"      Balance:  ${abs(balance):,.2f} owed")
                else:
                    print(f"      Balance:  ${balance:,.2f}")

                print(f"      Due:      ${total_due:,.2f} ({len(upcoming)} payment{'s' if len(upcoming) != 1 else ''})")

                if account.get("type") != "credit":
                    if surplus >= 0:
                        print(f"      Surplus:  ${surplus:,.2f}")
                    else:
                        print(f"      SHORT:    -${abs(surplus):,.2f}  *** ALERT ***")
                        alerts.append({
                            "account": acct_label,
                            "shortfall": abs(surplus),
                            "window": window,
                        })
            else:
                print(f"      Balance:  unknown (no Xero/Monarch match)")
                print(f"      Due:      ${total_due:,.2f} ({len(upcoming)} payment{'s' if len(upcoming) != 1 else ''})")

            for p in upcoming:
                due_str = p["due_date"].strftime("%b %d")
                auto = "auto" if p.get("autopay") else "manual" if p.get("autopay") is False else "???"
                print(f"        - {p['name']}: ${p['amount']:,.2f} (due {due_str}, {auto})")

    # Unassigned payments
    if unassigned:
        print(f"\n  {'─' * 60}")
        print(f"  UNASSIGNED PAYMENTS (no funding account)")
        print(f"  {'─' * 60}")
        for p in unassigned:
            auto = "auto" if p.get("autopay") else "manual" if p.get("autopay") is False else "???"
            notes = p.get("notes", "")
            print(f"    - {p['name']}: ${p.get('amount', 0):,.2f} (day {p.get('day_of_month', '?')}, {auto})")
            if notes:
                print(f"      {notes}")

    # Summary
    print(f"\n{'=' * 70}")
    if alerts:
        print(f"  *** {len(alerts)} SHORTFALL{'S' if len(alerts) != 1 else ''} DETECTED ***")
        print()
        for a in alerts:
            print(f"    {a['account']}: -${a['shortfall']:,.2f} within {a['window']} days")
        print()
        print(f"  Action needed: transfer funds to cover shortfalls")
    else:
        print(f"  All funding accounts have sufficient coverage")

    if unassigned:
        print(f"  {len(unassigned)} payment(s) still need funding accounts assigned")
    print(f"{'=' * 70}")


async def main():
    parser = argparse.ArgumentParser(description="Weekly payment coverage report")
    parser.add_argument("--days", type=int, help="Show only one window (e.g., 7, 14, 30)")
    args = parser.parse_args()

    config = load_payments_yaml()

    print("Fetching balances...", end=" ", flush=True)
    monarch_balances = await fetch_monarch_balances()
    xero_balances = fetch_xero_balances() if fetch_xero_balances else {}
    print("OK")

    if args.days:
        windows = [args.days]
    else:
        windows = [7, 14, 30]

    print_report(config, monarch_balances, xero_balances, windows)


if __name__ == "__main__":
    asyncio.run(main())
