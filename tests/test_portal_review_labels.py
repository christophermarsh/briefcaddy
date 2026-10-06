"""Typed answers must identify their source question and subfield."""
from factgraph import FactGraph
from fill import load_field_map
from review.state import Catalog, _fact_view, build_cards
import schema_path


def test_portal_postal_code_names_job_question_and_field():
    graph = FactGraph("fictional")
    key = "applicant.foreign_employer_postal_code"
    graph.add_source(key, "portal questionnaire", "intake_questionnaire", "01931", "01931", .95, tier=3)
    catalog = Catalog(load_field_map(schema_path.path("field_map", "i485")), schema_path.path("template", "i485"))
    fact = _fact_view(graph, key, catalog)
    assert fact["portal_question"]["id"] == "last_foreign_job"
    assert fact["portal_question"]["label"] == "Your last job or school before coming to the U.S."
    assert fact["portal_question"]["field"] == "ZIP / postal code"
    assert "last job or school" in fact["short"] and "ZIP / postal code" in fact["short"]
    item = {"id": "fictional-item", "kind": "fact", "group": "questionnaire", "title": "Postal code", "level": "review", "facts": [dict(fact, ref=None)],
            "evidence": [], "actions": ["confirm", "set"], "messages": [], "docs": []}
    cards = build_cards([item])
    assert len(cards) == 1
    assert cards[0]["title"] == "Your last job or school before coming to the U.S."
    assert cards[0]["id"] == "portal:question:last_foreign_job"


def test_unmapped_unrelated_portal_facts_are_separate_cards():
    def item(key):
        return {"id": key, "kind": "fact", "group": "questionnaire", "title": key, "level": "review", "facts": [
            {"key": key, "short": key, "ref": None, "sources": [{"doc": "portal questionnaire"}]}],
            "evidence": [], "actions": ["confirm"], "messages": [], "docs": []}
    cards = build_cards([item("fictional.first"), item("fictional.second")])
    assert len(cards) == 2 and all("Other answers" not in c["title"] for c in cards)
