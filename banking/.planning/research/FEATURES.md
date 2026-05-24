# Feature Landscape

**Domain:** Personal finance payment forecasting (CLI tool)
**Researched:** 2026-05-03

## Table Stakes

Features users expect from a bill-pay forecasting tool. Missing any of these and the tool feels broken or incomplete.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Bill-to-account mapping config | Core premise -- must know which debit account pays which bill | Low | New YAML file mapping `payment -> funding_account`. Many payments in `payments.yaml` already have `funding_account: null` so this is the #1 gap |
| Unified forecast CLI | Single command to see all upcoming obligations, balances, and projected outcomes | Medium | Combines Zoho Calendar dates + Monarch balances + mapping config. This is the main deliverable |
| Per-account projected balance | After-payment balance for each funding account across the time horizon | Low | `balance - sum(upcoming payments)` -- straightforward math once mapping exists |
| Shortfall detection and warnings | Flag when projected debits exceed available balance | Low | Already partially implemented in `coverage_report.py`; needs calendar-date awareness |
| Configurable time horizon | `--days 7`, `--days 30`, `--days 90` | Low | Pattern already exists in `coverage_report.py` with `--days` arg |
| Summary totals | Total outgoing, total available, net position across all accounts | Low | Aggregation of per-account data; provides the "am I OK?" headline |
| Autopay vs manual distinction | Show which payments are autopay (will hit regardless) vs manual (can defer) | Low | Data already exists in `payments.yaml` (`autopay: true/false`); display it prominently because autopay bills are non-negotiable cash drains |
| Chronological payment timeline | Show payments in date order, not grouped arbitrarily | Low | Users think in calendar time: "what's due next?", not "what's due on account X?" |
| Graceful handling of missing data | Unknown balances, unmapped accounts, missing Zoho matches should warn, not crash | Low | Existing code already does this for balance lookups; extend pattern to new features |

## Differentiators

Features that elevate the tool beyond a simple balance-minus-bills calculator. Not expected, but high value for a power user managing 30+ accounts.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Email alerts for projected shortfalls | "Know before payday" -- the core value prop delivered proactively via Gmail | Medium | PROJECT.md lists Gmail via MCP integration. Trigger: run forecast, if any shortfall, send email with details. Negative framing ("You will be short $X on Account Y by May 15") is proven more effective than neutral alerts |
| Daily scheduled forecast | Automated daily run that emails results without manual invocation | Low | Cron/systemd timer wrapping the CLI. Low complexity because it is just scheduling an existing command |
| Multi-window comparison | Side-by-side 7/14/30 day view showing how the picture changes over time | Low | Already half-built in `coverage_report.py`; apply same pattern to calendar-based forecast |
| Credit card statement balance vs minimum payment awareness | For autopay-full cards, use current balance as the payment amount; for autopay-min, use minimum payment | Medium | `payments.yaml` has `autopay_type: full/min` but amounts are static. For full-pay cards, the real amount is the current balance from Monarch. This matters because a $0 amount in YAML for an autopay-full card means the forecast underestimates outflows |
| Funding gap recommendations | "Transfer $X from Mercury Personal to BoA Checking to cover shortfall" | Medium | Uses `transfer_rules` from `payments.yaml` (personal_hub, business_hub, business_funding_source) to suggest specific transfers. Read-only suggestion, never executes |
| Calendar event-to-payment matching | Auto-match Zoho Calendar events to `payments.yaml` entries via `zoho_match` field | Medium | Bridge the two data sources: calendar knows dates, YAML knows amounts and accounts. Fuzzy matching on title prefix. This is the key integration that makes the tool more than two separate scripts |
| Personal vs business separation | Filter or section the report by personal/business category | Low | Data already tagged with `category: personal/business` in YAML. Useful because personal shortfalls and business shortfalls have different remedies |
| What-if scenario support | "What if I pay card X in full this month instead of minimum?" | High | PocketSmith's killer feature. For a CLI tool, this could be `--scenario pay-full:boa-plat1-5153` that overrides the payment amount for one run. Powerful but adds significant complexity |
| Historical tracking / trend view | Store past forecast runs, show whether your cash position is improving or declining | Medium | Write each run's summary to a log file (JSON lines), then `--history` flag to show trend. Not a database -- just append-only file |

## Anti-Features

Features to explicitly NOT build. These are scope traps that add complexity without serving the core "know before payday" value.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| Web dashboard / UI | Out of scope per PROJECT.md. CLI-first tool. Building a web UI turns a weekend project into a month-long project | Keep terminal output clean and readable. If visualization is ever needed, output CSV/JSON and use existing tools (Metabase, Excel) |
| Automatic payment execution | Tool is read-only forecasting, never moves money. Even "helpful" auto-transfers create liability and trust issues | Show transfer recommendations as text suggestions only |
| Transaction categorization / budgeting | That is Monarch Money's job. Duplicating categorization logic is a black hole of edge cases | Consume Monarch's balance data; don't try to replicate its transaction analysis |
| Multi-user support | Single user, personal finance tool. Auth, permissions, and user isolation are massive complexity for zero value | Hardcode single-user assumptions throughout |
| Real-time push notifications | Adds mobile app or webhook infrastructure. Email is sufficient for a daily summary tool | Gmail email alerts are the notification channel |
| ML-based spending prediction | Predicting variable spending requires transaction history analysis, model training, and ongoing tuning. Overkill for known recurring bills | Stick to declared recurring payments from YAML + calendar. The user already knows their bills |
| Plaid/bank-direct integration | Monarch Money already aggregates. Adding another aggregator creates duplicate connections and sync conflicts | Use Monarch as the single aggregation layer |
| Full accounting / double-entry ledger | The tool forecasts, it does not track actuals. Actuals live in Monarch and bank portals | Focus exclusively on forward-looking projections |
| Recurring payment auto-detection | Detecting patterns in transaction history to find recurring bills. Monarch and banks already do this | User maintains `payments.yaml` manually -- this is intentional for control and accuracy |

## Feature Dependencies

```
Bill-to-account mapping config
  |
  v
Unified forecast CLI  <--  Calendar event-to-payment matching (Zoho dates)
  |                              |
  |                              v
  +--> Per-account projected balance
  |         |
  |         v
  |    Shortfall detection
  |         |
  |         +--> Email alerts (requires shortfall detection output)
  |         |
  |         +--> Funding gap recommendations (requires shortfall + transfer_rules)
  |
  +--> Summary totals
  |
  +--> Chronological timeline view
  |
  +--> Personal vs business separation

Credit card statement balance awareness
  |
  +--> Requires: Monarch balance lookup for credit accounts
  +--> Requires: autopay_type field in payments.yaml (already exists)

What-if scenarios
  +--> Requires: Unified forecast CLI (fully working baseline first)

Daily scheduled forecast
  +--> Requires: Email alerts (so the scheduled run has something to send)

Historical tracking
  +--> Requires: Unified forecast CLI (output format must be stable before logging)
```

## MVP Recommendation

**Phase 1: The Mapping and Forecast Core**

Prioritize these in order -- each enables the next:

1. **Bill-to-account mapping config** -- Fill in all `funding_account: null` entries in `payments.yaml` or create a dedicated mapping YAML. Without this, nothing else works.
2. **Calendar event-to-payment matching** -- Connect Zoho Calendar dates to `payments.yaml` amounts. This is what makes it a *forecaster* rather than two disconnected scripts.
3. **Unified forecast CLI** -- Single `task forecast` command that outputs: upcoming payments in date order, per-account projected balances, and shortfall warnings.
4. **Per-account projected balance** -- balance minus upcoming payments within the horizon.
5. **Shortfall detection** -- Flag negative projected balances.
6. **Summary totals** -- Headline numbers: total outgoing, total available, net.

**Phase 2: Alerts and Automation**

7. **Email alerts** -- Gmail notification when shortfalls detected.
8. **Daily scheduled run** -- Cron job that runs forecast and emails results.
9. **Funding gap recommendations** -- "Transfer $X from Y to Z" suggestions.

**Defer:**

- **What-if scenarios**: High complexity, low urgency. The user needs to see their real situation first. Add after the core is proven useful.
- **Historical tracking**: Requires stable output format. Add after the CLI output format settles.
- **Credit card dynamic balance**: Medium complexity integration. Add once the basic forecast is validated -- otherwise you are optimizing accuracy before the core loop works.

## Complexity Budget

Estimated effort for MVP (Phase 1 features):

| Feature | Estimated Effort | Risk |
|---------|-----------------|------|
| Bill-to-account mapping | 1-2 hours | Low -- mostly data entry in YAML |
| Calendar-payment matching | 2-4 hours | Medium -- fuzzy matching on event titles needs testing |
| Unified forecast CLI | 4-6 hours | Medium -- integrating three data sources |
| Projected balance | 1 hour | Low -- arithmetic |
| Shortfall detection | 1 hour | Low -- comparison |
| Summary totals | 30 minutes | Low -- aggregation |

Total Phase 1: roughly 10-15 hours of focused work.

## Sources

- [PocketSmith Cash Flow Forecasts](https://www.pocketsmith.com/tour/cash-flow-forecasts/) -- calendar-based daily balance projection, what-if scenarios, 30-year forecast horizon
- [PocketSmith Scenarios](https://learn.pocketsmith.com/article/1248-everything-you-need-to-know-about-scenarios-in-pocketsmith) -- secondary scenario modeling for financial decisions
- [Quicken Best Personal Finance Software 2026](https://www.quicken.com/blog/best-personal-finance-software-for-cash-flow-and-expense-tracking/) -- cash flow projection as standard feature, 12-month forward view
- [Monarch Money Review 2026](https://www.thepennyhoarder.com/budgeting/monarch-money-review/) -- cash flow projection on Core plan, advanced forecasting on Plus plan
- [Copilot Money Forecasting Feature Request](https://copilot.canny.io/feature-requests/p/forecasting-1) -- community demand for forecasting shows it is a gap in many tools
- [YNAB vs Monarch vs Copilot Comparison](https://aicashcaptain.com/ynab-vs-monarch-vs-copilot/) -- YNAB deliberately avoids forecasting, focusing on active budgeting instead
- [AI Overdraft Prediction Research](https://www.meniga.com/resources/ai-in-overdraft-protection/) -- negative framing in alerts reduces overdrafts by 9%
- [Bill Tracker Software Best Practices](https://moneypatrol.com/moneytalk/budgeting/bill-tracker-software-set-up-reminders-that-prevent-late-fees/) -- 3-5 day advance reminders prevent late fees
- [CalendarBudget Top Finance Software 2026](https://calendarbudget.com/top-personal-finance-software-best-budgeting-apps-for-2026/) -- calendar-view forecasting as emerging standard
