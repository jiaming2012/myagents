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

## The Weekly Question (one command)

```bash
task week                  # next 7 days: timeline, per-bank-account funding verdict, card charges
task week -- --days 14     # same, two weeks out
```

`task week` answers "how much leaves each bank account this week, and does it
clear?" in one screen: the dated timeline with a running balance, then a
**FUNDING CHECK** row per bank account (balance and its as-of date, total due,
count, verdict: `OK`, `OK but below floor`, `SHORT by $x`, or `UNKNOWN balance`),
then anything charged to a credit card. Exit code is 0/1/2 for ok/warning/error.

Conventions the forecast relies on (all in `payments.yaml`):

- **`day_of_month` is the bank-side posting day**, not the card's due date. Card
  autopay lands at the bank 1-4 days after the card shows it (Amex says the
  16th; Recurring ACH 4354 sees it on the 17th). The point of the forecast is
  bank funding, so the registry records the day the money actually leaves.
- **`autopay_type: min` uses the `amount` in the file** (record the last
  observed minimum). Only `autopay_type: full` prices the payment from the
  card's live balance, and that balance includes post-statement charges, so it
  is an upper bound on the debit.
- **A credit card as `funding_account` is a card charge, not a bank draw.** It
  is listed under "Charged to credit cards" and excluded from bank totals; the
  card's own payment entry is what reaches a bank account.
- **Stale balances are labelled, never hidden.** A funding account whose Monarch
  connection stopped syncing shows `STALE Nd`; a full-payment amount priced
  from a dead card feed shows `[card bal. STALE Nd]`. An account no source can
  vouch for (e.g. a BoA account that is in neither Monarch nor Xero) is
  `UNKNOWN`, not `$0.00`.

## What's Due (no logins)

```bash
task due                   # next 7 days from payments.yaml -- deterministic, no Monarch/Xero/Zoho
task due:month             # next 30 days
```

`payments.yaml` is the source of truth. Balances are skipped, so the projected
columns are meaningless; the payment list and dates are exact.

## Account Sync Status (is the data behind the numbers current?)

```bash
task sync-status           # Monarch connections + Zoho bank feeds: who is disconnected, stale, or unfed
task sync-status:fresh     # same, bypassing the 30-minute cache
task sync-status:warn      # banner only (what every numbers command prints first)
task sync-status:gate      # flow step: exit 1 if a connection is broken; -- --fail-on warning is stricter
task sync-status -- --json # machine-readable
```

Monarch: a credential with `updateRequired` or a provider disconnect is an **error**
(one finding per connection, naming every account on it); an account not updated in
3 days is a **warning**. Manual, hidden, deactivated and sync-disabled accounts are
listed as "parked" and never warned about. Zoho Books: an active bank/card account
with no bank feed, a stale refresh (2 days), a book-vs-bank balance gap or open
uncategorized lines is a **warning**; MFA pending, consent expired or a failed
refresh is an **error**.

Every command that presents balances or charges (`week`, `forecast*`, `payments*`,
`bill-map*`, `report*`, `balances`, `budget`, `audit`, `rewards`, `zoho:recon`,
`zoho:reconcile`) runs `sync-status:warn` first, so a stale source is announced
before its numbers. `task health` embeds the same banner at the top of the terminal
report and the emailed HTML, and `forecast:email*` injects it above the summary.
Results are cached in `.sync_status_cache.json` (30 min, 5 min after an outage) so a
chain of tasks costs one Monarch login and one Zoho API call; `SYNC_STATUS_TTL_MINUTES`
overrides the lifetime. `--warn` never fails the command it precedes.

To use it as a step in a flow, call the gate before the work:

```yaml
  month-close:
    cmds:
      - task: sync-status:gate      # stop here if a feed is broken
      - task: zoho:recon:apply
      - task: zoho:reconcile
```

## Debt Paydown Plan (aggressive, without overdrafting)

```bash
task debt                              # owe / headroom / this month's move / outlook
task debt:plan -- --monthly-extra 600  # commit $600/month extra on top of minimums
task debt:goal -- --by 2028-06         # solve: monthly extra needed to be debt-free by then
task debt -- --snowball                # smallest balance first instead of highest APR
task debt -- --json                    # machine-readable
task debt:test                         # unit tests, no network
```

Balances are live: Monarch for personal debts (`monarch_match`), Zoho Books for business
ones (`zoho_match` on the registry account). `debts.yaml` adds what the sources do not
know: APR, minimum payment, funding account, and whether a card is carried. A debt
missing from `debts.yaml` still runs with an **assumed** APR (cards 24.99%, loans
9.99%) and is flagged until you fill it in. Cards whose registry payment is
`autopay_type: full` are "paid in full monthly" and stay out of the paydown unless
`carried: true`.

The "without overdrafting" part is section 2: for every depository funding account,
headroom = balance − every scheduled debit in the next 30 days (the same engine as
`task week`) − the account's `min_balance` floor. Positive headroom is pooled per
category (personal money pays personal debt, business pays business) and pointed at
the avalanche target; negative headroom is an autopay already at risk and is shown
with the debits that will bounce. Income is not modeled, so headroom is money you can
send this instant, not a forecast of next month. The outlook simulates minimums only
versus your plan, month by month, rolling each paid-off minimum into the next target,
and `--by` binary-searches the monthly extra a deadline needs.

## Registry Gaps (what the forecast can't see)

```bash
task gaps                  # live: Monarch + Xero + Zoho
task gaps:offline          # registry-only
task gaps:personal         # personal accounts/payments only (Monarch + Zoho)
task gaps:business         # business only (Xero + Zoho; Monarch only if mapped)
```

The `:personal` / `:business` views filter on `category:` in `payments.yaml`. A
payment with no `category` inherits one from `pays_account`, then
`funding_account`; anything still unresolved is reported as `uncategorized` so
it cannot hide from both views. Unknown Monarch liabilities count as personal.

Flags: credit/loan accounts without a payment entry, Monarch liabilities the
registry lacks, registry accounts that are closed or **stale** in Monarch (a
balance not updated in 7 days), payments with no calendar event, Monarch/Xero
outages, and Xero bank accounts with no transaction in 10 days. The same checks
run as section 5 of `task health`, so drift shows up in the Monday email.

`task health:schedule` installs a launchd agent that fires **daily** at 08:00
and at login, and `weekly_health.py --email --weekly-guard` sends only if the
last send was 6+ days ago (`.cache/health_last_sent`). This is deliberate:
launchd silently drops a calendar trigger missed while the Mac was powered off
(it only catches up after sleep), so a Monday-only trigger could skip a week.

Registry conventions that feed these checks:

- `pays_account: <account id>` on a payment entry links it to the credit card or
  loan it pays down (the name heuristic is the fallback).
- `inactive: true` on an account excludes it from every check (closed cards).
- `monarch_match` must be Monarch's exact display name; `®`/case differences are
  tolerated, but an old hidden duplicate connection is not -- point at the live one.

## Credit Card Rewards (deterministic)

```bash
task rewards               # all cards in rewards.yaml, current year, pulls Monarch, saves snapshot
task rewards:amex          # Amex Platinum only
task rewards:offline       # re-run from the last snapshot, no network
task rewards:json          # same, as JSON
task rewards:test          # unit tests, no network
```

`rewards.yaml` holds the rules; `rewards.py` applies them. Cards are keyed by
their `payments.yaml` account id, so the card itself is defined once. Per card:
an **ordered** list of earn rules (first match wins, last rule is the base
rate, optional spend caps per calendar period), the statement credits worth
tracking (amount + monthly/quarterly/semiannual/annual + merchant patterns),
an optional sign-up bonus, and rewards balances you type in from the issuer.

Each card opens with a **SUMMARY** (points earned and their dollar value,
credits used, total earned = points + credits, fee, net, and when the points
themselves expire per `points_expire`) and an **EXPIRING** block that lists
every unused enrolled credit grouped by deadline, soonest first, with days
left and the total dollars still on the table. Below that: points earned per
rule, each credit period as `used`, `partial`, `MISSED` or `open` (with days
left), sign-up bonus pace, and the expected balance now. With two typed-in balances it also shows the drift
between what the issuer actually granted and what the rules computed; that
drift is how you find merchants the issuer codes differently from Monarch.

Every live run writes `.rewards_snapshot.json`; `--offline` reuses it and
`--as-of YYYY-MM-DD` pins "today" so a report can be reproduced exactly.
Card payments and transfers are excluded by the `exclude:` block at the top of
`rewards.yaml`.

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
- `1` — warnings only (accounts below min_balance threshold, or a balance that
  could not be fetched: shown as `? BALANCE UNKNOWN ?`, never as a shortfall)
- `2` — errors (negative projected balance)

`--no-balances` always exits 0: without balances, severities mean nothing.

## Configuration

All payment and account configuration lives in `payments.yaml`:

- **accounts** — bank accounts with IDs, nicknames, balance source info, and optional `min_balance` thresholds
- **payments** — recurring bills with amounts, due dates, and funding account assignments
- **transfer_rules** — income hub and business routing rules
