# Zoho Books recon — apply run 1 (2026-10-15, interrupted by the 1,000/day API cap)

Raw log of the first bulk apply; 754 of 786 documents posted before Zoho returned code 45. Resume with `task zoho:recon:apply`.

```
# Zoho Books recon APPLY — 2026-10-15 (2026-01-01 → 2026-10-15)
## Chart of accounts / bank accounts
  35 coded accounts, banks: Operations ••4613, Outgoing ACH ••5396, Taxes ••7818, Operations ••0551 (closed), BoA Business Checking ••1778
## Mercury ↔ feed pairing
| account | Mercury posted | feed uncategorized | paired | Mercury unpaired | feed unpaired | opening 2025-12-31 |
|---|---|---|---|---|---|---|
| Operations ••4613 | 530 | 525 | 525 | 0 | 0 | 388.69 |
| Outgoing ACH ••5396 | 120 | 119 | 119 | 0 | 0 | 207.00 |
| Taxes ••7818 | 136 | 135 | 135 | 0 | 0 | 37.21 |
## Toast payouts: 142 Mercury credits, 142 with a toast_xero deposit, 0 without (left uncategorized)
## Plan
- transfers: 422 lines -> 273 transfer documents (149 internal keys, 122 one-sided keys, 0 uneven)
- coded spends/refunds: 212
- Toast journals: 142 (approach A)
- unmatched merchants (left uncategorized): 3
- already done (in state): 0
### Unmatched merchants (3) — add to merchants.yaml or leave
- Jd Transport: 1 txns, net -338.00   # suggested: - {pattern: "Jd Transport", account: "????"}
- Public Storage: 1 txns, net -53.31   # suggested: - {pattern: "Public Storage", account: "????"}
- Commerce Management: 1 txns, net -1000.00   # suggested: - {pattern: "Commerce Management", account: "????"}
opening balances FAILED: POST /settings/openingbalances -> 400: {"code": 22006, "message": "Please enter a valid currency."}
  Toast A: 2026-01-05 93.29 clean -> journal 1622286000000123115
  Toast A: 2026-01-05 411.16 clean -> journal 1622286000000126116
  Toast A: 2026-01-06 274.91 clean -> journal 1622286000000117073
  Toast A: 2026-01-12 85.89 clean -> journal 1622286000000124157
  Toast A: 2026-01-12 104.7 clean -> journal 1622286000000111161
  Toast A: 2026-01-13 198.26 clean -> journal 1622286000000130134
  Toast A: 2026-01-20 52.88 clean -> journal 1622286000000106142
  Toast A: 2026-01-20 266.55 clean -> journal 1622286000000137061
  Toast A: 2026-01-20 154.3 clean -> journal 1622286000000125126
  Toast A: 2026-01-20 243.9 clean -> journal 1622286000000116129
  Toast A: 2026-01-23 60.95 clean -> journal 1622286000000129139
  Toast A: 2026-01-26 169.94 clean -> journal 1622286000000125149
  Toast A: 2026-02-13 16.35 clean -> journal 1622286000000106171
  Toast A: 2026-02-17 139.54 clean -> journal 1622286000000122186
  Toast A: 2026-02-17 112.79 clean -> journal 1622286000000112185
  Toast A: 2026-02-17 113.81 clean -> journal 1622286000000108106
  Toast A: 2026-02-17 329.88 clean -> journal 1622286000000131134
  Toast A: 2026-02-23 245.26 clean -> journal 1622286000000115143
  Toast A: 2026-02-23 354.26 clean -> journal 1622286000000132134
  Toast A: 2026-02-23 184.94 clean -> journal 1622286000000127063
  Toast A: 2026-03-02 164.07 clean -> journal 1622286000000127089
  Toast A: 2026-03-02 613.22 clean -> journal 1622286000000116155
  Toast A: 2026-03-02 93.3 clean -> journal 1622286000000113176
  Toast A: 2026-03-03 241.73 clean -> journal 1622286000000107192
  Toast A: 2026-03-09 18.55 clean -> journal 1622286000000107218
  Toast A: 2026-03-09 399.52 clean -> journal 1622286000000120234
  Toast A: 2026-03-09 391.45 clean -> journal 1622286000000127112
  Toast A: 2026-03-10 284.71 clean -> journal 1622286000000135049
  Toast A: 2026-03-16 294.72 clean -> journal 1622286000000123141
  Toast A: 2026-03-16 277.4 clean -> journal 1622286000000107244
  Toast A: 2026-03-16 163.27 clean -> journal 1622286000000127144
  Toast A: 2026-03-17 375.54 clean -> journal 1622286000000127167
  Toast A: 2026-03-23 378.44 clean -> journal 1622286000000128280
  Toast A: 2026-03-23 353.02 clean -> journal 1622286000000126147
  Toast A: 2026-03-23 150.45 clean -> journal 1622286000000129165
  Toast A: 2026-03-24 270.79 clean -> journal 1622286000000109193
  Toast A: 2026-03-30 339.66 clean -> journal 1622286000000122213
  Toast A: 2026-03-30 345.06 clean -> journal 1622286000000116184
  Toast A: 2026-03-30 392.57 clean -> journal 1622286000000101171
  Toast A: 2026-03-31 428.81 clean -> journal 1622286000000119271
  Toast A: 2026-04-06 299.59 clean -> journal 1622286000000137091
  Toast A: 2026-04-06 420.73 clean -> journal 1622286000000100116
  Toast A: 2026-04-06 258.27 clean -> journal 1622286000000109219
  Toast A: 2026-04-13 346.47 clean -> journal 1622286000000129191
  Toast A: 2026-04-13 396.29 clean -> journal 1622286000000107270
  Toast A: 2026-04-13 445.84 clean -> journal 1622286000000103162
  Toast A: 2026-04-14 306.29 clean -> journal 1622286000000101197
  Toast A: 2026-04-20 592.99 clean -> journal 1622286000000118092
  Toast A: 2026-04-20 277.19 clean -> journal 1622286000000129217
  Toast A: 2026-04-20 197.62 clean -> journal 1622286000000121125
  Toast A: 2026-04-21 112.58 clean -> journal 1622286000000108130
  Toast A: 2026-04-27 156.85 clean -> journal 1622286000000132166
  Toast A: 2026-04-27 515.81 clean -> journal 1622286000000133160
  Toast A: 2026-04-27 220.27 clean -> journal 1622286000000100149
  Toast A: 2026-04-28 301.54 clean -> journal 1622286000000134096
  Toast A: 2026-05-04 320.97 clean -> journal 1622286000000137120
  Toast A: 2026-05-04 152.75 clean -> journal 1622286000000112208
  Toast A: 2026-05-04 322.71 clean -> journal 1622286000000103191
  Toast A: 2026-05-05 251.23 clean -> journal 1622286000000111188
  Toast A: 2026-05-11 372.3 clean -> journal 1622286000000132189
  Toast A: 2026-05-11 407.9 clean -> journal 1622286000000102233
  Toast A: 2026-05-11 393.92 clean -> journal 1622286000000136125
  Toast A: 2026-05-12 204.49 clean -> journal 1622286000000120266
  Toast A: 2026-05-18 459.18 clean -> journal 1622286000000123170
  Toast A: 2026-05-18 516.01 clean -> journal 1622286000000100178
  Toast A: 2026-05-18 488.67 clean -> journal 1622286000000114144
  Toast A: 2026-05-19 568.62 clean -> journal 1622286000000109246
  Toast A: 2026-05-26 241.67 clean -> journal 1622286000000108161
  Toast A: 2026-05-26 341.3 clean -> journal 1622286000000134128
  Toast A: 2026-05-26 306.43 clean -> journal 1622286000000106195
  Toast A: 2026-05-26 164.57 clean -> journal 1622286000000125175
  Toast A: 2026-06-01 497.09 clean -> journal 1622286000000115166
  Toast A: 2026-06-01 302.91 clean -> journal 1622286000000129249
  Toast A: 2026-06-01 364.94 clean -> journal 1622286000000110168
  Toast A: 2026-06-02 573.86 clean -> journal 1622286000000125204
  Toast A: 2026-06-08 194.33 clean -> journal 1622286000000123196
  Toast A: 2026-06-08 195.22 clean -> journal 1622286000000125233
  Toast A: 2026-06-09 394.89 clean -> journal 1622286000000100204
  Toast A: 2026-06-15 166.34 clean -> journal 1622286000000112235
  Toast A: 2026-06-15 408.54 clean -> journal 1622286000000131164
  Toast A: 2026-06-15 246.24 clean -> journal 1622286000000105178
  Toast A: 2026-06-16 284.6 clean -> journal 1622286000000119299
  Toast A: 2026-06-22 500.52 clean -> journal 1622286000000116214
  Toast A: 2026-06-22 290.45 clean -> journal 1622286000000124184
  Toast A: 2026-06-23 238.45 clean -> journal 1622286000000118121
  Toast A: 2026-06-29 442.96 clean -> journal 1622286000000132216
  Toast A: 2026-06-29 215.14 clean -> journal 1622286000000115195
  Toast A: 2026-06-29 184.08 clean -> journal 1622286000000116243
  Toast A: 2026-06-30 339.73 clean -> journal 1622286000000137157
  Toast A: 2026-07-06 433.7 clean -> journal 1622286000000120289
  Toast A: 2026-07-07 106.7 clean -> journal 1622286000000133191
  Toast A: 2026-07-13 319.92 clean -> journal 1622286000000129278
  Toast A: 2026-07-13 346.77 clean -> journal 1622286000000123225
  Toast A: 2026-07-13 317.6 clean -> journal 1622286000000134155
  Toast A: 2026-07-14 270.2 clean -> journal 1622286000000111218
  Toast A: 2026-07-20 293.0 clean -> journal 1622286000000108188
  Toast A: 2026-07-20 468.88 clean -> journal 1622286000000134182
  Toast A: 2026-07-20 198.61 clean -> journal 1622286000000112264
  Toast A: 2026-07-21 122.39 clean -> journal 1622286000000117103
  Toast A: 2026-08-03 411.54 clean -> journal 1622286000000136153
  Toast A: 2026-08-03 413.57 clean -> journal 1622286000000119327
  Toast A: 2026-08-03 366.32 clean -> journal 1622286000000127191
  Toast A: 2026-08-04 114.29 clean -> journal 1622286000000115225
  Toast A: 2026-08-10 275.93 clean -> journal 1622286000000102265
  Toast A: 2026-08-10 434.04 clean -> journal 1622286000000133217
  Toast A: 2026-08-10 115.95 clean -> journal 1622286000000119353
  Toast A: 2026-08-11 343.8 clean -> journal 1622286000000113205
  Toast A: 2026-08-17 249.98 clean -> journal 1622286000000120315
  Toast A: 2026-08-17 483.76 clean -> journal 1622286000000135078
  Toast A: 2026-08-17 256.9 clean -> journal 1622286000000111244
  Toast A: 2026-08-18 481.29 clean -> journal 1622286000000132243
  Toast A: 2026-08-24 402.58 clean -> journal 1622286000000128314
  Toast A: 2026-08-24 264.03 clean -> journal 1622286000000123251
  FAIL toast 2026-08-24 316.25: POST /banktransactions/uncategorized/1622286000000095262/match: retries exhausted
  FAIL toast 2026-08-25 266.14: POST /journals: retries exhausted
  FAIL toast 2026-08-31 261.73: POST /journals: retries exhausted
  FAIL toast 2026-08-31 309.91: POST /journals: retries exhausted
  FAIL toast 2026-08-31 377.32: POST /journals: retries exhausted
  FAIL toast 2026-09-01 239.66: POST /journals: retries exhausted
  FAIL toast 2026-09-02 61.61: POST /journals: retries exhausted
  FAIL toast 2026-09-08 163.90: POST /journals: retries exhausted
  FAIL toast 2026-09-08 443.45: POST /journals: retries exhausted
  FAIL toast 2026-09-08 174.41: POST /journals: retries exhausted
  FAIL toast 2026-09-14 176.34: POST /journals: retries exhausted
  FAIL toast 2026-09-14 288.89: POST /journals: retries exhausted
  FAIL toast 2026-09-14 171.39: POST /journals: retries exhausted
  FAIL toast 2026-09-15 365.23: POST /journals: retries exhausted
  FAIL toast 2026-09-21 332.45: POST /journals: retries exhausted
  FAIL toast 2026-09-21 434.86: POST /journals: retries exhausted
  FAIL toast 2026-09-21 414.31: POST /journals: retries exhausted
```
