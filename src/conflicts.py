"""The conflict search: before a client is added (on the screen, by the Docketwise import, by the Clio sync), every person the new case names is
looked for among every person on every other case (the people index, src/people.py, in the query layer), and what the person running it decided
is recorded with who and when.

    search(...)        runs one search and logs it (data/conflict_checks.jsonl and the event ledger); returns the whole record
    present(...)       what one person may see of a search: a hit on a case they may not open is "a hit on a case you cannot open: ask an
                       attorney" and nothing else (no name, date, number, score, role or case id: restricted.visible_to, the same gate as every list)
    for_new_client     the decision recorded on a client added on the screen (src/review/front_desk.py add_client refuses to add without one)
    hold_new           the importer's and the sync's "not yet decided": the case is held out of invitations until an attorney decides
    decide             an attorney's decision on a case waiting for one (or a change of decision), on the screen
    note               the decision on a search by hand (Settings, Conflict search)
    held               True while a case waits for an attorney (or the client was declined): no invitation goes (src/portal/notify.py)
    listing, csv       Settings, "Conflict checks": every search and decision, 50 a page, or all of them as a CSV file

What a hit means is the attorney's call. The product never says "conflict" of a hit: it says "hit", gives the facts (the score, the sentence that
explains it, the case, the role the person has there, whether the other side), and records what a person decided. The words of the choices are
DRAFT for the attorney (docs/attorney_review.md, "The conflict search").

Where it is kept:
    data/conflict_checks.jsonl              every search and every decision, appended (I485_CONFLICTS points elsewhere; the tests do). It holds the names
                                            searched for and the hits in full (case ids included): Settings shows it to attorneys only
    data/clients/<case id>/conflict_check.json   the new case's own record: the client and the other side as the intake named them, the searches
                                            made for it, the decision and every earlier one
"""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Callable

import clock
import events
import name_match
import people

FILE = people.FILE
LOG = "conflict_checks.jsonl"
VERSION = 1
MAX_PARTIES = 5
MAX_HITS = 50  # per person searched for and per reader: the strongest of the hits they may see; the screen says when there were more
KEEP = 2000  # hits kept in the log per person searched for (the strongest), so a common name cannot make a row of megabytes
PURPOSE_WORDS = {"add": "for a new client", "hand": "by hand", "import": "for the Docketwise import", "sync": "for the Clio sync",
                 "cli": "for the command line's import",
                 "case": "again for a case waiting for a decision"}  # the ledger's words (the product's names keep their capitals)

# The choices, in the words the screen shows (DRAFT: docs/attorney_review.md). "undecided" is what the importer and the sync record, and what a
# person adding a client records when a hit is for an attorney to weigh: the client is held out of invitations until an attorney decides.
DECISIONS = {"none": "No conflict", "declined": "Conflict: the client is declined", "waived": "Conflict: an attorney waived it",
             "undecided": "Not yet decided: an attorney decides"}
WAIVER = ("Why the attorney waived it: the record keeps the reason with the attorney's name and the date. The wording of a waiver, and whether the client's "
          "informed consent in writing is needed, are the attorney's to decide.")
DECLINED = ("Not added: the client is declined because of a conflict. The decision is kept under Settings, Conflict checks. What the firm tells the person is "
            "the attorney's to decide.")
HIDDEN = "A hit on a case you cannot open: ask an attorney."
HELD = "The conflict check is waiting for an attorney's decision: nothing goes to this client, and no sign-in link is made, until then (Settings, Conflict checks)."
DECLINED_HELD = "The client was declined after the conflict search: nothing goes to them, and no sign-in link is made (Settings, Conflict checks)."
PURPOSES = {"add": "Adding a client", "hand": "A search by hand", "import": "The Docketwise import", "sync": "The Clio sync", "cli": "The command line's import",
            "case": "A search again for a case waiting for a decision"}
PARTY_ROLES = {"adverse": "The other side in a court case", "abuser": "The person who abused the client", "trafficker": "The trafficker",
               "petitioner": "A relative who petitions for the client", "spouse": "The spouse", "parent": "A parent", "other": "Someone else involved"}


class ConflictProblem(ValueError):
    """Something the person must change, said in words."""


# -- where ------------------------------------------------------------------------------------------------------------


def log_path(clients_root: str | Path) -> Path:
    """I485_CONFLICTS, else conflict_checks.jsonl in the data folder (next to the case folders)."""
    env = os.environ.get("I485_CONFLICTS")
    return Path(env) if env else Path(clients_root).resolve().parent / LOG


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)  # one write of one whole line, owner-only: it holds the names searched for
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def rows(clients_root: str | Path) -> list[dict[str, Any]]:
    """Every row of the log, oldest first (a line that is not JSON is skipped)."""
    path = log_path(clients_root)
    out = []
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    return out


def _find(clients_root: str | Path, search_id: str) -> dict[str, Any] | None:
    return next((r for r in reversed(rows(clients_root)) if r.get("kind") == "search" and r.get("id") == search_id), None)


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# -- what is searched for ---------------------------------------------------------------------------------------------------


def _date(text: Any, what: str) -> str | None:
    text = str(text or "").strip()
    if not text:
        return None
    d = name_match.parse_date(text)
    if d is None or not (1900 <= d.year <= clock.today().year):
        raise ConflictProblem(f"Write {what} as MM/DD/YYYY.")
    return d.isoformat()


def clean(body: dict[str, Any]) -> dict[str, Any]:
    """What the screen (or an import) sends, checked: name, other names, date of birth, A-Number, passport number, and the other side the
    intake names (up to five people: name, date of birth, role)."""
    name = " ".join(str(body.get("name") or "").split())[:120]
    others = body.get("other_names") or []
    if isinstance(others, str):
        others = re.split(r"\s*[;\n]\s*", others)
    others = [" ".join(str(x).split())[:120] for x in others if str(x).strip()][:5]
    dob = _date(body.get("dob"), "the date of birth")
    a_raw, p_raw = str(body.get("a_number") or "").strip(), str(body.get("passport") or "").strip()
    a_num = name_match.a_number(a_raw)
    if a_raw and not a_num:
        raise ConflictProblem("That A-Number doesn't look right: it has seven to nine digits, with or without the A.")
    passport = name_match.passport(p_raw)
    if p_raw and not passport:
        raise ConflictProblem("That passport number doesn't look right: it has at least six letters and digits.")
    parties = []
    for p in (body.get("parties") or [])[:MAX_PARTIES]:
        if not isinstance(p, dict) or not str(p.get("name") or "").strip():
            continue
        role = str(p.get("role") or "other")
        parties.append({"name": " ".join(str(p["name"]).split())[:120], "dob": _date(p.get("dob"), "the other person's date of birth"),
                        "role": role if role in PARTY_ROLES else "other"})
    if not (name_match.Name.parse(full=name) or a_num or passport):
        raise ConflictProblem("Enter the person's full name, or an A-Number or passport number, to search for.")
    return {"name": name, "other_names": others, "dob": dob, "a_number": a_num, "passport": passport, "parties": parties}


def _subjects(q: dict[str, Any]) -> list[tuple[str, name_match.Person]]:
    """(who, the person as the matcher reads them): the client first, then each person of the other side."""
    names = [n for n in (name_match.Name.parse(full=x) for x in [q.get("name"), *(q.get("other_names") or [])]) if n]
    born = [d for d in [name_match.parse_date(q.get("dob"))] if d]
    out = [("client", name_match.Person(names, born, {q["a_number"]} if q.get("a_number") else set(), {q["passport"]} if q.get("passport") else set()))]
    for n, p in enumerate(q.get("parties") or [], start=1):
        name = name_match.Name.parse(full=p.get("name"))
        if name:
            out.append((f"party_{n}", name_match.Person([name], [d for d in [name_match.parse_date(p.get("dob"))] if d])))
    return out


# -- the search -----------------------------------------------------------------------------------------------------------------


def _index(clients_root: Path, refresh: bool, db_path: str | Path | None) -> list[dict[str, Any]]:
    import query

    if refresh:
        try:
            # the query layer's throttled refresh (at most once a minute: a pass over 2,000 case folders on a Windows disk takes many seconds). A client
            # added on the screen, imported or synced is put in the index the moment its record is written (record_new), and a case whose records
            # change in the review app is rebuilt at once, so the minute only concerns changes made outside the app.
            query.refresh(clients_root, db_path)
        except Exception as exc:  # noqa: BLE001 -- the index as it is still answers; said on the console
            import sys

            sys.stderr.write(f"people index not refreshed: {type(exc).__name__}\n")
    path = Path(db_path) if db_path else query.default_path(clients_root)
    db = query.open_read(path)
    if db is None:
        query.rebuild_changed(clients_root, db_path)
        db = query.open_read(path)
    if db is None:
        raise ConflictProblem("The people index could not be read, so the search could not run: ask your IT (Keeping current, the query layer).")
    try:
        out = []
        for r in db.execute("select * from people"):
            row = dict(r)
            for col in ("names", "birth_dates", "a_numbers", "passports", "countries", "documents"):
                row[col] = json.loads(row[col] or "[]")
            out.append(row)
        return out
    finally:
        db.close()


def search(clients_root: str | Path, body: dict[str, Any], *, by: str, role: str | None, purpose: str, case: str | None = None,
           exclude: str | None = None, refresh: bool = True, log: bool = True, db_path: str | Path | None = None, via: str | None = None) -> dict[str, Any]:
    """Runs the search for every person the body names (clean()) against every person on every case but `exclude` (the new case itself), and
    (log) appends it, with every hit in full, to the conflict log and a row to the event ledger. Returns the record: id, at, by, role, purpose, case,
    query, hits [{for, case, person, role, relationship, adverse, restricted, score, strength, sentence, names, birth_dates, client}], more."""
    clients_root = Path(clients_root)
    if purpose not in PURPOSES:
        raise ValueError(f"unknown purpose {purpose!r}")
    q = clean(body)
    index = _index(clients_root, refresh, db_path)
    clients = {}
    for row in index:
        if row["person"] == "applicant" and row["names"]:
            clients[row["case_id"]] = row["names"][0]["name"]
    hits: list[dict[str, Any]] = []
    matched: dict[str, list[str]] = {}  # every case a person searched for matches by name or number alone, the dates of birth left out entirely
    processed: dict[str, bool] = {}
    for who, subject in _subjects(q):
        found, cases = [], set()
        undated = name_match.Person(subject.names, [], subject.a_numbers, subject.passports)
        for row in index:
            if exclude and row["case_id"] == exclude:
                continue
            them = people.as_person(row)
            # presence by name or number alone: a date of birth never makes or unmakes it (what a person who may not open the case is told
            # must not change with the date typed: else repeating a search with dates would tell the date)
            if name_match.score(undated, name_match.Person(them.names, [], them.a_numbers, them.passports)) is not None:
                cases.add(row["case_id"])
            s = name_match.score(subject, them)
            if s is None:
                continue
            if row["case_id"] not in processed:
                processed[row["case_id"]] = (clients_root / row["case_id"] / "fact_graph.json").exists()
            found.append({"for": who, "case": row["case_id"], "person": row["person"], "role": row["role"], "relationship": row["relationship"],
                          "adverse": row["role"] in people.ADVERSE, "restricted": bool(row["restricted"]), "score": s.score, "strength": s.strength,
                          "sentence": s.sentence, "names": _spellings(row["names"]), "birth_dates": [d["value"] for d in row["birth_dates"]][:3],
                          "client": clients.get(row["case_id"], ""), "processed": processed[row["case_id"]]})
        found.sort(key=lambda h: (-h["score"], not h["adverse"], h["case"], h["person"]))
        hits += found[:KEEP]  # every hit is kept (to a bound): the cut to MAX_HITS is made per reader, among the hits they may see (present)
        matched[who] = sorted(cases)
    # a folder the review app's start-up sweep marked abandoned (an add that stopped part way: no client has it) is no hit. The search itself never
    # marks one: it may run outside the review app (an import, a sync) against another portal, where a real client would look like an orphan
    gone = set()
    for c in {h["case"] for h in hits} | {c for cs in matched.values() for c in cs}:
        d = clients_root / c
        if (d / FILE).exists() and abandoned(d):
            gone.add(c)
    if gone:
        hits = [h for h in hits if h["case"] not in gone]
        matched = {who: [c for c in cs if c not in gone] for who, cs in matched.items()}
    record = {"kind": "search", "id": secrets.token_hex(8), "at": clock.stamp(), "by": by, "role": role, "purpose": purpose, "case": case, "query": q,
              "hits": hits, "matched": matched}
    if log:
        _append(log_path(clients_root), record)
        n = len(hits)
        events.record("conflicts", "searched", f"Ran the conflict search {PURPOSE_WORDS[purpose]}: " + (f"{n} hit{'s' if n != 1 else ''}" if n else "no hits"),
                      case=case, home=clients_root.resolve().parent, who=by, via=via)
    return record


def _spellings(names: list[dict[str, Any]], limit: int = 4) -> list[str]:
    """Up to four of a person's names as the case writes them, one per spelling (capitals and accents aside: "ANA CLARA" and "Ana Clara" are one)."""
    out: dict[str, str] = {}
    for n in names:
        out.setdefault(" ".join(name_match.words(n.get("name"))), n.get("name") or "")
    return [x for x in out.values() if x][:limit]


def hidden_for(record: dict[str, Any], may_see: Callable[[str], bool] | None) -> set[str]:
    """The people searched for ("client", "party_1" ...) who match, by name or number alone, someone on a case this person may not open."""
    if may_see is None:
        return set()
    matched = record.get("matched")
    if matched is None:  # a record from before the date-free presence was kept: its hits' cases
        matched = {}
        for h in record.get("hits") or []:
            matched.setdefault(h["for"], []).append(h["case"])
    return {who for who, cases in matched.items() if any(not may_see(c) for c in cases)}


def present(record: dict[str, Any], may_see: Callable[[str], bool] | None = None) -> dict[str, Any]:
    """What one person sees of a search: every hit on a case they may open in full, the strongest MAX_HITS per person searched for; and, for each
    person searched for who matches anyone on a case they may not open (may_see False: the restricted.visible_to gate), exactly one line, HIDDEN, and
    nothing else. That line comes from the name or number alone, never from a date of birth, and is one line however many such cases matched, so
    neither repeating a search with other dates nor counting lines tells anything. may_see None: everyone sees everything (no staff accounts, or an
    attorney)."""
    q = record.get("query") or {}
    # nothing typed is sent back: the screen has it, and the answer carries no name at all
    labels = {"client": "The person searched for" if record.get("purpose") == "hand" else "The client"}
    for n, p in enumerate(q.get("parties") or [], start=1):
        labels[f"party_{n}"] = f"The other side, person {n}: {PARTY_ROLES.get(p.get('role'), 'Someone else involved').lower()}"
    groups: dict[str, dict[str, Any]] = {k: {"for": k, "label": v, "hits": []} for k, v in labels.items()}
    more = 0
    for h in record.get("hits") or []:
        g = groups.setdefault(h["for"], {"for": h["for"], "label": h["for"], "hits": []})
        if may_see is not None and not may_see(h["case"]):
            continue
        if len(g["hits"]) >= MAX_HITS:
            more += 1
            continue
        g["hits"].append({"case": h["case"], "client": h.get("client") or "", "person": h["relationship"], "open": bool(h.get("processed")),
                          "role": people.ROLES.get(h["role"], h["role"]), "adverse": h["adverse"], "restricted": h["restricted"],
                          "score": h["score"], "strength": h["strength"], "sentence": h["sentence"], "names": h["names"],
                          "birth_dates": [_us(d) for d in h["birth_dates"]]})
    for who in hidden_for(record, may_see):
        groups.setdefault(who, {"for": who, "label": who, "hits": []})["hits"].append({"hidden": True, "text": HIDDEN})
    shown = [g for g in groups.values()]
    flat = [h for g in shown for h in g["hits"]]
    return {"id": record["id"], "at": record["at"], "by": record.get("by"), "people": shown, "hits": len(flat),
            "strong": sum(1 for h in flat if h.get("strength") == "strong"), "adverse": sum(1 for h in flat if h.get("adverse")),
            "hidden": sum(1 for h in flat if h.get("hidden")), "more": more}


def _us(iso: str | None) -> str:
    d = name_match.parse_date(iso)
    return f"{d:%m/%d/%Y}" if d else ""


# -- decisions --------------------------------------------------------------------------------------------------------------------


def _check(decision: str, reason: str, role: str | None, record: dict[str, Any] | None, may_see: Callable[[str], bool] | None, *, allow_undecided: bool) -> str:
    if decision not in DECISIONS or (decision == "undecided" and not allow_undecided):
        raise ConflictProblem("Choose what you decided about the conflict search.")
    reason = " ".join(str(reason or "").split())[:600]
    if decision == "waived":
        if role == "paralegal":
            raise PermissionError("Only an attorney waives a conflict. Choose Not yet decided: an attorney decides, and the client is held until then.")
        if not reason:
            raise ConflictProblem("Say why the conflict was waived: the record keeps the reason with your name.")
    if decision == "none" and record is not None and may_see is not None and hidden_for(record, may_see):
        raise PermissionError("A hit is on a case you cannot open, so an attorney decides. Choose Not yet decided: an attorney decides.")
    return reason


def _decision(decision: str, reason: str, by: str, role: str | None, search_id: str | None) -> dict[str, Any]:
    return {"decision": decision, "words": DECISIONS[decision], "reason": reason or None, "by": by, "role": role, "at": clock.stamp(), "search": search_id}


def _log_decision(clients_root: Path, d: dict[str, Any], case: str | None, purpose: str, via: str | None = None) -> None:
    _append(log_path(clients_root), {"kind": "decision", "id": secrets.token_hex(8), "case": case, "purpose": purpose} | d)
    events.record("conflicts", "decided", f"Recorded the conflict check: {d['words']}", case=case, home=clients_root.resolve().parent, who=d["by"], via=via)


def for_new_client(clients_root: str | Path, conflict: dict[str, Any] | None, name: str, *, by: str, role: str | None,
                   may_see: Callable[[str], bool] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Add a client: the decision the person recorded on the search they ran for this name (conflict: {search, decision, reason}). Returns (the
    search record, the decision). Without a search, the search runs now and is accepted only when it found nothing (the person pressed Add on a name
    that has no hit). Raises in words when no decision was recorded or the search was for another name."""
    clients_root = Path(clients_root)
    conflict = conflict if isinstance(conflict, dict) else {}
    decision = str(conflict.get("decision") or "")
    if not decision:
        raise ConflictProblem("Run the conflict search and record what you decided before adding the client.")
    if conflict.get("search"):
        record = _find(clients_root, str(conflict["search"]))
        if record is None:
            raise ConflictProblem("That conflict search was not found: search again.")
        searched = name_match.words((record.get("query") or {}).get("name"))
        if searched != name_match.words(name):
            raise ConflictProblem("The conflict search was for another name: search again for this client.")
    else:
        record = search(clients_root, {"name": name} | {k: conflict.get(k) for k in ("dob", "a_number", "passport", "other_names", "parties") if conflict.get(k)},
                        by=by, role=role, purpose="add")
        if record["hits"]:
            raise ConflictProblem("The conflict search found hits for this client: look at them and record what you decided.")
    reason = _check(decision, str(conflict.get("reason") or ""), role, record, may_see, allow_undecided=True)
    return record, _decision(decision, reason, by, role, record["id"])


def _write_case(case_dir: Path, record: dict[str, Any], d: dict[str, Any]) -> None:
    q = record.get("query") or {}
    data = _read(case_dir / FILE) if (case_dir / FILE).exists() else None
    data = data if isinstance(data, dict) else {"version": VERSION, "subject": {}, "parties": [], "searches": [], "decision": None, "history": []}
    data["subject"] = {k: q.get(k) for k in ("name", "other_names", "dob", "a_number", "passport")}
    data["parties"] = list(q.get("parties") or [])
    data["searches"].append({"id": record["id"], "at": record["at"], "by": record["by"], "purpose": record["purpose"], "hits": len(record.get("hits") or []),
                             "strong": sum(1 for h in record.get("hits") or [] if h["strength"] == "strong"),
                             "adverse": sum(1 for h in record.get("hits") or [] if h["adverse"])})
    data["decision"] = d
    data["history"].append(d)
    if data.pop("abandoned", None):  # an add that stopped part way, taken up again under the same id: the record goes on
        data.setdefault("resumed", []).append(clock.stamp())
    case_dir.mkdir(parents=True, exist_ok=True)
    _write(case_dir / FILE, data)


def record_new(case_dir: str | Path, record: dict[str, Any], d: dict[str, Any], *, via: str | None = None, index: bool = True) -> None:
    """The new case's own record (conflict_check.json, written before anything else of the case), the log's decision row and the ledger's; then the
    case is put in the people index at once (index), so the next search finds this client."""
    case_dir = Path(case_dir)
    _write_case(case_dir, record, d)
    _log_decision(case_dir.parent, d, case_dir.name, record["purpose"], via)
    if index:
        _reindex(case_dir)


def _reindex(case_dir: Path) -> None:
    try:
        import query

        query.rebuild(case_dir)
    except Exception as exc:  # noqa: BLE001 -- the nightly run indexes it; said on the console
        import sys

        sys.stderr.write(f"people index: new case not indexed now ({type(exc).__name__})\n")


def hold_new(cases_root: str | Path, case_id: str, body: dict[str, Any], *, by: str, purpose: str, via: str, refresh: bool = True,
             dry_run: bool = False, db_path: str | Path | None = None) -> dict[str, Any]:
    """The importer's and the sync's search for a new case: run, logged, and recorded on the case as "not yet decided" (so it is held out of
    invitations until an attorney decides on the screen). dry_run: the search only, nothing written. Returns the search record."""
    cases_root = Path(cases_root)
    record = search(cases_root, body, by=by, role=None, purpose=purpose, case=case_id, exclude=case_id, refresh=refresh, log=not dry_run,
                    db_path=db_path, via=via)
    if not dry_run:
        record_new(cases_root / case_id, record, _decision("undecided", "", by, None, record["id"]), via=via)
    return record


def decide(case_dir: str | Path, decision: str, reason: str, *, by: str, role: str | None) -> dict[str, Any]:
    """An attorney decides on the screen for a case (one waiting since an import or a sync, or a decision to change). Recorded on the case,
    in the log and the ledger; the case's invitations follow (held())."""
    if role == "paralegal":
        raise PermissionError("What a hit means is the attorney's call: an attorney records the decision.")
    case_dir = Path(case_dir)
    data = _read(case_dir / FILE)
    if not isinstance(data, dict):
        raise ConflictProblem("This case has no conflict check yet: run the search for it first.")
    last = (data.get("searches") or [{}])[-1].get("id")
    reason = _check(decision, reason, role, None, None, allow_undecided=False)
    d = _decision(decision, reason, by, role, last)
    data["decision"] = d
    data.setdefault("history", []).append(d)
    _write(case_dir / FILE, data)
    _log_decision(case_dir.parent, d, case_dir.name, "case")
    _reindex(case_dir)
    return state(case_dir)


def search_again(case_dir: str | Path, *, by: str, role: str | None) -> dict[str, Any]:
    """The search run again for a case (Settings, the cases waiting for a decision): the same people, against everyone now on the other cases."""
    if role == "paralegal":
        raise PermissionError("What a hit means is the attorney's call: an attorney runs the search again.")
    case_dir = Path(case_dir)
    data = _read(case_dir / FILE)
    if not isinstance(data, dict):
        raise ConflictProblem("This case has no conflict check yet.")
    body = dict(data.get("subject") or {}) | {"parties": data.get("parties") or []}
    record = search(case_dir.parent, body, by=by, role=role, purpose="case", case=case_dir.name, exclude=case_dir.name)
    data.setdefault("searches", []).append({"id": record["id"], "at": record["at"], "by": by, "purpose": "case", "hits": len(record["hits"]),
                                            "strong": sum(1 for h in record["hits"] if h["strength"] == "strong"),
                                            "adverse": sum(1 for h in record["hits"] if h["adverse"])})
    _write(case_dir / FILE, data)
    return record


def note(clients_root: str | Path, search_id: str, decision: str, reason: str, *, by: str, role: str | None) -> dict[str, Any]:
    """The decision on a search by hand (Settings, Conflict search): the attorney's."""
    if role == "paralegal":
        raise PermissionError("The search by hand is the attorney's.")
    clients_root = Path(clients_root)
    record = _find(clients_root, search_id)
    if record is None:
        raise ConflictProblem("That conflict search was not found: search again.")
    reason = _check(decision, reason, role, None, None, allow_undecided=False)
    d = _decision(decision, reason, by, role, search_id)
    _log_decision(clients_root, d, record.get("case"), record.get("purpose") or "hand")
    return d


# -- what a case's record says --------------------------------------------------------------------------------------------------


def record_of(case_dir: str | Path) -> dict[str, Any] | None:
    data = _read(Path(case_dir) / FILE)
    return data if isinstance(data, dict) else None


def held(case_dir: str | Path) -> bool:
    """No invitation for this client while the conflict check waits for an attorney, or after the client was declined."""
    data = record_of(case_dir)
    return bool(data and (data.get("decision") or {}).get("decision") in ("undecided", "declined"))


ORPHAN_AGE = 3600  # seconds: an add that stopped between the conflict check and the portal's record is taken as abandoned after an hour


def orphan(case_dir: str | Path, age: float | None = None, portal: str | Path | None = None) -> bool:
    """A folder left by an add that stopped part way (on the screen or the command line's import): it holds the conflict check and nothing of a case
    (perhaps the restriction record), and the caller's own portal (portal: its data folder, the one the add writes to) has no client by its name.
    No portal: False, always: a caller that cannot see the portal the client was added to can never tell a real client from an orphan. age: only
    when the check is at least that many seconds old."""
    import time

    if portal is None:
        return False
    case_dir = Path(case_dir)
    path = case_dir / FILE
    try:
        names = {p.name for p in case_dir.iterdir()}
        stamp = path.stat().st_mtime
    except OSError:
        return False
    if not names or names - {FILE, "access.json", "confidentiality.json"} or (Path(portal) / "clients" / case_dir.name / "profile.json").exists():
        return False
    data = record_of(case_dir) or {}
    if not {s.get("purpose") for s in data.get("searches") or []} & {"add", "cli"}:
        return False  # the Docketwise import and the Clio sync keep a case folder before its portal client by design
    return age is None or time.time() - stamp >= age


def abandoned(case_dir: str | Path) -> bool:
    return bool((record_of(case_dir) or {}).get("abandoned"))


def sweep(clients_root: str | Path, portal: str | Path) -> list[str]:
    """Every orphan older than an hour marked abandoned (its record says so, with when): out of the people index, out of the lists; the next add of
    the same name reuses its id (front_desk.new_client_id). Only the review app runs it, when it starts, with its own portal (portal): nothing else
    ever marks a case abandoned (an import or a sync may run against another portal, where a real client would look like an orphan)."""
    out = []
    root = Path(clients_root)
    for d in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        if (d / FILE).exists() and not abandoned(d) and orphan(d, ORPHAN_AGE, portal):
            _abandon(d)
            out.append(d.name)
    return out


def _abandon(case_dir: Path) -> None:
    data = record_of(case_dir) or {}
    data["abandoned"] = {"at": clock.stamp(), "why": "The add stopped before the client was made: no client has this record."}
    _write(case_dir / FILE, data)
    _reindex(case_dir)


def hold_words(case_dir: str | Path) -> str:
    """Why nothing goes to this client, in words: waiting for an attorney, or declined ("" when neither)."""
    decision = ((record_of(case_dir) or {}).get("decision") or {}).get("decision")
    return HELD if decision == "undecided" else DECLINED_HELD if decision == "declined" else ""


def state(case_dir: str | Path) -> dict[str, Any]:
    """The case's conflict check in words: the decision now, who and when, and the searches made (counts only)."""
    data = record_of(case_dir) or {}
    d = data.get("decision") or {}
    return {"decision": d.get("decision"), "words": d.get("words"), "by": d.get("by"), "at": d.get("at"), "reason": d.get("reason"),
            "held": held(case_dir), "searches": data.get("searches") or [], "history": data.get("history") or []}


def waiting(clients_root: str | Path, may_see: Callable[[str], bool] | None = None, portal: str | Path | None = None) -> list[dict[str, Any]]:
    """The cases whose conflict check waits for an attorney's decision, oldest first, each with what its last search found (as this person may see it).
    portal: the review app's own portal (an orphan older than an hour is left out; none: nothing is taken for an orphan)."""
    clients_root = Path(clients_root)
    found = []
    for d in sorted(p for p in clients_root.iterdir() if p.is_dir()) if clients_root.is_dir() else []:
        data = record_of(d) if (d / FILE).exists() else None
        if not data or data.get("abandoned") or (data.get("decision") or {}).get("decision") != "undecided" or orphan(d, ORPHAN_AGE, portal):
            continue
        if may_see is not None and not may_see(d.name):
            continue
        last = (data.get("searches") or [{}])[-1].get("id")
        record = _find(clients_root, last) if last else None
        found.append({"case": d.name, "name": (data.get("subject") or {}).get("name") or d.name, "since": (data.get("decision") or {}).get("at"),
                      "by": (data.get("decision") or {}).get("by"), "search": present(record, may_see) if record else None})
    return sorted(found, key=lambda x: clock.key(x["since"]))


# -- Settings, "Conflict checks" ----------------------------------------------------------------------------------------------------


KINDS = {"search": "Searches", "decision": "Decisions"}


def _line(r: dict[str, Any]) -> dict[str, Any]:
    q = r.get("query") or {}
    hits = r.get("hits") or []
    if r.get("kind") == "search":
        searched = "; ".join(x for x in [q.get("name"), *(q.get("other_names") or []), *(f"{p.get('name')} ({PARTY_ROLES.get(p.get('role'), '')})"
                                                                                       for p in q.get("parties") or [])] if x)
        numbers = ", ".join(x for x in [f"A-Number {q['a_number']}" if q.get("a_number") else "", f"passport {q['passport']}" if q.get("passport") else ""] if x)
        what = (f"{len(hits)} hit{'s' if len(hits) != 1 else ''}" if hits else "No hits") + (
            f", {sum(1 for h in hits if h['strength'] == 'strong')} strong" if hits else "") + (
            f", {sum(1 for h in hits if h['adverse'])} on the other side of a case" if any(h["adverse"] for h in hits) else "")
        return {"at": r.get("at"), "when": _when(r.get("at")), "who": r.get("by") or "", "kind": "search", "kind_label": "Search", "why": PURPOSES.get(r.get("purpose"), ""),
                "searched": searched, "dob": _us(q.get("dob")), "numbers": numbers, "what": what, "case": r.get("case"),
                "hits": [{"case": h["case"], "client": h.get("client") or h["case"], "role": people.ROLES.get(h["role"], h["role"]), "strength": h["strength"],
                          "sentence": h["sentence"], "restricted": h["restricted"], "adverse": h["adverse"]} for h in hits]}
    return {"at": r.get("at"), "when": _when(r.get("at")), "who": r.get("by") or "", "kind": "decision", "kind_label": "Decision", "why": PURPOSES.get(r.get("purpose"), ""),
            "searched": "", "dob": "", "numbers": "", "what": r.get("words") or DECISIONS.get(r.get("decision"), ""), "reason": r.get("reason") or "",
            "case": r.get("case"), "hits": []}


def _named_line(clients_root: str | Path, r: dict[str, Any]) -> dict[str, Any]:
    """A row for the screen: the case named by its client's name (as the case's conflict check names them), never by its id."""
    line = _line(r)
    case = r.get("case")
    line["client"] = ((record_of(Path(clients_root) / case) or {}).get("subject") or {}).get("name") or "" if case else ""
    return line


def _when(stamp: Any) -> str:
    from review.oversight import us_when

    return us_when(stamp)


def _select(clients_root: str | Path, q: dict[str, Any]) -> list[dict[str, Any]]:
    from review.oversight import _span

    start, end = _span(str(q.get("from") or ""), str(q.get("to") or ""))
    person, kind = str(q.get("person") or ""), str(q.get("kind") or "")
    out = []
    for r in reversed(rows(clients_root)):
        if kind and r.get("kind") != kind or person and (r.get("by") or "") != person:
            continue
        at = clock.parse(r.get("at"))
        moment = at.timestamp() if at else None
        if start is not None and (moment is None or moment < start) or end is not None and (moment is None or moment >= end):
            continue
        out.append(r)
    return out


def listing(clients_root: str | Path, q: dict[str, Any], page: int = 1) -> dict[str, Any]:
    """One page (50 rows, newest first) of every search and decision, with the people and kinds to filter by, and the cases waiting."""
    from review.oversight import PAGE, pages

    chosen = _select(clients_root, q)
    page, count = pages(len(chosen), page)
    everyone = sorted({r.get("by") or "" for r in rows(clients_root)} - {""})
    return {"rows": [_named_line(clients_root, r) for r in chosen[(page - 1) * PAGE: page * PAGE]], "page": page, "pages": count, "per": PAGE, "total": len(chosen),
            "people": [{"email": p, "name": p} for p in everyone], "kinds": [{"id": k, "label": v} for k, v in KINDS.items()]}


def csv(clients_root: str | Path, q: dict[str, Any]) -> bytes:
    """Every search and decision the filters match, as a CSV file (each cell that starts with = + - @ is made text: review/reports._cell)."""
    from review.oversight import csv_file

    lines = []
    for r in _select(clients_root, q):
        x = _line(r)
        x["hit_list"] = [f"{h['client']}, case {h['case']} ({h['role']}, {h['strength']}{', restricted' if h['restricted'] else ''}): {h['sentence']}"
                         for h in x["hits"]]
        lines.append(x)
    return csv_file([("when", "When"), ("who", "Who"), ("kind_label", "Kind"), ("why", "Why"), ("searched", "Searched for"), ("dob", "Date of birth"),
                     ("numbers", "Numbers"), ("what", "What it found or what was decided"), ("reason", "Reason"), ("case", "Case"), ("hit_list", "Hits")], lines)


def counts(record: dict[str, Any], cases_root: str | Path) -> dict[str, int]:
    """A search's counts for someone who may open no restricted case (the command line's reader: the import report): the hits on cases that are
    not restricted (hits, strong, adverse), and hidden: 1 when anyone matched on a restricted case, which only an attorney sees on the screen."""
    import restricted

    shown = present(record, lambda c: not restricted.is_restricted(Path(cases_root) / c))
    flat = [h for g in shown["people"] for h in g["hits"] if not h.get("hidden")]
    return {"hits": len(flat), "strong": shown["strong"], "adverse": shown["adverse"], "hidden": 1 if shown["hidden"] else 0}
