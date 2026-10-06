"""The job queue (src/jobs.py): the reading that used to run while a person waited is written down and read by one worker, one job at a time, under a lock the overnight
run and the review app's own writes share, so no write is lost. A job that died with its process says so, in words, at the next start. Invented clients only."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import jobs
from law_app.adapters.queue.filesystem import FilesystemJobQueries

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))


def test_filesystem_queries_preserve_active_precedence_and_bounded_recent_listing(tmp_path):
    root = tmp_path / "queue"
    done = root / "done"
    done.mkdir(parents=True)

    def record(n, folder, *, client="synthetic", valid=True):
        job_id = f"{n:020d}-staff_upload-12345678"
        path = folder / (job_id + ".json")
        value = {"id": job_id, "kind": "staff_upload", "client": client, "state": "queued"}
        path.write_text(json.dumps(value) if valid else "{", encoding="utf-8")
        os.utime(path, (1000, 1000))
        return value

    finished = [record(n, done) for n in range(202)]
    active = record(201, root, client="active")
    queries = FilesystemJobQueries(root, now=lambda: 1000)
    assert queries.get(active["id"]) == active
    assert queries.get(finished[0]["id"], active_only=True) is None
    assert queries.get("../../foreign") is None
    assert queries.list_jobs() == [active] + finished[2:]
    assert queries.list_jobs(client="active", kind="staff_upload") == [active]
    record(202, done, valid=False)
    os.utime(done / (finished[201]["id"] + ".json"), (0, 0))
    assert queries.list_jobs(recent=10) == [active] + finished[3:201]
    assert queries.get("00000000000000000202-staff_upload-12345678") is None


def test_context_queries_are_optional_and_worker_revalidation_stays_active_only(tmp_path, monkeypatch):
    from types import SimpleNamespace

    job_id = "00000000000000000001-staff_upload-12345678"
    cached = {"id": job_id, "state": "queued", "kind": "staff_upload", "client": "synthetic", "args": {}}
    calls = []
    answer = [dict(cached)]

    def get(jid, *, active_only=False):
        calls.append((jid, active_only))
        return answer[0]

    ctx = jobs.Context(tmp_path / "clients", jobs_root=tmp_path / "first",
                       job_queries=SimpleNamespace(get=get))
    assert jobs._current_cached_job(ctx, cached) is answer[0]
    answer[0] = dict(cached, args={"changed": True})
    assert jobs._current_cached_job(ctx, cached) is None
    answer[0] = None
    assert jobs._current_cached_job(ctx, cached) is None
    assert calls == [(job_id, True)] * 3

    ctx = jobs.Context(tmp_path / "clients", jobs_root=tmp_path / "first")
    ctx.root = tmp_path / "second"
    paths = []
    monkeypatch.setattr(jobs, "_read", lambda path: paths.append(path) or dict(cached))
    assert jobs._current_cached_job(ctx, cached) == cached
    assert paths == [ctx.root / (job_id + ".json")]
    query = jobs._queries(ctx.root)
    monkeypatch.setattr(jobs, "_read", lambda path: None)
    assert query.get(job_id) is None


@pytest.mark.parametrize("trusted", [False, True])
def test_default_and_trusted_composition_execute(tmp_path, monkeypatch, trusted):
    root = tmp_path / "jobs"
    binding = jobs.filesystem_job_binding(root)
    ctx = jobs.Context(tmp_path / "clients", jobs_root=root,
                       job_binding=binding if trusted else None,
                       job_queries=binding.queries if trusted else None)
    job = jobs.submit(root, "inbox_read")
    monkeypatch.setitem(jobs.HANDLERS, "inbox_read", lambda *_: {"synthetic": True})
    assert jobs.run_job(ctx, job)["state"] == "done"
    assert not (root / (job["id"] + ".json")).exists()
    assert jobs.get(root, job["id"])["result"] == {"synthetic": True}


@pytest.mark.parametrize("conflict,kind", [
    ("foreign_root", "inbox_read"), ("foreign_profile", "portal_process"),
    ("custom_query", "portal_upload"), ("custom_reader", "staff_upload"),
    ("changed_hooks", "drive_intake"), ("changed_root", "staff_upload"),
])
def test_execution_rejects_untrusted_configuration_before_effects(tmp_path, monkeypatch, conflict, kind):
    from dataclasses import replace
    from types import SimpleNamespace

    root = tmp_path / "jobs"
    binding = jobs.filesystem_job_binding(root)
    ctx = jobs.Context(tmp_path / "clients", jobs_root=root, job_binding=binding)
    if conflict == "foreign_root":
        ctx.job_binding = jobs.filesystem_job_binding(tmp_path / "foreign")
    elif conflict == "foreign_profile":
        ctx.job_binding = replace(binding, profile="postgresql")
    elif conflict == "custom_query":
        ctx.job_queries = SimpleNamespace(root=root, profile="filesystem-v1", get=lambda *_: {})
    elif conflict == "custom_reader":
        ctx.job_queries = FilesystemJobQueries(root, reader=lambda *_: {})
    elif conflict == "changed_hooks":
        binding.queries.reader = lambda *_: {}
    else:
        ctx.root = tmp_path / "changed"

    def forbidden(*_, **__):
        pytest.fail("Configuration conflict must precede gates, stores, handlers and upload preparation")

    for name in ("installation_gate", "case_lock", "_read", "_write", "_active", "_current_drive"):
        monkeypatch.setattr(jobs, name, forbidden)
    monkeypatch.setattr(ctx, "store", forbidden)
    monkeypatch.setattr(jobs, "_read_upload_without_writer_gate", forbidden)
    monkeypatch.setitem(jobs.HANDLERS, kind, forbidden)
    job = {"id": "00000000000000000001-staff_upload-12345678", "kind": kind,
           "client": "synthetic", "args": {"staff_receipt": "synthetic"}}
    with pytest.raises(jobs.JobConfigurationConflict):
        jobs.run_job(ctx, job)
    assert not root.exists() and not ctx.root.exists()


def test_untrusted_configuration_after_relative_root_changes_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("I485_JOBS", "jobs")
    ctx = jobs.Context(tmp_path / "clients")
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    monkeypatch.setattr(jobs, "installation_gate", lambda *_: pytest.fail("Foreign root gate"))
    with pytest.raises(jobs.JobConfigurationConflict):
        jobs.run_job(ctx, {"kind": "inbox_read"})
    assert not (tmp_path / "jobs").exists() and not (other / "jobs").exists()


@pytest.mark.parametrize("entry", ["_repair_portal", "_sweep_cached", "_sweep_drive",
                                    "sweep_dead", "_read_upload_without_writer_gate",
                                    "_run_current_job", "_run_job", "_run_locked_job", "_died"])
def test_mutated_context_root_rejects_recovery_and_direct_execution_before_effects(tmp_path, monkeypatch, entry):
    ctx = jobs.Context(tmp_path / "clients", portal=tmp_path / "portal", jobs_root=tmp_path / "jobs")
    ctx.root = tmp_path / "foreign"

    def forbidden(*_, **__):
        pytest.fail("Invalid binding reached a mutation dependency")

    for name in ("installation_gate", "case_lock", "_read", "_write", "_active"):
        monkeypatch.setattr(jobs, name, forbidden)
    monkeypatch.setattr(ctx, "store", forbidden)
    job = {"id": "00000000000000000001-portal_process-12345678", "kind": "portal_process",
           "client": "synthetic", "state": "running", "args": {}}
    with pytest.raises(jobs.JobConfigurationConflict):
        fn = getattr(jobs, entry)
        fn(ctx) if entry == "sweep_dead" else fn(ctx, job)
    assert not ctx.root.exists()


@pytest.mark.parametrize("outcome", ["return", "raise", "retry", "progress"])
def test_configuration_conflict_during_handler_does_not_publish_failure(tmp_path, monkeypatch, outcome):
    root = tmp_path / "jobs"
    ctx = jobs.Context(tmp_path / "clients", jobs_root=root)
    job = jobs.submit(root, "inbox_read")

    def handler(ctx, job, progress):
        ctx.root = tmp_path / "changed"
        if outcome == "raise":
            raise ValueError("synthetic handler failure")
        if outcome == "retry":
            raise jobs.Again(1)
        if outcome == "progress":
            progress(1, 2, "synthetic")
        return {}

    monkeypatch.setitem(jobs.HANDLERS, "inbox_read", handler)
    monkeypatch.setitem(jobs.HANDLERS_FAILED, "inbox_read", lambda *_: pytest.fail("Failure callback"))
    with pytest.raises(jobs.JobConfigurationConflict):
        jobs.run_job(ctx, job)
    assert jobs.get(root, job["id"])["state"] == "running"
    assert not (root / "done" / (job["id"] + ".json")).exists()
    assert not ctx.root.exists()


@pytest.mark.parametrize("boundary", ["write", "unlink"])
def test_finish_preserves_object_mutation_exception_and_residue(tmp_path, monkeypatch, boundary):
    root = tmp_path / "jobs"
    job = jobs.submit(root, "inbox_read")
    active = root / (job["id"] + ".json")
    done = root / "done" / active.name
    failure = OSError("synthetic " + boundary)
    if boundary == "write":
        def write(*_):
            raise failure
        monkeypatch.setattr(jobs, "_write", write)
    else:
        unlink = Path.unlink
        def fail_active(path, *args, **kwargs):
            if path == active:
                raise failure
            return unlink(path, *args, **kwargs)
        monkeypatch.setattr(Path, "unlink", fail_active)
    with pytest.raises(OSError) as caught:
        jobs._finish(root, job, "done", result={"synthetic": True})
    assert caught.value is failure
    assert job["state"] == "done" and job["finished"] and job["result"] == {"synthetic": True}
    assert json.loads(active.read_text())["state"] == "queued"
    assert done.exists() == (boundary == "unlink")
    if boundary == "unlink":
        assert json.loads(done.read_text()) == job


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_JOBS", str(tmp_path / "jobs"))
    monkeypatch.setenv("I485_JOBS_WORKER", "0")
    for key, name in {"I485_RULES_APPROVED": "rules_approved.json",
            "I485_MAINTENANCE_LOG": "maintenance_log.json", "I485_EVENTS": "events.jsonl",
            "I485_SETTINGS": "settings.json", "I485_CASES": "clients",
            "I485_PROSPECTS": "prospects", "PORTAL_DATA": "portal"}.items():
        monkeypatch.setenv(key, str(tmp_path / "data" / name))
    (tmp_path / "data" / "clients").mkdir(parents=True)
    return tmp_path / "jobs"


def clients(root: Path) -> Path:
    return root.parent / "data" / "clients"


# -- the queue ----------------------------------------------------------------------------------------------------------------


def test_a_job_is_written_down_and_a_screen_is_told_of_it_in_words(root):
    job = jobs.submit(root, "staff_upload", "case-ana", by="Paulo Paralegal", args={"name": "scan_passport.pdf", "pages": 2})
    assert (root / f"{job['id']}.json").exists() and job["state"] == "queued"
    seen = jobs.view(job)
    assert seen["label"] == "Waiting to be read" and seen["what"] == "A scan the office added" and seen["client"] == "case-ana"
    assert "args" not in seen and "scan_passport" not in json.dumps(seen)  # a file's name is never sent to a screen
    with pytest.raises(ValueError):
        jobs.submit(root, "something_else")
    assert jobs.counts(root) == {"waiting": 1, "running": 0} and jobs.pending(root, "case-ana") and not jobs.pending(root, "case-bia")
    assert [j["id"] for j in jobs.jobs(root, client="case-ana")] == [job["id"]] and jobs.jobs(root, client="case-bia") == []
    assert jobs.get(root, job["id"])["args"]["pages"] == 2 and jobs.get(root, "../../etc/passwd") is None  # an id is a name, never a path


def test_the_worker_runs_the_jobs_one_at_a_time_in_the_order_they_came_and_reports_how_far_it_is(root, monkeypatch):
    seen, running, most = [], [0], [0]

    def handler(ctx, job, progress):
        running[0] += 1
        most[0] = max(most[0], running[0])
        progress(1, 3, "Reading the scan")
        mid = jobs.get(ctx.root, job["id"])
        seen.append((job["client"], mid["state"], mid["progress"]["step"], jobs.view(mid)["text"], jobs.view(mid)["label"]))
        progress(2, 3, "Adding it to the case")
        running[0] -= 1
        return {"read": job["client"]}

    monkeypatch.setitem(jobs.HANDLERS, "staff_upload", handler)
    ids = [jobs.submit(root, "staff_upload", f"case-{n}", by="Paulo")["id"] for n in range(3)]
    assert jobs.work(clients(root), None, once=True) == 3
    assert [s[0] for s in seen] == ["case-0", "case-1", "case-2"] and most[0] == 1
    assert seen[0][1:] == ("running", 1, "Reading the scan", "Reading now")
    for n, job_id in enumerate(ids):
        done = jobs.get(root, job_id)
        assert done["state"] == "done" and done["result"] == {"read": f"case-{n}"} and done["finished"] and (root / "done" / f"{job_id}.json").exists()
        assert not (root / f"{job_id}.json").exists() and jobs.view(done)["label"] == "Read"
    assert jobs.counts(root) == {"waiting": 0, "running": 0}


def test_a_job_that_fails_is_marked_in_words_and_the_worker_goes_on(root, monkeypatch):
    def handler(ctx, job, progress):
        if job["client"] == "case-1":
            raise ValueError("That PDF is locked with a password.")
        if job["client"] == "case-2":
            raise RuntimeError("Traceback with a path /home/x/file.py in it")
        return {"ok": True}

    monkeypatch.setitem(jobs.HANDLERS, "staff_upload", handler)
    ids = [jobs.submit(root, "staff_upload", f"case-{n}")["id"] for n in range(4)]
    assert jobs.work(clients(root), None, once=True) == 4
    states = [jobs.get(root, i)["state"] for i in ids]
    assert states == ["done", "failed", "failed", "done"]
    assert jobs.view(jobs.get(root, ids[1]))["why"] == "That PDF is locked with a password."
    assert jobs.view(jobs.get(root, ids[2]))["why"] == "Could not be read: it will be read tonight"  # never an exception's own text, a path or a class name


def test_a_job_that_died_with_its_process_says_so_and_the_photo_is_put_back_for_tonight(root, tmp_path):
    from portal.store import PortalStore

    (clients(root) / "case-ana").mkdir(parents=True)
    store = PortalStore(tmp_path / "data" / "portal")
    store.add_client("case-ana", "Ana Clara Exemplo Souza", email="ana@example.com", language="pt")
    store.add_upload("case-ana", "passport", "photo.png", _png(), "image/png")
    store.mark_reading("case-ana", "now")
    assert store.uploads("case-ana")[0]["reading"] == "now"
    job = jobs.submit(root, "portal_upload", "case-ana", by="The client")
    job.update(state="running", pid=2 ** 22 + 1, started="2026-10-03T09:00:00-04:00")  # the process that was reading it is gone
    (root / f"{job['id']}.json").write_text(json.dumps(job), encoding="utf-8")
    marked = jobs.sweep(clients(root), tmp_path / "data" / "portal")
    assert marked == ["case-ana"]
    died = jobs.get(root, job["id"])
    assert died["state"] == "died" and jobs.view(died)["label"] == "Did not finish: will be read tonight" and jobs.view(died)["why"] == "Did not finish: will be read tonight"
    assert store.uploads("case-ana")[0]["reading"] == "tonight"  # the stale "being read now" label is gone
    assert not jobs.pending(root, "case-ana") and jobs.sweep(clients(root), tmp_path / "data" / "portal") == []


def test_a_job_running_while_a_worker_lives_is_not_called_dead(root, tmp_path):
    started, release = threading.Event(), threading.Event()

    def long(ctx, job, progress):
        started.set()
        release.wait(10)
        return {}

    jobs.HANDLERS["staff_upload"], real = long, jobs.HANDLERS["staff_upload"]
    try:
        job = jobs.submit(root, "staff_upload", "case-a")
        t = threading.Thread(target=jobs.work, args=(clients(root), None), kwargs={"once": True})
        t.start()
        assert started.wait(10) and jobs.alive(root)
        assert jobs.sweep(clients(root), None) == [] and jobs.get(root, job["id"])["state"] == "running"
        release.set()
        t.join(10)
        assert jobs.get(root, job["id"])["state"] == "done" and not jobs.alive(root)
    finally:
        jobs.HANDLERS["staff_upload"] = real


def test_there_is_only_one_worker(root, capsys):
    root.mkdir(parents=True, exist_ok=True)
    lock = os.open(root / "worker.lock", os.O_CREAT | os.O_RDWR)
    assert jobs._try_lock(lock) and jobs.alive(root)
    assert jobs.work(clients(root), None, once=True, log=lambda text: print(text)) == 0  # a second one cannot take the lock and leaves
    assert "Another worker is running" in capsys.readouterr().out
    jobs._unlock(lock)
    os.close(lock)
    assert not jobs.alive(root)


# -- the lock -----------------------------------------------------------------------------------------------------------------


def test_a_case_lock_excludes_other_threads_but_not_other_cases_and_not_itself(root):
    order = []
    with jobs.case_lock(root, "case-a"):
        with jobs.case_lock(root, "case-a"):  # the code a job calls takes the case's lock again: it is the same holder
            order.append("inner")
        with jobs.case_lock(root, "case-b", timeout=0.2):  # another case is free
            order.append("other case")

        def other():
            try:
                with jobs.case_lock(root, "case-a", timeout=0.3):
                    order.append("took it")
            except jobs.CaseBusy as exc:
                order.append(str(exc))

        t = threading.Thread(target=other)
        t.start()
        t.join(5)
    assert order == ["inner", "other case", "This case is being read right now. Try again in a moment."]
    with jobs.case_lock(root, "case-a", timeout=0.2):  # released
        order.append("free again")
    assert order[-1] == "free again"


def test_a_case_lock_holds_across_processes(root):
    code = ("import sys, time; sys.path.insert(0, 'src'); import jobs; "
            f"c = jobs.case_lock({str(root)!r}, 'case-a'); c.__enter__(); print('held', flush=True); time.sleep(2.5); c.__exit__(None, None, None)")
    holder = subprocess.Popen([sys.executable, "-c", code], cwd=REPO, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(jobs.CaseBusy):
            with jobs.case_lock(root, "case-a", timeout=0.3):
                pass
        started = time.monotonic()
        with jobs.case_lock(root, "case-a"):  # waits as long as it takes: the other process lets go
            assert time.monotonic() - started > 0.5
    finally:
        holder.wait(10)


def test_reading_a_case_waits_for_whoever_holds_it_so_no_write_is_lost(root, tmp_path, monkeypatch):
    """The notice inbox, a staff upload and the portal's photo all end in inbox.reprocess_documents: it takes the case's lock first."""
    import inbox

    case = tmp_path / "data" / "clients" / "case-ana"
    case.mkdir(parents=True)
    log = []
    monkeypatch.setattr(inbox, "_reprocess_documents", lambda *a, **k: (log.append("reprocess"), {"type": "x"})[1])
    holding, go = threading.Event(), threading.Event()

    def holder():
        with jobs.case_lock(root, "case-ana"):
            holding.set()
            go.wait(5)
            log.append("holder let go")

    h = threading.Thread(target=holder)
    h.start()
    assert holding.wait(5)
    t = threading.Thread(target=lambda: inbox.reprocess_documents(case, tmp_path, [("a.pdf", "x")], {}))
    t.start()
    time.sleep(0.4)
    assert log == []  # still waiting for the case
    go.set()
    h.join(5)
    t.join(5)
    assert log == ["holder let go", "reprocess"]


def test_the_overnight_run_takes_each_cases_lock_while_it_works_on_it(root, tmp_path, monkeypatch):
    import overnight
    import process_clients

    out = tmp_path / "data" / "clients"
    log = []
    monkeypatch.setattr(process_clients, "process_one", lambda name, source, o, ctx: (log.append(name), {"client": name, "counts": {}, "seconds": 0, "errors": [], "documents": 0})[1])
    holding, go = threading.Event(), threading.Event()

    def holder():
        with jobs.case_lock(root, "case-ana"):
            holding.set()
            go.wait(5)

    h = threading.Thread(target=holder)
    h.start()
    assert holding.wait(5)
    t = threading.Thread(target=lambda: overnight._run_one("case-ana", str(tmp_path / "src"), str(out / "case-ana")))
    t.start()
    time.sleep(0.4)
    assert log == []
    go.set()
    h.join(5)
    t.join(5)
    assert log == ["case-ana"]


def test_the_vision_model_is_used_by_one_reading_at_a_time(root):
    order = []

    def reading(name):
        with jobs.gpu_lock(root):
            order.append(f"{name} in")
            time.sleep(0.3)
            order.append(f"{name} out")

    threads = [threading.Thread(target=reading, args=(n,)) for n in "ab"]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert order[0].endswith("in") and order[1] == order[0].replace("in", "out") and order[2].endswith("in") and order[3].endswith("out")  # never two inside at once


# -- the review app ---------------------------------------------------------------------------------------------------------


def test_the_review_apps_writes_wait_for_a_reading_and_then_say_the_case_is_being_read(tmp_path, monkeypatch):
    import scale_world

    w = scale_world.build(tmp_path, 8, walk="0", warm=False)
    try:
        case = w.manifest["case_ids"][0]
        w.patch.setattr(jobs, "BUSY_WAIT", 0.4)
        reading, go = threading.Event(), threading.Event()

        def worker():
            with jobs.case_lock(w.app.jobs_root, case):  # the worker is reading the case's new photo
                reading.set()
                go.wait(10)

        t = threading.Thread(target=worker)
        t.start()
        assert reading.wait(5)
        status, answer = w.call("/api/decide", "attorney", {"client": case, "item_id": "x", "action": "confirm"})[1:]
        assert status == 409 and answer["error"] == "This case is being read right now. Try again in a moment."
        assert w.call(f"/api/items?client={case}", "attorney")[1] == 200  # looking at the case never waits
        other = w.manifest["case_ids"][1]
        assert w.call("/api/decide", "attorney", {"client": other, "item_id": "x", "action": "confirm"})[1] == 400  # another case: not locked (the item is made up)
        go.set()
        t.join(5)
        assert w.call("/api/decide", "attorney", {"client": case, "item_id": "x", "action": "confirm"})[1] == 400  # free again: it gets as far as the item
    finally:
        w.stop()


def test_adding_a_scan_does_not_wait_for_the_reading_of_the_last_one(tmp_path):
    import base64

    import scale_world
    from review.server import LOCK_FREE

    assert "/api/client-upload" in LOCK_FREE
    w = scale_world.build(tmp_path, 4, walk="0", warm=False, sources=4)
    try:
        w.patch.setenv("I485_CLIENTS_ROOT", w.manifest["inputs"])
        case = w.manifest["case_ids"][0]
        with jobs.case_lock(w.app.jobs_root, case):  # a reading of this case is under way
            import io

            from pypdf import PdfWriter

            writer, buf = PdfWriter(), io.BytesIO()
            writer.add_blank_page(width=612, height=792)
            writer.write(buf)
            pdf = base64.b64encode(buf.getvalue()).decode()
            started = time.monotonic()
            payload = {"client": case, "name": "second scan.pdf", "data": pdf, "attempt": "c" * 32}
            before = sorted(p.name for p in (Path(w.manifest["inputs"]) / case / "source").glob("*.pdf"))
            status, answer = w.call("/api/client-upload", "attorney", payload)[1:]
            assert status == 409 and answer["error"] == "This case is being read right now. Try again in a moment."
            assert time.monotonic() - started < 3
            assert not (w.clients / case / "staff-upload-receipts").exists()
            assert not jobs.pending(w.app.jobs_root, case)
            assert sorted(p.name for p in (Path(w.manifest["inputs"]) / case / "source").glob("*.pdf")) == before
        status, answer = w.call("/api/client-upload", "attorney", payload)[1:]
        assert status == 200 and answer["received"] and answer["reading"] and not answer["processed"]
        assert answer["job"]["state"] == "queued"
        assert len(jobs.jobs(w.app.jobs_root, client=case, kind="staff_upload")) == 1
        assert jobs.pending(w.app.jobs_root, case)
        view = w.get(f"/api/jobs?client={case}")  # the Documents tab asks this: "Reading now" or "Waiting to be read", with how far it is
        assert [j["what"] for j in view["jobs"]] == ["A scan the office added"] and view["jobs"][0]["label"] == "Waiting to be read" and view["worker"] is False
    finally:
        w.stop()


def test_a_job_on_a_case_is_shown_only_to_someone_who_may_open_it_and_its_answer_only_to_who_started_it(tmp_path):
    import scale_world

    w = scale_world.build(tmp_path, 40, walk="0", warm=False)
    try:
        paralegal = w.person("paralegal")
        import restricted

        hidden = next(c for c in w.manifest["restricted_ids"] if not restricted.visible_to(paralegal, w.clients / c))
        job = jobs.submit(w.app.jobs_root, "staff_upload", hidden, by=w.person("attorney")["name"], args={"name": "x.pdf"})
        assert w.call(f"/api/jobs?client={hidden}", "paralegal")[1:] == w.call("/api/jobs?client=nobody-here-0000", "paralegal")[1:]  # the one answer for a hidden case and no case
        assert w.call(f"/api/jobs/firm?id={job['id']}", "paralegal")[1] == 404
        seen = w.get(f"/api/jobs/firm?id={job['id']}", "attorney")["job"]
        assert seen["state"] == "queued" and seen["result"] is None
        firm = w.get("/api/jobs/firm", "paralegal")
        assert firm["counts"]["waiting"] == 1 and hidden not in json.dumps(firm)  # the count, never the case
    finally:
        w.stop()


# -- the worker as the app starts it ------------------------------------------------------------------------------------------------


def test_the_app_starts_a_worker_when_none_is_registered_and_it_leaves_with_the_app(root, tmp_path, monkeypatch):
    """A laptop with only the app: ensure_worker starts one (it reads a job within the minute), and when the app's process ends the worker does too."""
    monkeypatch.setenv("I485_JOBS_WORKER", "1")
    monkeypatch.setenv("I485_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("I485_INDEX", str(tmp_path / "index.db"))
    data = clients(root)
    code = ("import sys, time; sys.path.insert(0, 'src'); import jobs; "
            f"print(jobs.ensure_worker({str(data)!r}, None), flush=True); time.sleep(300)")
    app = subprocess.Popen([sys.executable, "-c", code], cwd=REPO, stdout=subprocess.PIPE, text=True, env=os.environ.copy())
    try:
        assert app.stdout.readline().strip() == "True"
        end = time.time() + 30
        while not jobs.alive(root) and time.time() < end:
            time.sleep(0.2)
        assert jobs.alive(root)
        job = jobs.submit(root, "inbox_read", None, by="Paulo")  # the empty inbox: nothing to read, and a worker that answers
        end = time.time() + 120
        while time.time() < end and jobs.get(root, job["id"])["state"] not in ("done", "failed"):
            time.sleep(0.5)
        done = jobs.get(root, job["id"])
        assert done["state"] == "done" and done["result"]["text"].startswith("Notice inbox: 0 notices routed")
        assert not any(p.name.endswith(".lock") and p.name != "worker.lock" and p.name != "gpu.lock" for p in root.iterdir())  # no stray lock files
    finally:
        app.kill()
        app.wait(10)
    end = time.time() + 60
    while jobs.alive(root) and time.time() < end:  # the worker watches its parent
        time.sleep(0.5)
    assert not jobs.alive(root)


def test_the_worker_run_as_a_program_reads_a_scan_without_waiting_on_its_own_lock(tmp_path):
    """python src/jobs.py --worker: the program's module and the one the pipeline imports as jobs are the same, so the case lock the job holds is the one reprocess_documents
    asks for again (a second copy of the module once left a worker waiting on itself for ever)."""
    import base64
    import io

    import scale_world
    from pypdf import PdfWriter

    w = scale_world.build(tmp_path, 3, walk="0", warm=False, sources=3)
    try:
        w.patch.setenv("I485_CLIENTS_ROOT", w.manifest["inputs"])
        case = w.manifest["case_ids"][0]
        writer, buf = PdfWriter(), io.BytesIO()
        writer.add_blank_page(width=612, height=792)
        writer.write(buf)
        status, answer = w.call("/api/client-upload", "attorney", {"client": case, "name": "scan.pdf", "data": base64.b64encode(buf.getvalue()).decode(), "attempt": "d" * 32})[1:]
        assert status == 200
        done = subprocess.run([sys.executable, "src/jobs.py", "--worker", "--once", "--data", str(w.clients), "--portal", str(w.portal)], cwd=REPO, env=os.environ.copy(),
                              capture_output=True, text=True, timeout=180)
        assert done.returncode == 0, done.stderr[-800:]
        finished = jobs.get(w.app.jobs_root, answer["job"]["id"])
        assert finished["state"] == "done" and finished["result"]["processed"] is True, finished
    finally:
        w.stop()


def test_a_worker_is_not_started_when_it_is_turned_off_or_one_is_there(root, tmp_path, monkeypatch):
    assert jobs.ensure_worker(clients(root), None) is False  # I485_JOBS_WORKER=0
    monkeypatch.setenv("I485_JOBS_WORKER", "1")
    root.mkdir(parents=True, exist_ok=True)
    lock = os.open(root / "worker.lock", os.O_CREAT | os.O_RDWR)
    try:
        assert jobs._try_lock(lock)
        started = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
        assert jobs.ensure_worker(clients(root), None) is True and started == []  # one is running: nothing more is started
    finally:
        jobs._unlock(lock)
        os.close(lock)


# -- the portal ---------------------------------------------------------------------------------------------------------------------


def _png() -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, format="PNG")
    return out.getvalue()


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    from portal import demo as seed
    from portal.store import PortalStore

    root = tmp_path_factory.mktemp("jobs-portal")
    seed.seed(PortalStore(root / "portal"), root / "clients")
    return root


def test_a_photo_the_client_sends_is_written_down_and_the_phone_is_answered_at_once(seeded, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from portal import demo as seed
    from portal.app import create_app
    from portal.engine import arrived
    from portal.store import PortalStore
    from communication_fixture import installation, approve_client, accepted_link

    ID = seed.DEMO_ID
    shutil.copytree(seeded, tmp_path / "w" / "data")
    root = installation(tmp_path / "w", monkeypatch)
    meta = json.loads((root / "clients" / ID / "meta.json").read_text(encoding="utf-8"))
    meta["source_folder"] = str(root / "portal" / "clients" / ID / "uploads")
    (root / "clients" / ID / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setenv("I485_CASES", str(root / "clients"))
    monkeypatch.setenv("I485_JOBS", str(root / "jobs"))
    store = PortalStore(root / "portal")
    original = store.profile(ID)
    # Retain the fictional source/answer fixture, but enroll this canonical
    # installation through the real writer; never copy a legacy identity grant.
    legacy = tmp_path / "legacy-fictional-intake"
    shutil.move(store.client_dir(ID), legacy)
    store.add_client(ID, original["name"], phone=original["phone"], email=original["email"],
                     language=original["language"], by="Fictional Staff")
    for name in ("answers.json", "uploads.json", "uploads", "tasks.json", "skipped_tasks.json", "requests.json"):
        if (legacy / name).is_dir():
            shutil.copytree(legacy / name, store.client_dir(ID) / name)
        elif (legacy / name).is_file():
            shutil.copyfile(legacy / name, store.client_dir(ID) / name)
    client = TestClient(create_app(root / "portal"))
    approve_client(store, ID)
    assert client.get(f"/l/{accepted_link(store, ID)}", follow_redirects=False).status_code == 303
    jobs_root = root / "jobs"
    lines = seed.DOCUMENTS["passport"][1] + ["EXEMPLO: THE PHONE'S PHOTO"]
    sent = client.post("/api/upload", data={"doc_id": "passport"}, files={"file": ("new.pdf", seed.document_pdf(lines), "application/pdf")}, headers={"X-Portal": "1"})
    assert sent.status_code == 200  # the answer does not wait for the reading
    [job] = jobs.jobs(jobs_root, client=ID, kind="portal_upload")
    assert job["state"] == "queued" and job["by"] == "The client"
    [photo] = [u for u in store.uploads(ID) if u["status"] == "received"]
    assert photo["reading"] == "tonight"  # no worker has the lock: the office's list says tonight, and the first worker to start reads it
    client.post("/api/upload", data={"doc_id": "passport"}, files={"file": ("again.pdf", seed.document_pdf(lines + ["MORE"]), "application/pdf")}, headers={"X-Portal": "1"})
    assert len(jobs.jobs(jobs_root, client=ID, kind="portal_upload")) == 1  # a photo already waiting reads this one too

    from portal import engine as portal_engine
    actual_read, actual_pipeline = portal_engine._read_upload, portal_engine.process_documents
    read_calls, pipeline_calls = [], []
    def read_once(folder, upload):
        read_calls.append(upload["stored"])
        return actual_read(folder, upload)
    def pipeline_once(*args, **kwargs):
        pipeline_calls.append(True)
        return actual_pipeline(*args, **kwargs)
    monkeypatch.setattr(portal_engine, "_read_upload", read_once)
    monkeypatch.setattr(portal_engine, "process_documents", pipeline_once)
    target_names = [u["stored"] for u in store.uploads(ID) if u["status"] == "received"]
    done = jobs.work(root / "clients", root / "portal", once=True)
    assert pipeline_calls == [True] and len(target_names) == 2
    assert all(read_calls.count(name) == 1 for name in target_names)
    assert done == 1 and arrived(store.uploads(ID)) == []  # read: no longer "not read yet"
    assert [u["status"] for u in store.uploads(ID) if u["doc_id"] == "passport"][-1] == "checked"
    finished = jobs.get(jobs_root, job["id"])
    assert finished["state"] == "done" and finished["result"]["read"] == 2 and finished["progress"]["step"] == 3
    ledger = [json.loads(line) for f in Path(os.environ["I485_EVENTS"]).parent.glob("events-*.jsonl") for line in f.read_text(encoding="utf-8").splitlines()]
    assert any(r["case"] == ID and r["what"] == "Finished reading what was added to the case" for r in ledger)  # the lists that follow the ledger read the case once, finished


def test_the_portal_marks_a_photo_as_being_read_now_only_when_a_worker_is_there(seeded, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from portal import demo as seed
    from portal.app import create_app
    from portal.store import PortalStore
    from communication_fixture import installation, approve_client, accepted_link

    ID = seed.DEMO_ID
    shutil.copytree(seeded, tmp_path / "w" / "data")
    root = installation(tmp_path / "w", monkeypatch)
    meta = json.loads((root / "clients" / ID / "meta.json").read_text(encoding="utf-8"))
    meta["source_folder"] = str(root / "portal" / "clients" / ID / "uploads")
    (root / "clients" / ID / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setenv("I485_CASES", str(root / "clients"))
    monkeypatch.setenv("I485_JOBS", str(root / "jobs"))
    store = PortalStore(root / "portal")
    original = store.profile(ID)
    legacy = tmp_path / "legacy-fictional-intake"
    shutil.move(store.client_dir(ID), legacy)
    store.add_client(ID, original["name"], phone=original["phone"], email=original["email"],
                     language=original["language"], by="Fictional Staff")
    for name in ("answers.json", "uploads.json", "uploads", "tasks.json", "skipped_tasks.json", "requests.json"):
        if (legacy / name).is_dir():
            shutil.copytree(legacy / name, store.client_dir(ID) / name)
        elif (legacy / name).is_file():
            shutil.copyfile(legacy / name, store.client_dir(ID) / name)
    client = TestClient(create_app(root / "portal"))
    approve_client(store, ID)
    assert client.get(f"/l/{accepted_link(store, ID)}", follow_redirects=False).status_code == 303
    (root / "jobs").mkdir(parents=True)
    lock = os.open(root / "jobs" / "worker.lock", os.O_CREAT | os.O_RDWR)
    try:
        assert jobs._try_lock(lock)
        client.post("/api/upload", data={"doc_id": "passport"}, files={"file": ("a.pdf", seed.document_pdf(["EXEMPLO: A"]), "application/pdf")}, headers={"X-Portal": "1"})
        assert [u["reading"] for u in store.uploads(ID) if u["status"] == "received"] == ["now"]
    finally:
        jobs._unlock(lock)
        os.close(lock)
    monkeypatch.setenv("PORTAL_READ_AT_ONCE", "0")  # a host that must not read documents: no job at all, the night reads it
    for j in jobs.jobs(root / "jobs"):
        (root / "jobs" / f"{j['id']}.json").unlink()
    client.post("/api/upload", data={"doc_id": "passport"}, files={"file": ("b.pdf", seed.document_pdf(["EXEMPLO: B"]), "application/pdf")}, headers={"X-Portal": "1"})
    assert jobs.jobs(root / "jobs") == [] and {u["reading"] for u in store.uploads(ID) if u["status"] == "received"} == {"tonight"}
