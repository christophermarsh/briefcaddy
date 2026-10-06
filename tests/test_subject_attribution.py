"""EV2 fictional source attribution acceptance, independent of legal rules."""
import json
from pathlib import Path

import pytest

from batch import process_documents
from factgraph import FactGraph
from subject_attribution import required_slots, role_for
import subject_attribution as subjects
import documents
import critical_review
from review.state import save_bundle, reviewed_graph, record_decision, load_decisions, load_decision_log
from synthetic_documents import process_retained_documents


I94 = json.loads((Path(__file__).parent / "fixtures" / "document_instances.json").read_text(encoding="utf-8"))["same_type_i94"]


def test_original_source_proof_survives_serialization_and_tracks_derivation_inputs():
    result = process_documents("synthetic", [("scan.pdf", I94[0])], pages={"scan.pdf": [I94[0]]})
    graph = FactGraph.from_dict(result.raw_graph.to_dict())
    source = graph.get("applicant.given_name").sources[0]
    assert source.instance_id and source.subject_role == "holder" and source.evidence_version
    graph.add_source("example.derived_name", "scan.pdf", "i94", "derived", "ALPHA", .8,
                     from_facts=["applicant.given_name"])
    assert graph.get("example.derived_name").sources[0].input_evidence == [source.evidence_version]
    second = process_documents("synthetic", [("scan.pdf", I94[0])], pages={"scan.pdf": [I94[0]]})
    assert second.raw_graph.get("applicant.given_name").sources[0].evidence_version == source.evidence_version


def test_reader_roles_keep_family_slots_distinct_and_unmapped_roles_explicit():
    assert role_for("birth_certificate", "applicant.dob") == "birth_subject"
    assert role_for("birth_certificate", "applicant.birth_cert.parent_a_name") == "parent_a"
    assert required_slots("birth_certificate", "parent_a") == ("birth_subject", "parent_a")
    assert role_for("birth_certificate", "applicant.birth_cert.grandparents") == "grandparents"
    assert required_slots("birth_certificate", "grandparents") == ("birth_subject",)
    assert role_for("marriage_certificate", "marriage.party_b.dob") == "party_b"
    assert role_for("marriage_certificate", "marriage.party_a.parent1_name") == "party_a_parent1"
    assert required_slots("marriage_certificate", "party_a_parent1") == ("party_a", "party_a_parent1")
    assert role_for("i360_approval", "applicant.a_number") == "unmapped"
    assert role_for("i360_approval", "applicant.given_name") == "beneficiary"
    assert role_for("intake_questionnaire", "applicant.mother_dob") == "mother"
    assert required_slots("intake_questionnaire", "mother") == ("respondent", "mother")
    assert role_for("tax_return", "i864.tax_return_2025") == "unmapped"


def saved(tmp_path, docs=None, pages=None):
    folder, case = tmp_path / "source", tmp_path / "clients" / "synthetic"
    result = process_retained_documents(case.name, folder, docs or [("scan.pdf", "\n".join(I94))],
                                        pages=pages or ({"scan.pdf": I94} if docs is None else None))
    save_bundle(result, case, folder)
    return case


def client_id(case):
    return next(p["id"] for p in documents.read(case)["case_subjects"]["people"] if p["case_role"] == "applicant")


def confirm(case, row, mappings, **kwargs):
    return subjects.assign(case, row["instance_id"], row["fingerprint"], mappings, "Synthetic Reviewer", "paralegal", **kwargs)


def test_mixed_family_requires_named_subject_and_never_uses_relative_dob(tmp_path):
    import packet
    case = saved(tmp_path)
    rows = subjects.views(case)
    assert len(rows) == 2 and all(row["bound"] and row["held"] for row in rows)
    assert reviewed_graph(case).get("applicant.given_name") is None
    planned = packet.plan(case, {"summary": {}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0})
    assert not planned["ready"] and any("whose facts" in p for p in planned["problems"])
    spouse = subjects.add_person(case, "BETA OTHER", "spouse", "Synthetic Reviewer", "paralegal")
    confirm(case, rows[0], {"holder": client_id(case)})
    confirm(case, rows[1], {"holder": spouse["id"]})
    graph = reviewed_graph(case)
    assert graph.get("applicant.given_name").value == "ALPHA" and graph.get("applicant.dob") is None
    assert not subjects.problems(case)
    assert len(documents.read(case)["documents"]) == 2  # family evidence stays in the catalog
    assert FactGraph.load(case / "fact_graph_raw.json").get("applicant.dob").value == "2001-05-06"


def test_text_only_identity_and_other_case_subject_cannot_be_confirmed(tmp_path):
    from batch import record_documents
    from synthetic_documents import retain_documents
    folder, case = tmp_path / "source", tmp_path / "clients" / "synthetic"
    docs = [("scan.pdf", I94[0])]
    retain_documents(folder, docs)
    result = process_documents(case.name, docs)  # deliberately missing retained-byte context
    record_documents(result, folder, docs)
    save_bundle(result, case, folder)
    row = subjects.views(case)[0]
    assert row["instance_id"] and not row["bound"]
    with pytest.raises(ValueError, match="unbound"):
        confirm(case, row, {"holder": client_id(case)})
    good = saved(tmp_path / "bound", docs)
    with pytest.raises(ValueError, match="another case"):
        confirm(good, subjects.views(good)[0], {"holder": client_id(case)})


@pytest.mark.parametrize("attribute,value", [("raw_value", "OTHER"), ("normalized_value", "OTHER"), ("page", 9), ("subject_role", "beneficiary")])
def test_source_edge_change_invalidates_assignment_without_replaying_it(tmp_path, attribute, value):
    case = saved(tmp_path, [("scan.pdf", I94[0])])
    confirm(case, subjects.views(case)[0], {"holder": client_id(case)})
    for name in ("fact_graph.json", "fact_graph_raw.json"):
        graph = FactGraph.load(case / name)
        setattr(graph.get("applicant.given_name").sources[0], attribute, value)
        graph.save(case / name)
    row = subjects.views(case)[0]
    assert not row["bound"] and row["held"] and not row["current"]
    assert reviewed_graph(case).get("applicant.given_name") is None


def test_reader_and_subject_version_changes_invalidate_exact_assignment(tmp_path, monkeypatch):
    case = saved(tmp_path, [("scan.pdf", I94[0])])
    confirm(case, subjects.views(case)[0], {"holder": client_id(case)})
    data = documents.read(case)
    data["case_subjects"]["people"][0]["label"] = "Corrected client identity"
    documents.save(case, data)
    assert not subjects.views(case)[0]["current"]
    confirm(case, subjects.views(case)[0], {"holder": client_id(case)})
    monkeypatch.setattr(subjects, "reader_version", lambda kind: "changed-reader")
    assert not subjects.views(case)[0]["bound"]


def test_subject_change_and_undo_preserve_history_without_reviving_field_approval(tmp_path):
    case = saved(tmp_path, [("scan.pdf", I94[0])])
    row = subjects.views(case)[0]
    confirm(case, row, {"holder": client_id(case)})
    item = {"id": "fact:applicant.given_name", "kind": "fact", "level": "review", "title": "Name", "group": "check",
            "facts": [{"key": "applicant.given_name", "input": {}}], "actions": ["confirm"]}
    record_decision(case, item, {"action": "confirm", "reviewer": "Synthetic Reviewer", "role": "paralegal",
                                 "evidence_fingerprints": {item["facts"][0]["key"]:
                                     critical_review.context(case)[item["facts"][0]["key"]]["fingerprint"]}})
    spouse = subjects.add_person(case, "ALPHA EXAMPLE", "spouse", "Synthetic Reviewer", "paralegal")
    confirm(case, row, {"holder": spouse["id"]})
    assert reviewed_graph(case).get("applicant.given_name") is None
    assert load_decision_log(case)[item["id"]]["undone"]
    confirm(case, row, {}, undo=True)
    assert subjects.views(case)[0]["held"]
    confirm(case, row, {"holder": client_id(case)})
    assert reviewed_graph(case).get("applicant.given_name").value == "ALPHA"
    assert item["id"] not in load_decisions(case)
    assert len(subjects.views(case)[0]["assignment"]["history"]) == 4


def test_unchanged_sibling_keeps_subject_and_field_approval(tmp_path):
    ssn = "YOUR SOCIAL SECURITY CARD\n123-45-6789\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION"
    case = saved(tmp_path, [("i94.pdf", I94[0]), ("ssn.pdf", ssn)])
    rows = {r["file"]: r for r in subjects.views(case)}
    for row in rows.values():
        confirm(case, row, {"holder": client_id(case)})
    item = {"id": "fact:applicant.ssn", "kind": "fact", "level": "review", "title": "SSN", "group": "check",
            "facts": [{"key": "applicant.ssn", "input": {}}], "actions": ["confirm"]}
    record_decision(case, item, {"action": "confirm", "reviewer": "Synthetic Reviewer", "role": "paralegal",
                                 "evidence_fingerprints": {item["facts"][0]["key"]:
                                     critical_review.context(case)[item["facts"][0]["key"]]["fingerprint"]}})
    other = subjects.add_person(case, "ALPHA EXAMPLE", "other", "Synthetic Reviewer", "paralegal")
    confirm(case, rows["i94.pdf"], {"holder": other["id"]})
    assert item["id"] in load_decisions(case)
    assert next(r for r in subjects.views(case) if r["file"] == "ssn.pdf")["current"]


def test_marriage_assigns_each_party_and_derived_dob_keeps_the_correct_edge(tmp_path):
    from test_name_events import MA_AFTER
    case = saved(tmp_path, [("marriage.pdf", MA_AFTER)])
    row = subjects.views(case)[0]
    spouse = subjects.add_person(case, "MATEUS PEDRO TESTE", "spouse", "Synthetic Reviewer", "paralegal")
    confirm(case, row, {"party_a": client_id(case), "party_b": spouse["id"]})
    graph = reviewed_graph(case)
    assert graph.get("applicant.dob").value == "2006-03-14"
    assert graph.get("applicant.spouse_dob").value == "2004-05-02"
    raw = FactGraph.load(case / "fact_graph_raw.json")
    own = raw.get("marriage.party_a.dob").sources[0].evidence_version
    partner = raw.get("marriage.party_b.dob").sources[0].evidence_version
    assert own in graph.get("applicant.dob").sources[0].input_evidence
    assert partner in graph.get("applicant.spouse_dob").sources[0].input_evidence


def test_catalog_owner_change_reopens_subject_review_instead_of_blanket_family_acceptance(tmp_path):
    case = saved(tmp_path, [("scan.pdf", I94[0])])
    confirm(case, subjects.views(case)[0], {"holder": client_id(case)})
    record = documents.read(case)["documents"][0]
    documents.set_person(case, record["id"], "spouse", "Synthetic Reviewer", "paralegal")
    assert subjects.views(case)[0]["held"] and subjects.views(case)[0]["assignment"]["undone"]
    assert reviewed_graph(case).get("applicant.given_name") is None


@pytest.mark.parametrize("field,value", [("version", 999), ("who", ""), ("role", "support"), ("at", "invalid"),
                                          ("source_sha256", "different"), ("evidence_versions", []), ("note", "")])
def test_malformed_reference_decision_cannot_clear_subject_hold(tmp_path, field, value):
    case = saved(tmp_path, [("scan.pdf", I94[0])])
    row = subjects.views(case)[0]
    confirm(case, row, {}, reference_only=True, note="An unrelated person's exhibit retained for context")
    assert not subjects.problems(case)
    data = documents.read(case)
    data["subject_assignments"][row["instance_id"]][field] = value
    documents.save(case, data)
    assert subjects.views(case)[0]["held"] and subjects.problems(case)


def test_birth_parent_slots_require_named_people_with_shared_grandparent_context(tmp_path):
    from test_people import AGREED_PARENT_TRANSLATOR
    case = saved(tmp_path, [("birth.pdf", AGREED_PARENT_TRANSLATOR)])
    row = subjects.views(case)[0]
    mother = subjects.add_person(case, "FICCAOL FICCAOG DA FICCAOB", "mother", "Reviewer", "paralegal")
    father = subjects.add_person(case, "ANA FICCAOO DOS FICCAOK", "father", "Reviewer", "paralegal")
    confirm(case, row, {"birth_subject": client_id(case), "parent_a": mother["id"]})
    graph = reviewed_graph(case)
    assert graph.get("applicant.dob").value == "2003-06-03"
    assert graph.get("applicant.mother_given_name").value == "FICCAOL"
    assert graph.get("applicant.father_given_name") is None  # no elimination or name match
    pending = [f for f in subjects.views(case)[0]["facts"] if f["state"] == "pending"]
    assert any(f["role"] == "parent_b" for f in pending)
    assert next(f for f in subjects.views(case)[0]["facts"] if f["role"] == "grandparents")["state"] == "accepted"
    assert role_for("birth_certificate", "applicant.unsupported_context") == "unmapped"
    confirm(case, row, {"birth_subject": client_id(case), "parent_a": mother["id"], "parent_b": father["id"]})
    graph = reviewed_graph(case)
    assert graph.get("applicant.father_given_name").value == "ANA" and not subjects.problems(case)
    assert FactGraph.load(case / "fact_graph_raw.json").get("applicant.birth_cert.grandparents")


def test_explicit_parent_choices_add_printed_names_without_retyping_or_field_approval(tmp_path):
    from test_people import AGREED_PARENT_TRANSLATOR
    case = saved(tmp_path, [("birth.pdf", AGREED_PARENT_TRANSLATOR)])
    row = subjects.views(case)[0]
    assert {p["case_role"] for p in documents.read(case)["case_subjects"]["people"]} == {"applicant"}
    confirm(case, row, {"birth_subject": client_id(case), "parent_a": "new:mother", "parent_b": "new:father"})
    people = {p["case_role"]: p for p in documents.read(case)["case_subjects"]["people"]}
    assert people["mother"]["label"] == "FICCAOL FICCAOG DA FICCAOB"
    assert people["father"]["label"] == "ANA FICCAOO DOS FICCAOK"
    assert people["mother"]["who"] == "Synthetic Reviewer" and people["father"]["role"] == "paralegal"
    assert not subjects.views(case)[0]["held"]
    assert not (case / "decisions.json").exists()


def test_typed_parent_names_suggest_roles_without_automatic_identity_or_form_approval(tmp_path):
    from test_people import AGREED_PARENT_TRANSLATOR
    case = saved(tmp_path, [("birth.pdf", AGREED_PARENT_TRANSLATOR)])
    for file in ("fact_graph_raw.json", "fact_graph.json"):
        graph = FactGraph.load(case / file)
        for role, name in (("mother", "FICCAOL FICCAOG DA FICCAOB"), ("father", "ANA FICCAOO DOS FICCAOK")):
            graph.add_source(f"questionnaire.{role}_birth_name", "portal questionnaire", "intake_questionnaire", name, name, .95, tier=3)
        graph.save(case / file)
    row = subjects.views(case)[0]
    assert row["parent_suggestions"] == {"parent_a": {"role": "mother", "subject_id": None}, "parent_b": {"role": "father", "subject_id": None}}
    assert row["held"] and not row["current"]
    assert len(documents.read(case)["case_subjects"]["people"]) == 1


@pytest.mark.parametrize("choices", [{"parent_a": "new:mother", "parent_b": "new:mother"},
                                     {"parent_a": "new:grandfather"}, {"holder": "new:mother"}])
def test_invalid_parent_creation_cannot_leave_partial_people_or_assignments(tmp_path, choices):
    from test_people import SECOND_TRANSLATOR
    case = saved(tmp_path, [("birth.pdf", SECOND_TRANSLATOR)])
    row = subjects.views(case)[0]
    before = (case / "documents.json").read_bytes()
    with pytest.raises(ValueError):
        confirm(case, row, {"birth_subject": client_id(case), **choices})
    assert (case / "documents.json").read_bytes() == before


def test_marriage_parent_mapping_beats_shared_names_and_reversed_columns(tmp_path):
    # The two parent identities deliberately have the same name. Column A/B
    # changes on the second certificate; neither names nor position assign sex.
    template = """The Commonwealth of Massachusetts
Certificate of Marriage
Date of Marriage: JULY 1, 2026 Place of Marriage: WORCESTER, MA
Party A Party B
Name: {a} Name: {b}
Name of Parent: ALEX EXAMPLE Name of Parent: ALEX EXAMPLE
"""
    case = saved(tmp_path, [("first.pdf", template.format(a="ALPHA EXAMPLE", b="BETA EXAMPLE")),
                            ("second.pdf", template.format(a="BETA EXAMPLE", b="ALPHA EXAMPLE"))])
    spouse = subjects.add_person(case, "BETA EXAMPLE", "spouse", "Reviewer", "paralegal")
    mother = subjects.add_person(case, "ALEX EXAMPLE", "mother", "Reviewer", "paralegal")
    father = subjects.add_person(case, "ALEX EXAMPLE", "father", "Reviewer", "paralegal")
    for name in ("fact_graph.json", "fact_graph_raw.json"):
        graph = FactGraph.load(case / name)
        graph.add_source("questionnaire.mother_name", "portal questionnaire", "intake_questionnaire", "ALEX EXAMPLE", "ALEX EXAMPLE", .95, tier=3)
        graph.save(case / name)
    for row in subjects.views(case):
        own = "party_a" if row["file"] == "first.pdf" else "party_b"
        other = "party_b" if own == "party_a" else "party_a"
        confirm(case, row, {own: client_id(case), other: spouse["id"], own + "_parent1": father["id"], other + "_parent1": mother["id"]})
    graph = reviewed_graph(case)
    mom = graph.get("applicant.mother_given_name")
    dad = graph.get("applicant.father_given_name")
    assert mom.value == dad.value == "ALEX"
    assert all(s.doc_id == "portal questionnaire" for s in mom.sources)
    assert dad.sources and all(s.doc_type == "marriage_certificate" for s in dad.sources)
    assert not subjects.problems(case)


def test_legacy_catalog_owner_is_history_and_never_an_accepted_fact_subject(tmp_path):
    case = saved(tmp_path, [("scan.pdf", I94[0])])
    data = documents.read(case)
    data["documents"][0].update(person="applicant", person_set_by={"who": "Earlier Reviewer", "at": "2026-09-01T10:00:00+00:00"})
    documents.save(case, data)
    assert subjects.views(case)[0]["held"] and reviewed_graph(case).get("applicant.given_name") is None
    assert documents.read(case)["documents"][0]["person_set_by"]["who"] == "Earlier Reviewer"
