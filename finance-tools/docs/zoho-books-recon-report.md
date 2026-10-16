# Zoho Books × Mercury reconciliation — final report (2026-10-16)

Runs: 2026-10-15 (spike + bulk apply, stopped by the 1,000/day API cap at 754/786) and
2026-10-16 (remainder, opening balances, bank rules, monthly reconciliations).
Script: `zoho_books_recon.py` (`task zoho:recon`, `task zoho:recon:apply`, `task zoho:rules`,
`task zoho:reconcile`). Checkpoint: `finance-tools/.zoho_recon_state.json`.

## Outcome

| account | Mercury posted 2026 | feed lines | categorized / matched | left uncategorized | Zoho book balance | Mercury current |
|---|---|---|---|---|---|---|
| Operations ••4613 | 533 | 533 | 530 | 3 (unmapped merchants) | 920.74 | 47.24 |
| Outgoing ACH ••5396 | 120 | 120 | 119 | 1 (unmapped merchant) | 1450.18 | 450.18 |
| Taxes ••7818 | 137 | 137 | 137 | 0 | 214.29 | 214.29 |

The book-minus-Mercury gap on 4613 (873.50) and 5396 (1,000.00) is exactly the sum of
the uncategorized lines below. Everything else reconciles to the cent.

**Documents posted:** 150 internal transfers (created once, matched from the other
side), 192 expenses, 21 owner draws, 2 expense refunds, 144 Toast payout journals
(sales / sales tax / tips / fees / cash / 1310 / 2050 / 1320 split from toast_xero),
3 opening balances as of 2025-12-31 (388.69 / 207.00 / 37.21), 52 bank rules,
27 monthly reconciliation records.

## Left for the operator (O-2): four merchants not in merchants.yaml

| date | account | amount | merchant | suggested line |
|---|---|---|---|---|
| 2026-10-02 | 5396 | −1,000.00 | Commerce Management | `- {pattern: "Commerce Management", account: "????"}` |
| 2026-10-04 | 4613 | −338.00 | Jd Transport | `- {pattern: "Jd Transport", account: "????"}` |
| 2026-10-11 | 4613 | −53.31 | Public Storage | `- {pattern: "Public Storage", account: "6310"}` (Rent - Other, like CubeSmart?) |
| 2026-10-16 | 4613 | −482.19 | Cummins Sales and Service | `- {pattern: "Cummins", account: "????"}` |

Add the lines to `merchants.yaml`, run `task zoho:recon:apply`, and the lines categorize
(the bank rules only fire on future feed imports). No vendor contacts were created (O-3).

## Monthly reconciliation records (all 27 created, status `reconciled`)

Zoho reconciles strictly by its own document dates. The feed dates a transfer that
Mercury posts late in the evening (Eastern) on the next day, so a few month-ends differ
from Mercury's balance by those straddlers; each one reverses the next month.

| account | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep |
|---|---|---|---|---|---|---|---|---|---|
| 4613 Zoho closing | 509.29 | 257.51 | 414.18 | 82.92 | 168.49 | 1170.61 | 25.34 | 957.02 | 293.64 |
| 4613 Mercury | 509.29 | 257.51 | 114.18 | 82.92 | 168.49 | 785.56 | 25.34 | 957.02 | 293.64 |
| 5396 Zoho closing | 3.89 | 100.00 | 0.29 | 26.87 | 287.61 | 212.55 | 71.61 | 18.17 | 1283.25 |
| 5396 Mercury | 3.89 | 100.00 | 0.29 | 626.87 | 287.61 | 427.55 | 71.61 | 18.17 | 1283.25 |
| 7818 Zoho closing | 97.68 | 135.10 | 37.65 | 158.86 | 296.68 | 203.13 | 486.52 | 769.13 | 0.16 |
| 7818 Mercury | 97.68 | 135.10 | 37.65 | 158.86 | 296.68 | 371.13 | 486.52 | 769.13 | 0.16 |

Straddlers (feed date / Mercury date): Mar 31→Apr 1 −300.00 (4613→5396 reversed on
5396 Apr 30→May 1 +600.00 includes a second transfer); Jun 30→Jul 1: 4613 −99.75,
−215.00, −68.25 and a −2.05 expense; 5396 +215.00; 7818 +99.75, +68.25.

## API facts learned (not in Zoho's docs)

- **Daily cap on this org is 1,000 calls** (error code 45) although the plan reads
  PREMIUM TRIAL. The client stops on code 45 and resumes from the checkpoint.
- Feed lines list only under `filter_by=Status.Uncategorized`; once categorized, the
  documents appear under `GET /bankaccounts/{id}/transactions` with `reconcile_status`.
- `uncategorize` must be called with the **statement-line id** (`imported_transaction_id`
  of the categorized row), not the document id; `DELETE` on a categorized/matched
  document is refused.
- A journal that debits the bank account is offered as a match candidate for the feed
  line (`transaction_type: journal`) — this is what makes the Toast split work.
- Reconciliation: `transactions_to_be_reconciled` must be a **plain list of ids** (the
  documented object form is rejected); the opening of a period is the sum of reconciled
  documents dated before it; the first period must include the opening-balance document
  dated 2025-12-31; a saved draft blocks further records until completed or deleted;
  `DELETE …/reconciliations/{id}` undoes the last one.
- Rule names reject punctuation such as `:` and `>`.
- Opening balances need `currency_id` per account.

## API budget used

| run | calls |
|---|---|
| 2026-10-15 inventory + spike + bulk apply | ~1,000 (cap) |
| 2026-10-16 remainder + rules + reconciliations + probes | ~450 |

A normal week from here: one `task zoho:recon:apply` ≈ 20–60 calls.
