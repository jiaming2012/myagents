"""Toast payout-overview rows are provisional until Toast marks them SENT.

A payout exported while its row still reads CAPTURED (or anything but SENT) can
change afterwards — on 2026-11-16 the row for the Nov 14 sales day went from
307.59 / fees 11.77 / 9 txns to 345.04 / 13.49 / 12 txns once sent, which
toast_xero reports as a deposit that does not balance (unreconciled_diff) and
zoho_books_recon.py then leaves the Mercury credit uncategorized.

This module reads the Status column that toast_xero ignores, finds payout zips
in the toast_xero inputs/ folder that still hold provisional rows, and refreshes
them: the stale zip moves to inputs/.provisional/ (invisible to toast_xero's
non-recursive globs and to the exporter's presence check) and `task toast:export`
re-downloads the same range. A failed export puts the old file back so the
pipeline keeps working on yesterday's figures.

Used by toast_preview_dump.py (`task zoho:toast-dump`, `--no-refresh` to skip)."""
import csv
import io
import re
import shutil
import subprocess
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

REPO = Path.home() / "projects/yumyums/accounting/xero"
INPUTS = REPO / "inputs"
PROVISIONAL_SUBDIR = ".provisional"
FINAL_STATUS = "SENT"
NAME_RE = re.compile(r"^PayoutOverviewByPayout_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.zip$")
CENT = Decimal("0.01")


def payout_range(path: Path) -> tuple[date, date] | None:
    m = NAME_RE.match(path.name)
    if not m:
        return None
    return date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))


def read_rows(path: Path) -> list[dict]:
    """The payout rows of one PayoutOverviewByPayout zip (first CSV member)."""
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            return []
        text = zf.read(names[0]).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def row_status(row: dict) -> str:
    return (row.get("Status") or "").strip().upper()


def row_key(row: dict) -> tuple[str, str] | None:
    """(settled date ISO, payout amount) — how a toast_xero deposit's payout rows are found."""
    settled, payout = (row.get("Settled date") or "").strip(), (row.get("Payout") or "").strip()
    if not settled or not payout:
        return None
    try:
        d = datetime.strptime(settled, "%m/%d/%Y").date()
        return d.isoformat(), str(Decimal(payout).quantize(CENT))
    except (ValueError, ArithmeticError):
        return None


def provisional_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if row_status(r) != FINAL_STATUS]


def payout_zips(inputs: Path = INPUTS) -> list[Path]:
    return sorted(p for p in inputs.glob("PayoutOverviewByPayout_*.zip") if payout_range(p))


def status_index(inputs: Path = INPUTS) -> dict[tuple[str, str], str]:
    """(settled date, payout) -> Status, over every payout zip in inputs/."""
    idx = {}
    for p in payout_zips(inputs):
        for r in read_rows(p):
            k = row_key(r)
            if k:
                idx[k] = row_status(r)
    return idx


def _run_export(frm: date, to: date) -> int:
    """Re-download one payout range through toast_xero's own Taskfile target."""
    return subprocess.call(["task", "toast:export", "--", "--from", frm.isoformat(), "--to", to.isoformat()],
                           cwd=str(REPO))


def refresh(path: Path, say=print, run=_run_export) -> int | None:
    """Re-export one payout zip. Returns the number of provisional rows left
    (None when the export failed and the old file was restored)."""
    frm, to = payout_range(path)
    parked = path.parent / PROVISIONAL_SUBDIR / path.name
    parked.parent.mkdir(exist_ok=True)
    shutil.move(str(path), str(parked))
    say(f"toast: {path.name} has provisional payout rows -> re-exporting {frm} → {to}")
    try:
        rc = run(frm, to)
    except Exception as e:  # noqa: BLE001 — the pipeline must keep its old file whatever failed
        rc, err = 1, e
    else:
        err = None
    if rc != 0 or not path.exists():
        shutil.move(str(parked), str(path))
        say(f"toast: re-export failed ({err or f'exit {rc}'}); kept the old {path.name}")
        return None
    parked.unlink()
    left = provisional_rows(read_rows(path))
    say(f"toast: {path.name} refreshed, {len(left)} provisional row(s) remain"
        + ("" if not left else " (Toast has not sent them yet — run again later)"))
    return len(left)


def refresh_provisional(inputs: Path = INPUTS, say=print, run=_run_export) -> list[tuple[str, int, int | None]]:
    """Refresh every payout zip holding a non-SENT row. Returns (name, before, after) per file."""
    done = []
    for p in payout_zips(inputs):
        before = len(provisional_rows(read_rows(p)))
        if before:
            done.append((p.name, before, refresh(p, say, run)))
    return done
