"""The query layer: the firm's records as read-only SQLite tables, for the firm's own tools and for the product's lists.

One SQLite file (data/query.db; I485_QUERY_DB points elsewhere), built the way the search index is (src/index.py): from the files the product
already keeps, never from the scans, and never the source of truth. Delete it and the next question or the nightly run builds it again.
The firm's IT person reads it with any SQLite tool, without opening a case's files and without our software:

    sqlite3 -readonly data/query.db "select stage, count(*) from cases group by stage"

    cases        one row per case: its track and stage, its office, whether it is restricted, and counts
    documents    one row per document record: whose it is, its type, language, scan quality, dates, the roles it fills
    facts        one row per fact in the case's fact graph: its key, its tier, whether it has a value, how many documents support it
    decisions    one row per step of every review decision: who, when, the action, the fact keys it was about
    deadlines    one row per deadline on a case's timeline
    filings      one row per filing recorded as mailed
    events       the event ledger (src/events.py), copied in: who changed what on which case, and when
    people       one row per person a case names (src/people.py): every spelling of their name, dates of birth, numbers, their role on the case

What it holds of a person's data, said exactly. The people table is the exception, on purpose: it is the people index the conflict search reads
(src/conflicts.py), and holds the names, dates of birth, A-Numbers, passport numbers and countries the case's records carry for each person, with
where each came from. It is the only place in this file a person's own data is; the screens read it only through the conflict search, which
shows a hit on a case to someone who may not open that case as nothing but "a hit on a case you cannot open". For every other table, NOT copied: any fact's value, a name, a date of birth, an address, a number read from a document (A-Number,
receipt, passport, tracking), a note, a deadline's text, or a document's text; a receipt or passport number inside a fact key, a decision's item id or a
deadline's id is replaced by #1, #2 ... (one counter for the case); a deadline at the client's age has no date, because it would be the date of birth plus years.
What IS in it: the case id (the client's folder name, which the front desk and the importers make from the client's name, so it can be a name), counts,
the kind of document and whose it is (applicant, spouse, child ...), the dates of documents (issued, expires), of deadlines (due) and of filings
(mailed_on), the staff who decided, and the ledger's sentences (which name no value). So it is as protected as the case folders.

Who may see which rows is the product's to say, as it is for every list: each row carries its case id, and the screens that read this file
leave out the cases a person may not open (src/restricted.py scope; review/server.py may_open). The file is the firm's own: it is readable by
whoever can read the data folder, as index.db is, and its permission is 0600 (owner only), like the other files the installer protects.

    rebuild(client_dir)           replace one case's rows (the nightly run and process_clients call it; so does refresh() for a case whose files changed)
    rebuild_changed(clients_root) only the cases whose files changed since their rows were built, then the ledger's new rows
    rebuild_all(clients_root)     from scratch
    refresh(clients_root)         rebuild_changed, at most once in `every` seconds (60, or I485_QUERY_REFRESH: a screen asks before it reads; a pass walks every case
                                  folder, which on a network or Windows disk with 2,000 cases takes many seconds, so a page never asks for it twice a minute)
    open_read(path)               the file opened read-only (None when it does not exist yet): what the product's screens use

The tables are in SCHEMA below; each column's sentence is its comment (docs/data_dictionary.md is made from them). SCHEMA_VERSION is raised when a
table changes: an old file is dropped and built again (it is only a copy).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import clock
import events
import keepup

REPO = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 2  # 2: the people table (the conflict search's index)
SOURCES = ("documents.json", "fact_graph.json", "decisions.json", "status.json", "office.json", "access.json", "meta.json", "journey_summary.json",
           "conflict_check.json")
BATCH = 50  # cases per transaction when many are built at once: one commit each would be one disk flush each

# What each table is (the data dictionary's sentence); the columns' are the comments in SCHEMA.
TABLES = {
    "cases": "One row per case: the case's track and stage, its office, whether it is restricted, and counts.",
    "documents": "One row per document record of a case: whose it is, its type, language, scan quality, dates and the roles it fills.",
    "facts": "One row per fact in a case's fact graph: its key, its tier, whether it has a value and how many documents support it. Never the value.",
    "decisions": "One row per step of every review decision: who decided, when, the action, and which facts it was about. Never the value or the note.",
    "deadlines": "One row per deadline on a case's timeline.",
    "filings": "One row per filing recorded as mailed on a case: which filing, when, how and by whom. Never the tracking or receipt number.",
    "events": "The event ledger (data/events-YYYY-MM.jsonl) copied in: one row per change to a record, who made it and when.",
    "ledger_files": "How far into each month's ledger file the events table has been read.",
    "meta": "When the file was built and for which schema version.",
    "people": ("One row per person a case names (the client, the petitioner, the parents, the spouse, the children, the abuser on a VAWA case, the other side "
               "in a court case, the sponsor, the relatives a petition brings), with every spelling of their name, their dates of birth and numbers as the case's "
               "records carry them, and where each came from. The people index the conflict search reads: the one table here that holds a person's own data."),
}
# The columns that hold a person's own data (the data dictionary marks them): the people table's.
PERSON_COLUMNS = {("people", c) for c in ("names", "birth_dates", "a_numbers", "passports", "countries", "documents")}

SCHEMA = """
create table if not exists meta (
  key text primary key,   -- schema_version or built
  value text              -- its value
);
create table if not exists cases (
  case_id text primary key,   -- the case's id: the client's folder name in the data folder
  track text,                 -- which path the case is on (sij, family, naturalization, asylum, caa, vawa, t_visa, u_visa, daca), from its timeline; null until the nightly run has worked the timeline out
  stage text,                 -- where the case stands on that path, as an id (null until its timeline is worked out)
  stage_name text,            -- the same stage in words
  office text,                -- the office's name (src/offices.py)
  office_id text,             -- the office's id
  restricted integer,         -- 1 when the case is restricted (by the law or by an attorney), else 0
  law text,                   -- why the law restricts it: 1367 (VAWA, T, U), 208.6 (asylum), unknown when it could not be worked out, or null
  documents integer,          -- how many document records the case has
  facts integer,              -- how many facts its fact graph holds
  facts_set integer,          -- how many of them have a value
  decisions integer,          -- how many review decisions are in force (not undone)
  reviewer text,              -- who made the latest decision in force: the case's reviewer of record
  last_decision text,         -- when that decision was made (with its offset)
  filings_mailed integer,     -- how many filings are recorded as mailed
  filed_at text,              -- when the case was marked filed (with its offset), or null
  deadlines integer,          -- how many deadlines its timeline has
  built text,                 -- when this row was built (with its offset)
  signature text              -- what the row was built from: a change in those files means the row is built again
);
create table if not exists documents (
  case_id text not null,      -- the case it belongs to
  doc_id text not null,       -- the document record's id (the first 16 hex of its file's sha256)
  type text,                  -- what kind of document it is, as an id from the taxonomy (schemas/registers/document_types.json)
  person text,                -- whose it is: applicant, spouse, petitioner, parent, child_1, child_2 ... or unknown
  person_basis text,          -- how that was decided: named, identifiers, only_person, set_by_person or unknown
  language text,              -- the language it is printed in: en, pt, es, fr, ht or unknown
  quality text,               -- how readable the scan is: readable, blurry, cut_off, partial, check or unknown
  issued text,                -- the date it was issued (YYYY-MM-DD), or null
  expires text,               -- the date it ends (YYYY-MM-DD), or null
  roles text,                 -- json list of what it can show in a filing (the type's roles, what its text shows, what a reviewer tagged)
  source text,                -- where it came from: portal, scan_inbox, drive, m365, filevine, docketwise, clio or folder
  confidential integer,       -- 1 when the document is confidential (8 U.S.C. 1367, 8 CFR 208.6), else 0
  files integer,              -- how many files hold it
  pages integer,              -- how many pages it has, when known
  added text,                 -- when it was added (with its offset), or null
  primary key (case_id, doc_id)
);
create index if not exists documents_type on documents(type);
create index if not exists documents_expires on documents(expires);
create table if not exists facts (
  case_id text not null,      -- the case it belongs to
  key text not null,          -- the fact's key, such as applicant.date_of_birth (never the value); a receipt, passport or other number inside a key is replaced by #1, #2 ...
  tier integer,               -- 1 read from a document, 2 derived by a rule, 3 given by a person
  status text,                -- resolved, conflict (documents disagree) or missing
  is_set integer,             -- 1 when the fact has a value, else 0
  sources integer,            -- how many documents support it
  primary key (case_id, key)
);
create table if not exists decisions (
  case_id text not null,      -- the case it belongs to
  item text not null,         -- the review item's id, with any number in it replaced by #1, #2 ...
  step integer not null,      -- 1 for the first decision on the item, 2 for the next after an undo, and so on
  kind text,                  -- what kind of item it was
  level text,                 -- how serious it was: blocking, review or info
  action text,                -- confirm, set, blank or acknowledge
  reviewer text,              -- who decided
  role text,                  -- their role: attorney or paralegal, when accounts are on
  at text,                    -- when (with its offset)
  undone integer,             -- 1 when the decision was later undone
  undone_by text,             -- who undid it
  undone_at text,             -- when it was undone (with its offset)
  fact_keys text,             -- json list of the fact keys the item was about (numbers in them replaced by #1, #2 ...)
  primary key (case_id, item, step)
);
create index if not exists decisions_reviewer on decisions(reviewer);
create table if not exists deadlines (
  case_id text not null,      -- the case it belongs to
  deadline text not null,     -- the deadline's kind as an id, with any receipt number or date in it replaced by #1, #2 ...
  due text,                   -- the date it falls (YYYY-MM-DD); null for a deadline at the client's age, which would be their date of birth plus years
  what text,                  -- the kind of deadline in words (never its own text, which holds addresses, names and numbers)
  owner text,                 -- who has to act: attorney, paralegal or client
  expiring integer,           -- 1 when it is a document that is about to expire
  primary key (case_id, deadline)
);
create index if not exists deadlines_due on deadlines(due);
create table if not exists filings (
  case_id text not null,      -- the case it belongs to
  n integer not null,         -- 1 for the first filing recorded on the case, 2 for the next
  filing text,                -- which filing, as an id (i485, i601a, ...)
  title text,                 -- its name as the packet gives it
  mailed_on text,             -- the date it was mailed or filed (YYYY-MM-DD)
  carrier text,               -- how: a carrier's name, Hand delivery or Online
  online integer,             -- 1 when it was filed in the USCIS online account
  by text,                    -- who recorded it
  at text,                    -- when it was recorded (with its offset)
  primary key (case_id, n)
);
create table if not exists events (
  id integer primary key,     -- the row's place in this table (not in the ledger)
  at text,                    -- when (with its offset)
  who text,                   -- who: a person's name, or the overnight run, the client, the importer, a connector
  role text,                  -- attorney, paralegal, client, system or staff
  via text,                   -- how: staff, overnight, portal, importer, connector, tool or system
  case_id text,               -- the case, or null for a change to the firm's own records
  kind text,                  -- which record changed (data_dictionary: the ledger's kinds)
  version integer,            -- the record's version after the write
  action text,                -- what was done, in one word
  what text,                  -- what changed, in a short sentence (never a person's data)
  file text                   -- the month's ledger file the row came from
);
create index if not exists events_case on events(case_id, at);
create index if not exists events_at on events(at);
create table if not exists ledger_files (
  file text primary key,      -- a month's ledger file (events-YYYY-MM.jsonl)
  bytes integer               -- how many bytes of it the events table has read
);
create table if not exists people (
  case_id text not null,      -- the case that names the person
  person text not null,       -- who the person is on the case, as a tag: applicant (the client), father, mother, spouse, former_spouse, child_1, petitioner, petitioner_parent_1, abuser, relative_1, court_parent, sponsor, party_1 (named at intake), person_1 (recorded on the case page)
  role text,                  -- the role on the case: client, petitioner, parent, spouse, former_spouse, child, relative, sponsor, beneficiary, abuser, trafficker, adverse (the other side in a court case) or other
  relationship text,          -- how they are related to the client, in words
  names text,                 -- json list of every spelling of the person's name the case carries, as written: name, given, family, and sources (from: the fact key in words; document: the kind of document or answer)
  birth_dates text,           -- json list of the person's dates of birth: value (YYYY-MM-DD), as written, and sources
  a_numbers text,             -- json list of the person's A-Numbers: value (nine digits), as written, and sources
  passports text,             -- json list of the person's passport numbers: value, as written, and sources
  countries text,             -- json list of the countries the case gives for the person (of birth or citizenship): value, as written, and sources
  documents text,             -- json list of the documents the Documents tab says are the person's: id and kind
  restricted integer,         -- 1 when the case is restricted (the conflict search shows a hit on it in full only to someone who may open it), else 0
  primary key (case_id, person)
);
"""

_ORDER = ("people", "events", "ledger_files", "filings", "deadlines", "decisions", "facts", "documents", "cases", "meta")


class QueryDamaged(Exception):
    """The file could not be read; it was set aside and the next refresh builds a new one."""


# -- the file ---------------------------------------------------------------------------------------------


def default_path(clients_root: str | Path) -> Path:
    """I485_QUERY_DB, else data/query.db next to the client folders."""
    env = os.environ.get("I485_QUERY_DB")
    return Path(env) if env else Path(clients_root).resolve().parent / "query.db"


def _corrupt(exc: Exception) -> bool:
    text = str(exc).lower()
    return isinstance(exc, sqlite3.DatabaseError) and any(w in text for w in ("malformed", "not a database", "corrupt", "disk image"))


def _reset(path: Path) -> None:
    for suffix in ("-journal", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    if path.exists():
        try:
            os.replace(path, Path(str(path) + ".damaged"))
        except OSError:
            path.unlink(missing_ok=True)


def connect(path: str | Path) -> sqlite3.Connection:
    """The file for writing, made (owner-only: 0600) if it is not there; one that can't be read is set aside and made new."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        db = None
        try:
            if not path.exists():
                os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
            db = sqlite3.connect(path, timeout=30)
            db.row_factory = sqlite3.Row
            db.execute("pragma journal_mode=delete")  # a rollback journal, not WAL: a reader opens the file read-only without writing beside it
            db.execute("pragma synchronous=normal")
            if db.execute("pragma user_version").fetchone()[0] not in (0, SCHEMA_VERSION):
                for name in _ORDER:
                    db.execute(f"drop table if exists {name}")
            db.executescript(SCHEMA)
            db.execute(f"pragma user_version = {SCHEMA_VERSION}")
            db.execute("select 1 from cases limit 1").fetchall()
            return db
        except sqlite3.DatabaseError as exc:
            if db is not None:
                db.close()
            if not _corrupt(exc):
                raise
            if attempt == 2:
                raise QueryDamaged(f"{type(exc).__name__}: {exc}") from exc
            _reset(path)
    raise QueryDamaged("unreachable")  # pragma: no cover


def open_read(path: str | Path) -> sqlite3.Connection | None:
    """The file opened read-only, rows as sqlite3.Row; None when it is not there or can't be read as ours (the caller reads the records instead)."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        if db.execute("pragma user_version").fetchone()[0] != SCHEMA_VERSION:
            db.close()
            return None
        db.execute("select 1 from cases limit 1").fetchall()
        return db
    except sqlite3.DatabaseError:
        return None


# -- reading a case ----------------------------------------------------------------------------------------


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _signature(client_dir: Path, stamp: str) -> str:
    parts = [stamp]
    for name in SOURCES:
        p = client_dir / name
        try:
            st = p.stat()
            parts.append(f"{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append("-")
    return "|".join(parts)


def _journey(client_dir: Path, fresh: bool) -> dict[str, Any]:
    """The timeline's summary (src/journey.py summary). fresh: worked out now if the case's cached one is out of date (review/overview.py journey_row: one case,
    when its records have just changed). Not fresh (a pass over every case): the cached one as the nightly run left it, whatever day it was made, and none for a
    case that has none yet, so a pass over two thousand cases never works out two thousand timelines."""
    try:
        if fresh:
            from review.overview import journey_row

            return journey_row(client_dir) or {}
        cached = _read(client_dir / "journey_summary.json")
        return (cached.get("row") if isinstance(cached, dict) else None) or {}
    except Exception:  # noqa: BLE001 -- a case whose timeline can't be worked out still has its other rows
        return {}


def _office(client_dir: Path) -> tuple[str, str]:
    try:
        import index

        _name, state = index._name_and_state(client_dir)
        return index._office(client_dir, state)
    except Exception:  # noqa: BLE001
        return "", ""


def _stamp() -> str:
    try:
        import index

        return index._office_stamp()
    except Exception:  # noqa: BLE001
        return ""


def _delete(db: sqlite3.Connection, case: str) -> None:
    for table in ("cases", "documents", "facts", "decisions", "deadlines", "filings", "people"):
        db.execute(f"delete from {table} where case_id = ?", (case,))


def _insert(db: sqlite3.Connection, client_dir: Path, stamp: str, now: str, fresh: bool = True) -> None:
    case = client_dir.name
    _delete(db, case)
    docs = _documents(client_dir)
    graph = _read(client_dir / "fact_graph.json")
    facts = graph.get("facts") if isinstance(graph, dict) and isinstance(graph.get("facts"), dict) else {}
    log = _read(client_dir / "decisions.json")
    log = log if isinstance(log, dict) else {}
    status = _read(client_dir / "status.json")
    status = status if isinstance(status, dict) else {}
    summary = _journey(client_dir, fresh) if (client_dir / "fact_graph.json").exists() else {}

    import restricted

    try:
        law, closed = restricted.law(client_dir), restricted.is_restricted(client_dir)
    except Exception:  # noqa: BLE001 -- a case whose record can't be read is restricted until it can be (the same rule as restricted.law)
        law, closed = restricted.UNKNOWN, True
    for r in docs:
        ids = r.get("doc_ids") or r.get("files") or []
        pages = r.get("pages") if isinstance(r.get("pages"), list) else []
        try:
            import documents

            roles = documents._with_roles(r)["roles"]
        except Exception:  # noqa: BLE001
            roles = list(r.get("roles") or [])
        db.execute("insert or replace into documents values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (case, str(r["id"]), r.get("type") or "", r.get("person") or "unknown", r.get("person_basis") or "unknown", r.get("language") or "unknown",
                    r.get("quality") or "unknown", r.get("issued") or None, r.get("expires") or None, json.dumps(roles), r.get("source") or None,
                    1 if (r.get("confidential") or law) else 0, len(r.get("files") or ids), len(pages) or None, r.get("added") or None))
    set_n = 0
    seen: dict[str, int] = {}  # one counter for the case: a receipt or passport number inside a key or an id is the same #n wherever it appears
    for key, f in sorted(facts.items()):
        if not isinstance(f, dict):
            continue
        is_set = f.get("status") == "resolved" and f.get("value") not in (None, "")
        set_n += 1 if is_set else 0
        db.execute("insert or replace into facts values (?,?,?,?,?,?)", (case, events.mask(key, seen), f.get("tier"), f.get("status"), 1 if is_set else 0, len(f.get("sources") or [])))
    in_force, latest = 0, None
    for item, entry in log.items():
        if not isinstance(entry, dict):
            continue
        meta = entry.get("item") or {}
        chain = entry.get("history") or [{k: v for k, v in entry.items() if k not in ("item", "history", "undone")} | ({"undone": entry["undone"]} if entry.get("undone") else {})]
        for n, step in enumerate(chain, start=1):
            undone = step.get("undone") or {}
            db.execute("insert or replace into decisions values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (case, events.mask(item, seen), n, meta.get("kind"), meta.get("level"), step.get("action"), step.get("reviewer"), step.get("role"), step.get("at"),
                        1 if undone else 0, undone.get("by"), undone.get("at"), json.dumps([events.mask(k, seen) for k in meta.get("facts") or []])))
        if not entry.get("undone"):
            in_force += 1
            if latest is None or clock.key(entry.get("at")) > clock.key(latest.get("at")):
                latest = entry
    deadlines = [d for d in summary.get("deadlines") or [] if isinstance(d, dict) and d.get("id")]
    for d in deadlines:
        kind = events.mask(str(d["id"]), seen)  # the id of a hearing, a move or a request from USCIS carries a date or a receipt number
        due = None if re.match(r"age_\d+", kind) else str(d.get("date") or "")[:10] or None  # a deadline at an age is the date of birth plus those years: only its kind is kept
        db.execute("insert or replace into deadlines values (?,?,?,?,?,?)", (case, kind, due, _kind_words(kind, d), d.get("owner"), 1 if d.get("expiry") else 0))
    filings = [f for f in status.get("filings") or [] if isinstance(f, dict)]
    for n, f in enumerate(filings, start=1):
        db.execute("insert or replace into filings values (?,?,?,?,?,?,?,?,?)",
                   (case, n, f.get("filing"), f.get("title"), f.get("mailed_on"), f.get("carrier"), 1 if f.get("online") else 0, f.get("by"), f.get("at")))
    _people(db, client_dir, facts, log, status, docs, 1 if closed else 0)
    office, office_id = _office(client_dir) if graph else ("", "")
    db.execute("insert into cases values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (case, summary.get("track"), summary.get("stage"), summary.get("stage_name"), office, office_id, 1 if closed else 0, law, len(docs), len(facts), set_n,
                in_force, (latest or {}).get("reviewer"), (latest or {}).get("at"), len(filings), status.get("filed_at"), len(deadlines), now,
                _signature(client_dir, stamp)))


def _people(db: sqlite3.Connection, client_dir: Path, facts: dict, log: dict, status: dict, docs: list[dict[str, Any]], closed: int) -> None:
    """The case's rows in the people index (src/people.py), from what _insert has read already."""
    import people

    try:
        found = people.rows(client_dir, facts=facts, log=log, status=status, docs=docs)
    except Exception as exc:  # noqa: BLE001 -- the case's other rows still count; the next change to its records tries again
        import sys

        sys.stderr.write(f"people index: case not indexed ({type(exc).__name__})\n")
        return
    for p in found:
        db.execute("insert or replace into people values (?,?,?,?,?,?,?,?,?,?,?)",
                   (client_dir.name, p["person"], p["role"], p["relationship"], json.dumps(p["names"], ensure_ascii=False), json.dumps(p["birth_dates"]),
                    json.dumps(p["a_numbers"]), json.dumps(p["passports"]), json.dumps(p["countries"], ensure_ascii=False),
                    json.dumps(p["documents"], ensure_ascii=False), closed))


def _documents(client_dir: Path) -> list[dict[str, Any]]:
    """The case's document records as the Documents tab shows them (src/documents.py load: a record for every classified document, and whose each is as the
    whole case says, infer_people), the same as the search index reads them, so the two agree; documents.json as written when that cannot be read."""
    try:
        import documents

        return [r for r in documents.load(client_dir)["documents"] if isinstance(r, dict) and r.get("id")]
    except Exception:  # noqa: BLE001 -- the records as written still count
        raw = _read(client_dir / "documents.json")
        return [r for r in (raw.get("documents") if isinstance(raw, dict) else None) or [] if isinstance(r, dict) and r.get("id")]


def _kind_words(kind: str, deadline: dict[str, Any]) -> str:
    """The deadline's kind in words, from its (masked) id: never its own text, which carries the court's address, the judge, receipt numbers and dates."""
    if deadline.get("expiry"):
        return "A document expires"
    if re.match(r"age_\d+", kind):
        return "A deadline tied to the client's age"
    parts = [w for part in kind.split(".") for w in part.split("_") if w and w.isalpha() and w.islower()]
    return ("A deadline: " + " ".join(parts)) if parts else "A deadline"


def _has_records(client_dir: Path) -> bool:
    # conflict_check.json: a client added on the screen, imported or synced holds the conflict check before anything else (src/conflicts.py), and
    # is in the people index from that moment, so the next search finds them
    return any((client_dir / n).exists() for n in ("documents.json", "fact_graph.json", "decisions.json", "status.json", "conflict_check.json"))


# -- building ----------------------------------------------------------------------------------------------


def rebuild(client_dir: str | Path, db_path: str | Path | None = None) -> bool:
    """Replaces one case's rows (client_dir is data/clients/<id>). True when the case has records; a case with none loses its rows. A damaged file is
    made new, holding just this case until the nightly run (or the next screen) fills it. Also reads the ledger's new rows."""
    client_dir = Path(client_dir)
    path = Path(db_path) if db_path else default_path(client_dir.parent)
    for attempt in (1, 2):
        try:
            with closing(connect(path)) as db:
                with db:
                    if _has_records(client_dir):
                        _insert(db, client_dir, _stamp(), clock.stamp())
                    else:
                        _delete(db, client_dir.name)
                _read_events(db, events.base_path(client_dir.parent.parent))
            return _has_records(client_dir)
        except sqlite3.DatabaseError as exc:
            if not _corrupt(exc) or attempt == 2:
                raise
            _reset(path)
    return False


def rebuild_changed(clients_root: str | Path, db_path: str | Path | None = None, *, force: bool = False) -> dict[str, int]:
    """Builds the cases whose files changed since their rows were built, drops the rows of a case folder that is gone, and reads the ledger's new
    rows (the nightly run; a screen asks through refresh()). force: every case."""
    clients_root = Path(clients_root)
    path = Path(db_path) if db_path else default_path(clients_root)
    for attempt in (1, 2):
        try:
            with closing(connect(path)) as db:
                out = _refresh(db, clients_root, force)
                out["events"] = _read_events(db, events.base_path(clients_root.resolve().parent))
                db.execute("insert or replace into meta values ('schema_version', ?), ('built', ?)", (str(SCHEMA_VERSION), clock.stamp()))
                db.commit()
                return out
        except sqlite3.DatabaseError as exc:
            if not _corrupt(exc) or attempt == 2:
                raise
            _reset(path)
    raise QueryDamaged("unreachable")  # pragma: no cover


def _refresh(db: sqlite3.Connection, clients_root: Path, force: bool) -> dict[str, int]:
    stamp, now = _stamp(), clock.stamp()
    known = {r["case_id"]: r["signature"] for r in db.execute("select case_id, signature from cases")}
    out = {"rebuilt": 0, "unchanged": 0, "skipped": 0, "removed": 0, "unreadable": 0}
    present: set[str] = set()
    pending = 0
    dirs = sorted(p for p in clients_root.iterdir() if p.is_dir()) if clients_root.is_dir() else []
    for d in dirs:
        present.add(d.name)
        if not _has_records(d):
            if d.name in known:
                _delete(db, d.name)
                out["removed"] += 1
            else:
                out["skipped"] += 1
            continue
        if not force and known.get(d.name) == _signature(d, stamp):
            out["unchanged"] += 1
            continue
        try:
            _insert(db, d, stamp, now, fresh=False)
            out["rebuilt"] += 1
        except sqlite3.DatabaseError:
            raise
        except Exception:  # noqa: BLE001 -- one odd record must not stop the other 1,799
            _delete(db, d.name)
            out["unreadable"] += 1
        pending += 1
        if pending >= BATCH:
            db.commit()
            pending = 0
    for gone in set(known) - present:  # a case folder that no longer exists
        _delete(db, gone)
        out["removed"] += 1
    db.commit()
    out["cases"] = db.execute("select count(*) from cases").fetchone()[0]
    return out


def rebuild_all(clients_root: str | Path, db_path: str | Path | None = None) -> dict[str, int]:
    """Every case from scratch."""
    return rebuild_changed(clients_root, db_path, force=True)


def _read_events(db: sqlite3.Connection, base: Path) -> int:
    """Copies the ledger's rows that are not in the events table yet (each month's file from where the last read stopped; a file that shrank or is
    gone is read again or dropped). Returns the rows added."""
    have = {r["file"]: r["bytes"] for r in db.execute("select file, bytes from ledger_files")}
    added = 0
    live = events.files(base)
    for gone in set(have) - {p.name for p in live}:
        db.execute("delete from events where file = ?", (gone,))
        db.execute("delete from ledger_files where file = ?", (gone,))
    for path in live:
        size = path.stat().st_size
        start = have.get(path.name, 0)
        if start > size:  # cut short or replaced: read it all again
            db.execute("delete from events where file = ?", (path.name,))
            start = 0
        if start == size:
            continue
        with open(path, "rb") as f:
            f.seek(start)
            at = start
            for raw in f:
                if not raw.endswith(b"\n"):
                    break  # a row still being written: the next read takes it whole
                at += len(raw)
                try:
                    r = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(r, dict) or r.get("kind") == events.REDACTED:  # a purged case's blanked row (src/purge.py): nothing to copy
                    continue
                db.execute("insert into events (at, who, role, via, case_id, kind, version, action, what, file) values (?,?,?,?,?,?,?,?,?,?)",
                           (r.get("at"), r.get("who"), r.get("role"), r.get("via"), r.get("case"), r.get("kind"), r.get("version"), r.get("action"), r.get("what"), path.name))
                added += 1
        db.execute("insert or replace into ledger_files values (?, ?)", (path.name, at))
    db.commit()
    return added


_LAST_REFRESH: dict[str, float] = {}
_FOLLOWERS: dict[str, keepup.Follower] = {}


def _rebuild_some(clients_root: Path, path: Path, cases: set[str]) -> None:
    """The cases the ledger names, rebuilt from their records as the nightly run left their timelines, and the ledger's new rows copied in."""
    with closing(connect(path)) as db:
        stamp, now = _stamp(), clock.stamp()
        for case in sorted(cases):
            d = clients_root / case
            if Path(case).name != case or case in ("", ".", ".."):
                continue
            with db:
                if d.is_dir() and _has_records(d):
                    _insert(db, d, stamp, now, fresh=False)
                else:
                    _delete(db, case)
        _read_events(db, events.base_path(clients_root.resolve().parent))


def _age(path: Path) -> float | None:
    """Seconds since the file was last written; None when there is none."""
    try:
        return max(0.0, time.time() - Path(path).stat().st_mtime)
    except OSError:
        return None


def _follower(clients_root: Path, path: Path) -> keepup.Follower:
    key = str(path)
    if key not in _FOLLOWERS:
        _FOLLOWERS[key] = keepup.Follower(lambda: events.base_path(clients_root.resolve().parent), lambda: rebuild_changed(clients_root, path),
                                          lambda cases: _rebuild_some(clients_root, path, cases), name="query layer",
                                          age=lambda: _age(path))
    return _FOLLOWERS[key]


def refresh(clients_root: str | Path, db_path: str | Path | None = None, every: float | None = None) -> None:
    """rebuild_changed, but no more than once in `every` seconds (a screen asks before it reads: a record the nightly run has not reached yet is
    counted, and 1,800 folders are not looked at again for every page). With the file there and I485_WALK_EVERY above 0 (as installed) it does not wait:
    a follower (src/keepup.py) rebuilds the cases the ledger names in the background, and walks over every case now and then, and the screen reads the
    file as it is."""
    path = Path(db_path) if db_path else default_path(Path(clients_root))
    now = time.monotonic()
    every = float(os.environ.get("I485_QUERY_REFRESH", "60")) if every is None else every
    if now - _LAST_REFRESH.get(str(path), -1e9) < every and path.exists():
        return
    _LAST_REFRESH[str(path)] = now
    if every > 0 and keepup.walk_every() > 0 and path.exists():
        _follower(Path(clients_root), path).poke()
        return
    rebuild_changed(clients_root, db_path)


def fresh(clients_root: str | Path, db_path: str | Path | None = None) -> sqlite3.Connection | None:
    """What a screen calls: the file brought up to date at most once a minute (refresh; a change made in the review app has already rebuilt its own case), a
    failure said on standard error and the screen reading the records, then opened read-only."""
    path = Path(db_path) if db_path else default_path(Path(clients_root))
    try:
        refresh(clients_root, path)
    except Exception as exc:  # noqa: BLE001 -- a count from the file is a convenience; the screen has the records to fall back on
        import sys

        sys.stderr.write(f"query layer not refreshed: {type(exc).__name__}\n")
    return open_read(path)


# -- what the product's screens ask -------------------------------------------------------------------------


def facets(clients_root: str | Path, include_confidential: bool = False, hidden_cases: list[str] | tuple = (), confidential_cases: list[str] | tuple = (),
           db_path: str | Path | None = None) -> dict[str, Any] | None:
    """What the Search page's pick lists offer (src/index.py facets, from this file): the types in the documents with their counts, and how many cases and
    documents the searcher may see. None when the file cannot be read (the caller asks the index). hidden_cases are not counted at all; a confidential
    document is counted for include_confidential (an attorney) or when its case is in confidential_cases."""
    import index

    db = fresh(clients_root, db_path)
    if db is None:
        return None
    visible, args = "", []
    if hidden_cases:
        visible, args = " and case_id not in (select value from json_each(?))", [json.dumps(list(hidden_cases))]
    if not include_confidential:
        visible += " and (confidential = 0 or case_id in (select value from json_each(?)))"
        args.append(json.dumps(list(confidential_cases or ())))
    try:
        with closing(db):
            rows = db.execute("select type, count(*) n from documents where type <> ''" + visible + " group by type order by n desc", args).fetchall()
            cases, documents = db.execute("select count(distinct case_id), count(*) from documents where 1 = 1" + visible, args).fetchone()
    except sqlite3.DatabaseError:
        return None
    return {"types": [{"id": r["type"], "name": index.type_name(r["type"]), "count": r["n"]} for r in rows], "cases": cases, "documents": documents}


def document_counts(clients_root: str | Path, hidden: list[str] | set[str] = (), shown: list[str] | set[str] | None = None,
                    db_path: str | Path | None = None, *, permitted: set[str] | None = None) -> tuple[list[tuple[str, int, int]], list[tuple[str, int]], int] | None:
    """Reports' two document tables, from this file: ((type id, documents, cases) by type, (quality, documents) by scan quality, how many confidential documents
    the counts leave out). hidden: cases not counted at all. shown: the cases whose confidential documents are counted (None: every case's). None when the
    file cannot be read."""
    db = fresh(clients_root, db_path)
    if db is None:
        return None
    gone = json.dumps(sorted(hidden))
    where = " and case_id not in (select value from json_each(?))" + (" and (confidential = 0 or case_id in (select value from json_each(?)))" if shown is not None else "")
    args = [gone] + ([json.dumps(sorted(shown))] if shown is not None else [])
    positive = " and case_id in (select value from json_each(?))" if permitted is not None else ""
    positive_args = [json.dumps(sorted(permitted))] if permitted is not None else []
    where += positive
    args += positive_args
    try:
        with closing(db):
            types = [(r["type"], r["n"], r["cases"]) for r in
                     db.execute("select type, count(*) n, count(distinct case_id) cases from documents where type <> ''" + where + " group by type order by n desc, type", args)]
            qualities = [(r["quality"] or "unknown", r["n"]) for r in db.execute("select quality, count(*) n from documents where 1=1" + where + " group by quality order by n desc", args)]
            left_out = db.execute("select count(*) from documents where confidential = 1 and case_id not in (select value from json_each(?))"
                                  " and case_id not in (select value from json_each(?))" + positive, [gone, json.dumps(sorted(shown))] + positive_args).fetchone()[0] if shown is not None else 0
    except sqlite3.DatabaseError:
        return None
    return types, qualities, left_out


def reviewers(clients_root: str | Path, db_path: str | Path | None = None) -> dict[str, tuple[str | None, int]] | None:
    """{case id: (the reviewer of record, decisions in force)} for every case, from the file (None when it cannot be read: the caller reads each
    case's decisions itself). Reports and Expiring ask: it is one query instead of one file per case."""
    db = fresh(clients_root, db_path)
    if db is None:
        return None
    with closing(db):
        return {r["case_id"]: (r["reviewer"], r["decisions"]) for r in db.execute("select case_id, reviewer, decisions from cases")}
