"""The ledger is tamper-evident: what its chain proves, the daily seal the attorney sees, and the check that names the first row that does not match.

    data/ledger_anchors.jsonl   one line a day, written by the overnight run: day, the last row's time, its hash, the day's row count (0600, appended only)
    data/ledger_check.json      the result of the last check, for the Settings line to read (a view never walks the ledger)

The chain (src/events.py record): each row carries `prev`, the hash of the row before it, and `hash`, the SHA-256 of its own canonical JSON without `hash`
(events.row_hash). The first row of a month's file links to the last row of the month before; the first row ever, and the first row after rows written
before the chain, has prev "". verify() walks the month files in order, recomputes every hash, checks every link, then checks every anchor against the rows.

What it proves: that a row was changed, removed, moved or cut off after it was written, and which row is the first that does not match. An anchor is the day's
last hash: the firm can write it down or print it (tools/verify_ledger.py --anchors), and a changed past cannot match it.
What it does not prove: who changed the history (the ledger says who wrote each row, not who edited the file), and a copy of the whole ledger rewritten from its
first row with the anchors file replaced or lost would pass: the anchors the firm has on paper, or in a place the server cannot write, are its defence. Nor does
it see rows lost off the end of the ledger since the last anchor; the days before it are held by their anchors.

A purged case (src/purge.py): its rows are blanked in place, not removed (events.tombstone: the time, prev and hash stay, the content goes), so every link and
every seal still holds. A blanked row's hash cannot be recomputed, so the check accepts it only where a purge's line in ledger_redactions.jsonl (beside the
month files) names its purge, and the rows blanked under that purge are exactly as many, with exactly the hashes, that line's digest says; any other blanked
row is "changed". What this does not prove: that the purge was the attorney's (the purge's own rows in the ledger, which stay whole, say who asked and who
confirmed), nor anything against someone who can rewrite the redactions file as well as the ledger, the same limit as above.

The check reads the files as bytes of lines, a month at a time, and keeps three numbers a day; nothing here writes a row of the ledger.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import clock
import events

ANCHORS = "ledger_anchors.jsonl"
CHECK = "ledger_check.json"
SEAL_CHARS = 16  # the hash as people read it out and write it down


def anchors_path(base: str | Path) -> Path:
    return Path(base).with_name(ANCHORS)


def check_path(base: str | Path) -> Path:
    return Path(base).with_name(CHECK)


def _mdy(day: Any) -> str:
    """"2026-10-03" or a stamp as 10/03/2026: the day the row's own time says (the firm's, as events.month_file cuts it), never moved to another zone."""
    try:
        return date.fromisoformat(str(day)[:10]).strftime("%m/%d/%Y")
    except ValueError:
        return str(day)


def _when(at: Any) -> str:
    """A stamp as 10/03/2026 14:05 (the time it carries: the firm's own)."""
    text = str(at or "")
    return f"{_mdy(text[:10])} {text[11:16]}".strip() if text[11:16] else _mdy(text)


def read_anchors(base: str | Path) -> list[dict[str, Any]]:
    """Every anchor, in the order written (a line that is not one is passed over: the check says the rows it would have vouched for)."""
    path = anchors_path(base)
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                a = json.loads(line)
            except ValueError:
                continue
            if isinstance(a, dict) and isinstance(a.get("day"), str) and isinstance(a.get("hash"), str) and isinstance(a.get("rows"), int):
                out.append(a)
    return out


def _row(at: Any, who: Any, path: Path, line: int) -> dict[str, Any]:
    return {"at": str(at or ""), "who": str(who or ""), "file": path.name, "line": line}


def _problem(kind: str, where: dict[str, Any] | None, **more: Any) -> dict[str, Any]:
    return {"kind": kind, **(where or {}), **more}


def words(p: dict[str, Any]) -> str:
    """The problem as one sentence: the row's time, who, which file, and whether it was changed, removed, moved or the file cut short after it."""
    row = f"the row written at {_when(p.get('at'))} by {p.get('who') or 'someone'}" if p.get("at") else "a row"
    where = f" ({p['file']}, line {p['line']})" if p.get("file") else ""
    kind = p["kind"]
    if kind == "changed":
        return f"{row} was changed{where}."
    if kind == "unreadable":
        after = p.get("after")
        return (f"a row in {p['file']} at line {p['line']} cannot be read: it was changed or damaged"
                + (f"; the row before it was written at {_when(after['at'])} by {after['who'] or 'someone'}" if after else "") + ".")
    if kind == "removed":
        return f"a row was removed before {row}{where}."
    if kind == "moved":
        return f"rows are out of order: {row} comes before the row it was written after{where}."
    if kind == "cut_short":
        if p.get("at"):
            return f"{p['cut']} was cut short after {row}{where}."
        return f"{p['cut']} was cut short: the rows the seal for {_mdy(p['day'])} names are not there."
    if kind == "gone":
        return f"the last row sealed for {_mdy(p['day'])}, written at {_when(p['at'])}, is not in the ledger: it was removed or changed."
    if kind == "blanked":
        return f"{row} was blanked as a purge blanks a row, but no purge recorded it{where}."
    if kind == "blanked_count":
        return f"the rows blanked by a purge do not match what the purge recorded ({p['found']} found, {p['recorded']} recorded)."
    if kind == "day_short":
        return f"the seal for {_mdy(p['day'])} counts {p['sealed']} rows and {p['found']} are there; {row} is the last that day{where}."
    return f"the seal for {_mdy(p['day'])} counts {p['sealed']} rows and {p['found']} are there{where}."


def verify(base: str | Path) -> dict[str, Any]:
    """Walks every month file of the ledger in order. Returns {"ok", "rows" (chained rows checked), "before" (rows written before the chain), "first", "last" (the
    first and last chained row's time), "anchors" (how many), "problem" (None, or {"kind", "at", "who", "file", "line", ...}: the first that does not match),
    "line" (the one sentence the tool prints and Settings shows), "days" ({day: {"rows", "hash", "at"}}, when intact)}."""
    base = Path(base)
    anchors = read_anchors(base)
    sealed = {a["hash"] for a in anchors}
    recorded = read_redactions(base)  # {purge id: {"rows", "digest"}}: the rows each purge blanked (src/purge.py)
    blanked: dict[str, list[str]] = {}
    expect, started = "", False  # the hash the next chained row must link to; whether a chained row has been seen
    before = rows = 0
    first = last = None
    prior: dict[str, Any] | None = None  # the last row that matched
    days: dict[str, dict[str, Any]] = {}
    found: dict[str, dict[str, Any]] = {}  # the rows an anchor names, where they are
    problem: dict[str, Any] | None = None
    looking: str | None = None  # after a broken link: the hash the row should have followed, to say whether it comes later (out of order) or is gone
    for path in events.files(base):
        with open(path, "rb") as f:
            for n, raw in enumerate(f, 1):
                text = raw.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                    if not isinstance(row, dict):
                        raise ValueError("not a row")
                except ValueError:
                    if problem is None:
                        if raw.endswith(b"\n"):
                            problem = _problem("unreadable", {"file": path.name, "line": n}, after=prior)
                        else:  # the file's last line has no end: the write that made it was cut off
                            problem = _problem("cut_short", prior, cut=path.name)
                    continue
                if looking is not None:  # only the question of where the missing row went is left
                    if row.get("hash") == looking:
                        problem["kind"] = "moved"
                        break
                    continue
                day = str(row.get("at") or "")[:10]  # the row's own time is the firm's (events.month_file cuts it the same way)
                d = days.setdefault(day, {"rows": 0, "hash": None, "at": None})
                d["rows"] += 1
                here = _row(row.get("at"), row.get("who"), path, n)
                if problem is not None:
                    continue
                h = row.get("hash")
                if not isinstance(h, str):
                    if started:
                        problem = _problem("changed", here, why="no hash after the chain began")
                    else:
                        before += 1
                    continue
                if row.get("kind") == events.REDACTED:  # a purged case's row: its hash stands for content that is gone; its purge must account for it
                    if str(row.get("purge") or "") not in recorded:
                        problem = _problem("blanked", here)
                        continue
                    blanked.setdefault(str(row["purge"]), []).append(h)
                elif events.row_hash(row) != h:
                    problem = _problem("changed", here)
                    continue
                if row.get("prev") != expect:
                    problem = _problem("removed", here)
                    looking = row.get("prev") if isinstance(row.get("prev"), str) else ""
                    continue
                started, expect = True, h
                rows += 1
                first = first or here["at"]
                last = here["at"]
                prior = here
                d["hash"], d["at"] = h, here["at"]
                if h in sealed:
                    found[h] = here
        if looking is not None and problem and problem["kind"] == "moved":
            break
    if problem is None:
        problem = _anchor_problem(anchors, days, found, prior, last)
    if problem is None:
        for pid, hashes in blanked.items():
            if len(hashes) != recorded[pid]["rows"] or events.redaction_digest(hashes) != recorded[pid]["digest"]:
                problem = _problem("blanked_count", None, found=len(hashes), recorded=recorded[pid]["rows"])
                break
    nblank = sum(len(h) for h in blanked.values())
    out: dict[str, Any] = {"ok": problem is None, "rows": rows, "before": before, "first": first, "last": last, "anchors": len(anchors), "problem": problem,
                           "blanked": nblank}
    out["line"] = ((_intact_line(rows, before, first, last) + (f"; {nblank} rows of purged cases blanked, as their purges recorded" if nblank else ""))
                   if problem is None else "does not match: " + words(problem))
    if problem is None:
        out["days"] = days
    return out


def read_redactions(base: str | Path) -> dict[str, dict[str, Any]]:
    """{purge id: {"rows", "digest"}} from ledger_redactions.jsonl (a line that is not one is passed over: the rows it would account for are then "blanked")."""
    path = events.redactions_path(base)
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get("purge"), str) and r["purge"] and isinstance(r.get("rows"), int) and isinstance(r.get("digest"), str):
            out[r["purge"]] = {"rows": r["rows"], "digest": r["digest"]}
    return out


def _intact_line(rows: int, before: int, first: Any, last: Any) -> str:
    older = f" ({before} rows before the chain began)" if before else ""
    if not rows:
        return "intact: no rows in the chain yet" + older
    return f"intact: {rows} rows from {_mdy(first)} to {_mdy(last)}{older}"


def _anchor_problem(anchors: list[dict[str, Any]], days: dict[str, dict[str, Any]], found: dict[str, dict[str, Any]], prior: dict[str, Any] | None,
                    last: Any) -> dict[str, Any] | None:
    """The first anchor the rows do not agree with: its day's last row is gone (the ledger ends before it: cut short; else removed or changed), the day has fewer
    rows than the seal counted, or more."""
    for a in anchors:
        d = days.get(a["day"]) or {"rows": 0, "hash": None}
        if a["hash"] not in found:
            end, sealed_at = clock.parse(last), clock.parse(a.get("at"))
            if end is None or (sealed_at is not None and end < sealed_at):  # the ledger ends before the row the seal names
                return _problem("cut_short", prior, cut=(prior or {}).get("file") or "the ledger", day=a["day"])
            return _problem("gone", None, day=a["day"], at=a.get("at"))
        where = found[a["hash"]]
        if d["rows"] < a["rows"]:
            return _problem("day_short", where, day=a["day"], sealed=a["rows"], found=d["rows"])
        if d["rows"] > a["rows"] or d["hash"] != a["hash"]:
            return _problem("day_more", where, day=a["day"], sealed=a["rows"], found=d["rows"])
    return None


# -- the daily anchor ---------------------------------------------------------------------------------------------------


def new_anchors(base: str | Path, days: dict[str, dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """The anchors for the complete days (before today) that have none yet and have a chained row: [{"day", "at", "hash", "rows"}], oldest first."""
    have = {a["day"] for a in read_anchors(base)}
    return [{"day": day, "at": d["at"], "hash": d["hash"], "rows": d["rows"]} for day, d in sorted(days.items())
            if d["hash"] and day not in have and (clock.local_date(day) or today) < today]


def write_anchors(base: str | Path, anchors: list[dict[str, Any]]) -> None:
    if not anchors:
        return
    path = anchors_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(a, ensure_ascii=False, separators=(",", ":")) + "\n" for a in anchors).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def seal_text(anchor: dict[str, Any]) -> str:
    """"The record's seal for 10/02/2026: 3fa9c10b7d2e4a65, 123 rows that day." (the first 16 characters of the hash: what the attorney writes down)"""
    n = anchor["rows"]
    return f"The record's seal for {_mdy(anchor['day'])}: {anchor['hash'][:SEAL_CHARS]}, {n} row{'s' if n != 1 else ''} that day."


def _keep(base: str | Path, result: dict[str, Any]) -> dict[str, Any]:
    """The result of this check, kept for the Settings line: when, whether, how many rows, the sentence. Written whole and moved into place."""
    kept = {"at": clock.stamp(), "ok": result["ok"], "rows": result["rows"], "before": result["before"], "line": result["line"]}
    path = check_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (json.dumps(kept, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)
    return kept


def nightly(base: str | Path, today: date | None = None) -> str:
    """What the overnight run does: checks the whole ledger and keeps the result; seals each complete day that has no seal (only when the record matches: a seal
    never vouches for a record that does not); and says both. Returns the morning report's lines. Never raises."""
    try:
        today = today or clock.today()
        result = verify(base)
        _keep(base, result)
        if not result["ok"]:
            return f"The record does not match what was written: the first row that does not match: {result['line'].split(': ', 1)[-1]} No new seal is written until it is looked at."
        write_anchors(base, new_anchors(base, result["days"], today))
        anchors = read_anchors(base)
        seal = seal_text(anchors[-1]) if anchors else "The record's seal: no complete day to seal yet."
        return f"The record is intact: {result['rows']} rows" + (f" from {_mdy(result['first'])} to {_mdy(result['last'])}" if result["rows"] else "") + f". {seal}"
    except Exception as exc:  # noqa: BLE001 -- a line missing from one morning's report is not worth a failed morning
        return f"The record's check could not run ({type(exc).__name__}); it runs again tomorrow night."


def kept(base: str | Path) -> dict[str, Any] | None:
    """The result of the last check, or None when there has been none (or the file is not readable)."""
    try:
        data = json.loads(check_path(base).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "ok" in data else None


def settings_line(base: str | Path) -> dict[str, Any]:
    """The line under Settings: {"text", "ok" (True, False, or None before the first check), "at"}. Reads the kept result; it never walks the ledger."""
    data = kept(base)
    if data is None:
        return {"text": "The record has not been checked yet: the overnight run checks it every night.", "ok": None, "at": None}
    if data["ok"]:
        return {"text": f"The record is intact as of {_when(data['at'])} ({data.get('rows', 0)} rows)", "ok": True, "at": data["at"]}
    return {"text": str(data.get("line") or "The record does not match."), "ok": False, "at": data["at"]}
