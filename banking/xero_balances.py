#!/usr/bin/env python3
"""Fetch business account balances from Xero Accounting API.

Uses the xero-python SDK with file-based OAuth2 token persistence.
Token management pattern copied from ~/projects/yumyums/accounting/xero/app.py.

Usage:
    python xero_balances.py              # Fetch and display Xero balances
"""

import json
import os
import sys
from pathlib import Path

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
    from xero_python.api_client import ApiClient, Configuration
    from xero_python.api_client.oauth2 import OAuth2Token
    from xero_python.accounting import AccountingApi
    from xero_python.identity import IdentityApi
except ImportError:
    AccountingApi = None

# --- Constants ---

CLIENT_ID = os.getenv("XERO_CLIENT_ID")
CLIENT_SECRET = os.getenv("XERO_CLIENT_SECRET")
TOKEN_URL = "https://identity.xero.com/connect/token"
TOKEN_FILE = Path(__file__).parent / ".xero_token.json"


# --- Token persistence (copied from yumyums pattern) ---


def load_token():
    """Load OAuth2 token from file."""
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    return None


def save_token(token):
    """Save OAuth2 token to file."""
    TOKEN_FILE.write_text(json.dumps(token))


def refresh_token(token):
    """Refresh the OAuth2 access token using the refresh token.

    Raises on failure -- if the refresh token is expired (60 days of non-use),
    the user must re-authorize via the yumyums Flask app.
    """
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code == 400:
        error_data = response.json() if response.text else {}
        error_type = error_data.get("error", "unknown")
        if error_type == "invalid_grant":
            raise RuntimeError(
                "Xero refresh token expired. Re-authorize via yumyums Flask app: "
                "cd ~/projects/yumyums/accounting/xero && python app.py"
            )
    response.raise_for_status()
    new_token = response.json()
    save_token(new_token)
    return new_token


# --- Balance fetching ---


def _parse_bank_summary_report(report):
    """Parse a Xero ReportWithRows (Bank Summary) into a balances dict.

    The Bank Summary report has sections (rows with RowType 'Section'),
    each containing sub-rows. Account name is in the first cell,
    closing balance in the last cell.

    Returns: {account_name: {"balance": float, "type": str}}
    """
    balances = {}
    if not report or not hasattr(report, "reports") or not report.reports:
        return balances

    for rpt in report.reports:
        if not hasattr(rpt, "rows") or not rpt.rows:
            continue
        for row in rpt.rows:
            row_type = getattr(row, "row_type", None)
            title = getattr(row, "title", "") or ""

            # Determine account type from section title
            title_lower = title.lower()
            if "credit" in title_lower or "card" in title_lower:
                acct_type = "CREDITCARD"
            else:
                acct_type = "BANK"

            # Section rows contain the actual account data rows
            row_type_val = row_type.value if hasattr(row_type, "value") else row_type
            if row_type_val == "Section" and hasattr(row, "rows") and row.rows:
                for data_row in row.rows:
                    data_row_type = getattr(data_row, "row_type", None)
                    data_row_type_val = data_row_type.value if hasattr(data_row_type, "value") else data_row_type
                    if data_row_type_val != "Row":
                        continue
                    cells = getattr(data_row, "cells", []) or []
                    if len(cells) < 2:
                        continue
                    account_name = getattr(cells[0], "value", None)
                    closing_balance_str = getattr(cells[-1], "value", None)
                    if not account_name or closing_balance_str is None:
                        continue
                    try:
                        balance = float(str(closing_balance_str).replace(",", ""))
                    except (ValueError, TypeError):
                        continue
                    balances[account_name] = {
                        "balance": balance,
                        "type": acct_type,
                    }

    return balances


def fetch_xero_balances():
    """Fetch bank + credit card balances from Xero.

    Returns: {account_name: {"balance": float, "type": str}}
    Returns empty dict on any failure (graceful degradation).
    """
    if AccountingApi is None:
        print(
            "Warning: xero-python not installed, skipping Xero balance lookup",
            file=sys.stderr,
        )
        return {}

    if not CLIENT_ID or not CLIENT_SECRET:
        print(
            "Warning: XERO_CLIENT_ID/SECRET not set, skipping Xero",
            file=sys.stderr,
        )
        return {}

    token = load_token()
    if not token:
        print(
            "Warning: No Xero token found. Run yumyums Flask app to authorize.",
            file=sys.stderr,
        )
        return {}

    # Proactively refresh token (access tokens expire in 30 min)
    try:
        token = refresh_token(token)
    except Exception as e:
        print(f"Warning: Xero token refresh failed: {e}", file=sys.stderr)
        return {}

    # Initialize SDK
    try:
        api_client = ApiClient(
            Configuration(
                oauth2_token=OAuth2Token(
                    client_id=CLIENT_ID, client_secret=CLIENT_SECRET
                )
            ),
            oauth2_token_getter=load_token,
            oauth2_token_saver=save_token,
        )
        api_client.set_oauth2_token(token)

        # Get tenant ID
        identity_api = IdentityApi(api_client)
        connections = identity_api.get_connections()
        if not connections:
            print("Warning: No Xero tenants connected.", file=sys.stderr)
            return {}
        tenant_id = connections[0].tenant_id

        # Fetch bank summary report (includes bank accounts and credit cards)
        accounting_api = AccountingApi(api_client)
        report = accounting_api.get_report_bank_summary(xero_tenant_id=tenant_id)

        # Parse the report into balances dict
        balances = _parse_bank_summary_report(report)

        if not balances:
            print(
                "Warning: No balances found in Xero Bank Summary report. "
                "Check that accounts exist and accounting.reports.read scope is authorized.",
                file=sys.stderr,
            )

        return balances

    except requests.exceptions.HTTPError as e:
        if hasattr(e, "response") and e.response is not None and e.response.status_code == 401:
            # Token may have been invalidated -- try one more refresh
            try:
                token = refresh_token(token)
                api_client.set_oauth2_token(token)
                report = accounting_api.get_report_bank_summary(
                    xero_tenant_id=tenant_id
                )
                return _parse_bank_summary_report(report)
            except Exception as retry_err:
                print(
                    f"Warning: Xero API retry after 401 failed: {retry_err}",
                    file=sys.stderr,
                )
                return {}
        print(f"Warning: Xero API HTTP error: {e}", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"Warning: Xero API error: {e}", file=sys.stderr)
        return {}


# --- Standalone mode ---


def main():
    """Fetch and display Xero balances."""
    balances = fetch_xero_balances()
    if not balances:
        print("No Xero balances retrieved.")
        return

    print(f"\nXero Balances ({len(balances)} accounts):")
    print("-" * 60)
    for name, data in sorted(balances.items()):
        balance = data["balance"]
        acct_type = data["type"]
        print(f"  {name:<40} ${balance:>12,.2f}  ({acct_type})")
    print("-" * 60)


if __name__ == "__main__":
    main()
