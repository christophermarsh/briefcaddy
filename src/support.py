"""Support access (brief R3): the provider's support person, let in by an attorney for hours, read-only, the clients masked.

WHO. A third role, "support", that the Staff section can never give (review/auth.py ROLES): an attorney with the second factor set up lets one person
in from Settings, Let support in: their name and e-mail (the provider's), the hours (1 to 8) and whether client values are shown plain for this session
(off unless ticked). The product shows a one-time sign-in code for the attorney to pass on by their own means, and never sends it. The session ends by
itself at its hour (review/auth.py checks it on the server at every request), and any attorney ends it early in one click.

WHAT IT CAN DO. Read, nothing else: every POST is refused to support but signing out (review/server.py), restricted cases and confidential documents are
absent for it however the session was opened (restricted._named: support is never named on a case), and the portal is not its. Every request it makes is
a row in the event ledger (kind "support", via "support", under the support person's name) and in the access log, written before anything is sent.

THE MASK (a masked session: the default). Where it lives and why (docs/decisions.md, 10/04/2026): at the server's answer boundary, deny by default.
Every JSON answer passes through View.mask before it is written: every string is replaced by MASK unless its key is one the product fills from its own
words (SAFE: kinds, states, stages, levels, actions, roles, the ledger's sentence, error messages, timestamps, staff names), and even those pass through
a scrubber of every client value the firm holds (src/find.py's Masker over every case's people, fact graph and portal profile, plus the patterns for
dates, numbers, addresses, phones and e-mails) with the staff's own names kept. A number is kept only under a key that counts something. A case id is
replaced wherever it stands, as a value or a key, by "Case N" from a map made for the session (case folders may be named for the client), and the routes
take "Case N" back. Files (scans, PDFs, packets, CSV files, exports, backups) are refused in a masked session. Why at the boundary and not field by
field: the screens' answers are built from many records in many shapes; a field-by-field rule would have to know every one and would leak the first
field it forgot, while a deny-by-default boundary leaks only what a SAFE key carries, and those keys hold the product's own words. What it costs:
support sees the shape of a case (which records exist, their sizes and times, counts), not its content; the routes made for support (health, the
case list, a case's shape) are built from metadata only and are the useful ones.

A plain session (the attorney ticked it, and the ledger says so): the answers are as staff see them, files are sent and logged like any opening; still
read-only, still no restricted case.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import clock
import events

MASK = "•••"
ROLE = "support"
# keys whose string values are the product's own words (scrubbed all the same)
SAFE = frozenset({"kind", "type", "status", "state", "stage", "level", "action", "role", "via", "what", "error", "who", "by", "reviewer", "decided_by",
                  "asked_by", "confirmed_by", "cadence", "owner", "party", "responsible", "record", "store", "health", "result", "line", "words_kind"})
TIME_KEYS = re.compile(r"(^at$|_at$|^built$|^created$|^started$|^finished$|^modified$|^saved$|^last_walk$|^released$|^until$|^signed_in$)")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?([+-]\d{2}:\d{2}|Z)?$")
COUNT_KEYS = re.compile(r"(count|total|^n$|pages?$|size|bytes|files|rows|days|hours|minutes|seconds|version|^level$|steps?$|limit|^per$|blocking|^fix$|"
                        r"^check$|^attorney$|decided|open|done|waiting|running|queued|failed|late|due|overdue|cases|records|items|jobs|age)")
ALIAS = re.compile(r"^Case (\d+)$")
PRODUCT_WORDS = ("case", "cases", "client", "clients", "support", "file", "files", "new", "test")
REFUSED_WRITE = "Support is read-only: it changes nothing in the firm's records."
REFUSED_FILE = "Files are not shown in a masked support session: the attorney who let support in can open it plain if a file must be seen."


class Shown(dict):
    """An answer made for support from metadata only (health, the case list, a case's shape): its strings are scrubbed and its case ids aliased,
    not denied."""


def is_support(user: dict[str, Any] | None) -> bool:
    return bool(user) and user.get("role") == ROLE


def plain(user: dict[str, Any] | None) -> bool:
    return bool(((user or {}).get("support") or {}).get("plain"))


class View:
    """One support session's view of the firm: the map of case ids to "Case N", and the scrubber of client values."""

    def __init__(self, app, user: dict[str, Any]):
        self.app, self.user = app, user
        self.session = (user.get("support") or {}).get("id") or ""
        self.plain = plain(user)
        self.lock = threading.Lock()
        self.alias: dict[str, str] = {}
        self.real: dict[str, str] = {}
        self._masker = None
        self._masker_at = 0.0
        self._all_ids: set[str] = set()
        self._number()

    # -- the case map ------------------------------------------------------------------------------------------------------------------------

    def _ids(self) -> set[str]:
        root = Path(self.app.data_root)
        ids = {p.name for p in root.iterdir() if p.is_dir()} if root.is_dir() else set()
        portal = getattr(self.app, "portal_root", None)
        if portal is not None and (Path(portal) / "clients").is_dir():
            ids |= {p.name for p in (Path(portal) / "clients").iterdir() if p.is_dir()}
        return ids - {"", ".", ".."}

    def _number(self) -> None:
        """Application helper with evidence-bound inputs."""
        ids = self._ids()
        with self.lock:
            self._all_ids = ids | set(self.alias)
            for case in sorted(ids - set(self.alias)):
                if self.app.may_open(self.user, case) or self.app.accounts is None:
                    n = f"Case {len(self.alias) + 1}"
                    self.alias[case], self.real[n] = n, case

    def unalias(self, value: Any) -> str:
        """The case id a "Case N" names, for a route (an unknown one names nothing: the route says the case does not exist)."""
        text = str(value or "")
        if text in self.real:
            return self.real[text]
        if ALIAS.match(text):
            self._number()
            return self.real.get(text, "\0none")
        return "\0none"  # a real id typed by hand is no way in: a masked session takes the numbers only

    def name_of(self, case: str) -> str:
        if case not in self.alias:
            self._number()
        return self.alias.get(case, MASK)

    # -- the scrubber ------------------------------------------------------------------------------------------------------------------------

    def masker(self):
        """A Masker of every client value the firm holds (every case, restricted ones too), the staff's names kept; made again every ten minutes."""
        if self._masker is not None and time.monotonic() - self._masker_at < 600:
            return self._masker
        import find

        names: list[str] = []
        values: list[str] = []
        root = Path(self.app.data_root)
        for case in sorted(self._all_ids):
            d = root / case
            if d.is_dir():
                try:
                    n, v = find.masker_inputs(d)
                    names += n
                    values += v
                except Exception:  # noqa: BLE001 -- the patterns still mask
                    pass
            portal = getattr(self.app, "portal_root", None)
            if portal is not None:
                prof = _read(Path(portal) / "clients" / case / "profile.json")
                names += [str(prof.get("name") or "")]
                values += [str(prof.get(k) or "") for k in ("email", "phone")]
        # kept: the staff's names (they stay plain), and the product's own words a case folder's name may hold ("case-0042")
        keep = [str(u.get("name") or "") for u in (self.app.accounts.users() if self.app.accounts is not None else [])] + list(PRODUCT_WORDS)
        self._masker = find.Masker([n for n in names if n], [v for v in values if v], keep=keep)
        self._masker_at = time.monotonic()
        return self._masker

    def scrub(self, text: str) -> str:
        """A string the product wrote, with every case id named by its number and every client value the firm holds masked."""
        if text in self.alias:
            return self.alias[text]
        out = text
        for case in sorted(self._all_ids, key=len, reverse=True):
            if case and case in out:
                out = re.sub(rf"(?<![\w-]){re.escape(case)}(?![\w-])", self.name_of(case), out)
        return self.masker()(out)

    # -- the answer ----------------------------------------------------------------------------------------------------------------------------

    def mask(self, obj: Any, key: str = "", shown: bool = False) -> Any:
        """An answer as a masked session may see it (the module docstring)."""
        if isinstance(obj, Shown):
            shown = True
        if isinstance(obj, dict):
            return {self._key(str(k)): self.mask(v, str(k), shown) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.mask(x, key, shown) for x in obj]
        if isinstance(obj, bool) or obj is None:
            return obj
        if isinstance(obj, (int, float)):
            return obj if shown or COUNT_KEYS.search(key or "") else MASK
        if isinstance(obj, str):
            if obj in self.alias:
                return self.alias[obj]
            if obj in self.real:  # "Case N" itself
                return obj
            if obj in self._all_ids:
                return MASK  # a case this session may not open: its id is a value too
            if TIME_KEYS.search(key or ""):
                return obj if TIMESTAMP.match(obj) else MASK  # a time the system wrote; a bare date is a client's (birth, document, deadline)
            if shown or key in SAFE:
                return self.scrub(obj)
            return MASK
        return MASK

    def _key(self, k: str) -> str:
        if k in self.alias:
            return self.alias[k]
        if k in self._all_ids:
            return MASK
        return self.scrub(k)


def view(app, user: dict[str, Any]) -> View:
    """The session's View, kept on the app for the session (a new session numbers the cases afresh)."""
    views = app.__dict__.setdefault("_support_views", {})
    sid = (user.get("support") or {}).get("id") or ""
    v = views.get(sid)
    if v is None:
        v = views[sid] = View(app, user)
    return v


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# -- the record of every request -------------------------------------------------------------------------------------------------------------


def note(app, user: dict[str, Any], method: str, path: str, case: str | None, refused: str = "") -> None:
    """One request by support, in the ledger (under the support person's name, via support) and the access log, before anything is answered."""
    what = f"Support asked for {path}" + (f" ({method})" if method != "GET" else "") + (f": refused, {refused}" if refused else "")
    events.record("support", "request", what, case=case or None, home=Path(app.data_root).resolve().parent, who=user.get("name"), role=ROLE, via="support")
    if app.accounts is not None:
        app.accounts.log("support_request", user.get("email") or "", method=method, path=path, **({"client": case} if case else {}),
                         **({"refused": refused} if refused else {}))


# -- what support can see: the health of the install, the case list, a case's shape ---------------------------------------------------------------


def health(app) -> Shown:
    """The install's health, from metadata only: the version, the lists' copy's age, the followers' last walks, the backups, the register's overdue
    items, the job queue, the error log's last lines (scrubbed), the ledger's last check."""
    import version as version_mod

    home = Path(app.data_root).resolve().parent
    now = clock.utcnow()
    out: dict[str, Any] = {"version": getattr(version_mod, "VERSION", ""), "released": getattr(version_mod, "RELEASED", ""), "at": clock.stamp()}
    roster = getattr(app, "roster", None)
    saved = _read(Path(roster.path)).get("saved") if roster is not None else None
    out["roster"] = {"saved_at": saved, "age_minutes": round((now - clock.parse(saved)).total_seconds() / 60) if clock.parse(saved) else None,
                     "cases": len(getattr(roster, "entries", {}) or {})}
    walks = []
    for name, mod in (("search index", "index"), ("find across the firm", "find"), ("query layer", "query")):
        try:
            m = __import__(mod)
            for f in (getattr(m, "_FOLLOWERS", {}) or {}).values():
                walks.append({"store": name, "walks": f.walks, "last_walk_minutes": round((time.monotonic() - f.last_walk) / 60) if f.last_walk else None})
        except Exception:  # noqa: BLE001
            pass
    out["followers"] = walks
    try:
        import backups

        log = backups.read_log()
        b, r = log.get("last_backup") or {}, log.get("last_test_restore") or {}
        out["backups"] = {"last_backup_at": b.get("at"), "complete": b.get("complete"), "cases": (b.get("counts") or {}).get("cases"),
                          "last_test_restore_at": r.get("at"), "test_restore_ok": r.get("ok")}
    except Exception:  # noqa: BLE001
        out["backups"] = {}
    try:
        import maintenance

        due = [i for i in maintenance.status() if i["due"]]
        out["register"] = {"overdue": len(due), "items": [{"record": i["id"], "cadence": i["cadence"], "owner": i["owner"]} for i in due[:50]]}
    except Exception:  # noqa: BLE001
        out["register"] = {}
    try:
        import jobs

        root = jobs.folder_for(app.data_root)
        listed = jobs.jobs(root, recent=86400)
        out["jobs"] = {"queued": sum(1 for j in listed if j.get("state") == "queued"), "running": sum(1 for j in listed if j.get("state") == "running"),
                       "failed": sum(1 for j in listed if j.get("state") == "failed"), "done": sum(1 for j in listed if j.get("state") == "done"),
                       "kinds": sorted({str(j.get("kind")) for j in listed})}
        log_file = root / "worker.log"
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-40:] if log_file.is_file() else []
        out["error_log"] = [{"line": x} for x in lines]
    except Exception:  # noqa: BLE001
        out["jobs"], out["error_log"] = {}, []
    try:
        import ledger_seal

        kept = ledger_seal.kept(events.base_path(home)) or {}
        out["ledger"] = {"result": kept.get("line"), "checked_at": kept.get("at")}
    except Exception:  # noqa: BLE001
        out["ledger"] = {}
    out["stores"] = [{"store": name, "bytes": (home / name).stat().st_size, "modified": _mtime(home / name)}
                     for name in ("index.db", "query.db", "find.db", "roster.json", "learning.db") if (home / name).is_file()]
    return Shown(out)


def _mtime(path: Path) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")


def cases(app, user: dict[str, Any]) -> Shown:
    """The case list as support sees it (the "Choose a client" rows of the cases it may open): "Case N" (the id in a plain session), the kind of
    case, the counts of blocking and review items and of decisions, the files in the folder; never a name."""
    v = view(app, user)
    out = []
    for r in app.clients(user):
        case = r.get("id")
        if not case or not app.may_open(user, case):
            continue
        d = Path(app.data_root) / case
        out.append({"case": case if v.plain else v.name_of(case), "kind": r.get("kind") if v.plain else None,
                    "blocking": r.get("blocking"), "review": r.get("review"), "decisions": r.get("decisions"),
                    "files": sum(1 for p in d.rglob("*") if p.is_file()) if d.is_dir() else 0})
    return Shown({"cases": out, "total": len(out), "plain": v.plain})


def case_shape(app, user: dict[str, Any], case: str) -> Shown:
    """One case's shape: which records exist (the catalog's own titles), how many files and bytes each has and when each last changed, the counts of
    its documents, decisions, notes, tasks and deadlines, the overnight run's state for it; never a value."""
    import records

    d = Path(app.data_root) / case
    v = view(app, user)
    present = []
    for rec in records.RECORDS:
        if rec["area"] != "case":
            continue
        files = [p for pat in rec["files"] for p in d.glob(pat) if p.is_file()]
        if files:
            present.append({"record": rec["id"], "kind": rec["title"], "files": len(files), "bytes": sum(p.stat().st_size for p in files),
                            "modified": max(_mtime(p) for p in files)})
    docs = (_read(d / "documents.json").get("documents") or [])
    decisions = _read(d / "decisions.json")
    notes = _read(d / "notes.json").get("notes") or []
    tasks = [x for x in (_read(d / "deadlines_set.json").get("deadlines") or []) if isinstance(x, dict) and x.get("task")]
    state = _read(Path(app.data_root).resolve().parent / "batch_state.json").get(case) or {}
    shape = {"case": v.name_of(case) if not v.plain else case, "records": present,
             "documents": len(docs), "documents_by_type": [{"type": t, "count": sum(1 for x in docs if isinstance(x, dict) and x.get("type") == t)}
                                                           for t in sorted({str(x.get("type")) for x in docs if isinstance(x, dict)})],
             "decisions": len(decisions) if isinstance(decisions, dict) else 0, "notes": len(notes), "tasks": len(tasks),
             "overnight": {"status": state.get("status"), "at": state.get("at"), "seconds": state.get("seconds")}}
    return Shown(shape)
