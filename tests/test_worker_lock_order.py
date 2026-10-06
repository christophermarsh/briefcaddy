"""Real installation/case OS locks, fictional records, bounded synchronization."""
import json
import threading
from contextlib import contextmanager

import jobs
import pytest
from portal import communication_consent as consent
from test_source_association import firm  # noqa: F401 -- current canonical fixture


def canonical_env(monkeypatch, data):
    for key, relative in {"I485_JOBS": "jobs", "I485_RULES_APPROVED": "rules_approved.json",
            "I485_MAINTENANCE_LOG": "maintenance_log.json", "I485_EVENTS": "events.jsonl",
            "I485_SETTINGS": "settings.json", "I485_CASES": "clients", "I485_PROSPECTS": "prospects",
            "PORTAL_DATA": "portal"}.items():
        monkeypatch.setenv(key, str(data / relative))


def test_actual_purge_worker_does_not_hold_case_while_waiting_for_installation_gate(tmp_path, monkeypatch):
    data = tmp_path / "fictional-firm" / "data"
    cases = data / "clients"
    cases.mkdir(parents=True)
    queue = data / "jobs"
    canonical_env(monkeypatch, data)
    # Not eligible for destruction. The actual purge handler enters its real
    # gate then refuses; no deletion, identity or legal approval is fabricated.
    (data / "purges.json").write_text(json.dumps({"cases": {"fictional-case": {
        "id": "fictional-purge", "state": "waiting", "purge_on": "2099-01-01",
        "needs_confirm": True, "confirmed_by": None}}}))
    context = jobs.Context(cases, data / "portal")
    job = jobs.submit(queue, "purge", args={"purge": "fictional-purge"})
    attempting_gate = threading.Event()
    original_gate = consent.gate
    failures = []

    @contextmanager
    def observed_gate(scope, timeout=10):
        if threading.current_thread().name == "fictional-worker":
            attempting_gate.set()
        with original_gate(scope, timeout=2):
            yield

    monkeypatch.setattr(consent, "gate", observed_gate)

    def worker():
        try:
            jobs.run_job(context, job)
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=worker, name="fictional-worker", daemon=True)
    acquired = False
    try:
        with consent.data_gate(data):
            thread.start()
            assert attempting_gate.wait(3), "Actual worker did not reach installation gate"
            try:
                with jobs.case_lock(queue, "fictional-case", timeout=0.25):
                    acquired = True
            except jobs.CaseBusy:
                pass
    finally:
        if thread.ident is not None:
            thread.join(4)
    assert not thread.is_alive() and not failures
    assert acquired, "Worker held case lock while blocked on installation gate"
    assert jobs.get(queue, job["id"])["state"] == "failed"
    assert (data / "purges.json").exists()


@pytest.mark.parametrize("change", ["removed", "args", "state"])
def test_cached_job_is_not_recreated_after_waiting(change, tmp_path, monkeypatch):
    data = tmp_path / "fictional-firm" / "data"
    cases = data / "clients"
    cases.mkdir(parents=True)
    canonical_env(monkeypatch, data)
    ctx = jobs.Context(cases)
    job = jobs.submit(ctx.root, "staff_upload", client="fictional-case", args={"name": "fictional.pdf"})
    attempts, called, errors = threading.Event(), [], []
    original = consent.gate

    @contextmanager
    def observed(scope, timeout=10):
        if threading.current_thread().name == "fictional-worker":
            attempts.set()
        with original(scope, timeout=2):
            yield

    monkeypatch.setattr(consent, "gate", observed)
    monkeypatch.setitem(jobs.HANDLERS, "staff_upload", lambda *_: called.append(True))
    result = []

    def worker():
        try:
            result.append(jobs.run_job(ctx, job))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker, name="fictional-worker", daemon=True)
    with consent.data_gate(data):
        thread.start()
        assert attempts.wait(3)
        path = ctx.root / (job["id"] + ".json")
        if change == "removed":
            path.unlink()
        else:
            changed = dict(job, args={"name": "different.pdf"}) if change == "args" else dict(job, state="cancelled")
            path.write_text(json.dumps(changed))
    thread.join(4)
    assert not thread.is_alive() and not errors and not called
    assert result[0]["state"] == "unavailable"
    assert not (ctx.root / "done" / (job["id"] + ".json")).exists()
    if change == "removed":
        assert not path.exists()
    else:
        assert json.loads(path.read_text()) == changed


def test_only_acquisition_timeout_retries_and_job_stays_queued(tmp_path, monkeypatch):
    data = tmp_path / "fictional-firm" / "data"
    cases = data / "clients"
    cases.mkdir(parents=True)
    canonical_env(monkeypatch, data)
    ctx = jobs.Context(cases)
    job = jobs.submit(ctx.root, "staff_upload", client="fictional-case")
    original = consent.data_gate
    attempts, called, pauses = [], [], []

    @contextmanager
    def once_busy(current_data):
        attempts.append(True)
        if len(attempts) == 1:
            assert jobs.get(ctx.root, job["id"])["state"] == "queued"
            raise TimeoutError("Synthetic bounded gate acquisition contention")
        with original(current_data):
            yield

    def body_timeout(*_):
        called.append(True)
        raise TimeoutError("Synthetic reader timeout; must not replay")

    monkeypatch.setattr(consent, "data_gate", once_busy)
    monkeypatch.setattr(jobs.time, "sleep", pauses.append)
    monkeypatch.setitem(jobs.HANDLERS, "staff_upload", body_timeout)
    result = jobs.run_job(ctx, job)
    assert result["state"] == "failed" and called == [True]
    assert pauses == [0.2]
    assert jobs.get(ctx.root, job["id"])["state"] == "failed"


def test_stop_during_gate_wait_leaves_unstarted_job_queued(tmp_path, monkeypatch):
    data = tmp_path / "fictional-firm" / "data"
    cases = data / "clients"
    cases.mkdir(parents=True)
    canonical_env(monkeypatch, data)
    ctx = jobs.Context(cases)
    ctx.wait_stop = lambda: True
    job = jobs.submit(ctx.root, "staff_upload", client="fictional-case")

    @contextmanager
    def busy(_):
        raise TimeoutError("Synthetic acquisition contention")
        yield

    monkeypatch.setattr(consent, "data_gate", busy)
    with pytest.raises(jobs.WorkerStopped):
        jobs.run_job(ctx, job)
    assert jobs.get(ctx.root, job["id"])["state"] == "queued"
    assert not jobs.get(ctx.root, job["id"]).get("started")


def test_direct_portal_processor_takes_gate_before_case(tmp_path, monkeypatch):
    from portal import engine
    data = tmp_path / "fictional-firm" / "data"
    cases = data / "clients"
    cases.mkdir(parents=True)
    canonical_env(monkeypatch, data)
    original = jobs.case_lock
    entered = []

    @contextmanager
    def checked(root, case, timeout=None):
        assert getattr(consent._LOCAL, "held", {}).get(str(data / "communication.lock"))
        entered.append(case)
        with original(root, case, timeout):
            yield

    monkeypatch.setattr(jobs, "case_lock", checked)
    monkeypatch.setattr(engine, "_process_client", lambda *_: {"wrapper_only": True})
    assert engine.process_client(None, "fictional-case", out_root=cases) == {"wrapper_only": True}
    assert entered == ["fictional-case"]


def test_source_association_gate_precedes_case_and_current_authority(firm, monkeypatch):  # noqa: F811
    import source_association
    scope, store, client, user, _ = firm
    original_lock, original_authority = jobs.case_lock, source_association._authority
    entered = []

    class AuthorityReached(Exception):
        pass

    @contextmanager
    def checked(root, case, timeout=None):
        assert getattr(consent._LOCAL, "held", {}).get(str(scope.data / "communication.lock"))
        entered.append(case)
        with original_lock(root, case, timeout):
            yield

    def authority(*args):
        original_authority(*args)  # actual active account and case ACL
        assert (str(scope.queue), client) in jobs._held.names
        raise AuthorityReached()  # do not invoke a model or rewrite a source

    monkeypatch.setattr(jobs, "case_lock", checked)
    monkeypatch.setattr(source_association, "_authority", authority)
    with pytest.raises(AuthorityReached):
        source_association.associate(scope.root, store, client, actor_email=user["email"])
    assert entered == [client]
    assert not (scope.cases / client / source_association.FILE).exists()


def test_overnight_producer_order_without_claiming_reproduced_cycle(tmp_path, monkeypatch):
    import overnight
    import process_clients
    data = tmp_path / "fictional-firm" / "data"
    cases = data / "clients"
    cases.mkdir(parents=True)
    canonical_env(monkeypatch, data)
    original = jobs.case_lock

    @contextmanager
    def checked(root, case, timeout=None):
        assert getattr(consent._LOCAL, "held", {}).get(str(data / "communication.lock"))
        with original(root, case, timeout):
            yield

    monkeypatch.setattr(jobs, "case_lock", checked)
    monkeypatch.setattr(process_clients, "process_one", lambda *_: {"wrapper_only": True})
    result = overnight._run_one("fictional-case", str(tmp_path / "fictional-source"), str(cases / "fictional-case"))
    assert result == {"status": "done", "wrapper_only": True}


@pytest.mark.parametrize("startup", [False, True])
def test_sweep_does_not_recreate_removed_cached_running_job(tmp_path, monkeypatch, startup):
    data = tmp_path / "fictional-firm" / "data"
    cases = data / "clients"
    cases.mkdir(parents=True)
    canonical_env(monkeypatch, data)
    ctx = jobs.Context(cases)
    job = jobs.submit(ctx.root, "staff_upload", client="fictional-case")
    job.update(state="running")
    path = ctx.root / (job["id"] + ".json")
    path.write_text(json.dumps(job))
    captured, resume = threading.Event(), threading.Event()
    original_read = jobs._read
    results, errors = [], []

    def paused_read(current_path):
        value = original_read(current_path)
        if current_path == path and threading.current_thread().name == "fictional-sweep" and not captured.is_set():
            captured.set()
            assert resume.wait(3)
        return value

    def sweep():
        try:
            results.append(jobs.sweep_dead(ctx) if startup else jobs.sweep(cases))
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(jobs, "_read", paused_read)
    thread = threading.Thread(target=sweep, name="fictional-sweep", daemon=True)
    with consent.data_gate(data):
        thread.start()
        assert captured.wait(3)
        path.unlink()  # actual removal before stale snapshot can be used
        resume.set()
    thread.join(4)
    assert not thread.is_alive() and not errors
    assert not path.exists()
    assert not (ctx.root / "done" / path.name).exists(), "Sweep recreated a removed running job"
    assert results == ([None] if startup else [[]])


@pytest.mark.parametrize("startup", [False, True])
def test_request_sweep_gate_timeout_does_not_fall_back_to_terminal_write(tmp_path, monkeypatch, startup):
    data = tmp_path / "fictional-firm" / "data"
    cases = data / "clients"
    cases.mkdir(parents=True)
    canonical_env(monkeypatch, data)
    ctx = jobs.Context(cases)
    job = jobs.submit(ctx.root, "staff_upload", client="fictional-case")
    job.update(state="running")
    path = ctx.root / (job["id"] + ".json")
    path.write_text(json.dumps(job))
    before = path.read_bytes()

    @contextmanager
    def busy(_):
        raise TimeoutError("Synthetic bounded request gate contention")
        yield

    monkeypatch.setattr(consent, "data_gate", busy)
    with pytest.raises(TimeoutError):
        jobs.sweep_dead(ctx) if startup else jobs.sweep(cases)
    assert path.read_bytes() == before
    assert not (ctx.root / "done" / path.name).exists()
