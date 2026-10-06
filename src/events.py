"""The event ledger: one appended row for every change to a case's records, and to the firm's own records.

    data/events.jsonl           names the ledger (I485_EVENTS points elsewhere; the tests do)
    data/events-YYYY-MM.jsonl   where its rows are: one file a month, so a file stays small and an old month is never opened again

Every writer in the product appends here after it has written its record (decisions, documents set by a person, journey marks, mailing
records, restriction changes, policy edits, imports, syncs, the portal's answers and uploads, settings, staff accounts, exports). The ledger
answers "who changed what on this case, and when" without opening the case's files. It is the firm's, in plain text, readable without us.

A row (the data dictionary, docs/data_dictionary.md, says the same in a table; this docstring is where each field's sentence comes from):

    at       when, in the firm's own time with its offset ("2026-10-03T10:05:09-04:00": src/clock.py stamp)
    who      the person: the signed-in staff member's name, "The overnight run", "The client", "The importer", the connector's name
    role     attorney, paralegal, client, system, or staff (a person using the app with no staff accounts: a typed name)
    via      how: staff (the review app), overnight, portal, importer, connector, tool (a command run by the firm's IT) or system
    case     the case's id (the client's folder name, which the front desk and the importers make from the client's name: it can be a name), or null for a change to
             the firm's own records
    kind     which record changed: a key of KINDS
    version  the record's version after the write (its own version field; a record with no version field is version 1)
    action   what was done, in one word
    what     what changed, in a short plain sentence, in the product's own words (a step's title, a filing's name, a fact's label). NEVER a value, number or date a
             person gave: no id, key or date is copied into it (events.plain and events.words take them out)

    prev     the previous row's hash ("" for the first row ever, and for the first row after rows written before the chain)
    hash     the SHA-256 of this row's own canonical JSON without the hash (src/ledger_seal.py says what that proves and checks it)

Append-only. Nothing in the product rewrites or deletes a row. A month's file is opened for appending (one write of one whole line, so two
processes never mix their rows). The writer reads only the last row of the ledger, from the end of the file, for its hash, and takes the ledger's own lock
(src/oslock.py, events.lock beside the files) for that read and the append, so the overnight run's workers, the server and the job worker chain one row after
another; the row's time is taken under the lock too, so the chain's order is the clock's. The screens read the files through an index built the way
review/oversight.py builds the view log's (review.oversight.Events), and the query layer (src/query.py) copies the rows into its events table.

record() never raises: a ledger that cannot be written is said on the server's console (standard error) and the work goes on. The change
itself has been made by then, and stopping a filing over a log line would be worse than the missing line.

Who: the review app names the signed-in person for the length of a request (acting(), set_actor()); the overnight run, the importers and the
connectors name themselves the same way; the portal's own writes are "The client" unless a staff member is acting. A writer that knows the
person (a decision's reviewer) passes it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import clock
import oslock

REPO = Path(__file__).resolve().parent.parent
FILE = "events.jsonl"
FIELDS = ("at", "who", "role", "via", "case", "kind", "version", "action", "what")
VIA = ("staff", "overnight", "portal", "importer", "connector", "tool", "system", "support")  # support: the provider's support person, let in by an attorney (src/support.py)
ROLES = ("attorney", "paralegal", "client", "system", "staff", "support")  # staff: a person using the app with no staff accounts (one machine, a typed name)
CHAIN = ("prev", "hash")  # what the chain adds to a row (a row written before the chain has neither)
MAX_WHAT = 240  # a row's sentence is cut here: a ledger row is a line, not a document
LOCK_WAIT = 30.0  # seconds a writer waits for another's turn (a turn is a read of one row and one append: milliseconds)

# What changed, as the screens and the dictionary call it. One row per kind of record the product keeps; the key is what a row's "kind" holds.
# "firm" kinds have no case: they are the firm's own records (settings, policies, staff, upkeep, exports).
KINDS: dict[str, dict[str, Any]] = {
    "assignment": {"name": "Staff case responsibility", "firm": False},
    "facts": {"name": "Facts read from documents", "firm": False},
    "documents": {"name": "Documents", "firm": False},
    "decisions": {"name": "Review decisions and answers", "firm": False},
    "journey": {"name": "Where the case stands", "firm": False},
    "path": {"name": "The case's path: a change proposed, approved, refused or taken back to the template", "firm": False},
    "filings": {"name": "Filings mailed", "firm": False},
    "packet": {"name": "Packets and forms", "firm": False},
    "access": {"name": "Who may open the case", "firm": False},
    "office": {"name": "The case's office", "firm": False},
    "translations": {"name": "Translations", "firm": False},
    "portal": {"name": "The client portal", "firm": False},
    "imports": {"name": "Imports and syncs", "firm": False},
    "engagement": {"name": "The agreement and the case's end", "firm": False},
    "conflicts": {"name": "Conflict searches and decisions", "firm": False},  # src/conflicts.py; a search by hand has no case
    "questions": {"name": "Questions asked about the case", "firm": False},
    "prospects": {"name": "First calls: people who are not clients yet", "firm": False},  # src/prospects.py; the row's case reads "prospect:<id>"
    "notes": {"name": "Case notes, tasks and what a person could apply for", "firm": False},  # src/case_notes.py, src/apply_for.py: on a case, or on a prospect
    "settings": {"name": "The firm's settings", "firm": True},
    "policies": {"name": "Firm policies and rule approvals", "firm": True},
    "accounts": {"name": "Staff accounts", "firm": True},
    "upkeep": {"name": "Keeping current", "firm": True},
    "wordings": {"name": "The firm's wordings", "firm": True},  # src/wordings.py: the library learned from approvals (brief L3)
    "export": {"name": "Exports of the firm's data", "firm": True},
    "secrets": {"name": "Keys and secrets", "firm": True},  # src/firmsecrets.py: that a secret was changed, by whom and when; never the value
    "find": {"name": "Questions asked of Find across the firm", "firm": True},  # src/find.py: who and when and the question's length, never its text
    "purge": {"name": "Purges of a case: asked, confirmed, cancelled, run", "firm": False},
    "support": {"name": "Support sessions: let in, every request support made, ended", "firm": True},  # src/support.py: under the support person's name, via support  # src/purge.py: the only rows of a purged case the ledger keeps
}

_local = threading.local()
_process: dict[str, str | None] | None = None  # who is acting for the whole process (the overnight run, whose workers are threads or processes of their own)


# -- who -------------------------------------------------------------------------------------------------------------


def set_actor(who: str | None, role: str | None = None, via: str = "staff") -> None:
    """The person at the keyboard for the rest of this thread's request (the review app's handler; clear_actor() ends it)."""
    _local.actor = {"who": str(who or "").strip() or None, "role": role if role in ROLES else None, "via": via if via in VIA else "system"}


def clear_actor() -> None:
    _local.actor = None


def actor() -> dict[str, str | None] | None:
    """The person acting for this request (set_actor), else the one acting for the whole process (acting(everywhere=True)), else nobody."""
    return getattr(_local, "actor", None) or _process


@contextmanager
def acting(who: str | None, role: str | None = None, via: str = "staff", everywhere: bool = False) -> Iterator[None]:
    """Everything written inside is the ledger's row for this person ("The overnight run", via="overnight"). everywhere: for every thread of this process,
    not only this one (the overnight run hands its cases to workers)."""
    global _process
    before, before_process = getattr(_local, "actor", None), _process
    set_actor(who, role, via)
    if everywhere:
        _process = dict(_local.actor)
    try:
        yield
    finally:
        _local.actor = before
        _process = before_process


# -- words -----------------------------------------------------------------------------------------------------------


# What a person's data looks like inside a key or an id: a receipt, passport or A-Number (capitals and digits), a long number, a date.
IDENTIFIER = re.compile(r"[A-Z]{1,4}[A-Z0-9]*\d[A-Z0-9]*|\d{6,}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}")


def mask(text: str, seen: dict[str, int] | None = None) -> str:
    """text with each identifier in it (IDENTIFIER) replaced by #1, #2 ... in the order they first appear (seen: the counter, kept across the keys of one case,
    so the same number is the same #n and nothing of it is left)."""
    seen = seen if seen is not None else {}
    return IDENTIFIER.sub(lambda m: f"#{seen.setdefault(m.group(0), len(seen) + 1)}", str(text or ""))


def words(key: str) -> str:
    """A fact key in words, never its value: "applicant.date_of_birth" is "applicant date of birth". An identifier in a key (a receipt or passport number) is dropped."""
    return re.sub(r"[\s._:#]+", " ", IDENTIFIER.sub(" ", str(key or ""))).strip().lower()


def plain(text: str, limit: int = 100) -> str:
    """A sentence from the product's own words (a step's title, a label) with anything that looks like a person's data (IDENTIFIER) taken out."""
    return " ".join(IDENTIFIER.sub("", str(text or "")).replace(" -- ", ": ").replace("—", ":").split())[:limit]


def item_words(item: dict[str, Any]) -> str:
    """What a review item is about, in the product's own labels ("Date of birth"), never a key or a value: the labels of its facts (short, else label), else the
    item's title; "an answer" when it has neither."""
    labels = [plain(f.get("short") or f.get("label") or "", 60) for f in item.get("facts") or [] if isinstance(f, dict)]
    labels = list(dict.fromkeys(x for x in labels if x)) or [plain(item.get("title") or "", 60)]
    labels = [x for x in labels if x]
    if not labels:
        return "an answer"
    return ", ".join(labels[:3]) + (f" and {len(labels) - 3} more" if len(labels) > 3 else "")


def list_words(keys: Any, limit: int = 3) -> str:
    """"a, b and 2 more": the first few fact keys in words."""
    names = list(dict.fromkeys(words(k) for k in keys if k))
    if len(names) <= limit:
        return " and ".join([", ".join(names[:-1]), names[-1]] if len(names) > 1 else names)
    return ", ".join(names[:limit]) + f" and {len(names) - limit} more"


# -- where ------------------------------------------------------------------------------------------------------------


def base_path(home: str | Path | None = None) -> Path:
    """The ledger's name: I485_EVENTS, else events.jsonl in the firm's data folder (home)."""
    env = os.environ.get("I485_EVENTS")
    return Path(env) if env else Path(home or REPO / "data") / FILE


def month_file(base: str | Path, at: str) -> Path:
    """The file a row written at `at` belongs to: events-YYYY-MM.jsonl beside the ledger's name (the month the row's own time says, in the firm's time)."""
    base = Path(base)
    return base.with_name(f"{base.stem}-{str(at)[:7]}{base.suffix}")


def files(base: str | Path) -> list[Path]:
    """Every month's file of this ledger, oldest first."""
    base = Path(base)
    if not base.parent.is_dir():
        return []
    pattern = re.compile(re.escape(base.stem) + r"-\d{4}-\d{2}" + re.escape(base.suffix) + r"$")
    return sorted(p for p in base.parent.iterdir() if pattern.fullmatch(p.name) and p.is_file())


# -- the chain (what a row's prev and hash are; src/ledger_seal.py reads them back) ---------------------------------------------------------


def row_hash(row: dict[str, Any]) -> str:
    """The SHA-256 of the row's canonical JSON without its hash: keys sorted, no spaces, characters as they are (UTF-8). The same row gives the same hash
    in any program that writes JSON this way (tools/verify_ledger.py is one; so can be a few lines of the firm's own)."""
    body = {k: v for k, v in row.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


# -- a purged case's rows (src/purge.py): blanked in place, so the chain and the daily seals still hold ------------------------------------------------

REDACTED = "redacted"  # the kind of a blanked row: its time, its place in the chain (prev, hash) and the purge's id, nothing else
REDACTIONS = "ledger_redactions.jsonl"  # beside the month files: one line per purge that blanked rows (its id, how many, a digest of their hashes)


def tombstone(row: dict[str, Any], purge_id: str) -> dict[str, Any]:
    """A purged case's row as it stays in the ledger: its time (the day's count of rows is unchanged), its prev and hash (the next row still links to it, the
    seals still hold), the kind "redacted" and the purge's id. Who, the case, the action and the words are gone. src/ledger_seal.py accepts it only where
    the purge's own line in ledger_redactions.jsonl accounts for it."""
    return {"at": row.get("at"), "kind": REDACTED, "purge": purge_id} | {k: row[k] for k in CHAIN if k in row}


def redactions_path(base: str | Path) -> Path:
    return Path(base).with_name(REDACTIONS)


def redaction_digest(hashes: list[str]) -> str:
    """The digest a purge records of the rows it blanked: the SHA-256 of their hashes, in the ledger's order, one a line."""
    return hashlib.sha256("".join(h + "\n" for h in hashes).encode("utf-8")).hexdigest()


def lock_path(base: str | Path) -> Path:
    """The ledger's lock: events.lock beside events.jsonl (not a month's file, so nothing reads it as rows; backups and exports leave locks out)."""
    base = Path(base)
    return base.with_name(base.stem + ".lock")


def _lines_from_end(path: Path) -> Iterator[bytes]:
    """The lines of a file, the last first, without their ends; a block at a time from the end of the file, so the cost does not depend on its length."""
    with open(path, "rb") as f:
        end = f.seek(0, os.SEEK_END)
        carry, size = b"", 4096
        while end > 0:
            start = max(0, end - size)
            f.seek(start)
            parts = (f.read(end - start) + carry).split(b"\n")
            carry = parts[0]  # its beginning may be before this block
            yield from reversed(parts[1:])
            end, size = start, min(size * 2, 65536)
        yield carry


def _head(base: Path, path: Path) -> tuple[str, bool]:
    """(the hash a row appended to `path` links to, whether `path` ends in a row that was cut short). The hash is that of the last row in the order of the files:
    the last of this month's file, else of the month before (an empty file is passed over). "" when there is no row yet or the last row was written before the
    chain."""
    torn = None
    for p in [path, *reversed([q for q in files(base) if q.name < path.name])]:
        if not p.exists():
            continue
        for raw in _lines_from_end(p):
            if torn is None and p == path:
                torn = bool(raw)  # the file's last piece is not empty: the file does not end in a newline
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue  # a cut row has no hash to give; the row before it does
            if isinstance(row, dict):
                h = row.get("hash")
                return (h if isinstance(h, str) else ""), bool(torn)
    return "", bool(torn)


# -- writing ----------------------------------------------------------------------------------------------------------


def record(kind: str, action: str, what: str, *, case: str | Path | None = None, case_dir: str | Path | None = None, home: str | Path | None = None,
           who: str | None = None, role: str | None = None, via: str | None = None, version: int = 1, default_who: tuple[str, str, str] | None = None) -> dict[str, Any] | None:
    """Appends one row. case_dir: the case's folder (data/clients/<id>): the row names the case and the ledger is found beside it (data/events.jsonl).
    case: the case's id when only the id is known (then home: the data folder, or I485_EVENTS, says where the ledger is). A change to the firm's own
    records passes neither.

    who/role/via: when the writer knows the person. Else the acting person (set_actor), else default_who (name, role, via), else "The product".
    Returns the row (None when it could not be written: said on standard error, never raised)."""
    try:
        if kind not in KINDS:
            raise ValueError(f"unknown ledger kind {kind!r}")
        if case_dir is not None:
            case_dir = Path(case_dir)
            case = case_dir.name
            home = home or case_dir.parent.parent
            if (case_dir / "prospect.json").exists():  # a prospect's folder (src/prospects.py): never a case; its rows say "prospect:<id>", so no client's history can show them
                case = f"prospect:{case}"
        now = actor() or {}
        d_who, d_role, d_via = default_who or ("The product", "system", "system")
        person = str(who or "").strip() or now.get("who") or d_who
        mine = now.get("who") == person  # the acting person is this one: their role and way in are known
        row = {"at": "", "who": person,
               "role": role if role in ROLES else (now.get("role") or "staff") if mine else d_role if person == d_who else "staff",
               "via": via if via in VIA else (now.get("via") if mine else None) or d_via, "case": str(case) if case else None, "kind": kind,
               "version": int(version or 1),
               "action": re.sub(r"[^a-z_]", "", str(action or "").lower().replace(" ", "_")) or "changed", "what": " ".join(str(what or "").split())[:MAX_WHAT]}
        base = base_path(home)
        with oslock.locked(lock_path(base), timeout=LOCK_WAIT, poll=0.002):  # the last row's hash and this row's append are one turn
            row["at"] = clock.stamp()  # the time is taken in the turn: the order of the chain is the order of the clock
            path = month_file(base, row["at"])
            path.parent.mkdir(parents=True, exist_ok=True)
            row["prev"], torn = _head(base, path)
            row["hash"] = row_hash(row)
            line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            if torn:  # a row cut short by a crash has no end: this row starts a line of its own (the check says the cut row)
                line = b"\n" + line
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)  # one write of one whole line: two processes never mix rows
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
        return row
    except Exception as exc:  # noqa: BLE001 -- the work that was done stands; the missing line is said, never raised
        sys.stderr.write(f"event ledger not written ({type(exc).__name__}: {exc})\n")
        return None


# -- reading (plain files, no index: the screens use review.oversight.Events; this is for tests, tools and the query layer) -------------


def rows(base: str | Path, *, case: str | None = None) -> Iterator[dict[str, Any]]:
    """Every row of every month, oldest first (a row that is not JSON is skipped)."""
    for path in files(base):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and (case is None or row.get("case") == case):
                    yield row


# -- what changed since a reader last looked (the lists, the search index and the query layer keep up with the cases the ledger names) ------------------------


def _complete(path: Path) -> int:
    """The size of the file up to the end of its last whole row (a row still being written is not counted)."""
    size = path.stat().st_size
    if not size:
        return 0
    with open(path, "rb") as f:
        f.seek(size - 1)
        if f.read(1) == b"\n":
            return size
        end = size
        while end > 0:  # back, a block at a time, to the last row's end: a row of any length that is still being written is not counted
            start = max(0, end - 65536)
            f.seek(start)
            cut = f.read(end - start).rfind(b"\n")
            if cut >= 0:
                return start + cut + 1
            end = start
    return 0


class Tail:
    """Which cases the ledger has named since this reader last looked. One Tail for each reader (each keeps its own position). start() takes the position
    as of now, without reading a row; take() then gives the cases named by the rows appended since, and moves on. It exists so that a screen at
    1,800 cases asks the ledger "what changed?" (a stat of one file and the new rows) instead of looking at every case's files.

    positions: {a month's file name: the byte it has been read to}, kept by a reader that wants to survive a restart (the lists' own file does)."""

    def __init__(self, base: str | Path, positions: dict[str, int] | None = None):
        self.base = Path(base)
        self.positions = dict(positions) if positions is not None else None

    def start(self) -> dict[str, int]:
        self.positions = {p.name: _complete(p) for p in files(self.base)}
        return dict(self.positions)

    def take(self) -> set[str] | None:
        """The case ids of the rows appended since the last look. None when there is no position yet or a file is shorter than it was (cut or replaced):
        the reader looks at every case. A row for the firm's own records names no case and counts for nothing."""
        if self.positions is None:
            return None
        found: set[str] = set()
        seen: dict[str, int] = {}
        for path in files(self.base):
            have = self.positions.get(path.name)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if have is None:
                have = 0  # a new month's file: every row in it is new
            if have > size:
                return None
            seen[path.name] = have
            if size == have:
                continue
            with open(path, "rb") as f:
                f.seek(have)
                at = have
                for raw in f:
                    if not raw.endswith(b"\n"):
                        break  # a row still being written: the next look takes it whole
                    at += len(raw)
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("case"):
                        found.add(str(row["case"]))
            seen[path.name] = at
        if set(self.positions) - {p.name for p in files(self.base)}:
            return None  # a month's file is gone
        self.positions = seen
        return found
