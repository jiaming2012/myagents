#!/usr/bin/env python3
"""
Transaction coverage audit: checks that all transactions are captured
and categorized across Monarch Money (personal) and Zoho Books (business).

Checks:
  1. Uncategorized/unreviewed transactions in Monarch
  2. Uncategorized bank-feed lines in Zoho Books
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
    from monarch_client import get_monarch_client
except ImportError:
    MonarchMoney = None

try:
    from coverage_report import load_payments_yaml
except ImportError:
    print("Error: coverage_report.py must be in the same directory", file=sys.stderr)
    sys.exit(1)

try:
    from zoho_balances import uncategorized_lines as zoho_uncategorized_lines
    from sync_status import fetch_zoho_accounts
except ImportError:
    fetch_zoho_accounts = None

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
    """Get an authenticated Monarch Money client (shared precedence: session > env)."""
    if MonarchMoney is None:
        return None
    mm = await get_monarch_client()
    if mm is None:
        print("Warning: skipping Monarch audit", file=sys.stderr)
    return mm


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


def fetch_uncategorized_zoho():
    """Bank-feed lines in Zoho Books that nobody has categorized or matched yet.

    Returns {"uncategorized": [{"account", "count"}], "error": str|None}. The
    fix is the weekly recon (task zoho:recon:apply for Mercury, task zoho:feed:apply
    for the feed-only banks) — not a click in a web UI.
    """
    if fetch_zoho_accounts is None:
        return {"uncategorized": [], "error": "Zoho Books not available (sync_status/zoho_balances missing)"}
    try:
        return {"uncategorized": zoho_uncategorized_lines(fetch_zoho_accounts())}
    except SystemExit as e:
        return {"uncategorized": [], "error": f"Zoho Books exited with code {e.code}"}
    except Exception as e:  # noqa: BLE001 - an outage is reported, never raised
        return {"uncategorized": [], "error": f"Zoho Books API error: {str(e)[:160]}"}


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

    zoho_result = await asyncio.to_thread(fetch_uncategorized_zoho)

    return {
        "monarch_uncategorized": uncategorized_result,
        "zoho_uncategorized": zoho_result,
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

    # --- Zoho Books uncategorized ---
    zr = audit["zoho_uncategorized"]
    print(f"\n  {Color.BOLD}Zoho Books — Uncategorized Feed Lines{Color.RESET}")
    print(f"  {'─' * 55}")

    if zr.get("error"):
        print(f"    {Color.YELLOW}Skipped: {zr['error']}{Color.RESET}")
    else:
        total = sum(x["count"] for x in zr["uncategorized"])
        if total == 0:
            print(f"    {Color.GREEN}Every feed line is booked{Color.RESET}")
        else:
            print(f"    {Color.YELLOW}{total} uncategorized line(s){Color.RESET}")
            for x in zr["uncategorized"]:
                print(f"      {x['count']:>4}  {x['account']}")
            print(f"\n    {Color.CYAN}Action: task zoho:recon:apply (Mercury) / task zoho:feed:apply (other banks){Color.RESET}")

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
        description="Transaction coverage audit across Monarch Money and Zoho Books"
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
