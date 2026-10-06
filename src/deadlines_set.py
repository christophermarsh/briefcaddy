"""Deadlines a person sets on a case, and the person responsible for any deadline (data/clients/<id>/deadlines_set.json).

The product works out most deadlines from the case's documents (src/journey.py). A person adds the rest on the case page ("Add a
deadline": a title, a date, a person responsible from the staff list, a note) and names who is responsible for one the product
worked out (an assignment, not a change to the deadline: the product's own deadline is re-worked every time).

    {"version": 1,
     "deadlines": [{"id": "set.1", "title", "date" (YYYY-MM-DD), "who" (a staff email, or null), "who_name", "note", "by", "at",
                    "done": null | {"by", "at"}, "task": true (only on a task: src/case_notes.py), "carried_from": "<prospect id>|<its id>" (a task that came
                    over from a prospect that became a client)}],
     "assigned": {"<a deadline's id>": {"email", "name", "by", "at"}}}

A deadline a person set is never deleted: marking it done hides it from every list (What's due, My work, the calendar feed, the case
page's open deadlines) and keeps who and when in the file and the case page's "Done" fold. Its id is "set.<n>", n counting up from
1 and never reused. It reaches every list the way the expiry radar's deadlines do: journey.journey() adds it, so What's due, My work,
the calendar feed, the month view and the overnight run's counts see it with no second code path.

Every change is one row in the event ledger (kind "journey"); its sentence never carries the title, the note or the date.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import clock
import events

FILE = "deadlines_set.json"
VERSION = 1
OWNERS = ("attorney", "paralegal")
MAX_TITLE, MAX_NOTE = 160, 600
MIN_YEAR, MAX_YEAR = 2000, 2100  # the month view's range: a date outside it is refused, in words


def _read(client_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((client_dir / FILE).read_text(encoding="utf-8")) if (client_dir / FILE).exists() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"version": VERSION, "deadlines": list(data.get("deadlines") or []), "assigned": dict(data.get("assigned") or {})}


def _write(client_dir: Path, data: dict[str, Any]) -> None:
    path = client_dir / FILE
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load(client_dir: str | Path) -> dict[str, Any]:
    return _read(Path(client_dir))


def mtime(client_dir: str | Path) -> float:
    p = Path(client_dir) / FILE
    return p.stat().st_mtime if p.exists() else 0.0


def _person(people: list[dict[str, Any]], email: Any, typed: Any = None) -> tuple[str | None, str | None, str | None]:
    """(email, name, role) of the person responsible. people: the staff accounts [{email, name, role}] (empty when the app runs without
    accounts: then a typed name is taken as it is). A blank answer is nobody yet. An email that is not on the staff list is refused."""
    email = str(email or "").strip().lower()
    if not email:
        name = " ".join(str(typed or "").split())[:80]
        if name and people:
            raise ValueError("Choose the person responsible from the staff list.")
        return None, name or None, None
    found = next((p for p in people if p["email"] == email), None)
    if found is None:
        raise ValueError("Choose the person responsible from the staff list.")
    return found["email"], found["name"], found.get("role")


def _who(by: str) -> str:
    by = str(by or "").strip()
    if not by:
        raise ValueError("Enter your name first: every change records who made it.")
    return by


def add(client_dir: str | Path, title: str, when: str, who: Any, note: str, by: str, people: list[dict[str, Any]], typed: Any = None, task: bool = False,
        ledger: bool = True) -> dict[str, Any]:
    """A deadline a person sets: a title, a date, the person responsible (optional) and a note. task: it is one of the case's tasks (src/case_notes.py: the same row, marked,
    so every list that reads deadlines reads tasks too). ledger: write the ledger row here (a task's is written by src/case_notes.py, which knows whether the folder is a case or a prospect)."""
    client_dir = Path(client_dir)
    by = _who(by)
    title = " ".join(str(title or "").split())
    if not title:
        raise ValueError("Say what the deadline is for.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(when or "")):
        raise ValueError("Choose the date.")
    try:
        day = date.fromisoformat(str(when))
    except ValueError:
        raise ValueError("That is not a date: check the day and the month.") from None
    if not MIN_YEAR <= day.year <= MAX_YEAR:
        raise ValueError(f"Choose a date between the years {MIN_YEAR} and {MAX_YEAR}.")
    email, name, role = _person(people, who, typed)
    data = _read(client_dir)
    n = 1 + max([int(str(d["id"]).split(".")[1]) for d in data["deadlines"] if str(d.get("id", "")).startswith("set.")] or [0])
    row = {"id": f"set.{n}", "title": title[:MAX_TITLE], "date": day.isoformat(), "who": email, "who_name": name, "role": owner_for(role),
           "note": " ".join(str(note or "").split())[:MAX_NOTE] or None, "by": by, "at": clock.stamp(), "done": None} | ({"task": True} if task else {})
    data["deadlines"].append(row)
    _write(client_dir, data)
    if ledger:
        events.record("journey", "deadline_added", "Added a deadline to the case", case_dir=client_dir, who=by)
    return row


def assign(client_dir: str | Path, deadline_id: str, who: Any, by: str, people: list[dict[str, Any]], typed: Any = None, ledger: bool = True) -> dict[str, Any]:
    """Names the person responsible for a deadline, one a person set or one the product worked out (an assignment record: the deadline
    itself is not changed). A blank person takes the assignment off."""
    client_dir = Path(client_dir)
    by = _who(by)
    deadline_id = str(deadline_id or "")
    if not deadline_id:
        raise ValueError("Choose the deadline.")
    email, name, role = _person(people, who, typed)
    data = _read(client_dir)
    mine = next((d for d in data["deadlines"] if d["id"] == deadline_id), None)
    if mine is not None:
        if mine.get("done"):
            raise ValueError("This deadline is done.")
        mine.update(who=email, who_name=name, role=owner_for(role))
    elif email or name:
        data["assigned"][deadline_id] = {"email": email, "name": name, "by": by, "at": clock.stamp()}
    else:
        data["assigned"].pop(deadline_id, None)
    _write(client_dir, data)
    if ledger:
        events.record("journey", "deadline_assigned", "Named the person responsible for a deadline" if email or name else "Took the person responsible off a deadline",
                      case_dir=client_dir, who=by)
    return {"id": deadline_id, "who": email, "who_name": name}


def done(client_dir: str | Path, deadline_id: str, by: str, ledger: bool = True) -> dict[str, Any]:
    """Marks a deadline a person set as done: who and when. It is hidden from the lists and kept; it is never deleted."""
    client_dir = Path(client_dir)
    by = _who(by)
    data = _read(client_dir)
    mine = next((d for d in data["deadlines"] if d["id"] == str(deadline_id or "")), None)
    if mine is None:
        raise LookupError("No such deadline.")
    if not mine.get("done"):
        mine["done"] = {"by": by, "at": clock.stamp()}
        _write(client_dir, data)
        if ledger:
            events.record("journey", "deadline_done", "Marked a deadline done", case_dir=client_dir, who=by)
    return mine


# -- what journey.journey() reads --------------------------------------------------------------------------------------


def open_deadlines(client_dir: str | Path, today: date, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """The deadlines a person set that are not done, in the shape journey.py's own deadlines have (id, date, days_left, level, what, owner,
    source) and the person responsible (who, who_name), the note and "set": True."""
    import journey

    out = []
    for d in _read(Path(client_dir))["deadlines"]:
        if d.get("done"):
            continue
        try:
            when = date.fromisoformat(str(d["date"])[:10])
        except (KeyError, ValueError):
            continue
        row = journey._deadline(d["id"], when, d["title"], d.get("role") or "paralegal", today, cfg)
        out.append(row | {"who": d.get("who"), "who_name": d.get("who_name"), "note": d.get("note"), "set": True, "set_by": d.get("by")} | ({"task": True} if d.get("task") else {}))
    return out


def finished(client_dir: str | Path) -> list[dict[str, Any]]:
    """The deadlines a person set that are done, newest first: for the case page's "Done" fold."""
    rows = [{"id": d["id"], "what": d["title"], "date": d["date"], "who_name": d.get("who_name"), "note": d.get("note"), "done_by": d["done"].get("by"),
             "done_at": d["done"].get("at")} | ({"task": True} if d.get("task") else {}) for d in _read(Path(client_dir))["deadlines"] if d.get("done")]
    return sorted(rows, key=lambda r: clock.key(r["done_at"]), reverse=True)


def apply_assignments(client_dir: str | Path, deadlines: list[dict[str, Any]]) -> None:
    """Puts the person responsible on each of the product's own deadlines that has an assignment record (in place)."""
    assigned = _read(Path(client_dir))["assigned"]
    for d in deadlines:
        a = assigned.get(d["id"])
        if a and not d.get("set"):
            d["who"], d["who_name"] = a.get("email"), a.get("name")


def owner_for(role: str | None) -> str:
    return role if role in OWNERS else "paralegal"
