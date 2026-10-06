"""src/rules/policy.py and the shipped schemas/law/policy_sijs.json."""

from datetime import date

from factgraph import FactGraph
from rules.policy import load_policy_profile, run_policies
import schema_path

_PROFILE = schema_path.path("law", "policy_sijs")
TODAY = date(2026, 9, 30)


def _sijs_graph(**facts):
    g = FactGraph("t")
    g.add_source("applicant.i360_receipt_number", "i360.pdf", "i360_approval", "X", "MSC0000000000", 0.99)
    for key, value in facts.items():
        g.add_source(key.replace("__", "."), "doc.pdf", "doc", value, value, 0.9)
    return g


def test_conditions_and_unanswered_mode():
    policies = [{"id": "P", "when_present": ["a.x"], "unless_present": ["a.block"], "mode": "unanswered", "set": {"a.out": "No", "a.kept": "No"}}]
    g = FactGraph("t")
    g.add_source("a.x", "d", "t", "1", "1", 1.0)
    g.add_source("a.kept", "q", "intake_questionnaire", "Yes", "Yes", 0.7, tier=3)
    assert run_policies(g, policies, TODAY) == ["P"]
    assert g.get("a.out").value == "No" and g.get("a.out").derived_by == "POLICY:P"
    assert g.get("a.kept").value == "Yes"  # already answered -> untouched

    g2 = FactGraph("t")
    g2.add_source("a.x", "d", "t", "1", "1", 1.0)
    g2.add_source("a.block", "d", "t", "1", "1", 1.0)
    assert run_policies(g2, policies, TODAY) == []


def test_policy_output_from_a_tier3_input_stays_tier3():
    policies = [{"id": "NA", "when_values": {"applicant.times_married": "0"}, "set": {"applicant.na.prior_spouse": "NOT APPLICABLE"}}]
    g = FactGraph("t")
    g.add_source("applicant.times_married", "q.pdf", "intake_questionnaire", "0", "0", 0.7, tier=3)
    run_policies(g, policies, TODAY)
    assert g.get("applicant.na.prior_spouse").tier == 3


def test_cspa_item_answered_no_only_under_21():
    profile = load_policy_profile(_PROFILE)
    young = _sijs_graph(applicant__dob="2008-01-01")
    run_policies(young, profile, TODAY)
    assert young.get("applicant.part2.cspa_21_or_older").value == "No"
    # Fictional fixture or generic implementation note.
    older = _sijs_graph(applicant__dob="2003-03-03")
    run_policies(older, profile, TODAY)
    assert older.get("applicant.part2.cspa_21_or_older") is None


def test_part9_default_no_needs_clean_criminal_answers_and_no_court_documents():
    profile = load_policy_profile(_PROFILE)
    clean = _sijs_graph(applicant__part9__arrested_cited_charged_detained="No", applicant__part9__committed_crime="No")
    run_policies(clean, profile, TODAY)
    assert clean.get("applicant.part9.pt8line25").value == "No"
    assert clean.get("applicant.part9.pt9line77") is None  # follow-up: never defaulted

    # Fictional fixture or generic implementation note.
    docket = _sijs_graph(applicant__part9__arrested_cited_charged_detained="No", applicant__part9__committed_crime="No", applicant__criminal_record_present="Yes")
    run_policies(docket, profile, TODAY)
    assert docket.get("applicant.part9.pt8line25") is None

    # Fictional fixture or generic implementation note.
    blank = _sijs_graph()
    run_policies(blank, profile, TODAY)
    assert blank.get("applicant.part9.pt8line25") is None


def test_eoir_answer_left_to_attorney_when_an_nta_exists():
    profile = load_policy_profile(_PROFILE)
    g = _sijs_graph(applicant__nta_present="Yes")
    run_policies(g, profile, TODAY)
    assert g.get("applicant.filing_with_eoir") is None
