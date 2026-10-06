"""The overnight run: every client folder whose documents changed since its
last run, through the pipeline, unattended -- resumable, with progress and
an estimated finish time, and a morning report.

    python src/overnight.py                        # every client that needs it
    python src/overnight.py --until 06:30          # start no new client after 6:30 am
    python src/overnight.py --workers 3            # three clients at a time (without it: a number this computer's threads and memory suit)
    python src/overnight.py --dry-run              # what it would do, and how long
    python src/overnight.py --all                  # re-run everyone (e.g. after a pipeline improvement)

Which clients run (data/batch_state.json remembers each client's last run):
  - new: never processed;
  - changed: a document was added, replaced or removed since its last run;
  - failed: its last run failed;
  - with --all, everyone. A client processed by an older version of the
    pipeline is counted in the report, so the firm can choose when to
    re-run them (--all), instead of every code change re-running 1,800.

Safe to stop at any time (Ctrl+C, a reboot, --until): each client's result
is saved as soon as it finishes, and the next run picks up where this one
stopped. Re-running a client keeps every review decision already made
(process_clients.process_one -> refill). One run at a time (a lock file).

Progress for the review app's All clients page: data/batch_progress.json.
The morning report: data/batch_report.txt (and every run in batch_log.jsonl). It is read by
everyone, so a restricted case (src/restricted.py: VAWA, T, U, asylum, or one an attorney
restricted) is never named in it, nor counted among the cases with a blocking issue or an
unreadable document: those who may open it see it on All clients.
After the clients: the official sources (src/maintenance.py), USCIS's case
status for the open receipts when the server has the API keys
(src/case_status.py), the notice inbox (src/inbox.py: the day's scanned mail
onto its cases), then every case's timeline. With Clio connected (Settings,
Connections; src/connectors/clio.py), its matters' documents come in before
the clients are chosen, and the results go back to Clio at the end.

Scheduling (on the firm machine), e.g. Windows Task Scheduler, daily 7 pm:
    wsl bash -lc "cd ~/i485-pipeline && source .venv-wsl/bin/activate && python3 src/overnight.py --until 06:30"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import clock  # noqa: E402
import events  # noqa: E402
import schema_path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SECONDS = 180.0  # a first guess per client, until real runs give an average


def default_workers() -> int:
    """Cases at a time on this computer: a third of its threads, no more than its memory holds (each worker loads the language models: about 4 GB), and never more
    than 8; at least one. The vision model is the one step the workers share: they take turns on it (src/jobs.py gpu_lock), so more workers than GPUs only
    wait there while the rest of a case (the text, the rules, the forms) goes on in parallel."""
    cpus = os.cpu_count() or 2
    memory = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                memory = int(line.split()[1]) / 1048576  # GB
    except (OSError, ValueError):
        pass
    return max(1, min(cpus // 3, int(memory // 4) if memory else 2, 8))


def _now() -> datetime:
    """The office's clock in UTC (src/clock.py): the run's stamps, its estimated finish and its elapsed time are real hours;
    --until and the morning report are written in the office's time (clock.now(), clock.local)."""
    return clock.utcnow()


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def source_signature(folder: Path, exclude=()) -> str:
    """Changes when a document is added, replaced or removed (names, sizes,
    modification times -- not contents, so 1,800 folders are checked in seconds).
    exclude: file names left out, as if they were not there (the signature of what has been read: a scan added and not read yet is not part of it)."""
    h = hashlib.sha256()
    for p in sorted(folder.glob("*.pdf")):
        if p.name in exclude:
            continue
        st = p.stat()
        h.update(f"{p.name}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:16]


def pipeline_version(repo: Path = REPO) -> str:
    """Changes when the pipeline's code or schemas change."""
    h = hashlib.sha256()
    for p in sorted([*(repo / "src").rglob("*.py"), *schema_path.all_files(".json", schema_path.schemas_in(repo))]):
        h.update(p.relative_to(repo).as_posix().encode() + b"\0" + p.read_bytes())
    return h.hexdigest()[:12]


def choose(clients_root: Path, out_root: Path, state: dict, every: bool, only: list[str] | None) -> tuple[list[tuple[str, str]], dict[str, int]]:
    """[(client, why it runs)], and counts of the clients that don't run."""
    names = only or sorted(p.name for p in clients_root.iterdir() if (p / "source").is_dir())
    todo, skipped = [], {"unchanged": 0, "no_documents": 0}
    for name in names:
        source = clients_root / name / "source"
        if not any(source.glob("*.pdf")):
            skipped["no_documents"] += 1
            continue
        last = state.get(name) or {}
        meta = out_root / name / "meta.json"
        if not last and not (every or only) and (out_root / name / "fact_graph.json").exists() and meta.exists() \
                and meta.stat().st_mtime >= max(p.stat().st_mtime for p in source.glob("*.pdf")):
            # processed before the overnight run existed (process_clients.py), and nothing changed since
            state[name] = last = {"status": "done", "at": _read(meta, {}).get("processed_at"), "version": None,
                                  "why": "processed earlier", "signature": source_signature(source)}
        if every or only:
            todo.append((name, "requested"))
        elif not last or not (out_root / name / "fact_graph.json").exists():
            todo.append((name, "new"))
        elif last.get("status") == "failed":
            todo.append((name, "failed last time"))
        elif last.get("signature") != source_signature(source):
            todo.append((name, "documents changed"))
        else:
            skipped["unchanged"] += 1
    return todo, skipped


# -- worker processes ---------------------------------------------------------------

_CTX: dict | None = None


def _init(policies: bool, use_vision: bool) -> None:
    global _CTX
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C is the parent's to handle: it lets running clients finish
    events.set_actor(WORKER, "system", "overnight")  # the event ledger's rows from a worker process (src/events.py)
    from process_clients import load_context

    _CTX = load_context(policies, use_vision)


def _run_one(name: str, source: str, out: str) -> dict:
    import jobs
    from process_clients import process_one

    try:
        ctx = jobs.Context(Path(out).parent)
        # Consistent producer order; this direct pipeline did not reproduce a
        # portal-gate cycle. Canonical mutations serialize long reader work.
        with jobs.installation_gate(ctx), jobs.case_lock(ctx.root, name):
            return {"status": "done", **process_one(name, Path(source), Path(out), _CTX)}
    except Exception as exc:  # noqa: BLE001 -- one client's failure must not stop the night
        return {"status": "failed", "client": name, "error": f"{type(exc).__name__}: {exc}"[:500]}


# -- the run ----------------------------------------------------------------------------


class Lock:
    """One run at a time, by the operating system's own file lock (src/oslock.py): a run that died lets go of it by dying, so there is nothing to take over and nothing
    to delete; nobody can take it from a run that is alive. The file says who holds it (pid and time) for a person looking: nothing depends on it."""

    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None

    def __enter__(self):
        import oslock

        fd = oslock.open_lock_file(self.path)
        if not oslock.try_lock(fd):
            who = os.pread(fd, 100, 0).decode("utf-8", "replace").strip() if hasattr(os, "pread") else ""
            os.close(fd)
            raise SystemExit(f"Another run is in progress ({who or 'a run is holding ' + str(self.path)}).")
        self.fd = fd
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()} {_now().isoformat()}".encode(), 0) if hasattr(os, "pwrite") else None
        return self

    def __exit__(self, *exc):
        import oslock

        if self.fd is not None:
            oslock.unlock(self.fd)
            os.close(self.fd)
            self.fd = None


def _until(text: str | None) -> datetime | None:
    """'06:30' -> the next 6:30 in the office's time zone (the Settings page), whatever zone the server keeps."""
    if not text:
        return None
    hour, minute = (int(x) for x in text.split(":"))
    local = clock.now()
    stop = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return stop if stop > local else stop + timedelta(days=1)


WORKER = "The overnight run"  # who the event ledger names for what the run writes


def run(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """The run (_run's arguments); everything it writes is the overnight run's, in the event ledger (src/events.py)."""
    with events.acting(WORKER, "system", "overnight", everywhere=True):
        return _run(*args, **kwargs)


def _run(clients_root: Path, out_root: Path, data_root: Path, workers: int = 1, until: str | None = None, every: bool = False,
         only: list[str] | None = None, policies: bool = False, use_vision: bool = True, dry_run: bool = False,
         log=print, runner=None) -> dict[str, Any]:
    """runner(name, source, out) -> result: in-process instead of worker processes (tests)."""
    if not dry_run:  # Clio's new and changed documents first, so tonight's run reads them (says nothing when Clio isn't set up)
        said = clio_step(data_root, clients_root, out_root, "in")
        if said:
            log(said)
    state_path, progress_path = data_root / "batch_state.json", data_root / "batch_progress.json"
    state = _read(state_path, {})
    version = pipeline_version()
    todo, skipped = choose(clients_root, out_root, state, every, only)
    history = [s["seconds"] for s in state.values() if s.get("status") == "done" and s.get("seconds")]
    average = sum(history) / len(history) if history else DEFAULT_SECONDS
    estimate = timedelta(seconds=average * len(todo) / max(1, workers))
    older = sum(1 for n, s in state.items() if s.get("version") != version and n not in dict(todo))
    log(f"{len(todo)} to run ({', '.join(f'{v} {k}' for k, v in _reasons(todo).items()) or 'none'}); "
        f"{skipped['unchanged']} unchanged; about {_hours(estimate)} with {workers} at a time.")
    if dry_run:
        return {"todo": todo, "skipped": skipped, "estimate_seconds": estimate.total_seconds(), "older_version": older}

    lock = Lock(data_root / "batch.lock")
    lock.__enter__()
    _write(state_path, state)  # clients processed before this run existed are now on record
    stop_at = _until(until)
    started = _now()
    results: list[dict] = []
    progress = {"started_at": started.isoformat(), "total": len(todo), "done": 0, "failed": 0, "running": [], "finished_at": None,
                "stop_at": stop_at.isoformat() if stop_at else None, "eta": (started + estimate).isoformat(), "pid": os.getpid()}
    _write(progress_path, progress)
    stopping = {"why": None}

    def interrupt(*_):
        stopping["why"] = "stopped by hand"
        log("Stopping after the clients already running finish (Ctrl+C again to quit now).")
        signal.signal(signal.SIGINT, signal.default_int_handler)

    previous = signal.signal(signal.SIGINT, interrupt) if hasattr(signal, "SIGINT") else None
    queue = list(todo)
    try:
        if runner is not None:
            from concurrent.futures import ThreadPoolExecutor

            pool_cm, submit = ThreadPoolExecutor(max_workers=workers), runner
        else:
            pool_cm, submit = ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(policies, use_vision)), _run_one
        with pool_cm as pool:
            running: dict = {}
            while queue or running:
                while queue and len(running) < workers and not stopping["why"]:
                    if stop_at and clock.now() >= stop_at:
                        stopping["why"] = f"reached --until {until}"
                        break
                    name, why = queue.pop(0)
                    source = clients_root / name / "source"
                    signature = source_signature(source)  # before reading: a change during the run re-queues it next time
                    future = pool.submit(submit, name, str(source), str(out_root / name))
                    running[future] = (name, why, signature, time.time())
                if not running:
                    break
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in finished:
                    name, why, signature, t0 = running.pop(future)
                    r = future.result()
                    results.append(r | {"why": why})
                    entry = {"status": r["status"], "at": _now().isoformat(), "version": version, "why": why,
                             "seconds": r.get("seconds") or round(time.time() - t0, 1)}
                    if r["status"] == "done":
                        entry |= {"signature": signature, "counts": r["counts"], "errors": r["errors"] or None}
                        progress["done"] += 1
                    else:
                        entry |= {"error": r["error"], "signature": None}
                        progress["failed"] += 1
                    state[name] = entry
                    _write(state_path, state)  # after every client: a stop loses nothing
                    finished_n = progress["done"] + progress["failed"]
                    pace = (_now() - started).total_seconds() / finished_n
                    progress["eta"] = (_now() + timedelta(seconds=pace * (len(queue) + len(running)))).isoformat()
                    log(f"[{finished_n}/{len(todo)}] {_shown(out_root, name)}: " + (f"{r['counts']['blocking']} blocking, {r['counts']['review']} to review ({r['seconds']:.0f}s)"
                                                                if r["status"] == "done" else f"FAILED: {r['error']}")
                        + f"  · done about {_clock(progress['eta'])}")
                progress["running"] = [n for n, *_ in running.values()]
                _write(progress_path, progress)
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)
        lock.__exit__()

    progress.update(finished_at=_now().isoformat(), running=[], stopped=stopping["why"], left=len(queue))
    _write(progress_path, progress)
    report = _report(results, skipped, queue, stopping["why"], started, older, closed=lambda name: _closed(out_root, name))
    (data_root / "batch_report.txt").write_text(report, encoding="utf-8")
    with open(data_root / "batch_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"started_at": started.isoformat(), "finished_at": progress["finished_at"], "version": version,
                            "done": progress["done"], "failed": progress["failed"], "left": len(queue), "stopped": stopping["why"]}) + "\n")
    log(report)
    try:  # forms, fees, guidelines and USCIS addresses: still current? (src/maintenance.py; never fails the run)
        import maintenance

        if os.environ.get("I485_LIVE_CHECKS", "1") == "0":
            raise RuntimeError("switched off")
        checked = maintenance.live_checks()
        stale = [k for k, r in checked["results"].items() if r.get("ok") is False]
        if stale:
            log(f"Keeping current: {len(stale)} source(s) changed: {', '.join(stale)} (review app -> Keeping current).")
    except Exception as exc:  # noqa: BLE001
        log(f"Keeping current: the live check couldn't run ({type(exc).__name__}).")
    try:  # USCIS's own case status for the open receipts, within the day's budget (src/case_status.py; nothing without the API keys)
        import case_status

        if os.environ.get("I485_CASE_STATUS", "1") == "0":
            raise RuntimeError("switched off")
        log(case_status.nightly(out_root, data_root))
    except Exception as exc:  # noqa: BLE001
        log(f"USCIS case status: couldn't run ({type(exc).__name__}).")
    inbox_text = notice_inbox(out_root, data_root)  # the day's mail onto its cases, before the timelines are worked out
    log(inbox_text)
    alerts = sign_in_alerts(data_root)  # an account or an address locked out three times in a day (review/auth.py, review/server.py)
    if alerts:
        log(alerts)
    with open(data_root / "batch_report.txt", "a", encoding="utf-8") as f:  # the morning report says it too
        f.write(f"  {inbox_text}\n" + (f"  {alerts}\n" if alerts else ""))
    try:  # where every case stands, for the morning (src/journey.py): the dashboard's cache, then the portal's "your case" page
        log(journeys(out_root, data_root / "portal"))
    except Exception as exc:  # noqa: BLE001
        log(f"Case timelines: couldn't update ({type(exc).__name__}).")
    approvals_text = approvals_night(out_root, data_root)  # what waits for an attorney (src/approvals.py): the line My work says too, in the morning report
    log(approvals_text)
    with open(data_root / "batch_report.txt", "a", encoding="utf-8") as f:
        f.write(f"  {approvals_text}\n")
    log(purges_due(out_root))
    day_text = day_plan_night(out_root)  # how many cases the office can get to a signed packet today (src/day_plan.py): the line My work says too
    log(day_text)
    with open(data_root / "batch_report.txt", "a", encoding="utf-8") as f:
        f.write(f"  {day_text}\n")
    log(search_index(out_root))
    log(find_index(out_root))
    log(query_layer(out_root))
    log(staff_reminders_night(out_root, data_root))
    log(client_reminders_night(out_root, data_root))
    log(feedback_night(out_root, data_root))
    log(accuracy_night(out_root))
    audit_text = audit_night(out_root)  # what the reviewers' Saves change, box by box (src/audit_fill.py): the morning report says it too
    log(audit_text)
    with open(data_root / "batch_report.txt", "a", encoding="utf-8") as f:
        f.write("".join(f"  {line}\n" for line in audit_text.splitlines()))
    reading_text = reading_night(out_root)  # the fields the office changes most, from the firm's labelled examples (src/reader_examples.py)
    log(reading_text)
    with open(data_root / "batch_report.txt", "a", encoding="utf-8") as f:
        f.write("".join(f"  {line}\n" for line in reading_text.splitlines()))
    posture_text = posture_night(data_root)  # the computer's duties, once a day (src/posture.py): the lines that are off or not known
    if posture_text:
        log(posture_text)
        with open(data_root / "batch_report.txt", "a", encoding="utf-8") as f:
            f.write("".join(f"  {x}\n" for x in posture_text.splitlines()))
    drill_text = restore_drill_night(stop_at)  # the monthly restore drill, last: after every file the night writes is written (src/backups.py drill)
    if drill_text:
        log(drill_text)
        with open(data_root / "batch_report.txt", "a", encoding="utf-8") as f:
            f.write(f"  {drill_text}\n")
    said = clio_step(data_root, clients_root, out_root, "out")  # the packets, stages and deadlines back to Clio's matters
    if said:
        log(said)
    secrets_text = secrets_night(data_root)  # which key or secret is overdue for a change (names and dates, never a value): src/firmsecrets.py
    log(secrets_text)
    with open(data_root / "batch_report.txt", "a", encoding="utf-8") as f:
        f.write(f"  {secrets_text}\n")
    ledger_text = ledger_night(data_root)  # last, so the check sees every row tonight's run wrote
    log(ledger_text)
    with open(data_root / "batch_report.txt", "a", encoding="utf-8") as f:
        f.write(f"  {ledger_text}\n")
    return {"results": results, "left": [n for n, _ in queue], "skipped": skipped, "older_version": older}


def clio_step(data_root: Path, clients_root: Path, out_root: Path, direction: str) -> str | None:
    """Clio in or out (src/connectors/clio.py); None when Clio was never set up here (or I485_CLIO=0). Never fails the run."""
    try:
        from connectors import clio

        if os.environ.get("I485_CLIO", "1") == "0" or not clio.folder(data_root).exists():
            return None
        return clio.nightly(data_root, clients_root, out_root, direction)
    except Exception as exc:  # noqa: BLE001
        return f"Clio: couldn't run ({type(exc).__name__})."


def journeys(out_root: Path, portal_root: Path | None = None, workers: int | None = None) -> str:
    """Rebuilds each case's timeline summary (deadlines move with the date),
    and -- when the portal is set up -- the client's "your case" view, with a
    "there's news" message when the stage moved or an appointment appeared."""
    import journey
    from review import roster
    from review.overview import deadlines

    # every client's row on the lists, read in a pool of processes (each case's review row and timeline are the dashboard's own caches, so this warms them too) and
    # saved for the review app to adopt (src/review/roster.py): the morning's first look at any list is instant
    found = roster.warm(out_root, portal_root, workers)
    rows = [{"id": case, "journey": e["row"].get("journey")} for case, e in found.entries.items()
            if e["has_case"] and e["row"] is not None and "error" not in (e["row"].get("journey") or {"error": 1})]
    closed = {case for case, e in found.entries.items() if e["closed"]}
    # the morning report is read by everyone: a restricted case's deadlines are not counted in it (src/restricted.py)
    due = [x for x in deadlines([r for r in rows if r["id"] not in closed], horizon_days=14)]
    late = sum(1 for x in due if x["level"] == "overdue")  # a deadline long past is "passed", not late news
    text = f"Case timelines: {len(rows)} updated; {len(due)} deadline(s) in the next 14 days" + (f", {late} LATE" if late else "") + "."
    if portal_root is not None and (portal_root / "clients").exists():
        pushed = journey.push_to_portal(out_root, portal_root)
        text += f" Portal: {len(pushed['changed'])} client view(s) changed, {pushed['notified']} told there's news."
    return text


def secrets_night(data_root: Path) -> str:
    """"Keys and secrets overdue for a change: Mail (due 10/01/2026).": which kind the attorney's chosen cadence has passed (src/firmsecrets.py), or that none is. A key found in
    deployment.json is moved into the vault first. Never a value. Never stops the run."""
    try:
        import firmsecrets

        moved = firmsecrets.migrate_deployment(data_root=data_root)
        return " ".join(moved + [firmsecrets.morning_line(data_root=data_root)])
    except Exception as exc:  # noqa: BLE001 -- a line missing from one morning's report is not worth a failed morning
        return f"Keys and secrets: could not be checked ({type(exc).__name__}); they are checked again tomorrow night."


def ledger_night(data_root: Path) -> str:
    """"The record is intact: 1204 rows from 09/01/2026 to 10/04/2026. The record's seal for 10/03/2026: 3fa9c10b7d2e4a65, 123 rows that day.": the whole event ledger
    checked and the result kept for Settings, and the day's seal written for the attorney (src/ledger_seal.py). Never stops the run."""
    try:
        if os.environ.get("I485_LEDGER_SEAL", "1") == "0":
            return "The record's check: switched off."
        import ledger_seal

        return ledger_seal.nightly(events.base_path(data_root))
    except Exception as exc:  # noqa: BLE001 -- a line missing from one morning's report is not worth a failed morning
        return f"The record's check could not run ({type(exc).__name__}); it runs again tomorrow night."


def approvals_night(out_root: Path, data_root: Path) -> str:
    """"12 approvals waiting, the oldest from 10/01/2026": the attorney's queue (My approvals), from the cases the timelines step has just read. The report is read by everyone,
    so a restricted case's items are not in it, whoever is named on the case (they are on My approvals for the attorneys named)."""
    try:
        import approvals
        from review import roster

        entries = roster.saved(out_root)
        if entries is None:
            return "Approvals: not counted (the lists were not saved)."
        closed = {case for case, e in entries.items() if e["closed"] or e.get("unwritten")}
        cases = [(case, None, e["approvals"]) for case, e in sorted(entries.items())
                 if e.get("approvals") and e["row"] is not None and not e["row"].get("end") and case not in closed]
        firm = approvals.firm_items(approvals.FirmContext(data_root, lambda case: case in entries and case not in closed))
        return (approvals.line(approvals.summary(cases, firm)) or "No approvals waiting") + "."
    except Exception as exc:  # noqa: BLE001 -- the morning report is made without it
        return f"Approvals: couldn't be counted ({type(exc).__name__})."


def day_plan_night(out_root: Path) -> str:
    """"12 cases can reach a signed packet today": Today's count (src/day_plan.py), from the cases the timelines step has just read. The report is read by everyone, so a
    restricted case is not counted in it, whoever is named on the case."""
    try:
        import day_plan
        from review import roster

        entries = roster.saved(out_root)
        if entries is None:
            return "Day plan: not counted (the lists were not saved)."
        closed = {case for case, e in entries.items() if e["closed"] or e.get("unwritten")}
        return day_plan.night_line(entries, closed)
    except Exception as exc:  # noqa: BLE001 -- the morning report is made without it
        return f"Day plan: couldn't be counted ({type(exc).__name__})."


def posture_night(data_root: Path) -> str:
    """The computer's duties read once a day (src/posture.py: the disk encrypted, the screen locked, a recent backup on another device, the system updated, the firewall on): kept in
    data/posture.json for Settings, and the lines that are off or not known go in the morning report. It only reads: the report says the firm's IT person changes what is off.
    Never stops the run."""
    try:
        import posture

        if not posture.enabled():
            return ""
        lines = posture.report_lines(posture.refresh(data_root))
        return "\n".join(lines + ["This computer: the product only reads these; your IT person changes what is off (Settings, This computer, says how)."] if lines else [])
    except Exception as exc:  # noqa: BLE001 -- the morning report is made without it
        return f"This computer: couldn't be read ({type(exc).__name__})."


def restore_drill_night(stop_at: datetime | None = None) -> str:
    """The restore drill (src/backups.py drill) on the first night of the month, and on any night it is overdue (never run, failed, or more than a month ago): the latest backup
    restored into a scratch folder and compared with the live install. Run after every other step of the night, so what it compares is what the night left; stopped, and
    said, when the hour the run ends (stop_at: the office's time, as --until gives it) comes first. Returns the line the morning report carries: the drill's own, on the
    morning after a drill and whenever the item is overdue; nothing on other mornings. Never stops the run."""
    try:
        import backups

        if os.environ.get("I485_RESTORE_DRILL", "1") == "0":
            return "Restore drill: switched off."
        before = backups.drill_status()
        # the first night of the month; a drill the last night's end stopped; a month gone by or never run. A drill that failed this month is not run again by the night (it would fail
        # the same way and open the whole backup for nothing): it is said every morning until an attorney runs it by hand or the first night of the month comes
        if clock.today().day == 1 or before["unfinished"] or (before["overdue"] and not before["failed_lately"]):
            if backups.latest() is None:
                return "Restore drill: no backup has been made yet, so there is nothing to restore."
            return backups.drill(None, backups.drill_passphrase(), deadline=stop_at)["line"]
        return before["line"] if before["overdue"] else ""
    except Exception as exc:  # noqa: BLE001 -- the morning report is made without it
        return f"Restore drill: couldn't run ({type(exc).__name__})."


def notice_inbox(out_root: Path, data_root: Path) -> str:
    """The notice inbox (src/inbox.py): each scanned notice routed to its case (reprocessed for that one document), the
    rest waiting for a person. Never stops the run."""
    try:
        import inbox

        if os.environ.get("I485_NOTICE_INBOX", "1") == "0":
            return "Notice inbox: switched off."
        result = inbox.process_inbox(out_root, inbox.default_path(out_root), state_path=data_root / "batch_state.json")
        return inbox.report_line(result)
    except Exception as exc:  # noqa: BLE001 -- the mail waits in the inbox for tomorrow or the button on My work
        return f"Notice inbox: couldn't be read ({type(exc).__name__}); the notices stay in the inbox."


def purges_due(out_root: Path) -> str:
    """The purges whose day has come and that are confirmed when they must be (src/purge.py): each handed to the job worker, which empties every store
    of the case. Never names a case; never stops the run."""
    try:
        import purge

        due = purge.due_now(out_root)
        for case in due:
            purge.submit(out_root, case, "The overnight run")
        return f"Purges: {len(due)} case(s) handed to the job worker to be purged." if due else "Purges: none due."
    except Exception as exc:  # noqa: BLE001 -- a purge waits for the next run or the attorney's button
        return f"Purges: couldn't be started ({type(exc).__name__}); they are tried again on the next run."


def search_index(out_root: Path) -> str:
    """The firm-wide document index (src/index.py): the cases whose documents changed since their rows were built, and a check of
    the index file itself (a damaged one is made new and every case is read again). Never stops the run."""
    try:
        import index

        r = index.rebuild_changed(out_root, integrity=True)
        return f"Search index: {r['rebuilt']} case(s) updated, {r['documents']} documents in all."
    except Exception as exc:  # noqa: BLE001 -- search is a convenience; the morning's work doesn't wait on it
        return f"Search index: couldn't update ({type(exc).__name__}); it is built again on the next run."


def find_index(out_root: Path) -> str:
    """Find across the firm's index of meaning (src/find.py), when an attorney has switched it on: the cases whose records changed, embedded by the
    firm's own model. Never stops the run; switched off, nothing is indexed."""
    try:
        import find

        if not find.is_on():
            return "Find across the firm: switched off, nothing indexed."
        r = find.rebuild_changed(out_root)
        return f"Find across the firm: {r['rebuilt']} case(s) indexed again, {r['passages']} passages in all."
    except Exception as exc:  # noqa: BLE001 -- the morning's work doesn't wait on it; the follower and the next run try again
        return f"Find across the firm: couldn't update ({type(exc).__name__}); it is built again on the next run."


def query_layer(out_root: Path) -> str:
    """The query layer (src/query.py): the cases whose records changed since their rows were built, and the event ledger's new rows. After the
    timelines are worked out, so each case's stage and deadlines are the ones just written. Never stops the run."""
    try:
        import query

        r = query.rebuild_changed(out_root)
        return f"Query layer: {r['rebuilt']} case(s) updated, {r['cases']} in all, {r['events']} new ledger row(s)."
    except Exception as exc:  # noqa: BLE001 -- the firm's own tools read it, and it is built again next time
        return f"Query layer: couldn't update ({type(exc).__name__}); it is built again on the next run."


def staff_reminders_night(out_root: Path, data_root: Path) -> str:
    """E-mails to the staff who turned reminders on, about the deadlines they are responsible for, the week before and the morning of
    (src/staff_reminders.py). After the timelines, so the deadlines are the ones just worked out. Never stops the run."""
    try:
        import staff_reminders

        if os.environ.get("I485_STAFF_REMINDERS", "1") == "0":
            return "Staff reminders: switched off."
        return staff_reminders.nightly(out_root, data_root)
    except Exception as exc:  # noqa: BLE001 -- a reminder missed one night is not worth a failed morning
        return f"Staff reminders: couldn't run ({type(exc).__name__}); they run again tomorrow night."


def client_reminders_night(out_root: Path, data_root: Path) -> str:
    """Reminders to the clients about an appointment, the week before and the day before (src/client_reminders.py), by the channels each client agreed to.
    After the timelines, so the appointments are the ones just worked out. Never stops the run."""
    try:
        import client_reminders

        if os.environ.get("I485_CLIENT_REMINDERS", "1") == "0":
            return "Client reminders: switched off."
        return client_reminders.nightly(out_root, data_root)
    except Exception as exc:  # noqa: BLE001 -- a reminder missed one night is tried again the next
        return f"Client reminders: couldn't run ({type(exc).__name__}); they run again tomorrow night."


def feedback_night(out_root: Path, data_root: Path) -> str:
    """The clients' answers to "how was this step" copied from the portal's folder onto their cases (src/client_case.py). Never stops the run."""
    try:
        import client_case

        n = client_case.sync_all_feedback(out_root, data_root / "portal")
        return f"Client feedback: {n} answer(s) copied onto the cases." if n else "Client feedback: nothing new."
    except Exception as exc:  # noqa: BLE001
        return f"Client feedback: couldn't copy ({type(exc).__name__}); it is copied again tomorrow night."


def accuracy_night(out_root: Path) -> str:
    """The comparison with the firm's hand-filled references (src/accuracy.py): tonight's figures added to the history the
    Accuracy record shows. The made-up references stand in while the firm has none of its own. Never stops the run."""
    try:
        if os.environ.get("I485_ACCURACY", "1") == "0":
            return "Accuracy: switched off."
        import accuracy

        return accuracy.nightly(out_root)
    except Exception as exc:  # noqa: BLE001 -- a figure missing for one night is not worth a failed morning
        return f"Accuracy: couldn't run ({type(exc).__name__}); it runs again tomorrow night."


def audit_night(out_root: Path) -> str:
    """What the reviewers' Saves change, box by box, across the cases (src/audit_fill.py): counted again, and one line for each box changed the
    same way on three or more cases. The lines name no client and no value; the cases that are not protected only, and says so. Never stops the run."""
    try:
        if os.environ.get("I485_AUDIT", "1") == "0":
            return "Boxes the office changes: switched off."
        import audit_fill

        return audit_fill.nightly(out_root)
    except Exception as exc:  # noqa: BLE001 -- a line missing from one morning's report is not worth a failed morning
        return f"Boxes the office changes: couldn't be counted ({type(exc).__name__}); it is counted again tomorrow night."


def reading_night(out_root: Path) -> str:
    """The firm's labelled examples counted per reader and field (src/reader_examples.py): one line, and the three fields the office changes most
    once a field has 20 examples with more than 10% changed. Numbers only: no value, no name; the cases that are not protected only, and says so.
    Never stops the run."""
    try:
        if os.environ.get("I485_READING", "1") == "0":
            return "Reading: switched off."
        import reader_examples

        return reader_examples.nightly(out_root)
    except Exception as exc:  # noqa: BLE001 -- a line missing from one morning's report is not worth a failed morning
        return f"Reading: couldn't be counted ({type(exc).__name__}); it is counted again tomorrow night."


def _reasons(todo: list[tuple[str, str]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for _, why in todo:
        out[why] = out.get(why, 0) + 1
    return out


def _hours(d: timedelta) -> str:
    minutes = round(d.total_seconds() / 60)
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def _clock(iso: str) -> str:
    return clock.local(iso).strftime("%H:%M")


def _closed(out_root: Path, name: str) -> bool:
    """A restricted case (src/restricted.py): never named or counted in what everyone reads. Never stops the run."""
    try:
        import restricted

        return restricted.is_restricted(out_root / name)
    except Exception:  # noqa: BLE001 -- a case that can't be asked is not named either
        return (out_root / name / "fact_graph.json").exists()


def _shown(out_root: Path, name: str) -> str:
    """The client's folder name on the console, or "a restricted case"."""
    return "a restricted case" if _closed(out_root, name) else name


def _report(results: list[dict], skipped: dict, left: list, stopped: str | None, started: datetime, older: int, closed=None) -> str:
    """closed(name): a restricted case, left out of every list and count below the run's own totals (src/restricted.py)."""
    done = [r for r in results if r["status"] == "done"]
    failed = [r for r in results if r["status"] != "done"]
    open_done = [r for r in done if not (closed and closed(r["client"]))]
    open_failed = [r for r in failed if not (closed and closed(r["client"]))]
    lines = [f"Overnight run {clock.local(started):%m/%d/%Y %H:%M}: {_hours(_now() - started)}",
             f"  processed: {len(done)}   failed: {len(failed)}   unchanged (skipped): {skipped['unchanged']}"
             + (f"   not reached: {len(left)} ({stopped}; they run next time)" if left else "")]
    if older:
        lines.append(f"  {older} clients were last processed by an older version of the pipeline (python src/overnight.py --all to refresh them)")
    if open_done:
        blocking = [r for r in open_done if r["counts"]["blocking"]]
        lines.append(f"  with a blocking issue for the attorney: {len(blocking)}")
        lines += [f"    {r['client']}: {r['counts']['blocking']} blocking" for r in blocking[:50]]
        with_errors = [r for r in open_done if r["errors"]]
        if with_errors:
            lines.append(f"  with a document that couldn't be read: {len(with_errors)}")
            lines += [f"    {r['client']}: {', '.join(sorted(r['errors']))}" for r in with_errors[:50]]
    if open_failed:
        lines.append("  FAILED (they run again next time):")
        lines += [f"    {r['client']}: {r['error']}" for r in open_failed]
    if closed is not None:
        lines.append("  Restricted cases are not named in this report: whoever may open them sees them on All clients.")
    return "\n".join(lines) + "\n"


def sign_in_alerts(data_root: Path, now: datetime | None = None) -> str | None:
    """The morning report's line when, in the last day, an account was locked out three times or an address was refused three times for too many tries
    (the "lockout_alert" rows of the staff access log, data/review_users_access.jsonl). How many, never whose: the report is read by everyone; the
    attorney sees which on Settings, Staff, "What staff did". None when there were none (or there is no access log: no staff accounts)."""
    path = Path(data_root) / "review_users_access.jsonl"
    if not path.exists():
        return None
    since = (now or clock.utcnow()) - timedelta(days=1)
    found = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"lockout_alert"' not in line:
                continue
            try:
                at = clock.parse(json.loads(line).get("at"))
            except (ValueError, AttributeError):
                continue
            if at is not None and at >= since:
                found += 1
    if not found:
        return None
    return (f"Sign-in alerts: {found} in the last day (an account locked out, or a computer address refused for too many tries, three times in one day). "
            "The attorney sees which on Settings, Staff, What staff did.")


def progress(data_root: Path) -> dict[str, Any] | None:
    """The current or last run, for the review app (None if there never was one)."""
    p = _read(data_root / "batch_progress.json", None)
    import oslock

    if p and not p.get("finished_at") and not oslock.held_by_someone(Path(data_root) / "batch.lock"):  # the run holds the system's lock for as long as it lives
        p.update(stopped="interrupted", running=[])  # the run died without finishing
    return p


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clients", nargs="*", help="only these clients (default: every one that needs it)")
    ap.add_argument("--clients-root", type=Path, default=REPO / "clients")
    ap.add_argument("--out", type=Path, default=REPO / "data" / "clients")
    ap.add_argument("--data", type=Path, default=REPO / "data", help="where the run's state, progress and report go")
    ap.add_argument("--workers", type=int, default=0, help="clients at a time (0: this computer's own choice; the vision model is shared: workers take turns on it)")
    ap.add_argument("--until", help="HH:MM. Start no new client after this time (the office's time zone)")
    ap.add_argument("--all", action="store_true", help="re-run every client, changed or not")
    ap.add_argument("--policies", action="store_true", help="apply schemas/law/policy_sijs.json (NOT attorney-approved yet)")
    ap.add_argument("--no-vision", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="list what would run and how long it would take")
    args = ap.parse_args(argv)
    run(args.clients_root, args.out, args.data, workers=max(1, args.workers or default_workers()), until=args.until, every=args.all, only=args.clients or None,
        policies=args.policies, use_vision=not args.no_vision, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
