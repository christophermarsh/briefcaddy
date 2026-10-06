"""New portal durable-write residue: exact scope, export/backup, actual Q1 wait."""
from file_policy_fixture import own_case_identity, disposition
import hashlib
import os
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import backups
import client_file
import clock
import engagement
import export_firm
import jobs
import oslock
import purge
import records
from portal import queue_bridge as qb
from portal.store import PortalStore
from test_purge import firm as purge_firm  # noqa: F401 -- pytest fixture registration and helper reexports
from test_restricted import doc, make_case


def stage_name(kind, case, nonce="a" * 16):
    return "portal-" + kind + "-" + hashlib.sha256(case.encode()).hexdigest() + "-" + nonce + ".tmp"


def interrupt_replace(monkeypatch, action):
    with monkeypatch.context() as patch:
        patch.setattr(qb.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("fictional pre-replace interruption")))
        with pytest.raises(OSError):
            action()


def test_new_receipt_and_residue_catalog_classification():
    row = records.record_of("portal", "processing-receipts/" + "a" * 32 + ".json")
    assert row["id"] == "portal_processing_receipts" and row["exported"] is False
    assert records.coverage("portal", "processing-receipts/" + "a" * 32 + ".json") == "never"
    assert records.coverage("portal", "processing-receipts/" + "a" * 32 + ".json.1234567890abcdef.tmp") == "never"
    assert any(r["id"] == "portal_processing_partial" for r in records.WORKING_FILES)
    for path in ["jobs/" + stage_name("job", "fictional-a"),
                 "jobs/done/" + stage_name("job", "fictional-a"),
                 "portal/queue/" + stage_name("queue", "fictional-a")]:
        assert records.coverage("firm", path) == "never" and records.not_backed_up(path)
    assert records.not_backed_up("portal/clients/fictional-a/processing-receipts/a.json.1234567890abcdef.tmp")


@pytest.mark.parametrize("kind", ["job", "queue"])
def test_exact_staging_scope_preserves_other_and_legacy_files(tmp_path, kind):
    root = tmp_path / kind; root.mkdir()
    own = root / stage_name(kind, "fictional-a"); own.write_bytes(b"fictional partial JSON")
    other = root / stage_name(kind, "fictional-b"); other.write_bytes(b"other fictional residue")
    malformed = root / stage_name(kind, "fictional-a", "wrong"); malformed.write_bytes(b"not our exact staging contract")
    legacy = root / "old.json.123.456.tmp"; legacy.write_bytes(b"legacy untouched")
    assert purge._portal_staging(root, {"fictional-a"}, kind) == 1
    assert not own.exists() and other.exists() and malformed.exists() and legacy.exists()


@pytest.mark.parametrize("where", ["root", "done", "file"])
def test_staging_cleanup_refuses_symlink_traversal(tmp_path, where):
    outside = tmp_path / "outside"; outside.mkdir()
    target = outside / stage_name("job", "fictional-a"); target.write_bytes(b"outside canary")
    root = tmp_path / "jobs"
    if where == "root": link = root; destination = outside; directory = True
    else:
        root.mkdir()
        link = root / ("done" if where == "done" else target.name)
        destination = outside if where == "done" else target
        directory = where == "done"
    try: os.symlink(destination, link, target_is_directory=directory)
    except (OSError, NotImplementedError): pytest.skip("platform cannot create the isolated symlink fixture")
    with pytest.raises(ValueError, match="link or reparse"):
        purge._portal_staging(root, {"fictional-a"}, "job")
    assert target.read_bytes() == b"outside canary" and link.is_symlink()


def test_actual_q1_removes_new_staging_and_receipts_with_other_client_retained(purge_firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    firm = purge_firm
    case = "fictional-queue-purge"; keep_id = "fictional-queue-keep"
    folder = make_case(firm.clients, case, "Fictional Queue Client", [doc("fictional-passport", "passport", text="Fictional passport")])
    keep = make_case(firm.clients, keep_id, "Fictional Queue Keeper", [doc("fictional-keep", "passport", text="Fictional keeper")])
    store = PortalStore(firm.portal)
    store.add_client(case, "Fictional Queue Client", consent={})
    store.add_client(keep_id, "Fictional Queue Keeper", consent={})
    ctx = jobs.Context(firm.clients, firm.portal, jobs_root=firm.data / "jobs", use_policies=False)
    _marker = store.enqueue(case)
    assert qb.bridge(ctx)[0]["state"] == "pending"
    job = jobs._next(ctx.root)
    assert job["client"] == case
    # Genuine pre-replace faults for pending proof, active progress and completion.
    args = job["args"] | {"attempt": 9}
    interrupt_replace(monkeypatch, lambda: jobs.submit(ctx.root, "portal_process", case, args=args, operation_id="f" * 64, recover_reserved=True))
    interrupt_replace(monkeypatch, lambda: jobs._write(ctx.root / (job["id"] + ".json"), job | {"state": "running"}))
    interrupt_replace(monkeypatch, lambda: jobs._write(ctx.root / "done" / (job["id"] + ".json"), job | {"state": "done"}))
    interrupt_replace(monkeypatch, lambda: store.enqueue(case))
    receipt_dir = store.client_dir(case) / "processing-receipts"
    interrupt_replace(monkeypatch, lambda: qb._atomic(receipt_dir / "unfinished.json", {"client": case, "canary": "fictional-receipt-partial"}))
    parts = list(ctx.root.glob("portal-job-*.tmp")) + list((ctx.root / "done").glob("portal-job-*.tmp"))
    queue_parts = list((firm.portal / "queue").glob("portal-queue-*.tmp"))
    assert len(parts) == 3 and len(queue_parts) == 1 and list(receipt_dir.glob("*.tmp"))
    other = ctx.root / stage_name("job", keep_id); other.write_bytes(b"fictional other residue")
    other_queue = firm.portal / "queue" / stage_name("queue", keep_id); other_queue.write_bytes(b"fictional other queue")
    legacy = ctx.root / "unrelated-default.json.123.456.tmp"; legacy.write_bytes(b"legacy untouched")
    selected, _, _ = client_file.gather(folder, firm.portal)
    assert not any(e.source and ("processing-receipts" in e.source.parts or e.source.suffix == ".tmp") for e in selected)
    exported = export_firm.everything(export_firm.default_where(firm.clients, firm.portal, firm.data / "review_users.json"), who="Fictional Attorney", role="attorney")
    with zipfile.ZipFile(exported["path"]) as archive:
        assert not any("processing-receipts" in name or "portal-job-" in name or "portal-queue-" in name for name in archive.namelist())
    before = backups.make_backup(firm.tmp / "before-backup", firm.data, clients=firm.docs)
    with zipfile.ZipFile(before["archive"]) as archive:
        assert any("processing-receipts" in name and name.endswith(".json") for name in archive.namelist())
        assert not any(name.endswith(".tmp") for name in archive.namelist())
    engagement.end(folder, "closed", "Fictional Attorney", "attorney", reason="Fictional close", portal_root=firm.portal)
    purge.record_contact(folder, "phone", "10/05/2026", "Fictional contact record", "Fictional Attorney", "attorney")
    purge.review_originals(folder, [], True, "Fictional Attorney", "attorney")
    own_case_identity(folder)
    monkeypatch.setattr(clock, "_now_override", datetime(2032, 10, 6, 10, 30))
    disposition(folder, who="Fictional Attorney", portal_root=firm.portal,
                completed_on="2026-10-05", age_status="adult", keep_until="2032-10-05")
    requested = purge.ask(folder, "Fictional early purge acceptance", "Fictional Attorney", "attorney", attorneys=1)
    with pytest.raises(purge.PurgeError): purge.run(firm.clients, case, firm.portal)
    monkeypatch.setattr(clock, "_now_override", datetime.fromisoformat(requested["purge_on"]) + timedelta(hours=12))
    result = purge.run(firm.clients, case, firm.portal)
    assert result["left"] == 0
    assert not folder.exists() and keep.exists() and not store.client_dir(case).exists()
    assert all(not path.exists() for path in parts + queue_parts)
    assert not list(ctx.root.glob("operation-*.json")) and not jobs.get(ctx.root, job["id"])
    assert other.exists() and other_queue.exists() and legacy.exists() and store.client_dir(keep_id).exists()
    after = backups.make_backup(firm.tmp / "after-backup", firm.data, clients=firm.docs)
    with zipfile.ZipFile(after["archive"]) as archive:
        assert not any(case in name for name in archive.namelist())
        assert not any(b"fictional-receipt-partial" in archive.read(name) for name in archive.namelist())
    # Q1 does not rewrite an already retained backup/export.
    assert Path(before["archive"]).exists() and Path(exported["path"]).exists()


def test_q1_waits_for_validated_producer_then_prevents_resurrection(purge_firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    firm = purge_firm
    case = "fictional-queue-race"
    folder = make_case(firm.clients, case, "Fictional Queue Race", [doc("fictional-race", "passport", text="Fictional passport")])
    store = PortalStore(firm.portal)
    store.add_client(case, "Fictional Queue Race", consent={})
    ctx = jobs.Context(firm.clients, firm.portal, jobs_root=firm.data / "jobs", use_policies=False)
    marker = store.enqueue(case)
    engagement.end(folder, "closed", "Fictional Attorney", "attorney", reason="Fictional close", portal_root=firm.portal)
    purge.record_contact(folder, "phone", "10/05/2026", "Fictional contact record", "Fictional Attorney", "attorney")
    purge.review_originals(folder, [], True, "Fictional Attorney", "attorney")
    own_case_identity(folder)
    monkeypatch.setattr(clock, "_now_override", datetime(2032, 10, 6, 10, 30))
    disposition(folder, who="Fictional Attorney", portal_root=firm.portal,
                completed_on="2026-10-05", age_status="adult", keep_until="2032-10-05")
    requested = purge.ask(folder, "Fictional queue race acceptance", "Fictional Attorney", "attorney", attorneys=1)
    monkeypatch.setattr(clock, "_now_override", datetime.fromisoformat(requested["purge_on"]) + timedelta(hours=12))

    validated = threading.Event(); release = threading.Event(); queue_wait = threading.Event()
    outcomes = {}; errors = []
    real_paths = qb._paths
    real_try_lock = oslock.try_lock

    def paused_paths(*args):
        result = real_paths(*args)
        if threading.current_thread().name == "fictional-queue-producer":
            validated.set()
            if not release.wait(8):
                raise TimeoutError("fictional producer was not released")
        return result

    def observed_try_lock(fd):
        acquired = real_try_lock(fd)
        # The paused producer holds only queue/.lock. The purger's case lock
        # and ledger append have completed before it reaches that same lock.
        if not acquired and threading.current_thread().name == "fictional-queue-purger":
            queue_wait.set()
        return acquired

    monkeypatch.setattr(qb, "_paths", paused_paths)
    monkeypatch.setattr(oslock, "try_lock", observed_try_lock)

    def produce():
        try: outcomes["bridge"] = qb.bridge(ctx)
        except BaseException as exc: errors.append(exc)

    def destroy():
        try:
            from portal.communication_consent import data_gate
            with data_gate(firm.data), jobs.case_lock(ctx.root, case, timeout=8):
                outcomes["purge"] = purge.run(firm.clients, case, firm.portal)
        except BaseException as exc: errors.append(exc)

    producer = threading.Thread(target=produce, name="fictional-queue-producer")
    purger = threading.Thread(target=destroy, name="fictional-queue-purger")
    producer.start()
    try:
        assert validated.wait(5), "producer must validate the profile while holding the queue lock"
        purger.start()
        assert queue_wait.wait(5), "purger must actually wait on the producer's OS queue lock"
        assert (store.client_dir(case) / "profile.json").is_file()
        assert "purge" not in outcomes
    finally:
        release.set()
        producer.join(8)
        if purger.ident is not None: purger.join(8)
    assert not producer.is_alive() and not purger.is_alive() and not errors
    assert outcomes["bridge"] == [{"client": case, "state": "pending", "attempts": 1}]
    assert outcomes["purge"]["left"] == 0
    assert not folder.exists() and not store.client_dir(case).exists()
    assert not (firm.portal / "queue" / case).exists()
    assert not list(ctx.root.glob("operation-*.json"))
    assert not list(ctx.root.glob("*-portal_process-*.json"))
    assert not list((ctx.root / "done").glob("*-portal_process-*.json"))
    assert not list(ctx.root.glob("portal-job-*.tmp"))
    assert not list((firm.portal / "queue").glob("portal-queue-*.tmp"))
    # Both producers validate inside the same lock after deletion; neither
    # may recreate a queue marker, receipt, proof or processing job.
    with pytest.raises(LookupError, match="unknown client"):
        store.enqueue(case)
    with qb._lock(store), pytest.raises(LookupError, match="unknown client"):
        qb._attempt(ctx, store, case, marker)
    assert not store.client_dir(case).exists()
    assert not (firm.portal / "queue" / case).exists()
    assert not list(ctx.root.glob("operation-*.json"))
    assert not list(ctx.root.glob("*-portal_process-*.json"))
