# Cross-check: payments.yaml vs the "Bills" Google Sheet (2026-10-17)

Source: Google Sheet **Bills** (Drive id `13aC4P_I9J4O9_6Y3dGZ7Gj74KmWfZJy1NjDJ7eVBi4U`), last edited
2026-08-17. Tabs read in full: Chart of Accounts, C.C. - Zero Balance, Recurring, Loans, Notes.
Compared against `payments.yaml` as of commit 02bb5f1 and the `task due:month` / `task gaps:offline`
output of the same day. Nothing in the registry was changed by this check; see "Proposed edits".

Vintage warning: the Chart of Accounts tab is 2023 data (statement dates April–May 2023, balances
to match). The **Recurring**, **C.C. - Zero Balance** and **Loans** tabs are the live parts and are
what the findings below rely on.

## 1. Open CONFIRM items the sheet answers

| Registry item (CONFIRM note) | What the sheet says | Resolution |
|---|---|---|
| **Rent** $1,000 on the 1st from BoA 1778, category personal | Two rents. `Rent \| Business \| 1,000 \| BoA-1778` and `Rent - RESA \| Personal \| 1,350 \| Cap1-4354`, plus an auto transfer `4354 → Natasha 1,350 on the 1st` | The $1,000 is the **business** location rent (Zoho posts it as Commerce Management from Mercury 5396 on the 1st). The personal rent is a separate $1,350 from 4354 on the 1st. The Jul 2026 Bilt $1,900 / Akelius $2,184 from Lafayette 0621 is not in the sheet at all, so the personal rent may have changed after August. Owner to confirm the current personal rent and account. |
| **Chase Credit Card** $156 on the 9th from 4354 ("duplicate of Whole Foods?") | C.C. tab, Business Cards: `Chase Ink 7667, min 164, payment day 8`. Chart: Chase Ink autopay min on the 8th. Note: "Chase autopay — fill in business credit cards, deduct from BoA" | Not a Whole Foods duplicate. It is the **Chase Ink minimum** (≈$156–164, due 8th, posts 9th). That also fills one of the four `no_payment_entry` gaps. Funding is probably BoA 1778, which explains why no debit was seen on 4354. |
| **ESCI - Heartland** $55 and **ESCI - Gov** $39.04 on the 10th (no debits seen) | Loans tab: one `Heartland` loan, payment $39.04, autopay 10th from 4354. Chart: `Heartland … 39.04 10th`. | "ESCI" is Heartland **ECSI**, the Perkins loan servicer. The registry's `Perkins Loan $42.43 on the 2nd` (added from Monarch) is the same loan at its current amount. Both ESCI entries are stale duplicates and can be deleted. |
| **US Dept of Education** $156.30 on the 24th | Loans tab: `CRI 156.30, due 21st, autopay 21st, from 4354` (cri.studentaid.gov) | Confirmed as the CRI student loan. Due 21st; bank posts 22nd–24th, so the registry's 24 is the safe posting day. The missing Sep debit is still unexplained. |
| **IRS** $256 on the 13th ("installment or one-off?") | Recurring: `IRS \| Personal \| Monthly \| 246 \| Cap1-4354` | Recurring monthly installment. Keep. Amount moved from $246 to $256. |
| **Truist Auto Loan** $702.91 on the 18th | Loans: `Truist 702.91, due 23rd, autopay 23rd, from 4354`, balance $19,524 at 4.43% | Amount and account agree. The sheet's 23rd is the due date; Monarch saw postings 18th–25th, so 18 stays as the earliest. |
| **Progressive** $450.10 on the 30th, category personal | Recurring: `Progressive \| Business \| 450.10 \| BoA-1778 \| auto debit day 28` | Category should be **business** (Zoho codes it 6210). Day 28, not 30. |
| **Northwest (Mail Forwarding)** $20 on the 16th from Mercury 5396 | Recurring: `Northwest Mailing Service \| Business \| 20 \| BoA-1778` and `Northwest Registered Agent \| Business \| Annually 125 \| BoA-1778` | Sheet says BoA 1778, registry moved it to Mercury 5396 after 0551 was archived. Verify on the next BoA 1778 or Mercury 5396 statement. The $125 annual is not in the registry. |
| **CORP Account 2119** no payment entry | C.C. tab: `BoA Corp 2119, min 45, payment day 6`. Chart: due 5th, autopay min | Add a `min` entry, ≈$45 on the 6th. Funding per the sheet note: BoA 1778. |
| **Chase Ink 7667** no payment entry | see "Chase Credit Card" above | Rename that entry; set `pays_account: chase-ink-7667`. |
| **Business Adv Cash Rewards 5819** no payment entry | Chart: balance $90, no due date, no autopay. "Yumyum Bank Accounts" lists a "BoA Credit L McGriff" | Sheet has no payment for it. If 5819 is the second cardholder's card, mark it inactive or add an entry once a statement is seen. |
| **Wells Fargo Autograph 8694** (at risk, autopay account unknown) | Not in the sheet at all | No help from the sheet. |
| **Lafayette FCU Personal Loan** $615.64 | Not in the sheet (Loans tab predates it) | No help from the sheet. |

## 2. Registry entries that differ from the sheet

| Bill | Registry | Sheet | Verdict |
|---|---|---|---|
| Quickbooks | $38 on Chase Ink, 11th | `Quickbooks \| 0 \| Chase Ink` | Sheet says $0 (cancelled?). QuickBooks last appeared on BoA 0551, closed Jun 2026. Confirm it still bills. |
| Spotify | BoA CORP 2119, 16th | `Amex` | BoA card feed shows Spotify charges, so the registry is right and the sheet is stale. |
| Medium | category personal | `Business` | Zoho posts it as an owner draw. Registry wins; sheet stale. |
| Sling | category personal | `Business` | Same as Medium. |
| OnPay | $61, 2nd, BoA 1778 | `61, auto debit day 2` | Match. |
| BoA Plat Plus 1 5153 | min $137 on the 1st | C.C.: min 142, day 4; Chart: autopay 5 days before the 4th | Match (minimum, posts ~1st). |
| BoA Plat Plus 2 7250 | min $78 on the 19th | C.C.: "full", day 23; Chart: min, 5 days before the 23rd | Bank debits of $78/$76 are minimums, so the registry is right. |
| Amex Platinum 2004 | full, 17th | C.C.: day 21; Chart: autopay 11th | Bank evidence (8/17, 9/17) wins. |
| Citi Rewards+ 8281 | full, 22nd | C.C.: day 1; Chart: autopay 21st | Registry matches the autopay date. |
| Amex Delta 1008 | full, 6th | C.C.: day 10 | One debit seen 7/6. Keep. |
| Chase 7006, Discover 9035/8886, BoA 2986, Quicksilver, Apple | full, 3 / 14 / 19 / 8 / 11 / 24 | 1 / 13 / 19 / 7 / 12 / 31 | Registry days are bank posting days, one later than the card date. Consistent. |

## 3. Bills in the sheet that the registry does not have

Bank draws (these affect the `task week` verdict and are missing today):

| Bill | Amount | Account | Day | Note |
|---|---|---|---|---|
| Rent - RESA (to Natasha) | 1,350.00 | Cap1 4354 | 1st | see §1 Rent |
| Mom | 800.00 monthly | not stated | | plus auto transfer `8513 → Mom 200 weekly (Friday)` |
| Verizon Fios | 64.99 | BoA debit 2803 | | personal |
| Zoho Books | 8.00 | BoA 1778 | | business; Zoho's own fee (PAYPAL *ZOHODISTRIB) |
| Todoist | 16.00 | BoA 1778 | | business |
| Northwest Registered Agent | 125.00 annual | BoA 1778 | | business |
| Namecheap | 61.04 + 20.98 annual | not stated | | yumyums.kitchen, jamalcole.me |
| Wyoming Agents | 25 + 163 annual | not stated | | marked personal in the sheet |

Card charges (no bank draw; they only matter through the card's "full" payment):
Geico 87 (Amex), Amazon Prime 15.98 (Chase 7006), Polygon 29 (BoA 5153), Tradingview 7.46 (BoA 5153),
Claude 20, Google One 2 (Amex), New York Times 4 (Amex), Street Lights TK360 100 (Amex), iCloud+ 10 and
AppleCare+ 13.49 (Apple Card), Github 10 (Pure Craft), LastPass 48 annual (Chase Ink), Gsuite 21.60
(Chase Ink, "cancelled?"), Verizon 160 ("cancelled?"), Cubesmart 200 ("cancelled?"; Zoho now shows
Public Storage 53.31 instead), Hiscox / Bizee / ClassPass / Localxpose / Microsoft 360 at $0.

Inflows and transfers the sheet tracks that `transfer_rules` does not: `8513 → 4354 1,275 every payday`,
`4354 → Natasha 1,350 on the 1st`, `8513 → Mom 200 weekly`. The sheet's own totals: personal
3,790.17/month (2,440.17 excluding rent), business 2,183.48/month.

## 4. Accounts

Registry accounts that the sheet also has and that agree on last-4: Cap One 8513 / 4354 / 5550 / 0732,
BoA 2803 / 1778 / 5153 / 7250 / 2986 / 2119 / 5819, Chase 7667 / 7006, Discover 8886 / 9035 / 8206,
Citi 8281, Amex 1008 / 2004 (sheet shows five digits), Navy Federal 7909 / 7517, Bilt 4607, Apple.
Sheet-only accounts, all closed or dormant per the sheet: Cap One Business 0474 (closed), M&T 2002 /
2010 / savings, Bluevine, Coinbase, the brokerage and retirement accounts. Registry-only: all four
Lafayette accounts and the loan, Fidelity 0789, Wells Fargo 8694 / 4637, Truist 1001, Mercury business
accounts. The sheet predates all of those.

## 5. Proposed registry edits (not applied)

1. Rent: split into `Rent (food truck location)` business $1,000 from Mercury 5396 on the 1st, and a
   personal rent entry from 4354 on the 1st at the amount the owner confirms ($1,350 per the sheet).
2. Rename `Chase Credit Card` to `Chase Ink (min)`, `pays_account: chase-ink-7667`, funding BoA 1778,
   amount ≈$164, day 9.
3. Add `BoA CORP 2119 (min)` ≈$45 on the 6th from BoA 1778.
4. Delete `ESCI - Heartland` and `ESCI - Gov`; the Perkins entry covers the loan.
5. Progressive: `category: business`, `day_of_month: 28`.
6. IRS: drop the CONFIRM note; it is a monthly installment.
7. Add bank-draw bills from §3: personal rent, Mom, Verizon Fios, Zoho, Todoist, and the annual
   Northwest / Namecheap / Wyoming fees.
8. Add the three auto transfers to `transfer_rules` so the 4354 funding check can see the payday inflow.
9. Quickbooks: confirm it still bills, else delete.
10. 5819: mark `inactive: true` unless a statement shows activity.
