#!/usr/bin/env python3
"""
Weekly financial health check — consolidated report across all checks.

Runs:
  1. Transaction audit (uncategorized, missing recurring, unreconciled)
  2. Payment forecast (account balances, shortfalls, upcoming bills)
  3. Budget pacing (spending vs plan per category)
  4. Goal progress (savings contributions vs targets)

Usage:
    python weekly_health.py                # Terminal report
    python weekly_health.py --email        # Send HTML email report
    python weekly_health.py --dry-run      # Save HTML to file for preview
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from coverage_report import fetch_monarch_balances, load_payments_yaml

try:
    from xero_balances import fetch_xero_balances
except ImportError:
    fetch_xero_balances = None

from transaction_audit import run_audit as run_transaction_audit
from payment_forecast import build_forecast, determine_exit_code
from monarch_budget import fetch_budget_data, fetch_categories, analyze_budget_pacing, analyze_goal_progress

try:
    from alert_email import get_smtp_config, get_alert_recipient, send_email, export_preview
    _has_email = True
except ImportError:
    _has_email = False


# ANSI colors
class Color:
    RED = "\033[31m"
    BOLD_RED = "\033[1;31m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    BOLD = "\033[1m"
    CYAN = "\033[36m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    @classmethod
    def disable(cls):
        for attr in ("RED", "BOLD_RED", "YELLOW", "GREEN", "BOLD", "CYAN", "DIM", "RESET"):
            setattr(cls, attr, "")

if not sys.stdout.isatty():
    Color.disable()


async def gather_all_data():
    """Gather data from all sources concurrently where possible.

    Returns dict with all raw data needed for the report.
    """
    # Start async tasks
    monarch_balances_task = fetch_monarch_balances()
    transaction_audit_task = run_transaction_audit(days=7)
    budget_data_task = fetch_budget_data()
    categories_task = fetch_categories()

    # Gather async results
    monarch_balances, audit_result, budget_data, categories = await asyncio.gather(
        monarch_balances_task,
        transaction_audit_task,
        budget_data_task,
        categories_task,
    )

    # Sync calls
    xero_balances = fetch_xero_balances() if fetch_xero_balances else {}

    # Build forecast from balances
    config = load_payments_yaml()
    forecast = build_forecast(config, monarch_balances, xero_balances, days=30)

    # Analyze budget
    budget_analysis = analyze_budget_pacing(budget_data, categories)
    goal_progress = analyze_goal_progress(budget_data)

    return {
        "audit": audit_result,
        "forecast": forecast,
        "budget": budget_analysis,
        "goals": goal_progress,
        "run_at": datetime.now(),
    }


def count_action_items(data):
    """Count total action items across all checks."""
    items = []

    # Transaction audit items
    audit = data["audit"]
    mc = audit["monarch_uncategorized"]
    nr = len(mc.get("needs_review", []))
    uc = len(mc.get("uncategorized", []))
    if nr > 0:
        items.append(f"{nr} Monarch transaction(s) need review → https://app.monarchmoney.com/transactions")
    if uc > 0 and uc != nr:
        items.append(f"{uc} uncategorized transaction(s) in Monarch")

    xr = audit["xero_unreconciled"]
    ur = len(xr.get("unreconciled", []))
    if ur > 0:
        items.append(f"{ur} unreconciled Xero transaction(s) → https://go.xero.com/Bank/BankAccounts.aspx")

    missing = audit["recurring_check"].get("missing", [])
    if missing:
        items.append(f"{len(missing)} expected payment(s) not found")

    # Forecast items
    for acct in data["forecast"]["accounts"]:
        if acct["severity"] == "error":
            items.append(f"SHORTFALL: {acct['name']} projected ${acct['projected_balance']:,.2f}")
        elif acct["severity"] == "warning":
            items.append(f"LOW: {acct['name']} projected ${acct['projected_balance']:,.2f} (min: ${acct['min_balance']:,.2f})")

    # Budget items
    for cat in data["budget"]["categories"]:
        if cat["status"] == "over":
            items.append(f"Over budget: {cat['name']} (${cat['actual']:,.0f} / ${cat['planned']:,.0f})")

    # Goal items
    for g in data["goals"]:
        if g["status"] == "at_risk":
            items.append(f"Goal at risk: {g['name']} ({g['pct']:.0f}% of planned)")

    return items


def print_terminal_report(data):
    """Print consolidated report to terminal."""
    run_at = data["run_at"]

    print(f"\n{'━' * 70}")
    print(f"  {Color.BOLD}WEEKLY FINANCIAL HEALTH CHECK{Color.RESET}")
    print(f"  {run_at.strftime('%A, %B %d, %Y %I:%M %p')}")
    print(f"{'━' * 70}")

    # --- Section 1: Transaction Audit ---
    audit = data["audit"]
    mc = audit["monarch_uncategorized"]
    xr = audit["xero_unreconciled"]
    rc = audit["recurring_check"]

    print(f"\n  {Color.BOLD}1. TRANSACTION AUDIT{Color.RESET} (last {audit['days']} days)")
    print(f"  {'─' * 55}")

    # Monarch
    if mc.get("error"):
        print(f"    Monarch: {Color.DIM}skipped ({mc['error']}){Color.RESET}")
    else:
        nr = len(mc.get("needs_review", []))
        if nr == 0:
            print(f"    Monarch: {Color.GREEN}All reviewed{Color.RESET} ({mc['total_checked']} txns)")
        else:
            print(f"    Monarch: {Color.YELLOW}{nr} need review{Color.RESET} ({mc['total_checked']} txns)")

    # Xero
    if xr.get("error"):
        print(f"    Xero:    {Color.DIM}skipped ({xr['error']}){Color.RESET}")
    else:
        ur = len(xr.get("unreconciled", []))
        if ur == 0:
            print(f"    Xero:    {Color.GREEN}All reconciled{Color.RESET}")
        else:
            print(f"    Xero:    {Color.YELLOW}{ur} unreconciled{Color.RESET}")

    # Recurring
    missing = rc.get("missing", [])
    found = rc.get("found", [])
    if missing:
        print(f"    Bills:   {Color.BOLD_RED}{len(missing)} missing{Color.RESET}, {len(found)} confirmed")
        for p in missing:
            print(f"             - {p['name']}: ${p['amount']:,.2f}")
    elif found:
        print(f"    Bills:   {Color.GREEN}All {len(found)} confirmed{Color.RESET}")

    # --- Section 2: Account Health ---
    forecast = data["forecast"]
    print(f"\n  {Color.BOLD}2. ACCOUNT HEALTH{Color.RESET} (30-day forecast)")
    print(f"  {'─' * 55}")

    for acct in forecast["accounts"]:
        if acct["severity"] == "error":
            status = f"{Color.BOLD_RED}SHORTFALL{Color.RESET}"
        elif acct["severity"] == "warning":
            status = f"{Color.YELLOW}LOW{Color.RESET}"
        else:
            status = f"{Color.GREEN}OK{Color.RESET}"

        acct_label = acct["name"]
        if acct.get("last4"):
            acct_label += f" (..{acct['last4']})"

        print(f"    {acct_label:<35} ${acct['projected_balance']:>10,.2f}  {status}")

    summary = forecast["summary"]
    net = summary["net_position"]
    net_color = Color.GREEN if net >= 0 else Color.BOLD_RED
    print(f"    {'─' * 55}")
    print(f"    {'Net Position':<35} {net_color}${net:>10,.2f}{Color.RESET}")

    # --- Section 3: Budget ---
    budget = data["budget"]
    print(f"\n  {Color.BOLD}3. BUDGET STATUS{Color.RESET} ({budget['month_pct']:.0f}% through month)")
    print(f"  {'─' * 55}")

    problem_cats = [c for c in budget["categories"] if c["status"] in ("over", "trending_over")]
    ok_cats = [c for c in budget["categories"] if c["status"] not in ("over", "trending_over")]

    if problem_cats:
        for c in problem_cats:
            if c["status"] == "over":
                status = f"{Color.BOLD_RED}OVER{Color.RESET}"
            else:
                status = f"{Color.YELLOW}TRENDING OVER{Color.RESET}"
            print(f"    {c['name']:<25} ${c['actual']:>8,.0f} / ${c['planned']:>8,.0f}  {status}")

    if ok_cats:
        ok_count = len(ok_cats)
        print(f"    {Color.GREEN}{ok_count} other categorie(s) on pace{Color.RESET}")

    if not budget["categories"]:
        print(f"    {Color.DIM}No budgeted categories found{Color.RESET}")

    # --- Section 4: Goals ---
    goals = data["goals"]
    print(f"\n  {Color.BOLD}4. GOAL PROGRESS{Color.RESET}")
    print(f"  {'─' * 55}")

    if not goals:
        print(f"    {Color.DIM}No active goals found{Color.RESET}")
    else:
        for g in goals:
            if g["status"] == "on_track":
                status = f"{Color.GREEN}ON TRACK{Color.RESET}"
            elif g["status"] == "behind":
                status = f"{Color.YELLOW}BEHIND{Color.RESET}"
            else:
                status = f"{Color.BOLD_RED}AT RISK{Color.RESET}"
            print(f"    {g['name']:<25} ${g['actual']:>8,.0f} / ${g['planned']:>8,.0f}  {status}")

    # --- Action Items ---
    items = count_action_items(data)
    print(f"\n{'━' * 70}")
    if items:
        print(f"  {Color.BOLD}ACTION ITEMS ({len(items)}){Color.RESET}")
        for i, item in enumerate(items, 1):
            print(f"    {i}. {item}")
    else:
        print(f"  {Color.GREEN}No action items — finances are healthy!{Color.RESET}")
    print(f"{'━' * 70}")


def build_health_email_html(data):
    """Build HTML email for the weekly health report.

    Returns:
        tuple: (subject, html_body)
    """
    run_at = data["run_at"]
    items = count_action_items(data)

    # Determine overall status for subject line
    has_errors = any(a["severity"] == "error" for a in data["forecast"]["accounts"])
    has_over_budget = any(c["status"] == "over" for c in data["budget"]["categories"])

    if has_errors:
        status_emoji = "ALERT"
        header_color = "#dc2626"
    elif items:
        status_emoji = "REVIEW"
        header_color = "#d97706"
    else:
        status_emoji = "HEALTHY"
        header_color = "#16a34a"

    subject = f"[{status_emoji}] Weekly Financial Health — {run_at.strftime('%b %d, %Y')}"

    # Build account health rows
    acct_rows = ""
    for acct in data["forecast"]["accounts"]:
        if acct["severity"] == "error":
            bg = "#fee2e2"
            weight = "font-weight: bold;"
        elif acct["severity"] == "warning":
            bg = "#fef9c3"
            weight = ""
        else:
            bg = "#ffffff"
            weight = ""

        acct_label = acct["name"]
        if acct.get("last4"):
            acct_label += f" (..{acct['last4']})"

        acct_rows += (
            f'<tr style="background: {bg}; {weight}">'
            f'<td style="padding: 6px 8px; border: 1px solid #e5e7eb;">{acct_label}</td>'
            f'<td style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: right;">${acct["current_balance"]:,.2f}</td>'
            f'<td style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: right;">${acct["projected_balance"]:,.2f}</td>'
            f'</tr>\n'
        )

    # Build budget rows (only problem categories + summary)
    budget_rows = ""
    problem_cats = [c for c in data["budget"]["categories"] if c["status"] in ("over", "trending_over")]
    for c in problem_cats:
        bg = "#fee2e2" if c["status"] == "over" else "#fef9c3"
        status = "OVER" if c["status"] == "over" else "TRENDING"
        budget_rows += (
            f'<tr style="background: {bg};">'
            f'<td style="padding: 6px 8px; border: 1px solid #e5e7eb;">{c["name"]}</td>'
            f'<td style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: right;">${c["actual"]:,.0f}</td>'
            f'<td style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: right;">${c["planned"]:,.0f}</td>'
            f'<td style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: center;">{status}</td>'
            f'</tr>\n'
        )
    ok_count = len(data["budget"]["categories"]) - len(problem_cats)
    if ok_count > 0:
        budget_rows += (
            f'<tr style="background: #f0fdf4;">'
            f'<td colspan="4" style="padding: 6px 8px; border: 1px solid #e5e7eb; color: #16a34a;">'
            f'{ok_count} other categories on pace</td></tr>\n'
        )

    # Build goal rows
    goal_rows = ""
    for g in data["goals"]:
        if g["status"] == "on_track":
            color = "#16a34a"
            label = "ON TRACK"
        elif g["status"] == "behind":
            color = "#d97706"
            label = "BEHIND"
        else:
            color = "#dc2626"
            label = "AT RISK"
        goal_rows += (
            f'<tr>'
            f'<td style="padding: 6px 8px; border: 1px solid #e5e7eb;">{g["name"]}</td>'
            f'<td style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: right;">${g["actual"]:,.0f}</td>'
            f'<td style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: right;">${g["planned"]:,.0f}</td>'
            f'<td style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: center; color: {color};">{label}</td>'
            f'</tr>\n'
        )

    # Action items HTML
    action_html = ""
    if items:
        action_items = "".join(f"<li style='margin-bottom: 4px;'>{item}</li>" for item in items)
        action_html = f"""
        <h3 style="color: #1f2937; margin-top: 24px;">Action Items ({len(items)})</h3>
        <ul style="color: #374151; padding-left: 20px;">{action_items}</ul>
        """
    else:
        action_html = '<p style="color: #16a34a; font-weight: bold; margin-top: 24px;">No action items — finances are healthy!</p>'

    # Transaction audit summary
    audit = data["audit"]
    mc = audit["monarch_uncategorized"]
    xr = audit["xero_unreconciled"]
    rc = audit["recurring_check"]
    nr = len(mc.get("needs_review", []))
    ur = len(xr.get("unreconciled", []))
    missing_count = len(rc.get("missing", []))
    found_count = len(rc.get("found", []))

    audit_color = "#16a34a" if (nr == 0 and ur == 0 and missing_count == 0) else "#d97706"

    html = f"""<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f9fafb;">
<div style="max-width: 640px; margin: 0 auto;">

<h2 style="color: {header_color}; margin-bottom: 4px;">Weekly Financial Health — {status_emoji}</h2>
<p style="color: #6b7280; margin-top: 0;">{run_at.strftime('%A, %B %d, %Y')}</p>

<h3 style="color: #1f2937;">Transaction Audit</h3>
<table style="width: 100%; border-collapse: collapse; margin-bottom: 16px;">
<tr style="background: #f3f4f6;">
<td style="padding: 6px 8px; border: 1px solid #e5e7eb;">Monarch — needs review</td>
<td style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: right; color: {audit_color};">{nr}</td>
</tr>
<tr>
<td style="padding: 6px 8px; border: 1px solid #e5e7eb;">Xero — unreconciled</td>
<td style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: right;">{ur}</td>
</tr>
<tr>
<td style="padding: 6px 8px; border: 1px solid #e5e7eb;">Recurring bills — confirmed / missing</td>
<td style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: right;">{found_count} / {missing_count}</td>
</tr>
</table>

<h3 style="color: #1f2937;">Account Health (30-day forecast)</h3>
<table style="width: 100%; border-collapse: collapse; margin-bottom: 16px;">
<tr style="background: #f3f4f6;">
<th style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: left;">Account</th>
<th style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: right;">Current</th>
<th style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: right;">Projected</th>
</tr>
{acct_rows}</table>

<h3 style="color: #1f2937;">Budget ({data['budget']['month_pct']:.0f}% through month)</h3>
<table style="width: 100%; border-collapse: collapse; margin-bottom: 16px;">
<tr style="background: #f3f4f6;">
<th style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: left;">Category</th>
<th style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: right;">Spent</th>
<th style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: right;">Budget</th>
<th style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: center;">Status</th>
</tr>
{budget_rows}</table>

<h3 style="color: #1f2937;">Goal Progress</h3>
<table style="width: 100%; border-collapse: collapse; margin-bottom: 16px;">
<tr style="background: #f3f4f6;">
<th style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: left;">Goal</th>
<th style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: right;">Actual</th>
<th style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: right;">Planned</th>
<th style="padding: 6px 8px; border: 1px solid #e5e7eb; text-align: center;">Status</th>
</tr>
{goal_rows if goal_rows else '<tr><td colspan="4" style="padding: 6px 8px; border: 1px solid #e5e7eb; color: #9ca3af;">No active goals</td></tr>'}
</table>

{action_html}

<p style="color: #9ca3af; font-size: 12px; margin-top: 24px;">Generated by Banking Payment Forecaster</p>
</div>
</body>
</html>"""

    return subject, html


async def main_async(args):
    """Async entry point."""
    print("Gathering financial data...", end=" ", flush=True)
    data = await gather_all_data()
    print("done\n")

    # Always print terminal report
    print_terminal_report(data)

    # Email / dry-run
    if args.email or args.dry_run:
        if not _has_email:
            print("\nError: alert_email.py module not found. Cannot send emails.", file=sys.stderr)
            sys.exit(1)

        subject, html = build_health_email_html(data)

        if args.dry_run:
            export_preview(html, filename="health_preview.html")
        elif args.email:
            smtp_config = get_smtp_config()
            recipient = get_alert_recipient()
            send_email(subject, html, recipient, smtp_config)
            print(f"\nReport emailed to {recipient}")


def main():
    parser = argparse.ArgumentParser(
        description="Weekly financial health check — consolidated report"
    )
    parser.add_argument(
        "--email", action="store_true",
        help="Send HTML email report"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Save HTML report to health_preview.html"
    )
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
