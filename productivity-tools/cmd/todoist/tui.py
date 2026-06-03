"""Interactive curses TUI to review and apply reschedules.json.

Mirrors the structure of email-tools `inbox view`:
  - Row 1: scope tabs   (All / Inbox / Overdue / Labeled / Recurring)
  - Row 2: batch tabs   (All / grp_*)
  - List of items with cursor, selection checkbox, color-coded priority
  - Detail pane below the cursor showing reasoning + label diff
  - Insights bar at top (counts, date distribution, batch sizes)
  - 'u' applies selected reschedules directly via the Todoist API
"""

from __future__ import annotations

import curses
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests

GRP_LABEL_RE = re.compile(r"^grp_[a-z0-9_]+$")
PRIORITY_TAG = {4: "p1", 3: "p2", 2: "p3", 1: "p4"}

CP_ACTIVE = 1
CP_DIM = 2
CP_P1 = 3
CP_P2 = 4
CP_P3 = 5
CP_P4 = 6
CP_HEADER = 7
CP_LABEL = 8
CP_OK = 9
CP_ERR = 10
CP_WARN = 11


@dataclass
class Item:
    task_id: str
    content: str
    description: str | None
    project_name: str
    is_recurring: bool
    priority: int                  # API value: 4=p1 (highest)
    current_due: str | None
    new_due: str | None
    current_labels: list[str]
    add_labels: list[str]
    reasoning: str
    is_inbox: bool
    is_overdue: bool
    selected: bool = True
    applied: bool = False
    error: str | None = None

    @property
    def merged_labels(self) -> list[str]:
        return list(dict.fromkeys([*self.current_labels, *self.add_labels]))

    @property
    def pri_tag(self) -> str:
        return PRIORITY_TAG.get(self.priority, "??")

    @property
    def batch(self) -> str:
        for l in self.add_labels:
            if GRP_LABEL_RE.match(l):
                return l
        for l in self.current_labels:
            if GRP_LABEL_RE.match(l):
                return l
        return "_unlabeled"


def load_items(reschedules_path: Path, snapshot_path: Path) -> tuple[list[Item], str]:
    proposals = json.loads(reschedules_path.read_text())
    snapshot = json.loads(snapshot_path.read_text()) if snapshot_path.exists() else {}
    today = snapshot.get("today") or date.today().isoformat()

    task_lookup: dict[str, dict] = {}
    for bucket in ("today_tasks", "tomorrow_tasks", "overdue_tasks"):
        for t in snapshot.get(bucket, []):
            task_lookup[t["id"]] = t

    items: list[Item] = []
    for p in proposals:
        tid = str(p["task_id"])
        meta = task_lookup.get(tid, {})
        current_due = p.get("current_due")
        items.append(Item(
            task_id=tid,
            content=meta.get("content") or "(task missing from snapshot)",
            description=meta.get("description"),
            project_name=meta.get("project_name") or "—",
            is_recurring=bool(meta.get("is_recurring", False)),
            priority=int(p.get("priority") or 1),
            current_due=current_due,
            new_due=p.get("new_due"),
            current_labels=list(p.get("current_labels") or meta.get("labels") or []),
            add_labels=list(p.get("add_labels") or []),
            reasoning=p.get("reasoning") or "",
            is_inbox=(meta.get("project_name") == "Inbox"),
            is_overdue=bool(current_due and current_due < today),
        ))
    return items, today


def to_record(it: Item) -> dict:
    rec: dict = {
        "task_id": it.task_id,
        "current_due": it.current_due,
        "priority": it.priority,
        "reasoning": it.reasoning,
    }
    if it.new_due:
        rec["new_due"] = it.new_due
    if it.add_labels:
        rec["add_labels"] = it.add_labels
        rec["current_labels"] = it.current_labels
    return rec


class TUI:
    SCOPES = ["All", "Inbox", "Overdue", "Labeled", "Recurring"]

    def __init__(self, items: list[Item], today: str, client):
        self.items = items
        self.today = today
        self.client = client
        self.scope_idx = 0
        self.group_idx = 0
        self.cursor = 0
        self.offset = 0
        self.confirming_apply = False
        self.confirming_quit = False
        self.status = ""
        self.height = 24
        self.width = 80
        self.show_detail = True
        self._refresh_groups()

    # ---------- data ----------
    def _scope_filter(self, idx: int, items: list[Item]) -> list[Item]:
        s = self.SCOPES[idx]
        if s == "Inbox":
            return [i for i in items if i.is_inbox]
        if s == "Overdue":
            return [i for i in items if i.is_overdue]
        if s == "Labeled":
            return [i for i in items if i.add_labels]
        if s == "Recurring":
            return [i for i in items if i.is_recurring]
        return list(items)

    def _scoped(self) -> list[Item]:
        return self._scope_filter(self.scope_idx, self.items)

    def _refresh_groups(self) -> None:
        scoped = self._scoped()
        by_batch: dict[str, list[Item]] = {}
        for it in scoped:
            by_batch.setdefault(it.batch, []).append(it)
        ordered = sorted(
            by_batch.items(),
            key=lambda kv: (kv[0] == "_unlabeled", -len(kv[1]), kv[0]),
        )
        self.groups: list[tuple[str, list[Item]]] = [("All", scoped)] + ordered
        if self.group_idx >= len(self.groups):
            self.group_idx = 0
        self.cursor = 0
        self.offset = 0

    @property
    def current_list(self) -> list[Item]:
        return self.groups[self.group_idx][1] if self.groups else []

    def selected_pending(self) -> list[Item]:
        return [i for i in self.items if i.selected and not i.applied]

    def applied_count(self) -> int:
        return sum(1 for i in self.items if i.applied)

    def failed_count(self) -> int:
        return sum(1 for i in self.items if i.error)

    # ---------- rendering ----------
    def _addstr(self, stdscr, y: int, x: int, text: str, attr: int = 0) -> None:
        if y < 0 or y >= self.height:
            return
        max_len = self.width - x - 1
        if max_len < 1:
            return
        if len(text) > max_len:
            text = text[:max_len]
        try:
            stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def render(self, stdscr) -> None:
        stdscr.erase()
        self.height, self.width = stdscr.getmaxyx()
        y = 0
        y = self._render_insights(stdscr, y)
        y = self._render_scope_tabs(stdscr, y)
        y = self._render_group_tabs(stdscr, y)
        y += 1
        detail_rows = 4 if self.show_detail else 0
        list_height = max(3, self.height - y - detail_rows - 2)
        end_y = self._render_list(stdscr, y, list_height)
        if self.show_detail:
            self._render_detail(stdscr, end_y + 1)
        self._render_footer(stdscr)
        stdscr.refresh()

    def _render_insights(self, stdscr, y: int) -> int:
        total = len(self.items)
        sel = len(self.selected_pending())
        done = self.applied_count()
        failed = self.failed_count()
        dates: dict[str, int] = {}
        for it in self.items:
            if it.new_due and not it.applied and not it.is_recurring:
                dates[it.new_due] = dates.get(it.new_due, 0) + 1
        date_str = " ".join(f"{d[5:]}:{n}" for d, n in sorted(dates.items())) or "—"
        batches: dict[str, int] = {}
        for it in self.items:
            for l in it.add_labels:
                batches[l] = batches.get(l, 0) + 1
        recurring = sum(1 for i in self.items if i.is_recurring)

        line1 = (
            f" {total} items  |  {sel} to apply  |  {done} applied  |  "
            f"{failed} failed  |  today {self.today}"
        )
        self._addstr(stdscr, y, 0, line1, curses.color_pair(CP_HEADER) | curses.A_BOLD)
        y += 1
        self._addstr(stdscr, y, 0, f" dates → {date_str}   (recurring kept: {recurring})",
                     curses.color_pair(CP_DIM))
        y += 1
        if batches:
            parts = "  ".join(f"{k}:{v}" for k, v in sorted(batches.items()))
            self._addstr(stdscr, y, 0, f" batches → {parts}", curses.color_pair(CP_LABEL))
            y += 1
        return y

    def _render_scope_tabs(self, stdscr, y: int) -> int:
        x = 0
        for i, s in enumerate(self.SCOPES):
            n = len(self._scope_filter(i, self.items))
            label = f"{s} ({n})"
            text = f"[{label}]" if i == self.scope_idx else f" {label} "
            attr = (curses.color_pair(CP_ACTIVE) | curses.A_BOLD
                    if i == self.scope_idx else curses.color_pair(CP_DIM))
            if x + len(text) + 1 >= self.width:
                break
            self._addstr(stdscr, y, x, text, attr)
            x += len(text) + 1
        return y + 1

    def _render_group_tabs(self, stdscr, y: int) -> int:
        x = 0
        for i, (name, items) in enumerate(self.groups):
            display = name if name != "_unlabeled" else "(unlabeled)"
            label = f"{display} ({len(items)})"
            text = f"[{label}]" if i == self.group_idx else f" {label} "
            attr = (curses.color_pair(CP_ACTIVE) | curses.A_BOLD
                    if i == self.group_idx else curses.color_pair(CP_DIM))
            if x + len(text) + 1 >= self.width:
                self._addstr(stdscr, y, x, "…", curses.color_pair(CP_DIM))
                break
            self._addstr(stdscr, y, x, text, attr)
            x += len(text) + 1
        return y + 1

    def _render_list(self, stdscr, start_y: int, height: int) -> int:
        items = self.current_list
        if not items:
            self._addstr(stdscr, start_y, 2, "(no items in this view)",
                         curses.color_pair(CP_DIM))
            return start_y

        lines_per_item = 3
        max_items = max(1, height // lines_per_item)
        if self.cursor < self.offset:
            self.offset = self.cursor
        if self.cursor >= self.offset + max_items:
            self.offset = self.cursor - max_items + 1

        y = start_y
        pri_color = {4: CP_P1, 3: CP_P2, 2: CP_P3, 1: CP_P4}
        for i in range(self.offset, min(len(items), self.offset + max_items)):
            it = items[i]
            is_cursor = (i == self.cursor)

            if it.applied:
                box, box_attr = "[✓]", curses.color_pair(CP_OK) | curses.A_BOLD
            elif it.error:
                box, box_attr = "[!]", curses.color_pair(CP_ERR) | curses.A_BOLD
            elif it.selected:
                box, box_attr = "[x]", curses.color_pair(CP_ACTIVE) | curses.A_BOLD
            else:
                box, box_attr = "[ ]", curses.color_pair(CP_DIM)

            cursor_mark = "> " if is_cursor else "  "
            cursor_attr = curses.color_pair(CP_ACTIVE) | curses.A_BOLD if is_cursor else 0
            self._addstr(stdscr, y, 0, cursor_mark, cursor_attr)
            self._addstr(stdscr, y, 2, box, box_attr)
            tag = f"[{it.pri_tag}]"
            self._addstr(stdscr, y, 6, tag,
                         curses.color_pair(pri_color.get(it.priority, CP_P4)) | curses.A_BOLD)
            content_attr = curses.A_BOLD if is_cursor else 0
            self._addstr(stdscr, y, 11, it.content, content_attr)
            y += 1

            parts = [it.project_name]
            cur = it.current_due or "no-date"
            if it.is_recurring and it.new_due:
                nd = f"{it.new_due} (recurring → kept)"
            elif it.new_due:
                nd = it.new_due
            else:
                nd = "keep date"
            parts.append(f"{cur} → {nd}")
            if it.add_labels:
                parts.append("+" + ",".join(it.add_labels))
            if it.is_overdue:
                parts.append("OVERDUE")
            if it.is_inbox:
                parts.append("Inbox")
            self._addstr(stdscr, y, 6, "  •  ".join(parts), curses.color_pair(CP_DIM))
            y += 1

            self._addstr(stdscr, y, 6, it.reasoning, curses.color_pair(CP_LABEL))
            y += 1

        if y < self.height - 5:
            scroll = (f"  {self.cursor + 1}/{len(items)}   "
                      f"group: {self.groups[self.group_idx][0]}")
            self._addstr(stdscr, y, 0, scroll, curses.color_pair(CP_DIM))
            y += 1
        return y

    def _render_detail(self, stdscr, y: int) -> None:
        items = self.current_list
        if not items or self.cursor >= len(items):
            return
        it = items[self.cursor]
        if y >= self.height - 2:
            return
        self._addstr(stdscr, y, 0, "─" * (self.width - 1), curses.color_pair(CP_DIM))
        y += 1
        if y >= self.height - 1:
            return
        if it.error:
            self._addstr(stdscr, y, 0, f"  ERROR: {it.error}",
                         curses.color_pair(CP_ERR) | curses.A_BOLD)
            y += 1
            if y >= self.height - 1:
                return
        meta = (f"  task_id {it.task_id}  •  recurring: {it.is_recurring}  •  "
                f"inbox: {it.is_inbox}  •  overdue: {it.is_overdue}")
        self._addstr(stdscr, y, 0, meta, curses.color_pair(CP_DIM))
        y += 1
        if y >= self.height - 1:
            return
        cur = ",".join(it.current_labels) or "—"
        new = ",".join(it.merged_labels) or "—"
        self._addstr(stdscr, y, 0, f"  labels: [{cur}]  →  [{new}]",
                     curses.color_pair(CP_LABEL))

    def _render_footer(self, stdscr) -> None:
        y = self.height - 1
        if self.confirming_quit:
            n = len(self.selected_pending())
            msg = f" Quit?  {n} still selected, {self.applied_count()} applied  (y/n)"
            self._addstr(stdscr, y, 0, msg, curses.color_pair(CP_WARN) | curses.A_BOLD)
            return
        if self.confirming_apply:
            n = len(self.selected_pending())
            self._addstr(stdscr, y, 0,
                         f" Apply {n} reschedule(s) to Todoist? (y/n)",
                         curses.color_pair(CP_WARN) | curses.A_BOLD)
            return
        if self.status:
            self._addstr(stdscr, y, 0, " " + self.status,
                         curses.color_pair(CP_ACTIVE) | curses.A_BOLD)
            return
        help_text = (" j/k nav  •  h/l scope  •  tab batch  •  space select  •  "
                     "a toggle-all  •  d detail  •  u apply  •  q quit")
        self._addstr(stdscr, y, 0, help_text, curses.color_pair(CP_DIM))

    # ---------- input ----------
    def handle_key(self, stdscr, key: int) -> bool:
        if self.confirming_quit:
            if key in (ord('y'), ord('Y')):
                return False
            if key in (ord('n'), ord('N'), 27):
                self.confirming_quit = False
                self.status = ""
            return True

        if self.confirming_apply:
            if key in (ord('y'), ord('Y')):
                self.confirming_apply = False
                self.apply(stdscr)
            elif key in (ord('n'), ord('N'), 27):
                self.confirming_apply = False
                self.status = ""
            return True

        items = self.current_list
        if key in (curses.KEY_UP, ord('k')):
            if items and self.cursor > 0:
                self.cursor -= 1
        elif key in (curses.KEY_DOWN, ord('j')):
            if items and self.cursor < len(items) - 1:
                self.cursor += 1
        elif key == curses.KEY_NPAGE:
            if items:
                self.cursor = min(len(items) - 1, self.cursor + 10)
        elif key == curses.KEY_PPAGE:
            if items:
                self.cursor = max(0, self.cursor - 10)
        elif key == ord('g'):
            self.cursor = 0
        elif key == ord('G'):
            if items:
                self.cursor = len(items) - 1
        elif key in (curses.KEY_RIGHT, ord('l')):
            self.scope_idx = (self.scope_idx + 1) % len(self.SCOPES)
            self._refresh_groups()
        elif key in (curses.KEY_LEFT, ord('h')):
            self.scope_idx = (self.scope_idx - 1) % len(self.SCOPES)
            self._refresh_groups()
        elif key in (ord('\t'), 9):
            if self.groups:
                self.group_idx = (self.group_idx + 1) % len(self.groups)
                self.cursor = 0
                self.offset = 0
        elif key in (curses.KEY_BTAB, 353):
            if self.groups:
                self.group_idx = (self.group_idx - 1) % len(self.groups)
                self.cursor = 0
                self.offset = 0
        elif key == ord(' '):
            if items and not items[self.cursor].applied:
                items[self.cursor].selected = not items[self.cursor].selected
        elif key == ord('a'):
            any_unsel = any(not i.selected and not i.applied for i in items)
            for i in items:
                if not i.applied:
                    i.selected = any_unsel
        elif key == ord('d'):
            self.show_detail = not self.show_detail
        elif key == ord('u'):
            if self.client is None:
                self.status = "No TODOIST_API_TOKEN configured — view-only mode"
            elif not self.selected_pending():
                self.status = "Nothing selected"
            else:
                self.confirming_apply = True
        elif key in (ord('q'), 27, 3):
            if not self.selected_pending() and self.applied_count() == 0:
                return False
            self.confirming_quit = True
        return True

    # ---------- apply ----------
    def apply(self, stdscr) -> None:
        targets = sorted(self.selected_pending(), key=lambda i: -i.priority)
        for idx, it in enumerate(targets, 1):
            bad = [l for l in it.add_labels if not GRP_LABEL_RE.match(l)]
            if bad:
                it.error = f"bad labels {bad}"
                continue

            body: dict = {}
            if it.new_due and not it.is_recurring:
                body["due_date"] = it.new_due
            if it.add_labels:
                body["labels"] = it.merged_labels

            if not body:
                # Recurring with only new_due → nothing to send; treat as applied no-op.
                it.applied = True
                it.selected = False
                self.status = f"Applying {idx}/{len(targets)} — skipped recurring {it.task_id}"
                self.render(stdscr)
                continue

            short = it.content[:32]
            self.status = f"Applying {idx}/{len(targets)} — [{it.pri_tag}] {short}"
            self.render(stdscr)
            try:
                self.client.post(f"/tasks/{it.task_id}", body)
                it.applied = True
                it.selected = False
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else "?"
                text = (e.response.text if e.response is not None else str(e))[:120]
                it.error = f"HTTP {code}: {text}"
            except requests.RequestException as e:
                it.error = f"{type(e).__name__}: {e}"
            self.render(stdscr)

        ok = sum(1 for i in targets if i.applied)
        fail = sum(1 for i in targets if i.error)
        self.status = f"Done. Applied {ok}, failed {fail}."


def _init_colors() -> None:
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    curses.init_pair(CP_ACTIVE, curses.COLOR_GREEN, bg)
    curses.init_pair(CP_DIM, 8 if curses.COLORS >= 16 else curses.COLOR_WHITE, bg)
    curses.init_pair(CP_P1, curses.COLOR_RED, bg)
    curses.init_pair(CP_P2, curses.COLOR_YELLOW, bg)
    curses.init_pair(CP_P3, curses.COLOR_CYAN, bg)
    curses.init_pair(CP_P4, curses.COLOR_BLUE, bg)
    curses.init_pair(CP_HEADER, curses.COLOR_MAGENTA, bg)
    curses.init_pair(CP_LABEL, curses.COLOR_CYAN, bg)
    curses.init_pair(CP_OK, curses.COLOR_GREEN, bg)
    curses.init_pair(CP_ERR, curses.COLOR_RED, bg)
    curses.init_pair(CP_WARN, curses.COLOR_YELLOW, bg)


def run_view(client, reschedules_path: Path, snapshot_path: Path,
             archive_path: Path) -> None:
    if not reschedules_path.exists():
        raise SystemExit(f"No proposals at {reschedules_path}")
    items, today = load_items(reschedules_path, snapshot_path)
    if not items:
        raise SystemExit(f"{reschedules_path} has no items")

    def _main(stdscr):
        curses.curs_set(0)
        _init_colors()
        stdscr.keypad(True)
        tui = TUI(items, today, client)
        while True:
            tui.render(stdscr)
            key = stdscr.getch()
            if key == curses.KEY_RESIZE:
                continue
            if not tui.handle_key(stdscr, key):
                return tui

    final: TUI = curses.wrapper(_main)
    ok = final.applied_count()
    fail = final.failed_count()
    print(f"Applied {ok}, failed {fail}")

    if ok == 0:
        return

    remaining = [it for it in items if not it.applied]
    if not remaining and fail == 0:
        reschedules_path.rename(archive_path)
        print(f"Archived {reschedules_path.name} → {archive_path.name}")
    else:
        reschedules_path.write_text(
            json.dumps([to_record(it) for it in remaining], indent=2)
        )
        print(f"Updated {reschedules_path.name}: {len(remaining)} pending")
        if fail:
            print(f"  {fail} failed (kept in file — fix and retry)")
