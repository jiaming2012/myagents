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
    from coverage_report import (fetch_monarch_balances, resolve_balance, resolve_balance_entry,
                                 load_payments_yaml)
    from monarch_client import MonarchAuthError
    from registry_gaps import check_liabilities_without_payment, MONARCH_STALE_DAYS
except ImportError as e:
    print(f"Error: coverage_report.py / monarch_client.py / registry_gaps.py must be in the same directory ({e})",
          file=sys.stderr)
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

    For credit card payments (identified by having a credit_account) that pay the
    statement in full, the payment amount is the absolute value of the credit
    card's current balance from Monarch. This handles the Monarch convention where
    credit card balances are negative. The current balance includes charges made
    after the statement closed, so it is an upper bound on the autopay debit.

    A minimum-payment autopay (autopay_type: min) never draws the full balance:
    the payments.yaml amount is the estimate (record the last observed minimum).

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
        if payment.get("autopay_type") == "min":
            return payment.get("amount", 0.0)
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
    # Explicit mapping wins (payments.yaml: pays_account: <account id>)
    explicit = payment.get("pays_account")
    if explicit:
        for acct in accounts:
            if acct.get("id") == explicit and acct.get("type") == "credit":
                return acct
        return None

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


def build_forecast(config, monarch_balances, xero_balances, days=30, warn_missing=True):
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
    card_charges = []
    total_outgoing = 0.0
    total_available = 0.0
    total_card_charges = 0.0

    for acct_id, acct_payments in sorted(payments_by_account.items()):
        account = accounts_by_id.get(acct_id)
        if not account:
            continue

        # A bill charged to a credit card does not draw down a bank account; the
        # card's own payment entry does. List these separately instead of
        # projecting a "shortfall" on an account that is a line of credit.
        if account.get("type") == "credit":
            for p in acct_payments:
                for d in get_payment_dates_in_horizon(p["day_of_month"], days):
                    amount = p.get("amount", 0.0)
                    total_card_charges += amount
                    card_charges.append({
                        "name": p["name"],
                        "amount": amount,
                        "due_date": d,
                        "card_id": acct_id,
                        "card_name": account.get("name", acct_id),
                    })
            continue

        # Resolve current balance
        balance, source, as_of = resolve_balance_entry(account, monarch_balances, xero_balances)
        if balance is None:
            if warn_missing:
                print(f"Warning: Could not fetch balance for {account.get('name', acct_id)}, "
                      f"defaulting to 0.0", file=sys.stderr)
            balance = 0.0
            source = "unknown"

        current_balance = balance
        balance_source = source or "unknown"
        min_balance = account.get("min_balance", 0)
        stale_days = _balance_age_days(as_of)

        # Compute upcoming payment amounts and dates
        account_payments = []
        for p in acct_payments:
            dates = get_payment_dates_in_horizon(p["day_of_month"], days)
            credit_account = _find_credit_card_for_payment(p, all_accounts)
            amount = resolve_payment_amount(p, accounts_by_id, monarch_balances, xero_balances,
                                            credit_account=credit_account)
            # A "full" card payment is priced from the card's live balance; if that
            # feed stopped syncing, the amount is as old as the feed. Say so.
            amount_stale_days = None
            if credit_account and p.get("autopay_type") != "min":
                _b, _s, card_as_of = resolve_balance_entry(credit_account, monarch_balances, xero_balances)
                age = _balance_age_days(card_as_of)
                if age is not None and age > MONARCH_STALE_DAYS:
                    amount_stale_days = age
            for d in dates:
                account_payments.append({
                    "name": p["name"],
                    "amount": amount,
                    "due_date": d,
                    "amount_stale_days": amount_stale_days,
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

        # Unknown balance: the source is down or the account is unmapped. That is
        # a gap to fix, not a shortfall -- flagging it as one buries real shortfalls.
        if balance_source == "unknown":
            severity = "unknown"

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
            "balance_as_of": as_of,
            "stale_days": stale_days,
            "stale": stale_days is not None and stale_days > MONARCH_STALE_DAYS,
            "payments": account_payments,
            "projected_balance": projected_balance,
            "min_balance": min_balance,
            "severity": severity,
        })

    card_charges.sort(key=lambda x: x["due_date"])

    return {
        "accounts": forecast_accounts,
        "card_charges": card_charges,
        "summary": {
            "total_outgoing": total_outgoing,
            "total_available": total_available,
            "net_position": total_available - total_outgoing,
            "total_card_charges": total_card_charges,
        },
    }


def _balance_age_days(as_of, today=None):
    """Days since a balance source last refreshed, or None when unknown."""
    if not as_of:
        return None
    try:
        updated = datetime.fromisoformat(str(as_of).replace("Z", "+00:00")).date()
    except ValueError:
        return None
    today = today or datetime.now().date()
    return (today - updated).days


def _amount_note(payment):
    """' [card balance STALE 53d]' when a full-payment amount came from a dead feed."""
    age = payment.get("amount_stale_days")
    if age is None:
        return ""
    return f" {Color.YELLOW}[card bal. STALE {age}d]{Color.RESET}"


def balance_label(acct):
    """'$3,068.53 (monarch, as of 2026-10-16)' with a STALE marker when too old."""
    label = f"${acct['current_balance']:,.2f} ({acct['balance_source']}"
    if acct.get("balance_as_of"):
        label += f", as of {str(acct['balance_as_of'])[:10]}"
    label += ")"
    if acct.get("stale"):
        label += f" {Color.BOLD_RED}STALE {acct['stale_days']}d{Color.RESET}"
    return label


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
        print(f"  Current Balance: {balance_label(acct)}")

        if acct["payments"]:
            table_data = []
            for p in acct["payments"]:
                table_data.append([
                    p["due_date"].strftime("%b %d"),
                    p["name"],
                    f"${p['amount']:,.2f}",
                    _amount_note(p),
                ])
            print(tabulate_fn(table_data, headers=["Date", "Payment", "Amount", ""],
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
        elif acct["severity"] == "unknown":
            print(f"  {Color.YELLOW}{proj_str} ? BALANCE UNKNOWN (source unavailable) ?{Color.RESET}")
        else:
            print(f"  {Color.GREEN}{proj_str}{Color.RESET}")

        if i < len(forecast["accounts"]) - 1:
            print(f"  {'─' * 60}")

    print_card_charges(forecast)


def print_card_charges(forecast):
    """Bills charged to a credit card: no bank draw, they ride on the card's payment."""
    charges = forecast.get("card_charges") or []
    if not charges:
        return
    print(f"\n  {Color.BOLD}Charged to credit cards (no bank draw; paid via the card's own entry){Color.RESET}")
    table_data = [[c["due_date"].strftime("%b %d"), c["name"], f"${c['amount']:,.2f}", c["card_name"]]
                  for c in charges]
    print(tabulate_fn(table_data, headers=["Date", "Charge", "Amount", "Card"],
                      tablefmt="simple", stralign="left", numalign="right"))


def print_funding_check(forecast, days):
    """One row per bank account: what it holds, what leaves it, and whether that clears."""
    print(f"\n  {Color.BOLD}FUNDING CHECK -- per bank account, next {days} days{Color.RESET}")
    table_data = []
    for acct in forecast["accounts"]:
        due = sum(p["amount"] for p in acct["payments"])
        label = acct["name"] + (f" (..{acct['last4']})" if acct.get("last4") else "")
        bal = f"${acct['current_balance']:,.2f}"
        if acct.get("balance_as_of"):
            bal += f" @{str(acct['balance_as_of'])[5:10]}"
        proj = acct["projected_balance"]
        if acct["severity"] == "unknown":
            verdict = f"{Color.YELLOW}UNKNOWN balance ({acct['balance_source']}){Color.RESET}"
        elif acct["severity"] == "error":
            verdict = f"{Color.BOLD_RED}SHORT by ${abs(proj):,.2f}{Color.RESET}"
        elif acct["severity"] == "warning":
            verdict = f"{Color.YELLOW}OK, but ${proj:,.2f} < ${acct['min_balance']:,.0f} floor{Color.RESET}"
        else:
            verdict = f"{Color.GREEN}OK, ${proj:,.2f} left{Color.RESET}"
        if acct.get("stale"):
            verdict += f" {Color.BOLD_RED}[balance STALE {acct['stale_days']}d]{Color.RESET}"
        table_data.append([label, bal, f"${due:,.2f}", len(acct["payments"]), verdict])
    print(tabulate_fn(table_data,
                      headers=["Bank account", "Balance", "Due", "#", "Verdict"],
                      tablefmt="simple", stralign="left", numalign="right"))


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
                "amount_stale_days": p.get("amount_stale_days"),
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
            f"${p['amount']:,.2f}" + _amount_note(p),
            p["account_name"],
            rb_str,
        ])

    print()
    print(tabulate_fn(table_data,
                      headers=["Date", "Payment", "Amount", "Funding Account", "Running Balance"],
                      tablefmt="simple", stralign="left", numalign="right"))

    print_funding_check(forecast, days)
    print_card_charges(forecast)


def print_summary(forecast):
    """Print summary line with totals (D-09)."""
    summary = forecast["summary"]
    print(f"\n{'═' * 70}")
    print(f"  SUMMARY")

    print(f"    Total Outgoing:   ${summary['total_outgoing']:>12,.2f}   (bank draws)")
    if summary.get("total_card_charges"):
        print(f"    Card Charges:     ${summary['total_card_charges']:>12,.2f}   (on cards, not a bank draw)")
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
        EXIT_WARNING (1) if any account is below min_balance but not negative,
                         or its balance could not be fetched (unknown).
        EXIT_ERROR (2) if any account has negative projected balance.
    """
    worst = EXIT_OK
    for acct in forecast["accounts"]:
        if acct["severity"] == "error":
            return EXIT_ERROR
        elif acct["severity"] in ("warning", "unknown"):
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
    parser.add_argument(
        "--no-balances", action="store_true",
        help="Skip Monarch/Xero entirely: list the scheduled payments with every balance "
             "treated as $0 (deterministic; use when a login is broken)"
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

    # Registry gaps that make this forecast incomplete (offline check; full set: task finance:gaps)
    for gap in check_liabilities_without_payment(config):
        print(f"WARNING: {gap['message']} {gap['fix']}", file=sys.stderr)

    # Fetch balances
    if args.no_balances:
        print("Balances skipped (--no-balances): every starting balance is $0, so projected "
              "balances and shortfall flags below are NOT meaningful. The payment list and dates are.",
              file=sys.stderr)
        monarch_balances, xero_balances = {}, {}
    else:
        print("Fetching balances...", end=" ", flush=True)
        try:
            monarch_balances = await fetch_monarch_balances()
        except MonarchAuthError as e:
            print(f"\nError: {e}", file=sys.stderr)
            print("Re-run with --no-balances to see the payment schedule without balances.",
                  file=sys.stderr)
            sys.exit(EXIT_ERROR)
        xero_balances = fetch_xero_balances() if fetch_xero_balances else {}
        print("")

    # Build forecast
    forecast = build_forecast(config, monarch_balances, xero_balances, days=args.days,
                              warn_missing=not args.no_balances)

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
            # Sync banner above the numbers: which Monarch connections / Zoho feeds are stale.
            try:
                from sync_status import collect as collect_sync_status, inject_banner_html
                html_body = inject_banner_html(html_body, await collect_sync_status())
            except Exception as e:  # noqa: BLE001 - the email must still go out
                print(f"Warning: sync banner skipped ({type(e).__name__}: {e})", file=sys.stderr)

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

    if args.no_balances:
        sys.exit(EXIT_OK)  # severities are meaningless without balances
    sys.exit(determine_exit_code(forecast))


if __name__ == "__main__":
    asyncio.run(main())
