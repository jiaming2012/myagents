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
3. Within those minutes, from the checkout that has the `zoho:sheet:*` targets (the
   `worktree-session-resume-task` worktree until this branch is merged to main):
   ```
   cd ~/projects/myagents/.claude/worktrees/session-resume-task/finance-tools
   task zoho:sheet:auth -- <code>
   ```
   This writes `ZOHO_SHEET_REFRESH_TOKEN` into the **main checkout's** `finance-tools/.env`
   (the script finds it through `git rev-parse --git-common-dir` when run from a worktree
   that has no `.env` of its own; `$FINANCE_TOOLS_DATA` overrides) and refuses the token if
   the Sheet scope is missing.

## Result (2026-10-19)

Operator minted the token and ran the upload the same day. The first copy
(`efajya53a7d6ef7a54db598c92cc10e9bc7cb`) landed in the account's default WorkDrive team,
an expired **Yumyums** trial team whose listing was paywalled, so it was invisible in the
live **Yumyums Smokin Grille** team. The operator deleted the trial team (only one team
remains now, so API uploads can only land there) and the file was re-uploaded:

Zoho Sheet workbook **Bills**, resource id **`efajy8c81bb8baeec4004bbff764f3c2fea8c`**,
https://sheet.zoho.com/sheet/open/efajy8c81bb8baeec4004bbff764f3c2fea8c, all nine
worksheets present (`worksheet.list`), TO_DATE fix below re-applied. The Sheet API has no
`workbook.delete` method, so the first copy may linger in `task zoho:sheet:list` until the
deleted team's 90-day purge; trash it from the Zoho Sheet UI if it still shows.

The upload itself was a one-off, so it has no task target of its own (operator rule:
targets are for reusable commands). It was run through the generic target:
```
task zoho:sheet:upload -- exports/bills-google-sheet-2026-08-17.xlsx --name Bills
```
Uploading again creates a second workbook named Bills; delete the extra in the Zoho UI.

Reusable targets: `zoho:sheet:auth` (re-mint the token), `zoho:sheet:list`,
`zoho:sheet:upload -- <file> [--name <name>]`. Once the branch is on main,
`cd ~/projects/myagents/finance-tools` works for all of these.

## Files

| thing | location |
|---|---|
| Script | `finance-tools/zoho_sheet.py` (`--auth`, `--upload`, `--list`) |
| Export | `finance-tools/exports/bills-google-sheet-2026-08-17.xlsx` |
| Token | `.env` → `ZOHO_SHEET_REFRESH_TOKEN`; access-token cache `.zoho_sheet_token.json` (55 min, gitignored) |
| API | `https://sheet.zoho.com/api/v2/upload` (`workbook.upload`), `/workbooks` (`workbook.list`); header `Authorization: Zoho-oauthtoken <access>` |

## Where API uploads actually go, and the WorkDrive folder

Correction (2026-10-19, later the same day): the first copy was still listed by
`workbook.list` after the trial team was deleted, so it never lived in that team. The Zoho
Sheet Data API files its uploads in **Zoho Sheet's own document space** (the old Zoho Docs
area, visible at https://sheet.zoho.com and by direct link), **not in WorkDrive**. That is why
neither WorkDrive team, nor the operator's new WorkDrive folder "bills", ever showed it.
Both API copies open fine by their links; they just cannot be browsed or moved from WorkDrive.

To have Bills inside the WorkDrive folder, the file has to be put there through WorkDrive:

- **`exports/bills-zoho-ready.xlsx`** is the same export with the eight TO_DATE formulas
  already rewritten to `DATE()` (conditional formats, validations and the `Accounts` name
  verified intact). Drag it into the WorkDrive folder; WorkDrive opens xlsx in Zoho Sheet
  and the formulas calculate. No API step needed.
- Or by API (**what was done, 2026-10-19**): the operator re-minted `ZOHO_SHEET_REFRESH_TOKEN`
  with `ZohoSheet.dataAPI.UPDATE,ZohoSheet.dataAPI.READ,WorkDrive.files.CREATE` (the script's
  `SCOPE` now includes it), then
  `task zoho:workdrive:upload -- exports/bills-zoho-ready.xlsx --folder efajy36818489827748c8a271759dd235e0d6 --name Bills.xlsx`
  put **Bills.xlsx** in the WorkDrive folder **bills** (My Folders of team Yumyums Smokin
  Grille): file id `efajyb8b15e86a20a48e0b3edab6cf4fb18c5`,
  https://workdrive.zoho.com/file/efajyb8b15e86a20a48e0b3edab6cf4fb18c5. WorkDrive's MD5
  matched the local file. The folder id is the tail of the folder's WorkDrive URL; the token
  cannot list folders (no `WorkDrive.files.READ`), so the operator supplies it.

This WorkDrive file is now the copy to use. The two Sheet-API workbooks are orphans.

## Google-only functions → Zoho (done 2026-10-19)

All 120 formulas in the export were inventoried. Functions used: IF, MAX, MIN, SUM, SUMIF,
SUMIFS, AVERAGE, NOW, DAY, MONTH, YEAR, OR, IFERROR, ROUNDDOWN, CONCATENATE, and one
Google-only function, **`TO_DATE`**, in `Recurring!J2:J10` ("Days Due", rows 2–5 and 7–10).
No GOOGLEFINANCE / IMPORTRANGE / QUERY / ARRAYFORMULA anywhere.

Google's xlsx export wraps unsupported functions as
`=IFERROR(__xludf.DUMMYFUNCTION("…"),<cached value>)`, so after upload those eight cells
were frozen at their cached values. Replaced in the Zoho workbook through the API
(`cell.content.set`, which evaluates content starting with `=`):

| Google | Zoho |
|---|---|
| `=MAX(0, ROUNDDOWN(TO_DATE(CONCATENATE($L$2,"/",I2,"/",$M$2)-$N$2)))` | `=MAX(0, ROUNDDOWN(DATE($M$2,$L$2,I2)-$N$2,0))` |

Same meaning: whole days from today (`N2 = NOW()`) to this month's auto-debit day (`I`),
floored at 0. Read back after the write: J9 (Progressive, day 28) = 8 on 2026-10-19, the
rest 0; the same expression evaluated in a scratch cell gave 8.78 → 8. The committed xlsx
export is left as Google produced it; if it is ever re-uploaded, redo these eight cells.

## Caveats

- This is a snapshot, not a sync. Edits in Google Sheets after 2026-08-17 (there were none as
  of 2026-10-19) will not appear in Zoho; re-export and run `task zoho:sheet:upload` again.
- The Chart of Accounts tab is 2023 data; Recurring, C.C. - Zero Balance and Loans are the
  live parts (see `bills-sheet-crosscheck.md`).
