"""HTTP receipt remains available while slow OCR runs outside writer gates."""
# ruff: noqa: F811 -- shared pytest fixtures
import base64
import json
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest

import jobs
from classify import classifier
from upload_workflow_fixtures import source_firm, world, app, server, sign_in  # noqa: F401
from assignment_route_fixtures import call
from test_staff_upload_recovery import pdf


def test_http_receives_another_file_during_staff_ocr(server, world, monkeypatch):
    monkeypatch.setattr(jobs, "ensure_worker", lambda *_: False)
    cookie = sign_in(server, "jane@firm.example")
    cid, scope = world["client"], world["scope"]
    def upload(attempt):
        return call(server + "/api/client-upload", cookie, {"client": cid, "name": "fictional.pdf", "data": base64.b64encode(pdf()).decode(), "attempt": attempt})
    status, body = upload("a" * 32)
    assert status == 200, body
    job = jobs.get(scope.queue, json.loads(body)["job"]["id"])
    entered, release = threading.Event(), threading.Event()
    def slow(data):
        entered.set()
        assert release.wait(10)
        return ["Fictional text"]
    monkeypatch.setattr(classifier, "_extract_pdf_pages", slow)
    def handler(ctx, job, progress):
        from review.staff_upload_recovery import validate_for_read
        validate_for_read(ctx.clients, ctx.store(), cid, job["args"], job["by"])
        return {"processed": True}
    monkeypatch.setitem(jobs.HANDLERS, "staff_upload", handler)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(jobs.run_job, jobs.Context(scope.cases, scope.portal, jobs_root=scope.queue), job)
        try:
            assert entered.wait(5)
            assert jobs.get(scope.queue, job["id"])["state"] == "running"
            status, body = upload("b" * 32)
            assert status == 200, body
            second = json.loads(body)
            assert second["received"] and second["job"]["state"] == "queued"
            status, body = upload("b" * 32)
            assert status == 200 and json.loads(body)["job"]["id"] == second["job"]["id"]
            assert call(server + "/api/staff-upload-outcome?client=" + cid + "&attempt=" + "b" * 32, cookie)[0] == 200
            assert len(world["store"].uploads(cid)) == 2
        finally:
            release.set()
        assert task.result(timeout=10)["state"] == "done"


def test_boundary_ocr_does_not_block_http_uploads(server, world, app, monkeypatch):
    import document_instances
    import documents
    monkeypatch.setattr(jobs, "ensure_worker", lambda *_: False)
    cookie = sign_in(server, "jane@firm.example")
    cid, scope = world["client"], world["scope"]
    body = {"client": cid, "name": "fictional.pdf", "data": base64.b64encode(pdf()).decode(), "attempt": "a" * 32}
    status, result = call(server + "/api/client-upload", cookie, body)
    assert status == 200, result
    row = world["store"].uploads(cid)[0]
    folder = world["store"].client_dir(cid) / "uploads"
    case = scope.cases / cid
    from factgraph import FactGraph
    FactGraph(cid).save(case / "fact_graph.json")
    (case / "meta.json").write_text(json.dumps({"source_folder": str(folder)}))
    plans = document_instances.prepare([(row["stored"], "Fictional text")], {row["stored"]: ["Fictional text"]}, document_instances.context(folder, None, [row["stored"]]))[2]
    documents.save(case, {"boundary_plans": plans})
    entered, release = threading.Event(), threading.Event()
    def slow(data):
        entered.set()
        assert release.wait(10)
        return ["Fictional text"]
    monkeypatch.setattr(classifier, "_extract_pdf_pages", slow)
    def finish(client, body, role):
        assert classifier.extract_pages(folder / row["stored"]) == ["Fictional text"]
        return {"ok": True}
    monkeypatch.setattr(app, "document_change", finish)
    review = {"client": cid, "id": row["stored"], "field": "boundaries", "value": "1", "fingerprint": plans[row["stored"]]["fingerprint"]}
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(call, server + "/api/document", cookie, review)
        try:
            assert entered.wait(5)
            status, result = call(server + "/api/client-upload", cookie, body | {"attempt": "b" * 32})
            assert status == 200, result
            assert json.loads(result)["received"]
        finally:
            release.set()
        status, result = task.result(timeout=10)
        assert status == 200, result

@pytest.mark.parametrize("kind", ["portal_process", "portal_upload"])
def test_phone_upload_ocr_leaves_staff_http_receipt_available(server, world, monkeypatch, kind):
    from portal import queue_bridge
    import io
    import oslock
    from pypdf import PdfWriter
    monkeypatch.setattr(jobs, "ensure_worker", lambda *_: False)
    cookie = sign_in(server, "jane@firm.example")
    cid, scope = world["client"], world["scope"]
    store = world["store"]
    store.add_upload(cid, "passport", "fictional-phone.pdf", pdf(), "application/pdf")
    ctx = jobs.Context(scope.cases, scope.portal, jobs_root=scope.queue, use_policies=False)
    if kind == "portal_process":
        store.enqueue(cid)
        queue_bridge.bridge(ctx)
        job = jobs._next(scope.queue)
    else:
        job = jobs.submit(scope.queue, "portal_upload", cid)
    entered, release = threading.Event(), threading.Event()
    reads = []
    def slow(data):
        assert not oslock.held_by_someone(scope.data / "communication.lock")
        reads.append(data)
        if len(reads) == 1:
            entered.set()
            assert release.wait(10)
        return ["Fictional phone text"]
    monkeypatch.setattr(classifier, "_extract_pdf_pages", slow)
    def handler(ctx, job, progress):
        with classifier.page_cache():
            for row in store.uploads(cid):
                assert classifier.extract_pages(store.client_dir(cid) / "uploads" / row["stored"]) == ["Fictional phone text"]
        return {"processed": True}
    monkeypatch.setitem(jobs.HANDLERS, kind, handler)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(jobs.run_job, ctx, job)
        try:
            assert entered.wait(5)
            writer, out = PdfWriter(), io.BytesIO()
            writer.add_blank_page(width=100, height=100)
            writer.write(out)
            status, body = call(server + "/api/client-upload", cookie, {"client": cid, "name": "fictional-staff.pdf", "data": base64.b64encode(out.getvalue()).decode(), "attempt": "d" * 32})
            assert status == 200, body
            assert json.loads(body)["received"]
        finally:
            release.set()
        assert task.result(timeout=10)["state"] == "done"
    assert len(reads) == 2
