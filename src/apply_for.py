"""What could this person apply for: the questions an attorney would ask, for each relief the product handles, answered yes, no or unsure by a person.

The screen (on a prospect, and on a case) never concludes. It shows each relief's questions, each with the section or form item it comes from and when the
product read that source, the answers given and who gave them and when, and one sentence: the attorney decides. There is no word on it that says a person
qualifies, no score, no ranking, and no order that reflects fit: the reliefs are listed by name, alphabetically, the same for everyone. A person records an
answer; the product adds nothing to it. docs/decisions.md says why.

The questions are in schemas/registers/apply_for.json. Each one comes from a source the product already holds (the filing's own notes and checks, the form's own items as the
product's questions hold them, the rules files with their citations): `held_in` and `held_text` name the file and the words, and tests/test_apply_for.py fails when
the words are not there. A question the product holds no source for is not in the file: the relief has fewer questions, and docs/attorney_review.md says which and why.

The answers are in the folder of the case (data/clients/<id>/apply_for.json) or the prospect (data/prospects/<id>/apply_for.json):

    {"version": 1,
     "answers": {"<question id>": {"answer": "yes" | "no" | "unsure" | null (taken back), "by", "role", "at",
                                   "history": [{"answer", "by", "role", "at"}]}}}

Every answer is one row of the ledger (kind "notes"): who and when, never the question or the answer. A prospect's answers go onto the case when it becomes a client (carry).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import clock
import events
import schema_path

CATALOG = schema_path.path("register", "apply_for")
FILE = "apply_for.json"
VERSION = 1
ANSWERS = {"yes": "Yes", "no": "No", "unsure": "Unsure"}
DECIDES = "The attorney decides."
INTRO = ("These are the questions an attorney would ask, for each kind of case the firm handles, each with the section or form item it comes from and when the "
         "product read it. Anyone on staff can record an answer, and the product records who answered and when. Nothing here says what a person can apply for.")
_CACHE: dict[str, Any] = {"mtime": None, "data": None}


def catalog() -> dict[str, Any]:
    """schemas/registers/apply_for.json, read again when it changes."""
    mtime = CATALOG.stat().st_mtime
    if _CACHE["mtime"] != mtime:
        _CACHE["data"], _CACHE["mtime"] = json.loads(CATALOG.read_text(encoding="utf-8")), mtime
    return _CACHE["data"]


def question_ids() -> dict[str, str]:
    """{question id: the relief's id}."""
    return {x["id"]: r["id"] for r in catalog()["reliefs"] for x in r["questions"]}


def _read(folder: Path) -> dict[str, Any]:
    try:
        data = json.loads((folder / FILE).read_text(encoding="utf-8")) if (folder / FILE).exists() else {}
    except (OSError, ValueError):
        data = {}
    answers = data.get("answers") if isinstance(data, dict) else None
    return {"version": VERSION, "answers": dict(answers) if isinstance(answers, dict) else {}}


def _write(folder: Path, data: dict[str, Any]) -> None:
    path = folder / FILE
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def view(folder: str | Path) -> dict[str, Any]:
    """The screen's content: the sentences, and each relief with its questions and the answers so far (each answer carries who gave it and when)."""
    folder = Path(folder)
    given = _read(folder)["answers"]
    names = catalog()["held_names"]
    reliefs, total, answered = [], 0, 0
    for r in catalog()["reliefs"]:
        rows = []
        for x in r["questions"]:
            a = given.get(x["id"]) or {}
            # the Read line says where the product holds the source in words ("the product's I-360 notes"), never a file's name
            rows.append({k: x[k] for k in ("id", "text", "cite")} | {"read": f"{x['read']}; held in {names[x['held_in']]}"}
                        | {"answer": a.get("answer"), "by": a.get("by"), "role": a.get("role"), "at": a.get("at"), "changes": max(0, len(a.get("history") or []) - 1)})
        n = sum(1 for x in rows if x["answer"])
        total, answered = total + len(rows), answered + n
        reliefs.append({"id": r["id"], "name": r["name"], "about": r["about"], "questions": rows, "answered": n, "total": len(rows)})
    return {"intro": INTRO, "decides": DECIDES, "reliefs": reliefs, "answered": answered, "total": total}


def history(folder: str | Path, question_id: str) -> list[dict[str, Any]]:
    """Every answer ever given to the question, newest first (an answer changed is kept, with who changed it)."""
    return list(reversed((_read(Path(folder))["answers"].get(question_id) or {}).get("history") or []))


def answer(folder: str | Path, question_id: str, value: str | None, by: str, role: str | None = None, *, prospect: bool = False) -> dict[str, Any]:
    """Records yes, no or unsure for a question, or takes the answer back (value None or ""). A change is a new entry in the question's history: nothing is overwritten."""
    folder = Path(folder)
    by = str(by or "").strip()
    if not by:
        raise ValueError("Enter your name first: every answer records who gave it.")
    if question_id not in question_ids():
        raise LookupError("No such question.")
    value = str(value).strip().lower() if value not in (None, "") else None
    if value is not None and value not in ANSWERS:
        raise ValueError("Answer yes, no or unsure.")
    data = _read(folder)
    entry = data["answers"].get(question_id) or {"history": []}
    if entry.get("answer") == value and entry["history"]:
        return entry  # said again: nothing to record
    if value is None and not entry["history"]:
        return entry
    row = {"answer": value, "by": by, "role": role if role in ("attorney", "paralegal") else None, "at": clock.stamp()}
    entry |= row
    entry["history"] = list(entry["history"]) + [row]
    data["answers"][question_id] = entry
    _write(folder, data)
    what = "Answered a question on what the person could apply for" if value else "Took back an answer on what the person could apply for"
    if prospect:
        events.record("notes", "applied_for_answered" if value else "applied_for_taken_back", what, case=f"prospect:{folder.name}", home=folder.parent.parent, who=by, role=role)
    else:
        events.record("notes", "applied_for_answered" if value else "applied_for_taken_back", what, case_dir=folder, who=by, role=role)
    return entry


def carry(from_folder: str | Path, to_folder: str | Path) -> int:
    """A prospect became a client: its answers go onto the case with their history (who answered and when), for a question the case has no answer to. Returns how many."""
    to_folder = Path(to_folder)
    mine, theirs = _read(to_folder), _read(Path(from_folder))
    moved = 0
    for qid, entry in theirs["answers"].items():
        if qid not in mine["answers"]:
            mine["answers"][qid] = entry
            moved += 1
    if moved:
        _write(to_folder, mine)
    return moved
