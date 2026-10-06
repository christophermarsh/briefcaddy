"""The lists' own copy of every case: one entry for each client, kept up to date from the event ledger, so a screen never walks 1,800 case folders.

All clients, My work, What's due, Reports, the client picker and the "who may see which case" test were each built, on every look, by opening a dozen files in each
case folder: seventeen seconds at 2,000 cases on the computer's own disk, and several times that on a Windows disk reached from WSL. Here each case is read once into an
entry (its row on All clients, whether it is restricted and who is named on it, its office, what it has filed, the counts the picker shows), and an entry is read again only
when something says its case changed:

  - the review app's own writes (touch(): its _requery calls it after every change to a case);
  - the event ledger (src/events.py: every writer in the product appends a row naming the case: the job worker, the overnight run, the portal, the importers);
  - the portal's queue (a client who submitted or answered is listed there until the worker has read it);
  - a walk over every case, for a change nobody recorded (a file copied in by hand), in the background, at most every I485_WALK_EVERY seconds (600), and never more often
    than ten times as long as the last one took (src/keepup.py);
  - the overnight run, which warms every entry in a pool (overnight.py) and leaves them in data/roster.json; the app adopts that file the moment it is newer.

With I485_WALK_EVERY=0 (the tests) none of this applies: every ask walks every case first, exactly as the screens did before, and nothing is kept.

What a person may see is decided here and nowhere else for the lists: scope() and rows() leave a restricted case out for anyone who is not an attorney and not named on it
(src/restricted.py: the same test as visible_to), and a case the ledger names is read again before the next list is built, so a case that becomes restricted is gone from
a paralegal's lists at the next ask.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import clock
import events
import keepup
import restricted

from . import overview as ov
import schema_path

VERSION = 4  # 2: attorney items; 3: day plan; 4: staff assignment summary. Other versions are walked again.
GAP = 0.5  # seconds between looks at the ledger (I485_ROSTER_GAP: a test that changes a case and asks at once sets 0)
BUDGET = 1.5  # seconds a list request spends reading changed cases itself (I485_ROSTER_BUDGET); the rest are read in the background
MAX_INLINE = 40  # and at most this many cases
PUBLISH = 50  # the first reading of an unread firm lists what it has read every this many cases
FIRST_WORKERS = 2  # and reads with this many threads, so a sign-in or a case page meanwhile is not kept waiting


REPO = Path(__file__).resolve().parents[2]


def default_catalog():
    """(field map, template, Catalog) as the review app loads them by default (its --field-map, --template and --policy)."""
    from fill import load_field_map
    from review.state import Catalog

    schemas = schema_path.ROOT
    field_map = load_field_map(schema_path.path("field_map", "i485", schemas))
    template = schema_path.path("template", "i485", schemas)
    policy = schema_path.path("law", "policy_sijs", schemas)
    policies = json.loads(policy.read_text(encoding="utf-8"))["policies"] if policy.exists() else []
    return field_map, template, Catalog(field_map, template, policies)


def _broken(case: str, exc: Exception) -> dict[str, Any]:
    """The entry for a folder that could not be read: a row that says so (one broken folder must not hide the others)."""
    return {"row": {"id": case, "error": type(exc).__name__}, "closed": False, "named": [], "has_case": False, "held": False, "check_only": False, "tasks_only": False, "unwritten": False, "conflict_held": None,
            "messages_on": None, "office": None, "filed": {}, "built": {}, "pick": None, "ts": None, "day": None,
            "assignment": {"state": "unavailable", "revision": None, "assignee": None, "audit_pending": True}}


def _build_chunk(args: tuple) -> dict[str, dict[str, Any]]:
    """A pool worker: the entries for these clients, read in a process of its own."""
    data_root, portal_root, ids = args
    field_map, template, catalog = default_catalog()
    roster = Roster(Path(data_root), field_map, template, catalog, Path(portal_root) if portal_root else None)
    out = {}
    for case in ids:
        try:
            got = roster.build(case)
        except Exception as exc:  # noqa: BLE001 -- one broken folder must not hide the others
            got = _broken(case, exc)
        if got is not None:
            out[case] = got
    return out


def warm(data_root: str | Path, portal_root: str | Path | None = None, workers: int | None = None) -> "Roster":
    """The overnight run's step: every client's entry read in a pool of processes (a case's row costs a third of a second to build and the pipeline's own libraries
    are not thread-safe to share), the ledger's position taken first, and the result saved in data/roster.json for the review app to adopt. Returns the roster."""
    from concurrent.futures import ProcessPoolExecutor

    roster = Roster(Path(data_root), {}, "", None, Path(portal_root) if portal_root else None)
    tail = events.Tail(roster.base())
    tail.start()
    ids = roster._ids()
    n = max(1, min(workers or max(1, (os.cpu_count() or 2) - 1), 12, max(1, len(ids) // 20)))
    entries: dict[str, dict[str, Any]] = {}
    if n == 1:
        entries = _build_chunk((str(data_root), str(portal_root) if portal_root else None, ids))
    else:
        chunks = [(str(data_root), str(portal_root) if portal_root else None, ids[i::n]) for i in range(n)]
        import multiprocessing

        # spawned, not forked: the review app (or a test) has threads running, and a fork of a threaded process can hand a worker a lock held by a thread that is not there
        with ProcessPoolExecutor(max_workers=n, mp_context=multiprocessing.get_context("spawn")) as pool:
            for part in pool.map(_build_chunk, chunks):
                entries |= part
    roster.adopt(dict(sorted(entries.items())), tail)
    return roster


def saved(out_root: str | Path) -> dict[str, dict[str, Any]] | None:
    """The entries the overnight run's timelines step has just saved (data/roster.json), when that copy is of this version and today's; None otherwise (the caller reads the folders
    itself). For the steps that follow the timelines in the same night (the staff's reminders), so they do not read every case's files again."""
    path = Path(os.environ["I485_ROSTER"]) if os.environ.get("I485_ROSTER") else Path(out_root).resolve().parent / "roster.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != VERSION or data.get("row_version") != ov.ROW_VERSION or data.get("day") != clock.today().isoformat() \
            or data.get("clients") != str(Path(out_root).resolve()):
        return None
    return data["entries"] if isinstance(data.get("entries"), dict) else None


def _plain_id(case: str) -> bool:
    return bool(case) and Path(case).name == case and case not in (".", "..")


class Roster:
    def __init__(self, data_root: Path, field_map: dict, template: str | Path, catalog, portal_root: Path | None = None, hold_unrecorded=None):
        self.data_root = Path(data_root)
        self.hold = hold_unrecorded  # the app's: a portal client of a protected kind whose folder holds no restriction record yet is recorded now, or kept out (False)
        self.field_map, self.template, self.catalog = field_map, template, catalog
        self.portal_root = Path(portal_root) if portal_root else None
        self.path = Path(os.environ["I485_ROSTER"]) if os.environ.get("I485_ROSTER") else self.data_root.resolve().parent / "roster.json"
        self.entries: dict[str, dict[str, Any]] = {}
        self.ready = False
        self.day: str | None = None
        self.tail: events.Tail | None = None
        self.queue_seen: dict[str, int] = {}
        self.lock = threading.RLock()
        self.dirty: set[str] = set()  # cases the review app itself changed: read again before the next list
        self.later: set[str] = set()  # cases the ledger named that the list did not wait for
        self.checked = 0.0
        self.last_walk = 0.0
        self.took = 0.0
        self.saved_at = 0.0
        self.unsaved = False
        self.file_mtime = 0
        self.thread: threading.Thread | None = None
        self.want_walk = False
        self.first = threading.Lock()  # the first reading of every case happens once, however many requests arrive together
        self.inflight: set[str] = set()  # cases one request took off the list and is reading again: every other request's list waits for them, within its budget
        self.settled = threading.Condition(self.lock)
        self.version = 0  # raised whenever an entry is added, replaced or removed
        self._names: tuple[int, tuple[set[str], set[str]]] | None = None
        self._missed: dict[str, float] = {}  # names asked about that were not there, and when
        self.first_walk: threading.Thread | None = None  # Implementation note.
        self.reading = {"done": 0, "total": 0}  # how far that first reading has got: the lists show what is read, and say how many are still being read
        self.walk_started = 0.0
        self.walks = 0  # full walks so far (a test checks that asking for a list does not cause one)
        self.reads = 0  # cases read into an entry so far (the same)

    # -- the switch ------------------------------------------------------------------------------------------------

    @property
    def production(self) -> bool:
        """The ledger keeps the entries up to date (I485_WALK_EVERY above 0). Otherwise every ask walks every case, as it did before."""
        return keepup.walk_every() > 0

    def base(self) -> Path:
        return events.base_path(self.data_root.resolve().parent)

    # -- one case ----------------------------------------------------------------------------------------------------

    def build(self, case: str) -> dict[str, Any] | None:
        """The entry for one client, read from its folders: None when there is no case folder with a case file, no portal client and no restriction record."""
        d = self.data_root / case
        has_case = (d / "fact_graph.json").exists()
        check_only = not has_case and (d / "conflict_check.json").exists()  # a client not processed yet whose folder holds the conflict check (src/conflicts.py) and perhaps nothing else
        # (not src/prospects.py's prospects: people who called and are not clients; their folders, data/prospects/, are never listed here)
        tasks_only = not has_case and (d / "deadlines_set.json").exists()  # a client with no case file yet whose folder holds notes and tasks (src/case_notes.py): the lists show their tasks
        folder = (self.portal_root / "clients" / case) if self.portal_root else None
        in_portal = bool(folder and (folder / "profile.json").exists())
        unwritten = False
        if self.hold is not None and not has_case and in_portal and not (d / restricted.FILE).exists():  # any portal client of a protected kind with no record: written now or closed
            unwritten = self.hold(case) is False  # a protected kind with no record that could not be written: closed to everyone (fail closed)
        held = not has_case and (d / restricted.FILE).exists()
        if not (has_case or in_portal or held or check_only or tasks_only):
            return None
        rows: dict[str, dict[str, Any]] = {}
        if has_case:
            rows[case] = ov.case_row(d, self.field_map, self.template, self.catalog)
        if in_portal:
            ov.add_portal(rows, folder)
        row = rows.get(case)  # None for a client held with a restriction record and nothing else: no row on All clients (as overview() never made one), but it is hidden from those who may not see it
        if row is not None:
            ov.fill_name(row, self.data_root)
            ov.settle(row)
            ended = None
            try:
                import engagement

                ended = engagement.end_info(d) or engagement.conflict_declined(d)  # declined, withdrawn, transferred, closed, or declined after the conflict search
            except Exception:  # noqa: BLE001 -- a record that cannot be read leaves the case open
                pass
            if ended:
                row["end"] = ended
        closed, named, messages_on = False, [], None
        if has_case or held:
            rec = restricted.record(d)
            named = [str(p.get("email") or "") for p in rec["people"]]
            closed = restricted.is_restricted(d)
            messages_on = bool((rec["messages"] or {}).get("on")) if closed else None
        conflict_held = None
        if check_only or (d / "conflict_check.json").exists():
            try:
                import conflicts

                conflict_held = conflicts.hold_words(d) if conflicts.held(d) else None  # the conflict check waits for an attorney's decision: no invitation until then
            except Exception:  # noqa: BLE001
                conflict_held = None
        entry: dict[str, Any] = {"row": row, "closed": closed, "named": named, "has_case": has_case, "held": held, "check_only": check_only, "tasks_only": tasks_only, "unwritten": unwritten,
                                 "conflict_held": conflict_held, "messages_on": messages_on,
                                 "office": None, "filed": {}, "built": {}, "pick": None,
                                 "ts": clock.parse(row["last_activity"]).timestamp() if row and row.get("last_activity") else None}
        import case_assignment

        entry["assignment"] = case_assignment.summary(d)
        if has_case:
            import offices
            from review import reports

            entry["office"] = offices.for_case(d, (row.get("summary") or {}).get("state"))["name"]
            entry["filed"], entry["built"] = reports._filed(d), reports._built(d)
            entry["pick"] = self._pick(d, row or {})
            import client_case
            import client_reminders

            entry["feedback"], entry["reminders"] = client_case.read_feedback(d), client_reminders.read(d)["sent"]  # what Reports counts of the client side (brief I3): a file each, read once here
            import approvals

            entry["approvals"] = approvals.case_items(d, row or {}, self.field_map, self.template, self.catalog)  # what only an attorney may approve on this case (the My approvals screen)
            entry["day"] = self._day(d, row or {}, entry["approvals"])  # what holds the packet and who holds each thing (Today, src/day_plan.py)
        self.reads += 1
        return entry

    @staticmethod
    def _day(d: Path, row: dict[str, Any], approvals: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The case's day plan (src/day_plan.py), unless the case is not one the list is for: a row that could not be read, a case that ended, a case filed."""
        if "error" in row or row.get("end") or row.get("stage") == "filed":
            return None
        import day_plan

        try:
            return day_plan.summary(d, row, approvals)
        except Exception as exc:  # noqa: BLE001 -- one case's plan must not hide its row
            sys.stderr.write(f"the day plan of a case was not made ({type(exc).__name__})\n")
            return None

    def _pick(self, d: Path, row: dict[str, Any]) -> dict[str, Any]:
        """The "Choose a client" line: the person, the kind of case, the decisions made and the blocking and review flags the last report counted."""
        report = (d / "flag_report.txt").read_text(encoding="utf-8") if (d / "flag_report.txt").exists() else ""
        counts = {}
        for level in ("BLOCKING", "REVIEW"):
            marker = report.find(f"{level} (")
            counts[level.lower()] = int(report[marker + len(level) + 2: report.find(")", marker)]) if marker >= 0 else None
        name, kind = ov.name_and_kind(d)
        return {"id": d.name, "name": name, "kind": kind, "decisions": row.get("decided") or 0, **counts}

    # -- keeping up ---------------------------------------------------------------------------------------------------

    def touch(self, case: str) -> None:
        """The review app changed this case: it is read again before the next list."""
        if _plain_id(case):
            with self.lock:
                self.dirty.add(case)

    def task_folders(self) -> list[Path]:
        """The folders of clients with no case file yet that hold notes and tasks (src/case_notes.py unprocessed_rows asks: not a look at every folder for it)."""
        self.sync()
        with self.lock:
            return [self.data_root / c for c, e in sorted(self.entries.items()) if e.get("tasks_only")]

    def touch_all(self) -> None:
        """A change that alters every row (the firm's settings, its policies, a rule's approval): every case is read again, in the background (the rows already held are served until then)."""
        with self.lock:
            self.want_walk = True

    def _ids(self) -> list[str]:
        """Every client there may be an entry for: the case folders, the folders that hold only a restriction record, and the portal's clients."""
        names: set[str] = set()
        for root in (self.data_root, self.portal_root / "clients" if self.portal_root else None):
            if root is not None and root.is_dir():
                with os.scandir(root) as it:
                    names |= {e.name for e in it if e.is_dir()}
        return sorted(names)

    def walk(self, parallel: bool = True, progressive: bool = False) -> dict[str, dict[str, Any]]:
        """Reads every client again. In production the position in the ledger is taken first, so what changes while it runs is picked up at the next look.
        progressive: the first reading of a firm with no saved copy: what is read is listed as it comes (a new copy of the entries every PUBLISH cases or
        half second), so a list never waits for the whole firm."""
        started = time.monotonic()
        tail = None
        if self.production:
            tail = events.Tail(self.base())
            tail.start()
        ids = self._ids()
        entries: dict[str, dict[str, Any]] = {}
        if progressive:
            with self.lock:
                self.reading = {"done": 0, "total": len(ids)}
        published = [time.monotonic(), 0]

        def one(case: str) -> None:
            try:
                got = self.build(case)
            except Exception as exc:  # noqa: BLE001 -- one broken folder must not hide the others
                got = _broken(case, exc)
            if got is not None:
                entries[case] = got
            if progressive:
                with self.lock:
                    self.reading["done"] += 1
                    if self.reading["done"] - published[1] >= PUBLISH or time.monotonic() - published[0] > 0.5:
                        self.entries = dict(sorted(entries.items()))  # a new dict each time: a list being built meanwhile keeps the one it took
                        self.version += 1
                        published[:] = [time.monotonic(), self.reading["done"]]

        if parallel and len(ids) > 40:  # the first reading in the background takes fewer threads: the app keeps answering the people signing in meanwhile
            with ThreadPoolExecutor(max_workers=FIRST_WORKERS if progressive else min(16, (os.cpu_count() or 4) * 2)) as pool:
                list(pool.map(one, ids))
        else:
            for case in ids:
                one(case)
        with self.lock:
            self.entries = dict(sorted(entries.items()))
            self.version += 1
            self.walks += 1
            self.day = clock.today().isoformat()
            self.took = time.monotonic() - started
            self.last_walk = time.monotonic()
            self.want_walk = False
            if tail is not None:
                self.tail = tail
                self.queue_seen = self._queue()
                self.dirty.clear()
                self.later.clear()
            self.ready = True
        if tail is not None:
            self.save()
        return self.entries

    def adopt_entries(self, entries: dict[str, dict[str, Any]], tail: events.Tail) -> None:
        """Entries read elsewhere (the overnight run's pool) become this roster's, and are saved."""
        with self.lock:
            self.entries = entries
            self.version += 1
            self.day = clock.today().isoformat()
            self.tail = tail
            self.queue_seen = self._queue()
            self.dirty.clear()
            self.later.clear()
            self.last_walk = time.monotonic()
            self.ready = True
        self.save()

    adopt = adopt_entries

    def _queue(self) -> dict[str, int]:
        """The clients the portal still lists as waiting for the worker (a client who answered, uploaded or submitted), with when each was listed."""
        folder = self.portal_root / "queue" if self.portal_root else None
        out: dict[str, int] = {}
        if folder is not None and folder.is_dir():
            with os.scandir(folder) as it:
                for e in it:
                    try:
                        out[e.name] = e.stat().st_mtime_ns
                    except OSError:
                        continue
        return out

    def save(self) -> None:
        """What the walk found, kept in data/roster.json so the next start (and the app, after the overnight run's own) begins from it."""
        if not self.production or self.tail is None or self.tail.positions is None:
            return
        try:
            tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
            with self.lock:
                data = {"version": VERSION, "row_version": ov.ROW_VERSION, "clients": str(self.data_root.resolve()), "day": self.day, "saved": clock.stamp(),
                        "positions": self.tail.positions, "entries": self.entries}
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # person data (every case's name, A-Number, date of birth, restricted cases included): the owner only
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(json.dumps(data, default=str))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            self.file_mtime = self.path.stat().st_mtime_ns
            self.saved_at = time.monotonic()
            self.unsaved = False
        except OSError as exc:
            sys.stderr.write(f"the lists' copy was not saved ({type(exc).__name__})\n")

    def _ours(self, data: Any) -> bool:
        """A saved copy of this version, made for this data folder (two apps on different folders must not adopt each other's entries)."""
        return (isinstance(data, dict) and data.get("version") == VERSION and data.get("row_version") == ov.ROW_VERSION and isinstance(data.get("entries"), dict)
                and data.get("clients") == str(self.data_root.resolve()))

    def _load(self) -> bool:
        """The saved copy, when it is of this version; the ledger since then is taken from where it stopped."""
        try:
            stat = self.path.stat()
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not self._ours(data):
            return False
        with self.lock:
            self.entries = data["entries"]
            self.version += 1
            self.day = data.get("day")
            self.tail = events.Tail(self.base(), data.get("positions") or {})
            self.queue_seen = self._queue()
            self.file_mtime = stat.st_mtime_ns
            self.last_walk = time.monotonic() - max(0.0, time.time() - stat.st_mtime)  # as old as the saved copy: a night old and a walk is due, a minute old and none is
            self.ready = True
        return True

    def sync(self) -> None:
        """Brings the entries up to date before a list is built: reads again the cases changed since (the review app's own writes first, then what the ledger and the
        portal's queue name), within a budget; the rest, and any walk that is due, wait for the background."""
        if not self.production:
            self.walk(parallel=False)
            return
        if not self.ready:
            with self.first:  # Implementation note.
                if not self.ready and self.first_walk is None and not self._load():
                    self.first_walk = threading.Thread(target=self._walk_first, daemon=True, name="roster-first-reading")
                    self.walk_started = time.monotonic()
                    self.first_walk.start()
                reading = self.first_walk is not None
            if reading:  # the reading took the ledger's position when it started: nothing else to read now, and the next ask looks at the ledger at once
                end = self.walk_started + float(os.environ.get("I485_ROSTER_BUDGET", BUDGET))  # counted from the start of the reading: only the first asks wait
                while not self.ready and time.monotonic() < end:  # a small firm is read within the budget; a large one is listed as it is read
                    time.sleep(0.02)
                return
        now = time.monotonic()
        todo: list[str] = []
        with self.lock:
            due = now - self.checked >= float(os.environ.get("I485_ROSTER_GAP", GAP))  # the looks at the disk (the saved copy, the ledger, the queue, the day) are at most this often: a page that asks
            if due and not self.dirty and self.path.exists() and self.path.stat().st_mtime_ns > self.file_mtime:  # a hundred times (each hit of a search) pays for one
                self._adopt()
            todo = sorted(self.dirty)
            self.dirty.clear()
            if due:
                self.checked = now
                named = self.tail.take() if self.tail is not None else None
                if named is None:
                    self.want_walk = True
                else:
                    todo += sorted(c for c in named if c not in todo and _plain_id(c))
                queue = self._queue()
                todo += sorted(c for c in queue if queue[c] != self.queue_seen.get(c) and c not in todo and _plain_id(c))  # listed since: the client answered, uploaded or submitted
                todo += sorted(c for c in self.queue_seen if c not in queue and c not in todo and _plain_id(c))  # taken off since: the portal's worker has finished reading them
                self.queue_seen = queue
                if clock.today().isoformat() != self.day or now - self.last_walk > max(keepup.walk_every(), 10 * self.took):
                    self.want_walk = True
            self.inflight |= set(todo)  # taken off the list: a list another request builds meanwhile waits for them (below)
        end = time.monotonic() + float(os.environ.get("I485_ROSTER_BUDGET", BUDGET))  # (a test that changes cases and looks at once sets it high: nothing is left for the background)
        done = 0
        try:
            for case in todo:
                if done >= MAX_INLINE or time.monotonic() > end:
                    with self.lock:
                        self.later |= set(todo[done:])
                    break
                self._reread(case)
                done += 1
        finally:
            with self.settled:
                self.inflight -= set(todo)
                self.settled.notify_all()
        if done:
            with self.lock:
                self.unsaved = True
        with self.settled:  # a case another request is still reading again (its own write, a row the ledger named) is listed here too, unless the budget runs out first
            while self.inflight and time.monotonic() < end:
                self.settled.wait(timeout=max(0.0, end - time.monotonic()))
        self._background()

    def _walk_first(self) -> None:
        try:
            self.walk(progressive=True)
        except Exception as exc:  # noqa: BLE001 -- the next list starts it again
            sys.stderr.write(f"the first reading of the cases stopped ({type(exc).__name__}); the next list starts it again\n")
            with self.first:
                self.first_walk = None

    def still_reading(self) -> int:
        """How many cases the first reading of an unread firm has still to read: 0 once every case is listed."""
        with self.lock:
            return 0 if self.ready else max(0, self.reading["total"] - self.reading["done"]) or (1 if self.first_walk is not None else 0)

    def _adopt(self) -> None:
        """The overnight run (or another copy of the app) left a newer file: its entries and its place in the ledger are taken."""
        try:
            stat = self.path.stat()
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if self._ours(data):
            self.entries = data["entries"]
            self.version += 1
            self.day = data.get("day")
            self.tail = events.Tail(self.base(), data.get("positions") or {})
            self.file_mtime = stat.st_mtime_ns
            self.last_walk = time.monotonic() - max(0.0, time.time() - stat.st_mtime)

    def _reread(self, case: str) -> None:
        try:
            got = self.build(case)
        except Exception as exc:  # noqa: BLE001 -- the old entry stays; the next walk tries again
            sys.stderr.write(f"a case was not read again ({type(exc).__name__})\n")
            return
        with self.lock:
            if got is None:
                self.entries.pop(case, None)
            else:
                self.entries[case] = got
            self.version += 1

    def _background(self) -> None:
        with self.lock:
            if (not self.later and not self.want_walk and not self._save_due()) or (self.thread and self.thread.is_alive()):
                return
            self.thread = threading.Thread(target=self._drain, name="the lists", daemon=True)
            self.thread.start()

    def _save_due(self) -> bool:
        return self.unsaved and time.monotonic() - self.saved_at > 300

    def _drain(self) -> None:
        try:
            while True:
                with self.lock:
                    pending, self.later = sorted(self.later), set()
                    walk = self.want_walk
                for case in pending:
                    self._reread(case)
                if walk:
                    self.walk()
                elif self._save_due():
                    self.save()
                with self.lock:
                    if not self.later and not self.want_walk:
                        return
        except Exception as exc:  # noqa: BLE001 -- the lists are as they were; the next ask tries again
            sys.stderr.write(f"the lists were not brought up to date ({type(exc).__name__})\n")

    def wait(self, seconds: float = 120.0) -> None:
        """Until the background has finished, the first reading of an unread firm included (a test). On an unread firm whose first reading has not
        started yet (no list was asked for), it is started here, so a caller that waits before its first request still gets every case."""
        if self.production and not self.ready and self.first_walk is None:
            self.sync()
        if self.first_walk is not None:
            self.first_walk.join(seconds)
        t = self.thread
        if t is not None:
            t.join(seconds)

    # -- what the lists ask ---------------------------------------------------------------------------------------

    def names(self, looking_for: str | None = None) -> tuple[set[str], set[str]]:
        """(the case folders with a case file, the clients with only a restriction record), as of the last look (production only; callers fall back to the disk otherwise).
        looking_for: a name asked about that is not among them: a folder of exactly that name may have been made by something that wrote no ledger row, so that one folder
        (never a walk) is read now, at most once in five seconds for a name."""
        self.sync()
        with self.lock:
            known = looking_for is None or looking_for in self.entries
        if not known:
            self.look_for(looking_for)
        with self.lock:
            if self._names is None or self._names[0] != self.version:
                self._names = (self.version, ({c for c, e in self.entries.items() if e["has_case"]}, {c for c, e in self.entries.items() if e["held"] or e.get("check_only")}))
            return self._names[1]

    def look_for(self, case: str) -> None:
        if not _plain_id(case):
            return
        now = time.monotonic()
        with self.lock:
            if now - self._missed.get(case, -1e9) < 5:
                return
            if len(self._missed) > 1000:
                self._missed = {c: t for c, t in self._missed.items() if now - t < 5}
            self._missed[case] = now
        try:
            exact = case in os.listdir(self.data_root) or bool(self.portal_root and case in os.listdir(self.portal_root / "clients"))  # the exact name: a disk that ignores case would find another
        except OSError:
            return
        if exact:
            self._reread(case)

    @staticmethod
    def _named(who: dict[str, Any] | None, entry: dict[str, Any]) -> bool:
        if (who or {}).get("role") == "support":  # never named on a restricted case (src/restricted.py _named)
            return False
        email = str((who or {}).get("email") or "").strip().lower()
        return bool(email) and email in entry["named"]

    def hides(self, who: dict[str, Any] | None, entry: dict[str, Any]) -> bool:
        """restricted.visible_to, from the entry: an attorney, or nobody signed in, sees every case; anyone else does not see a restricted case they are not named on."""
        if who is None or who.get("role") == "attorney":
            return False
        return (entry["closed"] and not self._named(who, entry)) or bool(entry.get("unwritten"))  # unwritten: fail closed, for everyone but an attorney

    def scope(self, who: dict[str, Any] | None) -> dict[str, Any]:
        """restricted.scope, from the entries: {"hidden": case ids this person may not see, "confidential": the ids whose confidential documents they may see, None for all}."""
        if who is None or who.get("role") == "attorney":
            return {"hidden": set(), "confidential": None}
        self.sync()
        hidden, confidential = set(), set()
        with self.lock:
            for case, e in self.entries.items():
                if e.get("unwritten"):
                    hidden.add(case)
                    continue
                if not (e["has_case"] or e["held"]):
                    continue
                if self._named(who, e):
                    confidential.add(case)
                elif e["closed"]:
                    hidden.add(case)
        return {"hidden": hidden, "confidential": confidential}

    def kind_of(self, case: str) -> tuple[bool, str | None]:
        """(whether the case is in the copy, its kind of case as the "Choose a client" line says it): a search's hits name theirs without opening each case's files."""
        with self.lock:
            e = self.entries.get(case)
        return (True, ((e or {}).get("pick") or {}).get("kind")) if e is not None else (False, None)

    def ended(self, case: str) -> dict[str, Any] | None:
        """How a case ended (closed, declined, withdrawn, transferred), from its row; None for an open one."""
        with self.lock:
            e = self.entries.get(case)
        return ((e or {}).get("row") or {}).get("end")

    def sees_confidential(self, who: dict[str, Any] | None, case: str) -> bool:
        if who is None or who.get("role") == "attorney":
            return True
        with self.lock:
            e = self.entries.get(case)
        return bool(e) and self._named(who, e)

    def rows(self, who: dict[str, Any] | None, attorney_view: bool | None = None, *, with_progress: bool = False):
        """Every client this person may see, as All clients' rows (the ones overview() made, with each marked restricted or not, its office and, for an attorney or
        nobody signed in, whether a restricted case sends messages), their days quiet worked out now. A row's keys beginning with an underscore are for the app's
        own lists (what the case filed, what it built) and are never sent to a screen."""
        self.sync()
        now = clock.now().timestamp()
        everyone = who is None or who.get("role") == "attorney"
        out = []
        with self.lock:
            items = sorted(self.entries.items(), key=lambda kv: (not kv[1]["has_case"], kv[0]))
            pending = self.still_reading()
        for case, e in items:
            if e["row"] is None or self.hides(who, e):
                continue
            row = dict(e["row"])
            if e.get("conflict_held"):
                row["conflict_held"] = e["conflict_held"]
            if e["has_case"] or e["held"]:
                row["restricted"] = e["closed"]
                if e["closed"] and not e["has_case"]:
                    row["held_note"] = restricted.INVITE_HELD  # no invitation goes to this client: the office hands the link over
                if e["closed"] and everyone:  # the attorney's filter "Messages on (restricted)": no one else is told
                    row["messages_on"] = bool(e["messages_on"])
            if e["office"]:
                row["office"] = e["office"]
            row["idle_days"] = int((now - e["ts"]) // 86400) if e["ts"] is not None else None
            row["_filed"], row["_built"], row["_has_case"] = e["filed"], e["built"], e["has_case"]
            row["_feedback"], row["_reminders"] = e.get("feedback") or [], e.get("reminders") or {}
            out.append(row)
        return (out, pending) if with_progress else out

    def assignment_page(self, who: dict[str, Any], accounts: Callable, query: dict | None = None) -> dict:
        """A bounded staff list using cached entries and one fresh account load.

        Pending rereads are excluded: their former ACL/assignment snapshot must
        not supply authorization. Detail/mutation routes still check live files.
        A committed assignment without a ledger event propagates through local
        touch(), successful retry/Tail, or the existing background periodic walk.
        """
        from review.case_lists import page

        self.sync()
        current_accounts = accounts()
        with self.lock:
            pending = self.dirty | self.later | self.inflight
            entries = {c: e for c, e in self.entries.items() if c not in pending}
            version = self.version
        result = page(entries, who, current_accounts, query or {})
        return result | {"roster_version": version}

    def approvals(self, who: dict[str, Any] | None, *, with_progress: bool = False):
        """(case id, the client's name, the case's open attorney items) for each open case that has any and that this reader may be told of: the stricter rule
        than the lists' own. A restricted case's items are for the attorneys named on it and no one else, an attorney not named included; an ended case has none.
        Nobody signed in (no accounts) sees all."""
        self.sync()
        with self.lock:
            items = sorted(self.entries.items())
            pending = self.still_reading()
        out = []
        for case, e in items:
            if not e.get("approvals") or e["row"] is None or e["row"].get("end") or e.get("unwritten"):
                continue
            if who is not None and e["closed"] and not self._named(who, e):
                continue
            out.append((case, ((e["row"].get("summary") or {}).get("name") or (e.get("pick") or {}).get("name")), e["approvals"]))
        return (out, pending) if with_progress else out

    def day_plans(self, who: dict[str, Any] | None, *, with_progress: bool = False):
        """(case id, the client's name, the case's day plan, its row) for each open case that has a plan and that this reader may be told of: the stricter rule than the
        lists' own (as approvals() has it). A restricted case is for the people named on it and no one else, an attorney not named included; nobody signed in sees all."""
        self.sync()
        with self.lock:
            items = sorted(self.entries.items())
            pending = self.still_reading()
        out = []
        for case, e in items:
            if not e.get("day") or e["row"] is None or e.get("unwritten"):
                continue
            if who is not None and e["closed"] and not self._named(who, e):
                continue
            out.append((case, (e["row"].get("summary") or {}).get("name") or (e.get("pick") or {}).get("name"), e["day"], e["row"]))
        return (out, pending) if with_progress else out

    def told(self, who: dict[str, Any] | None) -> Callable[[str], bool]:
        """Whether this reader may be told of a case in the attorney's queue (the same stricter rule as approvals(): a case not in the copy is not told)."""
        self.sync()
        with self.lock:
            hidden = {c for c, e in self.entries.items() if who is not None and (e["closed"] and not self._named(who, e) or e.get("unwritten"))}
            known = set(self.entries)
        return lambda case: case in known and case not in hidden

    def picks(self, who: dict[str, Any] | None) -> list[dict[str, Any]]:
        """The "Choose a client" list: the cases this person may see."""
        self.sync()
        with self.lock:
            items = sorted(self.entries.items())
        return [e["pick"] | {"restricted": e["closed"]} for case, e in items if e["pick"] and not self.hides(who, e)]
