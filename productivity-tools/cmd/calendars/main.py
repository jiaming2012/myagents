#!/usr/bin/env python3
"""iCloud calendar tool (read + write via CalDAV).

Subcommands:
  pull   Fetch events in a date window, print JSON to stdout and write
         .cache/calendars/snapshot.json (apply uses it for calendar lookups).
  list   List calendars with writable / read-only status (--json available).
  apply  Read .cache/calendars/proposals.json and create / update / delete
         events on iCloud. Archives the file on full clean success.
"""

import argparse
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import caldav
from caldav.elements import dav
from dotenv import find_dotenv, load_dotenv
from icalendar import Calendar as ICalendar, Event as IEvent
from lxml import etree

ICLOUD_CALDAV_URL = "https://caldav.icloud.com/"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / ".cache" / "calendars"
SNAPSHOT_PATH = CACHE_DIR / "snapshot.json"
PROPOSALS_PATH = CACHE_DIR / "proposals.json"
LOCAL_TZ = datetime.now().astimezone().tzinfo


# --- read-side helpers (unchanged from prior version) ---------------------

def connect():
    username = os.environ.get("ICLOUD_USERNAME")
    password = os.environ.get("ICLOUD_APP_PASSWORD")
    if not username or not password:
        sys.exit("ERROR: set ICLOUD_USERNAME and ICLOUD_APP_PASSWORD (see .env.example)")
    client = caldav.DAVClient(url=ICLOUD_CALDAV_URL, username=username, password=password)
    return client.principal()


def iso(v):
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _field(event, key):
    v = event.get(key)
    if v is None:
        return None
    if hasattr(v, "dt"):
        return v.dt
    return str(v)


def extract_event(event, calendar_name):
    return {
        "calendar": calendar_name,
        "uid": _field(event, "uid"),
        "summary": _field(event, "summary"),
        "description": _field(event, "description"),
        "location": _field(event, "location"),
        "start": iso(_field(event, "dtstart")),
        "end": iso(_field(event, "dtend")),
        "status": _field(event, "status"),
        "organizer": _field(event, "organizer"),
    }


def cal_name(cal):
    try:
        return cal.get_display_name() or "(unnamed)"
    except Exception:
        return "(unnamed)"


# --- writable detection ---------------------------------------------------

class _CurrentUserPrivilegeSet(dav.BaseElement):
    """{DAV:}current-user-privilege-set — not exposed by caldav.elements.dav."""
    tag = dav.ns("D", "current-user-privilege-set")


def check_writable(cal) -> bool | None:
    """Return True/False, or None if we cannot tell."""
    try:
        props = cal.get_properties([_CurrentUserPrivilegeSet()])
        for v in props.values():
            if not hasattr(v, "tag"):
                continue
            # Look for any <write/> or <write-content/> child privilege.
            for priv in v.iter():
                local = etree.QName(priv.tag).localname if priv.tag else ""
                if local in ("write", "write-content", "bind"):
                    return True
        return False
    except Exception:
        return None


# --- pull -----------------------------------------------------------------

def _add_pull_args(p):
    p.add_argument("--days", type=int, default=7,
                   help="Days forward from now (default: 7). Ignored if --from/--to set.")
    p.add_argument("--from", dest="from_date", help="ISO start date (YYYY-MM-DD)")
    p.add_argument("--to", dest="to_date", help="ISO end date (YYYY-MM-DD)")
    p.add_argument("--calendar", action="append",
                   help="Only fetch this calendar by name (repeatable). Default: all.")


def resolve_window(args):
    now = datetime.now(timezone.utc)
    start = (datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
             if args.from_date else now)
    end = (datetime.fromisoformat(args.to_date).replace(tzinfo=timezone.utc)
           if args.to_date else now + timedelta(days=args.days))
    return start, end


def cmd_pull(args, principal):
    start, end = resolve_window(args)
    wanted = set(args.calendar) if args.calendar else None

    results = []
    for cal in principal.calendars():
        name = cal_name(cal)
        if wanted and name not in wanted:
            continue
        try:
            events = cal.search(start=start, end=end, event=True, expand=True)
        except Exception as e:
            print(f"WARN: failed to query {name!r}: {e}", file=sys.stderr)
            continue
        for ev in events:
            for component in ev.icalendar_instance.walk("VEVENT"):
                results.append(extract_event(component, name))

    results.sort(key=lambda r: r["start"] or "")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(results, indent=2, default=str))

    json.dump(results, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


# --- list -----------------------------------------------------------------

def cmd_list(args, principal):
    entries = []
    for cal in principal.calendars():
        w = check_writable(cal)
        entries.append({
            "name": cal_name(cal),
            "writable": w,
            "status": "writable" if w is True else ("read-only" if w is False else "unknown"),
        })

    if args.json:
        json.dump(entries, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    width = max((len(e["name"]) for e in entries), default=10)
    for e in entries:
        print(f"  {e['name'].ljust(width)}  {e['status']}")


# --- apply ----------------------------------------------------------------

def parse_dt(s: str):
    """ISO string -> date (if no T) or tz-aware datetime."""
    if "T" not in s:
        return date.fromisoformat(s)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt


VALID_OPS = ("create", "update", "delete")
UPDATABLE_FIELDS = ("summary", "start", "end", "location", "description")


def validate(p: dict, by_name: dict) -> str | None:
    op = p.get("op")
    if op not in VALID_OPS:
        return f"op must be one of {VALID_OPS} (got {op!r})"
    if not p.get("reasoning"):
        return "missing reasoning"

    if op == "create":
        for f in ("calendar", "summary", "start", "end"):
            if f not in p:
                return f"create missing {f}"
        if p["calendar"] not in by_name:
            return (f"calendar {p['calendar']!r} not found — "
                    f"run task productivity:calendars:list")
        try:
            parse_dt(p["start"])
            parse_dt(p["end"])
        except ValueError as e:
            return f"bad date/time: {e}"

    elif op == "update":
        if not p.get("uid"):
            return "update missing uid"
        if not any(f in p for f in UPDATABLE_FIELDS):
            return f"update must include at least one of {UPDATABLE_FIELDS}"
        for f in ("start", "end"):
            if f in p:
                try:
                    parse_dt(p[f])
                except ValueError as e:
                    return f"bad {f}: {e}"

    elif op == "delete":
        if not p.get("uid"):
            return "delete missing uid"

    return None


def build_event_ical(*, summary, start, end, uid, location=None, description=None):
    cal = ICalendar()
    cal.add("prodid", "-//productivity-tools//iCloud writer//EN")
    cal.add("version", "2.0")
    e = IEvent()
    e.add("uid", uid)
    e.add("dtstamp", datetime.now(timezone.utc))
    e.add("summary", summary)
    e.add("dtstart", start)
    e.add("dtend", end)
    if location:
        e.add("location", location)
    if description:
        e.add("description", description)
    cal.add_component(e)
    return cal.to_ical()


def find_event(writable_cals, uid):
    for cal in writable_cals:
        try:
            return cal.event_by_uid(uid)
        except Exception:
            continue
    raise LookupError(f"event {uid!r} not found in any writable calendar")


def _replace(comp, key, value):
    if key in comp:
        del comp[key]
    comp.add(key, value)


def apply_create(target_cal, p):
    uid = f"{uuid.uuid4()}@productivity-tools.local"
    ical = build_event_ical(
        summary=p["summary"],
        start=parse_dt(p["start"]),
        end=parse_dt(p["end"]),
        uid=uid,
        location=p.get("location"),
        description=p.get("description"),
    )
    target_cal.save_event(ical=ical)
    return uid


def apply_update(writable_cals, p):
    event_obj = find_event(writable_cals, p["uid"])
    comp = next(event_obj.icalendar_instance.walk("VEVENT"))

    if "summary" in p:
        _replace(comp, "summary", p["summary"])
    if "start" in p:
        _replace(comp, "dtstart", parse_dt(p["start"]))
    if "end" in p:
        _replace(comp, "dtend", parse_dt(p["end"]))
    if "location" in p:
        _replace(comp, "location", p["location"])
    if "description" in p:
        _replace(comp, "description", p["description"])

    seq = 0
    try:
        seq = int(str(comp.get("sequence", 0)))
    except (TypeError, ValueError):
        pass
    _replace(comp, "sequence", seq + 1)
    _replace(comp, "dtstamp", datetime.now(timezone.utc))

    event_obj.data = event_obj.icalendar_instance.to_ical().decode()
    event_obj.save()


def apply_delete(writable_cals, p):
    event_obj = find_event(writable_cals, p["uid"])
    event_obj.delete()


def cmd_apply(args, principal):
    if not PROPOSALS_PATH.exists():
        sys.exit(f"No proposals at {PROPOSALS_PATH}")
    proposals = json.loads(PROPOSALS_PATH.read_text())
    if not isinstance(proposals, list) or not proposals:
        sys.exit(f"{PROPOSALS_PATH} is empty or not a JSON array")

    all_cals = list(principal.calendars())
    by_name = {cal_name(c): c for c in all_cals}
    writable_cals = [c for c in all_cals if check_writable(c) is not False]

    errors = []
    for i, p in enumerate(proposals):
        err = validate(p, by_name)
        if err:
            errors.append(f"  record {i}: {err}")
    if errors:
        print("Validation failed:\n" + "\n".join(errors), file=sys.stderr)
        sys.exit(1)

    ok = fail = 0
    for p in proposals:
        op = p["op"]
        why = p.get("reasoning", "")
        label = p.get("uid") or p.get("summary") or "?"
        try:
            if op == "create":
                uid = apply_create(by_name[p["calendar"]], p)
                print(f"  ok   create [{p['calendar']}] {p['summary']!r} {p['start']}  (uid={uid})  {why}")
            elif op == "update":
                apply_update(writable_cals, p)
                print(f"  ok   update {p['uid']}  {why}")
            elif op == "delete":
                apply_delete(writable_cals, p)
                print(f"  ok   delete {p['uid']}  {why}")
            ok += 1
        except Exception as e:
            print(f"  FAIL {op} {label}: {e}", file=sys.stderr)
            fail += 1

    print(f"\nApplied {ok}, failed {fail}")
    if ok and not fail:
        archived = PROPOSALS_PATH.with_suffix(".applied.json")
        PROPOSALS_PATH.rename(archived)
        print(f"Archived {PROPOSALS_PATH.name} -> {archived.name}")


# --- entry point ----------------------------------------------------------

def main():
    load_dotenv(find_dotenv(usecwd=True))

    parser = argparse.ArgumentParser(prog="calendars", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pull = sub.add_parser("pull", help="Pull events in a window -> JSON + snapshot")
    _add_pull_args(p_pull)

    p_list = sub.add_parser("list", help="List calendars with writable status")
    p_list.add_argument("--json", action="store_true", help="Emit JSON instead of text")

    sub.add_parser("apply", help="Apply proposals.json (create/update/delete)")

    args = parser.parse_args()
    principal = connect()
    {
        "pull": cmd_pull,
        "list": cmd_list,
        "apply": cmd_apply,
    }[args.cmd](args, principal)


if __name__ == "__main__":
    main()
