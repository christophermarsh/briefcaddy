"""Case notes and tasks, on every case and on every prospect (src/prospects.py).

A note is free text: who wrote it, when, and what. It is never edited and never deleted: a correction is a new note that says which one it corrects
(data/clients/<id>/notes.json, or data/prospects/<id>/notes.json). A restricted case's or prospect's notes are closed by the same gate as everything else
of it (ReviewApp.may_open, ReviewApp.prospect_dir): the notes are in the folder the law closes.

    {"version": 1,
     "notes": [{"id": "n.1", "text", "by", "role" ("attorney", "paralegal" or null), "at", "corrects" (a note's id, or null), "carried" (the prospect's id a note came
                over from when the prospect became a client, or null), "attorney_only" (true on a note an attorney marked for attorneys only; absent otherwise)}]}

A task is a deadline a person sets on the case, marked as a task (src/deadlines_set.py: title, a person responsible from the staff list, a due date, done with who and
when, never deleted). It is written in that file on purpose and in no file of its own: journey.journey() already reads that file, so a task is in My work for its
person, in What's due by its date, in the month view, on the calendar feed (src/calendar_feed.py) and in the staff reminders, with no second code path. What a task
is not: a deadline the product worked out. A task on a prospect is in the same file of the prospect's folder, and the lists read it from there
(prospects.work_rows).

Every note and every task is one row in the event ledger (kind "notes"), whose sentence never carries the text, the title or the date.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import clock
import deadlines_set
import events

FILE = "notes.json"
VERSION = 1
MAX_NOTE = 4000  # characters in one note


def _read(folder: Path) -> dict[str, Any]:
    try:
        data = json.loads((folder / FILE).read_text(encoding="utf-8")) if (folder / FILE).exists() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"version": VERSION, "notes": [n for n in data.get("notes") or [] if isinstance(n, dict)]}


def _write(folder: Path, data: dict[str, Any]) -> None:
    path = folder / FILE
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _who(by: str) -> str:
    by = str(by or "").strip()
    if not by:
        raise ValueError("Enter your name first: every note and task records who made it.")
    return by


def _ledger(folder: Path, prospect: bool, action: str, what: str, who: str, role: str | None) -> None:
    """One row: a prospect's is named "prospect:<id>" (it is not a case), a case's by its id."""
    if prospect:
        events.record("notes", action, what, case=f"prospect:{folder.name}", home=folder.parent.parent, who=who, role=role)
    else:
        events.record("notes", action, what, case_dir=folder, who=who, role=role)


def notes(folder: str | Path, attorney: bool = True) -> list[dict[str, Any]]:
    """Every note, newest first. attorney False: a note an attorney marked for attorneys only is left out (the reader is not an attorney)."""
    rows = [n for n in _read(Path(folder))["notes"] if attorney or not n.get("attorney_only")]
    return sorted(rows, key=lambda n: (clock.key(n.get("at")), int(str(n.get("id", "n.0")).split(".")[1] or 0)), reverse=True)  # two in the same second: the later id is newer


def add_note(folder: str | Path, text: str, by: str, role: str | None = None, *, prospect: bool = False, corrects: str | None = None,
             carried: str | None = None, at: str | None = None, attorney_only: bool = False) -> dict[str, Any]:
    """A note on the case or the prospect. corrects: the id of an earlier note this one corrects (the earlier one is left as it was). carried / at: a note that came over
    from a prospect keeps who wrote it and when, and says where it came from. attorney_only: the attorney's work product for attorneys only (an attorney's to set:
    only attorneys see the note, on the case and in Find across the firm, src/find.py)."""
    folder = Path(folder)
    by = _who(by)
    text = "\n".join(" ".join(line.split()) for line in str(text or "").strip().splitlines())  # the writer's own lines, with the spaces tidied
    if not text.strip():
        raise ValueError("Write the note first.")
    if len(text) > MAX_NOTE:
        raise ValueError(f"That note is too long ({MAX_NOTE} characters at most): write it as two notes.")
    if attorney_only and role not in ("attorney", None):
        raise PermissionError("Only an attorney can mark a note for attorneys only.")
    data = _read(folder)
    if corrects and not any(n.get("id") == corrects for n in data["notes"]):
        raise LookupError("The note you are correcting is not here.")
    n = 1 + max([int(str(x["id"]).split(".")[1]) for x in data["notes"] if str(x.get("id", "")).startswith("n.") and str(x["id"]).split(".")[1].isdigit()] or [0])
    row = {"id": f"n.{n}", "text": text.strip(), "by": by, "role": role if role in ("attorney", "paralegal") else None, "at": at or clock.stamp(),
           "corrects": corrects or None, "carried": carried or None, **({"attorney_only": True} if attorney_only else {})}
    data["notes"].append(row)
    _write(folder, data)
    if not carried:
        _ledger(folder, prospect, "note_corrected" if corrects else "note_added", "Added a correction to an earlier note" if corrects else "Added a note", by, role)
    return row


# -- tasks -------------------------------------------------------------------------------------------------------------------


def add_task(folder: str | Path, title: str, when: str, who: Any, note: str, by: str, people: list[dict[str, Any]], typed: Any = None, role: str | None = None,
             *, prospect: bool = False) -> dict[str, Any]:
    """A task: what, who is responsible (a staff member; optional), the day it is due, an optional note."""
    folder = Path(folder)
    row = deadlines_set.add(folder, title, when, who, note, by, people, typed, task=True, ledger=False)
    _ledger(folder, prospect, "task_added", "Added a task", row["by"], role)
    return row


def assign_task(folder: str | Path, task_id: str, who: Any, by: str, people: list[dict[str, Any]], typed: Any = None, role: str | None = None, *, prospect: bool = False) -> dict[str, Any]:
    folder = Path(folder)
    _task(folder, task_id)
    out = deadlines_set.assign(folder, task_id, who, by, people, typed, ledger=False)
    _ledger(folder, prospect, "task_assigned", "Named the person responsible for a task" if out.get("who") or out.get("who_name") else "Took the person responsible off a task",
            _who(by), role)
    return out


def finish_task(folder: str | Path, task_id: str, by: str, role: str | None = None, *, prospect: bool = False) -> dict[str, Any]:
    """Done: who and when. The task stays in the file and on the page's Done list; it is never deleted."""
    folder = Path(folder)
    mine = _task(folder, task_id)
    if not mine.get("done"):
        mine = deadlines_set.done(folder, task_id, by, ledger=False)
        _ledger(folder, prospect, "task_done", "Marked a task done", _who(by), role)
    return mine


def _task(folder: Path, task_id: str) -> dict[str, Any]:
    found = next((d for d in deadlines_set.load(folder)["deadlines"] if d.get("id") == str(task_id or "") and d.get("task")), None)
    if found is None:
        raise LookupError("No such task.")
    return found


def tasks(folder: str | Path, today: date | None = None) -> dict[str, list[dict[str, Any]]]:
    """{"open": the tasks not done, soonest first, "done": the done ones, newest first}: each task's id, title, date, who_name, note, by, at, done and, for an open
    one, days_left (negative when late)."""
    today = today or clock.today()
    open_, done_ = [], []
    for d in deadlines_set.load(Path(folder))["deadlines"]:
        if not d.get("task"):
            continue
        row = {k: d.get(k) for k in ("id", "title", "date", "who_name", "note", "by", "at", "done")}
        if d.get("done"):
            done_.append(row)
            continue
        try:
            row["days_left"] = (date.fromisoformat(str(d["date"])[:10]) - today).days
        except (KeyError, ValueError):
            row["days_left"] = None
        open_.append(row)
    open_.sort(key=lambda r: (r["date"] or "", r["id"]))
    done_.sort(key=lambda r: clock.key((r.get("done") or {}).get("at")), reverse=True)
    return {"open": open_, "done": done_}


def task_row(rec_id: str, name: str | None, folder: Path, today: date, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """The open tasks of a folder as one row the lists of deadlines read (id, summary.name, journey.deadlines: what an overview row of a case holds), or None when it has none.
    A processed case's tasks are read from its timeline (journey.journey); this is for a folder with no timeline: a prospect, or a client who has no case file yet."""
    found = [x for x in deadlines_set.open_deadlines(folder, today, cfg) if x.get("task")]
    if not found:
        return None
    return {"id": rec_id, "summary": {"name": name},
            "journey": {"deadlines": [{k: x[k] for k in ("id", "date", "what", "owner") if k in x} | {k: x[k] for k in ("who", "who_name", "set", "note", "task") if x.get(k)} for x in found]}}


def unprocessed_rows(data_root: str | Path, portal_root: str | Path | None, today: date, cfg: dict[str, Any], visible, candidates=None) -> list[dict[str, Any]]:
    """The open tasks of clients with no case file yet (a client who was just added, or a prospect that just became one): their folder in the clients' folder holds the notes
    and the tasks, and they have no timeline to show them in. visible(folder): the same gate as a case's. A client whose case with the firm has ended is left out.
    candidates: the folders to look at (the review app's lists hold which they are); None looks at every folder of the clients' folder."""
    import engagement

    rows = []
    root = Path(data_root)
    found = sorted(p for p in root.iterdir() if p.is_dir() and (p / deadlines_set.FILE).exists() and not (p / "fact_graph.json").exists()) if root.is_dir() and candidates is None else candidates or ()
    for d in found:
        if not ((d / deadlines_set.FILE).exists() and not (d / "fact_graph.json").exists()):
            continue  # (a candidate is checked again: the lists' copy may be a moment old)
        if not visible(d) or engagement.end_info(d) or engagement.conflict_declined(d):
            continue
        row = task_row(d.name, engagement.client_name(d, portal_root), d, today, cfg)
        if row:
            rows.append(row | {"unprocessed": True})
    return rows


def carry(from_folder: str | Path, to_folder: str | Path, prospect_id: str) -> dict[str, int]:
    """A prospect became a client: its notes and its tasks go onto the case as they were (who wrote them and when, done or not), each note marked as carried. The
    prospect's own files stay as they are (nothing is deleted). Returns {"notes": n, "tasks": n}. Carrying twice adds nothing the second time."""
    from_folder, to_folder = Path(from_folder), Path(to_folder)

    def key(n: dict[str, Any]) -> str:
        return f"{n.get('at')}|{n.get('by') or ''}|{n.get('text', '')}"

    have = {key(n): n["id"] for n in _read(to_folder)["notes"] if n.get("carried")}
    renumbered: dict[str, str] = {}  # the prospect's note id -> the id the note has on the case: a correction keeps pointing at the note it corrects
    moved = 0
    for n in sorted(_read(from_folder)["notes"], key=lambda x: (clock.key(x.get("at")), int(str(x.get("id", "n.0")).split(".")[1] or 0))):
        if key(n) in have:
            renumbered[n["id"]] = have[key(n)]
            continue
        row = add_note(to_folder, n["text"], n.get("by") or "the office", n.get("role"), carried=prospect_id, at=n.get("at"), corrects=renumbered.get(n.get("corrects") or ""),
                       attorney_only=bool(n.get("attorney_only")) and n.get("role") in ("attorney", None))
        renumbered[n["id"]] = row["id"]
        moved += 1
    data_to, data_from = deadlines_set.load(to_folder), deadlines_set.load(from_folder)
    taken = {d.get("carried_from") for d in data_to["deadlines"] if d.get("carried_from")}
    moved_tasks = 0
    for d in data_from["deadlines"]:
        if not d.get("task") or f"{prospect_id}|{d['id']}" in taken:
            continue
        n = 1 + max([int(str(x["id"]).split(".")[1]) for x in data_to["deadlines"] if str(x.get("id", "")).startswith("set.")] or [0])
        data_to["deadlines"].append(d | {"id": f"set.{n}", "carried_from": f"{prospect_id}|{d['id']}"})
        moved_tasks += 1
    if moved_tasks:
        deadlines_set._write(to_folder, data_to)
    return {"notes": moved, "tasks": moved_tasks}
