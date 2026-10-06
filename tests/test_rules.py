"""Rule engine (src/rules/engine.py) and the drafted rules
(src/rules/definitions.py) -- see docs/rules/*.md for the attorney-facing
writeups these implement. FactGraph fixtures are built directly, same
style as tests/test_factgraph.py, rather than routed through extractors."""

import json
import re

import pytest

from factgraph import FactGraph
from rules import ALL_RULES, CITIZENSHIP_01, NAME_01, OVERSTAY_01, Rule, run_rules, topological_order
import schema_path


def _overstayed_graph() -> FactGraph:
    # Real example case dates (docs/ARCHITECTURE.md section 4): admitted
    # until 02/21/2017, I-360 not filed until 10/14/2022.
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.i94_admit_until_date", "i94.pdf", "i94", "2017 February 21", "2017-02-21", 0.98)
    graph.add_source("applicant.i360_priority_date", "i360_approval.pdf", "i360_approval", "10/14/2022", "2022-10-14", 0.98)
    return graph


def test_overstay_01_fires_yes_when_admit_until_predates_priority_date():
    graph = _overstayed_graph()
    run_rules(graph, [OVERSTAY_01])

    fact = graph.get("applicant.part9.violated_nonimmigrant_status")
    assert fact.status == "resolved"
    assert fact.value == "Yes"
    assert fact.tier == 2
    assert fact.derived_by == "OVERSTAY-01"
    assert fact.derived_from == ["applicant.i94_admit_until_date", "applicant.i360_priority_date"]

    since = graph.get("applicant.part9.unlawfully_present_since_1997")
    assert since.value == "Yes"


def test_overstay_01_fires_no_when_admit_until_postdates_priority_date():
    graph = FactGraph("someone_else")
    graph.add_source("applicant.i94_admit_until_date", "i94.pdf", "i94", "2030 January 01", "2030-01-01", 0.98)
    graph.add_source("applicant.i360_priority_date", "i360_approval.pdf", "i360_approval", "01/01/2022", "2022-01-01", 0.98)
    run_rules(graph, [OVERSTAY_01])
    assert graph.get("applicant.part9.violated_nonimmigrant_status").value == "No"
    assert graph.get("applicant.part9.unlawfully_present_since_1997").value == "No"


def test_overstay_01_does_not_fire_when_an_interim_extension_exists():
    graph = _overstayed_graph()
    graph.add_source(
        "applicant.interim_status_extension", "extension_notice.pdf", "uscis_notice", "approved", "approved", 0.9
    )
    run_rules(graph, [OVERSTAY_01])
    assert graph.get("applicant.part9.violated_nonimmigrant_status") is None


def test_overstay_01_does_not_fire_when_an_input_never_resolves():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.i94_admit_until_date", "i94.pdf", "i94", "2017 February 21", "2017-02-21", 0.98)
    # no i360_priority_date at all
    run_rules(graph, [OVERSTAY_01])
    assert graph.get("applicant.part9.violated_nonimmigrant_status") is None


def test_overstay_01_does_not_fire_on_an_unresolved_conflict():
    # A conflicting (not resolved) input must not be treated as good enough
    # to derive a legal-materiality answer from.
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.i94_admit_until_date", "i94.pdf", "i94", "2017 February 21", "2017-02-21", 0.98)
    graph.add_source("applicant.i94_admit_until_date", "old_intake.pdf", "intake_form", "2018-01-01", "2018-01-01", 0.3)
    graph.add_source("applicant.i360_priority_date", "i360_approval.pdf", "i360_approval", "10/14/2022", "2022-10-14", 0.98)
    run_rules(graph, [OVERSTAY_01])
    assert graph.get("applicant.part9.violated_nonimmigrant_status") is None


def test_name_01_fires_when_marriage_certificate_name_resolves():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.birth_name", "birth_certificate.pdf", "birth_certificate", "Sabrina Moura Almeida", "Sabrina Moura Almeida", 0.9)
    graph.add_source(
        "applicant.marriage_certificate_name", "marriage_certificate.pdf", "marriage_certificate",
        "Sabrina Moura Almeida Sampaio", "Sabrina Moura Almeida Sampaio", 0.9,
    )
    run_rules(graph, [NAME_01])
    fact = graph.get("applicant.current_legal_name")
    assert fact.value == "Sabrina Moura Almeida Sampaio"
    assert fact.derived_by == "NAME-01"


def test_citizenship_01_fires_when_two_passports_disagree():
    # Real example case: I-94 and the Italian passport both say Italy, the
    # Brazilian passport says Brazil -- a real conflict, resolved by
    # country of birth per the paralegal's confirmation (docs/decisions.md).
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.citizenship", "i94.pdf", "i94", "Italy", "ITALY", 0.95)
    graph.add_source("applicant.citizenship", "italian_passport.pdf", "passport", "ITALIANA", "ITALY", 0.9)
    graph.add_source("applicant.citizenship", "brazilian_passport.pdf", "passport", "BRASILEIRO(A)", "BRAZIL", 0.9)
    assert graph.get("applicant.citizenship").status == "conflict"

    graph.add_source(
        "applicant.country_of_birth", "birth_certificate.pdf", "birth_certificate",
        "FEDERATIVE REPUBLIC OF BRAZIL", "BRAZIL", 0.9,
    )
    run_rules(graph, [CITIZENSHIP_01])

    fact = graph.get("applicant.citizenship")
    assert fact.status == "resolved"
    assert fact.value == "BRAZIL"
    assert fact.tier == 2
    assert fact.derived_by == "CITIZENSHIP-01"


def test_citizenship_01_does_not_fire_for_a_single_citizenship():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.citizenship", "i94.pdf", "i94", "Brazil", "BRAZIL", 0.95)
    graph.add_source(
        "applicant.country_of_birth", "birth_certificate.pdf", "birth_certificate",
        "FEDERATIVE REPUBLIC OF BRAZIL", "BRAZIL", 0.9,
    )
    run_rules(graph, [CITIZENSHIP_01])

    fact = graph.get("applicant.citizenship")
    assert fact.status == "resolved"
    assert fact.tier == 1  # untouched -- the rule never fired
    assert fact.derived_by is None


def test_citizenship_01_does_not_fire_without_a_country_of_birth():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.citizenship", "i94.pdf", "i94", "Italy", "ITALY", 0.95)
    graph.add_source("applicant.citizenship", "brazilian_passport.pdf", "passport", "BRASILEIRO(A)", "BRAZIL", 0.9)
    run_rules(graph, [CITIZENSHIP_01])

    fact = graph.get("applicant.citizenship")
    assert fact.status == "conflict"  # never resolved -- country_of_birth never arrived


def test_name_01_does_not_fire_without_a_marriage_certificate():
    # Per the 2026-09-29 decisions.md entry: no marriage certificate means
    # no override, full stop.
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.birth_name", "birth_certificate.pdf", "birth_certificate", "Sabrina Moura Almeida", "Sabrina Moura Almeida", 0.9)
    run_rules(graph, [NAME_01])
    assert graph.get("applicant.current_legal_name") is None


def test_topological_order_runs_producer_before_consumer():
    graph = FactGraph("client")
    graph.add_source("input.a", "doc.pdf", "doc", "1", 1, 0.9)

    produces_b = Rule(rule_id="R1", inputs=["input.a"], outputs=["derived.b"], apply=lambda g: {"derived.b": "x"})
    consumes_b = Rule(rule_id="R2", inputs=["derived.b"], outputs=["derived.c"], apply=lambda g: {"derived.c": "y"})

    # Declared out of dependency order -- engine must still run R1 first.
    ordered = topological_order([consumes_b, produces_b])
    assert [r.rule_id for r in ordered] == ["R1", "R2"]

    run_rules(graph, [consumes_b, produces_b])
    assert graph.get("derived.c").value == "y"
    assert graph.get("derived.c").derived_from == ["derived.b"]


def test_topological_order_raises_on_a_cycle():
    r1 = Rule(rule_id="R1", inputs=["derived.c"], outputs=["derived.b"], apply=lambda g: {})
    r2 = Rule(rule_id="R2", inputs=["derived.b"], outputs=["derived.c"], apply=lambda g: {})
    with pytest.raises(ValueError):
        topological_order([r1, r2])


def test_all_rules_runs_cleanly_against_the_full_example_case():
    graph = _overstayed_graph()
    graph.add_source("applicant.birth_name", "birth_certificate.pdf", "birth_certificate", "Sabrina Moura Almeida", "Sabrina Moura Almeida", 0.9)
    graph.add_source(
        "applicant.marriage_certificate_name", "marriage_certificate.pdf", "marriage_certificate",
        "Sabrina Moura Almeida Sampaio", "Sabrina Moura Almeida Sampaio", 0.9,
    )
    run_rules(graph, ALL_RULES)
    assert graph.get("applicant.part9.violated_nonimmigrant_status").value == "Yes"
    assert graph.get("applicant.current_legal_name").value == "Sabrina Moura Almeida Sampaio"


# -- readable where they are approved (docs/design_plan.md Part 4.2) -----------------------------



def test_every_rule_and_policy_has_plain_text_and_a_source():
    for rule in ALL_RULES:
        assert len(rule.plain_text) > 40 and rule.plain_text.endswith("."), rule.rule_id
        assert rule.source.startswith("firm practice, see decisions.md 2026-"), rule.rule_id
    policies = json.loads((schema_path.path("law", "policy_sijs")).read_text(encoding="utf-8"))["policies"]
    for p in policies:
        assert len(p.get("plain_text") or "") > 30 and p["plain_text"].endswith((".", ")")), p["id"]
        assert p.get("source"), p["id"]
        assert "->" not in p["plain_text"] and " -- " not in p["plain_text"] and "—" not in p["plain_text"], p["id"]


def test_policies_are_in_this_firms_words():
    """Fictional fixture helper."""
    text = (schema_path.path("law", "policy_sijs")).read_text(encoding="utf-8")
    assert not re.search(r"client \d", text, flags=re.I)
    for p in json.loads(text)["policies"]:
        for words in (p.get("why", ""), p["plain_text"]):
            assert not re.search(r"client \d", words, flags=re.I), (p["id"], words)


def test_the_screen_says_the_rule_its_source_and_its_approval(tmp_path, monkeypatch):
    from review.state import rule_info
    from rules import approval

    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    info = rule_info("OVERSTAY-01")
    assert info["name"].startswith("the overstay rule") and "admit-until date" in info["plain_text"]
    # a buying firm has no decision log and made no practice: the rule is the product's, until its attorney approves it
    assert info["source"] == "Built-in practice, taken from filed forms the product was built on (recorded 09/29/2026). It is not your firm's practice until an attorney approves it for every case"
    assert "decision log" not in info["source"] and "decisions.md" not in info["source"] and "firm practice" not in info["source"].lower()
    assert info["approval"] == {"state": "not_approved"} and info["approval_text"] == "Not yet approved"
    policy = rule_info("POLICY:NO-CREWMAN")
    assert policy["kind"] == "policy" and policy["plain_text"].startswith("The firm's standard answer")

    with pytest.raises(ValueError):
        approval.approve("OVERSTAY-01", "  ")  # every approval records who gave it
    with pytest.raises(LookupError):
        approval.approve("NO-SUCH-RULE", "Ana Attorney")
    state = approval.approve("OVERSTAY-01", "Ana Attorney", "attorney")
    assert state["state"] == "approved" and state["by"] == "Ana Attorney"
    info = rule_info("OVERSTAY-01")
    assert re.fullmatch(r"Approved by Ana Attorney on \d{2}/\d{2}/\d{4}", info["approval_text"])
    saved = json.loads((tmp_path / "rules_approved.json").read_text())["OVERSTAY-01"][-1]
    assert saved["plain_text"] == OVERSTAY_01.plain_text and len(saved["hash"]) == 64

    # the rule's text changes after the approval: it says so until an attorney approves it again
    monkeypatch.setattr(OVERSTAY_01, "plain_text", OVERSTAY_01.plain_text + " Changed.")
    approval._catalog.cache_clear()
    try:
        info = rule_info("OVERSTAY-01")
        assert info["approval"]["state"] == "changed" and info["approval_text"].startswith("Changed since approval (approved by Ana Attorney on ")
        assert approval.statuses()["POLICY:NO-CREWMAN"]["state"] == "not_approved"
    finally:
        monkeypatch.undo()
        approval._catalog.cache_clear()


def test_only_an_attorney_approves_a_rule_for_every_case(tmp_path, monkeypatch):
    from review.server import ReviewApp

    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    (tmp_path / "clients").mkdir()
    app = ReviewApp(tmp_path / "clients", schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None)
    with pytest.raises(PermissionError):
        app.rules_approve({"rule": "NAME-01", "reviewer": "Paulo Paralegal"}, "paralegal")
    assert not (tmp_path / "rules_approved.json").exists()
    rules = {r["id"]: r for r in app.rules_approve({"rule": "NAME-01", "reviewer": "Ana Attorney"}, "attorney")["rules"]}
    assert rules["NAME-01"]["approval"]["state"] == "approved" and rules["CITIZENSHIP-01"]["approval"]["state"] == "not_approved"
    assert {"OVERSTAY-01", "NTA-01", "CRIM-01", "POLICY:PART9-DEFAULT-NO"} <= set(rules)


def test_every_policy_has_a_short_name_shown_with_its_code():
    from review.state import rule_info

    raw = (schema_path.path("law", "policy_sijs")).read_text(encoding="utf-8")
    policies = json.loads(raw)["policies"]
    for p in policies:
        assert 3 < len(p.get("name") or "") <= 50 and p["name"] != p["id"], p["id"]
    assert len({p["name"] for p in policies}) == len(policies)
    assert rule_info("POLICY:NA-OTHER-NAMES")["name"] == "Other names used: none"
    assert json.dumps(json.loads(raw), indent=2, ensure_ascii=False) + "\n" == raw  # re-dumps byte for byte


def test_rule_texts_say_no_more_than_the_code_does():
    from rules import NTA_01

    assert "divorce decree" not in NAME_01.plain_text and "marriage certificate" in NAME_01.plain_text
    assert "leaves Item 73 to a person" in NTA_01.plain_text and "No if so" not in NTA_01.plain_text
    reasons = [p["why"] for p in json.loads((schema_path.path("law", "policy_sijs")).read_text(encoding="utf-8"))["policies"]]
    assert sum("as on the firm's earlier filed I-485" in w for w in reasons) == 5
