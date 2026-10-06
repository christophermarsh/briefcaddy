"""Observed court-name split retains its actual parent evidence, no forged raw edge."""
import pytest
from factgraph import FactGraph
import name_events
import subject_attribution as subjects

PARENT = "applicant.name_change.new_name"
EVENT = {"kind": "court_order", "doc": "order.pdf", "doc_type": "name_change_order", "name": "ALPHA SAMPLE", "tier": 1}


def test_court_name_split_inherits_actual_matching_parent_evidence():
    graph = FactGraph("fictional")
    graph.add_source(PARENT, "order.pdf", "name_change_order", "New name: ALPHA SAMPLE", "ALPHA SAMPLE", 1,
                     instance_id="fictional-instance", evidence_version="fictional-version", subject_role="holder")
    name_events._put(graph, "applicant.family_name", "SAMPLE", EVENT, "Split from actual new name")
    source = graph.get("applicant.family_name").sources[0]
    assert source.from_facts == [PARENT] and source.input_evidence == ["fictional-version"]
    assert source.instance_id is None and source.evidence_version is None and source.subject_role is None
    assert source.from_facts and source.input_evidence  # views excludes this derivation from raw-edge attribution


@pytest.mark.parametrize("damage", ["missing", "other-document", "other-type", "other-name"])
def test_court_name_split_without_matching_parent_retains_unbound_guard(damage):
    graph = FactGraph("fictional")
    if damage != "missing":
        graph.add_source(PARENT, "another.pdf" if damage == "other-document" else "order.pdf",
                         "birth_certificate" if damage == "other-type" else "name_change_order", "Printed name",
                         "DIFFERENT PERSON" if damage == "other-name" else "ALPHA SAMPLE", 1,
                         instance_id="fictional-instance", evidence_version="fictional-version", subject_role="holder")
    name_events._put(graph, "applicant.family_name", "SAMPLE", EVENT, "Fictional split")
    source = graph.get("applicant.family_name").sources[0]
    assert source.from_facts == [] and source.input_evidence == []
    assert source.instance_id is None and source.evidence_version is None and source.subject_role is None
    assert subjects.original_source(source)
