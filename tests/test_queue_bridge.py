"""Fictional portal generations; no service, notifier, or real client data."""
import json
from pathlib import Path

import pytest

import jobs
from portal import engine, queue_bridge as qb
from portal.store import PortalStore

REAL_PROCESS = engine.process_client


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("I485_JOBS", str(tmp_path / "wrong-jobs"))
    monkeypatch.setenv("I485_JOBS_WORKER", "0")
    store = PortalStore(tmp_path / "portal")
    store.add_client("fictional-a", "Fictional Alpha", language="en", consent={})
    store.add_client("fictional-b", "Fictional Beta", language="en", consent={})
    ctx = jobs.Context(tmp_path / "cases", store.root, jobs_root=tmp_path / "jobs", use_policies=False)
    calls = []

    def pipeline(store, client, out_root, use_policies):
        calls.append((client, out_root, use_policies))
        folder = Path(out_root) / client
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "meta.json").write_text(json.dumps({"client_id": client, "errors": {}}))
        (folder / "documents.json").write_text(json.dumps({"documents": [], "boundary_processing": []}))
        return {"tasks": 2, "blocking": 1, "review": 1, "informational": 0}

    monkeypatch.setattr(engine, "process_client", pipeline)
    # Internal processing must never reach an outbound provider or even queue a message.
    from portal.notify import Notifier
    monkeypatch.setattr(Notifier, "send", lambda *a, **k: pytest.fail("outbound call"))
    return ctx, store, calls, pipeline


def receipt(ctx, client, marker):
    return ctx.store().client_dir(client) / "processing-receipts" / (marker["generation"] + ".json")


def queued_job(ctx, store, client="fictional-a"):
    marker = store.enqueue(client)
    assert qb.bridge(ctx)[0]["state"] == "pending"
    job = jobs._next(ctx.root)
    return marker, job


def run_once(ctx):
    return jobs.work(ctx.clients, ctx.portal, once=True, jobs_root=ctx.root, use_policies=ctx.use_policies, log=lambda _: None)


def test_generation_and_conditional_ack(world):
    ctx, store, _, _ = world
    old = store.enqueue("fictional-a")
    assert PortalStore(store.root).queue_generation("fictional-a") == old
    new = store.enqueue("fictional-a")
    assert old["generation"] != new["generation"]
    assert store.acknowledge_queue("fictional-a", old["generation"]) == "newer_generation_retained"
    assert store.queue_generation("fictional-a") == new
    assert store.acknowledge_queue("fictional-a", new["generation"]) == "acknowledged"
    assert store.acknowledge_queue("fictional-a", new["generation"]) == "absent"
    with pytest.raises(ValueError):
        store.dequeue("fictional-a")


def test_legacy_marker_has_stable_identity(world):
    ctx, store, _, _ = world
    (store.root / "queue" / "fictional-a").write_text("2026-10-04T10:00:00+00:00")
    first = store.queue_generation("fictional-a")
    assert first == PortalStore(store.root).queue_generation("fictional-a") and first["legacy"]
    assert run_once(ctx) == 1 and not store.queued()


def test_installed_worker_bridges_queue_with_explicit_roots(world):
    ctx, store, calls, _ = world
    marker = store.enqueue("fictional-a")
    assert run_once(ctx) == 1
    assert calls == [("fictional-a", ctx.clients, False)]
    assert not store.queued() and json.loads(receipt(ctx, "fictional-a", marker).read_text())["state"] == "acked"
    assert not (ctx.root.parent / "wrong-jobs").exists()
    assert run_once(ctx) == 0


def test_newer_generation_and_metadata_change_are_retained(world, monkeypatch):
    ctx, store, calls, pipeline = world
    old, job = queued_job(ctx, store)
    newer = []

    def changed(*args, **kwargs):
        store.save_answers("fictional-a", {"dob": "2000-01-02"})
        newer.append(store.enqueue("fictional-a"))
        return pipeline(*args, **kwargs)

    monkeypatch.setattr(engine, "process_client", changed)
    done = jobs.run_job(ctx, job)
    saved = json.loads(receipt(ctx, "fictional-a", old).read_text())
    assert done["state"] == "done" and len(calls) == 1
    assert saved["acknowledgment"] == "newer_generation_retained"
    assert saved["input_changed_during_processing"] and saved["newer_generation_observed"]
    assert store.queue_generation("fictional-a") == newer[0]


@pytest.mark.parametrize("boundary", ["proof-before-job", "job-before-publication"])
def test_reserved_crash_is_inert_and_recovers_same_job(world, monkeypatch, boundary):
    ctx, store, calls, _ = world
    marker = store.enqueue("fictional-a")
    original = jobs._durable_write
    count = [0]

    def interrupted(path, value):
        count[0] += 1
        original(path, value)
        if count[0] == (1 if boundary == "proof-before-job" else 2):
            raise OSError("fictional crash")

    with monkeypatch.context() as patch:
        patch.setattr(jobs, "_durable_write", interrupted)
        assert qb.bridge(ctx)[0]["state"] == "unavailable"
        assert jobs._next(ctx.root) is None
        active = [jobs._read(p) for p in jobs._active(ctx.root)]
        if active:
            with pytest.raises(ValueError, match="not published"):
                jobs.run_job(ctx, active[0])
        assert not calls
    proof = next(ctx.root.glob("operation-*.json"))
    reserved_id = json.loads(proof.read_text())["id"]
    assert qb.bridge(ctx)[0]["state"] == "pending"
    job = jobs._next(ctx.root)
    assert job["id"] == reserved_id and json.loads(proof.read_text())["state"] == "published"
    assert jobs.run_job(ctx, job)["state"] == "done" and len(calls) == 1
    assert json.loads(receipt(ctx, "fictional-a", marker).read_text())["state"] == "acked"


def test_published_pruned_operation_never_reconstructs(world):
    ctx, store, calls, _ = world
    marker, job = queued_job(ctx, store)
    assert jobs.run_job(ctx, job)["state"] == "done"
    (ctx.root / "done" / (job["id"] + ".json")).unlink()
    attempt = json.loads(receipt(ctx, "fictional-a", marker).read_text())["attempts"][0]
    out = jobs.submit(ctx.root, "portal_process", "fictional-a", args=job["args"], operation_id=attempt["operation"], recover_reserved=True)
    assert out["state"] == "unavailable" and not jobs._active(ctx.root) and len(calls) == 1


@pytest.mark.parametrize("fault", ["corrupt-json", "invalid-shape", "missing-created", "bad-id", "bad-state"])
def test_existing_damaged_proof_is_not_overwritten(world, fault):
    ctx, store, _, _ = world
    _, job = queued_job(ctx, store)
    proof = next(ctx.root.glob("operation-*.json"))
    value = json.loads(proof.read_text())
    if fault == "corrupt-json": text = "{broken"
    elif fault == "invalid-shape": text = "[]"
    else:
        if fault == "missing-created": value.pop("created")
        if fault == "bad-id": value["id"] = "../escape"
        if fault == "bad-state": value["state"] = "success-ish"
        text = json.dumps(value)
    proof.write_text(text)
    assert qb.bridge(ctx)[0]["state"] == "unavailable"
    assert proof.read_text() == text and jobs._next(ctx.root) is None


def test_processed_receipt_ack_failure_repairs_without_reread(world, monkeypatch):
    ctx, store, calls, _ = world
    marker, job = queued_job(ctx, store)
    with monkeypatch.context() as patch:
        patch.setattr(qb, "_ack", lambda *a: (_ for _ in ()).throw(OSError("ack unavailable")))
        assert jobs.run_job(ctx, job)["state"] == "failed"
    assert json.loads(receipt(ctx, "fictional-a", marker).read_text())["state"] == "processed"
    assert qb.bridge(ctx)[0]["state"] == "acked"
    assert len(calls) == 1 and not store.queued()


def test_restart_after_queue_unlink_before_ack_marker(world, monkeypatch):
    ctx, store, calls, _ = world
    marker, job = queued_job(ctx, store)
    original = qb._atomic

    def interrupted(path, value, **kwargs):
        if value.get("state") == "acked":
            raise SystemExit("fictional process died after queue unlink")
        return original(path, value, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(qb, "_atomic", interrupted)
        with pytest.raises(SystemExit):
            jobs.run_job(ctx, job)
    assert not store.queued() and jobs.get(ctx.root, job["id"])["state"] == "running"
    jobs.sweep_dead(ctx)
    assert jobs.get(ctx.root, job["id"])["state"] == "done"
    assert json.loads(receipt(ctx, "fictional-a", marker).read_text())["state"] == "acked" and len(calls) == 1


def test_crash_after_pipeline_before_receipt_remains_visible_replayable(world, monkeypatch):
    ctx, store, calls, _ = world
    marker, job = queued_job(ctx, store)
    original = qb._atomic

    def interrupted(path, value, **kwargs):
        if value.get("state") == "processed":
            raise OSError("fictional missing processing proof")
        return original(path, value, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(qb, "_atomic", interrupted)
        assert jobs.run_job(ctx, job)["state"] == "failed"
    assert store.queue_generation("fictional-a") == marker and len(calls) == 1
    assert qb.bridge(ctx)[0]["state"] == "failed"
    saved = json.loads(receipt(ctx, "fictional-a", marker).read_text())
    saved["attempts"][-1]["retry_at"] = 0
    receipt(ctx, "fictional-a", marker).write_text(json.dumps(saved))
    assert qb.bridge(ctx)[0]["attempts"] == 2
    assert jobs.run_job(ctx, jobs._next(ctx.root))["state"] == "done" and len(calls) == 2
    assert json.loads(receipt(ctx, "fictional-a", marker).read_text())["replay_possible"]


def test_failure_is_bounded_and_other_client_progresses(world, monkeypatch):
    ctx, store, calls, pipeline = world
    marker = store.enqueue("fictional-a")
    store.enqueue("fictional-b")

    def fail_one(store, client, **kwargs):
        if client == "fictional-a":
            raise ValueError("Fictional unreadable input")
        return pipeline(store, client, **kwargs)

    monkeypatch.setattr(engine, "process_client", fail_one)
    assert run_once(ctx) == 2
    assert store.queued() == ["fictional-a"] and calls[0][0] == "fictional-b"
    for attempt_number in (2, 3):
        assert qb.bridge(ctx)[0]["state"] == "failed"
        path = receipt(ctx, "fictional-a", marker)
        saved = json.loads(path.read_text()); saved["attempts"][-1]["retry_at"] = 0
        path.write_text(json.dumps(saved))
        assert qb.bridge(ctx)[0]["attempts"] == attempt_number
        assert jobs.run_job(ctx, jobs._next(ctx.root))["state"] == "failed"
    for _ in range(4):
        assert qb.bridge(ctx)[0]["attempts"] == qb.MAX_ATTEMPTS
    assert jobs._next(ctx.root) is None and store.queued() == ["fictional-a"]


@pytest.mark.parametrize("bad", ["empty-completed", "missing-snapshot", "wrong-roots", "infinite-retry"])
def test_damaged_receipts_never_ack_queue(world, bad):
    ctx, store, calls, _ = world
    marker, _ = queued_job(ctx, store)
    path = receipt(ctx, "fictional-a", marker)
    saved = json.loads(path.read_text())
    if bad == "empty-completed": saved.update(state="processed", attempts=[])
    if bad == "missing-snapshot": saved.update(state="processed", outcome={}, processed_at="2026-10-04T00:00:00+00:00")
    if bad == "wrong-roots": saved["identity"]["clients_root"] = "/fictional/wrong"
    if bad == "infinite-retry": saved["attempts"][0]["retry_at"] = float("inf")
    path.write_text(json.dumps(saved))
    assert qb.bridge(ctx)[0]["state"] == "unavailable"
    assert store.queue_generation("fictional-a") == marker and not calls


@pytest.mark.parametrize("bad", ["missing-documents", "incomplete-boundaries", "meta-errors"])
def test_pipeline_incomplete_outputs_do_not_ack(world, monkeypatch, bad):
    ctx, store, _, pipeline = world
    marker, job = queued_job(ctx, store)

    def incomplete(*args, **kwargs):
        out = pipeline(*args, **kwargs)
        folder = ctx.clients / "fictional-a"
        if bad == "missing-documents": (folder / "documents.json").unlink()
        if bad == "incomplete-boundaries": (folder / "documents.json").write_text(json.dumps({"documents": [], "boundary_processing": ["fictional.pdf"]}))
        if bad == "meta-errors": (folder / "meta.json").write_text(json.dumps({"client_id": "fictional-a", "errors": {"__client__": "fictional incomplete"}}))
        return out

    monkeypatch.setattr(engine, "process_client", incomplete)
    assert jobs.run_job(ctx, job)["state"] == "failed"
    assert store.queue_generation("fictional-a") == marker


def test_admin_worker_requires_roots_and_delegates_same_worker(world, monkeypatch):
    from portal import admin
    ctx, store, _, _ = world
    monkeypatch.delenv("PORTAL_DATA", raising=False)
    with pytest.raises(SystemExit): admin.main(["worker", "--once"])
    with pytest.raises(SystemExit): admin.main(["worker", "--once", "--cases", str(ctx.clients), "--jobs", str(ctx.root)])
    seen = []
    monkeypatch.setattr(jobs, "work", lambda *a, **kw: seen.append((a, kw)) or 0)
    assert admin.main(["--root", str(store.root), "worker", "--once", "--no-policies", "--cases", str(ctx.clients), "--jobs", str(ctx.root)]) == 0
    assert seen == [((ctx.clients, store.root), {"once": True, "poll": 5.0, "jobs_root": ctx.root, "use_policies": False})]
    with pytest.raises(ValueError, match="explicit"): engine.run_worker(store, once=True)


def test_real_typed_only_pipeline_creates_case_and_retains_review_holds(world, monkeypatch):
    ctx, store, _, _ = world
    monkeypatch.setattr(engine, "process_client", REAL_PROCESS)
    store.save_answers("fictional-a", {"dob": "2000-01-02", "sex": "Female"})
    marker = store.enqueue("fictional-a")
    assert run_once(ctx) == 1
    saved = json.loads(receipt(ctx, "fictional-a", marker).read_text())
    assert saved["state"] == "acked" and saved["outcome"]["counts"]["review"] > 0
    graph = json.loads((ctx.clients / "fictional-a" / "fact_graph.json").read_text())
    assert graph and (ctx.clients / "fictional-a" / "documents.json").is_file()
    assert store.tasks("fictional-a") == [{"id": "upload:birth_certificate", "kind": "upload", "doc_id": "birth_certificate", "needed": 1}]
    assert not store.queued()


@pytest.mark.parametrize("state", ["ended", "waiting-purge", "damaged", "empty-purge", "unknown-purge", "missing-destroyed-case", "invalid-destroyed-case"])
def test_current_lifecycle_prevents_processing_or_recreation(world, state):
    import engagement
    import purge
    ctx, store, calls, _ = world
    marker, job = queued_job(ctx, store)
    folder = ctx.clients / "fictional-a"
    if state in {"ended", "damaged"}:
        folder.mkdir(parents=True)
        (folder / engagement.FILE).write_text(json.dumps({"end": {"state": "closed"}}) if state == "ended" else "[]")
    else:
        ctx.clients.parent.mkdir(parents=True, exist_ok=True)
        if state in {"missing-destroyed-case", "invalid-destroyed-case"}:
            row = {} if state == "missing-destroyed-case" else {"case": "../escape"}
            (ctx.clients.parent / engagement.DESTROYED_FILE).write_text(json.dumps({"cases": [row]}))
        else:
            value = {} if state == "empty-purge" else {"state": "damaged" if state == "unknown-purge" else "waiting"}
            (ctx.clients.parent / purge.PURGES_FILE).write_text(json.dumps({"cases": {"fictional-a": value}}))
    assert jobs.run_job(ctx, job)["state"] == "failed" and not calls
    assert store.queue_generation("fictional-a") == marker
    assert not (folder / "meta.json").exists()


def test_valid_cancelled_purge_permits_otherwise_open_generation(world):
    import purge
    ctx, store, calls, _ = world
    ctx.clients.parent.mkdir(parents=True, exist_ok=True)
    (ctx.clients.parent / purge.PURGES_FILE).write_text(json.dumps({"cases": {"fictional-a": {"state": "cancelled"}}}))
    store.enqueue("fictional-a")
    assert run_once(ctx) == 1 and calls and not store.queued()



def test_unrelated_valid_case_folder_identity_does_not_block_portal(world):
    import engagement
    ctx, store, calls, _ = world
    ctx.clients.parent.mkdir(parents=True, exist_ok=True)
    (ctx.clients.parent / engagement.DESTROYED_FILE).write_text(json.dumps({"cases": [{"case": "Former Client"}]}))
    store.enqueue("fictional-a")
    assert run_once(ctx) == 1 and calls and not store.queued()


def test_legacy_upload_consumes_canonical_job_with_actual_handled_count(world, monkeypatch):
    ctx, store, calls, pipeline = world
    records = [{"id": "photo-a", "sha256": "a" * 64, "status": "received"},
               {"id": "photo-b", "sha256": "b" * 64, "status": "received"}]
    store.update_uploads("fictional-a", records)
    legacy = jobs.submit(ctx.root, "portal_upload", "fictional-a")
    old = store.enqueue("fictional-a")
    qb.bridge(ctx)
    newer = []
    def handled(*args, **kwargs):
        rows = store.uploads("fictional-a")
        rows[0]["status"] = "checked"
        rows[1].update(status="checked", sha256="c" * 64)  # a replacement is not the old target
        store.update_uploads("fictional-a", rows)
        newer.append(store.enqueue("fictional-a"))
        return pipeline(*args, **kwargs)
    monkeypatch.setattr(engine, "process_client", handled)
    monkeypatch.setattr(engine, "read_new_uploads", lambda *a, **k: pytest.fail("duplicate incremental path"))
    out = jobs.run_job(ctx, legacy)
    assert out["state"] == "done" and out["result"]["read"] == 1 and len(calls) == 1
    canonical = jobs.get(ctx.root, out["result"]["canonical_job"])
    assert canonical["state"] == "done" and canonical["result"]["processed"]
    assert json.loads(receipt(ctx, "fictional-a", old).read_text())["acknowledgment"] == "newer_generation_retained"
    assert store.queue_generation("fictional-a") == newer[0]


def test_legacy_handoff_failure_does_not_immediately_run_canonical_again(world, monkeypatch):
    ctx, store, calls, _ = world
    store.update_uploads("fictional-a", [{"id": "photo-a", "sha256": "a" * 64, "status": "received"}])
    legacy = jobs.submit(ctx.root, "portal_upload", "fictional-a")
    marker = store.enqueue("fictional-a")
    def failed(*args, **kwargs):
        calls.append("failed")
        raise ValueError("Fictional processing interruption")
    monkeypatch.setattr(engine, "process_client", failed)
    monkeypatch.setattr(engine, "read_new_uploads", lambda *a, **k: pytest.fail("duplicate incremental path"))
    assert run_once(ctx) == 1 and calls == ["failed"]
    assert jobs.get(ctx.root, legacy["id"])["state"] == "failed"
    [canonical] = jobs.jobs(ctx.root, client="fictional-a", kind="portal_process", recent=86400)
    assert canonical["state"] == "failed" and jobs._next(ctx.root) is None
    assert qb.bridge(ctx)[0]["state"] == "failed"
    assert store.queue_generation("fictional-a") == marker


def test_generation_change_before_canonical_start_reports_no_photos_read(world, monkeypatch):
    ctx, store, calls, _ = world
    store.update_uploads("fictional-a", [{"id": "photo-a", "sha256": "a" * 64, "status": "received"}])
    legacy = jobs.submit(ctx.root, "portal_upload", "fictional-a")
    old = store.enqueue("fictional-a")
    qb.bridge(ctx)
    original = jobs.run_job
    newer = []
    def changed(context, job):
        if job["kind"] == "portal_process":
            newer.append(store.enqueue("fictional-a"))
        return original(context, job)
    monkeypatch.setattr(jobs, "run_job", changed)
    monkeypatch.setattr(engine, "read_new_uploads", lambda *a, **k: pytest.fail("duplicate incremental path"))
    assert original(ctx, legacy)["state"] == "failed" and not calls
    [canonical] = jobs.jobs(ctx.root, client="fictional-a", kind="portal_process", recent=86400)
    assert canonical["state"] == "done" and canonical["result"] == {"processed": False, "superseded": True}
    assert json.loads(receipt(ctx, "fictional-a", old).read_text())["state"] == "superseded"
    assert store.queue_generation("fictional-a") == newer[0]

def test_superseded_generation_does_not_read_old_documents(world, monkeypatch):
    from classify import classifier
    from test_staff_upload_recovery import pdf
    ctx, store, calls, _ = world
    store.add_upload("fictional-a", "passport", "fictional.pdf", pdf(), "application/pdf")
    marker, job = queued_job(ctx, store)
    store.enqueue("fictional-a")
    monkeypatch.setattr(classifier, "_extract_pdf_pages", lambda *_: pytest.fail("Superseded answers must not trigger OCR"))
    finished = jobs.run_job(ctx, job)
    assert finished["state"] == "done" and finished["result"]["superseded"]
    assert not calls
