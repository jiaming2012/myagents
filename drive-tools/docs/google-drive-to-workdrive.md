# Google Drive → Zoho WorkDrive migration

Moves one Google account's My Drive into one WorkDrive folder, keeping the folder
tree. Built for jamal@yumyums.kitchen (467 owned items, ~12 GB, plus 40 items owned
by Tanya and two service accounts that sit inside those folders).

Two ways exist. This tool is the scripted one; Zoho's own tool is noted at the end.

## How it works

```
Google Drive  --inventory-->  state/<account>/inventory.json
              --download--->  ~/Drive-Archive/<account>/   (local copy, keep it)
              --upload----->  WorkDrive <dest folder>/...   (same tree)
              --verify----->  size check per file, written to state/<account>/progress.json
```

- Every phase is resumable. Re-running skips what already succeeded.
- Google Docs, Sheets and Slides are exported as `.docx`, `.xlsx`, `.pptx` (WorkDrive
  previews these and can convert them to Writer/Sheet/Show on demand). Drawings become
  `.png`.
- Google Forms, shortcuts, Sites and Maps have no file form and are listed as skipped.
  The forms' "(Responses)" sheets migrate like any other sheet.
- Binary files are checked against Google's MD5 on download. WorkDrive only reports
  sizes, so `verify` compares sizes; the archive on disk is the byte-exact backup.
- Two files in the same folder with the same name become `name` and `name (2)`.
- Items in My Drive owned by someone else (Tanya's PDFs, the service-account sheets)
  are included because they are reachable from the tree. Items only *shared with* the
  account and never added to My Drive are skipped unless `--include-shared` is given;
  they then land under `_Shared with me/`.
- Upload route by size: ≤250 MB multipart, ≤1 GB stream, above that chunked session
  with per-chunk MD5. WorkDrive plan limits still apply to the two multi-GB files
  (8.7 GB `In Memory of Dadaji2.mov`, 2.9 GB `truck_menu_board_original.psb`); if the
  plan refuses them the report says so and everything else proceeds.

## One-time setup

1. **Google OAuth client.** The Gmail client in `email-tools/.env` is reused
   (`GMAIL_CLIENT_ID`/`GMAIL_CLIENT_SECRET`). Enable the **Google Drive API** on that
   Google Cloud project (APIs & Services → Library → Google Drive API → Enable). If the
   OAuth consent screen is "External / Testing", add jamal@yumyums.kitchen as a test user.
   Then:

   ```
   task drive:auth:google
   ```

   A browser opens; sign in as jamal@yumyums.kitchen and approve read-only Drive
   access. The refresh token is saved to `drive-tools/.tokens/`.

2. **Zoho WorkDrive token.** Zoho API console → the existing Self Client → Generate Code
   with scope

   ```
   WorkDrive.files.ALL,WorkDrive.team.READ,WorkDrive.users.READ,WorkDrive.workspace.READ,ZohoFiles.files.CREATE
   ```

   (if the console rejects a scope name, drop it and keep at least `WorkDrive.files.ALL`),
   then within a few minutes:

   ```
   task drive:auth:zoho -- <grant code>
   ```

3. **Destination folder.** Create a folder in WorkDrive (for example
   `My Folders / Google Drive (jamal@yumyums.kitchen)`), open it, and copy the id at the
   end of the URL into `drive-tools/.env` as `WORKDRIVE_DEST_FOLDER_ID`.
   `task drive:myfolders` prints the My Folders id per team if you want to create the
   folder at the top level by hand.

## Running it

```
task drive:inventory              # what is there; prints skipped items and totals
task drive:download               # ~12 GB to ~/Drive-Archive/jamal@yumyums.kitchen
task drive:upload -- --dry-run    # shows folders to create and files to send
task drive:upload                 # the real thing
task drive:verify
task drive:report
```

or all of it: `task drive:migrate`.

Interrupt freely; re-run the same command to continue. `task drive:upload -- --only
receipts_and_invoices` restricts a run to paths containing that text.

## After it is done

- Keep `~/Drive-Archive/<account>` until you have spot-checked WorkDrive.
- Sharing permissions are not copied; share the WorkDrive folder with Tanya by hand.
- Nothing is deleted from Google Drive by this tool.

## Zoho's built-in alternative

WorkDrive has its own Google Workspace migration (Admin Console → Migration). It needs a
paid WorkDrive team plan, a Google Workspace super admin, a Google Cloud service account
with domain-wide delegation, and a request to Zoho to enable the tab. It converts Docs
to Writer/Sheet/Show natively and keeps internal sharing, but skips Forms, cannot pick a
destination folder, and is all clicking. Use it instead if those trade-offs suit better;
this tool remains useful afterwards as the local backup.

Sources: Zoho's migration guide and FAQ at help.zoho.com (workdrive/migrations/google
and workdrive/frequently-asked-questions-faqs/migration), and the WorkDrive API docs
for upload, chunk upload sessions, create folder and get file info.
