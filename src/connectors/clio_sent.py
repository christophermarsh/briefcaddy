"""What went to Clio, per case (data/clients/<case id>/clio_sent.json): the line on the case page, and what failed.

The sync (src/connectors/clio.py sync_out) keeps its working record of what it sent in data/clio/state.json. This file is the case's own,
short record of the same, in words a person can read on the case page ("In Clio: packet sent 10/02/2026, 3 deadlines, stage note 09/30/2026;
last sync 10/03/2026 02:10"), written beside the case so it is closed by the same gate as the case (a restricted case's is closed with it) and
is in the export of the firm's data. It is written only when something in it changes (a document or a note or an entry went, a refusal began or
ended), and each write is a row in the event ledger whose sentence carries no date, id or name.

    {"version": 1, "matter": "<the Clio matter's id>",
     "packet_at": stamp | null,       when the filing packet or its review bundle last went (a new version counts)
     "mailings": n,                   mailing records sent
     "stage_at": stamp | null,        when the stage note last went
     "end": {"state", "on", "at"} | null   the case's end state sent as a note (and "reopened": stamp when it was reopened after)
     "deadlines": n, "tasks": n,      calendar entries and tasks open in Clio now
     "failed": [{"at", "what"}],      what Clio refused for this case in the last attempt, in words; empty when the last attempt went through
     "by_hand": {"by", "at"} | null}  who last pressed "Send now" for this case

The last sync is not in this file: it is the connection's own (the sync that covered every linked case), so nothing is written nightly for a
case with nothing new.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import clock
import events

FILE = "clio_sent.json"
VERSION = 1
RETRY = "The overnight run tries again tonight."


def read(out_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((Path(out_dir) / FILE).read_text(encoding="utf-8")) if (Path(out_dir) / FILE).exists() else {}
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _write(out_dir: Path, rec: dict[str, Any]) -> None:
    path = Path(out_dir) / FILE
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def summary(matter: str, mine: dict[str, Any]) -> dict[str, Any]:
    """The record's fields (but "failed" and "by_hand") from what the sync keeps for the case (clio.py's `mine`)."""
    docs = [d.get("at") for d in (mine.get("docs") or {}).values() if d.get("at")]
    opened = [e for e in (mine.get("deadlines") or {}).values() if e.get("id") and not e.get("closed")]
    tasks = [t for t in (mine.get("tasks") or {}).values() if t.get("id") and not t.get("closed")]
    end = mine.get("end") or None
    return {"version": VERSION, "matter": str(matter), "packet_at": max(docs, key=clock.key) if docs else None, "mailings": len(mine.get("mailings") or {}),
            "stage_at": mine.get("stage_at"), "end": end, "deadlines": len(opened), "tasks": len(tasks)}


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return all(a.get(k) == b.get(k) for k in set(a) | set(b))


def _words(old: dict[str, Any], new: dict[str, Any]) -> str:
    if new.get("failed") and not old.get("failed"):
        return "Clio refused something sent for this case"
    if old.get("failed") and not new.get("failed"):
        return "What Clio refused for this case went through"
    return "Sent something about this case to Clio"


def record(out_dir: Path, matter: str, mine: dict[str, Any], failed: list[dict[str, str]], by_hand: dict[str, str] | None = None) -> bool:
    """Writes the case's record when it differs from the one there (True), with one row in the ledger. `failed`: what Clio refused in this attempt."""
    out_dir = Path(out_dir)
    old = read(out_dir)
    new = summary(matter, mine) | {"failed": failed, "by_hand": by_hand or old.get("by_hand")}
    if old and _same(old, new):
        return False
    if not old and not (new["packet_at"] or new["mailings"] or new["stage_at"] or new["end"] or new["deadlines"] or new["tasks"] or failed):
        return False  # nothing went and nothing failed: no file
    _write(out_dir, new)
    events.record("imports", "sent_to_clio", _words(old, new), case_dir=out_dir, default_who=("Clio", "system", "connector"))
    return True


def say_failed(what: str) -> list[dict[str, str]]:
    return [{"at": clock.stamp(), "what": f"{str(what).rstrip('.')}. {RETRY}"}]


# -- the line ----------------------------------------------------------------------------------------------------------


def _day(stamp: Any) -> str:
    return clock.us_date(stamp)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def sent_line(rec: dict[str, Any], last_sync: str | None) -> str:
    """"In Clio: packet sent 10/02/2026, 3 deadlines, stage note 09/30/2026; last sync 10/03/2026 02:10"."""
    parts = []
    if rec.get("packet_at"):
        parts.append(f"packet sent {_day(rec['packet_at'])}")
    if rec.get("mailings"):
        parts.append(_plural(rec["mailings"], "mailing record"))
    if rec.get("deadlines"):
        parts.append(_plural(rec["deadlines"], "deadline"))
    if rec.get("tasks"):
        parts.append(_plural(rec["tasks"], "task"))
    if rec.get("stage_at"):
        parts.append(f"stage note {_day(rec['stage_at'])}")
    end = rec.get("end") or {}
    if end.get("on"):
        parts.append(f"{str(end.get('name') or 'closing').lower()} note {_day(end['on'])}")
    text = "In Clio: " + (", ".join(parts) if parts else "nothing sent yet")
    if last_sync:
        local = clock.local(last_sync)
        text += f"; last sync {_day(last_sync)} {local.strftime('%H:%M') if local else ''}".rstrip()
    return text + "."


def case_view(data_root: Path | None, out_dir: Path, case: str, can_send_held: bool) -> dict[str, Any] | None:
    """What the case page shows about Clio, or None when this case is not a Clio matter (or Clio isn't set up here): the line, what failed, why
    nothing is sent (a protected case waits for an attorney), and which button the person may press."""
    from . import clio, clio_hooks

    if not clio.folder(data_root).exists():
        return None
    st = clio.state(data_root)
    mid = next((m for m, info in (st.get("matters") or {}).items() if info.get("case") == case), None)
    if mid is None:
        return None
    out_dir = Path(out_dir)
    held = clio.held_back(out_dir, case, st) if (out_dir / "fact_graph.json").exists() else None
    rec = read(out_dir)
    last = (st.get("last_sync") or {}).get("at")
    waiting = clio_hooks.waiting(data_root).get(mid)
    return {"line": sent_line(rec, last) if rec or not held else None, "held": held, "failed": rec.get("failed") or [],
            "not_processed": not (out_dir / "fact_graph.json").exists(), "by_hand": rec.get("by_hand"),
            "waiting": ({"since": waiting.get("since")} if waiting else None),
            "can_send": bool(clio.ready(data_root) is None and (out_dir / "fact_graph.json").exists() and (not held or can_send_held)),
            "button": "Send to Clio" if held else "Send now", "can_send_held": can_send_held}
