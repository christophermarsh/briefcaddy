"""FactGraph -- modeled directly on real findings from the example case
(Maria Eduarda Moura Sampaio): a single clean source (I-94) should resolve
cleanly; two disagreeing sources (driver's license eye color vs. whatever
the old manual process typed) should surface as a conflict, not silently
pick one; a rule's output should be traceable to the facts it read."""

import pytest

from factgraph import FactGraph


def test_single_source_resolves_cleanly():
    graph = FactGraph("maria_eduarda")
    graph.add_source(
        "applicant.i94_number", doc_id="i94.pdf", doc_type="i94",
        raw_value="14335150685", normalized_value="14335150685", confidence=0.98,
    )
    fact = graph.get("applicant.i94_number")
    assert fact.status == "resolved"
    assert fact.value == "14335150685"
    assert fact.tier == 1
    assert len(fact.sources) == 1


def test_agreeing_sources_stay_resolved():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.dob", "birth_certificate.pdf", "birth_certificate", "02/20/2002", "2002-02-20", 0.9)
    graph.add_source("applicant.dob", "i94.pdf", "i94", "2002 February 20", "2002-02-20", 0.98)
    fact = graph.get("applicant.dob")
    assert fact.status == "resolved"
    assert fact.value == "2002-02-20"
    assert len(fact.sources) == 2


def test_disagreeing_sources_become_a_conflict():
    # Real case: driver's license said Brown, the old manual process
    # entered Black. A second source that disagrees must not silently
    # overwrite the first.
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.eye_color", "drivers_license.pdf", "drivers_license", "BRO", "Brown", 0.95)
    graph.add_source("applicant.eye_color", "old_intake_form.pdf", "intake_form", "Black", "Black", 0.4)
    fact = graph.get("applicant.eye_color")
    assert fact.status == "conflict"
    assert len(fact.sources) == 2
    # higher-confidence source wins as the provisional value, but stays flagged
    assert fact.value == "Brown"


def test_resolve_conflict_records_who_and_why():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.eye_color", "drivers_license.pdf", "drivers_license", "BRO", "Brown", 0.95)
    graph.add_source("applicant.eye_color", "old_intake_form.pdf", "intake_form", "Black", "Black", 0.4)
    graph.resolve_conflict(
        "applicant.eye_color", chosen_value="Brown",
        reason="Driver's license is the more reliable source; intake form typo.",
        resolved_by="paralegal_jane",
    )
    fact = graph.get("applicant.eye_color")
    assert fact.status == "resolved"
    assert fact.value == "Brown"
    assert fact.resolution.resolved_by == "paralegal_jane"


def test_resolve_conflict_raises_if_not_actually_in_conflict():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.dob", "i94.pdf", "i94", "2002 February 20", "2002-02-20", 0.98)
    with pytest.raises(ValueError):
        graph.resolve_conflict("applicant.dob", "2002-02-20", "n/a", "n/a")


def test_derived_fact_traces_back_to_its_inputs():
    # RULE OVERSTAY-01 from docs/GRAPH_MODEL.md
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.i94_admit_until_date", "i94.pdf", "i94", "2017 February 21", "2017-02-21", 0.98)
    graph.add_source("applicant.i360_priority_date", "i360_approval.pdf", "i360_approval", "10/14/2022", "2022-10-14", 0.98)
    graph.add_derived(
        "applicant.part9.violated_nonimmigrant_status",
        value="Yes",
        rule_id="OVERSTAY-01",
        input_fact_keys=["applicant.i94_admit_until_date", "applicant.i360_priority_date"],
    )
    fact = graph.get("applicant.part9.violated_nonimmigrant_status")
    assert fact.tier == 2
    assert fact.status == "resolved"
    assert fact.derived_by == "OVERSTAY-01"
    assert fact.derived_from == ["applicant.i94_admit_until_date", "applicant.i360_priority_date"]


def test_mark_missing_is_distinct_from_never_touched():
    graph = FactGraph("maria_eduarda")
    graph.mark_missing("applicant.employment_history", reason="No questionnaire or interview notes in folder yet.")
    fact = graph.get("applicant.employment_history")
    assert fact.status == "missing"
    assert "questionnaire" in fact.missing_reason


def test_missing_reports_both_untouched_and_explicitly_missing_keys():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.i94_number", "i94.pdf", "i94", "14335150685", "14335150685", 0.98)
    graph.mark_missing("applicant.employment_history", reason="no source yet")
    missing = graph.missing(["applicant.i94_number", "applicant.employment_history", "applicant.ssn"])
    assert set(missing) == {"applicant.employment_history", "applicant.ssn"}


def test_conflicts_lists_only_unresolved_conflicts():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.eye_color", "drivers_license.pdf", "drivers_license", "BRO", "Brown", 0.95)
    graph.add_source("applicant.eye_color", "old_intake_form.pdf", "intake_form", "Black", "Black", 0.4)
    graph.add_source("applicant.i94_number", "i94.pdf", "i94", "14335150685", "14335150685", 0.98)
    assert [f.fact_key for f in graph.conflicts()] == ["applicant.eye_color"]

    graph.resolve_conflict("applicant.eye_color", "Brown", "reason", "someone")
    assert graph.conflicts() == []


def test_by_tier_filters_correctly():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.i94_number", "i94.pdf", "i94", "14335150685", "14335150685", 0.98)
    graph.add_derived("applicant.part9.violated_nonimmigrant_status", "Yes", "OVERSTAY-01", ["applicant.i94_number"])
    graph.mark_missing("applicant.employment_history", "no source")
    assert [f.fact_key for f in graph.by_tier(1)] == ["applicant.i94_number"]
    assert [f.fact_key for f in graph.by_tier(2)] == ["applicant.part9.violated_nonimmigrant_status"]
    assert [f.fact_key for f in graph.by_tier(3)] == ["applicant.employment_history"]


def test_round_trip_serialization(tmp_path):
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.i94_number", "i94.pdf", "i94", "14335150685", "14335150685", 0.98)
    graph.add_source("applicant.eye_color", "drivers_license.pdf", "drivers_license", "BRO", "Brown", 0.95)
    graph.add_source("applicant.eye_color", "old_intake_form.pdf", "intake_form", "Black", "Black", 0.4)
    graph.add_derived("applicant.part9.violated_nonimmigrant_status", "Yes", "OVERSTAY-01", ["applicant.i94_number"])
    graph.mark_missing("applicant.employment_history", "no source yet")

    path = tmp_path / "fact_graph.json"
    graph.save(path)
    loaded = FactGraph.load(path)

    assert loaded.client_id == graph.client_id
    assert loaded.get("applicant.i94_number").value == "14335150685"
    assert loaded.get("applicant.eye_color").status == "conflict"
    assert loaded.get("applicant.part9.violated_nonimmigrant_status").derived_by == "OVERSTAY-01"
    assert loaded.get("applicant.employment_history").status == "missing"


def test_rule_output_keeps_existing_sources_and_agrees():
    # Real example: the client's questionnaire answered Part 9 item 13
    # "Yes" and OVERSTAY-01 derived "Yes" -- both must stay on record.
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.part9.violated_nonimmigrant_status", "q.pdf", "intake_questionnaire", "Yes", "Yes", 0.9, tier=3)
    fact = graph.add_derived("applicant.part9.violated_nonimmigrant_status", "Yes", "OVERSTAY-01", ["x"])
    assert fact.status == "resolved"
    assert fact.derived_by == "OVERSTAY-01"
    assert [s.doc_id for s in fact.sources] == ["q.pdf"]


def test_rule_output_contradicting_every_source_is_a_conflict_not_an_overwrite():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.part9.violated_nonimmigrant_status", "q.pdf", "intake_questionnaire", "No", "No", 0.9, tier=3)
    fact = graph.add_derived("applicant.part9.violated_nonimmigrant_status", "Yes", "OVERSTAY-01", ["x"])
    assert fact.status == "conflict"
    assert [s.normalized_value for s in fact.sources] == ["No"]


def test_rule_choosing_among_disagreeing_sources_records_a_resolution():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.citizenship", "it.pdf", "passport", "ITALIA", "ITALY", 0.95)
    graph.add_source("applicant.citizenship", "br.pdf", "passport", "BRASIL", "BRAZIL", 0.9)
    fact = graph.add_derived("applicant.citizenship", "BRAZIL", "CITIZENSHIP-01", ["applicant.country_of_birth"])
    assert fact.status == "resolved"
    assert fact.value == "BRAZIL"
    assert fact.resolution.resolved_by == "CITIZENSHIP-01"
    assert len(fact.sources) == 2
