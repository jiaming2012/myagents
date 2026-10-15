# Spike S-1 — Toast payout split in Zoho Books: PASSED (approach A)

Run 2026-10-15 with `zoho_books_recon.py --apply --spike --toast <toast_xero dump>`.
Nothing was reverted: every document the spike posted is a correct 2026 posting and
stays in the books.

## What was proved

| Case | Mercury credit | Zoho result |
|---|---|---|
| Clean payout (sales day 2025-12-31, settled 2026-01-02) | 301.83 on 4613 | journal #1: debit Operations ••4613 301.83, credit Sales 304.50, credit Sales Tax 17.06, debit Cash on Hand 7.42, debit Merchant Fees 12.31 — then the feed line was **offered as a match candidate and matched** (`status: matched`) |
| Split-payout remainder (2026-08-27, the $16.24 twin of the $402.58 primary) | 16.24 on 4613 | journal #2: debit Operations ••4613 16.24, debit Merchant Fees 0.72, credit Toast Split Payout Clearing 16.96 — matched the same way; the primary's journal carries the offsetting 1320 line so the cluster nets 0.00 |

Approach B (deposit to 1320 clearing + journal from 1320) was not needed.

Also exercised, one of each write shape the bulk run uses, all accepted first try:

| Shape | Sample | Zoho call |
|---|---|---|
| Internal transfer, creator side | 4613 → 7818, 7.55, 2026-01-02 | `categorize` `transfer_fund` from 4613 to 7818 |
| Internal transfer, other side | 7818 feed line 7.55 | `match` → candidate with `reference_number mercury:<creator id>`, type `transfer_fund` |
| Expense | DaMarien Lee Banks −73.88 on 5396 → 6450 Wages | `categorize/expenses` |
| Owner draw | Jamal Cole −600.00 on 4613 → 3200 | `categorize` `owner_drawings` |
| Expense refund | Instacart +1.90 on 4613 → 5000 COGS | `categorize` `expense_refund` |

Book balances after the spike equal the sum of these seven documents to the cent
(4613 −287.58, 5396 −73.88, 7818 +7.55 before opening balances), and the uncategorized
counts dropped by exactly the lines touched (530→525, 120→119, 136→135).

## Mechanics worth keeping

- Journal line signs: toast_xero's positive line amounts become **credits** (Sales,
  Sales Tax, tips), negative amounts become **debits** (fees, cash from sales,
  discounts, 1310 / 2050 reversals); the bank account is debited for the net payout.
- `GET /banktransactions/uncategorized/{line}/match` lists journals and transfers that
  touch the account; pick by `reference_number`, then `POST …/match` with
  `{transactions_to_be_matched:[{transaction_id, transaction_type}]}`.
- Once categorized or matched, documents show up under
  `GET /bankaccounts/{id}/transactions` (statuses `categorized` / `matched`), which is
  what the monthly reconciliation step uses.
- Calls per Toast payout: 3 (journal, match lookup, match). Per internal transfer: 3.
  Per spend: 1.

## Chart seeding that happened in the same run

35 coded accounts now exist. Stock accounts reused with a code added: 3100 Owner's
Equity, 3200 Drawings, 4000 Sales, 5000 Cost of Goods Sold, 6280 Printing and
Stationery. Two feed-less bank accounts created: `Operations ••0551 (closed)` and
`BoA Business Checking ••1778`.

## Run log

See `docs/zoho-books-recon-report.md` for the full apply; the spike's own log was
overwritten by it and is summarized above.
