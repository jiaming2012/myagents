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
- **feed_start** per account (first feed line date). Everything 2026-01-01 → feed_start−1
  must be *imported*; everything ≥ feed_start must be *categorized* against the feed.
- whether the chart needs seeding (§3.2).

Also check how the Mercury feed line text looks (`payee`/`description`): internal
transfers in Mercury surface as nicknames like `Taxes ••7818`; confirm the feed carries
the counterparty name so `merchants.yaml` patterns still hit. If the feed only carries
`bankDescription`, the classifier already falls back to that.

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

### 3.3 Fill the pre-feed gap: import statements from the Mercury API
Xero's biggest pain was that statement lines were not API-readable. Zoho has
`POST /bankstatements`. For each account, for the window **2026-01-01 → feed_start−1**:
```json
{ "account_id": "...", "start_date": "2026-01-01", "end_date": "<feed_start-1>",
  "transactions": [ { "transaction_id": "<mercury id>", "date": "YYYY-MM-DD",
      "debit_or_credit": "debit|credit", "amount": 123.45,
      "payee": "<counterparty>", "description": "<bankDescription>",
      "reference_number": "mercury:<id>" } ] }
```
Guard against overlap with the feed: before importing, diff the Mercury list against the
feed lines (date ± 2 days, |amount|, then payee fuzzy) and **never import a Mercury txn
that already has a feed line**. Any feed line with no Mercury twin, or Mercury txn inside
the feed window with no feed line, goes to the report as a *feed gap* (that alone is a
go/no-go signal for the switch).

Result: every Mercury posted txn since 2026-01-01 exists as exactly one uncategorized
line in Zoho, each carrying `reference_number mercury:<id>` (imported) or matched to one
by the diff (feed).

### 3.4 Categorize every line (the "reconcile clicks")
Walk accounts in `merchants.yaml` order (4613 first), lines oldest → newest. Decision per
line, exactly the `mercury_precode.py` classifier:

| Class | Zoho call | Notes |
|---|---|---|
| **Internal transfer** (`transfers:` map, both ends Mercury) | first side seen: `POST /banktransactions/uncategorized/{id}/categorize` body `{transaction_type:"transfer_fund", from_account_id, to_account_id, date, amount, reference_number}` — the other side: `GET …/{id}/match` → pick candidate whose `reference_number`/amount/date equal → `POST …/{id}/match` `{transactions_to_be_matched:[{transaction_id, transaction_type:"transfer_fund"}]}` | Dedupe exactly like precode: a transfer seen from both sides is created once, matched once |
| **Transfer to own BoA** (`Yumyums Kitchen LLC`) | `transfer_fund` to the BoA bank account if it exists in Zoho, else to a `BoA clearing` asset | phase 2 links BoA and matches the other side |
| **Spend** (`merchants:` hit) | `POST …/categorize/expenses` `{account_id:<code map>, date, amount, paid_through_account_id:<bank>, reference_number, description, [vendor_id]}` | vendor optional; create vendors (`POST /contacts`) only if the operator wants vendor reports |
| **Owner draw** (`Jamal Cole` → 3200) | `categorize` with `transaction_type:"owner_drawings"` | personal-Mercury `••5237` transfers likewise |
| **Toast payout** (credit, pattern TOAST) | `categorize` as `deposit` to **1320 Toast Clearing** (or `sales_without_invoices` to Sales) | **Operator decision O-1** (§5). One-shot ≠ port of toast_xero |
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

- **O-1 Toast payouts.** (a) one-line deposit to `1320 Toast Clearing` per payout — fast,
  balances reconcile, revenue detail deferred (recommended for the trial); (b) deposit to
  Sales income — simplest books, loses tips/fees split; (c) port toast_xero's
  per-sales-day split — only if the switch is decided.
- **O-2 Unmatched merchants** — the dry-run prints them with a suggested `merchants.yaml`
  line each; answer or say "leave uncategorized".
- **O-3 Vendors** — create vendor contacts from merchant names (yes/no). Default no.

---

## 6. Risks and budget

| Risk | Handling |
|---|---|
| Rate limits: 100 req/min/org; daily cap by plan (Free 1,000 · Standard 2,000 · Professional 5,000 · Premium+ 10,000; trial is normally Premium). ~500 Mercury txns since Jan → ~1,200–1,800 calls | throttle 90/min, back off on 429 (codes 44/45/1070), checkpoint progress in a local JSON so `--apply` resumes; on a Free plan split across two days |
| Feed only covers ≤90 days | §3.3 imports the rest from Mercury — this is the feature Xero lacked |
| Feed line text differs from Mercury API counterparty names | classifier falls back to description; unmatched lines are listed, not guessed |
| Duplicate transfers (the Xero duplicate saga) | one source of truth = Mercury txn id in `reference_number`; match-before-categorize on the second side |
| Opening balance wrong | derived from Mercury's own current balance minus posted txns; the §3.6 cent-equality check fails loudly if so |
| Zoho field names drift from the docs read 2026-10-15 | inventory step dumps one real object per endpoint before any write |

## 7. Switch criteria (what the trial must show)

1. Zero manual clicks in Zoho beyond the two operator replies.
2. All three accounts reconcile to Mercury to the cent, Jan→Sep 2026 records created.
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
