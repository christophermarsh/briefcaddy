"""Fictional upload fault probes; no network or production stores."""
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter

import events
import jobs
import records
from communication_fixture import installation, approve_client, accepted_link, review_request
from portal.app import create_app
from portal.questions import request_fields
from portal.store import PortalStore, image_to_pdf
from portal import upload_recovery as recovery

ATTEMPT = "a" * 32
def pdf_bytes(empty=False, encrypted=False):
    writer = PdfWriter()
    if not empty:
        writer.add_blank_page(width=72, height=72)
    if encrypted:
        writer.encrypt("fictional-password")
    buffer = io.BytesIO(); writer.write(buffer)
    return buffer.getvalue()


PDF = pdf_bytes()


@pytest.fixture
def store(tmp_path, monkeypatch):
    data = installation(tmp_path, monkeypatch)
    monkeypatch.setenv("PORTAL_READ_AT_ONCE", "0")  # these probes recover uploads, not document processing
    s = PortalStore(data / "portal")
    for client, name in (("fictional-a", "Fictional Alpha"), ("fictional-b", "Fictional Beta")):
        (data / "clients" / client).mkdir()
        s.add_client(client, name, email=client + "@example.test", language="en")
        approve_client(s, client)  # explicit actual fictional signoff; no imported consent or raw auth token
    return s


def accept(store, token=ATTEMPT, data=PDF, kind="application/pdf"):
    return recovery.accept(store, "fictional-a", token, "passport", "fictional.pdf", data, kind, lambda client, operation: "tonight")


def audit_counts(store):
    log = [json.loads(l) for l in (store.client_dir("fictional-a") / "events.jsonl").read_text().splitlines()]
    ledger = list(events.rows(events.base_path(store.root.parent), case="fictional-a"))
    return sum(r["event"] == "upload" for r in log), sum(r["action"] == "uploaded" for r in ledger)


def test_lost_response_concurrent_retry_is_one_upload(store):
    accept(store)  # browser loses this response
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: accept(store), range(4)))
    assert all(r["status"] == "complete" for r in results)
    assert len(store.uploads("fictional-a")) == 1
    assert len(list((store.client_dir("fictional-a") / "uploads").glob("*.pdf"))) == 1
    assert audit_counts(store) == (1, 1)
    assert store.uploads("fictional-a")[0]["source_sha256"] == hashlib.sha256(PDF).hexdigest()
    with pytest.raises(ValueError, match="upload_attempt_conflict"):
        accept(store, data=PDF + b"different")
    accept(store, token="b" * 32)  # intentional second upload, same content
    assert len(store.uploads("fictional-a")) == 2


@pytest.mark.parametrize("failure", ["uploads.json", "audit", "queue"])
def test_repair_after_file_commit(store, monkeypatch, failure):
    real_write, real_record, real_enqueue = store._write, events.record, store.enqueue
    def write(path, data):
        if path.name == failure:
            raise OSError("fictional interruption")
        return real_write(path, data)
    monkeypatch.setattr(store, "_write", write)
    if failure == "audit":
        monkeypatch.setattr(events, "record", lambda *a, **k: None)
    if failure == "queue":
        monkeypatch.setattr(store, "enqueue", lambda *a: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        accept(store)
    monkeypatch.setattr(store, "_write", real_write)
    monkeypatch.setattr(events, "record", real_record)
    monkeypatch.setattr(store, "enqueue", real_enqueue)
    assert recovery.outcome(store, "fictional-a", ATTEMPT, lambda *a: "tonight")["status"] == "complete"
    assert len(store.uploads("fictional-a")) == 1
    assert audit_counts(store) == (1, 1)


def test_clock_separated_photo_retry_repairs_part_file(store, monkeypatch):
    img = io.BytesIO(); Image.new("RGB", (8, 8), "white").save(img, format="PNG"); photo = img.getvalue()
    real_replace = recovery.os.replace
    def replace(source, target):
        if str(target).endswith(".pdf"):
            raise OSError("crash before file replace")
        return real_replace(source, target)
    monkeypatch.setattr(recovery.os, "replace", replace)
    with pytest.raises(OSError):
        accept(store, data=photo, kind="image/png")
    assert list((store.client_dir("fictional-a") / "uploads").glob("*.part"))
    assert recovery.outcome(store, "fictional-a", ATTEMPT, lambda *a: "tonight") == {"status": "pending", "received": False}
    before = image_to_pdf(photo)
    import time
    real_gmtime = time.gmtime
    monkeypatch.setattr(time, "gmtime", lambda seconds=None: real_gmtime(0 if seconds == 0 else 2000000000))
    assert image_to_pdf(photo) == before
    monkeypatch.setattr(recovery.os, "replace", real_replace)
    assert accept(store, data=photo, kind="image/png")["status"] == "complete"
    assert not list((store.client_dir("fictional-a") / "uploads").glob("*.part"))
    assert audit_counts(store) == (1, 1)


def test_auth_scoping_and_outcome_after_commit(store):
    app = create_app(store.root, secure_cookies=False)
    with TestClient(app) as browser:
        assert browser.get("/api/upload-outcome", params={"attempt": ATTEMPT}).status_code == 401
        browser.get("/l/" + accepted_link(store, "fictional-a"))
        uploaded = browser.post("/api/upload", headers={"X-Portal": "1"}, data={"doc_id": "passport", "attempt": ATTEMPT}, files={"file": ("fictional.pdf", PDF, "application/pdf")})
        assert uploaded.status_code == 200, uploaded.text
        assert browser.get("/api/upload-outcome", params={"attempt": ATTEMPT}).json()["upload_outcome"]["status"] == "complete"
        browser.get("/l/" + accepted_link(store, "fictional-b"))
        result = browser.get("/api/upload-outcome", params={"attempt": ATTEMPT}).json()["upload_outcome"]
        assert result == {"status": "unknown", "received": False}
        assert store.uploads("fictional-b") == []


@pytest.mark.parametrize("data,kind,status", [
    (b"%PDF-truncated-fictional", "application/pdf", 415),
    (pdf_bytes(empty=True), "application/pdf", 415),
    (pdf_bytes(encrypted=True), "application/pdf", 415),
    (b"\x00\x00\x00\x18ftypheicfictional", "image/heic", 415),
    (b"RIFFfictionalWEBP", "image/webp", 415),
    (b"\xff\xd8\xfffictional-truncated", "image/jpeg", 415),
    (b"\x89PNG\r\n\x1a\nfictional-truncated", "image/png", 415),
    (PDF + b" " * (15 * 1024 * 1024), "application/pdf", 413),
], ids=["malformed-pdf", "empty-pdf", "encrypted-pdf", "heic", "webp", "truncated-jpeg", "truncated-png", "oversize-pdf"])
def test_invalid_upload_does_not_leave_receipt_or_block_reselection(store, data, kind, status):
    app = create_app(store.root, secure_cookies=False)
    with TestClient(app) as browser:
        browser.get("/l/" + accepted_link(store, "fictional-a"))
        refused = browser.post("/api/upload", headers={"X-Portal": "1"}, data={"doc_id": "passport", "attempt": ATTEMPT}, files={"file": ("fictional", data, kind)})
        assert refused.status_code == status, refused.text
        assert not recovery._path(store, "fictional-a", ATTEMPT).exists()
        assert store.uploads("fictional-a") == []
        valid = browser.post("/api/upload", headers={"X-Portal": "1"}, data={"doc_id": "passport", "attempt": "c" * 32}, files={"file": ("fictional.pdf", PDF, "application/pdf")})
        assert valid.status_code == 200, valid.text


@pytest.mark.parametrize("format,kind", [("JPEG", "image/jpeg"), ("PNG", "image/png")])
def test_valid_supported_photos(store, format, kind):
    buffer = io.BytesIO(); Image.new("RGB", (8, 8), "white").save(buffer, format=format)
    assert accept(store, data=buffer.getvalue(), kind=kind)["received"]


def test_unknown_answer_is_not_acknowledged(store):
    app = create_app(store.root, secure_cookies=False)
    with TestClient(app) as browser:
        browser.get("/l/" + accepted_link(store, "fictional-a"))
        result = browser.put("/api/answers", headers={"X-Portal": "1"}, json={"removed_question": "fictional pending", "dob": "2000-01-01"}).json()
        assert result["accepted"] == ["dob"]
        assert "removed_question" not in store.answers("fictional-a")


def test_request_and_retakes_reconcile_without_reanswering(store, monkeypatch):
    request = store.add_request("fictional-a", "Fictional passport request", "passport", "Fictional Staff",
                                typed=request_fields({"text": "Fictional passport request"}, "en"))
    review_request(store, "fictional-a", request)
    store.save_tasks("fictional-a", [{"id": "retake:passport", "kind": "retake", "doc_id": "passport", "text": "Fictional retake"}])
    real_record = events.record
    def record(*args, **kwargs):
        if args[1] == "answered":
            return None
        return real_record(*args, **kwargs)
    monkeypatch.setattr(events, "record", record)
    with pytest.raises(OSError):
        accept(store)
    answered_at = store.requests("fictional-a")[0]["answered_at"]
    monkeypatch.setattr(events, "record", real_record)
    assert accept(store)["status"] == "complete"
    assert store.requests("fictional-a")[0]["answered_at"] == answered_at
    assert store.tasks("fictional-a")[0]["received_at"]
    log = [json.loads(l) for l in (store.client_dir("fictional-a") / "events.jsonl").read_text().splitlines()]
    assert sum(r["event"] == "request_answered" for r in log) == 1
    assert sum(r["action"] == "answered" for r in events.rows(events.base_path(store.root.parent), case="fictional-a")) == 1


def test_rebuilt_target_same_id_is_not_answered_by_old_attempt(store, monkeypatch):
    request = store.add_request("fictional-a", "Original fictional request", "passport", "Fictional Staff",
                                typed=request_fields({"text": "Original fictional request"}, "en"))
    review_request(store, "fictional-a", request)
    store.save_tasks("fictional-a", [{"id": "retake:passport", "kind": "retake", "doc_id": "passport", "text": "Original fictional retake"}])
    real_write = store._write
    def write(path, value):
        if path.name == "uploads.json":
            raise OSError("interrupt before follow-up")
        real_write(path, value)
    monkeypatch.setattr(store, "_write", write)
    with pytest.raises(OSError):
        accept(store)
    monkeypatch.setattr(store, "_write", real_write)
    requests = store.requests("fictional-a")
    requests[0].update(text="Replacement fictional request", text_en="Replacement fictional request",
                       **request_fields({"text": "Replacement fictional request"}, "en"))
    store._write(store.client_dir("fictional-a") / "requests.json", requests)
    review_request(store, "fictional-a", requests[0])  # current replacement wording, never inherit its prior review
    tasks = store.tasks("fictional-a"); tasks[0]["text"] = "Replacement fictional retake"; store.save_tasks("fictional-a", tasks)
    assert accept(store)["status"] == "complete"
    assert store.requests("fictional-a")[0]["status"] == "open"
    assert not store.tasks("fictional-a")[0].get("received_at")


def test_ended_case_receipt_before_file_is_not_received(store, monkeypatch):
    real_replace = recovery.os.replace
    def replace(source, target):
        if str(target).endswith(".pdf"):
            raise OSError("interrupted before commit")
        real_replace(source, target)
    monkeypatch.setattr(recovery.os, "replace", replace)
    with pytest.raises(OSError):
        accept(store)
    monkeypatch.setattr(recovery.os, "replace", real_replace)
    receipt = recovery._path(store, "fictional-a", ATTEMPT); before = receipt.read_bytes()
    app = create_app(store.root, secure_cookies=False)
    with TestClient(app) as browser:
        browser.get("/l/" + accepted_link(store, "fictional-a"))
        assert browser.get("/api/me").status_code == 200  # current accepted preclosure session
        # Closure authority lives in the canonical case, separately from its portal projection.
        case = store.communication_scope().cases / "fictional-a"
        (case / "engagement.json").write_text(json.dumps({"end": {"state": "withdrawn", "on": "2026-10-04"}}), encoding="utf-8")
        store.save_engagement("fictional-a", {"ended": {"at": "2026-10-04"}})
        assert browser.get("/api/upload-outcome", params={"attempt": ATTEMPT}).status_code == 401
    # Internal retained outcome is readable without granting a closed client normal HTTP access.
    result = recovery.outcome(store, "fictional-a", ATTEMPT, lambda *a: "tonight", repair=False)
    assert result["received"] is False and result["status"] == "pending"
    assert receipt.read_bytes() == before


def test_job_operation_retries_and_pruned_outcome(tmp_path):
    root = tmp_path / "jobs"
    def submit():
        return jobs.submit(root, "portal_upload", "fictional-a", args={"upload_operation": ATTEMPT}, operation_id=ATTEMPT)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: submit(), range(4)))
    assert len({r["id"] for r in results}) == 1
    job = results[0]; job["state"] = "failed"
    jobs._write(root / "done" / (job["id"] + ".json"), job)
    (root / (job["id"] + ".json")).unlink()
    assert submit()["state"] == "failed"  # reuse is not invented reading success
    job["state"] = "done"; jobs._write(root / "done" / (job["id"] + ".json"), job)
    assert submit()["state"] == "done"
    (root / "done" / (job["id"] + ".json")).unlink()  # 14-day cleanup
    assert submit()["state"] == "unavailable"
    assert jobs.jobs(root) == []
    with pytest.raises(ValueError, match="operation payload changed"):
        jobs.submit(root, "portal_upload", "fictional-a", args={"different": True}, operation_id=ATTEMPT)


def test_receipt_and_crash_residue_catalog(store):
    accept(store)
    assert records.record_of("portal", "upload-receipts/" + recovery.key(ATTEMPT) + ".json")["exported"]
    assert records.coverage("portal", "upload.lock") == "never"
    assert records.coverage("portal", "uploads/fictional.part") == "never"
    assert records.coverage("firm", "jobs/operation-" + "a" * 64 + ".json") == "never"
