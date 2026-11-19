#!/usr/bin/env python3
"""Google Drive -> Zoho WorkDrive migration.

One Google account's My Drive is mirrored, folder for folder, into one WorkDrive
folder. Every phase is resumable: state lives in STATE_DIR and a re-run skips
whatever already succeeded.

    auth-google   one-time browser consent for the Google account (drive.readonly)
    auth-zoho     exchange a Zoho Self Client grant code for a WorkDrive refresh token
    inventory     list every file/folder in the Google account -> state/inventory.json
    download      fetch everything into ARCHIVE_DIR (Google-native docs exported to Office)
    upload        recreate the tree under the WorkDrive destination folder
    verify        compare WorkDrive sizes against the local archive
    report        print a summary of what moved, what was skipped, and why
    myfolders     print the WorkDrive "My Folders" id of the signed-in user

Env (drive-tools/.env, falling back to finance-tools/.env and email-tools/.env):
    GOOGLE_ACCOUNT                  Google account to migrate (login hint + state/archive name)
    GOOGLE_CLIENT_ID/SECRET         OAuth desktop client (falls back to GMAIL_CLIENT_ID/SECRET)
    ZOHO_CLIENT_ID/SECRET           Zoho Self Client
    ZOHO_WORKDRIVE_REFRESH_TOKEN    written by auth-zoho
    WORKDRIVE_DEST_FOLDER_ID        destination folder (id at the end of its WorkDrive URL)
    ARCHIVE_DIR                     local copy of the Drive (default ~/Drive-Archive/<account>)
    STATE_DIR                       state/<account> under this directory by default
"""
import argparse
import hashlib
import http.server
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
for env in (HERE / ".env", HERE.parent / "finance-tools" / ".env", HERE.parent / "email-tools" / ".env"):
    load_dotenv(env)

ACCOUNT = os.getenv("GOOGLE_ACCOUNT", "jamal@yumyums.kitchen")
STATE_DIR = Path(os.getenv("STATE_DIR") or HERE / "state" / ACCOUNT)
ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR") or Path.home() / "Drive-Archive" / ACCOUNT).expanduser()
GOOGLE_TOKEN = HERE / ".tokens" / f"google-{ACCOUNT}.json"
ZOHO_TOKEN_CACHE = HERE / ".tokens" / "zoho-workdrive-access.json"
ENV_FILE = HERE / ".env"

GOOGLE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REDIRECT_PORT = 8086
DRIVE = "https://www.googleapis.com/drive/v3"

ZOHO_TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"
ZOHO_SCOPE = "WorkDrive.files.ALL,WorkDrive.team.READ,WorkDrive.users.READ,WorkDrive.workspace.READ,ZohoFiles.files.CREATE"
WD = "https://www.zohoapis.com/workdrive/api/v1"
WD_UPLOAD = "https://upload.zoho.com/workdrive-api/v1/stream/upload"
ZOHO_RT_KEY = "ZOHO_WORKDRIVE_REFRESH_TOKEN"

MB = 1024 * 1024
SIMPLE_UPLOAD_MAX = 250 * MB      # POST /upload (multipart)
STREAM_UPLOAD_MAX = 1024 * MB     # stream upload; above this WorkDrive wants chunk sessions

FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
EXPORTS = {  # Google-native type -> (export mime, extension)
    "application/vnd.google-apps.document": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "application/vnd.google-apps.drawing": ("image/png", ".png"),
    "application/vnd.google-apps.script": ("application/vnd.google-apps.script+json", ".json"),
}
NOT_EXPORTABLE = {  # Google-native types with no file representation
    "application/vnd.google-apps.form": "Google Form (no export format; its responses sheet migrates on its own)",
    "application/vnd.google-apps.site": "Google Site",
    "application/vnd.google-apps.map": "Google My Map",
    "application/vnd.google-apps.jam": "Jamboard",
}

INVENTORY = STATE_DIR / "inventory.json"
PROGRESS = STATE_DIR / "progress.json"
LOG = STATE_DIR / "log.txt"


# ---------------------------------------------------------------- utilities

def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        fh.write(line + "\n")


def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(path)


def md5_of(path, chunk=8 * MB):
    h = hashlib.md5()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _write_env(key, value):
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    lines = [l for l in lines if not l.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    os.environ[key] = value


def retrying(fn, *args, tries=6, what="request", **kwargs):
    """Call fn(*args, **kwargs); retry on 429/5xx/connection errors with backoff."""
    delay = 2
    for attempt in range(1, tries + 1):
        try:
            r = fn(*args, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt == tries:
                raise
            log(f"  {what}: {e.__class__.__name__}, retry {attempt}/{tries} in {delay}s")
        else:
            if r.status_code not in (429, 500, 502, 503, 504) or attempt == tries:
                return r
            delay = int(r.headers.get("Retry-After") or delay)
            log(f"  {what}: HTTP {r.status_code}, retry {attempt}/{tries} in {delay}s")
        time.sleep(delay)
        delay = min(delay * 2, 120)


# ------------------------------------------------------------- name mapping

_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize(name, maxlen=200):
    """A name usable on macOS and WorkDrive: no path separators or control chars."""
    name = _BAD.sub("_", name).strip().strip(".") or "_unnamed"
    if len(name) > maxlen:
        stem, ext = os.path.splitext(name)
        name = stem[: maxlen - len(ext)] + ext
    return name


def dedupe(names):
    """Make sibling names unique, keeping the first occurrence as-is: a, a (2), a (3)."""
    seen, out = {}, []
    for n in names:
        key = n.lower()
        if key not in seen:
            seen[key] = 1
            out.append(n)
            continue
        stem, ext = os.path.splitext(n)
        while True:
            seen[key] += 1
            cand = f"{stem} ({seen[key]}){ext}"
            if cand.lower() not in seen:
                seen[cand.lower()] = 1
                out.append(cand)
                break
    return out


def local_name(item):
    """File name in the archive: Google-native docs get the export extension."""
    name = sanitize(item["name"])
    exp = EXPORTS.get(item["mimeType"])
    if exp and not name.lower().endswith(exp[1]):
        name += exp[1]
    return name


def build_tree(items, root_id, include_shared=False):
    """Assign each item a relative path under the archive.

    Returns (nodes, skipped). nodes: id -> item with 'path' (posix, relative) and
    'kind' in {folder, file, export}. skipped: list of (item, reason)."""
    by_id = {i["id"]: dict(i) for i in items}
    children = {}
    for it in by_id.values():
        for p in it.get("parents") or []:
            children.setdefault(p, []).append(it["id"])
    reachable = set()
    stack = [root_id]
    while stack:
        pid = stack.pop()
        for cid in children.get(pid, []):
            if cid not in reachable:
                reachable.add(cid)
                stack.append(cid)
    nodes, skipped = {}, []

    def place(rel, ids):
        kids = sorted((by_id[i] for i in ids), key=lambda x: (x["name"].lower(), x.get("createdTime", ""), x["id"]))
        usable = []
        for it in kids:
            m = it["mimeType"]
            if m == SHORTCUT:
                target = (it.get("shortcutDetails") or {}).get("targetId", "?")
                skipped.append((it, f"Drive shortcut (points at {target})"))
            elif m in NOT_EXPORTABLE:
                skipped.append((it, NOT_EXPORTABLE[m]))
            elif m.startswith("application/vnd.google-apps.") and m not in EXPORTS and m != FOLDER:
                skipped.append((it, f"unsupported Google type {m}"))
            else:
                usable.append(it)
        names = dedupe([local_name(it) for it in usable])
        for it, name in zip(usable, names):
            path = f"{rel}/{name}" if rel else name
            it["path"] = path
            it["kind"] = "folder" if it["mimeType"] == FOLDER else ("export" if it["mimeType"] in EXPORTS else "file")
            nodes[it["id"]] = it
            if it["kind"] == "folder":
                place(path, children.get(it["id"], []))

    place("", children.get(root_id, []))
    # Owned items whose parent chain never reaches My Drive (orphans) and, optionally,
    # items shared with the account that it never added to My Drive.
    orphans = [i for i in by_id if i not in reachable and i != root_id]
    owned = [i for i in orphans if by_id[i].get("ownedByMe")]
    shared = [i for i in orphans if not by_id[i].get("ownedByMe")]
    # only top-level orphans: children of an orphan folder are placed by recursion
    orphan_set = set(orphans)
    owned = [i for i in owned if not any(p in orphan_set for p in by_id[i].get("parents") or [])]
    shared = [i for i in shared if not any(p in orphan_set for p in by_id[i].get("parents") or [])]
    if owned:
        place("_Orphaned (no parent folder)", owned)
    if include_shared and shared:
        place("_Shared with me", shared)
    else:
        for i in shared:
            skipped.append((by_id[i], "shared with me, not in My Drive (use --include-shared)"))
    return nodes, skipped


def upload_route(size):
    if size <= SIMPLE_UPLOAD_MAX:
        return "simple"
    if size <= STREAM_UPLOAD_MAX:
        return "stream"
    return "chunk"


# ------------------------------------------------------------------- google

def google_client():
    cid = os.getenv("GOOGLE_CLIENT_ID") or os.getenv("GMAIL_CLIENT_ID")
    sec = os.getenv("GOOGLE_CLIENT_SECRET") or os.getenv("GMAIL_CLIENT_SECRET")
    if not cid or not sec:
        sys.exit("GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET (or GMAIL_CLIENT_ID/SECRET) not set")
    return cid, sec


def auth_google(no_browser=False):
    cid, sec = google_client()
    redirect = f"http://localhost:{GOOGLE_REDIRECT_PORT}/callback"
    params = dict(client_id=cid, redirect_uri=redirect, response_type="code", scope=GOOGLE_SCOPE,
                  access_type="offline", prompt="consent", login_hint=ACCOUNT)
    url = GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"You can close this tab and return to the terminal.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("localhost", GOOGLE_REDIRECT_PORT), Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    print(f"Sign in as {ACCOUNT} and approve read-only Drive access:\n\n  {url}\n")
    if not no_browser:
        webbrowser.open(url)
    for _ in range(600):
        if got:
            break
        time.sleep(1)
    srv.server_close()
    if "code" not in got:
        sys.exit(f"No authorization code received: {got or 'timed out'}")
    r = requests.post(GOOGLE_TOKEN_URL, data=dict(code=got["code"], client_id=cid, client_secret=sec,
                                                   redirect_uri=redirect, grant_type="authorization_code"), timeout=30)
    tok = r.json()
    if "refresh_token" not in tok:
        sys.exit(f"Google token exchange failed: {tok}")
    tok["exp"] = time.time() + tok.get("expires_in", 3600) - 60
    GOOGLE_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    GOOGLE_TOKEN.write_text(json.dumps(tok))
    GOOGLE_TOKEN.chmod(0o600)
    who = requests.get(f"{DRIVE}/about", params={"fields": "user(emailAddress)"},
                       headers={"Authorization": "Bearer " + tok["access_token"]}, timeout=30).json()
    email = who.get("user", {}).get("emailAddress")
    print(f"Saved {GOOGLE_TOKEN} for {email}")
    if email and email.lower() != ACCOUNT.lower():
        print(f"WARNING: you signed in as {email}, but GOOGLE_ACCOUNT is {ACCOUNT}")


def google_token():
    if not GOOGLE_TOKEN.exists():
        sys.exit(f"{GOOGLE_TOKEN} missing. Run: task drive:auth:google")
    tok = json.loads(GOOGLE_TOKEN.read_text())
    if tok.get("exp", 0) > time.time():
        return tok["access_token"]
    cid, sec = google_client()
    r = requests.post(GOOGLE_TOKEN_URL, data=dict(refresh_token=tok["refresh_token"], client_id=cid,
                                                   client_secret=sec, grant_type="refresh_token"), timeout=30).json()
    if "access_token" not in r:
        sys.exit(f"Google refresh failed: {r}. Re-run: task drive:auth:google")
    tok.update(access_token=r["access_token"], exp=time.time() + r.get("expires_in", 3600) - 60)
    GOOGLE_TOKEN.write_text(json.dumps(tok))
    return tok["access_token"]


def gget(path, **params):
    r = retrying(requests.get, f"{DRIVE}/{path}", params=params,
                 headers={"Authorization": "Bearer " + google_token()}, timeout=60, what=f"GET {path}")
    if r.status_code >= 400:
        sys.exit(f"Google API {path}: HTTP {r.status_code} {r.text[:300]}")
    return r.json()


FIELDS = ("nextPageToken,files(id,name,mimeType,parents,size,md5Checksum,modifiedTime,createdTime,"
          "owners(emailAddress),ownedByMe,shared,shortcutDetails,exportLinks,trashed)")


def inventory(include_shared=False):
    root = gget("files/root", fields="id")["id"]
    items, token = [], None
    while True:
        page = gget("files", q="trashed = false", pageSize=1000, fields=FIELDS, corpora="user",
                    spaces="drive", **({"pageToken": token} if token else {}))
        items += page.get("files", [])
        token = page.get("nextPageToken")
        log(f"inventory: {len(items)} items so far")
        if not token:
            break
    nodes, skipped = build_tree(items, root, include_shared)
    data = {"account": ACCOUNT, "root": root, "taken": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "include_shared": include_shared, "items": items,
            "nodes": nodes, "skipped": [{"item": it, "reason": why} for it, why in skipped]}
    save_json(INVENTORY, data)
    files = [n for n in nodes.values() if n["kind"] != "folder"]
    total = sum(int(n.get("size") or 0) for n in files)
    log(f"inventory: {len(nodes)} items in tree ({len(files)} files, {human(total)} of binary content, "
        f"{sum(1 for n in files if n['kind'] == 'export')} Google-native docs to export), {len(skipped)} skipped")
    for it, why in skipped:
        log(f"  skip: {it['name']}  [{why}]")
    log(f"wrote {INVENTORY}")


def _stream_to(url, dest, headers, expect_md5=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    r = retrying(requests.get, url, headers=headers, stream=True, timeout=120, what="download")
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} {r.text[:200]}")
    h = hashlib.md5()
    with tmp.open("wb") as fh:
        for block in r.iter_content(8 * MB):
            fh.write(block)
            h.update(block)
    if expect_md5 and h.hexdigest() != expect_md5:
        tmp.unlink()
        raise RuntimeError(f"md5 mismatch: got {h.hexdigest()} expected {expect_md5}")
    tmp.replace(dest)
    return h.hexdigest(), dest.stat().st_size


def _already_local(n, dest, rec):
    """True when the archive copy matches what Google reports (size + md5 for binaries)."""
    if not dest.exists():
        return False
    if n["kind"] == "export":
        return True
    if dest.stat().st_size != int(n.get("size") or 0):
        return False
    want = n.get("md5Checksum")
    if not want:
        return True
    if rec.get("md5") == want:
        return True
    return md5_of(dest) == want


def download(force=False):
    inv = load_json(INVENTORY, None) or sys.exit("run inventory first")
    prog = load_json(PROGRESS, {"files": {}, "folders": {}})
    files = [n for n in inv["nodes"].values() if n["kind"] != "folder"]
    files.sort(key=lambda n: n["path"].lower())
    done = fail = present = 0
    for i, n in enumerate(files, 1):
        dest = ARCHIVE_DIR / n["path"]
        rec = prog["files"].setdefault(n["id"], {})
        if not force and _already_local(n, dest, rec):
            rec.update(local=str(dest), bytes=dest.stat().st_size,
                       md5=rec.get("md5") or n.get("md5Checksum") or md5_of(dest), downloaded=True, error=None)
            present += 1
            continue
        headers = {"Authorization": "Bearer " + google_token()}
        try:
            if n["kind"] == "export":
                mime = EXPORTS[n["mimeType"]][0]
                url = (n.get("exportLinks") or {}).get(mime) or \
                    f"{DRIVE}/files/{n['id']}/export?mimeType={urllib.parse.quote(mime)}"
                md5, nbytes = _stream_to(url, dest, headers)
            else:
                md5, nbytes = _stream_to(f"{DRIVE}/files/{n['id']}?alt=media", dest, headers, n.get("md5Checksum"))
            rec.update(local=str(dest), bytes=nbytes, md5=md5, downloaded=True, error=None)
            done += 1
            log(f"[{i}/{len(files)}] got {n['path']} ({human(nbytes)})")
        except Exception as e:  # keep going; the report lists failures
            rec.update(downloaded=False, error=str(e))
            fail += 1
            log(f"[{i}/{len(files)}] FAILED {n['path']}: {e}")
        if i % 20 == 0:
            save_json(PROGRESS, prog)
    save_json(PROGRESS, prog)
    log(f"download: {done} fetched, {present} already present, {fail} failed -> {ARCHIVE_DIR}")


# --------------------------------------------------------------------- zoho

def zoho_client():
    cid, sec = os.getenv("ZOHO_CLIENT_ID"), os.getenv("ZOHO_CLIENT_SECRET")
    if not cid or not sec:
        sys.exit("ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET not set")
    return cid, sec


def auth_zoho(code):
    cid, sec = zoho_client()
    r = requests.post(ZOHO_TOKEN_URL, data=dict(grant_type="authorization_code", client_id=cid,
                                               client_secret=sec, code=code), timeout=20).json()
    if "refresh_token" not in r:
        sys.exit(f"Zoho token exchange failed: {r}\nGrant codes expire in minutes; generate a fresh one "
                 f"(API console -> Self Client -> Generate Code) with scope:\n  {ZOHO_SCOPE}")
    granted = r.get("scope", "")
    if "WorkDrive.files" not in granted:
        sys.exit(f"Token granted with scope {granted!r}; it needs at least WorkDrive.files.ALL")
    _write_env(ZOHO_RT_KEY, r["refresh_token"])
    ZOHO_TOKEN_CACHE.unlink(missing_ok=True)
    print(f"Saved {ZOHO_RT_KEY} to {ENV_FILE} (scope: {granted})")


def zoho_token():
    c = load_json(ZOHO_TOKEN_CACHE, {})
    if c.get("exp", 0) > time.time() + 60:
        return c["access_token"]
    rt = os.getenv(ZOHO_RT_KEY)
    if not rt:
        sys.exit(f"{ZOHO_RT_KEY} not set. Run: task drive:auth:zoho -- <grant code>  (scope {ZOHO_SCOPE})")
    cid, sec = zoho_client()
    r = requests.post(ZOHO_TOKEN_URL, data=dict(grant_type="refresh_token", client_id=cid, client_secret=sec,
                                               refresh_token=rt), timeout=20).json()
    if "access_token" not in r:
        sys.exit(f"Zoho token error: {r}")
    save_json(ZOHO_TOKEN_CACHE, {"access_token": r["access_token"], "exp": time.time() + 3300})
    return r["access_token"]


def zh(extra=None):
    h = {"Authorization": "Zoho-oauthtoken " + zoho_token(), "Accept": "application/vnd.api+json"}
    h.update(extra or {})
    return h


def find_key(obj, *keys):
    """Depth-first search for the first of keys in a nested JSON object."""
    if isinstance(obj, dict):
        for k in keys:
            if obj.get(k):
                return obj[k]
        for v in obj.values():
            got = find_key(v, *keys)
            if got:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = find_key(v, *keys)
            if got:
                return got
    return None


def wd_json(r, what):
    try:
        d = r.json()
    except ValueError:
        raise RuntimeError(f"{what}: HTTP {r.status_code} {r.text[:300]}")
    if r.status_code >= 400 or "errors" in d:
        raise RuntimeError(f"{what}: HTTP {r.status_code} {json.dumps(d)[:500]}")
    return d


def wd_children(folder_id, kind="files"):
    """Children of a WorkDrive folder as (name, id, is_folder). kind: files | folders."""
    out, offset = [], 0
    while True:
        r = retrying(requests.get, f"{WD}/files/{folder_id}/{kind}",
                     params={"page[limit]": 50, "page[offset]": offset}, headers=zh(), timeout=60, what="list")
        d = wd_json(r, f"list {folder_id}")
        batch = d.get("data", [])
        for it in batch:
            a = it.get("attributes", {})
            out.append((a.get("name"), it.get("id"), bool(a.get("is_folder"))))
        if len(batch) < 50:
            return out
        offset += 50


def wd_mkdir(parent_id, name):
    body = {"data": {"attributes": {"parent_id": parent_id, "name": name}, "type": "files"}}
    r = retrying(requests.post, f"{WD}/files", json=body,
                 headers=zh({"content-type": "application/vnd.api+json"}), timeout=60, what="mkdir")
    d = wd_json(r, f"mkdir {name}")
    data = d.get("data")
    if isinstance(data, dict) and data.get("id"):
        return data["id"]
    return find_key(d, "resourceId", "resource_id", "id")


def wd_ensure_folder(parent_id, name, dry=False):
    """(folder id, created?) for name under parent_id; reuses an existing folder of that name."""
    if not str(parent_id).startswith("DRY-"):
        for n, rid, is_folder in wd_children(parent_id, "folders"):
            if is_folder and n == name:
                return rid, False
    if dry:
        return f"DRY-{name}", True
    return wd_mkdir(parent_id, name), True


def wd_upload_simple(path, parent_id, name):
    with Path(path).open("rb") as fh:
        r = retrying(requests.post, f"{WD}/upload",
                     params={"parent_id": parent_id, "filename": name, "override-name-exist": "true"},
                     files={"content": (name, fh)}, headers=zh(), timeout=900, what="upload")
    d = wd_json(r, f"upload {name}")
    return find_key(d, "resource_id", "RESOURCE_ID", "id")


def wd_upload_stream(path, parent_id, name):
    size = Path(path).stat().st_size
    uid = f"gd-{int(time.time())}-{hashlib.md5(name.encode()).hexdigest()[:8]}"
    with Path(path).open("rb") as fh:
        r = requests.post(WD_UPLOAD, data=fh, headers=zh({
            "x-filename": urllib.parse.quote(name), "x-parent_id": parent_id, "upload-id": uid,
            "x-streammode": "1", "x-override-name-exist": "true", "Content-Length": str(size),
            "Content-Type": "application/octet-stream"}), timeout=3600)
    d = wd_json(r, f"stream upload {name}")
    rid = find_key(d, "resource_id", "RESOURCE_ID")
    if not rid:  # fall back to looking the file up by name in the folder
        for n, fid, is_folder in wd_children(parent_id):
            if n == name and not is_folder:
                rid = fid
    return rid


def wd_upload_chunked(path, parent_id, name):
    size = Path(path).stat().st_size
    r = retrying(requests.post, f"{WD}/uploadsession/create",
                 params={"size": size, "file_name": name, "parent_id": parent_id, "name_conflict": "update"},
                 headers=zh(), timeout=60, what="session create")
    s = wd_json(r, f"chunk session {name}")
    upload_id = find_key(s, "upload_id")
    nxt = find_key(s, "next_chunk")
    if not upload_id or not nxt:
        raise RuntimeError(f"chunk session for {name}: unexpected response {json.dumps(s)[:300]}")
    with Path(path).open("rb") as fh:
        while nxt:
            start, end = (int(x) for x in str(nxt).split("-"))
            fh.seek(start)
            blob = fh.read(end - start + 1)
            r = retrying(requests.post, WD_UPLOAD, data=blob, headers=zh({
                "upload-id": upload_id, "Content-Length": str(len(blob)),
                "Content-Range": f"bytes {start}-{end}/{size}", "x-streammode": "1",
                "Content-Type": "application/octet-stream"}), timeout=1800, what="chunk")
            d = wd_json(r, f"chunk {start}-{end} of {name}")
            got = find_key(d, "md5_checksum")
            want = hashlib.md5(blob).hexdigest()
            if got and got != want:
                raise RuntimeError(f"chunk {start}-{end} md5 mismatch ({got} != {want})")
            log(f"    chunk {start}-{end} ok ({human(end - start + 1)})")
            nxt = find_key(d, "next_chunk")
            if end + 1 >= size:
                nxt = None
    r = retrying(requests.post, f"{WD}/uploadsession/commit",
                 params={"upload_id": upload_id, "parent_id": parent_id, "name_conflict": "update"},
                 headers=zh(), timeout=300, what="session commit")
    d = wd_json(r, f"commit {name}")
    return find_key(d, "resource_id", "RESOURCE_ID")


def upload(dest, dry=False, only=None):
    inv = load_json(INVENTORY, None) or sys.exit("run inventory first")
    prog = load_json(PROGRESS, {"files": {}, "folders": {}})
    dest = dest or os.getenv("WORKDRIVE_DEST_FOLDER_ID") or sys.exit("--dest or WORKDRIVE_DEST_FOLDER_ID required")
    nodes = inv["nodes"]
    folders = sorted((n for n in nodes.values() if n["kind"] == "folder"), key=lambda n: n["path"].count("/"))
    files = sorted((n for n in nodes.values() if n["kind"] != "folder"), key=lambda n: n["path"].lower())
    # synthetic top-level folders (_Orphaned…, _Shared with me) are not Google folders
    synthetic = sorted({n["path"].split("/")[0] for n in nodes.values() if n["path"].startswith("_")})
    wd_of = {"": dest}
    wd_of.update(prog["folders"])

    def parent_wd(path):
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        if parent not in wd_of:
            raise RuntimeError(f"parent folder not created yet: {parent!r}")
        return wd_of[parent]

    created = 0
    for name in synthetic:
        if name not in wd_of:
            wd_of[name], new = wd_ensure_folder(dest, name, dry)
            created += new
    for n in folders:
        if n["path"] in wd_of:
            continue
        rid, new = wd_ensure_folder(parent_wd(n["path"]), n["path"].rsplit("/", 1)[-1], dry)
        wd_of[n["path"]] = rid
        created += new
        if new:
            log(f"mkdir {n['path']}  -> {rid}")
    if not dry:
        prog["folders"] = {k: v for k, v in wd_of.items() if k}
        save_json(PROGRESS, prog)
    log(f"folders: {len(folders) + len(synthetic)} in tree, {created} created now")

    done = fail = present = 0
    for i, n in enumerate(files, 1):
        if only and only not in n["path"]:
            continue
        rec = prog["files"].setdefault(n["id"], {})
        if rec.get("workdrive_id"):
            present += 1
            continue
        local = Path(rec.get("local") or ARCHIVE_DIR / n["path"])
        if not local.exists():
            rec.update(upload_error="not downloaded")
            fail += 1
            continue
        size = local.stat().st_size
        route = upload_route(size)
        name = n["path"].rsplit("/", 1)[-1]
        if dry:
            log(f"[{i}/{len(files)}] would upload {n['path']} ({human(size)}, {route})")
            continue
        try:
            parent = parent_wd(n["path"])
            fn = {"simple": wd_upload_simple, "stream": wd_upload_stream, "chunk": wd_upload_chunked}[route]
            rid = fn(local, parent, name)
            if not rid:
                raise RuntimeError("upload returned no resource id")
            rec.update(workdrive_id=rid, workdrive_parent=parent, uploaded_bytes=size, upload_error=None)
            done += 1
            log(f"[{i}/{len(files)}] up {n['path']} ({human(size)}, {route}) -> {rid}")
        except Exception as e:
            rec.update(upload_error=str(e))
            fail += 1
            log(f"[{i}/{len(files)}] FAILED {n['path']}: {e}")
        if i % 10 == 0:
            save_json(PROGRESS, prog)
    save_json(PROGRESS, prog)
    log(f"upload: {done} uploaded, {present} already there, {fail} failed")


def verify():
    inv = load_json(INVENTORY, None) or sys.exit("run inventory first")
    prog = load_json(PROGRESS, {"files": {}, "folders": {}})
    ok = bad = missing = 0
    for n in inv["nodes"].values():
        if n["kind"] == "folder":
            continue
        rec = prog["files"].get(n["id"], {})
        rid = rec.get("workdrive_id")
        if not rid:
            missing += 1
            continue
        r = retrying(requests.get, f"{WD}/files/{rid}", headers=zh(), timeout=60, what="file info")
        try:
            d = wd_json(r, f"info {rid}")
        except RuntimeError as e:
            rec["verify"] = f"error: {e}"
            bad += 1
            continue
        a = (d.get("data") or {}).get("attributes", {})
        remote = int((a.get("storage_info") or {}).get("size_in_bytes") or a.get("size_in_bytes") or -1)
        local = int(rec.get("bytes") or rec.get("uploaded_bytes") or Path(rec["local"]).stat().st_size)
        if remote == local:
            rec["verify"] = "ok"
            ok += 1
        else:
            rec["verify"] = f"size mismatch local={local} remote={remote}"
            bad += 1
            log(f"MISMATCH {n['path']}: {rec['verify']}")
    save_json(PROGRESS, prog)
    log(f"verify: {ok} ok, {bad} mismatched, {missing} not uploaded")


def myfolders():
    me = wd_json(retrying(requests.get, f"{WD}/users/me", headers=zh(), timeout=30, what="me"), "users/me")
    zuid = me["data"]["id"]
    print(f"user id {zuid} ({me['data']['attributes'].get('email_id', '')})")
    teams = wd_json(retrying(requests.get, f"{WD}/users/{zuid}/teams", headers=zh(), timeout=30, what="teams"), "teams")
    for t in teams.get("data", []):
        tid, tname = t["id"], t["attributes"].get("name")
        cu = wd_json(retrying(requests.get, f"{WD}/teams/{tid}/currentuser", headers=zh(), timeout=30,
                              what="currentuser"), "currentuser")
        mid = cu["data"]["id"]
        ps = wd_json(retrying(requests.get, f"{WD}/users/{mid}/privatespace", headers=zh(), timeout=30,
                              what="privatespace"), "privatespace")
        for p in ps.get("data", []):
            print(f"team {tname!r} ({tid}): My Folders id = {p['id']}")


def report():
    inv = load_json(INVENTORY, None) or sys.exit("run inventory first")
    prog = load_json(PROGRESS, {"files": {}, "folders": {}})
    nodes = inv["nodes"]
    files = [n for n in nodes.values() if n["kind"] != "folder"]
    recs = {i: prog["files"].get(i, {}) for i in nodes}
    dl = sum(1 for n in files if recs[n["id"]].get("downloaded"))
    up = sum(1 for n in files if recs[n["id"]].get("workdrive_id"))
    ver = sum(1 for n in files if recs[n["id"]].get("verify") == "ok")
    print(f"Google account   {inv['account']}  (inventory taken {inv['taken']})")
    print(f"Archive          {ARCHIVE_DIR}")
    print(f"Folders          {sum(1 for n in nodes.values() if n['kind'] == 'folder')}")
    print(f"Files            {len(files)}  downloaded {dl}  uploaded {up}  verified {ver}")
    print(f"Content          {human(sum(int(n.get('size') or 0) for n in files))} binary + "
          f"{sum(1 for n in files if n['kind'] == 'export')} exported Google docs")
    for n in sorted(files, key=lambda n: n["path"].lower()):
        r = recs[n["id"]]
        problem = r.get("error") or r.get("upload_error") or (r.get("verify") if r.get("verify", "ok") != "ok" else None)
        if problem:
            print(f"  PROBLEM {n['path']}: {problem}")
    if inv["skipped"]:
        print(f"Skipped in inventory ({len(inv['skipped'])}):")
        for s in inv["skipped"]:
            print(f"  {s['item']['name']}  [{s['reason']}]")
    big = [n for n in files if int(n.get("size") or 0) > STREAM_UPLOAD_MAX]
    if big:
        print("Files over 1 GB (chunked upload; WorkDrive plan limits apply):")
        for n in big:
            print(f"  {human(n['size'])}  {n['path']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth-google").add_argument("--no-browser", action="store_true")
    sub.add_parser("auth-zoho").add_argument("code")
    sub.add_parser("inventory").add_argument("--include-shared", action="store_true",
                                             help="also migrate files shared with the account that are not in My Drive")
    sub.add_parser("download").add_argument("--force", action="store_true", help="re-download even if present")
    u = sub.add_parser("upload")
    u.add_argument("--dest", help="WorkDrive folder id (default WORKDRIVE_DEST_FOLDER_ID)")
    u.add_argument("--dry-run", action="store_true")
    u.add_argument("--only", help="only files whose path contains this text")
    sub.add_parser("verify")
    sub.add_parser("report")
    sub.add_parser("myfolders")
    a = ap.parse_args()
    if a.cmd == "auth-google":
        auth_google(a.no_browser)
    elif a.cmd == "auth-zoho":
        auth_zoho(a.code)
    elif a.cmd == "inventory":
        inventory(a.include_shared)
    elif a.cmd == "download":
        download(a.force)
    elif a.cmd == "upload":
        upload(a.dest, a.dry_run, a.only)
    elif a.cmd == "verify":
        verify()
    elif a.cmd == "report":
        report()
    elif a.cmd == "myfolders":
        myfolders()


if __name__ == "__main__":
    main()
