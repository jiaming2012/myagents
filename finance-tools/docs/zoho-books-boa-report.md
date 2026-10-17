# Zoho Books recon DRY RUN — 2026-10-17 (2026-01-01 → 2026-10-17)

## Chart of accounts / bank accounts
  37 coded accounts, banks: Operations ••4613, Outgoing ACH ••5396, Taxes ••7818, Operations ••0551 (closed), Business Adv Fundamentals, CORP Account - Business Adv Customized Cash Rewards - 2119, Chase Ink ••7667, Bluevine Checking (closed)

## Feed-only accounts (Bank of America)
| account | uncategorized | transfers | coded | unmatched | in state | feed balance | implied opening 2025-12-31 | statement opening |
|---|---|---|---|---|---|---|---|---|
| Business Adv Fundamentals | 32 | 0 | 0 | 32 | 0 | 52.16 | 1245.79 | 1245.79 |
| CORP Account - Business Adv Customized Cash  | 0 | 0 | 0 | 0 | 0 | 1651.98 | 1856.36 | 1856.36 |
  (implied opening = today's feed balance less the feed's 2026 net; equals the Dec 2025 statement closing only if the feed is complete)

### Business Adv Fundamentals
- unmatched Spotify: 9 lines, net -123.58   # suggested: - {pattern: "Spotify", account: "????"}
- unmatched Medium: 7 lines, net -35.00   # suggested: - {pattern: "Medium", account: "????"}
- unmatched DoorDash: 6 lines, net -59.94   # suggested: - {pattern: "DoorDash", account: "????"}
- unmatched Peacock: 2 lines, net -21.98   # suggested: - {pattern: "Peacock", account: "????"}
- unmatched RETURN of DoorDash: 2 lines, net 19.98   # suggested: - {pattern: "RETURN of DoorDash", account: "????"}
- unmatched PMNT SENT APPLE CASH SENT MONEY: 2 lines, net -159.63   # suggested: - {pattern: "PMNT SENT APPLE CASH SENT MONE", account: "????"}
- unmatched Uber: 1 lines, net -12.69   # suggested: - {pattern: "Uber", account: "????"}
- unmatched RETURN of Peacock: 1 lines, net 10.99   # suggested: - {pattern: "RETURN of Peacock", account: "????"}
- unmatched PAYPAL DES:RETRY PYMT ID:PEACOCKTVLL INDN:JAMAL COLE: 1 lines, net -10.99   # suggested: - {pattern: "PAYPAL DES:RETRY PYMT ID:PEACO", account: "????"}
- unmatched RETURN of PAYPAL DES:RETRY PYMT ID:PEACOCKTVLL INDN:JAMAL COLE: 1 lines, net 10.99   # suggested: - {pattern: "RETURN of PAYPAL DES:RETRY PYM", account: "????"}

### CORP Account - Business Adv Customized Cash Rewards - 2119

## Monthly reconciliation records (statement closings from closing_balances.yaml)
| account | month | Zoho closing | statement | diff | docs | status |
|---|---|---|---|---|---|---|
| Business Adv Fundamentals | 2026-01 | 27.77 | — | — | 30 | no statement balance |
| Business Adv Fundamentals | 2026-02 | 361.44 | — | — | 22 | no statement balance |
| Business Adv Fundamentals | 2026-03 | 1097.94 | — | — | 29 | no statement balance |
| Business Adv Fundamentals | 2026-04 | 117.99 | — | — | 30 | no statement balance |
| Business Adv Fundamentals | 2026-05 | 269.01 | — | — | 21 | no statement balance |
| Business Adv Fundamentals | 2026-06 | -52.12 | — | — | 21 | no statement balance |
| Business Adv Fundamentals | 2026-07 | 263.88 | — | — | 14 | no statement balance |
| Business Adv Fundamentals | 2026-08 | 74.11 | — | — | 28 | no statement balance |
| Business Adv Fundamentals | 2026-09 | 1013.97 | — | — | 22 | no statement balance |
| CORP Account - Business Adv Cu | 2026-01 | 1697.04 | — | — | 7 | no statement balance |
| CORP Account - Business Adv Cu | 2026-02 | 1684.34 | — | — | 5 | no statement balance |
| CORP Account - Business Adv Cu | 2026-03 | 1757.94 | — | — | 3 | no statement balance |
| CORP Account - Business Adv Cu | 2026-04 | 1650.92 | — | — | 6 | no statement balance |
| CORP Account - Business Adv Cu | 2026-05 | 1712.48 | — | — | 3 | no statement balance |
| CORP Account - Business Adv Cu | 2026-06 | 1721.64 | — | — | 4 | no statement balance |
| CORP Account - Business Adv Cu | 2026-07 | 1799.14 | — | — | 3 | no statement balance |
| CORP Account - Business Adv Cu | 2026-08 | 1727.28 | — | — | 4 | no statement balance |
| CORP Account - Business Adv Cu | 2026-09 | 1806.20 | — | — | 3 | no statement balance |