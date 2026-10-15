# Zoho Books inventory — 2026-10-15

Read-only dump for the recon plan (section 2). Regenerate with the job's `inventory.py`.

## Organization

- `942990573` **Yumyums Kitchen LLC** — plan PREMIUM TRIAL, USD, fiscal year start month 0, time zone America/New_York, country U.S.A, sales tax configured: True

## Bank accounts

| account_id | name | type | feed | last refresh | book balance | feed balance | uncategorized |
|---|---|---|---|---|---|---|---|
| `1622286000000095122` | Operations ••4613 | bank | None | 2026-10-15 | 0.0 | 528.43 | 530 |
| `1622286000000095128` | Outgoing ACH ••5396 | bank | None | 2026-10-15 | 0.0 | 450.18 | 120 |
| `1622286000000000361` | Petty Cash | cash | None |  | 0.0 | 0.0 | 0 |
| `1622286000000095134` | Taxes ••7818 | bank | None | 2026-10-15 | 0.0 | 239.29 | 136 |
| `1622286000000000358` | Undeposited Funds | cash | None |  | 0.0 | 0.0 | 0 |

## Mercury (independent truth)

| suffix | Mercury name | current balance | posted txns 2026-01-01→today | Σ 2026 | derived opening 2026-01-01 |
|---|---|---|---|---|---|
| 4613 | Operations ••4613 | 528.43 | 530 | 139.74 | 388.69 |
| 5396 | Outgoing ACH ••5396 | 450.18 | 120 | 243.18 | 207.00 |
| 7818 | Taxes ••7818 | 239.29 | 136 | 202.08 | 37.21 |

## Feed lines (uncategorized) per account

| suffix | count | first | last | money-in | money-out |
|---|---|---|---|---|---|
| 4613 | 530 | 2026-01-02 | 2026-10-15 | 168 | 362 |
| 5396 | 120 | 2026-01-01 | 2026-10-14 | 40 | 80 |
| 7818 | 136 | 2026-01-02 | 2026-10-12 | 110 | 26 |

Sample feed line (4613, oldest):

```json
{
  "transaction_id": "1622286000000095667",
  "date": "2026-01-02",
  "amount": 211.28,
  "debit_or_credit": "credit",
  "payee": "",
  "description": "Percentage-based rule auto-transfer; Merchant name: Mercury Checking \u2022\u20220551",
  "reference_number": "",
  "status": "uncategorized",
  "transaction_type": "uncategorized",
  "is_feed": true,
  "feed_source": "Automatic feeds",
  "imported_transaction_id": ""
}
```

Note: `GET /banktransactions` returns feed lines only with `filter_by=Status.Uncategorized` (or `status=uncategorized`); `Status.All` and the unfiltered call return none. `debit_or_credit`: debit = money in, credit = money out.

## Chart of accounts

52 accounts. Columns: account_id | code | name | type | active

| account_id | code | name | type | active |
|---|---|---|---|---|
| `1622286000000000373` |  | Accounts Payable | accounts_payable | True |
| `1622286000000000364` |  | Accounts Receivable | accounts_receivable | True |
| `1622286000000095122` |  | Operations ••4613 | bank | True |
| `1622286000000095128` |  | Outgoing ACH ••5396 | bank | True |
| `1622286000000095134` |  | Taxes ••7818 | bank | True |
| `1622286000000000361` |  | Petty Cash | cash | True |
| `1622286000000000358` |  | Undeposited Funds | cash | True |
| `1622286000000034003` |  | Cost of Goods Sold | cost_of_goods_sold | True |
| `1622286000000012001` |  | Drawings | equity | True |
| `1622286000000000385` |  | Opening Balance Offset | equity | True |
| `1622286000000000382` |  | Owner's Equity | equity | True |
| `1622286000000000379` |  | Retained Earnings | equity | True |
| `1622286000000000403` |  | Advertising And Marketing | expense | True |
| `1622286000000000424` |  | Automobile Expense | expense | True |
| `1622286000000000439` |  | Bad Debt | expense | True |
| `1622286000000000409` |  | Bank Fees and Charges | expense | True |
| `1622286000000000454` |  | Consultant Expense | expense | True |
| `1622286000000000412` |  | Credit Card Charges | expense | True |
| `1622286000000000451` |  | Depreciation Expense | expense | True |
| `1622286000000000427` |  | IT and Internet Expenses | expense | True |
| `1622286000000000433` |  | Janitorial Expense | expense | True |
| `1622286000000032023` |  | Lodging | expense | True |
| `1622286000000000448` |  | Meals and Entertainment | expense | True |
| `1622286000000000400` |  | Office Supplies | expense | True |
| `1622286000000000460` |  | Other Expenses | expense | True |
| `1622286000000000436` |  | Postage | expense | True |
| `1622286000000000442` |  | Printing and Stationery | expense | True |
| `1622286000000072001` |  | Purchase Discounts | expense | True |
| `1622286000000000430` |  | Rent Expense | expense | True |
| `1622286000000000457` |  | Repairs and Maintenance | expense | True |
| `1622286000000000445` |  | Salaries and Employee Wages | expense | True |
| `1622286000000000421` |  | Telephone Expense | expense | True |
| `1622286000000000418` |  | Travel Expense | expense | True |
| `1622286000000035005` |  | Uncategorized | expense | True |
| `1622286000000000367` |  | Furniture and Equipment | fixed_asset | True |
| `1622286000000000406` |  | Discount | income | True |
| `1622286000000000391` |  | General Income | income | True |
| `1622286000000000394` |  | Interest Income | income | True |
| `1622286000000000397` |  | Late Fee Income | income | True |
| `1622286000000010001` |  | Other Charges | income | True |
| `1622286000000000388` |  | Sales | income | True |
| `1622286000000014001` |  | Shipping Charge | income | True |
| `1622286000000000370` |  | Advance Tax | other_current_asset | True |
| `1622286000000035001` |  | Employee Advance | other_current_asset | True |
| `1622286000000095009` |  | Prepaid Expenses | other_current_asset | True |
| `1622286000000035003` |  | Employee Reimbursements | other_current_liability | True |
| `1622286000000003001` |  | Opening Balance Adjustments | other_current_liability | True |
| `1622286000000000376` |  | Tax Payable | other_current_liability | True |
| `1622286000000005001` |  | Unearned Revenue | other_current_liability | True |
| `1622286000000000415` |  | Exchange Gain or Loss | other_expense | True |
| `1622286000000045001` |  | Dimension Adjustments | other_liability | True |
| `1622286000000034001` |  | Inventory Asset | stock | True |

## Taxes

| tax_id | name | rate | type |
|---|---|---|---|
(none configured)

## Bank rules

0 rules.

## Vendors / customers

0 vendors, 0 customers.

## Journals / other

- `GET /journals`: success, 0 journals
- `GET /settings/openingbalances`: success — {}

API calls used: 14
