"""Fictional roots/accounts/PDFs only; no services or provider credentials."""
import hashlib
import io
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from pypdf import PdfWriter

import jobs
from portal.store import PortalStore
from review import front_desk
from review import staff_upload_recovery as recovery

ATTEMPT = "a" * 32


def pdf():
    out = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def firm(tmp_path, monkeypatch):
    root = tmp_path / "fictional-staff-firm"
    clients, docs, queue = root / "data/clients", root / "clients", root / "data/jobs"
    clients.mkdir(parents=True)
    docs.mkdir()
    user = {"email": "staff@fictional.example", "name": "Fictional Staff", "role": "attorney", "active": True}
    users = clients.parent / "review_users.json"
    users.write_text(json.dumps({"users": {user["email"]: user}, "sessions": {}}))
    # Install the current canonical scope before enrollment; the store rejects
    # paths inherited from another fictional installation in tests/conftest.
    for key, path in {"I485_JOBS": queue, "I485_CLIENTS_ROOT": docs, "I485_EVENTS": clients.parent / "events.jsonl",
                      "I485_INDEX": clients.parent / "index.db", "I485_QUERY_DB": clients.parent / "query.db",
                      "I485_FIND": clients.parent / "find.db", "I485_READER_EXAMPLES": clients.parent / "reader_examples",
                      "I485_ROSTER": clients.parent / "roster.json", "I485_CASES": clients,
                      "I485_PROSPECTS": clients.parent / "prospects", "PORTAL_DATA": clients.parent / "portal",
                      "I485_SETTINGS": clients.parent / "settings.json",
                      "I485_RULES_APPROVED": clients.parent / "rules_approved.json",
                      "I485_MAINTENANCE_LOG": clients.parent / "maintenance_log.json"}.items():
        monkeypatch.setenv(key, str(path))
    store = PortalStore(clients.parent / "portal")
    for client in ("case-a", "case-b"):
        case = clients / client
        case.mkdir()
        store.add_client(client, "Fictional " + client, by="Fictional Staff")
    source = docs / "case-a/source"
    source.mkdir(parents=True)
    (clients / "case-a/fact_graph.json").write_text('{"facts":{}}')
    (clients / "case-a/meta.json").write_text(json.dumps({"source_folder": str(source)}))
    return {"clients": clients, "docs": docs, "queue": queue, "store": store, "source": source,
            "user": user, "access": {"case-a": True, "case-b": True}, "users": users}


def trusted(f):
    return {"actor_email": f["user"]["email"], "actor_reader": lambda email: f["user"] if email == f["user"]["email"] else None,
            "may_access": lambda actor, case: f["access"].get(case.name, False), "jobs_root": f["queue"], "documents_root": f["docs"]}


def accept(f, client="case-a", data=None, attempt=ATTEMPT, wake=None, original="fictional.pdf"):
    return front_desk.accept_staff_upload(f["clients"], f["store"], client, original, pdf() if data is None else data, attempt,
                                         wake_worker=wake or (lambda *_: False), **trusted(f))


def outcome(f, client="case-a", attempt=ATTEMPT):
    return front_desk.staff_upload_outcome(f["clients"], f["store"], client, attempt, **trusted(f))


def heic():
    from PIL import Image
    from pillow_heif import register_heif_opener
    register_heif_opener()
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), "#6495ed").save(buffer, format="HEIF", quality=90)
    return buffer.getvalue()


def test_heic_photo_converts_to_one_pdf_and_retries_without_duplicates(firm):
    from pypdf import PdfReader
    raw = heic()
    first = accept(firm, client="case-b", data=raw, original="fictional-iphone.HEIC")
    again = accept(firm, client="case-b", data=raw, original="fictional-iphone.HEIC")
    assert first["received"] and first["name"] == again["name"]
    uploads = firm["store"].uploads("case-b")
    assert len(uploads) == 1 and uploads[0]["source_sha256"] == hashlib.sha256(raw).hexdigest()
    retained = firm["store"].client_dir("case-b") / "uploads" / uploads[0]["stored"]
    assert len(PdfReader(retained).pages) == 1
    assert PdfReader(retained).pages[0].images[0].image.size == (64, 48)
    assert len(list(firm["queue"].glob("*-staff_upload-*.json"))) == 1


def test_broken_heic_does_not_create_a_receipt_or_file(firm):
    with pytest.raises(ValueError, match="image can't be opened"):
        accept(firm, client="case-b", data=heic()[:64], original="broken.HEIC")
    assert not firm["store"].uploads("case-b")
    assert not list(firm["queue"].glob("*-staff_upload-*.json"))


def test_lost_response_and_concurrent_retries_keep_one_file_and_job(firm):
    data = pdf()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: accept(firm, data=data), range(4)))
    assert len({r["name"] for r in results}) == len({r["job"]["id"] for r in results}) == 1
    assert len(list(firm["source"].glob("*.pdf"))) == 1
    assert len(list(firm["queue"].glob("*-staff_upload-*.json"))) == 1
    assert results[0]["received"] and not results[0]["processed"] and results[0]["review_required"]
    assert outcome(firm)["job"]["id"] == results[0]["job"]["id"]


@pytest.mark.parametrize("changed", ["bytes", "name", "actor"])
def test_same_attempt_changed_payload_refused(firm, changed):
    initial = accept(firm)
    kw = {}
    if changed == "bytes":
        kw["data"] = pdf() + b"\nchanged"
    elif changed == "name":
        kw["original"] = "another.pdf"
    else:
        firm["user"]["email"] = "another@fictional.example"
    with pytest.raises(ValueError, match="attempt_conflict"):
        accept(firm, **kw)
    assert len(list(firm["source"].glob("*.pdf"))) == 1
    assert jobs.get(firm["queue"], initial["job"]["id"])


@pytest.mark.parametrize("fault", ["file", "row", "job", "response"])
def test_after_effect_fault_repairs_without_duplicate_file_row_or_job(firm, monkeypatch, fault):
    client = "case-b"  # authorized protected portal-only case; no fabricated bundle
    fault_patches = pytest.MonkeyPatch()
    if fault == "file":
        real = recovery.os.replace
        def replace(src, dest):
            real(src, dest)
            if str(dest).endswith(".pdf"):
                raise OSError("synthetic fault after retained file commit")
        fault_patches.setattr(recovery.os, "replace", replace)
    elif fault == "row":
        real = firm["store"].update_uploads
        def row(c, rows):
            real(c, rows)
            raise OSError("synthetic fault after portal row commit")
        fault_patches.setattr(firm["store"], "update_uploads", row)
    elif fault == "job":
        real = jobs.submit
        def submit(*args, **kw):
            real(*args, **kw)
            raise OSError("synthetic fault after job reservation/publication")
        fault_patches.setattr(jobs, "submit", submit)
    wake = (lambda *_: (_ for _ in ()).throw(OSError("synthetic lost response"))) if fault == "response" else None
    with pytest.raises(OSError):
        accept(firm, client=client, wake=wake)
    fault_patches.undo()
    result = accept(firm, client=client)
    assert result["received"] and result["reading"] and not result["processed"]
    assert len(firm["store"].uploads(client)) == 1
    assert len(list((firm["store"].client_dir(client) / "uploads").glob("*.pdf"))) == 1
    assert len(list(firm["queue"].glob("*-staff_upload-*.json"))) == 1
    assert not (firm["clients"] / client / "fact_graph.json").exists()
    assert not (firm["clients"] / client / "meta.json").exists()


def test_original_image_hash_differs_from_retained_pdf_hash(firm):
    from PIL import Image
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(image, format="PNG")
    original = image.getvalue()
    result = accept(firm, data=original, original="fictional.png")
    rec = json.loads(next((firm["clients"] / "case-a/staff-upload-receipts").glob("*.json")).read_text())
    assert rec["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert rec["sha256"] == hashlib.sha256((firm["source"] / result["name"]).read_bytes()).hexdigest()
    assert rec["source_sha256"] != rec["sha256"] and accept(firm, data=original, original="fictional.png")["name"] == result["name"]


def test_other_case_source_association_refused_before_file_receipt_or_job(firm):
    foreign = firm["docs"] / "case-b/source"
    foreign.mkdir(parents=True)
    (firm["clients"] / "case-a/meta.json").write_text(json.dumps({"source_folder": str(foreign)}))
    with pytest.raises(ValueError, match="own canonical"):
        accept(firm)
    assert not list(foreign.iterdir())
    assert not (firm["clients"] / "case-a/staff-upload-receipts").exists()
    assert not list(firm["queue"].glob("*-staff_upload-*.json"))


@pytest.mark.parametrize("foreign_explicit", [False, True])
def test_foreign_inherited_or_explicit_queue_refused_before_publication(firm, monkeypatch, foreign_explicit):
    foreign = firm["clients"].parents[2] / "fictional-other-firm/data/jobs"
    foreign.mkdir(parents=True)
    monkeypatch.setenv("I485_JOBS", str(foreign))
    if foreign_explicit:
        firm["queue"] = foreign
    with pytest.raises(ValueError, match="configured queue"):
        accept(firm)
    assert not list(foreign.iterdir()) and not list(firm["source"].glob("*.pdf"))
    assert not (firm["clients"] / "case-a/staff-upload-receipts").exists()


@pytest.mark.parametrize("lost", ["job", "proof"])
def test_missing_pruned_reserved_work_is_unavailable_never_recreated(firm, lost):
    result = accept(firm)
    path = (firm["queue"] / (result["job"]["id"] + ".json")) if lost == "job" else next(firm["queue"].glob("operation-*.json"))
    path.unlink()
    retried = accept(firm)
    assert retried["status"] == "unavailable" and not retried["processed"]
    assert not path.exists() and len(list(firm["source"].glob("*.pdf"))) == 1


@pytest.mark.parametrize("revocation", ["acl", "account"])
def test_retry_and_outcome_recheck_live_authority(firm, revocation):
    accept(firm)
    if revocation == "acl":
        firm["access"]["case-a"] = False
    else:
        firm["user"]["active"] = False
    for call in (lambda: accept(firm), lambda: outcome(firm)):
        with pytest.raises(PermissionError):
            call()


def test_worker_rechecks_current_account_exact_payload_and_source(firm):
    result = accept(firm)
    job = jobs.get(firm["queue"], result["job"]["id"])
    recovery.validate_for_read(firm["clients"], firm["store"], "case-a", job["args"], job["by"])
    changed = {**job["args"], "name": "other.pdf"}
    with pytest.raises(ValueError, match="payload_changed"):
        recovery.validate_for_read(firm["clients"], firm["store"], "case-a", changed, job["by"])
    (firm["source"] / job["args"]["name"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="source_changed"):
        recovery.validate_for_read(firm["clients"], firm["store"], "case-a", job["args"], job["by"])
    firm["user"]["active"] = False
    firm["users"].write_text(json.dumps({"users": {firm["user"]["email"]: firm["user"]}}))
    with pytest.raises(PermissionError):
        recovery.validate_for_read(firm["clients"], firm["store"], "case-a", job["args"], job["by"])


def test_portal_only_q1_removes_receipt_and_sources_without_fabricated_case(firm):
    import purge
    accept(firm, client="case-b")
    case = firm["clients"] / "case-b"
    assert (case / "staff-upload-receipts").is_dir() and not (case / "fact_graph.json").exists()
    with jobs.case_lock(firm["queue"], "case-b"):
        report = purge.empty_stores(firm["clients"], "case-b", firm["store"].root)
    assert not case.exists() and not firm["store"].client_dir("case-b").exists()
    assert not list(firm["queue"].glob("*-staff_upload-*.json"))
    assert not any("job records" in message for message in report["left"])
    assert (firm["clients"] / "case-a/fact_graph.json").exists()


def test_q1_cleans_owned_job_proof_temps_preserves_other_case_and_reports_unknown(firm):
    import purge
    a, b = accept(firm), accept(firm, client="case-b")
    a_job = jobs.get(firm["queue"], a["job"]["id"])
    b_job = jobs.get(firm["queue"], b["job"]["id"])
    a_tmp = firm["queue"] / (a_job["id"] + ".json.123.456.tmp")
    a_tmp.write_text("{interrupted")  # intact operation proves this exact job owner
    b_tmp = firm["queue"] / (b_job["id"] + ".json.123.456.tmp")
    b_tmp.write_text(json.dumps(b_job))
    b_before = b_tmp.read_bytes()
    a_proof = recovery._proof(firm["queue"], "case-a", recovery.key(ATTEMPT))
    proof_tmp = a_proof.with_name(a_proof.name + ".123.456.tmp")
    proof_tmp.write_bytes(a_proof.read_bytes())
    unknown = firm["queue"] / ("operation-" + "f" * 64 + ".json.123.456.tmp")
    unknown.write_text("{unassignable")
    with jobs.case_lock(firm["queue"], "case-a"):
        result = purge.empty_stores(firm["clients"], "case-a", firm["store"].root)
    assert not a_tmp.exists() and not proof_tmp.exists() and not a_proof.exists()
    assert b_tmp.read_bytes() == b_before and jobs.get(firm["queue"], b_job["id"])
    assert unknown.read_text() == "{unassignable"
    assert any("staff upload interrupted" in message for message in result["left"])


def test_conflicting_client_proofs_never_authorize_temp_deletion(firm):
    a = accept(firm)
    job = jobs.get(firm["queue"], a["job"]["id"])
    path = firm["queue"] / (job["id"] + ".json.123.456.tmp")
    path.write_text("{interrupted")
    conflicting = firm["queue"] / ("operation-" + "f" * 64 + ".json")
    conflicting.write_text(json.dumps({"kind": "staff_upload", "client": "case-b", "id": job["id"], "payload": "b" * 64, "state": "reserved"}))
    with jobs.case_lock(firm["queue"], "case-a"):
        removed, unresolved = recovery.purge_job_staging(firm["queue"], {"case-a"})
    assert removed == 0 and unresolved > 0 and path.read_text() == "{interrupted"


@pytest.mark.parametrize("marker", ["valid", None, "malformed"])
@pytest.mark.parametrize("method", ["run", "sweep", "sweep_dead"])
def test_cached_receipt_bound_rows_cannot_resurrect_after_purge(firm, monkeypatch, marker, method):
    import purge
    result = accept(firm)
    cached = jobs.get(firm["queue"], result["job"]["id"])
    if marker != "valid":
        cached["args"]["staff_receipt"] = marker
    path = firm["queue"] / (cached["id"] + ".json")
    cached["state"] = "running" if method != "run" else "queued"
    jobs._write(path, cached)
    ctx = jobs.Context(firm["clients"], firm["store"].root, jobs_root=firm["queue"])
    if method == "run":
        with jobs.case_lock(firm["queue"], "case-a"):
            purge.empty_stores(firm["clients"], "case-a", firm["store"].root)
        assert jobs.run_job(ctx, cached)["state"] == "unavailable"
    else:
        real_read = jobs._read
        done = False
        def interleave(p):
            nonlocal done
            row = real_read(p)
            if Path(p) == path and not done:
                done = True
                with jobs.case_lock(firm["queue"], "case-a"):
                    purge.empty_stores(firm["clients"], "case-a", firm["store"].root)
            return row
        monkeypatch.setattr(jobs, "_read", interleave)
        if method == "sweep":
            assert jobs.sweep(firm["clients"], firm["store"].root) == []
        else:
            jobs.sweep_dead(ctx)
        assert done
    assert not jobs.get(firm["queue"], cached["id"])


def test_legacy_stage_caller_and_marker_absence_keep_existing_contract(firm):
    staged = front_desk.stage_upload(firm["clients"], firm["store"], "case-a", "legacy.pdf", pdf(), "Fictional Staff")
    assert "staff_receipt" not in staged and not jobs._protected_intake({"kind": "staff_upload", "args": staged})
    assert (firm["source"] / staged["name"]).exists()


def test_reader_commit_before_job_done_is_not_globally_exactly_once(firm, monkeypatch):
    result = accept(firm)
    job = jobs.get(firm["queue"], result["job"]["id"])
    reads = []
    def reader(clients, store, client, staged, by, *args, **kw):
        recovery.validate_for_read(clients, store, client, staged, by)
        reads.append(staged["name"])
        return {"processed": True}
    monkeypatch.setattr(front_desk, "read_staff_upload", reader)
    real_finish = jobs._finish
    def finish(root, current, state, **kw):
        if state == "done":
            raise OSError("synthetic reader committed before job done")
        return real_finish(root, current, state, **kw)
    monkeypatch.setattr(jobs, "_finish", finish)
    assert jobs.run_job(jobs.Context(firm["clients"], firm["store"].root), job)["state"] == "failed"
    retried = accept(firm)
    assert not retried["processed"] and retried["review_required"] and retried["job"]["state"] == "failed"
    assert reads == [job["args"]["name"]] and len(list(firm["source"].glob("*.pdf"))) == 1


def test_registered_staff_worker_actual_incremental_pdf_reader_preserves_base_evidence(firm, monkeypatch):
    """Real registered worker/reader, legitimate base bundle; no model accuracy claim."""
    import shutil
    import schema_path
    import document_instances
    from batch import process_documents, record_documents
    from classify import extract_pages
    from factgraph import FactGraph
    from portal.demo import document_pdf
    from review.state import save_bundle

    root = firm["clients"].parent.parent
    shutil.copytree(schema_path.ROOT, schema_path.schemas_in(root))
    monkeypatch.setattr(schema_path, "ROOT", schema_path.schemas_in(root))
    for key, path in {"I485_CASES": firm["clients"], "PORTAL_DATA": firm["store"].root,
                      "I485_SETTINGS": firm["clients"].parent / "settings.json",
                      "I485_RULES_APPROVED": firm["clients"].parent / "rules_approved.json",
                      "I485_POLICIES_FIRM": firm["clients"].parent / "policies_firm.json"}.items():
        monkeypatch.setenv(key, str(path))
    page = json.loads((Path(__file__).parent / "fixtures/document_instances.json").read_text())["same_type_i94"][0]
    original = document_pdf(page.splitlines())
    old_path = firm["source"] / "retained-original.pdf"
    old_path.write_bytes(original)
    case = firm["clients"] / "case-a"
    (case / "fact_graph.json").unlink()
    (case / "meta.json").unlink()
    pages = extract_pages(old_path)
    inputs = [(old_path.name, "\n".join(pages))]
    built = process_documents("case-a", inputs, pages={old_path.name: pages},
                              boundary_context=document_instances.context(firm["source"], case, [old_path.name]))
    record_documents(built, firm["source"], built.document_texts, {old_path.name: {"pages": len(pages)}})
    save_bundle(built, case, firm["source"])
    before = FactGraph.load(case / "fact_graph_raw.json")
    old_sources = {key: [s.normalized_value for s in fact.sources if s.doc_id.split("#p", 1)[0] == old_path.name]
                   for key, fact in before.all_facts().items() if any(s.doc_id.split("#p", 1)[0] == old_path.name for s in fact.sources)}
    assert old_sources
    incoming = document_pdf((page + "\nAdditional unidentified page\nBirth Date: 05/06/2001").splitlines())
    submitted = accept(firm, data=incoming)
    job = jobs.get(firm["queue"], submitted["job"]["id"])
    finished = jobs.run_job(jobs.Context(firm["clients"], firm["store"].root, jobs_root=firm["queue"], use_policies=False), job)
    assert finished["state"] == "done", finished.get("error")
    assert finished["result"]["processed"] is True
    assert old_path.read_bytes() == original and (firm["source"] / submitted["name"]).read_bytes() == incoming
    after = FactGraph.load(case / "fact_graph_raw.json")
    for key, values in old_sources.items():
        assert [s.normalized_value for s in after.get(key).sources if s.doc_id.split("#p", 1)[0] == old_path.name] == values
    view = next(v for v in document_instances.views(case) if v["file"] == submitted["name"])
    assert not view["stale"] and not view["processing_incomplete"]
    retried = accept(firm, data=incoming)
    assert retried["job"]["id"] == submitted["job"]["id"] and retried["name"] == submitted["name"]
    assert retried["processed"] and retried["review_required"]
    assert len(list(firm["source"].glob("*.pdf"))) == 2

def test_upload_receipt_and_retry_continue_while_reader_is_in_ocr(firm, monkeypatch):
    import threading
    import oslock
    from classify import classifier
    entered, release = threading.Event(), threading.Event()
    reads = []
    def slow_ocr(data):
        reads.append(hashlib.sha256(data).hexdigest())
        if len(reads) == 1:
            entered.set()
            assert release.wait(10)
        assert not oslock.held_by_someone(firm["clients"].parent / "communication.lock")
        return ["Fictional document text"]
    monkeypatch.setattr(classifier, "_extract_pdf_pages", slow_ocr)
    def handler(ctx, job, progress):
        recovery.validate_for_read(ctx.clients, ctx.store(), job["client"], job["args"], job["by"])
        for upload in ctx.store().uploads(job["client"]):
            assert classifier.extract_pages(ctx.store().client_dir(job["client"]) / "uploads" / upload["stored"]) == ["Fictional document text"]
        return {"processed": True}
    monkeypatch.setitem(jobs.HANDLERS, "staff_upload", handler)
    first = accept(firm, client="case-b")
    job = jobs.get(firm["queue"], first["job"]["id"])
    ctx = jobs.Context(firm["clients"], firm["store"].root, jobs_root=firm["queue"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        reading = pool.submit(jobs.run_job, ctx, job)
        try:
            assert entered.wait(5)
            writer, out = PdfWriter(), io.BytesIO()
            writer.add_blank_page(width=100, height=100)
            writer.write(out)
            second = accept(firm, client="case-b", data=out.getvalue(), attempt="b" * 32)
            repeated = accept(firm, client="case-b", data=out.getvalue(), attempt="b" * 32)
            assert second["received"] and second["reading"]
            assert second["job"]["id"] == repeated["job"]["id"]
            assert outcome(firm, client="case-b", attempt="b" * 32)["received"]
            assert len(firm["store"].uploads("case-b")) == 2
            assert len(list(firm["queue"].glob("*-staff_upload-*.json"))) == 2
        finally:
            release.set()
        assert reading.result(timeout=10)["state"] == "done"
    assert len(reads) == 2  # Arrival during OCR is read outside the lock too.
    assert classifier._PAGE_CACHE.get() is None

@pytest.mark.parametrize("change", ["revoke", "replace"])
def test_reader_rechecks_authority_and_original_after_unlocked_ocr(firm, monkeypatch, change):
    from classify import classifier
    applied = []
    def read(data):
        if change == "revoke":
            firm["user"]["active"] = False
            firm["users"].write_text(json.dumps({"users": {firm["user"]["email"]: firm["user"]}, "sessions": {}}))
        else:
            next(firm["source"].glob("*.pdf")).write_bytes(b"changed original")
        return ["Fictional text"]
    monkeypatch.setattr(classifier, "_extract_pdf_pages", read)
    def handler(ctx, job, progress):
        recovery.validate_for_read(ctx.clients, ctx.store(), job["client"], job["args"], job["by"])
        applied.append(True)
        return {"processed": True}
    monkeypatch.setitem(jobs.HANDLERS, "staff_upload", handler)
    result = accept(firm)
    job = jobs.get(firm["queue"], result["job"]["id"])
    finished = jobs.run_job(jobs.Context(firm["clients"], firm["store"].root, jobs_root=firm["queue"]), job)
    assert finished["state"] == "failed"
    assert not applied
    assert classifier._PAGE_CACHE.get() is None

def test_incremental_staff_photo_reads_only_new_file(firm, monkeypatch):
    from classify import classifier
    (firm["source"] / "older-original.pdf").write_bytes(b"older original is not needed by incremental reader")
    reads = []
    def ocr(data):
        reads.append(data)
        assert data == pdf()
        return ["Fictional new scan"]
    monkeypatch.setattr(classifier, "_extract_pdf_pages", ocr)
    def handler(ctx, job, progress):
        recovery.validate_for_read(ctx.clients, ctx.store(), job["client"], job["args"], job["by"])
        assert classifier.extract_pages(firm["source"] / job["args"]["name"]) == ["Fictional new scan"]
        return {"processed": True}
    monkeypatch.setitem(jobs.HANDLERS, "staff_upload", handler)
    result = accept(firm)
    job = jobs.get(firm["queue"], result["job"]["id"])
    assert job["args"]["case"]
    assert jobs.run_job(jobs.Context(firm["clients"], firm["store"].root, jobs_root=firm["queue"]), job)["state"] == "done"
    assert len(reads) == 1
