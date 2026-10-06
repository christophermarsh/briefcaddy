"""Pending UX03 implementation contract; no labels supplied to a product reader."""
import pytest
from pypdf import PdfReader

import critical_review as critical
from factgraph import FactGraph
from fill import map_facts_to_fields, fill_pdf
from review.state import reviewed_graph, record_decision, undo_decision, source_prerequisites
from test_review_evidence_routes import world, app, controls  # noqa: F401 -- pytest fixture registration and helper reexports
from place_compare_fixtures import retain_place_case

CITY = "applicant.birth_city"
REFERENCE = "applicant.marriage_cert_birthplace"
FIELD = "form1[0].#subform[1].Pt1Line7_CityTownOfBirth[0]"


def filed_city(app, case, path):  # noqa: F811 -- pytest fixture injection
    mapped = map_facts_to_fields(reviewed_graph(case), app.field_map)
    fill_pdf(app.template, mapped.values, path)
    return str(PdfReader(path).get_fields()[FIELD].get("/V") or "")


def test_specificity_reference_acknowledges_only_current_i485_context(world, app, tmp_path):  # noqa: F811 -- pytest fixture injection
    case = retain_place_case(world, app, tmp_path)
    payload = app.items(case.name)
    card = next(c for c in payload["cards"] if c["id"] == "crosscheck:" + REFERENCE)
    compare = card["comparison"]
    assert compare["kind"] == "specificity" and compare["scope"] == "current_i485"
    assert compare["reference_impact"]["state"] == "reference"
    assert card["actions"] == ["acknowledge"]
    city_context = next(f for f in card["facts"] if f["key"] == CITY)
    assert city_context["comparison_readonly"] and city_context["value"] == "CAMPINAS"
    assert not city_context["input"].get("alternatives")
    assert {"birth.pdf", "marriage.pdf"} <= set(card["docs"])
    assert card["evidence_fingerprints"].get(CITY)  # inspectable proof, never approved by Ack
    assert compare["filed_city"]["key"] == CITY and compare["filed_city"]["fields"] == [FIELD]
    assert compare["filed_city"]["value"] == "CAMPINAS"
    before = filed_city(app, case, tmp_path / "before.pdf")
    pending = critical.problems(case)
    item = next(i for i in payload["open"] if i["id"] == "crosscheck:" + REFERENCE)
    record_decision(case, item, {"action": "acknowledge", "reviewer": "Jane Doe", "role": "paralegal", "note": "Reviewed state-level reference beside the city on the birth certificate."})
    assert filed_city(app, case, tmp_path / "after.pdf") == before == "CAMPINAS"
    assert critical.problems(case) == pending  # no source/field approval is manufactured
    import json
    decision = json.loads((case / "decisions.json").read_text(encoding="utf-8"))[item["id"]]
    assert not decision.get("evidence_confirmation")
    assert not decision.get("values")


def test_missing_current_city_output_lineage_renders_unavailable_readonly_comparison(world, app, tmp_path):  # noqa: F811 -- pytest fixture injection
    import copy
    from review.state import build_items
    case = retain_place_case(world, app, tmp_path)
    catalog = copy.copy(app.catalog)
    catalog.field_map = {k: v for k, v in app.catalog.field_map.items() if k != CITY}
    payload = build_items(case, app.field_map, app.template, catalog, pending=False)
    item = next(i for i in payload["open"] if i["id"] == "crosscheck:" + REFERENCE)
    card = next(c for c in payload["cards"] if c["id"] == item["id"])
    assert card["comparison"]["kind"] == "unavailable" and card["comparison"]["reason"]
    assert card["actions"] == ["acknowledge"]
    city = next(f for f in card["facts"] if f["key"] == CITY)
    assert city["comparison_readonly"] and not city["input"].get("alternatives")
    before = filed_city(app, case, tmp_path / "before-unavailable.pdf")
    pending = critical.problems(case)
    for action in ("confirm", "set"):
        with pytest.raises(ValueError):
            record_decision(case, item, {"action": action, "values": {CITY: "SOROCABA"}, "reviewer": "Jane Doe", "role": "paralegal"})
    assert filed_city(app, case, tmp_path / "after-unavailable.pdf") == before == "CAMPINAS"
    assert critical.problems(case) == pending


def test_true_city_disagreement_requires_choice_and_proof_bound_correction_undo(world, app, tmp_path):  # noqa: F811 -- pytest fixture injection
    case = retain_place_case(world, app, tmp_path, disagreement=True)
    import subject_attribution as subjects
    materialized = FactGraph.load(case / "fact_graph.json")
    birth = next(row for row in subjects.views(case, materialized) if row["type"] == "birth_certificate")
    assert birth["bound"] and birth["current"]
    assert CITY not in source_prerequisites(case)
    assert critical.context(case)[CITY]["bound"]
    parent = materialized.get("applicant.birth_certificate_name")
    versions = {source.evidence_version for source in parent.sources if source.doc_id == "birth.pdf"}
    for key in ("applicant.given_name", "applicant.family_name"):
        split = next(source for source in materialized.get(key).sources if source.doc_id == "birth.pdf")
        assert split.from_facts == ["applicant.birth_certificate_name"]
        assert versions <= set(split.input_evidence)
        assert split.instance_id is None and split.evidence_version is None  # derivation, no forged raw edge
    payload = app.items(case.name)
    card = next(c for c in payload["cards"] if c["id"] == "crosscheck:" + REFERENCE)
    assert card["comparison"]["kind"] == "disagreement"
    assert "acknowledge" not in card["actions"] and "set" in card["actions"]
    city = next(f for f in card["facts"] if f["key"] == CITY)
    assert city["input"]["alternatives"] == ["CAMPINAS", "SOROCABA"]
    assert city["input"]["type"] == "text"  # real correction keeps existing value validation
    assert filed_city(app, case, tmp_path / "before.pdf") == "CAMPINAS"
    item = next(i for i in payload["open"] if i["id"] == "crosscheck:" + REFERENCE)
    with pytest.raises(ValueError):
        record_decision(case, item, {"action": "acknowledge", "reviewer": "Jane Doe", "role": "paralegal"})
    with pytest.raises(ValueError, match="not part of this item"):
        record_decision(case, item, {"action": "set", "values": {REFERENCE: "SOROCABA, BRAZIL"}, "reviewer": "Jane Doe", "role": "paralegal"})
    with pytest.raises(ValueError, match="evidence changed"):
        record_decision(case, item, {"action": "set", "values": {CITY: "SOROCABA"}, "reviewer": "Jane Doe", "role": "paralegal"})
    fingerprints = {f["key"]: c["fingerprint"] for f in item["facts"] if (c := critical.context(case).get(f["key"]))}
    stored = record_decision(case, item, {"action": "set", "values": {CITY: "SOROCABA"}, "reviewer": "Jane Doe", "role": "paralegal", "evidence_fingerprints": fingerprints})
    assert stored["evidence_confirmation"]["basis"] == "manual_retained_source_review"
    assert filed_city(app, case, tmp_path / "selected.pdf") == "SOROCABA"
    undo_decision(case, item["id"], "Jane Doe", "paralegal")
    assert filed_city(app, case, tmp_path / "undone.pdf") == "CAMPINAS"
    assert any(c["id"] == item["id"] for c in app.items(case.name)["cards"])


def test_indirect_source_and_rule_lineage_is_filing_relevant_even_without_direct_mapping(app):  # noqa: F811 -- pytest fixture injection
    from review.filing_impact import output_impact
    graph = FactGraph("fictional")
    graph.add_source("raw.place", "fictional.pdf", "other", "Campinas, Brazil", "CAMPINAS, BRAZIL", 1)
    graph.add_source("normalized.city", "fictional.pdf", "derived", "city component", "CAMPINAS", 1, from_facts=["raw.place"])
    graph.add_derived(CITY, "CAMPINAS", "fictional-normalization", ["normalized.city"])
    impact = output_impact(graph, "raw.place", app.catalog)
    assert impact["scope"] == "current_i485" and impact["state"] == "mapped"
    target = next(t for t in impact["targets"] if t["key"] == CITY)
    assert target["fields"] == [FIELD] and target["lineage"] == ["raw.place", "normalized.city", CITY]


@pytest.mark.parametrize("damage", ["unknown-key", "malformed-parent-list", "cycle"], ids=["unknown-key", "malformed-lineage", "cyclic-lineage"])
def test_unknown_or_damaged_lineage_is_unavailable_not_reference(app, damage):  # noqa: F811 -- pytest fixture injection
    from review.filing_impact import output_impact
    graph = FactGraph("fictional")
    graph.add_source("raw.place", "fictional.pdf", "other", "Campinas", "CAMPINAS", 1)
    graph.add_derived(CITY, "CAMPINAS", "fictional-normalization", ["raw.place"])
    key = "raw.place"
    if damage == "unknown-key":
        key = "unrecorded.place"
    elif damage == "malformed-parent-list":
        graph.get(CITY).derived_from = "raw.place"
    else:
        graph.get("raw.place").sources[0].from_facts = [CITY]
    impact = output_impact(graph, key, app.catalog)
    assert impact["scope"] == "current_i485" and impact["state"] == "unavailable"
    assert impact["reason"]


@pytest.mark.parametrize("parent", ["missing", "different-document", "different-name"], ids=["missing-parent", "other-document-parent", "other-name-parent"])
def test_birth_name_split_without_matching_actual_parent_does_not_fabricate_provenance(parent):
    import name_events
    graph = FactGraph("fictional")
    if parent != "missing":
        graph.add_source("applicant.birth_certificate_name", "other.pdf" if parent == "different-document" else "birth.pdf",
                         "birth_certificate", "Name", "OTHER PERSON" if parent == "different-name" else "ALPHA SAMPLE", 1,
                         instance_id="fictional-instance", evidence_version="fictional-version", subject_role="birth_subject")
    event = {"kind": "birth", "doc": "birth.pdf", "doc_type": "birth_certificate", "name": "ALPHA SAMPLE", "tier": 1}
    name_events._put(graph, "applicant.given_name", "ALPHA", event, "Fictional split")
    source = graph.get("applicant.given_name").sources[0]
    assert source.from_facts == [] and source.input_evidence == []
    assert source.instance_id is None and source.evidence_version is None
    import subject_attribution as subjects
    assert subjects.original_source(source)  # still an unbound original, never silently exempted
