#!/usr/bin/env python3
"""
Payment forecast engine: projects per-account balances forward by
subtracting upcoming scheduled payments from live balances.

Detects shortfalls at two severity levels:
  - ERROR: projected balance < 0 (overdraft risk)
  - WARNING: projected balance >= 0 but < account min_balance threshold

Usage:
    python payment_forecast.py              # 30-day forecast (default)
    python payment_forecast.py --days 7     # 7-day forecast
    python payment_forecast.py --timeline   # Chronological view
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta

try:
    from tabulate import tabulate as tabulate_fn
except ImportError:
    print("Error: 'tabulate' package is required. Run: pip install tabulate", file=sys.stderr)
    sys.exit(1)

try:
    from dateutil.rrule import rrule, MONTHLY
except ImportError:
    print("Error: 'python-dateutil' package is required. Run: pip install python-dateutil", file=sys.stderr)
    sys.exit(1)

try:
    from coverage_report import fetch_monarch_balances, resolve_balance, load_payments_yaml
except ImportError:
    print("Error: coverage_report.py must be in the same directory", file=sys.stderr)
    sys.exit(1)

try:
    from xero_balances import fetch_xero_balances
except ImportError:
    fetch_xero_balances = None

try:
    from alert_email import (
        get_smtp_config, get_alert_recipient, check_alert_thresholds,
        build_alert_html, build_summary_html, compute_alert_hash,
        should_send_alert, record_alert_sent, check_and_record_alerts,
        send_email, export_preview,
    )
    _has_alert_email = True
except ImportError:
    _has_alert_email = False

# ANSI color codes with TTY detection (RESEARCH Pattern 4)
class Color:
    RED = "\033[31m"
    BOLD_RED = "\033[1;31m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    @classmethod
    def disable(cls):
        cls.RED = cls.BOLD_RED = cls.YELLOW = cls.GREEN = cls.BOLD = cls.RESET = ""


if not sys.stdout.isatty():
    Color.disable()

# Exit codes per D-12
EXIT_OK = 0
EXIT_WARNING = 1
EXIT_ERROR = 2


def validate_funding_accounts(payments):
    """Validate that all payments have a funding_account assigned.

    Args:
        payments: List of payment dicts from payments.yaml.

    Returns:
        List of payment dicts where funding_account is None or empty.
        Empty list means all payments are valid.
    """
    missing = []
    for p in payments:
        fa = p.get("funding_account")
        if not fa:  # None or empty string
            missing.append(p)
    return missing


def collect_funding_accounts(accounts):
    """Collect deposit-type accounts that can serve as funding accounts.

    Args:
        accounts: List of account dicts from payments.yaml accounts section.

    Returns:
        Sorted list of (id, name, institution) tuples for depository accounts.
    """
    result = []
    for a in accounts:
        if a.get("type") == "depository":
            result.append((a["id"], a.get("name", ""), a.get("institution", "")))
    return sorted(result)


def get_payment_dates_in_horizon(day_of_month, days_ahead, start=None):
    """Get all dates a monthly payment falls on within the forecast horizon.

    Uses dateutil.rrule for correct month-boundary handling (e.g., day 31 in
    months with fewer days is skipped, not silently rolled).

    Args:
        day_of_month: Day of month the payment is due (1-31).
        days_ahead: Number of days to look ahead.
        start: Start date for the horizon (default: now).

    Returns:
        List of datetime objects for each occurrence within the horizon.
    """
    if start is None:
        start = datetime.now()

    # Start from beginning of today (midnight) so same-day payments are included
    start_date = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=days_ahead)

    # rrule needs a dtstart before or on the first possible occurrence
    # Start from the 1st of the start month to catch the current month
    dtstart = start_date.replace(day=1)

    dates = list(rrule(
        MONTHLY,
        bymonthday=day_of_month,
        dtstart=dtstart,
        until=end_date,
    ))

    # Filter to only dates within [start_date, end_date]
    return [d for d in dates if start_date <= d <= end_date]


def resolve_payment_amount(payment, account_lookup, monarch_balances, xero_balances,
                           credit_account=None):
    """Resolve the actual payment amount, using live balance for credit cards.

    For credit card payments (identified by having a credit_account), the payment
    amount is the absolute value of the credit card's current balance from Monarch.
    This handles the Monarch convention where credit card balances are negative.

    For non-credit-card payments, returns the amount from payments.yaml.

    Args:
        payment: Payment dict from payments.yaml.
        account_lookup: Dict mapping account_id -> account dict.
        monarch_balances: Dict from fetch_monarch_balances().
        xero_balances: Dict from fetch_xero_balances().
        credit_account: The credit card account dict if this payment pays a credit card.

    Returns:
        float: The resolved payment amount.
    """
    if credit_account and credit_account.get("type") == "credit":
        balance, source = resolve_balance(credit_account, monarch_balances, xero_balances)
        if balance is not None:
            return abs(balance)
        # Fallback to YAML amount if balance unavailable
        return payment.get("amount", 0.0)

    return payment.get("amount", 0.0)


def _find_credit_card_for_payment(payment, accounts):
    """Try to find the credit card account a payment is paying.

    Heuristic: if the payment has autopay_type (full/min), it's likely a credit
    card payment. Match by checking if any credit card account's name or nicknames
    appear in the payment name.

    Args:
        payment: Payment dict.
        accounts: List of account dicts.

    Returns:
        Account dict if found, None otherwise.
    """
    autopay_type = payment.get("autopay_type")
    if not autopay_type:
        return None

    payment_name_lower = payment.get("name", "").lower()
    for acct in accounts:
        if acct.get("type") != "credit":
            continue
        # Check if account name or any nickname matches in payment name
        if acct.get("name", "").lower() in payment_name_lower:
            return acct
        for nick in acct.get("nicknames", []):
            if nick.lower() in payment_name_lower:
                return acct
    return None


def build_forecast(config, monarch_balances, xero_balances, days=30):
    """Build the complete forecast: per-account projected balances after deducting scheduled payments.

    Args:
        config: Parsed payments.yaml (dict with 'accounts' and 'payments' keys).
        monarch_balances: Dict from fetch_monarch_balances().
        xero_balances: Dict from fetch_xero_balances().
        days: Forecast horizon in days (default 30).

    Returns:
        Dict with structure:
            {
                "accounts": [
                    {
                        "id": str,
                        "name": str,
                        "current_balance": float,
                        "balance_source": str,
                        "payments": [{"name": str, "amount": float, "due_date": datetime}],
                        "projected_balance": float,
                        "min_balance": float,
                        "severity": "ok" | "warning" | "error",
                    }
                ],
                "summary": {
                    "total_outgoing": float,
                    "total_available": float,
                    "net_position": float,
                }
            }
    """
    accounts_by_id = {a["id"]: a for a in config.get("accounts", [])}
    payments = config.get("payments", [])
    all_accounts = config.get("accounts", [])

    # Group payments by funding account
    payments_by_account = {}
    for p in payments:
        fa = p.get("funding_account")
        if fa:
            payments_by_account.setdefault(fa, []).append(p)

    forecast_accounts = []
    total_outgoing = 0.0
    total_available = 0.0

    for acct_id, acct_payments in sorted(payments_by_account.items()):
        account = accounts_by_id.get(acct_id)
        if not account:
            continue

        # Resolve current balance
        balance, source = resolve_balance(account, monarch_balances, xero_balances)
        if balance is None:
            print(f"Warning: Could not fetch balance for {account.get('name', acct_id)}, "
                  f"defaulting to 0.0", file=sys.stderr)
            balance = 0.0
            source = "unknown"

        current_balance = balance
        balance_source = source or "unknown"
        min_balance = account.get("min_balance", 0)

        # Compute upcoming payment amounts and dates
        account_payments = []
        for p in acct_payments:
            dates = get_payment_dates_in_horizon(p["day_of_month"], days)
            credit_account = _find_credit_card_for_payment(p, all_accounts)
            amount = resolve_payment_amount(p, accounts_by_id, monarch_balances, xero_balances,
                                            credit_account=credit_account)
            for d in dates:
                account_payments.append({
                    "name": p["name"],
                    "amount": amount,
                    "due_date": d,
                })

        # Sort by due date
        account_payments.sort(key=lambda x: x["due_date"])

        # Projected balance
        total_payments = sum(ap["amount"] for ap in account_payments)
        projected_balance = current_balance - total_payments

        # Severity
        if projected_balance < 0:
            severity = "error"
        elif projected_balance < min_balance:
            severity = "warning"
        else:
            severity = "ok"

        # Unknown balance always error (can't verify solvency)
        if balance_source == "unknown":
            severity = "error"

        total_outgoing += total_payments
        if current_balance > 0:
            total_available += current_balance

        forecast_accounts.append({
            "id": acct_id,
            "name": account.get("name", acct_id),
            "last4": account.get("last4"),
            "institution": account.get("institution"),
            "current_balance": current_balance,
            "balance_source": balance_source,
            "payments": account_payments,
            "projected_balance": projected_balance,
            "min_balance": min_balance,
            "severity": severity,
        })

    return {
        "accounts": forecast_accounts,
        "summary": {
            "total_outgoing": total_outgoing,
            "total_available": total_available,
            "net_position": total_available - total_outgoing,
        },
    }


def print_grouped_view(forecast, days):
    """Print forecast grouped by funding account (default view per D-07).

    Each account section shows current balance, payment table via tabulate,
    projected balance with severity coloring, and threshold info for warnings.
    """
    print(f"\n{'=' * 70}")
    print(f"  PAYMENT FORECAST -- {datetime.now().strftime('%a %b %d, %Y')} (next {days} days)")
    print(f"{'=' * 70}")

    for i, acct in enumerate(forecast["accounts"]):
        acct_label = acct["name"]
        # Show last4 if available in the account data
        if acct.get("last4"):
            acct_label += f" (..{acct['last4']})"

        print(f"\n  {Color.BOLD}{acct_label}{Color.RESET}")
        print(f"  Current Balance: ${acct['current_balance']:,.2f} ({acct['balance_source']})")

        if acct["payments"]:
            table_data = []
            for p in acct["payments"]:
                table_data.append([
                    p["due_date"].strftime("%b %d"),
                    p["name"],
                    f"${p['amount']:,.2f}",
                ])
            print(tabulate_fn(table_data, headers=["Date", "Payment", "Amount"],
                              tablefmt="simple", stralign="left", numalign="right"))
        else:
            print("  No payments in this period.")

        # Projected balance with severity coloring
        proj_str = f"  Projected Balance: ${acct['projected_balance']:,.2f}"
        if acct["severity"] == "error":
            print(f"  {Color.BOLD_RED}{proj_str} ** SHORTFALL **{Color.RESET}")
        elif acct["severity"] == "warning":
            print(f"  {Color.YELLOW}{proj_str} * LOW BALANCE *{Color.RESET}")
            print(f"  (threshold: ${acct['min_balance']:,.2f})")
        else:
            print(f"  {Color.GREEN}{proj_str}{Color.RESET}")

        if i < len(forecast["accounts"]) - 1:
            print(f"  {'─' * 60}")


def print_timeline_view(forecast, days):
    """Print chronological payment timeline across all accounts (D-08).

    Shows all payments sorted by date with running balance per funding account.
    """
    print(f"\n{'=' * 70}")
    print(f"  PAYMENT TIMELINE -- {datetime.now().strftime('%a %b %d, %Y')} (next {days} days)")
    print(f"{'=' * 70}")

    # Collect all payments with their funding account info
    all_payments = []
    for acct in forecast["accounts"]:
        for p in acct["payments"]:
            all_payments.append({
                "due_date": p["due_date"],
                "name": p["name"],
                "amount": p["amount"],
                "account_name": acct["name"],
                "account_id": acct["id"],
            })

    if not all_payments:
        print("\n  No payments in this period.")
        return

    # Sort by date
    all_payments.sort(key=lambda x: x["due_date"])

    # Track running balance per account
    running = {}
    for acct in forecast["accounts"]:
        running[acct["id"]] = {
            "balance": acct["current_balance"],
            "min_balance": acct["min_balance"],
        }

    table_data = []
    for p in all_payments:
        acct_id = p["account_id"]
        running[acct_id]["balance"] -= p["amount"]
        rb = running[acct_id]["balance"]
        min_bal = running[acct_id]["min_balance"]

        # Color the running balance
        if rb < 0:
            rb_str = f"{Color.BOLD_RED}-${abs(rb):,.2f}{Color.RESET}"
        elif rb < min_bal:
            rb_str = f"{Color.YELLOW}${rb:,.2f}{Color.RESET}"
        else:
            rb_str = f"${rb:,.2f}"

        table_data.append([
            p["due_date"].strftime("%b %d"),
            p["name"],
            f"${p['amount']:,.2f}",
            p["account_name"],
            rb_str,
        ])

    print()
    print(tabulate_fn(table_data,
                      headers=["Date", "Payment", "Amount", "Funding Account", "Running Balance"],
                      tablefmt="simple", stralign="left", numalign="right"))


def print_summary(forecast):
    """Print summary line with totals (D-09)."""
    summary = forecast["summary"]
    print(f"\n{'═' * 70}")
    print(f"  SUMMARY")

    print(f"    Total Outgoing:   ${summary['total_outgoing']:>12,.2f}")
    print(f"    Total Available:  ${summary['total_available']:>12,.2f}")

    net = summary["net_position"]
    if net >= 0:
        net_str = f"{Color.GREEN}${net:>12,.2f}{Color.RESET}"
    else:
        net_str = f"{Color.BOLD_RED}-${abs(net):>12,.2f}{Color.RESET}"
    print(f"    Net Position:     {net_str}")

    print(f"{'═' * 70}")


def determine_exit_code(forecast):
    """Determine CLI exit code from worst account severity (D-12).

    Returns:
        EXIT_OK (0) if all accounts are ok.
        EXIT_WARNING (1) if any account is below min_balance but not negative.
        EXIT_ERROR (2) if any account has negative projected balance.
    """
    worst = EXIT_OK
    for acct in forecast["accounts"]:
        if acct["severity"] == "error":
            return EXIT_ERROR
        elif acct["severity"] == "warning":
            worst = EXIT_WARNING
    return worst


async def main():
    """CLI entry point for the payment forecast."""
    parser = argparse.ArgumentParser(
        description="Payment forecast -- project per-account balances and detect shortfalls"
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Forecast horizon in days (default: 30)"
    )
    parser.add_argument(
        "--timeline", action="store_true",
        help="Show chronological timeline instead of grouped view"
    )
    parser.add_argument(
        "--email-summary", action="store_true",
        help="Email the forecast report (cron-friendly daily summary mode)"
    )
    parser.add_argument(
        "--test-alert", action="store_true",
        help="Send a test alert email using the real forecast (forces send regardless of shortfalls)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Export email as forecast_preview.html instead of sending"
    )
    args = parser.parse_args()

    # T-02-03 mitigation: range check on --days
    if args.days < 1 or args.days > 365:
        print("Error: --days must be between 1 and 365", file=sys.stderr)
        sys.exit(EXIT_ERROR)

    config = load_payments_yaml()

    # D-05: Validate funding accounts
    all_payments = config.get("payments", [])
    missing = validate_funding_accounts(all_payments)
    if missing:
        print("ERROR: Cannot produce forecast -- these payments have no funding_account assigned:",
              file=sys.stderr)
        for p in missing:
            day = p.get("day_of_month", "?")
            amount = p.get("amount", 0.0)
            print(f"  - {p['name']}  (day {day}, ${amount:,.2f})", file=sys.stderr)

        all_accounts = config.get("accounts", [])
        available = collect_funding_accounts(all_accounts)
        if available:
            print(f"\nAvailable deposit accounts (use the id as funding_account):", file=sys.stderr)
            for acct_id, name, institution in available:
                print(f"  - {acct_id}  ({name}, {institution})", file=sys.stderr)

        print(f"\nEdit payments.yaml to assign funding_account for each payment above.",
              file=sys.stderr)
        sys.exit(EXIT_ERROR)

    # Warn about credit accounts with no associated payment
    all_accounts = config.get("accounts", [])
    credit_ids_with_payments = set()
    for p in all_payments:
        credit_acct = _find_credit_card_for_payment(p, all_accounts)
        if credit_acct:
            credit_ids_with_payments.add(credit_acct["id"])
    for acct in all_accounts:
        if acct.get("type") == "credit" and acct["id"] not in credit_ids_with_payments:
            print(f"WARNING: Credit account {acct['id']} ({acct.get('name', '?')}) "
                  f"has no payment event in Zoho Calendar. Add a recurring event with "
                  f"the bill amount in the title and funding account in notes.", file=sys.stderr)

    # Fetch balances
    print("Fetching balances...", end=" ", flush=True)
    monarch_balances = await fetch_monarch_balances()
    xero_balances = fetch_xero_balances() if fetch_xero_balances else {}
    print("")

    # Build forecast
    forecast = build_forecast(config, monarch_balances, xero_balances, days=args.days)

    # Display output
    if args.timeline:
        print_timeline_view(forecast, args.days)
    else:
        print_grouped_view(forecast, args.days)

    print_summary(forecast)

    # Email modes (Phase 3)
    email_requested = args.email_summary or args.test_alert
    if email_requested or args.dry_run:
        if not _has_alert_email:
            print("Error: alert_email.py module not found. Cannot send emails.", file=sys.stderr)
            sys.exit(EXIT_ERROR)

        accounts_config = {a["id"]: a for a in config.get("accounts", [])}

        # Load SMTP config once if we will be sending (not dry-run only)
        smtp_config = None
        recipient = None
        if email_requested and not args.dry_run:
            smtp_config = get_smtp_config()
            recipient = get_alert_recipient()

        if args.email_summary:
            # D-07: Daily summary mode -- condensed digest on good days, full report on bad days
            subject, html_body = build_summary_html(forecast)

            if args.dry_run:
                # D-10: Export HTML preview instead of sending
                export_preview(html_body)
            else:
                send_email(subject, html_body, recipient, smtp_config)
                print(f"Summary email sent to {recipient}")

            # Record dedup state for shortfall alerts (D-01 + D-05) but do NOT send
            # a second email -- build_summary_html already includes the full alert
            # table on bad days, so a separate shortfall email would be a duplicate.
            # Uses atomic check_and_record_alerts to avoid race conditions.
            alertable = check_alert_thresholds(forecast, accounts_config)
            if alertable and not args.dry_run:
                check_and_record_alerts(alertable)

        elif args.test_alert:
            # D-09: Force send regardless of shortfalls -- test full pipeline
            alertable = check_alert_thresholds(forecast, accounts_config)
            if alertable:
                html_body = build_alert_html(forecast, alertable)
                subject = f"[TEST] SHORTFALL ALERT -- {len(alertable)} account(s)"
            else:
                subject, html_body = build_summary_html(forecast)
                subject = f"[TEST] {subject}"

            if args.dry_run:
                export_preview(html_body)
            else:
                send_email(subject, html_body, recipient, smtp_config)
                print(f"Test alert sent to {recipient}")

        elif args.dry_run:
            # D-10: Dry-run alone -- preview summary email
            subject, html_body = build_summary_html(forecast)
            export_preview(html_body)

    sys.exit(determine_exit_code(forecast))


if __name__ == "__main__":
    asyncio.run(main())
