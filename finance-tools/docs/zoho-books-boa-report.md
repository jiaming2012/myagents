# Zoho Books recon APPLY — 2026-10-17 (2026-01-01 → 2026-10-17)

## Chart of accounts / bank accounts
  37 coded accounts, banks: Operations ••4613, Outgoing ACH ••5396, Taxes ••7818, Operations ••0551 (closed), Business Adv Fundamentals, CORP Account - Business Adv Customized Cash Rewards - 2119, Chase Ink ••7667, Bluevine Checking ••2189 (closed)

## Feed-only accounts (Bank of America)
| account | uncategorized | transfers | coded | unmatched | in state | feed balance | implied opening 2025-12-31 | statement opening |
|---|---|---|---|---|---|---|---|---|
| Business Adv Fundamentals | 33 | 1 | 0 | 32 | 0 | 52.16 | 1245.79 | 1245.79 |
| CORP Account - Business Adv Customized Cash  | 0 | 0 | 0 | 0 | 0 | 1651.98 | 1856.36 | 1856.36 |
| Bluevine Checking ••2189 (closed) | 2 | 1 | 1 | 0 | 0 | 0.00 | 81.76 | 81.76 |
  (implied opening = today's feed balance less the feed's 2026 net; equals the Dec 2025 statement closing only if the feed is complete)

### Business Adv Fundamentals
- transfer  ↔ Bluevine: Checking * Closed: 1 lines, net 71.76
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

### Bluevine Checking ••2189 (closed)
- coded     6050 Dormancy Fee: 1 lines, net -10.00
- transfer  ↔ BoA: Business Checking (1778): 1 lines, net -71.76

opening balance for 207062 = 81.76 posted as journal 1622286000000217071

## Verification
| account | Zoho book balance | Zoho feed balance | opening posted | uncategorized left |
|---|---|---|---|---|
| Business Adv Fundamentals | 434.01 | 52.16 | 1245.79 | 32 |  ← differs
| CORP Account - Business Adv Customized Cash  | 1651.98 | 1651.98 | -1856.36 | 0 |
| Bluevine Checking ••2189 (closed) | 0.0 | 0.0 | 81.76 | 0 |

writes: 3, Zoho calls: 15