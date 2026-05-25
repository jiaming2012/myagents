#!/usr/bin/env python3
"""
Monarch Money budget pacing and goal progress tracker.

Pulls budget data and goals from Monarch Money, then applies heuristics
to assess whether spending is on pace and goals are on track.

Usage:
    python monarch_budget.py                  # Current month
    python monarch_budget.py --month 2026-04  # Specific month
"""

import argparse
import asyncio
import calendar
import os
import sys
from datetime import datetime

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
    from gql import gql
except ImportError:
    gql = None

# ANSI colors with TTY detection
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


async def _get_monarch_client():
    """Get an authenticated Monarch Money client."""
    if MonarchMoney is None:
        print("Error: monarchmoney package not installed", file=sys.stderr)
        sys.exit(1)
    token = os.environ.get("MONARCH_TOKEN")
    if not token:
        print("Error: MONARCH_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    return MonarchMoney(token=token)


async def fetch_budget_data(target_month=None):
    """Fetch budget and goals data from Monarch Money.

    Uses direct GraphQL calls because the SDK's get_budgets() query
    contains stale fields that Monarch's API rejects (as of v0.1.15).

    Args:
        target_month: "YYYY-MM" string, defaults to current month.

    Returns:
        dict with keys: budgetData, goalsV2
    """
    mm = await _get_monarch_client()

    if target_month:
        year, month = map(int, target_month.split("-"))
    else:
        now = datetime.now()
        year, month = now.year, now.month

    start_date = f"{year}-{month:02d}-01"
    last_day = calendar.monthrange(year, month)[1]
    end_date = f"{year}-{month:02d}-{last_day:02d}"

    variables = {"startDate": start_date, "endDate": end_date}

    # Budget query — category-level planned vs actual
    budget_query = gql("""
    query GetBudgetData($startDate: Date!, $endDate: Date!) {
        budgetData(startMonth: $startDate, endMonth: $endDate) {
            monthlyAmountsByCategory {
                category {
                    id
                    __typename
                }
                monthlyAmounts {
                    month
                    plannedCashFlowAmount
                    actualAmount
                    remainingAmount
                    __typename
                }
                __typename
            }
            totalsByMonth {
                month
                totalIncome {
                    plannedAmount
                    actualAmount
                    remainingAmount
                    __typename
                }
                totalExpenses {
                    plannedAmount
                    actualAmount
                    remainingAmount
                    __typename
                }
                __typename
            }
            __typename
        }
    }
    """)

    # Goals query — v2 goals with planned/actual contributions
    goals_query = gql("""
    query GetGoals($startDate: Date!, $endDate: Date!) {
        goalsV2 {
            id
            name
            archivedAt
            completedAt
            priority
            plannedContributions(startMonth: $startDate, endMonth: $endDate) {
                id
                month
                amount
                __typename
            }
            monthlyContributionSummaries(startMonth: $startDate, endMonth: $endDate) {
                month
                sum
                __typename
            }
            __typename
        }
    }
    """)

    budget_data = await mm.gql_call(
        operation="GetBudgetData",
        graphql_query=budget_query,
        variables=variables,
    )

    goals_data = await mm.gql_call(
        operation="GetGoals",
        graphql_query=goals_query,
        variables=variables,
    )

    # Merge into a single response matching the shape the rest of the code expects
    budget_data["goalsV2"] = goals_data.get("goalsV2", [])
    return budget_data


async def fetch_categories():
    """Fetch category name lookup from Monarch.

    Returns:
        dict mapping category_id -> {name, group_name, group_type}
    """
    mm = await _get_monarch_client()
    data = await mm.get_transaction_categories()

    lookup = {}
    for cat in data.get("categories", []):
        group = cat.get("group", {}) or {}
        lookup[cat["id"]] = {
            "name": cat.get("name", "Unknown"),
            "group_name": group.get("name", ""),
            "group_type": group.get("type", ""),
        }
    return lookup


def analyze_budget_pacing(budget_data, categories, target_month=None):
    """Analyze budget pacing using day-of-month heuristics.

    For each budgeted category, compares actual spending to expected
    pace based on how far through the month we are.

    Args:
        budget_data: Raw response from get_budgets().
        categories: Category lookup from fetch_categories().
        target_month: "YYYY-MM" or None for current month.

    Returns:
        dict with:
            month_pct: float (0-100), how far through the month
            categories: list of category analysis dicts
            totals: income/expense totals with planned vs actual
    """
    if target_month:
        year, month = map(int, target_month.split("-"))
    else:
        now = datetime.now()
        year, month = now.year, now.month

    days_in_month = calendar.monthrange(year, month)[1]
    today = datetime.now()

    if today.year == year and today.month == month:
        day_of_month = today.day
    else:
        # Viewing a past or future month
        day_of_month = days_in_month

    month_pct = (day_of_month / days_in_month) * 100

    # Parse category-level budget data
    monthly_by_cat = budget_data.get("budgetData", {}).get("monthlyAmountsByCategory", [])
    target_month_str = f"{year}-{month:02d}-01"

    category_results = []
    for entry in monthly_by_cat:
        cat_id = entry.get("category", {}).get("id")
        if not cat_id:
            continue

        cat_info = categories.get(cat_id, {"name": "Unknown", "group_name": "", "group_type": ""})

        # Find the monthly amount for our target month
        for ma in entry.get("monthlyAmounts", []):
            if ma.get("month", "").startswith(f"{year}-{month:02d}"):
                planned = abs(ma.get("plannedCashFlowAmount", 0))
                actual = abs(ma.get("actualAmount", 0))
                remaining = ma.get("remainingAmount", 0)

                if planned == 0:
                    continue  # Skip unbudgeted categories

                pct_used = (actual / planned * 100) if planned > 0 else 0
                expected_pct = month_pct

                # Determine status
                days_remaining = days_in_month - day_of_month
                if actual > planned:
                    status = "over"
                elif pct_used > 80 and days_remaining > 7:
                    status = "trending_over"
                elif pct_used > expected_pct + 15:
                    status = "ahead"
                else:
                    status = "ok"

                category_results.append({
                    "name": cat_info["name"],
                    "group": cat_info["group_name"],
                    "group_type": cat_info["group_type"],
                    "planned": planned,
                    "actual": actual,
                    "remaining": remaining,
                    "pct_used": pct_used,
                    "status": status,
                })
                break

    # Sort: problems first, then by pct_used descending
    status_order = {"over": 0, "trending_over": 1, "ahead": 2, "ok": 3}
    category_results.sort(key=lambda c: (status_order.get(c["status"], 9), -c["pct_used"]))

    # Parse totals
    totals_by_month = budget_data.get("budgetData", {}).get("totalsByMonth", [])
    totals = {}
    for t in totals_by_month:
        if t.get("month", "").startswith(f"{year}-{month:02d}"):
            inc = t.get("totalIncome", {})
            exp = t.get("totalExpenses", {})
            totals = {
                "income_planned": inc.get("plannedAmount", 0),
                "income_actual": inc.get("actualAmount", 0),
                "expenses_planned": abs(exp.get("plannedAmount", 0)),
                "expenses_actual": abs(exp.get("actualAmount", 0)),
            }
            break

    return {
        "month_pct": month_pct,
        "day_of_month": day_of_month,
        "days_in_month": days_in_month,
        "year": year,
        "month": month,
        "categories": category_results,
        "totals": totals,
    }


def analyze_goal_progress(budget_data, target_month=None):
    """Analyze goal progress from Monarch v2 goals.

    Args:
        budget_data: Raw response from get_budgets().
        target_month: "YYYY-MM" or None for current month.

    Returns:
        list of goal analysis dicts.
    """
    if target_month:
        year, month = map(int, target_month.split("-"))
    else:
        now = datetime.now()
        year, month = now.year, now.month

    goals_v2 = budget_data.get("goalsV2", [])
    if not goals_v2:
        return []

    results = []
    for goal in goals_v2:
        # Skip archived/completed goals
        if goal.get("archivedAt") or goal.get("completedAt"):
            continue

        name = goal.get("name", "Unknown Goal")
        priority = goal.get("priority")

        # Find planned contribution for this month
        planned_amount = 0
        for pc in goal.get("plannedContributions", []):
            if pc.get("month", "").startswith(f"{year}-{month:02d}"):
                planned_amount = pc.get("amount", 0)
                break

        # Find actual contribution for this month
        actual_amount = 0
        for cs in goal.get("monthlyContributionSummaries", []):
            if cs.get("month", "").startswith(f"{year}-{month:02d}"):
                actual_amount = cs.get("sum", 0)
                break

        if planned_amount == 0 and actual_amount == 0:
            continue  # No activity this month

        pct = (actual_amount / planned_amount * 100) if planned_amount > 0 else 0
        if actual_amount >= planned_amount:
            status = "on_track"
        elif pct >= 50:
            status = "behind"
        else:
            status = "at_risk"

        results.append({
            "name": name,
            "priority": priority,
            "planned": planned_amount,
            "actual": actual_amount,
            "pct": pct,
            "status": status,
        })

    return results


def print_budget_report(analysis):
    """Print budget pacing report to terminal."""
    month_name = calendar.month_name[analysis["month"]]
    pct = analysis["month_pct"]

    print(f"\n{'=' * 70}")
    print(f"  BUDGET STATUS — {month_name} {analysis['year']} ({pct:.0f}% through month)")
    print(f"{'=' * 70}")

    cats = analysis["categories"]
    if not cats:
        print(f"\n  {Color.DIM}No budgeted categories found{Color.RESET}")
    else:
        # Group by expense group
        current_group = None
        for c in cats:
            if c["group"] != current_group:
                current_group = c["group"]
                if current_group:
                    print(f"\n  {Color.DIM}{current_group}{Color.RESET}")

            pct_str = f"{c['pct_used']:.0f}%"
            bar = f"${c['actual']:,.0f} / ${c['planned']:,.0f} ({pct_str})"

            if c["status"] == "over":
                label = f"{Color.BOLD_RED}OVER BUDGET{Color.RESET}"
            elif c["status"] == "trending_over":
                label = f"{Color.YELLOW}TRENDING OVER{Color.RESET}"
            elif c["status"] == "ahead":
                label = f"{Color.YELLOW}AHEAD OF PACE{Color.RESET}"
            else:
                label = f"{Color.GREEN}ON PACE{Color.RESET}"

            print(f"    {c['name']:<25} {bar:<30} {label}")

    # Totals
    totals = analysis.get("totals", {})
    if totals:
        print(f"\n  {'─' * 60}")
        inc_actual = totals.get("income_actual", 0)
        inc_planned = totals.get("income_planned", 0)
        exp_actual = totals.get("expenses_actual", 0)
        exp_planned = totals.get("expenses_planned", 0)

        print(f"    {'Income':<25} ${inc_actual:>10,.0f} / ${inc_planned:>10,.0f}")
        print(f"    {'Expenses':<25} ${exp_actual:>10,.0f} / ${exp_planned:>10,.0f}")

        net = inc_actual - exp_actual
        net_str = f"${net:>10,.0f}" if net >= 0 else f"-${abs(net):>10,.0f}"
        print(f"    {'Net':<25} {net_str}")


def print_goal_report(goals):
    """Print goal progress report to terminal."""
    print(f"\n{'=' * 70}")
    print(f"  GOAL PROGRESS")
    print(f"{'=' * 70}")

    if not goals:
        print(f"\n  {Color.DIM}No active goals found{Color.RESET}")
        return

    for g in goals:
        pct_str = f"{g['pct']:.0f}%"
        bar = f"${g['actual']:,.0f} / ${g['planned']:,.0f} ({pct_str})"

        if g["status"] == "on_track":
            label = f"{Color.GREEN}ON TRACK{Color.RESET}"
        elif g["status"] == "behind":
            label = f"{Color.YELLOW}BEHIND{Color.RESET}"
        else:
            label = f"{Color.BOLD_RED}AT RISK{Color.RESET}"

        print(f"    {g['name']:<25} {bar:<30} {label}")

    print(f"{'=' * 70}")


async def run_budget_check(target_month=None):
    """Run budget and goal analysis. Returns (budget_analysis, goal_results)."""
    print("Fetching budget data...", end=" ", flush=True)
    budget_data = await fetch_budget_data(target_month)
    categories = await fetch_categories()
    print("done")

    analysis = analyze_budget_pacing(budget_data, categories, target_month)
    goals = analyze_goal_progress(budget_data, target_month)

    return analysis, goals


def main():
    parser = argparse.ArgumentParser(
        description="Monarch Money budget pacing and goal progress tracker"
    )
    parser.add_argument(
        "--month", type=str, default=None,
        help="Target month in YYYY-MM format (default: current month)"
    )
    args = parser.parse_args()

    if args.month:
        try:
            datetime.strptime(args.month, "%Y-%m")
        except ValueError:
            print("Error: --month must be in YYYY-MM format", file=sys.stderr)
            sys.exit(1)

    analysis, goals = asyncio.run(run_budget_check(args.month))

    print_budget_report(analysis)
    print_goal_report(goals)


if __name__ == "__main__":
    main()
