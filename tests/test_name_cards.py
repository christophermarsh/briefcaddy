"""The two name cards (wave K, brief K1): "Which name is current?" with every name side by side and each document open at its page,
the attorney's "USCIS knows the client by another name", the packet held until both are saved, Save and Undo, and the attorney's
card closed to a paralegal. A made-up client (the Exemplo family) married after her I-360 was approved."""

from __future__ import annotations

from pathlib import Path

import pytest

import name_events
import packet
from synthetic_documents import process_retained_documents
from extract.base import ExtractedField
from fill import load_field_map
from review.auth import needs_attorney
from review.overview import review_row
from review.server import ReviewApp
from review.state import Catalog, build_items, load_decision_log, record_decision, reviewed_graph, save_bundle, undo_decision
from rules.policy import load_policy_profile
from test_name_events import I360, MA_AFTER
import schema_path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = schema_path.path("template", "i485")
FIELD_MAP = load_field_map(schema_path.path("field_map", "i485"))
CATALOG = Catalog(FIELD_MAP, TEMPLATE)
CURRENT, USCIS = "names:applicant.name_current", "names_uscis:applicant.name_uscis_ok"
MARRIED, EARLIER = "ANA CLARA EXEMPLO SOUZA TESTE", "ANA CLARA EXEMPLO SOUZA"
CERTIFICATE = "CERTIDAO DE NASCIMENTO\nREGISTRO CIVIL\nANA CLARA EXEMPLO SOUZA\n14/03/2006"


def _answers():
    said = {"applicant.given_name": ("Ana Clara", "ANA CLARA"), "applicant.family_name": ("Exemplo Souza", "EXEMPLO SOUZA"),
            "questionnaire.used_other_names": ("No", "No"), "questionnaire.blank.other_names": ("No", "Yes")}
    return [ExtractedField(k, raw, value, 0.95) for k, (raw, value) in said.items()]


def _retained_name_case(tmp_path, marriage, *, applicant_party="party_a", pages=None, client="exemplo"):
    """Keep the old declared manual reads, backed by retained fictional bytes.

    A named subject decision is distinct from field/name approval. Unsupported
    notice-recipient reads stay explicitly reference-only; no ownership guess.
    """
    import documents
    import subject_attribution as subjects
    source = tmp_path / "source"
    source.mkdir()
    result = process_retained_documents(client, source, [("i360.pdf", I360), ("casamento.pdf", marriage)], pages=pages, answers=_answers(),
                               manual_evidence=[("certidao.pdf", CERTIFICATE)],
                               policies=load_policy_profile(schema_path.path("law", "policy_sijs"), firm=False))
    part = result.boundary_plans["certidao.pdf"]["instances"][0]
    assert part["first"] == part["last"] == 0 and part["type"] == "birth_certificate" and part["state"] == "supported"
    # These two reads were always deliberately supplied by this fixture. Their
    # text is on the retained sole page; no product reader approval is invented.
    manual = [ExtractedField("applicant.birth_certificate_name", EARLIER, EARLIER, 0.8, page=0),
              ExtractedField("applicant.dob", "14/03/2006", "2006-03-14", 0.9, page=0)]
    for graph in (result.graph, result.raw_graph):
        for field in manual:
            graph.add_source(field.fact_key, "certidao.pdf", "birth_certificate", field.raw_value, field.normalized_value,
                             field.confidence, page=field.page, **subjects.provenance(part, "birth_certificate", field))
    out = tmp_path / "clients" / client
    save_bundle(result, out, source)
    actor = "Fictional Setup Reviewer"
    applicant = next(person for person in documents.read(out)["case_subjects"]["people"] if person["case_role"] == "applicant")
    subjects.rename_person(out, applicant["id"], "Ana Clara Exemplo Souza", actor, "paralegal")
    spouse = subjects.add_person(out, "Joao Pedro Teste", "spouse", actor, "paralegal")
    marriage_targets = {applicant_party: applicant["id"], "party_b" if applicant_party == "party_a" else "party_a": spouse["id"]}
    if applicant_party == "party_b":  # this fixture explicitly prints the client in Party B's column
        mother = subjects.add_person(out, "Maria Exemplo Souza", "mother", actor, "paralegal")
        father = subjects.add_person(out, "Jose Exemplo Souza", "father", actor, "paralegal")
        spouse_mother = subjects.add_person(out, "Rosa Teste Dias (Joao's mother)", "other", actor, "paralegal")
        spouse_father = subjects.add_person(out, "Carlos Teste Ruiz (Joao's father)", "other", actor, "paralegal")
        marriage_targets.update(party_b_parent1=mother["id"], party_b_parent2=father["id"],
                                party_a_parent1=spouse_mother["id"], party_a_parent2=spouse_father["id"])
    targets = {"birth_certificate": {"birth_subject": applicant["id"]},
               "i360_approval": {"beneficiary": applicant["id"], "case_subject": applicant["id"]},
               "marriage_certificate": marriage_targets}
    for row in subjects.views(out):
        assert row["bound"] and row["type"] in targets, row
        assert set(row["slots"]) <= set(targets[row["type"]]), row
        references = [fact["evidence_version"] for fact in row["facts"] if fact["role"] == "unmapped"]
        subjects.assign(out, row["instance_id"], row["fingerprint"], {slot: targets[row["type"]][slot] for slot in row["slots"]},
                        actor, "paralegal", reference_edges=references,
                        note="Unsupported recipient identity stays reference-only; printed named roles reviewed." if references else "")
    return out


@pytest.fixture
def case(tmp_path):
    return _retained_name_case(tmp_path, MA_AFTER)


def _items(d):
    return {i["id"]: i for i in build_items(d, FIELD_MAP, TEMPLATE, CATALOG, pending=False)["open"]}


def _independent_name_source_checks(d):
    """A legal-name choice does not authenticate independent retained reads."""
    import critical_review
    items = _items(d)
    keys = {"applicant.given_name", "applicant.family_name"}
    for key in keys:
        card = items["fact:" + key]
        assert card["kind"] == "fact" and not card.get("timeline")
        assert card["source_review"]["basis"] == "manual_retained_source_review"
        assert card["evidence_fingerprints"].get(key)
        assert any(f["key"] == key for f in card["facts"])
    assert all(f"Critical source review is still required: {key}." in critical_review.problems(d) for key in keys)
    row = review_row(d, FIELD_MAP, TEMPLATE, CATALOG)
    problems = packet.plan(d, row, packet.load_filing("i485"))["problems"]
    assert all(any(f"Critical source review is still required: {key}." in p for p in problems) for key in keys)
    return items


def _set(d, iid, value, who="Pat Paralegal", note=""):
    return record_decision(d, _items(d)[iid], {"reviewer": who, "action": "set", "values": {_items(d)[iid]["facts"][0]["key"]: value}, "note": note})


def _value(graph, key):
    f = graph.get(key)
    return f.value if f is not None and f.status == "resolved" else None


def test_which_name_is_current_shows_every_name_side_by_side_with_the_pick_and_why(case):
    items = _items(case)
    card = items[CURRENT]
    assert card["title"] == "Which name is current?" and card["group"] == "needs_input" and card["actions"] == ["set"]
    fact = card["facts"][0]
    assert fact["input"]["type"] == "choice" and fact["input"]["options"] == [MARRIED, EARLIER] and fact["value"] == MARRIED
    timeline = card["timeline"]
    assert [(e["name"], e["date"], e["from_document"]) for e in timeline["events"]] == [
        (EARLIER, "03/14/2006", True), (EARLIER, "08/20/2025", True), (MARRIED, "07/01/2026", True), (EARLIER, None, False)]
    assert [(e["doc"], e["page"]) for e in timeline["events"][:3]] == [("certidao.pdf", 0), ("i360.pdf", 0), ("casamento.pdf", 0)]
    assert timeline["current"] == MARRIED and timeline["others"] == [EARLIER]
    assert "marriage certificate of 07/01/2026" in timeline["why"]
    words = " ".join(card["messages"])
    assert "never used another name, but a document shows one" in words and "Part 1, Item 2" in words
    for text in [words, timeline["why"], card["title"]]:
        assert "—" not in text and " -- " not in text and "applicant." not in text and ".pdf" not in text
    cards = {c["id"]: c for c in build_items(case, FIELD_MAP, TEMPLATE, CATALOG, pending=False)["cards"]}
    assert cards[CURRENT]["tab"] == "fix" and cards[CURRENT]["timeline"]["current"] == MARRIED


def test_the_attorney_card_says_uscis_knows_her_as_x_and_this_filing_says_y(case):
    card = _items(case)[USCIS]
    assert card["title"] == "USCIS knows the client by another name" and card["group"] == "attorney"
    words = " ".join(card["messages"])
    assert f"USCIS knows the client as {EARLIER}" in words and f"this filing will say {MARRIED}" in words
    assert "The attorney decides whether an explanation or evidence" in words and "Nothing is filed as a name change request" in words
    assert card["docs"] == ["i360.pdf", "casamento.pdf"]  # both documents, open side by side
    assert card["timeline"]["uscis"][0]["date"] == "08/20/2025"
    also = card["facts"][1]  # listing USCIS's spelling in item 2 is the attorney's choice on this card, never automatic
    assert also["key"] == "applicant.name_uscis_also" and also["input"]["options"] == [EARLIER] and also["value"] is None
    assert needs_attorney(card, "set") and not needs_attorney(_items(case)[CURRENT], "set")


def test_the_packet_waits_until_both_cards_are_saved(case):
    schema = packet.load_filing("i485")
    row = review_row(case, FIELD_MAP, TEMPLATE, CATALOG)
    assert row["names_open"] == ["Which name is current?", "USCIS knows the client by another name"]
    problems = packet.plan(case, row, schema)["problems"]
    assert 'The card "Which name is current?" is not saved yet: the client\'s name on every form waits on it.' in problems
    _set(case, CURRENT, MARRIED)
    _set(case, USCIS, MARRIED, who="Ana Attorney", note="Marriage certificate goes in the packet with the I-485.")
    row = review_row(case, FIELD_MAP, TEMPLATE, CATALOG)
    assert row["names_open"] == []
    assert not any("is not saved yet" in p for p in packet.plan(case, row, schema)["problems"])


def test_save_settles_the_names_and_undo_reopens_the_card(case):
    _set(case, CURRENT, EARLIER, note="She still signs with her earlier name; the attorney checks the certificate.")
    assert CURRENT not in _items(case)
    g = reviewed_graph(case)
    assert (_value(g, "applicant.given_name"), _value(g, "applicant.family_name")) == ("ANA CLARA", "EXEMPLO SOUZA")
    assert (_value(g, "applicant.other_name1_given"), _value(g, "applicant.other_name1_family")) == ("ANA CLARA", "EXEMPLO SOUZA TESTE")
    assert USCIS not in _items(case)  # the filing now says what USCIS knows: nothing for the attorney to decide
    entry = load_decision_log(case)[CURRENT]
    assert entry["reviewer"] == "Pat Paralegal" and entry["values"] == {"applicant.name_current": EARLIER}
    assert entry["item"]["title"] == "Which name is current?"
    undo_decision(case, CURRENT, "Pat Paralegal")
    assert CURRENT in _items(case) and load_decision_log(case)[CURRENT]["undone"]["by"] == "Pat Paralegal"
    g = reviewed_graph(case)
    assert _value(g, "applicant.family_name") == "EXEMPLO SOUZA TESTE"  # back to the product's pick


def test_the_case_page_shows_the_clients_names_and_the_clients_own_page_says_nothing_of_them(case):
    import json

    import journey

    j = journey.journey(case)
    names = j["names"]
    assert names["current"] == MARRIED and names["others"] == [EARLIER]
    assert [e["what"] for e in names["events"]] == ["Birth certificate", "USCIS I-360 approval notice", "Marriage certificate",
                                                    "The client's own answer: name"]
    for lang in ("pt", "es", "en", "ht"):  # names are the office's work: the client's "your case" card is silent about them
        said = json.dumps(journey.client_view(j, lang), ensure_ascii=False).upper()
        assert "TESTE" not in said and "EXEMPLO" not in said, lang


def test_the_attorneys_card_is_closed_to_a_paralegal_and_saved_under_the_attorneys_name(case):
    app = ReviewApp(case.parent, schema_path.path("field_map", "i485"), TEMPLATE, None)
    body = {"reviewer": "Pat Paralegal", "decisions": [{"item_id": USCIS, "action": "set", "values": {"applicant.name_uscis_ok": MARRIED}}]}
    with pytest.raises(PermissionError):
        app.decide("exemplo", body, role="paralegal")
    app.decide("exemplo", body | {"reviewer": "Ana Attorney"}, role="attorney")
    assert USCIS not in _items(case)
    g = reviewed_graph(case)
    assert g.get("applicant.name_uscis_ok").review.resolved_by == "Ana Attorney"
    assert name_events.flags(g) == [f for f in name_events.flags(g) if f.kind == "names"]  # only the paralegal's card is still open


def test_every_decision_carries_its_own_date_onto_the_review():
    """Verification of K6, S6: confirm, set, blank and an absence mark are each dated by the decision, not by the last reading."""
    from factgraph import FactGraph
    from review.state import apply_decisions

    g = FactGraph("exemplo")
    for key in ("a.one", "a.two", "a.three"):
        g.add_source(key, "doc.pdf", "passport", "X", "X", 0.9)
    at = "2026-09-01T09:30:00-04:00"
    item = lambda *keys: {"facts": list(keys)}  # noqa: E731
    apply_decisions(g, {"1": {"action": "confirm", "reviewer": "Pat", "at": at, "item": item("a.one")},
                        "2": {"action": "set", "reviewer": "Pat", "at": at, "values": {"a.two": "Y"}, "item": item("a.two")},
                        "3": {"action": "blank", "reviewer": "Pat", "at": at, "item": item("a.three")},
                        "4": {"action": "absent", "reviewer": "Pat", "at": at, "absence": {"paper": "passport", "reason": "never_had"}, "item": item()}})
    assert [g.get(k).review.resolved_at for k in ("a.one", "a.two", "a.three", "case.absent.passport")] == [at] * 4


# --- K6: the marriage certificate prints no name after marriage: the card asks, with two empty boxes ------------------------------

from test_name_events import ASKED, MA_REAL_SHAPE  # noqa: E402

TYPED = ("applicant.name_chosen_given", "applicant.name_chosen_family")


@pytest.fixture
def asked(tmp_path):
    return _retained_name_case(tmp_path, MA_REAL_SHAPE, applicant_party="party_b")


def test_the_card_asks_with_nothing_picked_and_two_empty_boxes(asked):
    card = _items(asked)[CURRENT]
    assert card["title"] == "Which name is current?" and card["actions"] == ["set"]
    assert ASKED in " ".join(card["messages"])
    choice, given, family = card["facts"]
    assert choice["input"]["options"] == [EARLIER] and choice["value"] is None  # nothing is picked for the reviewer
    assert choice["input"]["labels"][EARLIER] == f"{EARLIER}: the name as it stands"
    assert [given["key"], family["key"]] == list(TYPED) and given["value"] is None and family["value"] is None  # empty: prefilled with nothing
    assert not given.get("suggest") and not family.get("suggest")
    assert "TESTE" in family["help"]["text"] and "for information only" in family["help"]["text"]  # the spouse's name beside the box, never a pick
    assert "TESTE" not in " ".join(choice["input"]["options"])
    assert "casamento.pdf" in card["docs"] and card["timeline"]["question"]["open"] is True
    # One legal-name choice, with independent EV3 source checks still open.
    items = _independent_name_source_checks(asked)
    assert [iid for iid, i in items.items() if i["kind"] == "names"] == [CURRENT]
    assert USCIS not in items  # the attorney's legal-name comparison awaits the choice
    with pytest.raises(ValueError):  # the married name cannot be chosen as an option: it is not one the folder proves
        record_decision(asked, card, {"reviewer": "Pat Paralegal", "action": "set", "values": {"applicant.name_current": MARRIED}})
    row = review_row(asked, FIELD_MAP, TEMPLATE, CATALOG)
    assert row["names_open"] == ["Which name is current?"]  # the packet waits; the attorney's card waits for the choice
    assert 'The card "Which name is current?" is not saved yet' in " ".join(packet.plan(asked, row, packet.load_filing("i485"))["problems"])


def test_a_typed_name_is_saved_under_the_reviewers_name_settles_every_form_and_undo_reopens(asked):
    import events

    app = ReviewApp(asked.parent, schema_path.path("field_map", "i485"), TEMPLATE, None)
    app.decide("exemplo", {"reviewer": "Pat Paralegal", "note": "the client signs with her married name; certificate checked",
                           "decisions": [{"item_id": CURRENT, "action": "set", "values": {TYPED[0]: "Ana Clara", TYPED[1]: "Exemplo Souza Teste"}}]},
               role="paralegal")
    assert CURRENT not in _items(asked)
    entry = load_decision_log(asked)[CURRENT]
    assert entry["reviewer"] == "Pat Paralegal" and entry["values"] == {TYPED[0]: "ANA CLARA", TYPED[1]: "EXEMPLO SOUZA TESTE"}
    g = reviewed_graph(asked)
    assert (_value(g, "applicant.given_name"), _value(g, "applicant.family_name")) == ("ANA CLARA", "EXEMPLO SOUZA TESTE")
    assert (_value(g, "applicant.other_name1_given"), _value(g, "applicant.other_name1_family")) == ("ANA CLARA", "EXEMPLO SOUZA")
    person = next(e for e in name_events.payload(g)["events"] if e["kind"] == "person")
    assert person["who"] == "Pat Paralegal" and person["date"] == entry["at"][:10]  # dated by the decision
    assert USCIS in _items(asked)  # the I-360 approval knows her by the birth name: now the attorney decides, as K1 has it
    items = _independent_name_source_checks(asked)
    assert not [i for i in items.values() if i["kind"] == "names"]  # no repeated legal-name question
    assert not entry.get("evidence_confirmation")  # typed legal-name choice is not source approval
    assert (_value(reviewed_graph(asked), "applicant.given_name"), _value(reviewed_graph(asked), "applicant.family_name")) == (
        "ANA CLARA", "EXEMPLO SOUZA TESTE")
    rows = [r for r in events.rows(events.base_path(asked.parent.parent), case="exemplo") if r["kind"] == "decisions"]
    assert rows and rows[-1]["who"] == "Pat Paralegal" and "TESTE" not in rows[-1]["what"]  # a ledger row, never the name itself
    undo_decision(asked, CURRENT, "Pat Paralegal")
    assert CURRENT in _items(asked) and _value(reviewed_graph(asked), "applicant.family_name") == "EXEMPLO SOUZA"


def test_the_choice_records_what_was_on_the_card_and_a_document_that_arrives_after_reopens_it(asked):
    """Re-verification of K6, F2, through the review app: a court order dated before the choice but added after it reopens the card."""
    import json
    import inbox
    from synthetic_documents import retain_documents
    from test_name_events import ORDER

    app = ReviewApp(asked.parent, schema_path.path("field_map", "i485"), TEMPLATE, None)
    app.decide("exemplo", {"reviewer": "Pat Paralegal", "decisions": [{"item_id": CURRENT, "action": "set",
                                                                      "values": {TYPED[0]: "Ana Clara", TYPED[1]: "Exemplo Souza Teste"}}]}, role="paralegal")
    assert load_decision_log(asked)[CURRENT]["over"] == ["certidao.pdf"] and CURRENT not in _items(asked)
    # The newly arrived order has actual retained bytes and uses the same
    # boundary/ingestion path as a staff upload, keeping its original alias.
    folder = Path(json.loads((asked / "meta.json").read_text(encoding="utf-8"))["source_folder"])
    docs = [("decreto.pdf", ORDER)]
    retain_documents(folder, docs)
    inbox.reprocess_documents(asked, folder, docs, {"decreto.pdf": {"pages": 1}}, asked.parent / "index.db",
                              source="folder", who="Pat Paralegal", pages={"decreto.pdf": [ORDER]})
    import documents
    import subject_attribution as subjects
    decree = next(row for row in subjects.views(asked) if row["file"] == "decreto.pdf")
    assert decree["bound"] and decree["type"] == "name_change_order" and set(decree["slots"]) == {"holder"}
    assert "applicant.name_change.new_name" in subjects.affected_keys(asked)
    assert any(f["state"] == "pending" for f in decree["facts"])  # new evidence is held before named attribution
    applicant = next(p for p in documents.read(asked)["case_subjects"]["people"] if p["case_role"] == "applicant")
    subjects.assign(asked, decree["instance_id"], decree["fingerprint"], {"holder": applicant["id"]}, "Pat Paralegal", "paralegal")
    assert "applicant.name_change.new_name" not in subjects.affected_keys(asked)
    from factgraph import FactGraph
    materialized = FactGraph.load(asked / "fact_graph.json")
    current_decree = next(row for row in subjects.views(asked, materialized) if row["file"] == "decreto.pdf")
    assert current_decree["bound"] and current_decree["current"]
    split = next(source for source in materialized.get("applicant.family_name").sources if source.doc_id == "decreto.pdf")
    assert split.from_facts == ["applicant.name_change.new_name"] and split.input_evidence
    assert split.instance_id is None and split.evidence_version is None  # no fabricated original edge
    card = _items(asked)[CURRENT]
    assert "A document that was not on the card when the choice was made has arrived" in " ".join(card["messages"])
    g = reviewed_graph(asked)
    assert _value(g, "applicant.family_name") == "EXEMPLO SOUZA TESTE" and _value(g, "applicant.other_name1_family") == "EXEMPLO SOUZA"
