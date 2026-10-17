# Handoff — reconcile the Bank of America accounts in Zoho Books

Written 2026-10-16 for a fresh session. Read `zoho-books-runbook.md` first (credentials,
script, API budget, repairs); this file is only what is different about BoA.

## 0. Status 2026-10-17 — steps 1–3 done, waiting on the operator for 4–5

- `task zoho:boa` (dry run) / `task zoho:boa:apply` / `task zoho:boa:reconcile` /
  `task zoho:boa:rules` exist (`--feed-only` in `zoho_books_recon.py`). Classification:
  `boa_transfers:` → `boa_merchants:` → shared `merchants:` in merchants.yaml, on
  payee + description (the ACH `INDN:<holder>` segment is stripped first so the
  "Jamal Cole" owner pattern cannot claim vendor debits).
- Placeholder `BoA Business Checking ••1778` deleted; `BANK_NAMES` points at the two feed
  accounts. Stock "Bank Fees and Charges" = 6050, "Credit Card Charges" = 6055.
- Posted (reference `boa:<line id>`, checkpointed under the same key): all 15 transfers
  between 1778 and 2119 (7 card payments, 8 overdraft advances), every fee, every mapped
  vendor, the 44 owner contributions from 2803, the 7 draws to 2803, and the returned
  items as refunds of the debit they reverse. See `zoho-books-boa-report.md` for the
  verification table and what is still uncategorized.
- **Operator answers 2026-10-17, posted the same day**: Chase Ink autopay = transfers to a
  new no-feed credit-card account `Chase Ink ••7667` (`1622286000000212046`; its 4
  returned autopays are transfers back); cash deposits (Counter Credit, ATM) = 4000 Sales
  as `deposit`; Bluevine 71.76 = transfer from a new no-feed `Bluevine Checking (closed)`
  (`1622286000000208105`); Avis = owner draw; U-Haul = 6310 (move to the storage unit
  near the truck). **Opening journals posted** from the December statements:
  1778 = 1,245.79 (`eStmt_2025-12-31.pdf`), 2119 = 1,856.36 owed
  (`December_Statement_2025-12-10.pdf`, no activity 12/11–12/31) — both equal to the
  feed-implied figures. Card 2119: book = feed = 1,651.98, 0 uncategorized.
- **Question 2 answered and posted 2026-10-17**: Spotify = 6340 (music on the truck),
  Medium / DoorDash DashPass / Peacock / Uber = owner draws, Apple Cash sends = 6450
  wages. **All three feed accounts now read book = feed with 0 uncategorized**: 1778 =
  52.16, 2119 = 1,651.98 owed, Bluevine = 0.00. 34 BoA bank rules created
  (`task zoho:boa:rules`): the BoA block plus the shared merchants seen on BoA, withdrawals
  only; "Jamal Cole" is deliberately excluded (ACH descriptors carry `INDN:JAMAL COLE`), and
  deposit patterns (2803 contributions, cash sales) stay script-driven.
- **Still open**: (a) nothing to classify.
  (b) Chase Ink DONE 2026-10-17: closed card (cannot be feed-linked, operator still paying
  it off). The 11 statements (`…/2025_taxes/yumyums/bank_statements/chase_7667/`, closing
  on the 14th, Dec 2025 → Oct 2026) chain exactly; 2025-12-31 balance = Dec 14 new balance
  5,964.17 (no activity 12/15–12/31), posted as the opening journal. The 31 lines of 2026
  activity (13 autopays, 4 returned payments, 3 QuickBooks, 1 LastPass, 10 interest) were
  pushed in with `POST /bankstatements` (statement `1622286000000219028`; same convention
  as 0551: credit = money in, so a payment lands as a `debit` line like on 2119), then
  `task zoho:boa:apply` matched the 17 payments/returns to the BoA-side transfer documents
  (match-first: the dates differ by a day) and coded the rest. Book = 5,708.07 owed = the
  Oct 14 statement. Keep the account active while it is being paid down; statements
  close on the 14th, so reconcile it by statement period or skip records. (c) Bluevine DONE 2026-10-17: the operator feed-linked it
  (two accounts arrived unnamed as "Yumyums Kitchen LLC"; renamed `Bluevine Checking
  ••2189 (closed)` = `1622286000000207062`, now a feed-only account in merchants.yaml, and
  `Bluevine ••2849 (closed, empty)` = `1622286000000207068`, no lines, delete if it never
  held business money). The no-feed placeholder was deleted, opening 81.76 (feed-implied)
  posted, 2 lines booked, book = feed = 0.00.
  (d) Month-end records — **1778 DONE 2026-10-17**: the nine eStmt PDFs
  (`…/2025_taxes/yumyums/bank_statements/boa_1778/`, periods = calendar months) gave the
  ending balances in `closing_balances.yaml`; all nine equalled Zoho's closings and
  Jan–Sep are `reconciled`. Card 2119, Bluevine and Chase Ink have no records yet: the
  card and Chase statements close on the 10th/14th, so either reconcile them by statement
  period (extend `reconcile_feed_only` to take arbitrary periods) or leave them — book =
  statement/feed on all three. Zoho's closings with everything posted
  (checking / card owed): Jan 0.00 / 1,697.04 · Feb 295.00 / 1,684.34 · Mar 1,012.65 /
  1,757.94 · Apr 0.00 / 1,650.92 · May 136.03 / 1,712.48 · Jun −377.43 / 1,721.64 · Jul
  −61.43 / 1,799.14 · Aug −275.04 / 1,727.28 · Sep 632.12 / 1,806.20 (the 0.00 months are
  real: overdraft protection pulls exactly the shortfall). Bluevine: 81.76 Jan–Apr, 71.76
  May, 0.00 Jun.
- Corrections to §2–3 below, from the actual feed: checking credit = money out; the 7
  "transfer to CHK 2803" lines are owner **draws**; the card payments are the 3
  "payment to CRD 2119" + 4 "BUSINESS CARD Bill Payment" lines; the card's credit =
  charge, debit = payment; "Zohaib" is the feed's guess for `PAYPAL *ZOHODISTRIB`, i.e.
  Zoho's own $8/mo charge (mapped 6340, review); Verizon was missing from `merchants:`
  (added to the BoA block, 6390). Progressive is coded 6210 as the handoff says, but
  `payments.yaml` lists it as personal — operator to confirm.

## 1. What is in Zoho right now (verified 2026-10-16)

| Zoho account | account_id | type | feed lines (uncategorized) | range | feed balance |
|---|---|---|---|---|---|
| Business Adv Fundamentals (checking ••1778) | `1622286000000143048` | bank | 255 | 2026-01-02 → 2026-10-15 | 52.16 |
| CORP Account - Business Adv Customized Cash Rewards - 2119 (card) | `1622286000000143054` | credit_card | 40 | 2026-01-06 → 2026-10-09 | 1,651.98 owed |
| BoA Business Checking ••1778 (placeholder, no feed, **0 documents**) | `1622286000000108013` | bank | 0 | — | 0.00 |

The operator linked both BoA accounts on 2026-10-16 with history from 2026-01-01, same
as Mercury. The placeholder was created by the Mercury recon for wires to BoA; none were
posted in 2026, so **delete it** (`DELETE /bankaccounts/1622286000000108013`) and point
`BANK_NAMES["BoA: Business Checking (1778)"]` in `zoho_books_recon.py` at
`Business Adv Fundamentals` instead.

Registry (`payments.yaml`): 1778 = `boa-business-1778` (business checking, Xero name
"BoA: Business Checking (1778)"); 2119 = `boa-corp-2119` (Xero "BoA: CORP Account (2119)").
The other BoA accounts in the registry (2803 Adv Plus personal checking, 5153 / 7250 /
2986 personal cards, 5819 Cash Rewards) are **not** linked and, being personal, do not
belong in the Yumyums Kitchen LLC books.

## 2. What is different from Mercury

1. **No bank API.** Mercury supplied the independent truth (balances, posting dates,
   counterparty names). For BoA the feed *is* the data; the independent check is the
   BoA statement closing balance per month (operator supplies the PDFs or reads them
   off; `bank_balance` in Zoho is only today's). The script's Mercury pairing step does
   not apply — add a **feed-only mode** that takes the uncategorized lines directly.
2. **Descriptions are raw BoA text**, `payee` is set on only 99 of 255 checking lines
   and none of the card lines. Classify on `description` with BoA-specific patterns
   (new `boa_merchants:` block in merchants.yaml, or reuse `merchants:` where the
   vendor is the same: OnPay, Google Workspace, Progressive, Verizon…).
3. **The checking account is mostly transfers and fees**, not merchant spend:
   - 44 × `Online Banking transfer from CHK 2803` (personal checking → business):
     **owner contribution** (3100 Owner's Equity / `owner_contribution`), not a transfer.
   - 7 × `Online Banking transfer to C…` and the `Online Banking payment to CRD 2119`
     lines: **transfer_fund** to the 2119 card (the card side shows `PAYMENT - THANK YOU`
     / `ONLINE PAYMENT FROM CHK 1778`): create from the checking side, match on the card.
   - 18 × `RETURN OF POSTED CHECK / ITEM` + 12 × `OVERDRAFT ITEM FEE` + 8 ×
     `OVERDRAFT PROTECTION FROM XX…`: bank fees → stock "Bank Fees and Charges"
     (give it code 6050 or similar); overdraft protection pulls are transfers from the
     linked card/account named in the description.
   - 10 × `CHASE CREDIT CRD DES:AUTOPAY`: payment of the Chase Ink card (registry
     `chase-ink-7667`, not in Zoho) → create a liability/credit-card account for Chase
     Ink without a feed, or treat as owner draw if the card is personal — **ask**.
   - 10 × `ONPAY INC DES:PayrollFee` → 6450/6360 like Mercury; Verizon → 6390;
     Progressive → 6210; Google Ads → 6000; Spotify / Medium → likely personal (owner
     draw 3200) — **ask**; `Zohaib` (7) → contract labor 6090? — **ask**.
4. **The card (2119)** is 40 lines: finance charges, late fees, overdraft advances and
   payments. Finance/late fees → "Interest / Bank Fees"; `OVERDRAFT ADVANCE` = the
   card funding the checking overdraft → transfer_fund card → checking (matches the
   checking side's `OVERDRAFT PROTECTION FROM` lines); payments match the checking
   side's transfers. Card feed sign: debit = charge (money out of your pocket), credit =
   payment — verify on the first line before trusting the convention.
5. **Opening balances 2025-12-31** must come from the Dec 2025 statements (checking
   balance and card balance owed). Post them as journals against Opening Balance Offset
   (the org's opening-balance record is reconciled and immutable).
6. **Reconciliation records** per month use the statement closing balance (operator)
   rather than a computed Mercury figure: extend `--reconcile` to read a small
   `closing_balances.yaml` (`account → {YYYY-MM: amount}`) and compare Zoho's closing to
   it. Expect no straddlers: BoA dates are already the bank's own.

## 3. Suggested order (one session, ~450 API calls, under the 1,000/day cap)

1. Delete the placeholder; fix `BANK_NAMES`; add 1778 and 2119 to a new
   `zoho_feed_only_accounts:` list in merchants.yaml with their Zoho ids and the
   Xero names above.
2. Dry run a new `--feed-only` mode: list uncategorized lines, classify by BoA
   patterns, print the unmapped descriptions grouped by count (like the Mercury
   "unmatched merchants" table). Expect to ask the operator the questions marked
   **ask** above in one message.
3. Apply: owner contributions, fees, vendors, transfers checking→card (create) then
   card side (match), card fees. Idempotency key: `boa:<zoho line id>` since there is
   no bank txn id.
4. Opening journals from the Dec 2025 statements; monthly records from statement
   closings; verify `balance` = statement balance on 2026-09-30 and today.
5. Bank rules for the recurring BoA patterns (`task zoho:rules` reads only
   `merchants:`; extend it to the BoA block).

## 4. Questions for the operator (ask once, at the dry run)

- Chase Ink autopay from 1778: business card to be modeled in Zoho, or owner draw?
- Spotify, Medium, Zohaib, and any other personal-looking charges on 1778: owner draws?
- Dec 2025 closing balances for 1778 and 2119 (or the PDFs).
- Should the 2803 personal checking transfers be owner contributions (default yes)?
