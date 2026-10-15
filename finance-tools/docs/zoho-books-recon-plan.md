# Zoho Books × Mercury reconciliation — one-shot plan

**Status:** plan, 2026-10-15. Written for the agent that will run the trial; the operator
(Jamal) wants Mercury reconciled in Zoho Books with as little input from him as possible.
If the trial is painless, Zoho Books replaces Xero. Nothing here touches Xero.

**Correctness bar (same as Xero):** books only need to be right from **2026-01-01**.

**Scope of the one shot:** the three Mercury business accounts of Yumyums Kitchen LLC
(4613 Operations, 5396 Outgoing ACH, 7818 Profit). Other banks (BoA, LFCU, Wells Fargo,
Cap1, NavyFed, Chase Ink, Mercury personal) are phase 2 and reuse the same mechanism.

---

## 0. What is already in hand (do not re-create)

| Thing | Where | Note |
|---|---|---|
| Zoho OAuth client (API console) | `finance-tools/.env` → `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET` | Used today by `zoho_calendar_payments.py`; same client can mint a Books token |
| Zoho datacenter | US | token URL `https://accounts.zoho.com/oauth/v2/token` → API root `https://www.zohoapis.com/books/v3` |
| Mercury API token | `MERCURY_API_TOKEN` in `~/projects/yumyums/accounting/xero/.env` (read as fallback by `mercury_precode.py`) | the `MERCURY_BUSINESS/PERSONAL_API_KEY` pair in `finance-tools/.env` is dead (401) |
| Mercury account ids | `finance-tools/merchants.yaml` → `accounts:` | 4613 `9553aae6-…`, 5396 `739dbde6-…`, 7818 `955515ac-…` |
| Classification rules | `finance-tools/merchants.yaml` → `skip:`, `transfers:`, `merchants:` | pattern → Xero account **code**; reuse verbatim, only the target id changes |
| Mercury fetch + classify code | `finance-tools/mercury_precode.py` (`fetch_mercury`, `classify`, `merchant_name`) | copy the functions, swap the Xero sink for a Zoho sink |
| Python interpreter | `PATH=~/miniconda3/bin:$PATH` | the bare `/usr/local/bin/python3` is 3.7 with no deps; every `task` target fails there |
| **Books access (DONE 2026-10-15)** | `finance-tools/.env` → `ZOHO_BOOKS_REFRESH_TOKEN` (scope `ZohoBooks.fullaccess.all`), `ZOHO_BOOKS_ORG_ID=942990573` | org "Yumyums Kitchen LLC", plan PREMIUM TRIAL (10k calls/day), USD. §1 is complete; start at §2 |
| Zoho bank accounts (feed active, last refresh 2026-10-15) | `1622286000000095122` Operations ••4613 (530 uncategorized) · `1622286000000095128` Outgoing ACH ••5396 (120) · `1622286000000095134` Taxes ••7818 (136) | book `balance` 0.00 on all three (nothing categorized yet, no opening balance); `bank_balance` 528.43 / 450.18 / 239.29 |
| **Feed coverage (verified 2026-10-15)** | operator set the feed start to **2026-01-01**; first feed line is 2026-01-02 (Mercury's first 2026 posting); uncategorized counts **530 / 120 / 136 equal Mercury's posted-txn counts 2026-01-01→today exactly** | the whole of 2026 is already in Zoho as feed lines; §3.3 is a verification, not an import. Operator goal: all of 2026 worked out in Zoho Books |
| Feed line shape | `GET /banktransactions?account_id=…&filter_by=Status.Uncategorized` → `transaction_id` (the id used in categorize/match URLs), `date`, `amount`, `debit_or_credit`, `payee` (clean merchant, e.g. "Restaurant Depot"), `description` ("RESTAURANT DEPOT; Merchant name: Restaurant Depot" / "Percentage-based rule auto-transfer; Merchant name: Mercury Checking ••0551"), `reference_number` empty, `is_feed: true` | **`Status.All` and the unfiltered list return zero feed lines** — always filter `Status.Uncategorized` (or `status=uncategorized`, or `GET /banktransactions/uncategorized`). `debit_or_credit` follows the ledger: **credit = money out, debit = money in**. Mercury's txn id is not carried; match Mercury↔feed on date + amount + merchant name |

---

## 1. Obtain API access (one 2-minute operator action)

Zoho refresh tokens are **scope-bound and never rotate**. The Calendar refresh token will
not work for Books; mint a second one and keep both.

1. Operator opens https://api-console.zoho.com, picks the existing client.
   - If it is a **Self Client**: *Generate Code* tab → scope below → validity 10 min →
     copy the grant code.
   - If it is a **Server-based** client: visit
     `https://accounts.zoho.com/oauth/v2/auth?response_type=code&client_id=<ID>&scope=<SCOPE>&redirect_uri=<registered>&access_type=offline&prompt=consent`
     and copy the `code` from the redirect URL (valid ~2 minutes).
2. Agent exchanges it once:
   ```
   POST https://accounts.zoho.com/oauth/v2/token
     grant_type=authorization_code client_id=… client_secret=… code=… [redirect_uri=…]
   ```
   Save `refresh_token` as **`ZOHO_BOOKS_REFRESH_TOKEN`** in `finance-tools/.env`.
3. Fetch `organization_id`: `GET /organizations` → save as **`ZOHO_BOOKS_ORG_ID`**.
   Every Books call takes `?organization_id=…` and header `Authorization: Zoho-oauthtoken <access>`.

**Scope:** `ZohoBooks.fullaccess.all` for the trial (one string, nothing to debug).
Granular equivalent if the operator prefers least privilege:
`ZohoBooks.banking.ALL,ZohoBooks.accountants.ALL,ZohoBooks.settings.ALL,ZohoBooks.contacts.ALL,ZohoBooks.expenses.ALL`.

**Token hygiene:** access tokens last 1 h; cache one for 55 min (the Calendar script's
cache pattern) — Zoho throttles access-token minting per refresh token.

**Fallback if the console is awkward:** create a brand-new Self Client in the console for
Books only. Nothing else depends on it.

---

## 2. Inspect the API (read-only, ~15 calls, no operator)

Run these and write the result to `finance-tools/docs/zoho-books-inventory.md`:

| Call | Why |
|---|---|
| `GET /organizations` | org id, plan name (→ daily API budget, §6), fiscal year start, base currency |
| `GET /bankaccounts` | which Mercury accounts the operator linked; per account: `account_id`, `is_feeds_active`, `feeds_last_refresh_date`, `balance` (book), `bank_balance` (feed), `uncategorized_transactions` |
| `GET /bankaccounts/{id}/statement/lastimported` | earliest/latest feed line → the **feed start date** (Zoho feeds pull ≤90 days of history) |
| `GET /banktransactions?account_id=…&filter_by=Status.Uncategorized&per_page=200&page=n` | every feed line waiting; keep `imported_transaction_id`, `date`, `amount`, `debit_or_credit`, `payee`, `description`, `reference_number` |
| `GET /banktransactions?account_id=…&filter_by=Status.All` | anything already categorized/matched by the operator during setup |
| `GET /chartofaccounts` | existing accounts; find the Mercury bank accounts' ledger ids and whether Xero-style codes (5000, 6350, 3200 …) exist |
| `GET /bankaccounts/rules` | existing bank rules (should be none) |
| `GET /contacts?contact_type=vendor` | existing vendors (should be none) |

Decide from this inventory:
- **feed_start** per account. Already known: 2026-01-02 on all three, feed set to start
  2026-01-01 — so nothing needs importing unless §3.3's diff finds a hole.
- whether the chart needs seeding (§3.2).

Feed line text (verified): `payee` is the clean merchant name and `description` ends in
`Merchant name: <Mercury counterparty>`, including the `••0551`-style nicknames for
internal transfers, so `merchants.yaml` patterns hit against `description` unchanged.

---

## 2a. Spike S-1 — prove the Toast payout split in Zoho (before any bulk write)

The operator wants Toast payouts broken out exactly as toast_xero does in Xero (sales,
sales tax, tips, Toast fees, unpaid orders → 1310, customer deposits → 2050, clearing
1320). Before the one-shot run, prove on **two real payouts** that Zoho can hold that
split and still reconcile the feed line. The spike is throwaway; only its findings
survive, in `finance-tools/docs/zoho-books-spike-toast-split.md`.

**Pick the two payouts.** Both inside the feed window (so no statement import is
needed), both untouched in Zoho (if the operator already split two by hand in the UI,
leave those alone and use them as the reference for line shapes):
1. a clean single-day payout (one Mercury credit = one Toast sales day);
2. a **split payout** (one sales day paid as two Mercury credits, e.g. the 8/21
   $402.58 + $16.24 pattern) — the case that needed the clearing account in Xero.

**Split amounts** come from toast_xero, not Mercury: run its parser over the exports in
`~/projects/yumyums/accounting/xero` `inputs/` for those sales days (or read the line
items of the BankTransaction it already posted to Xero for that payout — same numbers,
Xero org is reconnected as of 2026-10-15). Reuse its account codes verbatim.

**Approach A — journal matched to the feed line (try first).**
1. Seed only the accounts the two payouts need (`POST /chartofaccounts`, same codes as
   Xero: Sales, Sales Tax Payable, Tips Payable, Toast fees, 1310, 2050, 1320).
2. `POST /journals` per Mercury credit: debit the Mercury bank account for the net
   payout; credit Sales / Sales Tax Payable / Tips Payable; debit Toast fees; 1310/2050
   lines as toast_xero would post; for the split payout, each credit gets its own journal
   and the sales-day totals route through 1320 so the cluster nets 0.00 (toast_xero's
   per-event deposit rule). `reference_number = "toast:<payout-id>"`.
3. `GET /banktransactions/uncategorized/{feed_line_id}/match` — **the spike's key
   question: does the journal appear as a candidate?** If yes,
   `POST …/match {transactions_to_be_matched:[{transaction_id:<journal>, transaction_type:"journal"}]}`.
4. Verify: feed line status `matched`; `GET /bankaccounts/{id}` book balance unchanged
   vs Mercury; Balance Sheet shows the tax and tips liabilities; P&L shows sales and fees.
5. Reversibility: `POST …/unmatch`, `DELETE /journals/{id}` → line is uncategorized again.

**Approach B — clearing account, always works (fallback, and arguably cleaner).**
Bank side: categorize the feed line as a `deposit` to **1320 Toast Clearing** (pure
Mercury-driven, fits the one shot). Revenue side: `POST /journals` per sales day
*from 1320* to Sales / tax / tips / fees / 1310 / 2050. The journal never touches the
bank account, so matching is not involved and split payouts need no special case; the
check is simply that 1320 nets to 0.00 per sales-day cluster. Verify the same reports.

**Pass criteria (any one approach):** both payouts reconciled, Mercury balance equality
holds, liabilities and revenue land on the right accounts, every write reversible by
API, and the per-payout call count is known (budget for §6). Record which approach
passed; that becomes the Toast row in §3.4 and the default for O-1.

**Fail:** neither approach matches or the reports are wrong → stop, report, operator
decides between the clearing deposit without split (trial only) and abandoning Zoho.

---

## 3. Reconcile in one shot — `finance-tools/zoho_books_recon.py`

One script, `--dry-run` default, `--apply` to write. Mirrors `mercury_precode.py`
(same `merchants.yaml`, same classifier) with a Zoho sink. Every write is idempotent via
`reference_number = "mercury:<mercury-txn-id>"`.

### 3.1 Independent truth: Mercury
For each account in `merchants.yaml`:
- `GET /api/v1/accounts` → `currentBalance` now.
- `GET /api/v1/account/{id}/transactions?start=2025-12-01&end=today&limit=500&offset=…`
  keep `status == "sent"` (posted). Fields: `id`, `postedAt`, `amount`, `counterpartyName`,
  `counterpartyNickname`, `bankDescription`.
- **Opening balance 2026-01-01** = `currentBalance − Σ amount(postedAt ≥ 2026-01-01)`.
  Cross-check against the Xero anchor work (4613 anchor was $14,099.20 on 2025-12-31;
  5396 had a +1,863.83 gap in Xero that was never anchored — the Mercury-derived number
  is the truth here).
- **Month-end balances** Jan→Sep 2026 by the same subtraction (needed for §3.6).

### 3.2 Seed the chart of accounts (idempotent, once)
Create in Zoho every account code that `merchants.yaml` references, with the same code
and name as Xero (`POST /chartofaccounts` with `account_code`, `account_name`,
`account_type`): 5000 COGS, 5200 COGS-Supplies, 6350 Supplies, 6430 Utilities, 6360 /
6450 payroll, 6240 reimbursements, 3200 Owner's Draws, 3100 Owner's Equity, plus a
**`1320 Toast Clearing`** (other current asset) — see §3.4. Skip any that already exist
(match on `account_code`). Build `code → account_id`.

Set each Mercury bank account's **opening balance** as of 2025-12-31 to the §3.1 number
(`PUT /bankaccounts/{id}` opening-balance fields, or the Opening Balances settings
endpoint — confirm in step 2 which one the org exposes).

### 3.3 Verify feed coverage against Mercury (import only if a hole is found)
The feed already covers 2026-01-01 → today with counts equal to Mercury's. The job here
is a **pairing diff**, which the later steps also depend on: for each account, pair
every Mercury posted txn with exactly one feed line by (date, |amount|, money-in/out,
then merchant name in `description`); allow ±1 day only when an exact-date pair is
absent. The pairing table (`mercury_id → zoho transaction_id`) is the backbone of
§3.4 and the checkpoint file. Expected result: 0 unpaired on both sides.

If a hole does appear, fill it with `POST /bankstatements` (Xero had no such API):
```json
{ "account_id": "...", "start_date": "2026-01-01", "end_date": "<feed_start-1>",
  "transactions": [ { "transaction_id": "<mercury id>", "date": "YYYY-MM-DD",
      "debit_or_credit": "debit|credit", "amount": 123.45,
      "payee": "<counterparty>", "description": "<bankDescription>",
      "reference_number": "mercury:<id>" } ] }
```
`debit_or_credit` uses the ledger convention (credit = money out). **Never import a
Mercury txn that already has a feed line.** Any feed line with no Mercury twin, or
Mercury txn with no feed line, goes to the report as a *feed gap* (a go/no-go signal
for the switch).

Result: every Mercury posted txn since 2026-01-01 is paired with exactly one Zoho line.
Feed lines carry no reference, so the Mercury id goes onto the *created* document
(expense / transfer / journal) as `reference_number mercury:<id>` — that is what makes
re-runs idempotent.

### 3.4 Categorize every line (the "reconcile clicks")
Walk accounts in `merchants.yaml` order (4613 first), lines oldest → newest. Decision per
line, exactly the `mercury_precode.py` classifier:

| Class | Zoho call | Notes |
|---|---|---|
| **Internal transfer** (`transfers:` map, both ends Mercury) | first side seen: `POST /banktransactions/uncategorized/{id}/categorize` body `{transaction_type:"transfer_fund", from_account_id, to_account_id, date, amount, reference_number}` — the other side: `GET …/{id}/match` → pick candidate whose `reference_number`/amount/date equal → `POST …/{id}/match` `{transactions_to_be_matched:[{transaction_id, transaction_type:"transfer_fund"}]}` | Dedupe exactly like precode: a transfer seen from both sides is created once, matched once |
| **Transfer to own BoA** (`Yumyums Kitchen LLC`) | `transfer_fund` to the BoA bank account if it exists in Zoho, else to a `BoA clearing` asset | phase 2 links BoA and matches the other side |
| **Spend** (`merchants:` hit) | `POST …/categorize/expenses` `{account_id:<code map>, date, amount, paid_through_account_id:<bank>, reference_number, description, [vendor_id]}` | vendor optional; create vendors (`POST /contacts`) only if the operator wants vendor reports |
| **Owner draw** (`Jamal Cole` → 3200) | `categorize` with `transaction_type:"owner_drawings"` | personal-Mercury `••5237` transfers likewise |
| **Toast payout** (credit, pattern TOAST) | whichever approach passed spike S-1 (§2a): A = journal per Mercury credit + match; B = `deposit` to **1320 Toast Clearing** + journal per sales day from 1320 | split amounts from the toast_xero parser over `inputs/`; payouts whose Toast export is missing get the bank-side deposit only and are listed for `task toast:export` |
| **Refund / credit from a merchant** | `categorize` `expense_refund` to the merchant's code | |
| **Unmatched** | leave uncategorized; list in report with suggested pattern | same as precode's review list; operator answers once, rerun |

Order matters: categorize transfers on the 4613 pass; on the 5396/7818 passes try
**match before categorize** for every credit/debit whose counterparty is a Mercury
nickname, so the transfer is never created twice.

### 3.5 Bank rules for the future (optional in the one shot, big win for the switch)
`POST /bankaccounts/rules` one rule per `merchants:` pattern (criteria: description/payee
contains pattern → expense to account_id; apply to all Mercury accounts). Rules fire on
future feed imports, so the Monday automation becomes "categorize what rules missed".
Do this only after the operator approves the merchant list (it is the same list that
already governs Xero, so approval is implicit; just report it).

### 3.6 Verify and close
Per account, print:

| Check | Pass condition |
|---|---|
| `uncategorized_transactions` | 0, except the §3.4 review list |
| Zoho `balance` (book) vs Mercury `currentBalance` | equal to the cent |
| Zoho `bank_balance` (feed) vs Mercury `currentBalance` | equal; if not, feed lag — note date |
| Mercury txn count 2026-01-01→today vs Zoho lines (All statuses) | equal; list the diff |
| Monthly `POST /bankaccounts/{id}/reconciliations` Jan→Sep 2026 | `{start_date, end_date, closing_balance:<Mercury month-end>, save_option:"reconcile", transactions_to_be_reconciled:[…ids in month…]}` — Zoho's difference must be 0.00 |

Write the result to `finance-tools/docs/zoho-books-recon-report.md` and (if `--email`)
send via `alert_email.py` like the weekly agent.

---

## 4. Execution order (what "one shot" looks like)

```
[op]    grant code                                  2 min
[agent] token exchange, org id, inventory (§1-2)    ~10 min, read-only
[agent] spike S-1: two Toast payouts split+matched  ~30 min; writes a findings doc; stops on fail
[op]    read spike findings (approach A or B)       or "stop"
[agent] zoho_books_recon.py --dry-run               prints: imports planned, categorize plan,
                                                    unmatched merchants, feed gaps, balance math
[op]    one reply: O-1 Toast treatment, O-2 any "unmatched" patterns (or "proceed")
[agent] zoho_books_recon.py --apply                 chart seed → opening balances → statement
                                                    imports → categorize/match → rules → verify
[agent] report + reconciliation records             ~1 h wall clock incl. rate limits
```
Everything the agent does is reversible by API: `uncategorize`, `unmatch`, delete the
imported statement, delete created accounts. No Xero state is touched.

---

## 5. Operator decisions (asked once, in the dry-run reply)

- **O-1 Toast payouts.** Default = the full split (sales / sales tax / tips / fees /
  1310 / 2050) via the approach spike S-1 proved. Only if the spike fails does the
  operator choose between a plain deposit to `1320 Toast Clearing` (trial only) and
  stopping.
- **O-2 Unmatched merchants** — the dry-run prints them with a suggested `merchants.yaml`
  line each; answer or say "leave uncategorized".
- **O-3 Vendors** — create vendor contacts from merchant names (yes/no). Default no.

---

## 6. Risks and budget

| Risk | Handling |
|---|---|
| Rate limits: 100 req/min/org; **daily cap on this org is 1,000 calls (error code 45), verified 2026-10-15 — the Premium trial does not get Premium's 10,000**. The full run needs ~1,250 calls (786 lines: 1 per spend, 3 per transfer pair, 3 per Toast payout) plus ~250 for inventory/rules/reconciliation | the script stops cleanly on code 45 and resumes from its checkpoint next day; after the catch-up a normal week is ~60 calls | throttle 90/min, back off on 429 (codes 44/45/1070), checkpoint progress in a local JSON so `--apply` resumes; on a Free plan split across two days |
| Feed coverage | verified 2026-01-02 → today, counts equal Mercury; §3.3 re-checks by pairing and can import via `POST /bankstatements` if a hole appears |
| Feed line text differs from Mercury API counterparty names | classifier falls back to description; unmatched lines are listed, not guessed |
| Duplicate transfers (the Xero duplicate saga) | one source of truth = Mercury txn id in `reference_number`; match-before-categorize on the second side |
| Opening balance wrong | derived from Mercury's own current balance minus posted txns; the §3.6 cent-equality check fails loudly if so |
| Zoho field names drift from the docs read 2026-10-15 | inventory step dumps one real object per endpoint before any write |

## 7. Switch criteria (what the trial must show)

1. Zero manual clicks in Zoho beyond the operator replies (spike findings, dry run).
2. All three accounts reconcile to Mercury to the cent, Jan→Sep 2026 records created,
   with Toast payouts split like Xero (spike S-1 passed and the bulk run used it).
3. Feed gaps = 0 inside the feed window (otherwise Zoho's Mercury feed is not trustworthy).
4. Bank rules make the Monday run a no-op in a normal week.
If all four hold, phase 2 = link the other banks, import their history the same way, and
retire the Xero precode/keep-warm launchd agents.

## Sources (read 2026-10-15)
- https://www.zoho.com/books/api/v3/oauth/ — scopes, self-client flow, refresh tokens never expire, DC URLs
- https://www.zoho.com/books/api/v3/introduction/ — 100 req/min, daily caps by plan, concurrency, `organization_id`
- https://www.zoho.com/books/api/v3/bank-transactions/ — list filters (`filter_by=Status.Uncategorized`), categorize/match/unmatch/exclude bodies
- https://www.zoho.com/books/api/v3/bank-accounts/ — `POST /bankstatements`, reconciliations, `statement/lastimported`, balance fields
- https://www.zoho.com/books/api/v3/bank-rules/ — `/bankaccounts/rules`
- https://www.zoho.com/books/help/banking/feeds.html — feeds pull up to 90 days; older history via statement import
