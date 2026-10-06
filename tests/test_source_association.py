"""Fictional roots/PDFs; real protected creation and canonical worker pipelines."""
import json
from pathlib import Path
import shutil

import pytest

import document_instances
import jobs
import schema_path
import source_association as association
from connectors.base import RemoteClient, RemoteDoc
from connectors.drive_intake import FirmScope, SelectedIntake, READONLY
from portal import engine, queue_bridge
from portal.demo import document_pdf
from portal.store import PortalStore
from review import front_desk


@pytest.fixture
def firm(tmp_path, monkeypatch):
    root = tmp_path / "fictional-association-firm"
    (root / "install").mkdir(parents=True)
    (root / "install/install.json").write_text("{}")
    scope = FirmScope(root)
    scope.cases.mkdir(parents=True)
    scope.documents.mkdir()
    shutil.copytree(schema_path.ROOT, root / "schemas")
    monkeypatch.setattr(schema_path, "ROOT", root / "schemas")
    user = {"email": "staff@fictional.example", "name": "Fictional Attorney", "role": "attorney", "active": True}
    (scope.data / "review_users.json").write_text(json.dumps({"users": {user["email"]: user}, "sessions": {}}))
    for name, path in {"I485_INDEX": scope.data / "index.db", "I485_QUERY_DB": scope.data / "query.db",
                       "I485_CASES": scope.cases, "I485_CLIENTS_ROOT": scope.documents,
                       "I485_JOBS": scope.queue, "PORTAL_DATA": scope.portal,
                       "I485_EVENTS": scope.data / "events.jsonl", "I485_READER_EXAMPLES": scope.data / "reader_examples",
                       "I485_MAINTENANCE_LOG": scope.data / "maintenance_log.json",
                       "I485_PROSPECTS": scope.data / "prospects",
                       "I485_SETTINGS": scope.data / "settings.json", "I485_RULES_APPROVED": scope.data / "rules_approved.json",
                       "I485_POLICIES_FIRM": scope.data / "policies_firm.json", "I485_FIND": scope.data / "find.db",
                       "I485_ROSTER": scope.data / "roster.json", "I485_CONFLICTS": scope.data / "conflict_checks.jsonl"}.items():
        monkeypatch.setenv(name, str(path))
    import query
    query.rebuild_changed(scope.cases, force=True)
    store = PortalStore(scope.portal)
    created = front_desk.add_client(store, scope.cases, {"name": "Fictional Association Client", "language": "en", "track": "vawa",
                                  "conflict": {"decision": "none"}}, user["name"], role="attorney", may_see=lambda _: True)
    client = created["id"]
    assert created["restricted"] and not (scope.cases / client / "fact_graph.json").exists()
    pages = json.loads((Path(__file__).parent / "fixtures/document_instances.json").read_text())["same_type_i94"]
    return scope, store, client, user, pages


def associate(firm):
    scope, store, client, user, _ = firm
    return association.associate(scope.root, store, client, actor_email=user["email"], use_policies=False)


def upload(firm, text, original="fictional.pdf"):
    _, store, client, _, _ = firm
    return store.add_upload(client, "i94", original, document_pdf(text.splitlines()), "application/pdf")


def test_created_case_drive_then_later_portal_generation_preserves_complete_evidence(firm, monkeypatch):
    from connectors import drive_intake, sync
    from factgraph import FactGraph
    from portal.bank import PORTAL_DOC_ID
    import documents
    scope, store, client, user, pages = firm
    first = upload(firm, pages[0])
    store.save_answers(client, {"given_name": "ALPHA", "birth_country": "Brazil"})
    associated = associate(firm)
    source = scope.documents / client / "source"
    assert associated["associated"] and not association.hold(scope.cases / client)
    original = (store.client_dir(client) / "uploads" / first["stored"]).read_bytes()
    assert (source / first["stored"]).read_bytes() == original
    before = FactGraph.load(scope.cases / client / "fact_graph_raw.json")
    old = {k: [s.normalized_value for s in f.sources if s.doc_id.split("#p", 1)[0] == first["stored"]]
           for k, f in before.all_facts().items() if any(s.doc_id.split("#p", 1)[0] == first["stored"] for s in f.sources)}
    assert old
    drive_pdf = document_pdf((pages[1] + "\nDrive retained original").splitlines())
    remote_doc = RemoteDoc("document-a", "Drive evidence.pdf", "application/pdf", len(drive_pdf), "2026-10-05", "v1")
    settings = {"root_folder_id": "explicit-root", "documents_subfolder": None, "results_folder_name": None,
                "intake_mapping": {"version": 1, "revision": 1, "bindings": {"remote-a": client}}}
    class FictionalDrive:
        name, scope = "google_drive", READONLY
        s = {k: v for k, v in settings.items() if k != "intake_mapping"}
        def clients(self):
            return [RemoteClient("remote-a", "Fictional selected client")]
        def documents(self, _):
            return [remote_doc]
        def download(self, *_):
            return drive_pdf
    helper = SelectedIntake(scope, lambda: settings, lambda *_a, **_k: FictionalDrive(),
                            lambda email: user if email == user["email"] else None, lambda *_: True)
    shown = helper.preview(user["email"], ["remote-a"], {"remote-a": client})
    queued = helper.enqueue(user["email"], shown, "a" * 32, lambda *_: True)
    job = jobs.get(scope.queue, queued["jobs"]["remote-a"]["id"])
    monkeypatch.setattr(drive_intake, "installed", lambda _: helper)
    ctx = jobs.Context(scope.cases, scope.portal, jobs_root=scope.queue, use_policies=False)
    result = jobs.run_job(ctx, job)
    assert result["state"] == "done", result.get("error")
    processing = result["result"]["processing"]
    assert processing["state"] in {"completed", "held"}
    assert processing["result"]["processed"] and processing["result"]["review_required"]
    assert processing["result"]["signed"] is False and processing["result"]["sent"] is False
    drive_name = sync._filename(remote_doc)
    assert (source / drive_name).read_bytes() == drive_pdf
    replacement = {"email": "replacement@fictional.example", "name": "Fictional Replacement", "role": "attorney", "active": True}
    user["active"] = False
    (scope.data / "review_users.json").write_text(json.dumps({"users": {user["email"]: user, replacement["email"]: replacement}}))
    with pytest.raises(PermissionError):
        engine.process_client(store, client, scope.cases, use_policies=False)
    association.associate(scope.root, store, client, actor_email=replacement["email"], use_policies=False)
    assert (source / drive_name).read_bytes() == drive_pdf
    later = upload(firm, pages[0] + "\nLater client original", "later.pdf")
    store.save_answers(client, {"given_name": "UPDATED"})
    store.enqueue(client)
    queue_bridge.bridge(ctx)
    _, portal_job = queue_bridge.current_job(ctx, client)
    assert portal_job is not None
    finished = jobs.run_job(ctx, portal_job)
    assert finished["state"] == "done", finished.get("error")
    meta = json.loads((scope.cases / client / "meta.json").read_text())
    assert meta["source_folder"] == str(source)
    assert (source / first["stored"]).read_bytes() == original and (source / drive_name).read_bytes() == drive_pdf
    assert (source / later["stored"]).read_bytes() == (store.client_dir(client) / "uploads" / later["stored"]).read_bytes()
    after = FactGraph.load(scope.cases / client / "fact_graph_raw.json")
    for key, values in old.items():
        assert [s.normalized_value for s in after.get(key).sources if s.doc_id.split("#p", 1)[0] == first["stored"]] == values
    assert any(s.doc_id == PORTAL_DOC_ID and s.normalized_value == "UPDATED" for s in after.get("applicant.given_name").sources)
    rows = documents.read(scope.cases / client)["documents"]
    assert next(r for r in rows if drive_name in r["files"])["source"] == "drive"
    assert next(r for r in rows if first["stored"] in r["files"])["source"] == "portal"
    assert all(not row["stale"] and not row.get("processing_incomplete") for row in document_instances.views(scope.cases / client))
    assert not queue_bridge._generation(store, client)


@pytest.mark.parametrize("effect", ["copy", "meta"])
def test_interrupted_association_holds_engine_drive_and_incremental_then_recovers(firm, monkeypatch, effect):
    scope, store, client, user, pages = firm
    upload(firm, pages[0])
    real_replace, real_atomic = association.os.replace, association._atomic
    with monkeypatch.context() as fault:
        if effect == "copy":
            def replace(src, dst):
                real_replace(src, dst)
                if str(dst).endswith(".pdf"):
                    raise OSError("synthetic after copy")
            fault.setattr(association.os, "replace", replace)
        else:
            def atomic(path, value):
                real_atomic(path, value)
                if Path(path).name == "meta.json" and value.get("source_folder") == str(scope.documents / client / "source"):
                    raise OSError("synthetic after meta")
            fault.setattr(association, "_atomic", atomic)
        with pytest.raises(OSError):
            associate(firm)
    case = scope.cases / client
    graph = (case / "fact_graph_raw.json").read_bytes()
    assert association.hold(case) and document_instances.problems(case)
    with pytest.raises(ValueError, match="incomplete"):
        engine.process_client(store, client, scope.cases, use_policies=False)
    with pytest.raises(ValueError, match="incomplete"):
        scope.case(client)
    with pytest.raises(ValueError, match="incomplete"):
        document_instances.context(scope.documents / client / "source", case, [])
    # Actual staff/inbox orchestration reaches the same source hold before merge.
    source_name = association._read(case)["files"][0]["name"]
    staff = front_desk.read_staff_upload(scope.cases, store, client, {"name": source_name, "pages": 1, "case": True}, "Fictional Staff")
    assert not staff["processed"] and "incomplete" in staff["error"]
    import inbox
    with pytest.raises(ValueError, match="incomplete"):
        inbox.reprocess_documents(case, Path(json.loads((case / "meta.json").read_text())["source_folder"]),
                                  [(source_name, pages[0])], {source_name: {"pages": 1}}, pages={source_name: [pages[0]]})
    assert (case / "fact_graph_raw.json").read_bytes() == graph
    replacement = {"email": "replacement@fictional.example", "name": "Fictional Replacement", "role": "attorney", "active": True}
    user["active"] = False
    (scope.data / "review_users.json").write_text(json.dumps({"users": {user["email"]: user, replacement["email"]: replacement}}))
    assert association.associate(scope.root, store, client, actor_email=replacement["email"], use_policies=False)["associated"]
    assert not association.hold(case) and association._read(case)["actor"] == replacement["email"]
    engine.process_client(store, client, scope.cases, use_policies=False)
    assert not any(row.get("processing_incomplete") for row in document_instances.views(case))


def test_pending_empty_and_damaged_association_hold_review(firm):
    scope, _, client, user, _ = firm
    case = scope.cases / client
    path = case / association.FILE
    value = {"version": 1, "client": client, "actor": user["email"], "destination": str(scope.documents / client / "source"),
             "files": [], "status": "pending", "initial": True}
    path.write_text(json.dumps(value))
    assert association.hold(case) and document_instances.problems(case)
    view = document_instances.views(case)[0]
    assert view["hold_kind"] == "source_association" and view["physical_file"] is False
    assert "Source setup" in document_instances.problems(case)[0]
    assert "boundaries" not in document_instances.problems(case)[0]
    path.write_text("{damaged")
    assert association.hold(case) and document_instances.problems(case)


def test_revoked_originating_staff_requires_explicit_authorized_handover(firm):
    scope, store, client, user, pages = firm
    upload(firm, pages[0])
    associate(firm)
    new = {"email": "replacement@fictional.example", "name": "Fictional Replacement", "role": "attorney", "active": True}
    user["active"] = False
    (scope.data / "review_users.json").write_text(json.dumps({"users": {user["email"]: user, new["email"]: new}}))
    with pytest.raises(PermissionError):
        engine.process_client(store, client, scope.cases, use_policies=False)
    result = association.associate(scope.root, store, client, actor_email=new["email"], use_policies=False)
    assert result["associated"] and not association.hold(scope.cases / client)
    manifest = association._read(scope.cases / client)
    assert manifest["actor"] == new["email"] and [entry["actor"] for entry in manifest["authorizations"]] == [user["email"], new["email"]]
    engine.process_client(store, client, scope.cases, use_policies=False)


def test_empty_created_case_uses_actual_pipeline_without_invented_document_evidence(firm):
    scope, _, client, _, _ = firm
    assert associate(firm)["associated"]
    case = scope.cases / client
    assert (case / "fact_graph_raw.json").exists()
    assert json.loads((case / "documents.json").read_text())["documents"] == []
    assert (scope.documents / client / "source").is_dir() and not association.hold(case)
    engine.process_client(firm[1], client, scope.cases, use_policies=False)


@pytest.mark.parametrize("revoked", ["account", "acl", "ended"])
def test_current_authority_lifecycle_refuses_before_source_effects(firm, revoked):
    scope, store, client, user, _ = firm
    kwargs = {}
    if revoked == "account":
        user["active"] = False
        (scope.data / "review_users.json").write_text(json.dumps({"users": {user["email"]: user}}))
    elif revoked == "acl":
        kwargs["may_access"] = lambda *_: False
    else:
        (scope.cases / client / "engagement.json").write_text(json.dumps({"end": {"state": "ended"}}))
    with pytest.raises((ValueError, PermissionError)):
        association.associate(scope.root, store, client, actor_email=user["email"], use_policies=False, **kwargs)
    assert not (scope.cases / client / association.FILE).exists()
    assert not (scope.documents / client).exists()


def test_different_bytes_filename_collision_never_overwrites_and_remains_held(firm):
    scope, _, client, _, pages = firm
    row = upload(firm, pages[0])
    source = scope.documents / client / "source"
    source.mkdir(parents=True)
    target = source / row["stored"]
    before = b"%PDF-1.4 distinct fictional original"
    target.write_bytes(before)
    with pytest.raises(ValueError, match="collision"):
        associate(firm)
    assert target.read_bytes() == before and association.hold(scope.cases / client)


def test_initial_unrelated_original_and_later_unknown_origin_never_publish(firm):
    scope, store, client, _, pages = firm
    upload(firm, pages[0])
    source = scope.documents / client / "source"
    source.mkdir(parents=True)
    unknown = source / "unrelated.pdf"
    unknown.write_bytes(document_pdf(["Fictional unrelated original"]))
    with pytest.raises(ValueError, match="unrelated"):
        associate(firm)
    unknown.unlink()  # operator removes only this fictional fixture collision
    associate(firm)
    raw = (scope.cases / client / "fact_graph_raw.json").read_bytes()
    unknown.write_bytes(document_pdf(["Fictional unfinished intake"]))
    with pytest.raises(ValueError, match="verified origin"):
        engine.process_client(store, client, scope.cases, use_policies=False)
    assert (scope.cases / client / "fact_graph_raw.json").read_bytes() == raw and unknown.exists()


@pytest.mark.parametrize("damage", [False, True])
def test_q1_pending_copy_cleanup_preserves_other_case_and_reports_damaged_owner(firm, monkeypatch, damage):
    import purge
    scope, store, client, user, pages = firm
    row = upload(firm, pages[0])
    other = "other-fictional-case"
    (scope.cases / other).mkdir()
    store.add_client(other, "Other Fictional Client")
    other_source = scope.documents / other / "source"
    other_source.mkdir(parents=True)
    other_pdf = other_source / "keep.pdf"
    other_bytes = document_pdf(["Other fictional original"])
    other_pdf.write_bytes(other_bytes)
    (scope.cases / other / "meta.json").write_text(json.dumps({"source_folder": str(other_source)}))
    real_replace = association.os.replace
    with monkeypatch.context() as fault:
        def replace(src, dst):
            real_replace(src, dst)
            if str(dst).endswith(".pdf"):
                raise OSError("synthetic copy before meta publication")
        fault.setattr(association.os, "replace", replace)
        with pytest.raises(OSError):
            associate(firm)
    source = scope.documents / client / "source"
    (source / (row["stored"] + ".association.part")).write_bytes(b"interrupted own copy")
    if damage:
        (scope.cases / client / association.FILE).write_text("{damaged ownership")
    with jobs.case_lock(scope.queue, client):
        result = purge.empty_stores(scope.cases, client, store.root)
    assert other_pdf.read_bytes() == other_bytes and (scope.cases / other).is_dir()
    assert not (scope.cases / client).exists() and not store.client_dir(client).exists()
    if damage:
        assert source.exists() and any("Source setup originals" in row for row in result["left"])
    else:
        assert not source.parent.exists() and not any("Source setup originals" in row for row in result["left"])


@pytest.mark.parametrize("inconsistent", ["destination", "meta"])
def test_ready_wrong_owner_or_meta_is_a_pure_case_hold(firm, inconsistent):
    scope, _, client, _, _ = firm
    associate(firm)
    case = scope.cases / client
    path = case / association.FILE
    meta = case / "meta.json"
    foreign = scope.root.parent / "foreign-firm/source"
    if inconsistent == "destination":
        value = json.loads(path.read_text())
        value["destination"] = str(foreign)
        path.write_text(json.dumps(value))
    else:
        value = json.loads(meta.read_text())
        value["source_folder"] = str(foreign)
        meta.write_text(json.dumps(value))
    before = path.read_bytes(), meta.read_bytes()
    assert association.hold(case) and document_instances.problems(case)
    with pytest.raises(ValueError, match="incomplete"):
        document_instances.context(scope.documents / client / "source", case, [])
    assert (path.read_bytes(), meta.read_bytes()) == before and not foreign.exists()


def test_source_setup_instructions_preserve_independent_ev1_holds(firm):
    import documents
    scope, _, client, _, pages = firm
    upload(firm, pages[0])
    associate(firm)
    case = scope.cases / client
    # Preserve the real record and use a current resolved plan to isolate the
    # setup-implied hold; review evidence remains unchanged outside this fixture.
    saved = documents.read(case)
    for plan in saved["boundary_plans"].values():
        for instance in plan["instances"]:
            instance["state"] = "supported"
    saved["boundary_processing"] = []
    documents.save(case, saved)
    value = association._read(case)
    (case / association.FILE).write_text(json.dumps({**value, "status": "pending"}))
    problems = document_instances.problems(case)
    assert len(problems) == 1 and problems[0].startswith("Source setup")
    assert document_instances.held_aliases(case)
    saved["boundary_processing"] = list(saved["boundary_plans"])
    documents.save(case, saved)
    problems = document_instances.problems(case)
    assert any(p.startswith("Source setup") for p in problems)
    assert any("document processing did not finish" in p for p in problems)
    assert document_instances.held_aliases(case)


def test_unreadable_retained_client_original_does_not_replace_previous_bundle(firm, monkeypatch):
    scope, store, client, _, pages = firm
    upload(firm, pages[0])
    associate(firm)
    case = scope.cases / client
    paths = [case / name for name in ("fact_graph.json", "fact_graph_raw.json", "meta.json", "documents.json", "evidence.json", "reading_flags.json")]
    before = {path: path.read_bytes() for path in paths}
    real = engine._read_upload
    def unreadable(folder, row):
        if not row.get("source"):
            row.update(status="retake", detected="unreadable")
            return [], 0, {}
        return real(folder, row)
    monkeypatch.setattr(engine, "_read_upload", unreadable)
    with pytest.raises(ValueError, match="retained original could not be read"):
        engine.process_client(store, client, scope.cases, use_policies=False)
    assert {path: path.read_bytes() for path in paths} == before


def test_q2_malformed_document_alias_never_releases_source_staff_history(firm, monkeypatch):
    import client_file
    import documents
    scope, store, client, _, pages = firm
    original = upload(firm, pages[0])
    associate(firm)
    case = scope.cases / client
    part = case / (association.FILE + "." + "a" * 16 + ".tmp")
    part.write_text("fictional interrupted staff handover")
    saved = documents.read(case)
    saved["documents"].append({"files": [association.FILE, part.name]})
    documents.save(case, saved)
    selected, excluded, _ = client_file.gather(case, store.root)
    assert not any(item.source and item.source.name in {association.FILE, part.name} for item in selected)
    assert any(row["path"].endswith(association.FILE) and "Internal case, authority or security record" in row["why"] for row in excluded)
    assert any(item.source and item.source.name == original["stored"] for item in selected)
    # The exporter normally skips temps. If a malformed upstream candidate
    # nevertheless registers this exact owned temp, the client boundary refuses.
    import export_firm
    real = export_firm.gather
    def injected(args):
        entries, skipped, warnings, roots = real(args)
        return entries + [export_firm.Entry(f"clients/{client}/{part.name}", "Erroneous registered candidate", source=part)], skipped, warnings, roots
    monkeypatch.setattr(export_firm, "gather", injected)
    selected, excluded, _ = client_file.gather(case, store.root)
    assert not any(item.source == part for item in selected)
    assert any(row["path"].endswith(part.name) and "Internal case, authority or security record" in row["why"] for row in excluded)
    assert any(item.source and item.source.name == original["stored"] for item in selected)
