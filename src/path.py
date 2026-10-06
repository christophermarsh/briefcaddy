"""A case's own path (brief S3): the steps the templates know, put in this case's order, approved by an attorney, and everything derived from it.

WHAT A PATH IS. The ordered steps of a case: the stages of schemas/registers/journey.json (each has a name, the firm's steps at that stage, the client's
own words in four languages, and the filings it leads to in "next_filings"). The product derives a case's path from its track (journey.track_of and the
"stages" list for that track: the template). A paralegal or an attorney may change it for one case: reorder its steps, leave a step out, add a step that
another template holds (a filing, or the state-court step), set a step's expected date, and leave out a filing the case's journey offers. Never a step
typed by hand: a step id the templates do not hold is refused, and so is a filing they do not name.

THE TWO COPIES. The template stays what it is. The case keeps its own copy (path.json in its folder, in the catalog as "case_path") only once a change is
saved: "approved" is the path every derived thing uses; "proposed" is a change waiting for an attorney (My approvals, "Path change"; the case's Attorney
sign-off tab). A paralegal's proposal derives nothing until an attorney approves it; an attorney's own change is recorded as the same decision and approved
in the same step. Undo removes the case's copy: the template again. Every proposal, approval, refusal and undo is a ledger row (kind "path").

WHAT IT DERIVES (derive), from the templates' own records and nothing else:
  - the filings, in the path's order (journey.json next_filings for each step; "now": false ones only if needed), less those left out;
  - the papers the client must bring: each filing's packet exhibits (schemas/packets/<filing>.json: title, document types, required);
  - the questionnaire: the sections the portal asks for each filing (portal/bank.py: the green card questionnaire, or a filing's own bank);
  - the deadlines: a step with an expected date and a filing the template files at that step derives "the template's label, by that date"; a step with
    no filing (a wait, the state court, a result) derives none, and the screen says so;
  - the packet order (the filings in order), and the client's page (each step's name in the client's language, from the template's own words).
What the templates cannot give is left out and said (a filing with no packet template, no questionnaire of its own, a step with no deadline rule).
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
import schema_path

FILE = "path.json"
VERSION = 1
LANGS = ("pt", "es", "en", "ht")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NO_RULE = "No deadline: the templates hold no rule for this step."
NO_PACKET = "No packet template for this filing: its papers are not listed."
NO_QUESTIONS = "No questionnaire of its own: the portal asks nothing more for it."


def _settings() -> dict[str, Any]:
    import journey

    return journey.settings()


# -- what a path may hold ------------------------------------------------------------------------------------------------------------------------------


def filing_names() -> dict[str, str]:
    """Every filing the templates name, with its form: journey's own list of forms, and each step's filings."""
    import journey

    out = dict(journey._FILING_FORMS)
    for stage, rows in (_settings().get("next_filings") or {}).items():
        if not stage.startswith("_"):
            for f, _label, _now in rows:
                out.setdefault(f, f.upper())
    return out


def vocabulary() -> list[dict[str, Any]]:
    """Every step a path may hold, in the order the templates first list them: id, name, kind (filing, court, step), the filings it leads to, the
    tracks whose template holds it, and the firm's steps at it (who does what)."""
    s = _settings()
    order: list[str] = []
    tracks: dict[str, list[str]] = {}
    for track, stages in s["stages"].items():
        for x in stages:
            if x not in order:
                order.append(x)
            tracks.setdefault(x, []).append(track)
    order += [x for x in s["stage_names"] if x not in order]
    forms = filing_names()
    out = []
    for x in order:
        rows = (s.get("next_filings") or {}).get(x) or []
        filings = [{"filing": f, "form": forms.get(f, f), "label": label, "now": bool(now)} for f, label, now in rows]
        kind = "court" if x == "state_court" else "filing" if any(f["now"] for f in filings) else "step"
        out.append({"id": x, "name": s["stage_names"][x], "kind": kind, "filings": filings, "tracks": tracks.get(x, []),
                    "owners": [{"owner": o, "text": t} for o, t in (s["steps"].get(x) or []) if not str(o).startswith("_")]})
    return out


def _known() -> dict[str, dict[str, Any]]:
    return {v["id"]: v for v in vocabulary()}


# -- the record ----------------------------------------------------------------------------------------------------------------------------------------


def read(client_dir: str | Path) -> dict[str, Any]:
    try:
        data = json.loads((Path(client_dir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"version": VERSION, "approved": data.get("approved"), "proposed": data.get("proposed"), "history": data.get("history") or []}


def _write(client_dir: Path, record: dict[str, Any]) -> None:
    path = Path(client_dir) / FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def approved(client_dir: str | Path) -> dict[str, Any] | None:
    """The case's approved path ({"steps": [{"id", "date"}], "removed": [filing ids]}), or None: the template."""
    return read(client_dir)["approved"]


def validate(steps: Any, removed: Any = ()) -> tuple[list[dict[str, Any]], list[str]]:
    """The steps and the filings left out, as the record keeps them; refused (ValueError) when a step or a filing is not one the templates hold."""
    known = _known()
    if not isinstance(steps, list) or not steps:
        raise ValueError("A path has at least one step.")
    out, seen = [], set()
    for x in steps:
        sid = x.get("id") if isinstance(x, dict) else x
        if not isinstance(sid, str) or sid not in known:
            raise ValueError("A path holds only the steps the templates know: a step typed by hand is not one of them.")
        if sid in seen:
            raise ValueError(f"The step “{known[sid]['name']}” is on the path twice.")
        seen.add(sid)
        when = (x.get("date") if isinstance(x, dict) else None) or None
        if when is not None:
            when = str(when).strip()
            if not ISO.match(when):
                raise ValueError("An expected date is a date (YYYY-MM-DD).")
            date.fromisoformat(when)
        out.append({"id": sid, "date": when})
    forms = filing_names()
    gone = []
    for f in removed or []:
        if f not in forms:
            raise ValueError("Only a filing the templates name can be left out.")
        if f not in gone:
            gone.append(f)
    return out, gone


def change_words(before: dict[str, Any], after: dict[str, Any]) -> str:
    """The change in words: "I-765 (ead) left out; State court added before Preparing the I-485 packet"."""
    known, forms = _known(), filing_names()
    name = lambda sid: known[sid]["name"] if sid in known else sid  # noqa: E731
    old = [x["id"] for x in before["steps"]]
    new = [x["id"] for x in after["steps"]]
    words = []
    for f in after.get("removed") or []:
        if f not in (before.get("removed") or []):
            words.append(f"{forms.get(f, f)} left out")
    for f in before.get("removed") or []:
        if f not in (after.get("removed") or []):
            words.append(f"{forms.get(f, f)} back on the path")
    for sid in old:
        if sid not in new:
            words.append(f"{name(sid)} removed")
    for i, sid in enumerate(new):
        if sid not in old:
            words.append(f"{name(sid)} added " + (f"before {name(new[i + 1])}" if i + 1 < len(new) else f"after {name(new[i - 1])}" if i else "first"))
    kept_old = [s for s in old if s in new]
    kept_new = [s for s in new if s in old]
    if kept_old != kept_new:
        words.append("order changed: " + ", ".join(name(s) for s in kept_new))
    dates_old = {x["id"]: x.get("date") for x in before["steps"]}
    for x in after["steps"]:
        if x.get("date") and dates_old.get(x["id"]) != x["date"]:
            words.append(f"{name(x['id'])} expected {_us(x['date'])}")
    return "; ".join(words) or "no change"


def _us(iso: str) -> str:
    return f"{iso[5:7]}/{iso[8:10]}/{iso[:4]}"


def _base(client_dir: Path, template: list[str]) -> dict[str, Any]:
    """What the change is measured against: the approved path, or the template."""
    return approved(client_dir) or {"steps": [{"id": x, "date": None} for x in template], "removed": []}


def _row(action: str, what: str, client_dir: Path, who: str | None, role: str | None) -> None:
    events.record("path", action, what, case_dir=client_dir, who=who, role=role if role in ("attorney", "paralegal") else None)


def propose(client_dir: str | Path, steps: Any, removed: Any, reason: str, who: str, email: str | None, role: str | None, template: list[str]) -> dict[str, Any]:
    """A change to the case's path, with its reason. A paralegal's waits for an attorney; an attorney's is approved in the same step."""
    client_dir = Path(client_dir)
    reason = " ".join(str(reason or "").split())
    if not reason:
        raise ValueError("Say why the path changes: the reason is kept with the change.")
    steps, removed = validate(steps, removed)
    record = read(client_dir)
    base = _base(client_dir, template)
    after = {"steps": steps, "removed": removed}
    words = change_words(base, after)
    if words == "no change":
        raise ValueError("The path is the same as before: nothing to save.")
    now = clock.stamp()
    proposal = after | {"by": who, "by_email": email, "role": role, "at": now, "reason": reason, "change": words}
    record["history"].append({"at": now, "by": who, "action": "proposed", "change": words, "reason": reason})
    _row("proposed", f"Proposed a change to the case's path: {words}. Why: {reason}", client_dir, who, role)
    if role == "attorney" or role is None:
        record["approved"] = proposal | {"approved_by": who, "approved_at": now}
        record["proposed"] = None
        record["history"].append({"at": now, "by": who, "action": "approved", "change": words, "reason": reason})
        _row("approved", f"Approved the case's path: {words}", client_dir, who, role)
    else:
        record["proposed"] = proposal
    _write(client_dir, record)
    return record


def approve(client_dir: str | Path, who: str, role: str | None) -> dict[str, Any]:
    client_dir = Path(client_dir)
    if role not in ("attorney", None):
        raise PermissionError("A change to the path is approved by an attorney.")
    record = read(client_dir)
    p = record["proposed"]
    if not p:
        raise LookupError("No change to the path is waiting.")
    now = clock.stamp()
    record["approved"] = p | {"approved_by": who, "approved_at": now}
    record["proposed"] = None
    record["history"].append({"at": now, "by": who, "action": "approved", "change": p["change"], "reason": p["reason"]})
    _row("approved", f"Approved the change to the case's path proposed by {p.get('by')}: {p['change']}", client_dir, who, role)
    _write(client_dir, record)
    return record


def refuse(client_dir: str | Path, who: str, role: str | None, reason: str) -> dict[str, Any]:
    client_dir = Path(client_dir)
    if role not in ("attorney", None):
        raise PermissionError("A change to the path is refused by an attorney.")
    reason = " ".join(str(reason or "").split())
    if not reason:
        raise ValueError("Say why the change is refused: the paralegal reads it.")
    record = read(client_dir)
    p = record["proposed"]
    if not p:
        raise LookupError("No change to the path is waiting.")
    record["proposed"] = None
    record["history"].append({"at": clock.stamp(), "by": who, "action": "refused", "change": p["change"], "reason": reason})
    _row("refused", f"Refused the change to the case's path proposed by {p.get('by')}: {p['change']}. Why: {reason}", client_dir, who, role)
    _write(client_dir, record)
    return record


def undo(client_dir: str | Path, who: str, role: str | None, reason: str) -> dict[str, Any]:
    """The case's own copy taken off: the template again (a waiting proposal goes too)."""
    client_dir = Path(client_dir)
    if role not in ("attorney", None):
        raise PermissionError("Going back to the template is an attorney's decision.")
    reason = " ".join(str(reason or "").split())
    if not reason:
        raise ValueError("Say why the case goes back to the template.")
    record = read(client_dir)
    if not record["approved"] and not record["proposed"]:
        raise LookupError("The case already follows the template.")
    record["approved"] = record["proposed"] = None
    record["history"].append({"at": clock.stamp(), "by": who, "action": "undone", "change": "back to the template", "reason": reason})
    _row("undone", f"Took the case back to the template's path. Why: {reason}", client_dir, who, role)
    _write(client_dir, record)
    return record


# -- what a path derives --------------------------------------------------------------------------------------------------------------------------------


def _packet(filing: str) -> dict[str, Any] | None:
    try:
        return json.loads(schema_path.path("packet", filing).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, LookupError):
        return None


def _questions(filing: str) -> dict[str, Any] | None:
    """The portal's questionnaire for a filing: its own bank (portal/bank.py FILING_BANKS) or, for the green card filings, the green card questionnaire."""
    from portal import bank

    if filing not in bank.FILING_BANKS and filing not in GREEN_CARD:
        return None
    try:
        b = bank.load_bank(filing=filing if filing in bank.FILING_BANKS else None)
    except (OSError, ValueError, KeyError):
        return None
    return {"bank": filing if filing in bank.FILING_BANKS else "i485", "sections": [{"id": s["id"], "title": _en(s.get("title"))} for s in b.get("sections") or []]}


GREEN_CARD = ("i485", "family", "caa", "asylee", "vawa")  # the portal's green card questionnaire is for these filings (portal/bank.py load_bank: None / "i485")


def _en(text: Any) -> str:
    return text.get("en") or next(iter(text.values()), "") if isinstance(text, dict) else str(text or "")


def derive(steps: list[dict[str, Any]], removed: list[str] | None = None, today: date | None = None) -> dict[str, Any]:
    """Everything a path gives (the module docstring), from the templates only."""
    s = _settings()
    known = _known()
    removed = list(removed or [])
    filings: list[dict[str, Any]] = []
    deadlines: list[dict[str, Any]] = []
    for x in steps:
        v = known[x["id"]]
        files_here = [f for f in v["filings"] if f["filing"] not in removed]
        for f in files_here:
            if not any(g["filing"] == f["filing"] for g in filings):
                filings.append(f | {"step": x["id"]})
        now_files = [f for f in files_here if f["now"]]
        if x.get("date"):
            if now_files:
                owner = "attorney" if any(o["owner"] == "attorney" for o in v["owners"]) else (v["owners"][0]["owner"] if v["owners"] else "paralegal")
                for f in now_files:
                    deadlines.append({"step": x["id"], "date": x["date"], "what": f["label"], "owner": owner, "filing": f["filing"]})
            else:
                deadlines.append({"step": x["id"], "date": x["date"], "what": None, "none": NO_RULE})
    now_filings = [f for f in filings if f["now"]]
    documents, seen = [], set()
    for f in now_filings:
        p = _packet(f["filing"])
        if p is None:
            documents.append({"filing": f["filing"], "form": f["form"], "none": NO_PACKET})
            continue
        for e in p.get("exhibits") or []:
            key = tuple(e.get("types") or []) or (e.get("id"),)
            if key in seen:
                continue
            seen.add(key)
            documents.append({"filing": f["filing"], "form": f["form"], "id": e.get("id"), "title": e.get("title"), "types": e.get("types") or [],
                              "required": bool(e.get("required"))})
    questionnaire = []
    for f in now_filings:
        qs = _questions(f["filing"])
        questionnaire.append({"filing": f["filing"], "form": f["form"]} | (qs if qs else {"none": NO_QUESTIONS}))
    client = {lang: [{"id": x["id"], "name": _say(s["client"].get(x["id"]), lang)} for x in steps if x["id"] in s["client"]] for lang in LANGS}
    return {"filings": filings, "packet_order": [f["filing"] for f in now_filings], "documents": documents, "questionnaire": questionnaire,
            "deadlines": deadlines, "client": client}


def _say(words: Any, lang: str) -> str:
    if not isinstance(words, dict):
        return ""
    got = words.get(lang) or words.get("en") or []
    return got[0] if isinstance(got, list) and got else str(got or "")


def view(client_dir: str | Path, template: list[str], role: str | None, today: date | None = None) -> dict[str, Any]:
    """The Path tab: the template, the approved path (used for everything) and its derivations, the waiting change and its derivations, the steps a
    path may hold, and the history."""
    record = read(client_dir)
    base = _base(Path(client_dir), template)
    p = record["proposed"]
    return {"template": [{"id": x, "date": None} for x in template], "approved": record["approved"], "current": base, "proposed": p,
            "derived": derive(base["steps"], base.get("removed"), today), "derived_proposed": derive(p["steps"], p.get("removed"), today) if p else None,
            "vocabulary": vocabulary(), "filings": [{"filing": f, "form": n} for f, n in sorted(filing_names().items())],
            "history": record["history"][-50:], "can_approve": role in ("attorney", None), "no_rule": NO_RULE}


def place(found: str, template: list[str], custom: list[str]) -> str:
    """The stage the papers say the case is at, on the case's own path: itself when the path holds it, else the first step of the path that the template
    puts at or after it (the last step when none does)."""
    if found in custom:
        return found
    order = {x: i for i, x in enumerate(template)}
    at = order.get(found, -1)
    for x in custom:
        if order.get(x, 10_000) >= at:
            return x
    return custom[-1]
