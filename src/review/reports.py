"""Reports: counts and lists about the firm's cases and documents, never about money or time
(docs/design_plan.md Part 2). The page (index.html "Reports") shows each table and offers it as a CSV file.

    stages           cases at each stage, with who they are
    filings          each kind of filing: cases that filed it, cases with its packet built but not filed yet
    offices          cases by office (src/offices.py)
    reviewers        cases by reviewer of record (review/expiring.py: whoever made the latest decision), and decisions made
    audit            the boxes the office changes: the same box changed the same way across cases, with the cases the reader may open (src/audit_fill.py)
    wordings         the firm's approved wordings per answer on the form: cases each was used on, times picked, share edited (src/wordings.py)
    document_types   documents by type, from the firm-wide index (src/index.py)
    document_quality documents by scan quality
    overnight        what last night's run did (data/batch_log.jsonl), and what it could not read
    cases            every case in one list: stage, office, reviewer, filings, open items, last activity
    client_reminders reminders sent to clients about appointments (src/client_reminders.py), counts only
    client_feedback  how clients said each step went, counts per step and per office (src/client_case.py); client_feedback_words has their sentences

Everything is counted from what the review app already holds; nothing is estimated. Dates are MM/DD/YYYY, names
come from the taxonomy and the cases, never ids. A restricted case the reader may not open (src/restricted.py) is in no
table and no count: the review app passes only the cases they may see, and the scope that leaves the others out of the
document counts and last night's problems. A confidential document (8 U.S.C. 1367, 8 CFR 208.6) in a case they may see is
counted for an attorney and the staff named on the case, as the Search page lists it (src/index.py).
"""

from __future__ import annotations

import csv
import io
import json

import clock
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .expiring import reviewer_of_record
from .overview import STAGE_NAMES, STAGES
from .state import _read, load_decisions

# the Documents tab's own words (index.html QUALITY_WORDS)
QUALITY_NAMES = {"readable": "Clear", "blurry": "Hard to read", "cut_off": "Cut off", "partial": "Part missing", "check": "Needs a check", "unknown": "Not checked"}
NO_FILING, NOBODY = "Nothing filed yet", "Nobody yet"


_zone: ContextVar = ContextVar("reports_zone", default=None)  # the firm's zone, looked up once for a whole report (each lookup asks the settings file's date: 1,800 cases, 5,000 stamps)


def _local(value: str | None):
    zone = _zone.get()
    if zone is None:
        return clock.local(value)
    at = clock.parse(value)
    return at.astimezone(zone) if at else None


def _us(value: str | None, time: bool = False) -> str:
    """MM/DD/YYYY (with the time of day when asked) on the office's own clock; "" for nothing."""
    if not value:
        return ""
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-":  # a plain date is that date, never shifted (src/clock.py)
        plain = clock.local_date(text)
        return plain.strftime("%m/%d/%Y") if plain else text
    when = _local(value)  # a stamp as the office's clock reads it
    if when is None:
        return str(value)
    return when.strftime("%m/%d/%Y %I:%M %p" if time else "%m/%d/%Y")


def _name(row: dict[str, Any]) -> str:
    return (row.get("summary") or {}).get("name") or row["id"]


def _filing_title(filing: str, title: str | None = None) -> str:
    """The filing as the packet names it ("I-765 (work permit)"), never its id."""
    if title:
        return title
    try:
        import packet

        return packet.load_filing(filing)["title"]
    except Exception:  # noqa: BLE001 -- a filing this version doesn't know: said plainly, never as an id
        return "Another filing"


def _filed(client_dir: Path) -> dict[str, str]:
    """filing id -> its title, for each mailing or online filing recorded on the case (status.json, src/prefile.py)."""
    status = _read(client_dir / "status.json", {}) or {}
    return {r["filing"]: _filing_title(r["filing"], r.get("title")) for r in status.get("filings") or [] if isinstance(r, dict) and r.get("filing")}


def _built(client_dir: Path) -> dict[str, str]:
    """filing id -> its title, for each packet built on the case (the file src/packet.py wrote for it)."""
    import packet

    return {f: _filing_title(f) for f, name in packet.FILINGS.items() if (client_dir / name).exists()}


def _group(rows: list[dict[str, Any]], key) -> list[tuple[str, list[dict[str, Any]]]]:
    """(group, its cases) by key(row), A to Z."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(key(row), []).append(row)
    return sorted(groups.items(), key=lambda g: g[0].casefold())


def _clients(cases: list[dict[str, Any]]) -> list[str]:
    return sorted({c["name"] for c in cases}, key=str.casefold)


def _table(id_: str, title: str, about: str, columns: list[tuple[str, str]], rows: list[dict[str, Any]], note: str | None = None) -> dict[str, Any]:
    return {"id": id_, "title": title, "about": about, "note": note, "rows": rows,
            "columns": [{"key": k, "label": label, "list": k == "clients"} for k, label in columns]}  # a "clients" column is a list of names


def _documents(data_root: Path, role: str | None, scope: dict[str, Any] | None = None) -> tuple[list[tuple[str, int, int, str]], list[tuple[str, int]], int]:
    """(type's name, documents, cases), (quality, documents), and the confidential documents the count leaves out, from the
    firm-wide index (src/index.py). scope (src/restricted.scope): the hidden cases are not counted at all, confidential
    documents only in the cases listed (None: every case); without one, a paralegal counts no confidential document."""
    import index
    import query

    if scope is None:
        scope = {"hidden": set(), "confidential": set() if role == "paralegal" else None}
    # from the query layer (src/query.py), brought up to date now: not the Search page's once-a-minute pace, a report counts what is there now
    counted = query.document_counts(data_root, scope["hidden"], scope["confidential"], permitted=scope.get("permitted"))
    if counted is None:
        return [], [], 0
    types, qualities, hidden = counted
    return [(index.type_name(t), n, c, index.type_short(t)) for t, n, c in types], qualities, hidden  # the short name too, for the column on the screen (the CSV keeps the long one)


def _overnight(data_root: Path, names: dict[str, str], hidden=frozenset(), may_open=None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """What the last run did (the line it wrote to batch_log.jsonl) and each case it could not process (not a hidden one)."""
    log = data_root.parent / "batch_log.jsonl"
    last: dict[str, Any] = {}
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                last = json.loads(line) or last
            except ValueError:
                continue
    if not last:
        return {}, []
    state = _read(data_root.parent / "batch_state.json", {}) or {}
    problems = [{"client": names.get(c, c), "at": _us(s.get("at"), True), "problem": str(s.get("error") or "could not be processed")[:300]}
                for c, s in sorted(state.items()) if s.get("status") not in (None, "done") and (s.get("at") or "") >= (last.get("started_at") or "")
                and c not in hidden and (may_open is None or may_open(c))]
    return last, problems


def build(rows: list[dict[str, Any]], data_root: Path, *, role: str | None = None, scope: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every table (the firm's zone read once for the whole report)."""
    token = _zone.set(clock.zone())
    try:
        return _build(rows, data_root, role=role, scope=scope)
    finally:
        _zone.reset(token)


def _build(rows: list[dict[str, Any]], data_root: Path, *, role: str | None = None, scope: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every table. rows: the all-clients rows (review/overview.py) with each case's office (the review app adds it), the
    cases the reader may see. scope: src/restricted.scope for the reader (None: the role decides, as before)."""
    import query

    data_root = Path(data_root)
    known = query.reviewers(data_root) or {}  # every case's reviewer of record and decisions in force, in one query (src/query.py) instead of one file each
    cases = []
    for row in rows:
        d = data_root / row["id"]
        # the review app's roster (review/roster.py) already holds whether the case has a file and what it filed and built: no look at the folder for each of 1,800
        if not row["_has_case"] if "_has_case" in row else not (d / "fact_graph.json").exists():
            continue  # invited through the portal, no case file yet: nothing to report on
        who, n = known.get(row["id"]) or (reviewer_of_record(d), len(load_decisions(d)))
        cases.append({"id": row["id"], "name": _name(row), "stage": STAGE_NAMES.get(row.get("stage"), ""), "stage_id": row.get("stage"), "ended": bool(row.get("end")),
                      "office": row.get("office") or "No office", "reviewer": who or NOBODY, "decisions": n,
                      "open_items": row.get("open_items") or 0, "filed": row["_filed"] if "_filed" in row else _filed(d),
                      "feedback": row["_feedback"] if "_feedback" in row else None, "sent": row["_reminders"] if "_reminders" in row else None,  # (the lists' copy holds them: no file read each)
                      "built": row["_built"] if "_built" in row else _built(d), "last_activity": row.get("last_activity")})
    tables = [_table("stages", "Cases by stage", "Where each case is in the firm's work, from the portal invitation to filing.",
                     [("stage", "Stage"), ("cases", "Cases"), ("clients", "Clients")],
                     [{"stage": STAGE_NAMES[s], "cases": n, "clients": _clients(c)} for s in STAGES for c in [[x for x in cases if x["stage_id"] == s and not x["ended"]]]
                      for n in [len(c)]]
                     # a case closed, declined, withdrawn or transferred (src/engagement.py) is in no stage of the work: counted on a row of its own
                     + [{"stage": "Ended (closed, declined, withdrawn or transferred)", "cases": len(c), "clients": _clients(c)} for c in [[x for x in cases if x["ended"]]]])]
    filed_by: dict[str, list[dict[str, Any]]] = {}
    ready_by: dict[str, list[dict[str, Any]]] = {}
    for c in cases:
        for t in c["filed"].values():
            filed_by.setdefault(t, []).append(c)
        for f, t in c["built"].items():
            if f not in c["filed"]:
                ready_by.setdefault(t, []).append(c)
    tables.append(_table("filings", "Cases by filing", "Each kind of filing recorded on a case, and the cases with its packet built but not filed yet.",
                         [("filing", "Filing"), ("filed", "Filed"), ("ready", "Packet built, not filed"), ("clients", "Clients who filed it")],
                         [{"filing": t, "filed": len(filed_by.get(t, [])), "ready": len(ready_by.get(t, [])), "clients": _clients(filed_by.get(t, []))}
                          for t in sorted(set(filed_by) | set(ready_by), key=str.casefold)]))
    tables.append(_table("offices", "Cases by office", "The office each case is filed from.", [("office", "Office"), ("cases", "Cases"), ("clients", "Clients")],
                         [{"office": g, "cases": len(c), "clients": _clients(c)} for g, c in _group(cases, lambda c: c["office"])]))
    reviewers = _group(cases, lambda c: c["reviewer"])
    reviewers = [x for x in reviewers if x[0] != NOBODY] + [x for x in reviewers if x[0] == NOBODY]
    tables.append(_table("reviewers", "Cases by reviewer", "The reviewer of record is whoever made the case's latest review decision. Decisions are counted, never timed.",
                         [("reviewer", "Reviewer"), ("cases", "Cases"), ("decisions", "Decisions made"), ("clients", "Clients")],
                         [{"reviewer": g, "cases": len(c), "decisions": sum(x["decisions"] for x in c), "clients": _clients(c)} for g, c in reviewers]))
    audit_table, alerts = _audit(data_root, cases, (scope or {}).get("hidden") or frozenset())
    tables.append(audit_table)
    tables.append(_wordings(data_root, (scope or {}).get("hidden") or frozenset(), (scope or {}).get("may_open")))
    types, qualities, hidden = _documents(data_root, role, scope)
    note = f"{hidden} documents in protected cases are counted for an attorney only." if hidden else None
    tables.append(_table("document_types", "Documents by type", "Every document the firm holds, by what it is.", [("type", "Document"), ("documents", "Documents"), ("cases", "Cases")],
                         [{"type": n, "documents": d, "cases": c} | ({"short": s} if s != n else {}) for n, d, c, s in types], note))
    tables.append(_table("document_quality", "Documents by scan quality", "How readable each scan is.", [("quality", "Quality"), ("documents", "Documents")],
                         [{"quality": QUALITY_NAMES.get(q, q.replace("_", " ").capitalize()), "documents": n} for q, n in qualities], note))
    last, problems = _overnight(data_root, {c["id"]: c["name"] for c in cases}, (scope or {}).get("hidden") or frozenset(), (scope or {}).get("may_open"))
    ran = [{"item": "Started", "value": _us(last.get("started_at"), True)}, {"item": "Finished", "value": _us(last.get("finished_at"), True)},
           {"item": "Cases processed", "value": str(last.get("done", 0))}, {"item": "Cases that could not be processed", "value": str(last.get("failed", 0))},
           {"item": "Cases not reached (they run next time)", "value": str(last.get("left", 0))}] + (
        [{"item": "Stopped because", "value": str(last["stopped"])}] if last.get("stopped") else []) if last else []
    tables.append(_table("overnight", "Last night's run", "What the overnight run did: the cases it processed, and the ones it could not read." if last
                         else "The overnight run has not recorded a run yet.", [("item", "What"), ("value", "Result")], ran))
    if problems:
        tables.append(_table("overnight_problems", "Cases the last run could not process", "They run again the next night.",
                             [("client", "Client"), ("at", "When"), ("problem", "What went wrong")], problems))
    tables.append(_table("cases", "Every case", "One line per case.",
                         [("name", "Client"), ("stage", "Stage"), ("office", "Office"), ("reviewer", "Reviewer"), ("filings", "Filed"), ("open_items", "Open review items"),
                          ("last_activity", "Last activity")],
                         [{"name": c["name"], "stage": c["stage"], "office": c["office"], "reviewer": c["reviewer"], "filings": "; ".join(sorted(c["filed"].values())) or NO_FILING,
                           "open_items": c["open_items"], "last_activity": _us(c["last_activity"])} for c in sorted(cases, key=lambda c: c["name"].casefold())]))
    tables += _client_tables(cases, data_root)
    # the clients on All clients with no case file yet (invited, still answering): not counted here, and the page says how many
    return {"tables": tables, "cases": len(cases), "without_case": len(rows) - len(cases), "generated": _us(clock.stamp(), True), "alerts": alerts}


def _audit(data_root: Path, cases: list[dict[str, Any]], hidden) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Boxes the office changes (src/audit_fill.py): the same box changed the same way across cases, counted from the filled forms the office corrected or filled by
    hand and from the reviewers' Saves; built from the cases the reader may open only: a protected case they may not open is in no row, count or line. No value of any
    client is here: the values are inside each case. alerts: a line for each box changed the same way on three or more cases."""
    import audit_fill

    ran = audit_fill.read(data_root)
    found = audit_fill.for_reader(ran, {c["id"]: c["name"] for c in cases}, hidden)
    rows = [{"id": g["id"], "form": g["form"], "box": g["box"], "change": g["change"], "cases": g["cases"], "seen": g["seen"], "clients": g["clients"]} for g in found]
    about = ("Boxes a person changed from what the product filled in, counted across every case. A box is counted when the same change is made on a "
             f"form the office filled by hand or corrected, or saved on a review card. {audit_fill.THRESHOLD} or more cases changed the same way is a line at the top "
             "of this page: a rule may be missing. Nothing here changes a form by itself; a person decides." if ran
             else "Not counted yet. The overnight run counts what the reviewers' Saves change; the firm's IT person runs the comparison with the filled forms once a month.")
    note = ("Only the cases you may open are counted here. No client's value is shown here: "
            "the boxes and their values are inside each case.")
    return (_table("audit", "Boxes the office changes", about, [("form", "Form"), ("box", "Box"), ("change", "The office's change"), ("cases", "Cases"),
                                                              ("seen", "Where it was seen"), ("clients", "Clients")], rows, note),
            [{"id": g["id"], "text": g["line"]} for g in found if g["line"]])


def _wordings(data_root: Path, hidden=frozenset(), may_open=None) -> dict[str, Any]:
    """Firm wordings (src/wordings.py, brief L3): per answer on the form, the wordings the office approved, how many cases each was used on, and the share of the
    times a paralegal picked it that they then edited it (a high share says the wording is wrong). Counts and the wording's own slots only: no case is named
    here, and no value of any client is in a wording."""
    import wordings

    rows = wordings.report_rows(wordings.root(data_root), lambda case: case not in hidden and (may_open is None or may_open(case)))
    about = ("The wordings the firm's attorneys approved for Part 14 explanations, learned from what was approved on cases and from past filings. Each case counts once. "
             "Edited is the share of the times a paralegal picked the wording and then changed it before the attorney approved: a high share says the wording is wrong."
             if rows else "No wording is kept yet. Every explanation an attorney approves on a case is kept here, with its slots, for the next case with the same facts.")
    return _table("wordings", "Firm wordings", about, [("item", "Answer"), ("wording", "Wording, with its slots"), ("voice", "Voice"), ("status", "Status"), ("approved", "Approved by"),
                                                      ("cases", "Cases"), ("picked", "Times picked"), ("edited", "Edited after picking")], rows,
                  "Counts only, of the cases you may open: a restricted case you may not open is in no count. A wording holds blanks in place of the facts of a case.")


def _client_tables(cases: list[dict[str, Any]], data_root: Path) -> list[dict[str, Any]]:
    """What the client side of the product did (brief I3), of the cases the reader may open only (the rows passed in): the reminders sent to clients about their
    appointments, how clients said each step went (counts per step and per office: no case is named in a count), and the sentences clients wrote (named, for the
    people who may open the case: a hidden case is in no row at all). Nothing here is used for anything automatic."""
    import client_case
    import client_reminders

    tables = [_table("client_reminders", "Reminders sent to clients", "The week before and the day before a fingerprint appointment, an interview or a hearing, for appointments in "
                     "the last 60 days and the weeks ahead. A protected case is never texted or e-mailed: the office reaches that client by hand.",
                     [("reminder", "Reminder"), ("total", "Appointments"), ("sent", "Sent"), ("queued", "Waiting in the outbox (no provider)"), ("hand", "Left to the office by hand"),
                      ("held", "Held"), ("failed", "Could not be sent"), ("none", "No channel the client agreed to")],
                     client_reminders.summary(cases, data_root))]
    counts: dict[tuple[str, str], dict[str, int]] = {}
    written = []
    for c in cases:
        for r in (c["feedback"] if c.get("feedback") is not None else client_case.read_feedback(data_root / c["id"])):
            step, face = client_case.step_name(str(r.get("step"))), r.get("face") if r.get("face") in client_case.FACES else None
            if face is None:
                continue
            for office in ("Every office", c["office"]):
                counts.setdefault((step, office), {"good": 0, "ok": 0, "bad": 0})[face] += 1
            if r.get("comment"):
                written.append({"at": r.get("at") or "", "client": c["name"], "step": step, "face": client_case.FACE_NAMES[face], "comment": str(r["comment"])})
    order = list(client_case.STEP_NAMES.values()) + ["Another step"]
    rows = [{"step": step, "office": office, "good": n["good"], "ok": n["ok"], "bad": n["bad"], "answers": sum(n.values())}
            for (step, office), n in sorted(counts.items(), key=lambda x: (order.index(x[0][0]), x[0][1] != "Every office", x[0][1].casefold()))]
    tables.append(_table("client_feedback", "How clients said each step went", "One tap after a step: three faces. Counted per step, for every office and for each office. "
                         "Counts only: no case is named here.", [("step", "Step"), ("office", "Office"), ("good", "Good"), ("ok", "Okay"), ("bad", "Not good"), ("answers", "Answers")], rows))
    written.sort(key=lambda w: clock.key(w["at"]), reverse=True)
    tables.append(_table("client_feedback_words", "What clients wrote", "The optional sentence, in the client's own words and language, for the cases you may open.",
                         [("when", "When"), ("client", "Client"), ("step", "Step"), ("face", "Face"), ("comment", "What they wrote")],
                         [{"when": _us(w["at"]), "client": w["client"], "step": w["step"], "face": w["face"], "comment": w["comment"]} for w in written]))
    return tables


def _cell(value: Any) -> str:
    """One CSV cell. A name or a document type starting with = + - @ would run as a formula in a spreadsheet: a leading ' makes it text."""
    text = "; ".join(map(str, value)) if isinstance(value, list) else "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_csv(table: dict[str, Any]) -> str:
    """The table as a CSV file (UTF-8 with a byte-order mark, so a spreadsheet reads Portuguese names right)."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow([_cell(c["label"]) for c in table["columns"]])
    for row in table["rows"]:
        w.writerow([_cell(row.get(c["key"])) for c in table["columns"]])
    return "﻿" + out.getvalue()
