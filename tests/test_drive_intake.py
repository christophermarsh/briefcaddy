"""Selected intake acceptance with fictional roots/providers; no live Drive.

Processor callback mocks prove orchestration only. Root's integration cohort
must separately exercise process_local on fictional PDFs with real pipeline
code; these mocks are not recognition or packet correctness evidence.
"""
import json
from pathlib import Path
import threading
import time

import httpx
import pytest

import jobs
from connectors import sync
from connectors.base import RemoteClient, RemoteDoc
from connectors.drive_intake import FirmScope, IntakeError, ProcessingFailed, SelectedIntake, READONLY
from connectors.gdrive import GoogleDrive


class FakeDrive:
    name = "google_drive"
    scope = READONLY

    def __init__(self, settings):
        self.s = dict(settings)
        self.people = [RemoteClient("remote-a", "Fictional A"), RemoteClient("remote-b", "Fictional B")]
        self.docs = {c.id: [RemoteDoc("doc-" + c.id, "Same scan.pdf", "application/pdf", 18, "2026-10-04", "v1")] for c in self.people}
        self.downloads, self.listed, self.client_calls = [], [], 0

    def clients(self):
        self.client_calls += 1
        return self.people

    def documents(self, client):
        self.listed.append(client.id)
        return self.docs[client.id]

    def download(self, client, doc):
        self.downloads.append((client.id, doc.id))
        return b"%PDF-1.4 synthetic"

    def publish(self, *_):
        raise AssertionError("Intake must never invoke the result sink")


@pytest.fixture
def cohort(tmp_path, monkeypatch):
    root = tmp_path / "fictional-firm"
    (root / "install").mkdir(parents=True)
    (root / "install/install.json").write_text("{}", encoding="utf-8")
    for case in ("case-a", "case-b"):
        folder = root / "data/clients" / case
        folder.mkdir(parents=True)
        source = root / "clients" / case / "source"
        source.mkdir(parents=True)
        (folder / "fact_graph.json").write_text('{"facts":{}}', encoding="utf-8")
        (folder / "meta.json").write_text(json.dumps({"source_folder": str(source)}), encoding="utf-8")
    scope = FirmScope(root)
    # Every authority/lifecycle path belongs to this same fictional installation.
    for key, name in {"I485_RULES_APPROVED": "rules_approved.json", "I485_MAINTENANCE_LOG": "maintenance_log.json",
                      "I485_EVENTS": "events.jsonl", "I485_SETTINGS": "settings.json", "I485_CASES": "clients",
                      "I485_PROSPECTS": "prospects", "PORTAL_DATA": "portal"}.items():
        monkeypatch.setenv(key, str(scope.data / name))
    monkeypatch.setenv("I485_READER_EXAMPLES", str(scope.data / "reader_examples"))
    settings = {"root_folder_id": "configured-root", "documents_subfolder": None, "results_folder_name": "Disabled results",
                "intake_mapping": {"version": 1, "revision": 1, "bindings": {"remote-a": "case-a", "remote-b": "case-b"}}}
    source = FakeDrive({k: (None if k == "results_folder_name" else v) for k, v in settings.items() if k != "intake_mapping"})
    user = {"email": "staff@fictional.example", "name": "Fictional staff", "role": "paralegal", "active": True}
    access = {"case-a": True, "case-b": True}
    factories = []

    def factory(s, *, write):
        assert write is False and s["results_folder_name"] is None
        factories.append(s)
        return source

    helper = SelectedIntake(scope, lambda: settings, factory, lambda email: user if email == user["email"] else None,
                            lambda actor, folder: access.get(folder.name, False))
    monkeypatch.setitem(jobs.KINDS, "drive_intake", "Selected Drive documents")
    return {"helper": helper, "scope": scope, "source": source, "user": user, "access": access, "settings": settings, "factories": factories}


def preview(c, selected=None, mapping=None):
    return c["helper"].preview(c["user"]["email"], selected if selected is not None else ["remote-a"], mapping if mapping is not None else {"remote-a": "case-a"})


def job_for(c, shown=None, attempt="a" * 32):
    shown = shown or preview(c)
    out = c["helper"].enqueue(c["user"]["email"], shown, attempt, lambda scope: True)
    return jobs.get(c["scope"].queue, out["jobs"]["remote-a"]["id"])


@pytest.mark.parametrize("selected", [None, [], ["remote-a", "remote-a"], ["../a"], "remote-a", ["remote-a"] * 101])
def test_no_all_clients_fallback_or_invalid_selection(cohort, selected):
    with pytest.raises(IntakeError):
        cohort["helper"].preview(cohort["user"]["email"], selected, {"remote-a": "case-a"})
    assert cohort["factories"] == [] and cohort["source"].client_calls == 0


@pytest.mark.parametrize("mapping", [{}, {"remote-a": "../case-a"}, {"remote-a": []}, {"remote-a": "missing"}, {"remote-a": "case-a", "remote-b": "case-b"}])
def test_mapping_must_match_existing_in_root_selected_cases(cohort, mapping):
    with pytest.raises(IntakeError):
        preview(cohort, mapping=mapping)
    assert not cohort["factories"]


def test_duplicate_local_mapping_refused(cohort):
    with pytest.raises(IntakeError):
        preview(cohort, ["remote-a", "remote-b"], {"remote-a": "case-a", "remote-b": "case-a"})


def test_preview_is_readonly_and_does_not_list_unselected_documents(cohort):
    shown = preview(cohort)
    assert shown["writes_to_drive"] is False and shown["processing"] == "not queued"
    assert cohort["source"].listed == ["remote-a"] and cohort["source"].downloads == []
    assert not (cohort["scope"].documents / "sync_state.json").exists()
    assert shown["snapshot"]["mapping"] == {"remote-a": "case-a"}


def test_unknown_or_moved_remote_id_refused_before_download(cohort):
    cohort["source"].people = [RemoteClient("remote-b", "Fictional B")]
    with pytest.raises(IntakeError, match="configured Drive root"):
        preview(cohort)
    assert cohort["source"].downloads == []


@pytest.mark.parametrize("at", ["preview", "enqueue", "execution"])
def test_current_actor_and_case_access_rechecked_at_every_boundary(cohort, at):
    shown = preview(cohort) if at != "preview" else None
    job = job_for(cohort, shown) if at == "execution" else None
    cohort["access"]["case-a"] = False
    with pytest.raises(IntakeError, match="unavailable"):
        if at == "preview":
            preview(cohort)
        elif at == "enqueue":
            cohort["helper"].enqueue(cohort["user"]["email"], shown, "a" * 32, lambda *_: True)
        else:
            cohort["helper"].execute(job, lambda *_: {"processed": True})
    assert cohort["source"].downloads == []


def test_disabled_or_support_actor_cannot_run_import(cohort):
    shown = preview(cohort)
    job = job_for(cohort, shown)
    cohort["user"]["active"] = False
    with pytest.raises(IntakeError, match="active staff"):
        cohort["helper"].execute(job, lambda *_: {"processed": True})
    cohort["user"].update(active=True, role="support")
    with pytest.raises(IntakeError, match="active staff"):
        preview(cohort)


@pytest.mark.parametrize("change", ["version", "document", "config", "client_name"])
def test_changed_preview_rejected_before_copy(cohort, change):
    job = job_for(cohort)
    if change == "version":
        cohort["source"].docs["remote-a"][0].checksum = "v2"
    elif change == "document":
        cohort["source"].docs["remote-a"].append(RemoteDoc("extra", "extra.pdf", "application/pdf"))
    elif change == "config":
        cohort["settings"]["root_folder_id"] = "another-root"
    else:
        cohort["source"].people[0].name = "Changed source name"
    with pytest.raises(IntakeError):
        cohort["helper"].execute(job, lambda *_: {"processed": True})
    assert cohort["source"].downloads == [] and not (cohort["scope"].documents / "sync_state.json").exists()


def test_preview_cannot_cross_firm_actor_or_case(cohort):
    shown = preview(cohort)
    shown["snapshot"]["firm_root"] = str(cohort["scope"].root.parent / "another-firm")
    from connectors.drive_intake import digest
    shown["digest"] = digest(shown["snapshot"])
    with pytest.raises(IntakeError, match="installation and actor"):
        cohort["helper"].enqueue(cohort["user"]["email"], shown, "b" * 32, lambda *_: True)


def test_enqueue_retry_is_one_durable_job_and_payload_change_refused(cohort):
    shown = preview(cohort)
    first = job_for(cohort, shown)
    second = job_for(cohort, shown)
    assert first["id"] == second["id"]
    assert len(list(cohort["scope"].queue.glob("*-drive_intake-*.json"))) == 1
    cohort["source"].docs["remote-a"][0].checksum = "v2"
    with pytest.raises(IntakeError, match="cannot change"):
        job_for(cohort, preview(cohort))


def test_each_selected_job_contains_only_its_current_case_metadata(cohort):
    shown = preview(cohort, ["remote-a", "remote-b"], {"remote-a": "case-a", "remote-b": "case-b"})
    out = cohort["helper"].enqueue(cohort["user"]["email"], shown, "a" * 32, lambda *_: True)
    job = jobs.get(cohort["scope"].queue, out["jobs"]["remote-a"]["id"])
    body = job["args"]["preview"]["snapshot"]
    assert body["selected"] == ["remote-a"] and body["mapping"] == {"remote-a": "case-a"}
    assert [c["remote"] for c in body["clients"]] == ["remote-a"]
    assert "Fictional B" not in json.dumps(job) and "doc-remote-b" not in json.dumps(job)
    cohort["access"]["case-b"] = False
    result = cohort["helper"].execute(job, lambda *_: {"processed": True})
    assert result["processing"]["state"] == "completed"
    assert cohort["source"].downloads == [("remote-a", "doc-remote-a")]


def test_pruned_job_does_not_become_duplicate_or_invented_success(cohort):
    shown = preview(cohort)
    job = job_for(cohort, shown)
    (cohort["scope"].queue / (job["id"] + ".json")).unlink()
    out = cohort["helper"].enqueue(cohort["user"]["email"], shown, "a" * 32, lambda *_: True)
    assert out["jobs"]["remote-a"]["state"] == "unavailable" and out["completed"] is False
    assert not list(cohort["scope"].queue.glob("*-drive_intake-*.json"))


def test_copy_then_immediate_processor_holds_and_idempotent_completion(cohort):
    job = job_for(cohort)
    calls = []

    def processor(scope, case, source, emit):
        assert scope == cohort["scope"] and case == "case-a"
        assert source == scope.documents / case / "source"
        assert len(list(source.glob("*.pdf"))) == 1
        calls.append(case)
        return {"processed": True, "held": True, "review_required": True}

    out = cohort["helper"].execute(job, processor)
    assert out["copy"]["state"] == "completed" and out["processing"]["state"] == "held"
    assert cohort["source"].downloads == [("remote-a", "doc-remote-a")]
    assert not list((cohort["scope"].documents / "case-b/source").glob("*.pdf"))
    assert cohort["helper"].execute(job, processor) == out and calls == ["case-a"]


def test_processing_failure_retains_copy_proof_and_retry_reads_retained_files(cohort):
    job = job_for(cohort)
    with pytest.raises(ProcessingFailed):
        cohort["helper"].execute(job, lambda *_: {"processed": False})
    [phase] = list((cohort["scope"].queue / "drive-receipts").glob("*-phase.json"))
    failed = json.loads(phase.read_text(encoding="utf-8"))
    assert failed["copy"]["state"] == "completed" and failed["processing"]["state"] == "failed"
    assert len(list((cohort["scope"].documents / "case-a/source").glob("*.pdf"))) == 1
    out = cohort["helper"].execute(job, lambda *_: {"processed": True, "held": False})
    assert out["processing"]["state"] == "completed"
    assert len(cohort["source"].downloads) == 1


def test_incompatible_source_folder_refused_before_provider_access(cohort):
    meta = cohort["scope"].cases / "case-a/meta.json"
    meta.write_text(json.dumps({"source_folder": str(cohort["scope"].portal / "clients/case-a/uploads")}), encoding="utf-8")
    with pytest.raises(IntakeError, match="another source folder"):
        preview(cohort)
    assert cohort["factories"] == []


def test_explicit_mirror_mapping_cannot_mix_remote_clients_or_providers(cohort):
    state = cohort["scope"].documents / "sync_state.json"
    state.write_text(json.dumps({"clients": {"case-a": {"remote_id": "other-remote", "source": "clio", "docs": {}}}}), encoding="utf-8")
    before = state.read_bytes()
    with pytest.raises(ValueError, match="different or unverified source"):
        cohort["helper"].execute(job_for(cohort), lambda *_: {"processed": True})
    assert state.read_bytes() == before and cohort["source"].downloads == []


def test_source_must_be_readonly_and_sink_disabled(cohort):
    cohort["source"].scope = "https://www.googleapis.com/auth/drive"
    with pytest.raises(IntakeError, match="read-only"):
        preview(cohort)


@pytest.mark.parametrize("setting", ["I485_EVENTS", "I485_READER_EXAMPLES"])
def test_foreign_audit_or_reader_example_path_refused_before_copy(cohort, monkeypatch, setting):
    shown = preview(cohort)
    job = job_for(cohort, shown)
    monkeypatch.setenv(setting, str(cohort["scope"].root.parent / "another-firm" / setting))
    with pytest.raises(IntakeError, match="audit/evidence"):
        cohort["helper"].execute(job, lambda *_: {"processed": True})
    assert cohort["source"].downloads == []


def test_source_md5_change_refused(cohort):
    cohort["source"].docs["remote-a"][0].raw["md5Checksum"] = "f" * 32
    with pytest.raises(IntakeError, match="changed during download"):
        cohort["helper"].execute(job_for(cohort), lambda *_: {"processed": True})
    assert not list((cohort["scope"].documents / "case-a/source").glob("*.pdf"))


def test_explicit_vault_roots_ignore_inherited_account_file(tmp_path, monkeypatch):
    import firmsecrets
    calls = []
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(tmp_path / "foreign-key.json"))

    def key(name, **kw):
        calls.append((name, kw["data_root"], kw["env"]))
        return json.dumps({"client_email": "fictional@invalid.example", "private_key": "fictional never signed"})

    monkeypatch.setattr(firmsecrets, "get", key)
    for label in ("firm-a", "firm-b"):
        folder = tmp_path / label
        drive = GoogleDrive({"root_folder_id": "configured-root", "results_folder_name": None}, env={}, write=False,
                            data_root=folder / "data", transport=httpx.MockTransport(lambda _: pytest.fail("No provider call authorized")))
        assert drive.scope == READONLY
        drive.http.close()
    assert [c[1] for c in calls] == [tmp_path / "firm-a/data", tmp_path / "firm-b/data"]
    assert all(c[2] == {} for c in calls)


def test_shared_mirror_lock_preserves_both_selected_jobs(cohort):
    one = preview(cohort)
    two = preview(cohort, ["remote-b"], {"remote-b": "case-b"})
    job_a = job_for(cohort, one)
    out_b = cohort["helper"].enqueue(cohort["user"]["email"], two, "b" * 32, lambda *_: True)
    job_b = jobs.get(cohort["scope"].queue, out_b["jobs"]["remote-b"]["id"])
    errors = []
    original = cohort["source"].download

    def slow_download(*a):
        time.sleep(0.03)
        return original(*a)

    cohort["source"].download = slow_download

    def work(job):
        try:
            cohort["helper"].execute(job, lambda *_: {"processed": True})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(j,)) for j in (job_a, job_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()
    assert errors == []
    state = json.loads((cohort["scope"].documents / "sync_state.json").read_text(encoding="utf-8"))
    assert set(state["clients"]) == {"case-a", "case-b"}
    assert all(c["source"] == "google_drive" and c["docs"] for c in state["clients"].values())


@pytest.mark.parametrize("file,record", [("engagement.json", {"end": {"state": "closed"}}),
                                       ("engagement.json", {"destroyed": True})])
def test_case_lifecycle_refusal_precedes_copy(cohort, file, record):
    job = job_for(cohort)
    (cohort["scope"].cases / "case-a" / file).write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError):
        cohort["helper"].execute(job, lambda *_: {"processed": True})
    assert cohort["source"].downloads == []


def test_waiting_purge_refuses_processing(cohort):
    job = job_for(cohort)
    (cohort["scope"].data / "purges.json").write_text(json.dumps({"cases": {"case-a": {"state": "waiting"}}}), encoding="utf-8")
    with pytest.raises(ValueError):
        cohort["helper"].execute(job, lambda *_: {"processed": True})
    assert cohort["source"].downloads == []


@pytest.mark.parametrize("stage", ["receipt", "phase"])
def test_existing_corrupt_durable_state_is_not_replaced(cohort, stage):
    shown = preview(cohort)
    job = job_for(cohort, shown)
    if stage == "receipt":
        proof = next(p for p in (cohort["scope"].queue / "drive-receipts").glob("*.json"))
    else:
        with pytest.raises(ProcessingFailed):
            cohort["helper"].execute(job, lambda *_: {"processed": False})
        proof = next(p for p in (cohort["scope"].queue / "drive-receipts").glob("*-phase.json"))
    proof.write_text("{broken", encoding="utf-8")
    with pytest.raises(IntakeError, match="damaged"):
        if stage == "receipt":
            job_for(cohort, shown)
        else:
            cohort["helper"].execute(job, lambda *_: {"processed": True})
    assert proof.read_text(encoding="utf-8") == "{broken"


def test_completed_proof_requires_retained_files_and_real_completion_record(cohort):
    job = job_for(cohort)
    cohort["helper"].execute(job, lambda *_: {"processed": True})
    [file] = (cohort["scope"].documents / "case-a/source").glob("*.pdf")
    file.write_bytes(b"changed after completion")
    with pytest.raises(IntakeError, match="retained sources changed"):
        cohort["helper"].execute(job, lambda *_: pytest.fail("Must not invent/replay completion"))


@pytest.mark.parametrize("dispatch", ["helper", "worker"])
def test_actual_incremental_reader_preserves_originals_portal_raw_sources_and_current_ev_holds(cohort, monkeypatch, dispatch):
    """Actual PDF readers/merge/refill/index code; only Drive is fictional.

    Test-only schema/environment configuration represents an isolated installed
    firm. Production processing refuses mismatched schema roots and never swaps
    process globals. This is not real-provider, vision-model or packet accuracy.
    """
    import io
    import shutil
    import schema_path
    import document_instances
    from batch import process_documents, record_documents
    from classify import extract_pages
    from factgraph import FactGraph
    from portal.demo import document_pdf
    from portal.store import PortalStore
    from portal.bank import answers_to_facts, PORTAL_DOC_ID
    from pypdf import PdfReader, PdfWriter
    from review.state import save_bundle
    from connectors.drive_intake import process_local

    scope = cohort["scope"]
    shutil.copytree(schema_path.ROOT, schema_path.schemas_in(scope.root))
    monkeypatch.setattr(schema_path, "ROOT", schema_path.schemas_in(scope.root))
    for name, path in {"I485_INDEX": scope.data / "index.db", "I485_QUERY_DB": scope.data / "query.db", "I485_CASES": scope.cases,
                       "I485_CLIENTS_ROOT": scope.documents, "I485_JOBS": scope.queue, "PORTAL_DATA": scope.portal,
                       "I485_SETTINGS": scope.data / "settings.json", "I485_RULES_APPROVED": scope.data / "rules_approved.json",
                       "I485_POLICIES_FIRM": scope.data / "policies_firm.json", "I485_READER_EXAMPLES": scope.data / "reader_examples"}.items():
        monkeypatch.setenv(name, str(path))
    pages = json.loads((Path(__file__).parent / "fixtures/document_instances.json").read_text(encoding="utf-8"))["same_type_i94"]

    def pdf(texts):
        writer = PdfWriter()
        for text in texts:
            writer.add_page(PdfReader(io.BytesIO(document_pdf(text.splitlines()))).pages[0])
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    source = scope.documents / "case-a/source"
    original = pdf([pages[0]])
    retained_portal = pdf([pages[1]])
    (source / "original.pdf").write_bytes(original)
    (source / "portal-retained.pdf").write_bytes(retained_portal)
    store = PortalStore(scope.portal)
    store.add_client("case-a", "Fictional Intake", language="en")
    answers = store.save_answers("case-a", {"given_name": "ALPHA", "birth_country": "Brazil"})
    # Replace the orchestration fixture's placeholder with a real initial bundle.
    (scope.cases / "case-a/fact_graph.json").unlink()
    (scope.cases / "case-a/meta.json").unlink()
    by_page = {name: extract_pages(source / name) for name in ("original.pdf", "portal-retained.pdf")}
    built = process_documents("case-a", [(name, "\n".join(p)) for name, p in by_page.items()], pages=by_page,
                              answers=answers_to_facts(answers), boundary_context=document_instances.context(source, scope.cases / "case-a", list(by_page)))
    record_documents(built, source, built.document_texts, {name: {"pages": len(p)} for name, p in by_page.items()})
    save_bundle(built, scope.cases / "case-a", source)
    before = FactGraph.load(scope.cases / "case-a/fact_graph_raw.json")
    portal_before = {key: [s.normalized_value for s in fact.sources if s.doc_id == PORTAL_DOC_ID]
                     for key, fact in before.all_facts().items() if any(s.doc_id == PORTAL_DOC_ID for s in fact.sources)}
    assert portal_before
    new_pdf = pdf([pages[0], "Additional unidentified page\nBirth Date: 05/06/2001"])
    cohort["source"].download = lambda client, doc: new_pdf
    shown = preview(cohort)
    job = job_for(cohort, shown)
    new_name = sync._filename(cohort["source"].docs["remote-a"][0])

    def production_processor(current, case, folder, emit):
        return process_local(current, case, folder, emit, names=[new_name], use_vision=False, use_policies=False)

    if dispatch == "worker":
        from connectors import drive_intake
        # Mock provider/identity composition only; real registry/run_job,
        # worker_handler, selected-file planning and processor all execute.
        monkeypatch.setattr(drive_intake, "installed", lambda current: cohort["helper"])
        monkeypatch.setattr(drive_intake, "process_local", lambda *args, **kw: process_local(*args, **kw, use_vision=False))
        ctx = jobs.Context(scope.cases, scope.portal, jobs_root=scope.queue, use_policies=False)
        finished = jobs.run_job(ctx, job)
        assert finished["state"] == "done", finished.get("error")
        assert jobs.get(scope.queue, job["id"])["state"] == "done"
        result = finished["result"]
    else:
        result = cohort["helper"].execute(job, production_processor)
    assert result["processing"]["state"] == "held" and result["processing"]["result"]["signed"] is False
    assert (source / "original.pdf").read_bytes() == original and (source / "portal-retained.pdf").read_bytes() == retained_portal
    assert (source / new_name).read_bytes() == new_pdf and store.answers("case-a") == answers
    after = FactGraph.load(scope.cases / "case-a/fact_graph_raw.json")
    for key, values in portal_before.items():
        assert [s.normalized_value for s in after.get(key).sources if s.doc_id == PORTAL_DOC_ID] == values
    aliases = {s.doc_id.split("#p", 1)[0] for f in after.all_facts().values() for s in f.sources}
    assert {"original.pdf", "portal-retained.pdf"}.issubset(aliases)
    new_view = next(v for v in document_instances.views(scope.cases / "case-a") if v["file"] == new_name)
    assert new_view["stale"] is False and new_view["held"] is True and new_view["processing_incomplete"] is False
    assert (scope.data / "index.db").exists() and (scope.data / "query.db").exists()


def scoped_purge_paths(scope, monkeypatch):
    for key, path in {"I485_CLIENTS_ROOT": scope.documents, "I485_JOBS": scope.queue,
                      "I485_INDEX": scope.data / "index.db", "I485_QUERY_DB": scope.data / "query.db",
                      "I485_FIND": scope.data / "find.db", "I485_ROSTER": scope.data / "roster.json",
                      "I485_READER_EXAMPLES": scope.data / "reader_examples"}.items():
        monkeypatch.setenv(key, str(path))


def test_two_case_purge_preserves_other_retry_proofs_sources_and_mirror_state(cohort, monkeypatch):
    import purge
    scope = cohort["scope"]
    scoped_purge_paths(scope, monkeypatch)
    shown = preview(cohort, ["remote-a", "remote-b"], {"remote-a": "case-a", "remote-b": "case-b"})
    out = cohort["helper"].enqueue(cohort["user"]["email"], shown, "b" * 32, lambda *_: False)
    pending = {remote: jobs.get(scope.queue, entry["id"]) for remote, entry in out["jobs"].items()}
    results = {remote: cohort["helper"].execute(job, lambda *_: {"processed": True, "held": True}) for remote, job in pending.items()}
    group_path = next(p for p in (scope.queue / "drive-receipts").glob("*.json") if "-phase" not in p.name)
    group_before = json.loads(group_path.read_text())
    b_phase = next(p for p in (scope.queue / "drive-receipts").glob("*-phase.json") if json.loads(p.read_text())["client"] == "case-b")
    b_phase_before = b_phase.read_bytes()
    b_pdf = next((scope.documents / "case-b/source").glob("*.pdf"))
    b_pdf_before = b_pdf.read_bytes()
    state = json.loads((scope.documents / "sync_state.json").read_text())
    (scope.documents / "sync_state.tmp").write_text(json.dumps(state))
    # Targeted abandoned writes, using the producer's exact temp shape.
    a_phase = next(p for p in (scope.queue / "drive-receipts").glob("*-phase.json") if json.loads(p.read_text())["client"] == "case-a")
    a_phase.with_name(a_phase.name + ".123.456.tmp").write_bytes(a_phase.read_bytes())
    group_path.with_name(group_path.name + ".123.456.tmp").write_bytes(group_path.read_bytes())
    # Queue temp ownership can be proved by the intact per-case reservation.
    a_job_tmp = scope.queue / (pending["remote-a"]["id"] + ".json.123.456.tmp")
    a_job_tmp.write_text("{interrupted")
    b_job_tmp = scope.queue / (pending["remote-b"]["id"] + ".json.123.456.tmp")
    b_job_tmp.write_text(json.dumps(pending["remote-b"]))
    b_job_tmp_before = b_job_tmp.read_bytes()
    a_operation = next(p for p in scope.queue.glob("operation-*.json") if json.loads(p.read_text())["client"] == "case-a")
    a_operation.with_name(a_operation.name + ".123.456.tmp").write_bytes(a_operation.read_bytes())
    with jobs.case_lock(scope.queue, "case-a"):
        report = purge.empty_stores(scope.cases, "case-a", scope.portal)
    assert not any("Drive" in message or "mirrored-document" in message for message in report["left"])
    after = json.loads(group_path.read_text())
    assert after["retired"] is True and after["digest"] == group_before["digest"]
    assert after["jobs"] == {"remote-b": group_before["jobs"]["remote-b"]}
    assert b_phase.read_bytes() == b_phase_before and b_pdf.read_bytes() == b_pdf_before
    assert not (scope.cases / "case-a").exists() and not (scope.documents / "case-a").exists()
    assert not jobs.get(scope.queue, pending["remote-a"]["id"])
    assert jobs.get(scope.queue, pending["remote-b"]["id"])["id"] == pending["remote-b"]["id"]
    for path in (scope.documents / "sync_state.json", scope.documents / "sync_state.tmp"):
        assert json.loads(path.read_text())["clients"] == {"case-b": state["clients"]["case-b"]}
    assert not list((scope.queue / "drive-receipts").glob("*.tmp"))
    assert not a_job_tmp.exists() and b_job_tmp.read_bytes() == b_job_tmp_before
    assert list((scope.queue / "drive-receipts").glob("*.lock"))  # never unlink active lock identities
    assert cohort["helper"].execute(pending["remote-b"], lambda *_: pytest.fail("Completed survivor must not replay")) == results["remote-b"]
    with pytest.raises(IntakeError):
        cohort["helper"].enqueue(cohort["user"]["email"], shown, "b" * 32, lambda *_: False)


def test_unassignable_drive_residue_is_reported_not_deleted(cohort, monkeypatch):
    import purge
    scope = cohort["scope"]
    scoped_purge_paths(scope, monkeypatch)
    residue = scope.queue / "drive-receipts" / ("f" * 64 + ".json.123.456.tmp")
    residue.parent.mkdir(parents=True)
    residue.write_text("{broken")
    with jobs.case_lock(scope.queue, "case-a"):
        result = purge.empty_stores(scope.cases, "case-a")
    assert residue.read_text() == "{broken"
    assert any("Drive intake" in message for message in result["left"])


def test_group_retirement_refuses_recreation_and_preserves_reservation(cohort):
    shown = preview(cohort)
    job = job_for(cohort, shown)
    group = next(p for p in (cohort["scope"].queue / "drive-receipts").glob("*.json") if "-phase" not in p.name)
    rec = json.loads(group.read_text())
    rec["retired"] = True
    group.write_text(json.dumps(rec))
    with pytest.raises(IntakeError, match="retired"):
        job_for(cohort, shown)
    assert jobs.get(cohort["scope"].queue, job["id"])["id"] == job["id"]


def test_enqueue_waits_for_case_lock_and_rechecks_lifecycle_before_publication(cohort, monkeypatch):
    """Deterministic purge-wins interleaving: no association can resurrect."""
    import purge
    from contextlib import contextmanager
    scope = cohort["scope"]
    scoped_purge_paths(scope, monkeypatch)
    shown = preview(cohort)
    attempted, finished = threading.Event(), threading.Event()
    errors = []
    original_lock = jobs.case_lock

    @contextmanager
    def observed(root, case, timeout=None):
        attempted.set()
        with original_lock(root, case, timeout=timeout):
            yield

    def enqueue():
        try:
            job_for(cohort, shown)
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    with original_lock(scope.queue, "case-a"):
        monkeypatch.setattr(jobs, "case_lock", observed)
        thread = threading.Thread(target=enqueue)
        thread.start()
        assert attempted.wait(5) and not finished.is_set()
        purge.empty_stores(scope.cases, "case-a")
    thread.join(10)
    assert finished.is_set() and len(errors) == 1 and isinstance(errors[0], IntakeError)
    assert not list(scope.queue.glob("*-drive_intake-*.json"))
    assert not list((scope.queue / "drive-receipts").glob("*.json"))


def test_drive_records_catalog_and_external_mirror_state_export(cohort, monkeypatch):
    import records
    import sys
    sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
    import export_firm
    scope = cohort["scope"]
    scoped_purge_paths(scope, monkeypatch)
    job = job_for(cohort)
    cohort["helper"].execute(job, lambda *_: {"processed": True})
    for path in (scope.queue / "drive-receipts").glob("*.json"):
        rel = path.relative_to(scope.data).as_posix()
        record = records.record_of("firm", rel)
        assert record and record["id"] in {"drive_intake_groups", "drive_intake_phases"}
        assert record["version"] == 1 and records.coverage("firm", rel) == "never"
    where = export_firm.default_where(scope.cases, scope.portal, scope.data / "review_users.json")
    entries, _, _ = export_firm.everything_entries(where, Path(__file__).parents[1] / "docs/data_dictionary.md")
    state_entries = [e for e in entries if e.source == scope.documents / "sync_state.json"]
    assert len(state_entries) == 1 and state_entries[0].arcname == "firm/sync_state.json"
    assert not any(e.source and "drive-receipts" in e.source.parts for e in entries)


def test_stale_cached_run_after_purge_writes_nothing_and_next_job_runs(cohort, monkeypatch):
    import purge
    scope = cohort["scope"]
    scoped_purge_paths(scope, monkeypatch)
    cached = job_for(cohort)
    with jobs.case_lock(scope.queue, "case-a"):
        purge.empty_stores(scope.cases, "case-a")
    ctx = jobs.Context(scope.cases, scope.portal, jobs_root=scope.queue, use_policies=False)
    ledger_before = list(scope.data.glob("events*.jsonl"))
    result = jobs.run_job(ctx, cached)
    assert result["state"] == "unavailable" and result["result"]["processed"] is False
    assert not jobs.get(scope.queue, cached["id"])
    assert list(scope.data.glob("events*.jsonl")) == ledger_before
    shown = preview(cohort, ["remote-b"], {"remote-b": "case-b"})
    out = cohort["helper"].enqueue(cohort["user"]["email"], shown, "c" * 32, lambda *_: False)
    next_job = jobs.get(scope.queue, out["jobs"]["remote-b"]["id"])
    monkeypatch.setitem(jobs.HANDLERS, "drive_intake", lambda ctx, job, emit: cohort["helper"].execute(job, lambda *_: {"processed": True}, emit))
    assert jobs.run_job(ctx, next_job)["state"] == "done"


@pytest.mark.parametrize("mode", ["sweep", "sweep_dead"])
def test_cached_sweep_row_cannot_recreate_purged_job(cohort, monkeypatch, mode):
    import purge
    scope = cohort["scope"]
    scoped_purge_paths(scope, monkeypatch)
    cached = job_for(cohort)
    path = scope.queue / (cached["id"] + ".json")
    cached["state"] = "running"
    jobs._write(path, cached)
    original_read = jobs._read
    purged = False

    def interleaved_read(current):
        nonlocal purged
        row = original_read(current)
        if Path(current) == path and not purged:
            purged = True
            with jobs.case_lock(scope.queue, "case-a"):
                purge.empty_stores(scope.cases, "case-a")
        return row

    monkeypatch.setattr(jobs, "_read", interleaved_read)
    if mode == "sweep":
        assert jobs.sweep(scope.cases, scope.portal) == []
    else:
        jobs.sweep_dead(jobs.Context(scope.cases, scope.portal, jobs_root=scope.queue))
    assert purged and not jobs.get(scope.queue, cached["id"])
    assert not list((scope.queue / "drive-receipts").glob("*-phase.json"))
