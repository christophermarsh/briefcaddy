"""EV3 fictional backend contract; no model or real case data."""
import json

import pytest

import critical_review as critical
import reader_manifest
import subject_attribution as subjects
import documents
from factgraph import FactGraph
from review.state import save_bundle, record_decision, load_decisions, load_decision_log, reviewed_graph, undo_decision
from synthetic_documents import process_retained_documents
from validate.validate import validate_graph

TEXT = "Most Recent I-94\nAdmission (I-94) Record Number: 11111111111\nLast/Surname: SAMPLE\nFirst (Given) Name: ALPHA\nBirth Date: 01/02/2000"
KEY = "applicant.i94_number"


def saved(tmp_path, *, assign=True):
    case, source = tmp_path / "clients" / "fictional", tmp_path / "source"
    result = process_retained_documents(case.name, source, [("i94.pdf", TEXT)])
    save_bundle(result, case, source)
    if assign:
        row = subjects.views(case)[0]
        person = documents.read(case)["case_subjects"]["people"][0]["id"]
        subjects.assign(case, row["instance_id"], row["fingerprint"], {"holder": person}, "Reviewer", "paralegal")
    return case


def item(key=KEY):
    return {"id": "fact:" + key, "kind": "fact", "level": "review", "title": "Check source", "group": "other",
            "facts": [{"key": key, "input": {"type": "text"}}], "actions": ["confirm", "set", "blank"]}


def decision(case, action="confirm", key=KEY, value=None, **extra):
    current = critical.context(case)
    return {"action": action, "reviewer": "Reviewer", "role": "paralegal", "values": {key: value} if action == "set" else {},
            "evidence_fingerprints": {key: current[key]["fingerprint"]} if key in current else {}, **extra}


def test_tier1_needs_named_source_review_and_packet_ignores_empty_queue(tmp_path):
    import packet
    case = saved(tmp_path)
    graph = reviewed_graph(case)
    assert graph.get(KEY).tier == 1 and graph.get(KEY).status == "resolved"
    assert any(f.fact_key == KEY and "critical document read" in f.message for f in validate_graph(graph, []))
    assert critical.problems(case)
    assert not packet.plan(case, {"summary": {}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0})["ready"]
    assert not (case / "decisions.json").exists()
    stored = record_decision(case, item(), decision(case))
    assert stored["evidence_confirmation"]["basis"] == "manual_retained_source_review"
    assert stored["evidence_confirmation"]["model_release_approval"] is False
    assert item()["id"] in load_decisions(case)
    assert KEY not in [f.fact_key for f in critical.flags(reviewed_graph(case))]
    undo_decision(case, item()["id"], "Reviewer", "paralegal")
    assert KEY in [f.fact_key for f in critical.flags(reviewed_graph(case))]


def test_valid_new_source_still_explains_unreviewed_sibling_prerequisite(tmp_path):
    from review.state import source_prerequisites, build_items
    from review.server import ReviewApp
    import schema_path
    case, source = tmp_path / "clients" / "fictional", tmp_path / "source"
    result = process_retained_documents(case.name, source, [("old.pdf", TEXT), ("new.pdf", TEXT.replace("11111111111", "22222222222"))])
    save_bundle(result, case, source)
    row = next(r for r in subjects.views(case) if r["file"] == "new.pdf")
    person = documents.read(case)["case_subjects"]["people"][0]["id"]
    subjects.assign(case, row["instance_id"], row["fingerprint"], {"holder": person}, "Reviewer", "paralegal")
    assert critical.context(case)[KEY]["bound"]
    assert KEY in subjects.affected_keys(case)
    reasons = source_prerequisites(case)[KEY]
    assert {r["file"] for r in reasons} == {"old.pdf"}
    with pytest.raises(ValueError, match="boundaries and whose facts"):
        record_decision(case, item(), decision(case))
    assert not (case / "decisions.json").exists()
    app = ReviewApp(case.parent, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None)
    built = build_items(case, app.field_map, app.template, app.catalog, pending=False)
    card = next(c for c in built["cards"] if any(f["key"] == KEY for f in c["facts"]))
    assert any(r["key"] == KEY and r["file"] == "old.pdf" for r in card["source_prerequisites"])
    # Explicitly retain the old record as another person's/reference evidence;
    # this does not waive its original or silently assign it to this client.
    old = next(r for r in subjects.views(case) if r["file"] == "old.pdf")
    subjects.assign(case, old["instance_id"], old["fingerprint"], {}, "Reviewer", "paralegal",
                    reference_only=True, note="Fictional prior household record, not this applicant")
    assert KEY not in source_prerequisites(case)
    record_decision(case, item(), decision(case))
    assert item()["id"] in load_decisions(case)


def test_subject_prerequisite_survives_effective_graph_filtering(tmp_path):
    from review.state import source_prerequisites
    case = saved(tmp_path, assign=False)
    # A processing run can already have removed unassigned document sources
    # from its effective graph. The screen must still explain the same hold
    # enforced by Confirm, using retained raw evidence.
    FactGraph(case.name).save(case / "fact_graph.json")
    assert source_prerequisites(case)[KEY][0]["kind"] == "subject"
    assert source_prerequisites(case)[KEY][0]["file"] == "i94.pdf"
    with pytest.raises(ValueError, match="boundaries and whose facts"):
        record_decision(case, item(), decision(case))


def test_document_disagreement_appears_in_needs_attention_after_person_review(tmp_path):
    from review.server import ReviewApp
    import schema_path
    case, source = tmp_path / "clients" / "fictional", tmp_path / "source"
    changed = TEXT.replace("01/02/2000", "01/03/2000")
    save_bundle(process_retained_documents(case.name, source, [("first.pdf", TEXT), ("second.pdf", changed)]), case, source)
    person = documents.read(case)["case_subjects"]["people"][0]["id"]
    for row in subjects.views(case):
        subjects.assign(case, row["instance_id"], row["fingerprint"], {"holder": person}, "Reviewer", "paralegal")
    app = ReviewApp(case.parent, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None)
    card = next(c for c in app.items(case.name)["cards"] if any(f["key"] == "applicant.dob" for f in c["facts"]))
    assert card["tab"] == "fix"
    assert any(f["status"] == "conflict" for f in card["facts"])
    assert not card.get("source_prerequisites")


@pytest.mark.parametrize("action,value", [("set", "22222222222"), ("blank", None)])
def test_correction_and_blank_bind_original_and_selected_value(tmp_path, action, value):
    case = saved(tmp_path)
    stored = record_decision(case, item(), decision(case, action, value=value))
    proof = stored["evidence_confirmation"]["keys"][KEY]
    assert proof["proof"]["observed_value"] == "11111111111" and proof["chosen_value"] == value
    assert reviewed_graph(case).get(KEY).value == value
    assert item()["id"] in load_decisions(case)
    logs = load_decision_log(case)
    logs[item()["id"]]["values"][KEY] = "33333333333"
    if action == "blank":
        logs[item()["id"]]["action"] = "set"
    (case / "decisions.json").write_text(json.dumps(logs), encoding="utf-8")
    assert item()["id"] not in load_decisions(case)


def test_stale_page_and_forged_or_legacy_proof_never_accept(tmp_path):
    case = saved(tmp_path)
    old = decision(case)
    with pytest.raises(ValueError, match="changed or was not opened"):
        record_decision(case, item(), old | {"evidence_fingerprints": {}})
    with pytest.raises(ValueError, match="signed-in"):
        record_decision(case, item(), old | {"role": "client"})
    assert not (case / "decisions.json").exists()
    record_decision(case, item(), old)
    logs = load_decision_log(case)
    del logs[item()["id"]]["evidence_confirmation"]
    (case / "decisions.json").write_text(json.dumps(logs), encoding="utf-8")
    assert item()["id"] not in load_decisions(case)


def test_known_config_change_reopens_without_rewriting_read_time_manifest(tmp_path, monkeypatch):
    case = saved(tmp_path)
    before = FactGraph.load(case / "fact_graph_raw.json").get(KEY).sources[0].read_manifest
    record_decision(case, item(), decision(case))
    monkeypatch.setenv("READER_RELEASE_REVISION", "candidate-2")
    assert item()["id"] not in load_decisions(case)
    assert FactGraph.load(case / "fact_graph_raw.json").get(KEY).sources[0].read_manifest == before
    record_decision(case, item(), decision(case))
    proof = load_decisions(case)[item()["id"]]["evidence_confirmation"]["keys"][KEY]["proof"]["evidence"][0]
    assert proof["source"]["read_manifest"] == before
    assert proof["expected_manifest"]["declared_reader_release_revision"] == "candidate-2"
    assert proof["expected_manifest"]["upstream"]["model_weight_sha256"] is None


def test_subject_change_and_boundary_hold_cannot_be_cleared_by_field_action(tmp_path):
    case = saved(tmp_path, assign=False)
    with pytest.raises(ValueError, match="boundaries and whose"):
        record_decision(case, item(), decision(case))
    row = subjects.views(case)[0]
    person = documents.read(case)["case_subjects"]["people"][0]
    subjects.assign(case, row["instance_id"], row["fingerprint"], {"holder": person["id"]}, "Reviewer", "paralegal")
    record_decision(case, item(), decision(case))
    subjects.rename_person(case, person["id"], "Corrected label", "Reviewer", "paralegal")
    assert item()["id"] not in load_decisions(case)
    with pytest.raises(ValueError, match="boundaries and whose"):
        record_decision(case, item(), decision(case))


def test_read_manifest_legacy_is_explicit_and_never_backfilled(tmp_path):
    case = saved(tmp_path)
    for name in ("fact_graph.json", "fact_graph_raw.json"):
        graph = FactGraph.load(case / name)
        for fact in graph.all_facts().values():
            for source in fact.sources:
                source.read_manifest = None
        graph.save(case / name)
    assert "not recorded" in " ".join(critical.context(case)[KEY]["reasons"])
    record_decision(case, item(), decision(case))
    proof = load_decisions(case)[item()["id"]]["evidence_confirmation"]["keys"][KEY]["proof"]["evidence"][0]
    assert proof["source"]["read_manifest"] is None and proof["expected_manifest"]


def test_typed_only_answer_keeps_existing_flow(tmp_path):
    case = tmp_path / "typed"
    case.mkdir()
    graph = FactGraph(case.name)
    graph.add_source(KEY, "portal questionnaire", "intake_questionnaire", "11111111111", "11111111111", 1, tier=3)
    graph.save(case / "fact_graph.json")
    assert not critical.context(case)
    stored = record_decision(case, item(), {"action": "confirm", "reviewer": "Reviewer"})
    assert "evidence_confirmation" not in stored and item()["id"] in load_decisions(case)


def test_inventory_export_includes_expected_observed_and_unknown_fallback():
    from evaluation.critical_inventory import materialize
    from evaluation.corpus import digest
    spec = critical.inventory()
    corpus = {"cases": [{"expected": [{"key": KEY}, {"key": "nta.hearing_date"}]}]}
    observed = {"facts": [{"key": "new_reader.unlisted_filing_answer"}]}
    exported = materialize(spec, corpus, observed, accepted_by="Synthetic engineering reviewer", acceptance_evidence="test only")
    assert exported["keys"] == sorted([KEY, "nta.hearing_date", "new_reader.unlisted_filing_answer"])
    assert exported["inventory_source_digest"] == digest(spec)
    assert exported["model_release_approval"] is False
    challenger = {"facts": [{"key": "new_reader.challenger_only_wrong_answer"}]}
    shared = materialize(spec, corpus, [observed, challenger], accepted_by="Reviewer", acceptance_evidence="synthetic test")
    reverse = materialize(spec, corpus, [challenger, observed], accepted_by="Reviewer", acceptance_evidence="synthetic test")
    assert shared == reverse
    assert set(shared["keys"]) >= {"new_reader.challenger_only_wrong_answer", "new_reader.unlisted_filing_answer"}
    assert shared["materialized_observation_digests"] == sorted([digest(observed), digest(challenger)])


def test_real_document_quality_and_reader_helper_changes_reopen(tmp_path, monkeypatch):
    case = saved(tmp_path)
    record_decision(case, item(), decision(case))
    doc = documents.read(case)["documents"][0]
    assert doc["id"] != "i94.pdf"
    documents.set_quality(case, doc["id"], "blurry", "Reviewer", "paralegal")
    assert item()["id"] not in load_decisions(case)
    assert "blurry" in " ".join(critical.context(case)[KEY]["reasons"])
    record_decision(case, item(), decision(case))
    original = reader_manifest._hash
    monkeypatch.setattr(reader_manifest, "_hash", lambda p: "a" * 64 if p.name == "barcode.py" else original(p))
    assert item()["id"] not in load_decisions(case)
    manifest = reader_manifest.current("i213")
    assert {"src/extract/arrival.py", "src/extract/eoir_notice.py", "src/classify/barcode.py"} <= set(manifest["source_files"])


@pytest.mark.parametrize("bad", [[], "bad", {"version": 1, "keys": []}, {"version": 1, "keys": {KEY: []}}])
def test_malformed_confirmation_record_fails_closed(tmp_path, bad):
    case = saved(tmp_path)
    record_decision(case, item(), decision(case))
    logs = load_decision_log(case)
    logs[item()["id"]]["evidence_confirmation"] = bad
    (case / "decisions.json").write_text(json.dumps(logs), encoding="utf-8")
    assert item()["id"] not in load_decisions(case)
    assert critical.problems(case)


def test_corrected_input_then_derived_confirmation_records_actual_value(tmp_path, monkeypatch):
    import batch
    derived = "example.derived_identifier"
    original = batch.derive
    def derive(graph, rules, policies):
        original(graph, rules, policies)
        fact = graph.get(KEY)
        if fact and fact.status == "resolved":
            graph.add_derived(derived, fact.value + "-COPY", "SYNTHETIC-COPY", [KEY])
    monkeypatch.setattr(batch, "derive", derive)
    case = saved(tmp_path)
    record_decision(case, item(), decision(case, "set", value="22222222222"))
    assert reviewed_graph(case).get(derived).value == "22222222222-COPY"
    stored = record_decision(case, item(derived), decision(case, key=derived))
    proof = stored["evidence_confirmation"]["keys"][derived]
    assert proof["chosen_value"] == reviewed_graph(case).get(derived).value == "22222222222-COPY"
    assert proof["proof"]["dependency_decisions"]
    assert item(derived)["id"] in load_decisions(case)
    undo_decision(case, item()["id"], "Reviewer", "paralegal")
    assert item(derived)["id"] not in load_decisions(case)
    assert reviewed_graph(case).get(derived).value == "11111111111-COPY"


def test_grouped_input_and_derived_set_does_not_depend_on_its_own_metadata(tmp_path, monkeypatch):
    import batch
    derived = "example.derived_identifier"
    original = batch.derive
    def derive(graph, rules, policies):
        original(graph, rules, policies)
        if graph.get(KEY):
            graph.add_derived(derived, graph.get(KEY).value + "-COPY", "SYNTHETIC-COPY", [KEY])
    monkeypatch.setattr(batch, "derive", derive)
    case = saved(tmp_path)
    combined = item()
    combined["facts"] += item(derived)["facts"]
    current = critical.context(case)
    body = decision(case, "set", value="22222222222")
    body["values"][derived] = "22222222222-COPY"
    body["evidence_fingerprints"][derived] = current[derived]["fingerprint"]
    record_decision(case, combined, body)
    assert item()["id"] in load_decisions(case)
    assert reviewed_graph(case).get(derived).value == "22222222222-COPY"


def test_malformed_i94_and_date_keep_raw_but_do_not_propose_guessed_values(tmp_path):
    from extract.i94 import extract, number_issue
    fields = {f.fact_key: f for f in extract(TEXT.replace("11111111111", "1111111111O").replace("01/02/2000", "02/30/2000"))}
    assert fields[KEY].raw_value == "1111111111O" and fields[KEY].normalized_value is None
    assert fields[KEY].reading_issues and fields["applicant.dob"].reading_issues
    assert fields["applicant.dob"].normalized_value is None
    assert number_issue("123456789A1") is None and number_issue("12345678901") is None
    assert number_issue("12345678A11")
    case = saved(tmp_path)
    with pytest.raises(ValueError, match="I-94 number"):
        record_decision(case, item(), decision(case, "set", value="123X"))
    assert not (case / "decisions.json").exists()


def test_clear_checked_mrz_still_requires_manual_source_review():
    from batch import process_documents
    from extract.passport import read_mrz
    mrz = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nL898902C36UTO7408122F1204159ZE184226B<<<<<10"
    assert read_mrz(mrz)
    result = process_documents("fictional", [("passport.pdf", "PASSPORT\n" + mrz)])
    assert any(f.fact_key.startswith("folder.passport.") for f in critical.flags(result.graph))


def test_pending_notice_continues_after_subject_review_until_source_confirmation(tmp_path):
    import journey
    from test_inbox import RFE, confirm_notice_subjects
    case, source = tmp_path / "clients" / "fictional", tmp_path / "source"
    result = process_retained_documents(case.name, source, [("inbox_rfe.pdf", "\n".join(RFE))])
    save_bundle(result, case, source)
    derived = result.graph.get("folder.i485_filed_before")
    assert derived.sources[0].from_facts and derived.sources[0].input_evidence
    first = journey.journey(case)
    assert first["pending_evidence"][0]["state"] == "subject_unconfirmed"
    confirm_notice_subjects(case)
    second = journey.journey(case)
    assert second["pending_evidence"][0]["state"] == "source_unconfirmed"
    assert not any(d["date"] == "2026-12-28" for d in second["deadlines"])
    assert not any(e["date"] == "2026-09-30" for e in journey.client_view(second, "en")["happened"])
    assert not journey.notices(reviewed_graph(case))
    for key in list(critical.context(case)):
        record_decision(case, item(key), decision(case, key=key))
    final = journey.journey(case)
    assert not final["pending_evidence"]
    # Later confirming an unchanged parent does not reopen its already checked
    # derived value; both confirmations are independently required.
    derived_proof = load_decisions(case)["fact:folder.i485_filed_before"]["evidence_confirmation"]["keys"]["folder.i485_filed_before"]
    assert derived_proof["proof"]["dependency_decisions"] == {}
    assert any(d["date"] == "2026-12-28" for d in final["deadlines"])
    due = next(k for k in critical.context(case) if k.startswith("folder.notice.") and k.endswith(".due"))
    undo_decision(case, item(due)["id"], "Reviewer", "paralegal")
    record_decision(case, item(due), decision(case, "set", key=due, value="2026-12-29"))
    assert any(d["date"] == "2026-12-29" for d in journey.journey(case)["deadlines"])
