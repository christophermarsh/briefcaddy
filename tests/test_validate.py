"""Stage 7 (validate.py) and stage 8 (report.py). FactGraph fixtures are
built directly, same style as tests/test_factgraph.py."""

from factgraph import FactGraph
from validate import group_flags, render_report, validate_graph


def test_missing_required_fact_that_was_never_touched_is_blocking():
    graph = FactGraph("maria_eduarda")
    flags = validate_graph(graph, required_fact_keys=["applicant.ssn"])
    assert len(flags) == 1
    assert flags[0].level == "blocking"
    assert "not attempted" in flags[0].message


def test_explicitly_missing_required_fact_is_blocking_with_its_reason():
    graph = FactGraph("maria_eduarda")
    graph.mark_missing("applicant.employment_history", reason="no questionnaire yet")
    flags = validate_graph(graph, required_fact_keys=["applicant.employment_history"])
    assert len(flags) == 1
    assert flags[0].level == "blocking"
    assert "no questionnaire yet" in flags[0].message


def test_resolved_tier1_required_fact_requires_current_source_review():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.i94_number", "i94.pdf", "i94", "14335150685", "14335150685", 0.98)
    flags = validate_graph(graph, required_fact_keys=["applicant.i94_number"])
    assert len(flags) == 1 and flags[0].level == "review"
    assert "critical document read" in flags[0].message


def test_tier2_derived_fact_is_always_flagged_for_review():
    graph = FactGraph("maria_eduarda")
    graph.add_derived("applicant.part9.violated_nonimmigrant_status", "Yes", "OVERSTAY-01", ["x"])
    flags = validate_graph(graph, required_fact_keys=[])
    assert len(flags) == 1
    assert flags[0].level == "review"
    assert "OVERSTAY-01" in flags[0].message


def test_tier3_fact_from_questionnaire_is_flagged_for_review():
    graph = FactGraph("maria_eduarda")
    graph.add_source(
        "applicant.employment_history", "intake_questionnaire.pdf", "intake_questionnaire",
        "worked at a bakery", "worked at a bakery", 0.6, tier=3,
    )
    flags = validate_graph(graph, required_fact_keys=[])
    assert len(flags) == 2 and all(f.level == "review" for f in flags)
    assert any("Tier 3" in f.message for f in flags)
    assert any("critical document read" in f.message for f in flags)


def test_tier1_score_does_not_replace_named_source_review():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.i94_number", "i94.pdf", "i94", "14335150685", "14335150685", 0.98)
    flags = validate_graph(graph, required_fact_keys=[])
    assert len(flags) == 1 and flags[0].level == "review"
    assert "critical document read" in flags[0].message


def test_unresolved_conflict_is_review_because_its_field_is_left_blank():
    # map_facts_to_fields() never writes a conflicted fact, so the report
    # must say so at review level -- on the real example, height was left
    # blank while the report only called it "informational".
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.eye_color", "drivers_license.pdf", "drivers_license", "BRO", "Brown", 0.95)
    graph.add_source("applicant.eye_color", "old_intake.pdf", "intake_form", "Black", "Black", 0.4)
    flags = validate_graph(graph, required_fact_keys=[])
    assert len(flags) == 2 and all(f.level == "review" for f in flags)
    conflict = next(f for f in flags if "LEFT BLANK" in f.message)
    assert "drivers_license.pdf: 'Brown'" in conflict.message
    assert any("critical document read" in f.message for f in flags)


def test_resolved_conflict_stays_flagged_informational_for_audit():
    # Even after a human resolves the conflict, GRAPH_MODEL.md's
    # resolution record exists for audit -- a resolved conflict is not
    # the same as a fact that only ever had one clean source.
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.eye_color", "drivers_license.pdf", "drivers_license", "BRO", "Brown", 0.95)
    graph.add_source("applicant.eye_color", "old_intake.pdf", "intake_form", "Black", "Black", 0.4)
    graph.resolve_conflict("applicant.eye_color", "Brown", "license is correct", "paralegal_jane")
    flags = validate_graph(graph, required_fact_keys=[])
    # Resolution remains traceable and clears the conflict, but this direct
    # graph operation has no current retained-source confirmation proof.
    assert len(flags) == 1 and flags[0].level == "review"
    assert "critical document read" in flags[0].message


def test_value_that_matches_no_source_and_has_no_resolution_is_blocking():
    # This can only happen if something set .value outside the graph's own
    # API (add_source / resolve_conflict) -- a genuine traceability break,
    # not a spelling issue. A fuzzy similarity check can't catch a real
    # typo like "Worcesrter" anyway: it scores ~95% similar to "Worcester"
    # under difflib, so this checks for exact-match-or-resolved instead.
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.address_city", "i94.pdf", "i94", "Worcester", "Worcester", 0.9)
    graph.get("applicant.address_city").value = "Worcesrter"
    flags = validate_graph(graph, required_fact_keys=[])
    assert len(flags) == 2
    assert flags[0].level == "blocking"
    assert "traceability broken" in flags[0].message
    assert flags[1].level == "review" and "critical document read" in flags[1].message


def test_value_matching_source_is_traceable_but_still_requires_source_review():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.address_city", "i94.pdf", "i94", "Worcester", "Worcester", 0.9)
    flags = validate_graph(graph, required_fact_keys=[])
    assert len(flags) == 1 and flags[0].level == "review"
    assert "critical document read" in flags[0].message


def test_resolved_conflict_value_is_traceable_via_its_resolution_record():
    # A paralegal's corrected value need not match either original source
    # verbatim -- it's traceable through the resolution record instead.
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.address_city", "i94.pdf", "i94", "Worcesrter", "Worcesrter", 0.9)
    graph.add_source("applicant.address_city", "dl.pdf", "drivers_license", "Worcestor", "Worcestor", 0.5)
    graph.resolve_conflict("applicant.address_city", "Worcester", "both sources misspelled it", "paralegal_jane")
    flags = validate_graph(graph, required_fact_keys=[])
    assert len(flags) == 1 and flags[0].level == "review"
    assert "critical document read" in flags[0].message


def test_group_flags_buckets_by_level():
    graph = FactGraph("maria_eduarda")
    graph.add_derived("applicant.part9.violated_nonimmigrant_status", "Yes", "OVERSTAY-01", ["x"])
    graph.add_source("applicant.eye_color", "a.pdf", "drivers_license", "BRO", "Brown", 0.95)
    graph.add_source("applicant.eye_color", "b.pdf", "intake_form", "Black", "Black", 0.4)
    flags = validate_graph(graph, required_fact_keys=["applicant.ssn"])
    grouped = group_flags(flags)
    assert len(grouped["blocking"]) == 1
    assert len(grouped["review"]) == 3  # rule-derived + conflict + independent source check
    assert any("LEFT BLANK" in f.message for f in grouped["review"])
    assert any("critical document read" in f.message for f in grouped["review"])
    assert len(grouped["informational"]) == 0


def test_render_report_lists_every_level_even_when_empty():
    report = render_report("maria_eduarda", flags=[])
    assert "Flag report for maria_eduarda" in report
    assert "BLOCKING (0)" in report
    assert "REVIEW (0)" in report
    assert "INFORMATIONAL (0)" in report


def test_render_report_includes_flag_messages():
    graph = FactGraph("maria_eduarda")
    flags = validate_graph(graph, required_fact_keys=["applicant.ssn"])
    report = render_report("maria_eduarda", flags)
    assert "BLOCKING (1)" in report
    assert "applicant.ssn" in report
