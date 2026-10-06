"""EV1 acceptance: invented identities only, no external readers or services."""
import io
import json
from pathlib import Path

import pytest

import document_instances as instances
from batch import process_client_folder, process_documents
from classify import split_documents
from factgraph import FactGraph
from review.state import save_bundle


I94 = json.loads((Path(__file__).parent / "fixtures" / "document_instances.json").read_text(encoding="utf-8"))["same_type_i94"]


def pdf(pages):
    from portal.demo import document_pdf
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for page in pages:
        writer.add_page(PdfReader(io.BytesIO(document_pdf(page.splitlines()))).pages[0])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture
def case(tmp_path, monkeypatch):
    # Incremental processing's unrelated indexes/fill are covered by their own
    # regressions; acceptance here executes actual recognition/persistence.
    import index
    import query
    import review.overview
    import review.state
    monkeypatch.setattr(index, "rebuild", lambda *a, **k: {})
    monkeypatch.setattr(query, "rebuild", lambda *a, **k: {})
    monkeypatch.setattr(review.overview, "journey_row", lambda *a, **k: {})
    monkeypatch.setattr(review.state, "refill", lambda *a, **k: {"counts": {}})
    source, out = tmp_path / "source", tmp_path / "clients" / "synthetic"
    source.mkdir()
    out.mkdir(parents=True)
    return source, out


def run(case, pages):
    source, out = case
    (source / "scan.pdf").write_bytes(pdf(pages))
    result = process_client_folder(out.name, source, case_dir=out)
    save_bundle(result, out, source)
    return result


def test_frozen_mixed_i94_reproduction_now_has_distinct_sources_and_conflicts():
    result = process_documents("synthetic", [("scan.pdf", "\n".join(I94))], pages={"scan.pdf": I94})
    assert split_documents(I94) == [(0, 0, "i94"), (1, 1, "i94")]
    first, second = result.boundary_plans["scan.pdf"]["instances"]
    assert first["instance_id"] != second["instance_id"]
    assert result.graph.get("applicant.given_name").status == "conflict"
    assert result.graph.get("applicant.i94_number").status == "conflict"
    dob = result.graph.get("applicant.dob")
    assert [s.doc_id for s in dob.sources] == ["scan.pdf#p2"]
    assert not any(s.doc_id == "scan.pdf#p1" for s in dob.sources)


@pytest.mark.parametrize("decoration", [("Page 1 of 2", "Page 2 of 2"), ("", "continued")])
def test_new_identifier_overrides_apparent_continuation(decoration):
    pages = [I94[n] + decoration[n] for n in range(2)]
    assert split_documents(pages) == [(0, 0, "i94"), (1, 1, "i94")]
    # The incomplete first record may also lack its number, and the actual
    # CBP label encloses I-94 in parentheses. A weak 'continued' cannot merge it.
    first = I94[0].replace("Admission I-94 Record Number: 11111111111\n", "")
    second = I94[1].replace("Most Recent I-94", "continued").replace("Admission I-94", "Admission (I-94)")
    assert len(split_documents([first, second])) == 2


def test_same_person_different_records_and_same_name_relatives_still_split():
    second = I94[1].replace("OTHER", "EXAMPLE").replace("BETA", "ALPHA")
    assert len(split_documents([I94[0], second])) == 2
    passports = [f"P<BRASILVA<<SYNTHETIC<<<<<<<<<<<<\nPASSAPORTE PASSPORT\nPassport Number: XX000000{n}" for n in (1, 2)]
    assert len(split_documents(passports)) == 2


def test_genuine_multipage_form_and_translation_stay_associated():
    pages = [f"Part {n}. Information About You\nForm I-485 Edition 09/18/26 Page {n} of 2" for n in (1, 2)]
    assert instances.analyze(pages) == [{"first": 0, "last": 1, "type": "i485", "state": "supported", "reasons": []}]
    translated = instances.analyze(["CERTIDAO DE NASCIMENTO\nFILIACAO", "Certification of translation\nI certify this translation."])
    assert len(translated) == 1 and translated[0]["state"] == "unresolved"  # phrase associates, does not prove relation
    continued = instances.analyze([I94[0] + "Page 1 of 2", I94[0] + "Page 2 of 2"])
    assert len(continued) == 1 and continued[0]["state"] == "supported"
    # Coincidentally aligned page numbers cannot merge different known kinds.
    different = instances.analyze([I94[0] + "Page 1 of 2", "CERTIDAO DE NASCIMENTO\nFILIACAO\nPage 2 of 2"])
    assert len(different) == 2


def test_unknown_continuation_holds_all_affected_values():
    for next_page in ("Additional unidentified page", "continuation", "Certification of translation"):
        pages = [I94[0], next_page + "\nBirth Date: 05/06/2001"]
        result = process_documents("synthetic", [("scan.pdf", "\n".join(pages))], pages={"scan.pdf": pages})
        assert result.boundary_plans["scan.pdf"]["instances"][0]["state"] == "unresolved"
        assert result.graph.get("applicant.given_name") is None
        assert result.graph.get("applicant.dob") is None
        assert any(f.kind == "document_boundary" for f in result.review_flags)
    part = instances.analyze(["Form I-485 Edition 09/18/26 Page 1 of 24", "Part 2. Additional information\nBirth Date: 05/06/2001"])
    assert len(part) == 1 and part[0]["state"] == "unresolved"


@pytest.mark.parametrize("flattened", [False, True])
def test_multiple_records_on_single_physical_page_cannot_be_confirmed(case, flattened):
    text = "\n".join(I94)
    run(case, [text.replace("\n", " ") if flattened else text])
    source, out = case
    plan = instances.views(out)[0]
    assert plan["held"]
    with pytest.raises(ValueError, match="physical page"):
        instances.resolve(out, "scan.pdf", "1", plan["fingerprint"], "Synthetic Reviewer", "paralegal")
    # A repeated printed identifier is still one record, including OCR that
    # repeats a header inline. A normal one-identifier page remains supported.
    for same_record in (I94[0], I94[0] + " Admission (I-94) Record Number: 11111111111"):
        assert instances.analyze([same_record])[0]["state"] == "supported"


def test_named_split_confirmation_undo_reprocess_and_stable_identity(case):
    pages = [I94[0], "Additional unidentified page\nBirth Date: 05/06/2001"]
    run(case, pages)
    source, out = case
    plan = instances.views(out)[0]
    assert plan["held"]
    with pytest.raises(ValueError, match="name"):
        instances.resolve(out, "scan.pdf", "1", plan["fingerprint"], "", "paralegal")
    with pytest.raises(ValueError, match="Refresh"):
        instances.resolve(out, "scan.pdf", "1", "old", "Synthetic Reviewer", "paralegal")
    instances.resolve(out, "scan.pdf", "1,2", plan["fingerprint"], "Synthetic Reviewer", "paralegal")
    reviewed = instances.views(out)[0]
    assert not reviewed["held"]
    assert reviewed["reviewed_by"]["who"] == "Synthetic Reviewer"
    identities = [p["instance_id"] for p in reviewed["instances"]]
    save_bundle(process_client_folder(out.name, source, case_dir=out), out, source)
    assert [p["instance_id"] for p in instances.views(out)[0]["instances"]] == identities
    instances.resolve(out, "scan.pdf", "1", plan["fingerprint"], "Synthetic Reviewer", "paralegal", undo=True)
    assert instances.views(out)[0]["held"]
    log = json.loads((out / "documents.json").read_text())["boundary_decisions"]["scan.pdf"]
    assert len(log["history"]) == 2 and log["undone"]
    assert FactGraph.load(out / "fact_graph_raw.json").get("applicant.given_name") is None


def test_refining_uncertain_range_preserves_unchanged_sibling_identity_and_approval(case):
    from review.state import load_decisions, record_decision
    ssn = "YOUR SOCIAL SECURITY CARD\n123-45-6789\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION"
    run(case, [I94[0], "Unidentified next page", ssn])
    out = case[1]
    plan = instances.views(out)[0]
    sibling = plan["instances"][-1]
    # EV2 is a separate prerequisite: this test exercises preservation of an
    # already accepted sibling, not acceptance from its boundary alone.
    import documents
    import subject_attribution
    subject = next(p["id"] for p in documents.read(out)["case_subjects"]["people"] if p["case_role"] == "applicant")
    row = next(r for r in subject_attribution.views(out) if r["instance_id"] == sibling["instance_id"])
    subject_attribution.assign(out, row["instance_id"], row["fingerprint"], {"holder": subject}, "Synthetic Reviewer", "paralegal")
    import critical_review
    source_proof = critical_review.context(out)["applicant.ssn"]["fingerprint"]
    record_decision(out, {"id": "fact:applicant.ssn", "kind": "fact", "level": "review", "title": "SSN", "group": "check",
                          "facts": [{"key": "applicant.ssn", "input": {}}], "actions": ["confirm"]},
                    {"action": "confirm", "reviewer": "Synthetic Reviewer", "role": "paralegal",
                     "evidence_fingerprints": {"applicant.ssn": source_proof}})
    instances.resolve(out, "scan.pdf", "1,2,3", plan["fingerprint"], "Synthetic Reviewer", "paralegal")
    fresh = instances.views(out)[0]["instances"][-1]
    assert sibling["instance_id"] == fresh["instance_id"] and sibling["evidence_fingerprint"] == fresh["evidence_fingerprint"]
    assert "fact:applicant.ssn" in load_decisions(out)


def test_hold_visible_in_review_and_packet_even_with_zero_open_counts(case, monkeypatch):
    import packet
    import review.state
    from fill import load_field_map
    import schema_path
    run(case, [I94[0], "Unidentified next page"])
    out = case[1]
    field_map = load_field_map(schema_path.path("field_map", "i485"))
    template = schema_path.path("template", "i485")
    _, flags = review.state.current_flags(out, field_map, template)
    assert any(f.kind == "document_boundary" and f.level == "blocking" for f in flags)
    p = packet.plan(out, {"summary": {}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0})
    assert not p["ready"] and any("document boundaries" in message for message in p["problems"])


def test_original_replacement_suppresses_old_facts_and_approval_before_reread(case):
    from review.state import load_decisions, reviewed_graph
    run(case, [I94[0]])
    source, out = case
    decision = {"action": "confirm", "at": "2026-10-04T00:00:00Z", "reviewer": "Synthetic Reviewer",
                "item": {"facts": ["applicant.given_name"]}}
    (out / "decisions.json").write_text(json.dumps({"name": decision}))
    (source / "scan.pdf").write_bytes(pdf([I94[1]]))
    assert instances.views(out)[0]["held"] and instances.views(out)[0]["stale"]
    assert load_decisions(out) == {}
    assert reviewed_graph(out).get("applicant.given_name") is None
    save_bundle(process_client_folder(out.name, source, case_dir=out), out, source)
    assert json.loads((out / "decisions.json").read_text())["name"]["undone"]["reason"]


def test_incremental_notice_path_reads_real_pages_and_cannot_bypass_hold(case):
    from inbox import reprocess
    run(case, [I94[0]])
    source, out = case
    pages = [I94[1], "Unidentified next page"]
    (source / "new.pdf").write_bytes(pdf(pages))
    reprocess(out, source, "new.pdf", "\n".join(pages), 2)
    assert next(p for p in instances.views(out) if p["file"] == "new.pdf")["held"]
    graph = FactGraph.load(out / "fact_graph_raw.json")
    assert graph.get("applicant.given_name").value == "ALPHA"


def test_incremental_record_build_failure_leaves_durable_hold(case, monkeypatch):
    import documents
    from inbox import reprocess
    run(case, [I94[0]])
    source, out = case
    (source / "new.pdf").write_bytes(pdf([I94[1]]))
    def fail(*args, **kwargs):
        raise RuntimeError("injected records build failure")
    monkeypatch.setattr(documents, "build", fail)
    with pytest.raises(RuntimeError, match="evidence records"):
        reprocess(out, source, "new.pdf", I94[1], 1)
    assert any("did not finish" in message for message in instances.problems(out))
    graph = FactGraph.load(out / "fact_graph_raw.json")
    assert graph.get("applicant.given_name").value == "ALPHA"


def test_bundle_records_save_failure_leaves_durable_hold(case, monkeypatch):
    import documents
    from review.state import reviewed_graph
    run(case, [I94[0]])
    source, out = case
    result = process_client_folder(out.name, source, case_dir=out)
    def fail(*args, **kwargs):
        raise RuntimeError("injected records save failure")
    monkeypatch.setattr(documents, "save_run", fail)
    with pytest.raises(RuntimeError, match="injected"):
        save_bundle(result, out, source)
    assert any("did not finish" in message for message in instances.problems(out))
    assert reviewed_graph(out).get("applicant.given_name") is None


def test_portal_ingestion_uses_same_hold_and_reviewed_layout(case):
    from portal.store import PortalStore
    from portal.engine import process_client
    source, out = case
    store = PortalStore(source.parent / "portal")
    store.add_client(out.name, "Synthetic Client", language="en")
    _upload = store.add_upload(out.name, "i94", "scan.pdf", pdf([I94[0], "Unidentified next page"]), "application/pdf")
    process_client(store, out.name, out_root=out.parent, use_policies=False)
    plan = instances.views(out)[0]
    assert plan["held"] and FactGraph.load(out / "fact_graph_raw.json").get("applicant.given_name") is None
    instances.resolve(out, plan["file"], "1,2", plan["fingerprint"], "Synthetic Reviewer", "paralegal")
    process_client(store, out.name, out_root=out.parent, use_policies=False)
    assert not instances.views(out)[0]["held"]
    assert instances.views(out)[0]["reviewed_by"]["who"] == "Synthetic Reviewer"


def test_folder_record_build_failure_keeps_hold_in_existing_case(case, monkeypatch):
    import documents
    run(case, [I94[0]])
    source, out = case
    def fail(*args, **kwargs):
        raise RuntimeError("injected")
    monkeypatch.setattr(documents, "build", fail)
    with pytest.raises(RuntimeError, match="evidence records"):
        process_client_folder(out.name, source, case_dir=out)
    assert any("did not finish" in message for message in instances.problems(out))


@pytest.mark.parametrize("store", [None, {"version": 1, "documents": []}])
def test_metadata_poor_legacy_graph_holds_ocr_but_preserves_human_inputs(case, store):
    from review.state import reviewed_graph
    source, out = case
    graph = FactGraph(out.name)
    graph.add_source("applicant.given_name", "lost.pdf", "i94", "ALPHA", "ALPHA", .9)
    graph.add_source("applicant.dob", "portal questionnaire", "intake_questionnaire", "2001-05-06", "2001-05-06", 1, tier=3)
    graph.add_source("attorney.name", "firm_profile.json", "firm_profile", "SYNTHETIC", "SYNTHETIC", 1)
    graph.save(out / "fact_graph.json")
    if store is not None:
        (out / "documents.json").write_text(json.dumps(store))
    assert instances.problems(out)
    reviewed = reviewed_graph(out)
    assert reviewed.get("applicant.given_name") is None
    assert reviewed.get("applicant.dob").value == "2001-05-06"
    assert reviewed.get("attorney.name").value == "SYNTHETIC"


def test_absence_report_survives_unverifiable_ocr_without_accepting_its_value(case):
    import absence
    import packet
    from review.state import load_decisions, load_decision_log, record_decision, reviewed_graph

    source, out = case
    graph = FactGraph(out.name)
    graph.add_source("applicant.travel_document_number", "lost.pdf", "passport", "XX0000001", "XX0000001", .9)
    graph.save(out / "fact_graph.json")
    (out / "meta.json").write_text(json.dumps({"source_folder": str(source)}))
    # An ordinary acknowledgement touching the held value remains ineffective.
    item = {"id": "fact:passport", "kind": "fact", "level": "review", "group": "check", "title": "Passport",
            "facts": [{"key": "applicant.travel_document_number"}], "actions": ["acknowledge"]}
    record_decision(out, item, {"action": "acknowledge", "reviewer": "Synthetic Reviewer"})
    mark = absence.mark(out, "passport", "lost", "Office reports the original is missing", "Synthetic Reviewer", "paralegal")
    assert mark["by"] == "Synthetic Reviewer" and mark["reason"] == "lost"
    assert "absent:passport" in load_decisions(out) and "fact:passport" not in load_decisions(out)
    reviewed = reviewed_graph(out)
    assert reviewed.get("applicant.travel_document_number") is None
    assert reviewed.get("case.absent.passport").value == "lost"
    planned = packet.plan(out, {"summary": {}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0})
    assert not planned["ready"] and any("document boundaries" in p for p in planned["problems"])
    # The same distinction applies to durable evidence invalidation, not just
    # the read-time filter. Arrival still lifts absence through its own rule.
    _, _, incoming, _ = instances.prepare([("lost.pdf", "PASSAPORTE PASSPORT\nPassport Number: XX0000001")], None)
    instances.invalidate(out, incoming)
    log = load_decision_log(out)
    assert not log["absent:passport"].get("undone") and log["fact:passport"].get("undone")


@pytest.mark.parametrize("failure", ["process", "save"])
def test_batch_failure_preserves_existing_graph_reports_hold_and_continues(tmp_path, monkeypatch, failure):
    import batch
    import documents
    sources, outroot = tmp_path / "sources", tmp_path / "out"
    for name in ("a-bad", "z-good"):
        folder = sources / name
        folder.mkdir(parents=True)
        (folder / "scan.pdf").write_bytes(pdf([I94[0]]))
        save_bundle(process_client_folder(name, folder), outroot / name, folder)
    old_graph = (outroot / "a-bad" / "fact_graph.json").read_bytes()
    (sources / "a-bad" / "scan.pdf").write_bytes(pdf([I94[1]]))
    if failure == "process":
        real = batch.process_client_folder
        def flaky(name, folder, **kwargs):
            if name == "a-bad":
                raise RuntimeError("injected before staging")
            return real(name, folder, **kwargs)
        monkeypatch.setattr(batch, "process_client_folder", flaky)
    else:
        real = documents.save_run
        def flaky(out, built):
            if out.name == "a-bad":
                raise RuntimeError("injected bundle save")
            return real(out, built)
        monkeypatch.setattr(documents, "save_run", flaky)
    result = batch.run_batch(sources, outroot)
    assert result["a-bad"].errors and not result["z-good"].errors
    assert (outroot / "a-bad" / "fact_graph.json").read_bytes() == old_graph
    assert instances.problems(outroot / "a-bad")
    assert json.loads((outroot / "a-bad" / "meta.json").read_text())["errors"]

def test_new_original_without_completed_reading_is_pending_not_changed(case):
    source, out = case
    (source / "new-photo.pdf").write_bytes(pdf(["Fictional new photo"]))
    FactGraph(out.name).save(out / "fact_graph.json")
    (out / "meta.json").write_text(json.dumps({"source_folder": str(source)}))
    view = next(row for row in instances.views(out) if row["file"] == "new-photo.pdf")
    assert view["held"] and view["pending_read"] and view["processing_incomplete"]
    assert not view["stale"]
    assert view["fingerprint"] == "unrecorded"
