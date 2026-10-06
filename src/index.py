"""The firm-wide document index: every document record of every case, searchable.

One SQLite file (data/index.db; I485_INDEX points elsewhere), the same way the
learning store is one file (src/learning/store.py). It is built from what the
pipeline already wrote, never from the scans: each case's documents.json
(src/documents.py: one record per document, with its type, person, dates,
identifiers and OCR text). Nothing here is the source of truth -- delete the
file and the next search or the nightly run builds it again.

    documents   one row per record per case: type, person, language, issued, expires,
                the identifiers (A-Number, receipt, passport, SSN last 4), quality,
                confidential (8 U.S.C. 1367 / 8 CFR 208.6), files, pages, built
    cases       one row per case: the client's name, its office (src/offices.py), the
                filings recorded on it (status.json, src/prefile.py), and what the row was built from
    text_fts    FTS5 over each document's text and English translation

    rebuild(client_dir)           replace one case's rows (batch/process_clients call it after each case)
    rebuild_changed(clients_root) the nightly run: only the cases whose files changed since their rows were built
    rebuild_all(clients_root)     from scratch
    search(query, ...)            what the Search box and the saved searches ask

A full Social Security number is never stored: one in the OCR text is cut to its
last four digits before it is indexed (the record holds only the last 4 itself).
A damaged index file is set aside and built again; nothing here may stop the
overnight run, so the callers log and go on.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import clock
import events
import keepup
import schema_path

REPO = Path(__file__).resolve().parent.parent
SCHEMAS = schema_path.ROOT
DOCUMENTS = "documents.json"  # the per-case record (src/documents.py)
# the files a case's rows are built from: a change in any of them builds the case again (meta.json: which documents were read, and how each was classified)
SIGNED = (DOCUMENTS, "fact_graph.json", "status.json", "office.json", "meta.json")
SCHEMA_VERSION = 1  # raise when the tables change: an old file is dropped and built again (it is only a copy)
MARK_ON, MARK_OFF = "\x01", "\x02"  # around a match in a snippet; the page turns them into <mark>, so no HTML travels
MAX_HITS = 5000  # most matches one search ranks; "total" says when it was cut
SNIPPET_WORDS = 18

# What each table is, for the data dictionary (docs/data_dictionary.md); the columns' sentences are the comments in SCHEMA.
TABLES = {
    "cases": "One row per case: the client's name, its office, the filings recorded on it, and what the row was built from. Holds a person's name.",
    "documents": "One row per document record of a case: its type, whose it is, language, dates, the identifiers read from it and its quality. Holds a person's identifiers.",
    "text_fts": "A full-text index over each document's text and English translation, for Search. Holds the text of every document, protected cases included.",
}

SCHEMA = """
create table if not exists cases (
  case_id text primary key,   -- the case's id: the client's folder name in the data folder
  name text,                  -- the client's name, from the case's fact graph (the folder's name when it has none)
  office text,                -- the office's name (src/offices.py for_case)
  office_id text,             -- the office's id
  filings text,               -- json list of the filing ids recorded on the case (status.json), e.g. ["eoir28"]
  signature text,             -- what the rows were built from: a change means they are built again
  built text                  -- when the row was built (UTC, with offset)
);
create table if not exists documents (
  id integer primary key,     -- the row's number; text_fts uses it
  case_id text not null,      -- the case it belongs to
  doc_id text not null,       -- the document record's id (src/documents.py)
  type text,                  -- what kind of document it is, as an id from the taxonomy
  person text,                -- whose it is: applicant, spouse, petitioner, parent, child_1 ... or unknown
  language text,              -- the language it is printed in
  issued text,                -- the date it was issued (YYYY-MM-DD)
  expires text,               -- the date it ends (YYYY-MM-DD)
  a_number text,              -- the identifiers, normalized (digits; upper-case letters and digits)
  receipt text,               -- the USCIS receipt number read from it
  passport text,              -- the passport number read from it
  ssn_last4 text,             -- the last four digits of a Social Security number read from it (never the whole number)
  quality text,               -- how readable the scan is
  confidential text,          -- '1367', '208.6' or null
  files text,                 -- json list of the files that hold it
  pages text,                 -- json list of its pages
  page integer,               -- the first page to open, 1-based
  built text,                 -- when the row was built (UTC, with offset)
  unique (case_id, doc_id)
);
create index if not exists documents_case on documents(case_id);
create index if not exists documents_type on documents(type);
create index if not exists documents_kind on documents(case_id, type, person, expires, issued);  -- "the case's latest one of its kind"
create index if not exists documents_a_number on documents(a_number);
create index if not exists documents_receipt on documents(receipt);
create index if not exists documents_passport on documents(passport);
create virtual table if not exists text_fts using fts5(
  text,                       -- the document's own text, as read from the scan (a Social Security number cut to its last four digits)
  translated,                 -- its English translation, when it is in another language
  case_id unindexed,          -- the case it belongs to
  doc_id unindexed,           -- the document record
  tokenize = 'unicode61 remove_diacritics 2'
);
"""


class IndexDamaged(Exception):
    """The index file could not be read; it was set aside and the next refresh builds a new one."""


# -- the file ---------------------------------------------------------------------------


def default_path(clients_root: str | Path) -> Path:
    """I485_INDEX, else data/index.db next to the client folders."""
    env = os.environ.get("I485_INDEX")
    return Path(env) if env else Path(clients_root).resolve().parent / "index.db"


def _corrupt(exc: Exception) -> bool:
    """A damaged file, not a busy one (a lock is a DatabaseError too)."""
    text = str(exc).lower()
    return isinstance(exc, sqlite3.DatabaseError) and any(w in text for w in ("malformed", "not a database", "corrupt", "disk image", "vtable constructor"))


def reset(path: str | Path) -> None:
    """Sets a damaged file aside (index.db.damaged, kept for whoever looks) so a new one can be made."""
    path = Path(path)
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    if path.exists():
        try:
            os.replace(path, Path(str(path) + ".damaged"))
        except OSError:
            path.unlink(missing_ok=True)


def connect(path: str | Path) -> sqlite3.Connection:
    """The index, made if it isn't there; a file that can't be read is set aside and made new (raises IndexDamaged
    only if even a new file can't be used)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        db = None
        try:
            db = sqlite3.connect(path, timeout=30)
            db.row_factory = sqlite3.Row
            db.execute("pragma journal_mode=wal")  # the overnight run and the review app both use it
            if db.execute("pragma user_version").fetchone()[0] not in (0, SCHEMA_VERSION):
                for name in ("text_fts", "documents", "cases"):
                    db.execute(f"drop table if exists {name}")
            db.executescript(SCHEMA)
            db.execute(f"pragma user_version = {SCHEMA_VERSION}")
            db.execute("select 1 from documents limit 1").fetchall()
            db.execute("select 1 from text_fts limit 1").fetchall()
            return db
        except sqlite3.DatabaseError as exc:
            if db is not None:
                db.close()
            if not _corrupt(exc):
                raise
            if attempt == 2:
                raise IndexDamaged(f"{type(exc).__name__}: {exc}") from exc
            reset(path)
    raise IndexDamaged("unreachable")  # pragma: no cover


# -- reading a case ----------------------------------------------------------------------


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_SSN = re.compile(r"\b\d{3}[- ]\d{2}[- ](\d{4})\b")
_SSN_LABELLED = re.compile(r"(?i)\b((?:social\s+security|ssn)[^0-9]{0,30})\d{3}[- ]?\d{2}[- ]?(\d{4})\b")


def scrub(text: Any) -> str:
    """The text as it is indexed: a full Social Security number cut to its last four digits."""
    text = str(text or "")
    text = _SSN_LABELLED.sub(lambda m: f"{m.group(1)}XXX-XX-{m.group(2)}", text)
    return _SSN.sub(lambda m: f"XXX-XX-{m.group(1)}", text)


def norm_a_number(value: Any) -> str:
    """A-Number as digits, nine of them: 'A-012 345 678', 'a12345678' and '012345678' are one number."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(9) if 7 <= len(digits) <= 9 else ""


def norm_code(value: Any) -> str:
    """A receipt or passport number: letters and digits, upper case."""
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def _first_page(pages: Any, file_name: str) -> int | None:
    """The first page of the document, 1-based: the record's pages (a list of numbers or of [first, last]), else the
    '#p3-4' a split file's name carries."""
    flat: list[int] = []

    def walk(x):
        if isinstance(x, (list, tuple)):
            for y in x:
                walk(y)
        elif isinstance(x, (int, float)) and not isinstance(x, bool):
            flat.append(int(x))
        elif isinstance(x, str) and x.strip().isdigit():
            flat.append(int(x))

    walk(pages)
    if flat:
        return max(1, min(flat))
    m = re.search(r"#p(\d+)", file_name or "")
    return int(m.group(1)) if m else None


def _fact(facts: dict, key: str) -> str | None:
    f = facts.get(key) or {}
    return str(f["value"]) if f.get("status") == "resolved" and f.get("value") not in (None, "") else None


def _name_and_state(client_dir: Path) -> tuple[str, str | None]:
    """The client's name and the state of the I-485's physical address, from the case's fact graph (name: the folder's name without one)."""
    data = _read(client_dir / "fact_graph.json")
    facts = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(facts, dict):
        return client_dir.name, None
    name = " ".join(x for x in (_fact(facts, "applicant.given_name"), _fact(facts, "applicant.middle_name"), _fact(facts, "applicant.family_name")) if x)
    if not name:  # nobody's name read yet: the portal's name for the client, else "New case, MM/DD/YYYY" (never the id the folder is named by)
        from review.state import display_name

        name = display_name(client_dir)
    return name, _fact(facts, "applicant.physical_state")


def _office(client_dir: Path, state: str | None) -> tuple[str, str]:
    try:
        import offices

        o = offices.for_case(client_dir, state)
        return o["name"], o["id"]
    except Exception:  # noqa: BLE001 -- the office is a convenience filter; a settings problem must not stop the index
        return "", ""


def _filings(client_dir: Path) -> list[str]:
    status = _read(client_dir / "status.json")
    rows = status.get("filings") if isinstance(status, dict) else None
    return sorted({str(r["filing"]) for r in rows or [] if isinstance(r, dict) and r.get("filing")})


def _office_stamp() -> str:
    """Changes when an office or the states it files for changes (the Settings page): the case's office may be another one."""
    try:
        import offices

        return hashlib.sha256(json.dumps([(o["id"], o["states"]) for o in offices.offices()]).encode()).hexdigest()[:10]
    except Exception:  # noqa: BLE001
        return ""


def _signature(client_dir: Path, stamp: str) -> str:
    parts = [stamp]
    for name in SIGNED:
        p = client_dir / name
        parts.append(f"{p.stat().st_mtime_ns}:{p.stat().st_size}" if p.exists() else "-")
    return "|".join(parts)


def _case_confidentiality(client_dir: Path) -> str | None:
    """documents.case_confidentiality, or None when it can't be asked (never fail an index build over it)."""
    try:
        import documents

        return documents.case_confidentiality(client_dir)
    except Exception:  # noqa: BLE001 -- a case folder with nothing to go on
        return None


def _insert(db: sqlite3.Connection, client_dir: Path, records: dict, stamp: str, now: str) -> int:
    case = client_dir.name
    name, state = _name_and_state(client_dir)
    office, office_id = _office(client_dir, state)
    db.execute("delete from text_fts where rowid in (select id from documents where case_id = ?)", (case,))
    db.execute("delete from documents where case_id = ?", (case,))
    db.execute("delete from cases where case_id = ?", (case,))
    # a protected case (VAWA, T, U: 8 U.S.C. 1367; asylum: 8 CFR 208.6) restricts every document in it, whatever the
    # record says about each one: the case-level rule lives in documents.case_confidentiality and is asked here too,
    # so a filing recorded after the record was written still restricts the case in search
    case_flag = _case_confidentiality(client_dir)
    n, seen = 0, set()
    for rec in records.get("documents") or []:
        if not isinstance(rec, dict) or not rec.get("id") or str(rec["id"]) in seen:
            continue
        seen.add(str(rec["id"]))
        ids = rec.get("identifiers") if isinstance(rec.get("identifiers"), dict) else {}
        files = [str(f) for f in rec.get("files") or [] if f] if isinstance(rec.get("files"), list) else []
        pages = rec.get("pages") if isinstance(rec.get("pages"), list) else []
        confidential = case_flag or (str(rec["confidential"]) if rec.get("confidential") else None)
        cur = db.execute(
            "insert into documents (case_id, doc_id, type, person, language, issued, expires, a_number, receipt, passport, ssn_last4,"
            " quality, confidential, files, pages, page, built) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (case, str(rec["id"]), rec.get("type") or "", rec.get("person") or "unknown", rec.get("language") or "unknown",
             rec.get("issued") or None, rec.get("expires") or None, norm_a_number(ids.get("a_number")) or None,
             norm_code(ids.get("receipt")) or None, norm_code(ids.get("passport")) or None,
             re.sub(r"\D", "", str(ids.get("ssn_last4") or ""))[-4:] or None, rec.get("quality") or "unknown", confidential,
             json.dumps(files, ensure_ascii=False), json.dumps(pages), _first_page(pages, files[0] if files else ""), now))
        db.execute("insert into text_fts (rowid, text, translated, case_id, doc_id) values (?,?,?,?,?)",
                   (cur.lastrowid, scrub(rec.get("text")), scrub(rec.get("translated")), case, str(rec["id"])))
        n += 1
    db.execute("insert into cases (case_id, name, office, office_id, filings, signature, built) values (?,?,?,?,?,?,?)",
               (case, name, office, office_id, json.dumps(_filings(client_dir)), _signature(client_dir, stamp), now))
    return n


def _rebuild_case(db: sqlite3.Connection, client_dir: Path, stamp: str) -> int | None:
    """The case's rows from its documents.json. A case with no record has no rows (skipped); one whose record can't be
    read keeps the rows it had (None)."""
    path = client_dir / DOCUMENTS
    case = client_dir.name
    if not path.exists():
        with db:
            db.execute("delete from text_fts where rowid in (select id from documents where case_id = ?)", (case,))
            db.execute("delete from documents where case_id = ?", (case,))
            db.execute("delete from cases where case_id = ?", (case,))
        return 0
    records = _read(path)
    if not isinstance(records, dict):
        return None
    try:  # whose each document is as the whole case says it now (documents.infer_people): an assumption never outlives the case
        import documents

        records = documents.load(client_dir)
    except Exception:  # noqa: BLE001 -- the record as written still indexes
        pass
    with db:  # one transaction: a search never sees half a case
        return _insert(db, client_dir, records, stamp, clock.stamp())


def rebuild(client_dir: str | Path, db_path: str | Path | None = None) -> int:
    """Replaces one case's rows (client_dir is data/clients/<id>). Returns the documents indexed; 0 for a case with no
    documents.json. A damaged index file is made new, holding just this case until the nightly run (or the next search) fills it."""
    client_dir = Path(client_dir)
    path = Path(db_path) if db_path else default_path(client_dir.parent)
    with closing(connect(path)) as db:
        try:
            return _rebuild_case(db, client_dir, _office_stamp()) or 0
        except sqlite3.DatabaseError as exc:
            if not _corrupt(exc):
                raise
    reset(path)
    with closing(connect(path)) as db:
        return _rebuild_case(db, client_dir, _office_stamp()) or 0


def _case_dirs(clients_root: Path) -> list[Path]:
    return sorted(p for p in clients_root.iterdir() if p.is_dir()) if clients_root.is_dir() else []


def rebuild_changed(clients_root: str | Path, db_path: str | Path | None = None, *, integrity: bool = False, force: bool = False) -> dict[str, int]:
    """Builds the cases whose files changed since their rows were built (the nightly run; the Search page calls it
    before a search, at most once a minute). A case with no documents.json is skipped; a case folder that is gone loses its rows.
    integrity: also check the index file itself first (a damaged one is made new, and every case is built again). force: every case."""
    clients_root = Path(clients_root)
    path = Path(db_path) if db_path else default_path(clients_root)
    for attempt in (1, 2):
        try:
            with closing(connect(path)) as db:
                if integrity and db.execute("pragma quick_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("database disk image is malformed")
                return _refresh(db, clients_root, force)
        except sqlite3.DatabaseError as exc:
            if not _corrupt(exc) or attempt == 2:
                raise
            reset(path)
    raise IndexDamaged("unreachable")  # pragma: no cover


def _refresh(db: sqlite3.Connection, clients_root: Path, force: bool) -> dict[str, int]:
    stamp = _office_stamp()
    known = {r["case_id"]: r["signature"] for r in db.execute("select case_id, signature from cases")}
    out = {"rebuilt": 0, "unchanged": 0, "skipped": 0, "removed": 0, "unreadable": 0}
    present = set()
    for d in _case_dirs(clients_root):
        present.add(d.name)
        if not (d / DOCUMENTS).exists():
            if d.name in known:  # the record was taken away: so are its rows
                _rebuild_case(db, d, stamp)
                out["removed"] += 1
            else:
                out["skipped"] += 1
            continue
        if not force and known.get(d.name) == _signature(d, stamp):
            out["unchanged"] += 1
            continue
        try:
            n = _rebuild_case(db, d, stamp)
        except sqlite3.DatabaseError:
            raise
        except Exception:  # noqa: BLE001 -- one odd record must not stop the other 1,799
            n = None
        out["rebuilt" if n is not None else "unreadable"] += 1
    for gone in set(known) - present:  # a case folder that no longer exists
        with db:
            db.execute("delete from text_fts where rowid in (select id from documents where case_id = ?)", (gone,))
            db.execute("delete from documents where case_id = ?", (gone,))
            db.execute("delete from cases where case_id = ?", (gone,))
        out["removed"] += 1
    out["documents"] = db.execute("select count(*) from documents").fetchone()[0]
    return out


def rebuild_all(clients_root: str | Path, db_path: str | Path | None = None) -> dict[str, int]:
    """Every case from scratch."""
    return rebuild_changed(clients_root, db_path, force=True)


_LAST_REFRESH: dict[str, float] = {}


def refresh(clients_root: str | Path, db_path: str | Path | None = None, every: float = 60.0) -> None:
    """rebuild_changed, but no more than once in `every` seconds (the review app calls it on each search, so a record the
    overnight run hasn't reached yet is still found, and 1,800 folders are not looked at again for every keystroke)."""
    path = str(Path(db_path) if db_path else default_path(Path(clients_root)))
    now = time.monotonic()
    if now - _LAST_REFRESH.get(path, -1e9) < every and Path(path).exists():
        return
    _LAST_REFRESH[path] = now
    rebuild_changed(clients_root, db_path)


_FOLLOWERS: dict[str, keepup.Follower] = {}


def _age(path: Path) -> float | None:
    """Seconds since the file was last written; None when there is none."""
    try:
        return max(0.0, time.time() - Path(path).stat().st_mtime)
    except OSError:
        return None


def keep_up(clients_root: str | Path, db_path: str | Path | None = None, every: float = 60.0) -> None:
    """What the review app calls before a search. As installed (I485_WALK_EVERY above 0) and with the index built, it does not look at every case: a
    follower (src/keepup.py) rebuilds, in the background, the cases the event ledger names, and walks over every case now and then; the search reads the index
    as it is. Otherwise (the tests; a first build) it is refresh(): look at everything, then answer."""
    clients_root = Path(clients_root)
    path = Path(db_path) if db_path else default_path(clients_root)
    if keepup.walk_every() <= 0 or not path.exists():
        return refresh(clients_root, path, every=every)
    key = str(path)
    if key not in _FOLLOWERS:
        def some(cases: set[str]) -> None:
            for case in sorted(cases):
                if Path(case).name == case and case not in ("", ".", ".."):
                    rebuild(clients_root / case, path)

        _FOLLOWERS[key] = keepup.Follower(lambda: events.base_path(clients_root.resolve().parent), lambda: rebuild_changed(clients_root, path), some, name="search index",
                                          age=lambda: _age(path))
    _FOLLOWERS[key].poke()


# -- names ---------------------------------------------------------------------------------

# What the classifier calls each type today (classify/patterns.py), and the ids the document taxonomy uses
# (schemas/registers/document_types.json) for the ones the Search page names. The taxonomy's own name wins when it has one.
FALLBACK_NAMES = {
    "i94": "I-94", "uscis_notice": "USCIS notice", "birth_certificate": "Birth certificate", "drivers_license": "Driver's license",
    "ssn_card": "Social Security card", "passport": "Passport", "visa": "Visa", "intake_questionnaire": "Intake questionnaire",
    "g28": "Form G-28", "i765": "Form I-765", "i485": "Form I-485", "translation_certification": "Translator's certificate",
    "marriage_certificate": "Marriage certificate", "notice_to_appear": "Notice to Appear", "nta": "Notice to Appear",
    "sij_order": "SIJ court order", "us_passport": "U.S. passport", "citizenship_certificate": "Citizenship certificate",
    "green_card": "Green card", "us_birth_certificate": "U.S. birth certificate", "tax_return": "Tax return", "w2": "W-2",
    "pay_stub": "Pay stub", "bank_statement": "Bank statement", "lease": "Lease", "utility_bill": "Utility bill",
    "divorce_decree": "Divorce decree", "criminal_record": "Court or police record", "work_permit": "Work permit (EAD)",
    "ead": "Work permit (EAD)", "police_clearance": "Police clearance", "eoir28": "EOIR-28",
}
_NAMES: dict[str, tuple[float, dict[str, str]]] = {}
_TAXONOMY_STAT: dict[str, tuple[float, float]] = {}  # the taxonomy file: when its date was last asked and what it was (a search names fifty documents, and a file's date is a look at the disk each)


def _taxonomy_mtime(path: Path) -> float | None:
    now = time.monotonic()
    seen = _TAXONOMY_STAT.get(str(path))
    if seen is None or now - seen[0] > 2:
        try:
            seen = (now, path.stat().st_mtime)
        except OSError:
            return None
        _TAXONOMY_STAT[str(path)] = seen
    return seen[1]


def _taxonomy_names() -> dict[str, str]:
    """id -> name from schemas/registers/document_types.json, whichever shape it has (a list of types, or a dict keyed by id)."""
    path = schema_path.path("register", "document_types", SCHEMAS)
    mtime = _taxonomy_mtime(path)
    if mtime is None:
        return {}
    if _NAMES.get("t", (0, {}))[0] == mtime:
        return _NAMES["t"][1]
    data = _read(path)
    types = data.get("types") if isinstance(data, dict) else data
    out: dict[str, str] = {}
    items = types.items() if isinstance(types, dict) else ((t.get("id"), t) for t in types or [] if isinstance(t, dict))
    for key, t in items:
        if isinstance(t, dict) and key:
            label = t.get("name") or t.get("label") or t.get("title")
            if label:
                out[str(key)] = str(label)
    _NAMES["t"] = (mtime, out)
    return out


def type_name(type_id: str | None) -> str:
    type_id = type_id or ""
    return (_taxonomy_names().get(type_id) or FALLBACK_NAMES.get(type_id)
            or (type_id.replace("_", " ").capitalize() if type_id else "Document"))


def type_short(type_id: str | None) -> str:
    """The name for a column on a staff screen: the taxonomy's short name when the type has one (schemas/registers/document_types.json), else type_name()."""
    return _taxonomy_shorts().get(type_id or "") or type_name(type_id)


def _taxonomy_shorts() -> dict[str, str]:
    """id -> short name, for the types that have one (read again when the file changes, as _taxonomy_names is)."""
    path = schema_path.path("register", "document_types", SCHEMAS)
    mtime = _taxonomy_mtime(path)
    if mtime is None:
        return {}
    if _NAMES.get("s", (0, {}))[0] == mtime:
        return _NAMES["s"][1]
    data = _read(path)
    types = data.get("types") if isinstance(data, dict) else None
    out = {str(t["id"]): str(t["short_name"]) for t in types or [] if isinstance(t, dict) and t.get("id") and t.get("short_name")}
    _NAMES["s"] = (mtime, out)
    return out


# -- searching -----------------------------------------------------------------------------


def _fts_query(text: str) -> str | None:
    """The words typed, as an FTS5 query that can't be a syntax error: every word quoted, the last one a prefix (so the
    results follow the typing). Accents don't matter (the tokenizer drops them: 'Jose' finds 'José')."""
    words = re.findall(r"\w+", text or "")[:12]
    if not words:
        return None
    return " ".join([f'"{w}"' for w in words[:-1]] + [f'"{words[-1]}"*'])


def _identifier(text: str) -> tuple[str, str, str] | None:
    """(A-Number, receipt, passport) as they'd be stored, when the box holds a number rather than words: 'A-012 345 678',
    'IOE0999000300', 'YA1234567'. They match the identifiers exactly, as well as the text."""
    compact = re.sub(r"[\s-]", "", text or "").upper()
    if not re.fullmatch(r"[A-Z0-9]{6,15}", compact) or not any(ch.isdigit() for ch in compact):
        return None
    digits = compact[1:] if re.fullmatch(r"A\d{7,9}", compact) else compact if compact.isdigit() else ""
    return norm_a_number(digits), compact, compact


def _where(*, types, person, office, expiring_before, expiring_after, issued_before, no_filing, confidential: str,
           hidden: list[str] | tuple = (), open_cases: list[str] | tuple = ()) -> tuple[str, list]:
    """The filters every search shares, as SQL over `documents d join cases c`. confidential: 'no' (the public ones),
    'only' (the restricted ones, to count them) or 'all'. hidden: cases never searched (restricted cases the searcher may not
    open, src/restricted.py); open_cases: cases whose confidential documents count as public for this searcher (they are
    named on the case)."""
    sql, args = [], []
    if hidden:
        sql.append("d.case_id not in (select value from json_each(?))")
        args.append(json.dumps(list(hidden)))
    if types:
        sql.append(f"d.type in ({','.join('?' * len(types))})")
        args += list(types)
    if person:
        sql.append("d.person = ?")
        args.append(person)
    if office:
        sql.append("(lower(c.office_id) = lower(?) or lower(c.office) = lower(?))")
        args += [office, office]
    if expiring_before or expiring_after:
        # the case's current one of its kind: an older card that a newer one replaced isn't expiring on anyone
        sql.append("d.expires is not null")
        sql.append("not exists (select 1 from documents o where o.case_id = d.case_id and o.type = d.type and o.person = d.person"
                   " and o.expires > d.expires)")
        if expiring_before:
            sql.append("d.expires <= ?")
            args.append(expiring_before)
        if expiring_after:
            sql.append("d.expires >= ?")
            args.append(expiring_after)
    if issued_before:
        sql.append("d.issued is not null and d.issued < ?")
        args.append(issued_before)
        sql.append("not exists (select 1 from documents o where o.case_id = d.case_id and o.type = d.type and o.person = d.person"
                   " and o.issued > d.issued)")
    if no_filing:
        sql.append("not exists (select 1 from json_each(c.filings) where value = ?)")
        args.append(no_filing)
    if confidential == "no":
        sql.append("(d.confidential is null or d.case_id in (select value from json_each(?)))" if open_cases else "d.confidential is null")
        args += [json.dumps(list(open_cases))] if open_cases else []
    elif confidential == "only":
        sql.append("d.confidential is not null" + (" and d.case_id not in (select value from json_each(?))" if open_cases else ""))
        args += [json.dumps(list(open_cases))] if open_cases else []
    return (" and " + " and ".join(sql)) if sql else "", args


def _iso(value: str | None, what: str) -> str | None:
    """A date typed as 12/31/2026 or 2026-12-31, as 2026-12-31."""
    if not value:
        return None
    text = str(value).strip()
    try:
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", text):
            month, day, year = (int(x) for x in text.split("/"))
            return date(year, month, day).isoformat()
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError(f"{what}: a date like 12/31/2026 is needed") from None


def search(query: str = "", *, types: list[str] | tuple[str, ...] | None = None, person: str | None = None, office: str | None = None,
           expiring_before: str | None = None, expiring_after: str | None = None, issued_before: str | None = None,
           no_filing: str | None = None, include_confidential: bool = False, limit: int = 50,
           db_path: str | Path | None = None, hidden_cases: list[str] | tuple = (), confidential_cases: list[str] | tuple = ()) -> dict[str, Any]:
    """The documents matching a typed query and/or filters, best match first (a filter-only search lists by date).

    query: words (all must appear, in the text or its English translation; the last may be a prefix) or an A-Number,
        receipt number or passport number (matches the identifiers exactly, as well as the text).
    types: document type ids. person: applicant, spouse, child_1, petitioner, parent. office: an office's name or id.
    expiring_before / expiring_after: the document's expiry between the two dates (the case's latest one of that
        type and person only). issued_before: issued before that date (the latest of its kind only).
    no_filing: only cases with no such filing recorded (e.g. "eoir28").
    include_confidential: restricted documents (1367 / 208.6) are listed only when True (the attorney); otherwise
        they are left out and only counted ("restricted"), except in confidential_cases (the cases the searcher is named on).
    hidden_cases: restricted cases the searcher may not open (src/restricted.py): never searched, never counted.

    Returns {"query", "results", "total" (matches, up to MAX_HITS), "restricted" (matches left out), "limit"}; each result:
    case, name, office, doc, type, type_name, person, language, issued, expires, quality, confidential, file, page (1-based,
    or None), files, snippet (the match between \\x01 and \\x02), match ("text", "identifier" or "filter")."""
    query = (query or "").strip()
    filters = dict(types=[t for t in (types or []) if t], person=person or None, office=office or None,
                   expiring_before=_iso(expiring_before, "Expiring before"), expiring_after=_iso(expiring_after, "Expiring after"),
                   issued_before=_iso(issued_before, "Issued before"), no_filing=no_filing or None)
    try:
        limit = max(1, min(int(limit or 50), 200))
    except (TypeError, ValueError):
        raise ValueError("Limit must be a number.") from None
    if query and not re.search(r"\w", query):
        query = ""  # punctuation only: no words to find, so only the filters apply
    empty = {"query": query, "results": [], "total": 0, "restricted": 0, "limit": limit}
    if not query and not any(filters.values()):
        return empty  # nothing asked: never a dump of every document
    path = Path(db_path) if db_path else default_path(REPO / "data" / "clients")
    if not path.exists():
        return empty
    try:
        with closing(connect(path)) as db:
            if not db.execute("select 1 from cases limit 1").fetchone():
                _LAST_REFRESH.pop(str(path), None)  # nothing built (a new file, or a damaged one just set aside): the next refresh fills it
            access = {"hidden": list(hidden_cases or ()), "open_cases": list(confidential_cases or ())}
            return _search(db, query, filters, include_confidential, limit, access) | {"query": query, "limit": limit}
    except (sqlite3.DatabaseError, IndexDamaged) as exc:
        if isinstance(exc, IndexDamaged) or _corrupt(exc):
            reset(path)  # the next refresh builds a new one
            _LAST_REFRESH.pop(str(path), None)
            return empty | {"damaged": True}
        raise


def _ranked(db: sqlite3.Connection, query: str, filters: dict, confidential: str, access: dict | None = None) -> list[tuple[int, str]]:
    """[(document row id, how it matched)] best first: the identifier matches, then the text matches by rank."""
    where, args = _where(confidential=confidential, **filters, **(access or {}))
    hits: list[tuple[int, str]] = []
    seen: set[int] = set()
    ident = _identifier(query) if query else None
    if ident:
        a, receipt, passport = ident
        for r in db.execute("select d.id from documents d join cases c on c.case_id = d.case_id"
                            " where (d.a_number = ? or d.receipt = ? or d.passport = ?)" + where + " order by d.case_id, d.id limit ?",
                            [a or "-", receipt, passport, *args, MAX_HITS]):
            hits.append((r["id"], "identifier"))
            seen.add(r["id"])
    fts = _fts_query(query) if query else None
    if fts:
        for r in db.execute("select d.id from text_fts join documents d on d.id = text_fts.rowid join cases c on c.case_id = d.case_id"
                            " where text_fts match ?" + where + " order by bm25(text_fts) limit ?", [fts, *args, MAX_HITS]):
            if r["id"] not in seen:
                hits.append((r["id"], "text"))
                seen.add(r["id"])
    elif not query:
        order = "d.expires, d.case_id" if filters["expiring_before"] or filters["expiring_after"] else "d.issued, d.case_id" if filters["issued_before"] else "c.name, d.case_id"
        for r in db.execute(f"select d.id from documents d join cases c on c.case_id = d.case_id where 1 = 1{where} order by {order}, d.id limit ?",
                            [*args, MAX_HITS]):
            hits.append((r["id"], "filter"))
    return hits


def _search(db: sqlite3.Connection, query: str, filters: dict, include_confidential: bool, limit: int, access: dict | None = None) -> dict[str, Any]:
    hits = _ranked(db, query, filters, "all" if include_confidential else "no", access)
    restricted = 0 if include_confidential else len(_ranked(db, query, filters, "only", access))
    page = hits[:limit]
    by_id = {}
    if page:
        marks = ",".join("?" * len(page))
        for r in db.execute(f"select d.*, c.name, c.office from documents d join cases c on c.case_id = d.case_id where d.id in ({marks})",
                            [i for i, _ in page]):
            by_id[r["id"]] = r
    snippets: dict[int, str] = {}
    fts = _fts_query(query) if query else None
    if fts and page:
        marks = ",".join("?" * len(page))
        for r in db.execute(f"select rowid, snippet(text_fts, -1, ?, ?, '…', {SNIPPET_WORDS}) as s from text_fts"
                            f" where text_fts match ? and rowid in ({marks})", [MARK_ON, MARK_OFF, fts, *[i for i, _ in page]]):
            snippets[r["rowid"]] = r["s"]
    results = []
    for i, how in page:
        r = by_id.get(i)
        if r is None:
            continue
        files = json.loads(r["files"] or "[]")
        results.append({"case": r["case_id"], "name": r["name"], "office": r["office"], "doc": r["doc_id"], "type": r["type"],
                        "type_name": type_name(r["type"]), "type_short": type_short(r["type"]), "person": r["person"], "language": r["language"], "issued": r["issued"],
                        "expires": r["expires"], "quality": r["quality"], "confidential": bool(r["confidential"]),
                        "file": files[0] if files else None, "files": files, "page": r["page"], "snippet": snippets.get(i, ""), "match": how})
    return {"results": results, "total": len(hits), "restricted": restricted, "capped": len(hits) >= MAX_HITS}


def facets(db_path: str | Path | None = None, clients_root: str | Path | None = None, include_confidential: bool = False,
           hidden_cases: list[str] | tuple = (), confidential_cases: list[str] | tuple = ()) -> dict[str, list]:
    """What the Search page's pick lists offer: the types in the index (with counts), and how many cases have a document the
    searcher may see ("cases" and "documents": what a search that finds nothing says it looked at). A type that exists only in restricted
    documents is not offered, and they are not counted, unless include_confidential (the attorney) or the document's case is in
    confidential_cases. hidden_cases (src/restricted.py) are not counted at all."""
    path = Path(db_path) if db_path else default_path(clients_root or REPO / "data" / "clients")
    if not path.exists():
        return {"types": [], "cases": 0, "documents": 0}
    visible, args = "", []
    if hidden_cases:
        visible, args = " and case_id not in (select value from json_each(?))", [json.dumps(list(hidden_cases))]
    if not include_confidential:
        visible += " and (confidential is null or case_id in (select value from json_each(?)))"
        args.append(json.dumps(list(confidential_cases or ())))
    try:
        with closing(connect(path)) as db:
            rows = db.execute("select type, count(*) n from documents where type <> ''" + visible + " group by type order by n desc", args).fetchall()
            cases, documents = db.execute("select count(distinct case_id), count(*) from documents where 1 = 1" + visible, args).fetchone()
    except (sqlite3.DatabaseError, IndexDamaged):
        return {"types": [], "cases": 0, "documents": 0}
    return {"types": [{"id": r["type"], "name": type_name(r["type"]), "count": r["n"]} for r in rows], "cases": cases, "documents": documents}


# -- the questions the firm asks -------------------------------------------------------------


def saved_searches(today: date | None = None) -> list[dict[str, Any]]:
    """The buttons on the Search page: each is only a set of filters for the same search (nothing else is behind them).
    The type lists hold the taxonomy's id and the classifier's older one for the same thing."""
    today = today or clock.today()
    return [
        {"id": "ead_90", "label": "Work permits (EADs) expiring in 90 days", "none": "No work permits expire in the next 90 days",
         "params": {"type": "ead,work_permit", "expiring_after": today.isoformat(), "expiring_before": (today + timedelta(days=90)).isoformat()}},
        {"id": "nta_no_eoir28", "label": "Notice to Appear with no EOIR-28 filed", "none": "No case has a Notice to Appear without an EOIR-28 filed",
         "params": {"type": "nta,notice_to_appear", "no_filing": "eoir28"}},
        {"id": "police_old", "label": "Police clearances older than two years", "none": "No police clearance is older than two years",
         "params": {"type": "police_clearance", "issued_before": (today - timedelta(days=730)).isoformat()}},  # valid two years from issuance (9 FAM 504.4-4(A) c(3), read 10/02/2026; schemas/law/expiry_rules.json)
    ]
