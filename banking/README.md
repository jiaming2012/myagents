# Banking Payment Forecaster

Never miss a payment or finance charge. This tool pulls upcoming bills from Zoho Calendar, fetches real-time balances from Monarch Money (personal) and Xero (business), and projects per-account balances forward to flag shortfalls before they happen.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in Zoho, Monarch, and Xero credentials in .env
```

## Quick Start

```bash
task forecast              # 30-day forecast (default)
task forecast:week         # 7-day forecast
task forecast:month        # 30-day forecast
task forecast -- --days 14 # Custom range
task forecast -- --timeline # Chronological view
```

## How It Works

1. Reads payment schedules from `payments.yaml`
2. Fetches live balances from Monarch Money and Xero
3. Projects each funding account's balance forward through scheduled payments
4. Flags shortfalls (negative balance = ERROR, below threshold = WARNING)

## Setting Up Zoho Calendar Events

Each bill needs a Zoho Calendar event so the system knows when payments are due and which account pays them.

### Step 1: Create a Recurring Calendar Event

Create a monthly recurring event in Zoho Calendar for each bill.

**Event title format:** `Name - $Amount`

Examples:
- `Quickbooks - $38`
- `Progressive - $450.10`
- `Rent - $1000`

For variable-amount payments (credit cards paid in full), use `$0`:
- `BoA Visa Platinum - $0`

### Step 2: Add the Notes Field

The event **notes/description** field tells the system which account pays this bill.

**Notes format:** `Fund: <account nickname>`

Examples:
- `Fund: BoA Business`
- `Fund: Cap1 Recurring`
- `Fund: Chase Ink`

You can add optional fields separated by `|`:
- `Fund: Cap1 Recurring | Source: BoA Plat 1 | VARIABLE` — for credit card payments where the amount comes from the source account's live balance

**Special cases:**
- `NONE` or `N/A` — marks the event as informational (no funding account needed)

### Step 3: Account Nicknames

The `Fund:` value is matched against account nicknames defined in `payments.yaml`. Each account has a list of aliases that work:

| Account | Example Nicknames |
|---------|-------------------|
| Mercury Personal Checking | `Mercury Personal`, `Mercury 6343`, `Mercury Checking` |
| 360 Checking (Recurring ACH) | `Cap1 Recurring`, `Cap1 4354`, `Capital One 4354` |
| Adv Plus Banking (BoA) | `BoA Checking`, `BoA 2803`, `Bank of America 2803` |
| Business Adv Fundamentals (BoA) | `BoA Business`, `BoA 1778`, `BoA Biz` |
| Chase Ink | `Chase 7667`, `Ink 7667`, `Chase Ink`, `Ink` |
| EveryDay Checking (NavyFed) | `NavyFed Checking`, `NavyFed 7909`, `NavyFed` |

See `payments.yaml` → `accounts` → `nicknames` for the full list.

### Step 4: Add to payments.yaml

Each payment also needs an entry in `payments.yaml` under the `payments:` section with a `funding_account` set to the account ID (not the nickname):

```yaml
- name: "Quickbooks"
  amount: 38.00
  day_of_month: 11
  funding_account: chase-ink-7667    # account ID from accounts section
  autopay: true
  autopay_type: null
  category: business
  zoho_match: "Quickbooks"
```

If any payment has `funding_account: null`, the forecast will refuse to run and list what needs to be filled in, along with all available deposit account IDs.

### Example: Complete Setup for a New Bill

Say you get a new $50/month subscription billed on the 15th, paid from your Capital One recurring checking:

1. **Zoho Calendar:** Create monthly recurring event on the 15th
   - Title: `NewService - $50`
   - Notes: `Fund: Cap1 Recurring`

2. **payments.yaml:** Add under `payments:`
   ```yaml
   - name: "NewService"
     amount: 50.00
     day_of_month: 15
     funding_account: cap1-recurring-4354
     autopay: true
     autopay_type: null
     category: personal
     zoho_match: "NewService"
   ```

3. **Verify:** Run `task forecast` — the new bill should appear under the Capital One account section.

## Other Commands

```bash
task payments              # List upcoming Zoho Calendar payment events (7 days)
task payments:month        # Next 30 days of calendar events
task bill-map              # Bill-to-account mapping with balances
task report                # Coverage report across all accounts
task balances              # Monarch Money account balances
```

## Exit Codes

The forecast uses exit codes for scripting:
- `0` — all clear, no issues
- `1` — warnings only (accounts below min_balance threshold)
- `2` — errors (negative projected balance)

## Configuration

All payment and account configuration lives in `payments.yaml`:

- **accounts** — bank accounts with IDs, nicknames, balance source info, and optional `min_balance` thresholds
- **payments** — recurring bills with amounts, due dates, and funding account assignments
- **transfer_rules** — income hub and business routing rules
