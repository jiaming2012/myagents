# Bills Google Sheet → Zoho Sheet

Replicates the **Bills** Google Sheet (Drive id `13aC4P_I9J4O9_6Y3dGZ7Gj74KmWfZJy1NjDJ7eVBi4U`,
last edited 2026-08-17) as a Zoho Sheet workbook. Written 2026-10-19.

## What was done

- The sheet was exported from Google Drive as xlsx (the native export, so formulas, number
  formats, fills and column widths carry over) and committed as
  `exports/bills-google-sheet-2026-08-17.xlsx` (111 KB).
- Nine tabs: Chart of Accounts, C.C. - Zero Balance, Yumyum Bank Accounts, Plan, Recurring,
  Investments, Loans, Propane, Notes. (The 2026-10-17 cross-check in
  `bills-sheet-crosscheck.md` read only five of them.)
- `zoho_sheet.py` uploads that file to Zoho Sheet with `method=workbook.upload`, which
  creates a new workbook from the file. Task targets below.

## Why a new Zoho token is needed

Zoho refresh tokens are scope-bound and never rotate. Both existing tokens were tried against
Zoho Sheet on 2026-10-19 and rejected with `2403 OAuth scope is not valid`:

| token | scope |
|---|---|
| `ZOHO_REFRESH_TOKEN` (Calendar) | `ZohoCalendar.calendar.READ ZohoCalendar.event.READ` |
| `ZOHO_BOOKS_REFRESH_TOKEN` | `ZohoBooks.fullaccess.all` |

Zoho Sheet needs `ZohoSheet.dataAPI.UPDATE,ZohoSheet.dataAPI.READ`. Same client id and
secret; a third refresh token, stored as `ZOHO_SHEET_REFRESH_TOKEN`.

## One-time operator step (2 minutes)

1. Open https://api-console.zoho.com and pick the existing client (the one the Calendar and
   Books tokens came from).
2. **Self Client** → *Generate Code* tab → scope
   `ZohoSheet.dataAPI.UPDATE,ZohoSheet.dataAPI.READ` → time 10 minutes → *Create* → copy
   the code.
   (Server-based client instead: open
   `https://accounts.zoho.com/oauth/v2/auth?response_type=code&client_id=<ID>&scope=ZohoSheet.dataAPI.UPDATE,ZohoSheet.dataAPI.READ&redirect_uri=<registered>&access_type=offline&prompt=consent`,
   copy `code` from the redirect, and add `--redirect-uri <registered>` to the auth task.)
3. Within those minutes:
   ```
   cd ~/projects/myagents/finance-tools
   task zoho:sheet:auth -- <code>
   ```
   This writes `ZOHO_SHEET_REFRESH_TOKEN` into `finance-tools/.env` and refuses the token if
   the Sheet scope is missing.

## Then

```
task zoho:sheet:bills      # creates the "Bills" workbook; prints resource id + URL
task zoho:sheet:list       # sanity check: lists workbooks the token can see
```

`task zoho:sheet:bills` is not idempotent: running it twice creates two workbooks named
Bills (delete the extra one in the Zoho Sheet UI). For a different file:
`task zoho:sheet:upload -- <file> [--name <name>]`.

## Files

| thing | location |
|---|---|
| Script | `finance-tools/zoho_sheet.py` (`--auth`, `--upload`, `--list`) |
| Export | `finance-tools/exports/bills-google-sheet-2026-08-17.xlsx` |
| Token | `.env` → `ZOHO_SHEET_REFRESH_TOKEN`; access-token cache `.zoho_sheet_token.json` (55 min, gitignored) |
| API | `https://sheet.zoho.com/api/v2/upload` (`workbook.upload`), `/workbooks` (`workbook.list`); header `Authorization: Zoho-oauthtoken <access>` |

## Caveats

- This is a snapshot, not a sync. Edits in Google Sheets after 2026-08-17 (there were none as
  of 2026-10-19) will not appear in Zoho; re-export and run `task zoho:sheet:upload` again.
- Google-only functions (e.g. `GOOGLEFINANCE`, `IMPORTRANGE`) export as cached values and
  will not recalculate in Zoho. Check the Investments tab after upload.
- The Chart of Accounts tab is 2023 data; Recurring, C.C. - Zero Balance and Loans are the
  live parts (see `bills-sheet-crosscheck.md`).
