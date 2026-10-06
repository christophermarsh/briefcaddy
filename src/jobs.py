"""The job queue: the reading and the slow work that the review app and the portal used to do while a person waited.

A scan the office adds, a photo a client sends, "Read the inbox now", a notice placed on a case and Clio's webhook saying a matter changed (a "clio" job,
which waits a few minutes so a batch of uploads is one read) are written down as a job and answered at once; one worker process reads them, one at a
time, and the screen shows how far it is.

    data/jobs/                 (I485_JOBS moves it) one JSON file for each job waiting or running: <time>-<kind>-<id>.json
    data/jobs/done/            the finished ones (read, could not be read, did not finish), kept 14 days
    data/jobs/locks/           one lock file for each case
    data/jobs/worker.lock      held by the worker for as long as it lives: the way to know there is one (the system lets go of it when the process dies)
    data/jobs/worker.json      who the worker is and when it last looked for work (for people; nothing depends on it)

A job: {id, kind, client, by, state, created, started, finished, progress: {step, steps, text}, args, result, error, pid}. state is queued, running, done,
failed (it ran and could not finish: the file is on the case and the overnight run reads it) or died (the process stopped with the job running: the next start
marks it "Did not finish: will be read tonight" and puts back what the screen said was being read).

The worker (python src/jobs.py --worker ...) is started by the installer's unit and task beside the overnight run, and by the review app itself when no worker holds
the lock, so a laptop that has only the app still reads. An app-started worker leaves with the app (it watches its parent). Never two workers: the second one
cannot take worker.lock and leaves.

One lock, shared: a job holds the case's lock (case_lock) for as long as it writes to the case; the overnight run holds it while it works on that case; the review
app's own decisions and edits take it for the moment of the write. So a photo read, a staff upload and a night's run on the same case take turns, and no write is
lost. A request that cannot get the lock in a few seconds says the case is being read and to try again in a moment (CaseBusy).

This module reads and writes files only. The pipeline's own libraries load inside a handler, in the worker, never in the web process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from weakref import WeakKeyDictionary
from typing import Any, Callable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
if __name__ == "__main__":  # run as a program, the code the pipeline imports as "jobs" must be this very module (one set of held locks), not a second copy
    sys.modules.setdefault("jobs", sys.modules["__main__"])

import clock  # noqa: E402
import events  # noqa: E402
import oslock  # noqa: E402  (the operating system's own lock: fcntl.flock, msvcrt.locking on Windows)
from law_app.adapters.queue.filesystem import (  # noqa: E402
    JOB_FILE_PATTERN, FilesystemJobQueries, FilesystemJobPublication, active_files, read_record,
)
from law_app.ports.job_queries import JobQueries  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
KEEP_DAYS = 14  # a finished job's file is kept this long
RECENT = 600  # seconds: a finished job is still on the case's screen ("Read") this long
POLL = 1.0  # seconds between looks for work
BUSY_WAIT = 8.0  # seconds a request waits for a case's lock before it says the case is being read
NAME = JOB_FILE_PATTERN

# What each kind of job is called on the screen, and the words of its steps.
KINDS = {"portal_process": "The client's answers and documents", "portal_upload": "A photo the client sent", "staff_upload": "A scan the office added", "inbox_read": "The notice inbox", "inbox_place": "A notice placed on a case",
         "clio": "What Clio said changed", "purge": "A case purged at the end of its keeping period",
         "restore_drill": "The restore drill"}
CLIO_AGAIN = 300.0  # seconds before a "clio" job that found another Clio step running (the overnight run's, Sync now, Send now) tries again
CLIO_TRIES = 24  # times it tries (two hours of a long overnight Clio step); then it is done and tonight's step reads the list
LABELS = {"queued": "Waiting to be read", "running": "Reading now", "done": "Read", "failed": "Could not be read: it will be read tonight",
          "died": "Did not finish: will be read tonight"}
DIED = "Did not finish: will be read tonight"
DRILL_LABELS = {"queued": "Waiting to start", "running": "Running now", "done": "Finished", "failed": "The restore drill could not run just now",
                "died": "The restore drill did not finish: it runs again on the first night of the month"}  # the drill is no reading


class Again(Exception):
    """A handler's "not now": the job goes back to waiting, due `after` seconds from now, as the same job (no finished record for each try)."""

    def __init__(self, after: float):
        super().__init__(f"again in {after:.0f} s")
        self.after = after


class CaseBusy(Exception):
    """The case's lock is held by a reading, a staff upload or the overnight run, and did not come free in time."""

    def __init__(self, message: str = "This case is being read right now. Try again in a moment."):
        super().__init__(message)


# -- where ---------------------------------------------------------------------------------------------------------------


def folder_for(clients_root: str | Path | None = None) -> Path:
    """The jobs folder: I485_JOBS, else jobs beside the case folders (data/jobs)."""
    env = os.environ.get("I485_JOBS")
    return Path(env) if env else Path(clients_root or REPO / "data" / "clients").resolve().parent / "jobs"


def _setup(root: Path) -> None:
    """The folder and its two inside it, for the owner only (a job holds a client's name and the file names of their documents)."""
    for sub in ("", "done", "locks"):
        folder = root / sub
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(folder, 0o700)
        except OSError:
            pass


def _write(path: Path, data: Any) -> None:
    if isinstance(data, dict) and data.get("kind") == "portal_process" and data.get("operation_scope"):
        _durable_write(path, data)
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # person data: the owner only (the same rule as the query layer's file)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(json.dumps(data, indent=1, ensure_ascii=False))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _read(path: Path) -> dict[str, Any] | None:
    return read_record(path)


# -- locks ---------------------------------------------------------------------------------------------------------------


_try_lock, _unlock = oslock.try_lock, oslock.unlock


_held = threading.local()  # the case locks this thread holds: asking again for one it holds is a no-op (a job calls code that locks the case too)


@contextmanager
def case_lock(root: str | Path, case: str, timeout: float | None = None) -> Iterator[None]:
    """Holds the case's lock for the block. timeout: seconds to wait before CaseBusy (None: as long as it takes). Held by the job worker while it
    writes to a case, by the overnight run while it works on one, and for the moment of a write by the review app."""
    held = getattr(_held, "names", None)
    if held is None:
        held = _held.names = set()
    key = (str(root), case)
    if key in held:
        yield
        return
    folder = Path(root) / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (hashlib.sha256(case.encode("utf-8")).hexdigest()[:24] + ".lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    end = None if timeout is None else time.monotonic() + timeout
    try:
        while not _try_lock(fd):
            if end is not None and time.monotonic() >= end:
                raise CaseBusy()
            time.sleep(0.05)
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)
            _unlock(fd)
    finally:
        os.close(fd)


def alive(root: str | Path) -> bool:
    """A worker is running: its lock is held by someone else."""
    root = Path(root)
    path = root / "worker.lock"
    if not path.exists():
        return False
    fd = os.open(path, os.O_RDWR)
    try:
        if _try_lock(fd):
            _unlock(fd)
            return False
        return True
    finally:
        os.close(fd)


# -- the queue -----------------------------------------------------------------------------------------------------------


def _durable_write(path, value):
    from portal.queue_bridge import _atomic
    client = value.get("client")
    if value.get("kind") != "portal_process" or not isinstance(client, str) or not client:
        raise ValueError("Invalid portal job staging identity")
    _atomic(path, value, stage_prefix="portal-job-" + hashlib.sha256(client.encode()).hexdigest())


def _published(root, job):
    """New portal jobs are inert until their matching reservation is published."""
    if job.get("kind") != "portal_process":
        return True
    scope = job.get("operation_scope")
    if not isinstance(scope, str) or not re.fullmatch(r"[0-9a-f]{64}", scope):
        return False
    proof = _read(Path(root) / ("operation-" + scope + ".json"))
    payload = hashlib.sha256(json.dumps(job.get("args") or {}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not proof or not isinstance(proof.get("created"), str):
        return False
    try:
        if datetime.fromisoformat(proof["created"]).tzinfo is None:
            return False
    except ValueError:
        return False
    return bool(proof and proof.get("recovery_version") == 1 and proof.get("state") == "published"
                and proof.get("id") == job.get("id") and proof.get("kind") == job.get("kind")
                and proof.get("client") == job.get("client") and proof.get("payload") == payload)


def submit(root: str | Path, kind: str, client: str | None = None, by: str = "", args: dict[str, Any] | None = None, operation_id: str | None = None, *, recover_reserved: bool = False) -> dict[str, Any]:
    """Writes the job down and returns it; the caller answers at once. client: the case the job writes to (None for the notice inbox as a whole)."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind of job {kind!r}")
    if kind == "portal_process" and not recover_reserved:
        raise ValueError("Portal processing requires a recoverable generation reservation")
    if recover_reserved and (kind != "portal_process" or operation_id is None):
        raise ValueError("Recoverable reservations are only for portal queue processing")
    root = Path(root)
    _setup(root)
    if operation_id is not None:
        return filesystem_job_binding(root).publication.reserve_and_publish(
            kind, client, by, args, operation_id, recover_reserved=recover_reserved)
    stamp = f"{time.time_ns():020d}"
    job = {"id": f"{stamp}-{kind}-{secrets.token_hex(4)}", "kind": kind, "client": client, "by": by or None, "state": "queued", "created": clock.stamp(), "started": None,
           "finished": None, "progress": {"step": 0, "steps": 0, "text": ""}, "args": args or {}, "result": None, "error": None, "pid": None}
    _write(root / f"{job['id']}.json", job)
    return job


def submit_once(root: str | Path, kind: str, client: str | None = None, by: str = "", args: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Writes the job down unless one of this kind (for this case) is already waiting: two events at once make one job (a lock around the look and the
    write, between threads and processes). Returns the new job, or None when one was waiting."""
    root = Path(root)
    _setup(root)
    with oslock.locked(root / "submit.lock", timeout=10.0):  # each call opens the file anew: another thread of this process waits too
        if any(j.get("state") == "queued" for j in jobs(root, client=client, kind=kind, recent=0)):
            return None
        return submit(root, kind, client, by, args)


def _due(job: dict[str, Any]) -> bool:
    """A job is due unless its arguments say not before a time (a "clio" job waits a few minutes, so a batch of Clio's events is one read)."""
    try:
        return float((job.get("args") or {}).get("not_before") or 0) <= time.time()
    except (TypeError, ValueError):
        return True


def _active(root: Path) -> list[Path]:
    return active_files(root, NAME)


def _queries(root: str | Path) -> JobQueries:
    # Look up compatibility hooks at call time; do not capture monkeypatches,
    # clock state or a worker Context's mutable root in a cached adapter.
    return FilesystemJobQueries(root, reader=lambda path: _read(path),
                                active=lambda path: _active(path), pattern=NAME,
                                now=lambda: time.time())


class JobConfigurationConflict(RuntimeError):
    """Execution lacks an unchanged, trusted reader/publication composition."""


@dataclass(frozen=True, eq=False)
class _FilesystemJobBinding:
    root: Path
    queries: FilesystemJobQueries
    publication: FilesystemJobPublication
    profile: str = "filesystem-v1"


_job_bindings = WeakKeyDictionary()


def filesystem_job_binding(root: str | Path):
    """Compose approved dynamic facade hooks for one root; performs no I/O.

    Only this composition issues execution bindings. Matching metadata on an
    arbitrary reader does not confer execution capability or tenant authority.
    """
    root = Path(root)
    queries = _queries(root)
    publication = FilesystemJobPublication(
        root, read=lambda path: _read(path), write=lambda path, value: _write(path, value),
        durable_write=lambda path, value: _durable_write(path, value),
        get=lambda jid: get(root, jid),
        locked=lambda path, timeout: oslock.locked(path, timeout=timeout),
        stamp=lambda: clock.stamp(), time_ns=lambda: time.time_ns(), pattern=lambda: NAME)
    binding = _FilesystemJobBinding(root, queries, publication)
    _job_bindings[binding] = (root, root.absolute(), binding.profile, queries, publication,
                              vars(queries).copy(), vars(publication).copy())
    return binding


def _require_execution_binding(ctx):
    binding = getattr(ctx, "job_binding", None)
    try:
        issued = _job_bindings.get(binding) if type(binding) is _FilesystemJobBinding else None
    except TypeError:
        issued = None
    if issued is not None:
        root, configured_root, profile, queries, publication, query_hooks, publication_hooks = issued
        if (binding.root == root and binding.profile == profile
                and binding.queries is queries and binding.publication is publication
                and root.absolute() == configured_root
                and Path(ctx.root).absolute() == configured_root
                and vars(queries) == query_hooks and vars(publication) == publication_hooks
                and (ctx.job_queries is None or ctx.job_queries is queries)):
            return binding
    raise JobConfigurationConflict("Job execution requires its unchanged trusted filesystem binding")


def get(root: str | Path, job_id: str) -> dict[str, Any] | None:
    """One job by its id, waiting, running or finished."""
    return _queries(root).get(job_id)


def jobs(root: str | Path, client: str | None = None, kind: str | None = None, recent: float = RECENT) -> list[dict[str, Any]]:
    """The jobs waiting or running, and those finished in the last `recent` seconds, oldest first; of one case (client) or one kind when asked."""
    return _queries(root).list_jobs(client, kind, recent)


def view(job: dict[str, Any], with_result: bool = False) -> dict[str, Any]:
    """What a screen is told of a job, in words: never its arguments (they hold file names) and, unless asked, never its result."""
    p = job.get("progress") or {}
    state = job.get("state") or "queued"
    labels = DRILL_LABELS if job["kind"] == "restore_drill" else ({**LABELS, "failed": "Processing incomplete; the intake queue is retained", "died": "Processing stopped; the intake queue is retained"} if job["kind"] == "portal_process" else LABELS)
    if _protected_intake(job):
        labels = {**labels, "failed": "Processing incomplete; retained documents require follow-up", "died": "Processing stopped; retained documents require follow-up"}
    out = {"id": job["id"], "kind": job["kind"], "what": KINDS.get(job["kind"], "A job"), "state": state, "label": labels.get(state, state),
           "step": p.get("step") or 0, "steps": p.get("steps") or 0, "text": p.get("text") or "", "created": job.get("created"), "finished": job.get("finished"),
           "client": job.get("client")}
    if state in ("failed", "died"):
        out["why"] = labels["died"] if state == "died" and (job["kind"] in {"restore_drill", "portal_process"} or _protected_intake(job)) else DIED if state == "died" else (job.get("error") or labels["failed"])
    if job.get("kind") == "portal_process" and state == "done" and (job.get("result") or {}).get("superseded"):
        out["label"] = "Replaced by a newer intake update"
    if with_result:
        out["result"] = job.get("result")
    return out


def counts(root: str | Path) -> dict[str, int]:
    """For My work: how many jobs are waiting and how many are running (no case named)."""
    live = [j for j in (_read(p) for p in _active(Path(root))) if not j or j.get("kind") != "restore_drill"]  # (the drill is no reading: My work says "Reading now" of readings)
    return {"waiting": sum(1 for j in live if j and j.get("state") == "queued"), "running": sum(1 for j in live if j and j.get("state") == "running")}


def pending(root: str | Path, client: str) -> bool:
    """A job for this case is waiting or running."""
    return any(j.get("state") in ("queued", "running") for j in jobs(root, client=client, recent=0))


@contextmanager
def gpu_lock(root: str | Path | None = None) -> Iterator[None]:
    """One use of the vision model at a time, across every process (the overnight run's workers, the job worker): the model is one GPU's, and two readings
    loading it together fill its memory. Waits as long as it takes."""
    root = Path(root) if root else folder_for()
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / "gpu.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while not _try_lock(fd):
            time.sleep(0.1)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


# -- the worker ----------------------------------------------------------------------------------------------------------


class Context:
    """Where the worker's handlers find the firm's folders: the case folders, and the client portal's. clio_transport: Clio's HTTP layer for the "clio" job
    (the tests' simulated Clio; None: the network)."""

    def __init__(self, clients: str | Path, portal: str | Path | None = None, clio_transport=None, *, jobs_root=None, use_policies=True,
                 job_queries: JobQueries | None = None, job_binding=None):
        self.clients = Path(clients).absolute()
        self.portal = Path(portal).absolute() if portal else None
        self.root = Path(jobs_root).absolute() if jobs_root is not None else folder_for(self.clients)
        self.use_policies = bool(use_policies)
        self.clio_transport = clio_transport
        self.job_queries = job_queries
        self.job_binding = filesystem_job_binding(self.root) if job_binding is None else job_binding

    def store(self):
        from portal.store import PortalStore

        if self.portal is None:
            raise LookupError("no portal folder")
        return PortalStore(self.portal)


class WorkerStopped(Exception):
    """A stop requested while waiting leaves the durable job queued."""


@contextmanager
def installation_gate(ctx):
    """Acquire before case locks; only acquisition timeouts are retried.

    Long readers serialize canonical installation mutations. No running row is
    written while waiting. Request-side gates keep their existing time limits.
    """
    from portal.communication_consent import data_gate
    while True:
        guard = data_gate(ctx.clients.parent)
        try:
            guard.__enter__()
        except TimeoutError:
            stopped = getattr(ctx, "wait_stop", None)
            if stopped and stopped():
                raise WorkerStopped()
            time.sleep(0.2)
            continue
        break
    try:
        yield
    finally:
        guard.__exit__(*sys.exc_info())


def _current_cached_job(ctx, cached):
    jid = cached.get("id")
    if not isinstance(jid, str) or not NAME.fullmatch(jid + ".json"):
        return None
    queries = getattr(ctx, "job_queries", None)
    current = queries.get(jid, active_only=True) if queries is not None else _read(ctx.root / (jid + ".json"))
    if (not current or current.get("state") not in {"queued", "running"}
            or any(current.get(key) != cached.get(key) for key in ("id", "kind", "client", "args", "operation_scope"))):
        return None
    return current


def _finish(root: Path, job: dict[str, Any], state: str, **fields: Any) -> None:
    filesystem_job_binding(root).publication.finish(job, state, fields)


def _died(ctx: Context, job: dict[str, Any]) -> None:
    """Puts back what the screen said about a reading that never finished: the client's photos are for tonight."""
    _require_execution_binding(ctx)
    if job.get("kind") == "portal_upload" and ctx.portal is not None and job.get("client"):
        try:
            ctx.store().mark_reading(job["client"], "tonight")
        except JobConfigurationConflict:
            raise
        except Exception:  # noqa: BLE001 -- the client may be gone; the label is a convenience
            pass


def sweep(clients_root: str | Path, portal_root: str | Path | None = None) -> list[str]:
    """Marks every job that was running when its worker stopped as "Did not finish: will be read tonight" (and the photos it was reading as for tonight).
    Run when the app or the worker starts, and whenever a screen asks while no worker is alive. Returns the cases it marked (the lists read them again)."""
    ctx = Context(clients_root, portal_root)
    _require_execution_binding(ctx)
    root = ctx.root
    if not root.exists() or alive(root):
        return []
    marked = []
    for path in _active(root):
        job = _read(path)
        if job and job.get("state") == "running":
            if _sweep_cached(ctx, job):
                marked.append(job.get("client") or "")
    return [c for c in marked if c]


def _next(root: Path) -> dict[str, Any] | None:
    for path in _active(root):
        job = _read(path)
        if job and job.get("state") == "queued" and _due(job) and _published(root, job):
            return job
    return None


def _repair_portal(ctx, job):
    _require_execution_binding(ctx)
    if job.get("kind") != "portal_process":
        return False
    from portal.queue_bridge import repair_completed
    from portal.communication_consent import data_gate
    # Acquisition errors must propagate; callers may not fall back to ungated
    # terminal writes. Completion is inside the same gate and case lock.
    with data_gate(ctx.clients.parent), case_lock(ctx.root, job["client"], timeout=10):
        if ctx.clients.parent.name.casefold() == "data" and _current_cached_job(ctx, job) is None:
            return False
        try:
            result = repair_completed(ctx, job)
        except (ValueError, OSError, LookupError, TimeoutError):
            return False
        if result is not None:
            _require_execution_binding(ctx)
            _finish(ctx.root, job, "done", result=result)
            return True
    return False


def _sweep_cached(ctx, cached, *, worker=False):
    _require_execution_binding(ctx)
    from portal.communication_consent import data_gate
    retrying = worker and getattr(ctx, "wait_stop", None) is not None
    guard = installation_gate(ctx) if retrying else data_gate(ctx.clients.parent)
    case = cached.get("client")
    with guard, (case_lock(ctx.root, case, timeout=None if retrying else 10) if case else _nothing()):
        current = _current_cached_job(ctx, cached)
        if current is None or current.get("state") != "running":
            return False
        if _protected_intake(current):
            return _sweep_drive(ctx, current)
        if _repair_portal(ctx, current):
            return True
        _require_execution_binding(ctx)
        _finish(ctx.root, current, "died", error=DIED)
        _died(ctx, current)
        return True


def _protected_intake(job):
    return job.get("kind") == "drive_intake" or (job.get("kind") == "staff_upload" and isinstance(job.get("args"), dict) and "staff_receipt" in job["args"])


def run_job(ctx: Context, job: dict[str, Any]) -> dict[str, Any]:
    _require_execution_binding(ctx)
    if (job.get("kind") in {"portal_process", "portal_upload"}
            or job.get("kind") == "staff_upload" and isinstance(job.get("args"), dict) and "staff_receipt" in job["args"]):
        return _read_upload_without_writer_gate(ctx, job)
    with installation_gate(ctx):
        return _run_current_job(ctx, job)


def _read_upload_without_writer_gate(ctx, job):
    """OCR immutable byte snapshots outside writer locks, then recheck authority.

    New arrivals are warmed before applying the case. Original bytes, receipt,
    current actor and lifecycle are checked again under the existing gates.
    The operation-local text cache is discarded even on failure.
    """
    _require_execution_binding(ctx)
    from classify.classifier import page_cache, cached_pdf_pages
    from review.staff_upload_recovery import validate_for_read, _read, _safe
    import hashlib
    with page_cache() as cache:
        while True:
            with installation_gate(ctx), case_lock(ctx.root, job["client"]):
                current = _current_drive(ctx, job) if _protected_intake(job) else _current_cached_job(ctx, job)
                if current is None:
                    return _drive_unavailable(job)
                try:
                    if current["kind"] == "staff_upload":
                        validate_for_read(ctx.clients, ctx.store() if ctx.portal else None, job["client"], current["args"], current.get("by") or "")
                        receipt = _read(ctx.clients / job["client"] / "staff-upload-receipts" / (current["args"]["staff_receipt"] + ".json"))
                        folder = _safe(Path(receipt["folder"]))
                    else:
                        from portal import queue_bridge
                        store = ctx.store()
                        queue_bridge._open_case(ctx, store, current["client"])
                        if current["kind"] == "portal_process":
                            store, receipt_path = queue_bridge._paths(ctx, current["client"], current["args"].get("generation"))
                            identity = queue_bridge._identity(ctx, current["client"], current["args"].get("generation"))
                            receipt = queue_bridge._read(receipt_path, identity)
                            queue_bridge._bind_job(ctx, current, receipt, identity)
                            marker = queue_bridge.generation(store, current["client"])
                            if (receipt["state"] in {"processed", "acked"}
                                    or marker is None or marker["generation"] != current["args"].get("generation")):
                                return _run_current_job(ctx, current)  # Settle superseded generations without OCR.
                        elif not any(row.get("status") == "received" and not row.get("source")
                                     for row in store.uploads(current["client"])):
                            return _run_current_job(ctx, current)
                        import source_association
                        if (ctx.clients / current["client"] / source_association.FILE).exists():
                            folder = source_association.engine_sources(ctx.clients.parent.parent, store, current["client"])[0]
                        else:
                            folder = _safe(store.client_dir(current["client"]) / "uploads")
                    pending = []
                    paths = ([folder / current["args"]["name"]]
                             if current["kind"] == "staff_upload" and current["args"].get("case")
                             else sorted(folder.glob("*.pdf")))
                    for path in paths:
                        data = _safe(path).read_bytes()
                        if hashlib.sha256(data).hexdigest() not in cache:
                            pending.append(data)
                            break  # Bound temporary PDF memory to one original.
                except JobConfigurationConflict:
                    raise
                except Exception:
                    return _run_current_job(ctx, current)  # Existing handler records an authority/source failure.
                if not pending:
                    return _run_current_job(ctx, current)
                if current.get("state") == "queued":
                    _require_execution_binding(ctx)
                    current.update(state="running", started=clock.stamp(), pid=os.getpid(),
                                   progress={"step": 1, "steps": 2, "text": "Reading document text"})
                    _write(ctx.root / (current["id"] + ".json"), current)
            for data in pending:
                try:
                    cached_pdf_pages(data)
                except JobConfigurationConflict:
                    raise
                except Exception:
                    pass  # The normal handler records the cached reading failure.


def _run_current_job(ctx: Context, job: dict[str, Any]) -> dict[str, Any]:
    _require_execution_binding(ctx)
    if not _protected_intake(job):
        return _run_job(ctx, job)
    case = job.get("client")
    if not isinstance(case, str) or not case:
        return _drive_unavailable(job)
    # Includes initial running/progress and final writes, not just the handler.
    # A cached row may have been removed by Q1 while this worker waited.
    with installation_gate(ctx), case_lock(ctx.root, case):
        current = _current_drive(ctx, job)
        if current is None:
            return _drive_unavailable(job)
        return _run_job(ctx, current)


def _drive_unavailable(job):
    return {"id": job.get("id"), "kind": job.get("kind"), "state": "unavailable",
            "error": "The durable intake job is unavailable; no work was recreated.",
            "result": {"processed": False, "unavailable": True}}


def _current_drive(ctx, cached):
    jid = cached.get("id")
    if not isinstance(jid, str) or not NAME.fullmatch(jid + ".json"):
        return None
    current = _read(ctx.root / (jid + ".json"))
    if not current or not _protected_intake(current) or current.get("kind") != cached.get("kind") or current.get("client") != cached.get("client") or current.get("id") != jid or current.get("args") != cached.get("args") or current.get("state") not in {"queued", "running"}:
        return None
    return current


def _sweep_drive(ctx, cached):
    _require_execution_binding(ctx)
    from portal.communication_consent import data_gate
    case = cached.get("client")
    if not isinstance(case, str) or not case:
        return False
    with data_gate(ctx.clients.parent), case_lock(ctx.root, case, timeout=10):
        current = _current_drive(ctx, cached)
        if current is None or current.get("state") != "running":
            return False
        _require_execution_binding(ctx)
        _finish(ctx.root, current, "died", error="Intake processing stopped; retained documents require follow-up.")
        return True


def _run_job(ctx: Context, job: dict[str, Any]) -> dict[str, Any]:
    _require_execution_binding(ctx)
    with installation_gate(ctx), (case_lock(ctx.root, job["client"]) if job.get("client") else _nothing()):
        if ctx.clients.parent.name.casefold() == "data":
            current = _current_cached_job(ctx, job)
            if current is None:
                return {"id": job.get("id"), "kind": job.get("kind"), "state": "unavailable",
                        "error": "The durable job is unavailable or changed; no work was recreated."}
            job.update(current)
        return _run_locked_job(ctx, job)


@clock.cached_zone()
def _run_locked_job(ctx: Context, job: dict[str, Any]) -> dict[str, Any]:
    """Runs one job to its end and files it. The handler's work happens under the lock of the case the job names."""
    _require_execution_binding(ctx)
    root = ctx.root
    if not _published(root, job):
        raise ValueError("Portal processing reservation is not published")
    job.update(state="running", started=clock.stamp(), pid=os.getpid())
    path = root / f"{job['id']}.json"
    _write(path, job)

    def progress(step: int, steps: int, text: str) -> None:
        _require_execution_binding(ctx)
        job["progress"] = {"step": step, "steps": steps, "text": text}
        _write(path, job)

    handler = HANDLERS[job["kind"]]
    try:
        with case_lock(root, job["client"]) if job.get("client") else _nothing():
            result = handler(ctx, job, progress)
    except JobConfigurationConflict:
        raise
    except Again as again:  # put back as it was, due later; its tries counted on it
        _require_execution_binding(ctx)
        job.update(state="queued", started=None, pid=None, tries=int(job.get("tries") or 0) + 1, progress={"step": 0, "steps": 0, "text": ""},
                   args=(job.get("args") or {}) | {"not_before": time.time() + again.after})
        _write(path, job)
        return job
    except Exception as exc:  # noqa: BLE001 -- one job's failure never stops the worker; the file is on the case and the night reads it
        sys.stderr.write(f"job {job['id']} failed ({type(exc).__name__}: {exc})\n")
        if _repair_portal(ctx, job):
            return job
        _require_execution_binding(ctx)
        _finish(root, job, "failed", error=_plain(exc))
        try:
            HANDLERS_FAILED.get(job["kind"], lambda *_: None)(ctx, job)
        except JobConfigurationConflict:
            raise
        except Exception:  # noqa: BLE001
            pass
        return job
    try:
        progress(job["progress"].get("steps") or 1, job["progress"].get("steps") or 1, "")
        _finish(root, job, "done", result=result)
        if job.get("client") and not (job.get("kind") == "portal_process" and result.get("processed") is False):  # the last row of the job: after every record it wrote, so the lists that follow the ledger read the case once, finished
            events.record("documents", "read", "Finished reading what was added to the case", case=job["client"], home=ctx.clients.parent,
                          default_who=("The document reader", "system", "system"))
    except JobConfigurationConflict:
        raise
    except Exception as exc:  # noqa: BLE001 -- one job's failure never stops the worker; the file is on the case and the night reads it
        sys.stderr.write(f"job {job['id']} failed ({type(exc).__name__}: {exc})\n")
        if _repair_portal(ctx, job):
            return job
        _require_execution_binding(ctx)
        _finish(root, job, "failed", error=_plain(exc))
        try:
            HANDLERS_FAILED.get(job["kind"], lambda *_: None)(ctx, job)
        except JobConfigurationConflict:
            raise
        except Exception:  # noqa: BLE001
            pass
    return job


@contextmanager
def _nothing() -> Iterator[None]:
    yield


def _plain(exc: Exception) -> str:
    """A failure in words for a screen: a person's own message when the handler gave one (a ValueError), else the standard sentence."""
    return str(exc)[:200] if isinstance(exc, (ValueError, LookupError)) and str(exc) else LABELS["failed"]


def tidy(root: Path) -> None:
    """Removes finished jobs older than KEEP_DAYS."""
    done = root / "done"
    cutoff = time.time() - timedelta(days=KEEP_DAYS).total_seconds()
    if done.is_dir():
        for p in done.iterdir():
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass


def warm() -> None:
    """The pipeline's libraries, loaded now (the first photo is read within seconds, not after the models load)."""
    for name in ("classify", "batch", "inbox", "portal.engine", "review.front_desk"):
        try:
            __import__(name)
        except Exception:  # noqa: BLE001 -- a handler says what it cannot do
            pass


def work(clients_root: str | Path, portal_root: str | Path | None = None, *, once: bool = False, parent: int | None = None, poll: float = POLL,
         log: Callable[[str], None] = print, stop: Callable[[], bool] | None = None, warm_up: bool = False, clio_transport=None, jobs_root=None, use_policies=True) -> int:
    """The worker: takes the worker lock (leaves when there is another worker), marks what a dead worker left, then runs the waiting jobs one at a time until
    stopped. once: until none is due. parent: leave when this process does. Returns how many jobs it ran."""
    ctx = Context(clients_root, portal_root, clio_transport, jobs_root=jobs_root, use_policies=use_policies)
    _require_execution_binding(ctx)
    ctx.wait_stop = lambda: bool((stop and stop()) or (parent and not _pid_alive(parent)))
    root = ctx.root
    _setup(root)
    fd = os.open(root / "worker.lock", os.O_CREAT | os.O_RDWR, 0o600)
    if not _try_lock(fd):
        os.close(fd)
        log("Another worker is running: this one leaves.")
        return 0
    ran = 0
    try:
        try:
            sweep_dead(ctx)
        except WorkerStopped:
            return 0
        if warm_up:
            warm()
        last_tidy, last_beat = 0.0, 0.0
        queue_pass_done = False
        while True:
            if ctx.wait_stop():
                break
            _require_execution_binding(ctx)
            if ctx.portal is not None:
                from portal.opt_out import pump
                try:
                    stopped = pump(ctx)
                    if stopped.get("unresolved"):
                        log("Communication STOP retry remains pending; affected destinations remain held.")
                except (ValueError, OSError, TimeoutError):
                    log("Communication STOP retry state is unavailable; pending destinations remain held.")
            if time.monotonic() - last_beat > 20:  # who the worker is, for a person looking (nothing depends on it)
                _write(root / "worker.json", {"pid": os.getpid(), "beat": clock.stamp(), "parent": parent})
                last_beat = time.monotonic()
            if time.monotonic() - last_tidy > 3600:
                tidy(root)
                last_tidy = time.monotonic()
            if ctx.portal is not None and (not once or not queue_pass_done):
                from portal.queue_bridge import bridge
                bridge(ctx)
                queue_pass_done = True
            job = _next(root)
            if job is not None:
                try:
                    run_job(ctx, job)
                except WorkerStopped:
                    break
                ran += 1
                continue
            if once or (stop and stop()) or (parent and not _pid_alive(parent)):
                break
            time.sleep(poll)
    finally:
        (root / "worker.json").unlink(missing_ok=True)
        _unlock(fd)
        os.close(fd)
    return ran


def sweep_dead(ctx: Context) -> None:
    """For the worker that holds the lock: every job still marked running is a dead worker's."""
    _require_execution_binding(ctx)
    for path in _active(ctx.root):
        job = _read(path)
        if job and job.get("state") == "running":
            _sweep_cached(ctx, job, worker=True)


def _pid_alive(pid: int, platform: str | None = None, kernel32=None) -> bool:
    """Whether a process is running. On Windows os.kill(pid, 0) is not a check: signal 0 is Ctrl+C there, sent to the process's console group. So Windows asks the system
    (OpenProcess and GetExitCodeProcess, through ctypes: no new dependency). platform and kernel32 are for the test, which runs both branches on any computer."""
    if (platform or sys.platform).startswith("win"):
        return _pid_alive_windows(int(pid), kernel32)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _pid_alive_windows(pid: int, kernel32=None) -> bool:
    import ctypes

    k = kernel32 or ctypes.WinDLL("kernel32", use_last_error=True)  # not run on Windows by the builder: written from the documented calls (docs/decisions.md)
    still_active, query_limited, access_denied = 259, 0x1000, 5
    k.OpenProcess.restype = ctypes.c_void_p  # a handle is pointer-sized: the default int would cut it on a 64-bit system
    k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = k.OpenProcess(query_limited, 0, pid)
    if not handle:  # no such process, or one this account may not look at (it exists)
        return (k.last_error if hasattr(k, "last_error") else ctypes.get_last_error()) == access_denied
    try:
        code = ctypes.c_ulong()
        return bool(k.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == still_active
    finally:
        k.CloseHandle(handle)


def ensure_worker(clients_root: str | Path, portal_root: str | Path | None = None) -> bool:
    """Starts a worker of the app's own when none holds the lock (a laptop with only the app still reads); it leaves when this process does.
    True when one is running now or was started. I485_JOBS_WORKER=0 turns it off (the tests run jobs themselves; a host that must not read documents)."""
    if os.environ.get("I485_JOBS_WORKER", "1") == "0":
        return False
    root = folder_for(clients_root)
    _setup(root)
    if alive(root):
        return True
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", "--data", str(clients_root), "--parent", str(os.getpid()), "--warm"] + (["--portal", str(portal_root)] if portal_root else [])
    log = open(root / "worker.log", "ab")
    subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=REPO, start_new_session=True)
    return True


# -- what each kind of job does ------------------------------------------------------------------------------------------


def _portal_upload(ctx: Context, job: dict[str, Any], progress) -> dict[str, Any]:
    """A client's new photos: the portal's own reading (portal/engine.read_new_uploads), the same one the portal used to run in a thread."""
    from portal.engine import read_new_uploads

    store = ctx.store()
    targets = {u["id"]: u.get("sha256") for u in store.uploads(job["client"])
               if u.get("status") == "received" and not u.get("source")}
    if not targets:
        return {"read": 0}
    from portal.queue_bridge import current_job
    marker, canonical = current_job(ctx, job["client"])
    if marker is not None:
        if canonical is None:
            raise ValueError("The photos remain pending in the intake queue; processing is incomplete")
        progress(1, 3, "Reading the client's photos and answers")
        # Normal job execution, under this same reentrant case lock. It records
        # its own running/completion/failure and completion-repair semantics.
        finished = run_job(ctx, canonical)
        if finished["state"] != "done" or not (finished.get("result") or {}).get("processed"):
            raise ValueError("The photos remain pending; intake processing stopped or was superseded")
        handled = sum(1 for u in store.uploads(job["client"])
                      if u.get("id") in targets and u.get("sha256") == targets[u["id"]]
                      and u.get("status") in {"checked", "retake"})
        progress(3, 3, "Updating the lists")
        return {"read": handled, "generation": marker["generation"], "canonical_job": canonical["id"]}
    out = read_new_uploads(store, job["client"], ctx.clients, progress=progress)
    return {"read": out.get("read", 0)} | ({"why": out["why"]} if out.get("why") else {})


def _staff_upload(ctx: Context, job: dict[str, Any], progress) -> dict[str, Any]:
    """A scan the office added: stored already by the review app; read here (review/front_desk.read_staff_upload)."""
    from review import front_desk

    # the other scans added to this case that nobody has read (waiting, running, or a reading that failed or died): the overnight run must still see them as changes
    unread = [(j.get("args") or {}).get("name") for j in jobs(ctx.root, client=job["client"], kind="staff_upload", recent=14 * 86400)
              if j["id"] != job["id"] and j.get("state") in ("queued", "running", "failed", "died")]
    out = front_desk.read_staff_upload(ctx.clients, ctx.store() if ctx.portal else None, job["client"], job["args"], job.get("by") or "", progress,
                                       state_path=ctx.clients.parent / "batch_state.json", pending=[n for n in unread if n])
    if out.get("processed") is False:  # the reader caught a failure: the scan is on the case and the night reads it, and the screen must not say "Read"
        raise ValueError(LABELS["failed"])
    return out


def _inbox_read(ctx: Context, job: dict[str, Any], progress) -> dict[str, Any]:
    import inbox

    folder = inbox.default_path(ctx.clients)
    result = inbox.process_inbox(ctx.clients, folder, state_path=ctx.clients.parent / "batch_state.json", progress=progress)
    return {"text": inbox.report_line(result), "routed": len(result["routed"]), "queued": len(result["queued"])}


def _inbox_place(ctx: Context, job: dict[str, Any], progress) -> dict[str, Any]:
    import inbox

    args = job["args"]
    progress(1, 2, "Reading the notice")
    done = inbox.place(ctx.clients, inbox.default_path(ctx.clients), args["id"], args["case"], args["who"], state_path=ctx.clients.parent / "batch_state.json")
    progress(2, 2, "Adding it to the case")
    return {"placed": {k: done.get(k) for k in ("case", "name", "what", "rfe_key", "hearing", "processed")}}


def _clio(ctx: Context, job: dict[str, Any], progress) -> dict[str, Any]:
    """Clio's webhooks said documents or matters changed (src/connectors/clio_hooks.py): the matters on the list are read now instead of tonight, when an
    attorney switched that on. Never beside another Clio step (the overnight run's, Sync now, Send now: clio.step_lock); then this same job is put back
    (Again) for CLIO_AGAIN seconds while the list still holds something, at most CLIO_TRIES times (two hours), leaving no finished record per try;
    tonight's step reads the list anyway. Writes no one case under its lock: the copy in lands in the client folders the overnight run reads, as the
    night's does. It shares the one worker: a large batch (Clio's 50 requests a minute) holds the photos and scans queued behind it for its length
    (docs/decisions.md, brief J2 verification S6)."""
    from connectors import clio, clio_hooks

    firm = ctx.clients.parent  # data/: the Clio folder is data/clio
    progress(1, 1, "Reading what Clio said changed")
    try:
        line = clio_hooks.read_now(firm, ctx.portal, transport=ctx.clio_transport)
    except clio.Busy:
        if (clio_hooks.waiting(firm) or clio_hooks.load(firm)["lookups"]) and int(job.get("tries") or 0) + 1 < CLIO_TRIES:
            raise Again(CLIO_AGAIN) from None
        return {"text": "Another Clio step was running: what Clio named is read tonight."}
    return {"text": line or "Nothing to read now."}


def _clio_failed(ctx: Context, job: dict[str, Any]) -> None:
    """The read stopped on an error: said on Settings, Connections; the list stays for tonight."""
    from connectors import clio

    clio._error(ctx.clients.parent, "Reading Clio's new uploads stopped unexpectedly; they are read tonight.")


def _purge(ctx: Context, job: dict[str, Any], progress: Callable) -> dict[str, Any]:
    """A case purged (src/purge.py run): the job names the purge's id, never the case, and its result holds counts only."""
    import purge

    case = purge.case_of(ctx.clients, str((job.get("args") or {}).get("purge") or ""))
    if case is None:
        raise ValueError("No purge with that id is waiting.")
    progress(1, 2, "Emptying every store of the case")
    args = job.get("args") or {}
    with case_lock(ctx.root, case):  # nothing writes to the case while it is emptied (the lock's file is named by a hash, never the case)
        return purge.run(ctx.clients, case, ctx.portal, Path(args["views"]) if args.get("views") else None, Path(args["access"]) if args.get("access") else None)


def _restore_drill(ctx: Context, job: dict[str, Any], progress: Callable) -> dict[str, Any]:
    """The restore drill on the attorney's button (src/backups.py drill): the same drill the overnight run does, never on the request. Its result is in the backup log (the line
    under Settings); the job's answer is the same words. The passphrase is the server's environment's, never a job's argument (a job file is written in the clear)."""
    import backups

    progress(1, 1, "Restoring the latest backup and comparing it with the install")
    out = backups.drill(None, backups.drill_passphrase())
    return {"text": out["line"], "ok": bool(out.get("ok")), "ran": bool(out.get("ran"))}


def _portal_process(ctx, job, progress):
    from portal.queue_bridge import process
    return process(ctx, job, progress)


def _drive_intake(ctx: Context, job: dict[str, Any], progress) -> dict[str, Any]:
    """Import lazily: the selected intake helper also uses the durable queue."""
    from connectors.drive_intake import worker_handler
    return worker_handler(ctx, job, progress)


KINDS["drive_intake"] = "Selected Google Drive documents"

HANDLERS: dict[str, Callable[[Context, dict[str, Any], Callable], dict[str, Any]]] = {"drive_intake": _drive_intake, "portal_process": _portal_process, "portal_upload": _portal_upload, "staff_upload": _staff_upload,
                                                                                    "inbox_read": _inbox_read, "inbox_place": _inbox_place, "clio": _clio,
                                                                                    "purge": _purge, "restore_drill": _restore_drill}
HANDLERS_FAILED: dict[str, Callable[[Context, dict[str, Any]], None]] = {"portal_upload": _died, "clio": _clio_failed}  # a photo that could not be read is for tonight


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--worker", action="store_true", help="run the worker (until stopped)")
    ap.add_argument("--once", action="store_true", help="with --worker: leave when no job is waiting")
    ap.add_argument("--data", type=Path, default=REPO / "data" / "clients", help="the case folders (the review app's --data)")
    ap.add_argument("--portal", type=Path, default=Path(os.environ.get("PORTAL_DATA", REPO / "data" / "portal")), help="the client portal's folder")
    ap.add_argument("--jobs", type=Path, help="explicit jobs folder (otherwise configured I485_JOBS or beside cases)")
    ap.add_argument("--no-policies", action="store_true", help="omit policy checks in portal processing")
    ap.add_argument("--parent", type=int, help="leave when this process does")
    ap.add_argument("--warm", action="store_true", help="load the pipeline's libraries first")
    ap.add_argument("--status", action="store_true", help="say what is waiting and whether a worker is running")
    args = ap.parse_args(argv)
    root = args.jobs or folder_for(args.data)
    if args.status:
        c = counts(root)
        print(f"Worker: {'running' if alive(root) else 'not running'}; {c['waiting']} waiting, {c['running']} running.")
        return
    if not args.worker:
        ap.error("say --worker or --status")
    try:  # kill -USR1 <the worker> writes what every thread of it is doing to its log: the way to find out why a reading is slow
        import faulthandler
        import signal

        faulthandler.register(signal.SIGUSR1, all_threads=True)
    except (ImportError, AttributeError, ValueError):
        pass
    ran = work(args.data, args.portal, once=args.once, parent=args.parent, warm_up=args.warm, jobs_root=args.jobs, use_policies=not args.no_policies)
    print(f"The worker stopped after {ran} job(s).")


if __name__ == "__main__":
    main()
