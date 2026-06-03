#!/usr/bin/env python3
"""Todoist helper.

Subcommands:
  pull         Fetch today / tomorrow / overdue tasks + tasks completed today.
               Writes .cache/todoist/snapshot.json.
  reschedule   Apply reschedule proposals from .cache/todoist/reschedules.json
               (Claude writes that file during the evening flow).
  view         Interactive TUI to review and selectively apply reschedules.json
               without going through the batch `reschedule` command.
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import find_dotenv, load_dotenv

GRP_LABEL_RE = re.compile(r"^grp_[a-z0-9_]+$")

API_BASE = "https://api.todoist.com/api/v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / ".cache" / "todoist"
SNAPSHOT_PATH = CACHE_DIR / "snapshot.json"
RESCHEDULES_PATH = CACHE_DIR / "reschedules.json"
ARCHIVE_PATH = RESCHEDULES_PATH.with_suffix(".applied.json")


class TodoistClient:
    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def get(self, path: str, **params) -> dict | list:
        r = self.session.get(f"{API_BASE}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict) -> dict:
        r = self.session.post(
            f"{API_BASE}{path}",
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json() if r.text else {}


def _items(resp) -> list:
    """Unwrap a Todoist v1 response: list, or dict with 'results'/'items'."""
    if isinstance(resp, list):
        return resp
    for key in ("results", "items"):
        if key in resp:
            return resp[key]
    return []


def _paginate(client: "TodoistClient", path: str, **params) -> list:
    """Walk all cursor pages of a Todoist v1 list endpoint."""
    out: list = []
    cursor = None
    while True:
        page_params = {**params, **({"cursor": cursor} if cursor else {})}
        resp = client.get(path, **page_params)
        out.extend(_items(resp))
        cursor = resp.get("next_cursor") if isinstance(resp, dict) else None
        if not cursor:
            return out


def _serialize_task(t: dict, project_names: dict[str, str]) -> dict:
    due = t.get("due") or {}
    return {
        "id": t["id"],
        "content": t.get("content"),
        "description": t.get("description") or None,
        "priority": t.get("priority"),  # 1 (low/default) – 4 (p1, highest)
        "due_date": due.get("date"),
        "due_string": due.get("string"),
        "is_recurring": due.get("is_recurring", False),
        "labels": t.get("labels", []),
        "project_id": t.get("project_id"),
        "project_name": project_names.get(t.get("project_id")),
        "url": t.get("url"),
    }


def _serialize_completed(t: dict, project_names: dict[str, str]) -> dict:
    return {
        "task_id": t.get("task_id") or t.get("id"),
        "content": t.get("content"),
        "completed_at": t.get("completed_at"),
        "project_id": t.get("project_id"),
        "project_name": project_names.get(t.get("project_id")),
    }


def cmd_pull(client: TodoistClient) -> None:
    today = date.today()
    tomorrow = today + timedelta(days=1)

    project_names = {p["id"]: p["name"] for p in _paginate(client, "/projects")}

    today_overdue = _paginate(client, "/tasks/filter", query="today | overdue")
    tomorrow_raw = _paginate(client, "/tasks/filter", query="tomorrow")

    today_iso = today.isoformat()
    today_tasks, overdue_tasks = [], []
    for t in today_overdue:
        due_date = ((t.get("due") or {}).get("date") or "")[:10]
        if due_date and due_date < today_iso:
            overdue_tasks.append(_serialize_task(t, project_names))
        elif due_date == today_iso:
            today_tasks.append(_serialize_task(t, project_names))
        # else: future-dated or undated. /tasks/filter should not return
        # these for a `today | overdue` query, but drop them defensively
        # so the snapshot only ever reflects what is actually due today.

    tomorrow_tasks = [_serialize_task(t, project_names) for t in tomorrow_raw]

    since = datetime.combine(today, datetime.min.time()).isoformat()
    until = datetime.now().isoformat()
    completed_raw = _paginate(
        client,
        "/tasks/completed/by_completion_date",
        since=since,
        until=until,
    )
    completed_today = [_serialize_completed(t, project_names) for t in completed_raw]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "today": today.isoformat(),
        "tomorrow": tomorrow.isoformat(),
        "today_tasks": today_tasks,
        "tomorrow_tasks": tomorrow_tasks,
        "overdue_tasks": overdue_tasks,
        "completed_today": completed_today,
    }
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, default=str))
    print(f"Wrote {SNAPSHOT_PATH.relative_to(PROJECT_ROOT)}")
    print(
        f"  today: {len(today_tasks)}  tomorrow: {len(tomorrow_tasks)}  "
        f"overdue: {len(overdue_tasks)}  completed: {len(completed_today)}"
    )


def cmd_reschedule(client: TodoistClient) -> None:
    if not RESCHEDULES_PATH.exists():
        sys.exit(f"No proposals at {RESCHEDULES_PATH} — run todoist:evening first")

    proposals = json.loads(RESCHEDULES_PATH.read_text())
    if not isinstance(proposals, list) or not proposals:
        sys.exit(f"{RESCHEDULES_PATH} is empty or not a JSON array")

    priority_tag = {4: "p1", 3: "p2", 2: "p3", 1: "p4"}
    # p1 first when we apply, so the most important changes happen even if a later call fails
    proposals = sorted(proposals, key=lambda p: -(p.get("priority") or 0))

    ok = fail = 0
    for p in proposals:
        task_id = str(p["task_id"])
        tag = priority_tag.get(p.get("priority"), "  ")
        why = p.get("reasoning", "")
        new_due = p.get("new_due")
        add_labels = p.get("add_labels") or []
        current_labels = p.get("current_labels") or []

        if not new_due and not add_labels:
            print(f"  FAIL [{tag}] {task_id}: needs new_due or add_labels", file=sys.stderr)
            fail += 1
            continue

        bad = [l for l in add_labels if not GRP_LABEL_RE.match(l)]
        if bad:
            print(f"  FAIL [{tag}] {task_id}: bad labels {bad} (must match grp_[a-z0-9_]+)", file=sys.stderr)
            fail += 1
            continue

        body: dict = {}
        change_parts = []
        if new_due:
            body["due_date"] = new_due
            change_parts.append(f"-> {new_due}")
        if add_labels:
            merged = list(dict.fromkeys([*current_labels, *add_labels]))
            body["labels"] = merged
            change_parts.append("+" + ",".join(add_labels))

        try:
            client.post(f"/tasks/{task_id}", body)
            print(f"  ok   [{tag}] {task_id} {' '.join(change_parts)}  {why}")
            ok += 1
        except requests.HTTPError as e:
            print(f"  FAIL [{tag}] {task_id}: {e.response.status_code} {e.response.text}", file=sys.stderr)
            fail += 1

    print(f"\nApplied {ok}, failed {fail}")
    if ok and not fail:
        RESCHEDULES_PATH.rename(ARCHIVE_PATH)
        print(f"Archived {RESCHEDULES_PATH.name} -> {ARCHIVE_PATH.name}")


def cmd_view(client: TodoistClient | None) -> None:
    from tui import run_view
    run_view(client, RESCHEDULES_PATH, SNAPSHOT_PATH, ARCHIVE_PATH)


def main():
    load_dotenv(find_dotenv(usecwd=True))

    parser = argparse.ArgumentParser(prog="todoist", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pull", help="Fetch today/tomorrow/overdue + completed_today")
    sub.add_parser("reschedule", help="Apply reschedule proposals from reschedules.json")
    sub.add_parser("view", help="Interactive TUI to review + apply reschedules.json")
    args = parser.parse_args()

    token = os.environ.get("TODOIST_API_TOKEN")
    needs_token = args.cmd in ("pull", "reschedule")
    if needs_token and not token:
        sys.exit("ERROR: set TODOIST_API_TOKEN (see .env.example)")

    client = TodoistClient(token) if token else None
    if args.cmd == "pull":
        cmd_pull(client)
    elif args.cmd == "reschedule":
        cmd_reschedule(client)
    elif args.cmd == "view":
        cmd_view(client)


if __name__ == "__main__":
    main()
