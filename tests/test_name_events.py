"""The client's names as a timeline (wave K, brief K1): the name after marriage read from the certificate's own field, the
events in order, the settled current name, the other names, the questionnaire's "no" outranked by a document, particles set
aside, and NOT APPLICABLE only when every event agrees. Everyone here is made up (the Exemplo family); the certificate's SHAPE is
the Massachusetts one the reader was built on (tests/test_people.py), with its "Surname after Marriage" row."""

from __future__ import annotations

from pathlib import Path

import pytest

import name_events
from assemble import assemble
from extract.i360_approval import extract as extract_i360
from extract.marriage_certificate import extract as extract_marriage
from extract.name_change_order import extract as extract_order
from extract.uscis_notice import extract as extract_notice
from extract.base import ExtractedField
from factgraph import FactGraph
import schema_path

ROOT = Path(__file__).resolve().parents[1]

# A made-up Massachusetts certificate: one row per field, party A's column then party B's, as OCR reads the real layout.
MA_AFTER = """The Commonwealth of Massachusetts
Certificate of Marriage
Date of Marriage: JULY 1, 2026 Place of Marriage: WORCESTER, MA
Party A Party B
Name: ANA CLARA EXEMPLO SOUZA Name: JOAO PEDRO TESTE
Surname after Marriage: EXEMPLO SOUZA TESTE Surname after Marriage: TESTE ip
Residence: 10 EXAMPLE STREET, #3, WORCESTER, MA 10 EXAMPLE STREET, #3, WORCESTER, MA
Date of Birth: MARCH 14, 2006 Date of Birth: MAY 2, 2004
Place of Birth: SAO PAULO, BRAZIL Place of Birth: BOGOTA, COLOMBIA
Number of Marriage: FIRST Number of Marriage: FIRST
"""
MA_NO_FIELD = MA_AFTER.replace("Surname after Marriage: EXEMPLO SOUZA TESTE Surname after Marriage: TESTE ip\n", "")
MA_DASHED = MA_AFTER.replace("EXEMPLO SOUZA TESTE Surname after Marriage: TESTE ip", "----- Surname after Marriage: SAME")
MA_FULL_NAME = MA_AFTER.replace("Surname after Marriage: EXEMPLO SOUZA TESTE Surname after Marriage: TESTE ip",
                                "Name after Marriage: ANA CLARA EXEMPLO SOUZA TESTE Name after Marriage: JOAO PEDRO TESTE")

# A made-up I-360 approval in the I-797 layout (src/extract/uscis_notice.py's docstring).
I360 = """Department of Homeland Security
U.S. Citizenship and Immigration Services
I-797, NOTICE OF ACTION
Receipt Number Case Type
IOE9000000001 1360 - PETITION FOR AMERASIAN, WIDOW(ER), OR SPECIAL IMMIGRANT
Received Date Priority Date Petitioner A123 456 789
01/02/2025 01/02/2025 EXEMPLO SOUZA, ANA CLARA
Notice Date Page Beneficiary A123 456 789

08/20/2025 1 of 1 EXEMPLO SOUZA, ANA CLARA
Notice Type: Approval Notice
"""
# A made-up I-765 receipt for the client, after the wedding, under the new name.
I765_RECEIPT = """Department of Homeland Security
U.S. Citizenship and Immigration Services
I-797C, NOTICE OF ACTION
Receipt Number Case Type
IOE9000000002 I765 - APPLICATION FOR EMPLOYMENT AUTHORIZATION
Received Date Priority Date Applicant A123 456 789
09/01/2026
Notice Date Page Applicant
09/05/2026 1 of 1 EXEMPLO SOUZA TESTE, ANA CLARA
Notice Type: Receipt Notice
"""
ORDER = """COMMONWEALTH OF MASSACHUSETTS
DECREE OF CHANGE OF NAME
New Name: ANA CLARA TESTE
Former Name: ANA CLARA EXEMPLO SOUZA TESTE
Date: 09/15/2026
"""


def _add(g: FactGraph, doc: str, doc_type: str, fields, tier: int = 1) -> None:
    for f in fields:
        g.add_source(f.fact_key, doc, doc_type, f.raw_value, f.normalized_value, f.confidence, tier=tier)


def _graph(*, marriage: str | None = MA_AFTER, typed: tuple[str, str] | None = ("Ana Clara", "Exemplo Souza"), said_no: bool = True,
           extra: tuple = (), answers: dict | None = None, reviews: dict | None = None) -> FactGraph:
    """answers: more of the client's typed answers (Tier 3); reviews: decisions a person saved on the review screen, dated 10/04/2026."""
    g = FactGraph("exemplo")
    for key, value in (reviews or {}).items():
        g.set_by_review(key, value, "Pat Paralegal", "on the names card").review.resolved_at = "2026-10-04T10:00:00-04:00"
    for key, value in (answers or {}).items():
        g.add_source(key, "portal questionnaire", "intake_questionnaire", value, value.upper() if key.startswith("applicant.") else value, 0.95, tier=3)
    g.add_source("applicant.birth_certificate_name", "certidao.pdf", "birth_certificate", "ANA CLARA EXEMPLO SOUZA", "ANA CLARA EXEMPLO SOUZA", 0.8)
    g.add_source("applicant.dob", "certidao.pdf", "birth_certificate", "14/03/2006", "2006-03-14", 0.9)
    _add(g, "i360.pdf", "i360_approval", extract_i360(I360) + extract_notice(I360))
    if marriage:
        _add(g, "casamento.pdf", "marriage_certificate", extract_marriage(marriage))
    if typed:  # the portal keeps what was typed and compares it in capitals (src/portal/bank.py)
        g.add_source("applicant.given_name", "portal questionnaire", "intake_questionnaire", typed[0], typed[0].upper(), 0.95, tier=3)
        g.add_source("applicant.family_name", "portal questionnaire", "intake_questionnaire", typed[1], typed[1].upper(), 0.95, tier=3)
    if said_no:
        g.add_source("questionnaire.used_other_names", "portal questionnaire", "intake_questionnaire", "No", "No", 0.95, tier=3)
        g.add_source("questionnaire.blank.other_names", "portal questionnaire", "intake_questionnaire", "No", "Yes", 0.95, tier=3)
    for doc, doc_type, fields in extra:
        _add(g, doc, doc_type, fields)
    if reviews and any(key in reviews for key in (name_events.CURRENT_KEY, name_events.CHOSEN_GIVEN, name_events.CHOSEN_FAMILY)):
        # These unit fixtures represent a current names Save. Legacy absence
        # and changed-read behavior have separate explicit regression tests.
        binding = name_events.marriage_evidence(g)
        if binding is not None and name_events.EVIDENCE_KEY not in reviews:
            g.set_by_review(name_events.EVIDENCE_KEY, binding, "Pat Paralegal", "current fictional names Save")
    assemble(g)
    return g


def _v(g: FactGraph, key: str):
    f = g.get(key)
    return f.value if f is not None and f.status == "resolved" else None


# --- the reader --------------------------------------------------------------------------------------------------------


def test_the_massachusetts_certificate_gives_each_partys_surname_after_marriage():
    fields = {f.fact_key: f for f in extract_marriage(MA_AFTER)}
    v = {key: field.normalized_value for key, field in fields.items()}
    assert v["marriage.party_a.surname_after"] == "EXEMPLO SOUZA TESTE"
    assert "marriage.party_b.surname_after" not in v  # Unsupported letters must not be silently dropped.
    assert v["marriage.party_b.after_read_state"] == "unreadable"
    assert "TESTE ip" in fields["marriage.party_b.after_read_state"].raw_value
    assert fields["marriage.party_b.after_read_state"].reading_issues
    assert v["marriage.party_a.name"] == "ANA CLARA EXEMPLO SOUZA"  # the other rows read as before


def test_a_certificate_that_prints_no_such_field_gives_no_name_after_marriage():
    for text in (MA_NO_FIELD, MA_DASHED):
        keys = {f.fact_key for f in extract_marriage(text)}
        assert not any(k.endswith(("surname_after", "name_after")) for k in keys), text
    v = {f.fact_key: f.normalized_value for f in extract_marriage(MA_FULL_NAME)}
    assert v["marriage.party_a.name_after"] == "ANA CLARA EXEMPLO SOUZA TESTE" and "marriage.party_a.surname_after" not in v


def test_a_notice_gives_the_clients_name_under_applicant_or_beneficiary_never_petitioner():
    v = {f.fact_key: f.normalized_value for f in extract_notice(I765_RECEIPT)}
    assert v["folder.notice.IOE9000000002.receipt_20260905.name"] == "EXEMPLO SOUZA TESTE, ANA CLARA"
    family_petition = I765_RECEIPT.replace("Notice Date Page Applicant", "Notice Date Page Petitioner")
    assert not any(f.fact_key.endswith(".name") for f in extract_notice(family_petition))


def test_a_name_change_order_gives_the_new_name_and_its_date():
    v = {f.fact_key: f.normalized_value for f in extract_order(ORDER)}
    assert v["applicant.name_change.new_name"] == "ANA CLARA TESTE"
    assert v["applicant.name_change.former_name"] == "ANA CLARA EXEMPLO SOUZA TESTE"
    assert v["applicant.name_change.date"] == "2026-09-15"
    sentence = "ORDER ON CHANGE OF NAME\nIt is ordered that the name of Ana Clara Exemplo Souza be changed to Ana Clara Teste.\nDated: October 1, 2026\n"
    v = {f.fact_key: f.normalized_value for f in extract_order(sentence)}
    assert (v["applicant.name_change.new_name"], v["applicant.name_change.date"]) == ("ANA CLARA TESTE", "2026-10-01")


def test_the_classifier_knows_the_order_and_never_the_petition():
    from classify import classify_text

    assert classify_text(ORDER).doc_type == "name_change_order"
    assert classify_text("PETITION TO CHANGE NAME OF ADULT\nPresent name: ANA CLARA EXEMPLO\n").doc_type != "name_change_order"


# --- the timeline ------------------------------------------------------------------------------------------------------


def test_the_events_in_order_birth_then_uscis_then_the_marriage_then_what_the_client_typed():
    evs = name_events.events(_graph())
    assert [(e["kind"], e["date"], e["name"]) for e in evs] == [
        ("birth", "2006-03-14", "ANA CLARA EXEMPLO SOUZA"),
        ("uscis", "2025-08-20", "ANA CLARA EXEMPLO SOUZA"),
        ("marriage", "2026-07-01", "ANA CLARA EXEMPLO SOUZA TESTE"),
        ("client", None, "ANA CLARA EXEMPLO SOUZA"),
    ]
    assert [e["tier"] for e in evs] == [1, 1, 1, 3]
    marriage = evs[2]
    assert (marriage["given"], marriage["family"], marriage["doc"]) == ("ANA CLARA", "EXEMPLO SOUZA TESTE", "casamento.pdf")
    assert marriage["printed"] == "Surname after marriage: EXEMPLO SOUZA TESTE"


def test_the_name_after_marriage_is_the_current_name_and_the_earlier_one_is_an_other_name():
    """The owner's find: Part 1 item 1 carries the name after marriage, item 2 the earlier name, never NOT APPLICABLE."""
    g = _graph()
    assert (_v(g, "applicant.given_name"), _v(g, "applicant.family_name")) == ("ANA CLARA", "EXEMPLO SOUZA TESTE")
    assert g.get("applicant.family_name").tier == 1  # the certificate prints the surname; USCIS split the given name
    assert (_v(g, "applicant.other_name1_given"), _v(g, "applicant.other_name1_family")) == ("ANA CLARA", "EXEMPLO SOUZA")
    assert g.get("applicant.other_name2_family") is None
    data = name_events.payload(g)
    assert data["current"]["kind"] == "marriage" and "marriage certificate" in data["why"] and "07/01/2026" in data["why"]
    assert data["client_said_no"] is True and data["differs"] is True


def test_the_questionnaires_no_never_outranks_a_document_and_not_applicable_only_when_all_agree():
    from rules.policy import load_policy_profile, run_policies

    policies = load_policy_profile(schema_path.path("law", "policy_sijs"), firm=False)
    g = _graph()  # the client said No to other names; the certificate shows another
    run_policies(g, policies)
    assert g.get("applicant.na.other_names") is None and _v(g, "applicant.other_name1_family") == "EXEMPLO SOUZA"

    g = _graph(marriage=None)  # every event carries the same name, and no marriage certificate: the firm's NOT APPLICABLE
    run_policies(g, policies)
    assert _v(g, "applicant.na.other_names") == "NOT APPLICABLE" and g.get("applicant.other_name1_family") is None
    assert (_v(g, "applicant.given_name"), _v(g, "applicant.family_name")) == ("ANA CLARA", "EXEMPLO SOUZA")
    assert name_events.payload(g)["differs"] is False and name_events.flags(g) == []

    g = _graph(marriage=MA_NO_FIELD)  # a certificate that does not say which name she uses now: asked, and no NOT APPLICABLE meanwhile (K6)
    run_policies(g, policies)
    assert g.get("applicant.na.other_names") is None and g.get("applicant.other_name1_family") is None


def test_no_field_no_event_and_a_spouses_surname_is_never_taken_by_custom():
    g = _graph(marriage=MA_NO_FIELD)
    assert [e["kind"] for e in name_events.events(g)] == ["birth", "uscis", "client"]
    assert "TESTE" not in str(_v(g, "applicant.family_name"))


def test_particles_and_accents_are_set_aside_when_names_are_compared():
    assert name_events.same_name("Ana Clara da Silva", "ANA CLARA SILVA")
    assert name_events.same_name("Ána Clára Exemplo de Souza", "ANA CLARA EXEMPLO SOUZA")
    assert name_events.same_name("Ana Clara de la Exemplo", "ANA CLARA EXEMPLO") and name_events.same_name("SOUZA-TESTE", "SOUZA TESTE")
    assert not name_events.same_name("ANA CLARA EXEMPLO SOUZA", "ANA CLARA EXEMPLO SOUZA TESTE")
    # no letter is forgiven: these are different names, and a difference opens a card (verification of K1)
    for a, b in (("ANA CLARA EXEMPLO SOUZA", "ANA CLARA EXEMPLO SOUSA"), ("MARIA EXEMPLO", "MARIO EXEMPLO"), ("JULIANA EXEMPLO", "JULIANO EXEMPLO"),
                 ("LUIZA EXEMPLO", "LUISA EXEMPLO"), ("ANA CLARO EXEMPLO", "ANA CLARA EXEMPLO"), ("ANA CLARA EXEMPLO SOUZA", "ANA CLARA EXEMPIO SOUZA")):
        assert not name_events.same_name(a, b), (a, b)
    g = _graph(marriage=None, typed=("Ana Clara", "Exemplo de Souza"))
    assert name_events.payload(g)["differs"] is False and g.get("applicant.other_name1_family") is None


def test_the_certificates_whole_name_after_marriage_is_read_as_printed():
    g = _graph(marriage=MA_FULL_NAME)
    assert (_v(g, "applicant.given_name"), _v(g, "applicant.family_name")) == ("ANA CLARA", "EXEMPLO SOUZA TESTE")


def test_a_stray_mark_between_the_label_and_the_name_is_the_certificates_background_not_the_name():
    # the real certificate's row, read in grayscale on 10/04/2026: "Name after Marriage: © JOAO ... Name after Marriage: © ANA ... ="
    noisy = MA_FULL_NAME.replace("Name after Marriage: ANA CLARA EXEMPLO SOUZA TESTE Name after Marriage: JOAO PEDRO TESTE",
                                 "1 Name after Marriage: © ANA CLARA EXEMPLO SOUZA TESTE Name after Marriage: © JOAO PEDRO TESTE =")
    v = {f.fact_key: f.normalized_value for f in extract_marriage(noisy)}
    assert (v["marriage.party_a.name_after"], v["marriage.party_b.name_after"]) == ("ANA CLARA EXEMPLO SOUZA TESTE", "JOAO PEDRO TESTE")
    dashed = {f.fact_key: f.normalized_value for f in extract_marriage(MA_DASHED)}
    assert "marriage.party_a.surname_after" not in dashed and dashed["marriage.party_b.name_unchanged"] == "SAME"


def test_a_later_court_order_is_the_current_name_and_both_earlier_names_are_other_names():
    g = _graph(extra=(("decreto.pdf", "name_change_order", extract_order(ORDER)),))
    assert (_v(g, "applicant.given_name"), _v(g, "applicant.family_name")) == ("ANA CLARA", "TESTE")
    others = [(_v(g, f"applicant.other_name{n}_given"), _v(g, f"applicant.other_name{n}_family")) for n in (1, 2)]
    assert others == [("ANA CLARA", "EXEMPLO SOUZA TESTE"), ("ANA CLARA", "EXEMPLO SOUZA")]  # the most recent first


def test_an_undated_event_cannot_be_the_latest():
    undated = MA_AFTER.replace("Date of Marriage: JULY 1, 2026 ", "")
    g = _graph(marriage=undated)
    order = [e["kind"] for e in name_events.events(g)]
    assert order[:3] == ["birth", "marriage", "uscis"]  # after the birth, before every dated event
    data = name_events.payload(g)
    # the only name-setting document after the birth: it is used, and the card says its date was not read
    assert data["current"]["kind"] == "marriage" and _v(g, "applicant.family_name") == "EXEMPLO SOUZA TESTE"
    assert "could not be read" in data["why"] and data["differs"] is True


def test_a_uscis_notice_after_the_wedding_under_the_new_name_agrees():
    g = _graph(extra=(("receipt.pdf", "uscis_notice", extract_notice(I765_RECEIPT)),))
    evs = name_events.events(g)
    assert [e["kind"] for e in evs if e["tier"] == 1] == ["birth", "uscis", "marriage", "uscis"]
    data = name_events.payload(g)
    assert data["current"]["doc"] == "casamento.pdf"  # a notice never sets the name, even when it agrees
    assert _v(g, "applicant.family_name") == "EXEMPLO SOUZA TESTE"
    assert [e["doc"] for e in data["uscis"]] == ["i360.pdf"]  # the I-360 approval still has the earlier name: the attorney's card


def test_the_clients_typed_other_names_go_in_item_2_as_the_clients_own_answer():
    g = FactGraph("exemplo")
    g.add_source("applicant.birth_certificate_name", "certidao.pdf", "birth_certificate", "ANA CLARA EXEMPLO SOUZA", "ANA CLARA EXEMPLO SOUZA", 0.8)
    for key, value in (("questionnaire.other_name1_given", "Ana"), ("questionnaire.other_name1_family", "Souza")):
        g.add_source(key, "portal questionnaire", "intake_questionnaire", value, value, 0.95, tier=3)
    assemble(g)
    assert (_v(g, "applicant.other_name1_given"), _v(g, "applicant.other_name1_family")) == ("ANA", "SOUZA")
    assert g.get("applicant.other_name1_family").tier == 3  # the client's statement: a person confirms it
    assert name_events.payload(g)["differs"] is False  # an other name the client gives is not a question of which is current


def test_a_reviewers_own_name_boxes_are_never_overwritten():
    g = FactGraph("exemplo")
    g.add_source("applicant.birth_certificate_name", "certidao.pdf", "birth_certificate", "ANA CLARA EXEMPLO SOUZA", "ANA CLARA EXEMPLO SOUZA", 0.8)
    _add(g, "i360.pdf", "i360_approval", extract_i360(I360) + extract_notice(I360))
    _add(g, "casamento.pdf", "marriage_certificate", extract_marriage(MA_AFTER))
    g.set_by_review("applicant.family_name", "EXEMPLO SOUZA", "Pat Paralegal", "as the client signs")
    assemble(g)
    assert _v(g, "applicant.family_name") == "EXEMPLO SOUZA"


# --- a notice never sets the name (decided 10/03/2026 after the verification of K1) -------------------------------------


def _birth_only(name="ANA CLARA EXEMPLO SOUZA", notice=I360, typed=None, marriage=None):
    g = FactGraph("exemplo")
    g.add_source("applicant.birth_certificate_name", "certidao.pdf", "birth_certificate", name, name, 0.8)
    g.add_source("applicant.dob", "certidao.pdf", "birth_certificate", "14/03/2006", "2006-03-14", 0.9)
    _add(g, "i360.pdf", "i360_approval", extract_i360(notice) + extract_notice(notice))
    if marriage:
        _add(g, "casamento.pdf", "marriage_certificate", extract_marriage(marriage))
    if typed:
        g.add_source("applicant.given_name", "portal questionnaire", "intake_questionnaire", typed[0], typed[0], 0.95, tier=3)
        g.add_source("applicant.family_name", "portal questionnaire", "intake_questionnaire", typed[1], typed[1], 0.95, tier=3)
    assemble(g)
    return g


def _cards(g):
    return {f.kind: f.message for f in name_events.flags(g)}


def test_c_married_before_the_i360_approval_the_married_name_stands_and_the_attorney_is_asked():
    g = _graph(marriage=MA_AFTER.replace("JULY 1, 2026", "JULY 1, 2024"))
    assert (_v(g, "applicant.given_name"), _v(g, "applicant.family_name")) == ("ANA CLARA", "EXEMPLO SOUZA TESTE")
    assert (_v(g, "applicant.other_name1_given"), _v(g, "applicant.other_name1_family")) == ("ANA CLARA", "EXEMPLO SOUZA")
    cards = _cards(g)
    assert "USCIS knows the client as ANA CLARA EXEMPLO SOUZA (the USCIS I-360 approval notice of 08/20/2025)" in cards["names_uscis"]


def test_a_receipt_filed_before_the_name_was_updated_never_moves_the_name():
    older = I765_RECEIPT.replace("EXEMPLO SOUZA TESTE, ANA CLARA", "EXEMPLO SOUZA, ANA CLARA")  # dated 09/05/2026, after the wedding
    g = _graph(extra=(("receipt.pdf", "uscis_notice", extract_notice(older)),))
    assert (_v(g, "applicant.given_name"), _v(g, "applicant.family_name")) == ("ANA CLARA", "EXEMPLO SOUZA TESTE")
    assert (_v(g, "applicant.other_name1_family"), g.get("applicant.other_name2_family")) == ("EXEMPLO SOUZA", None)
    assert "USCIS I-765 receipt notice of 09/05/2026" in _cards(g)["names_uscis"]


def test_a_notice_misprinting_the_given_name_never_becomes_the_name_nor_an_other_name():
    g = _birth_only(notice=I360.replace("EXEMPLO SOUZA, ANA CLARA", "EXEMPLO SOUZA, ANE CLARA"))
    assert (_v(g, "applicant.given_name"), _v(g, "applicant.family_name")) == ("ANA CLARA", "EXEMPLO SOUZA")
    assert g.get("applicant.other_name1_family") is None  # a spelling, not a name the client used
    assert "USCIS knows the client as ANE CLARA EXEMPLO SOUZA" in _cards(g)["names_uscis"]


def test_a_notice_dropping_a_surname_never_becomes_the_name():
    g = _birth_only(notice=I360.replace("EXEMPLO SOUZA, ANA CLARA", "SOUZA, ANA CLARA"), typed=("ANA CLARA", "EXEMPLO SOUZA"))
    assert (_v(g, "applicant.given_name"), _v(g, "applicant.family_name")) == ("ANA CLARA", "EXEMPLO SOUZA")
    assert g.get("applicant.other_name1_family") is None
    assert "USCIS knows the client as ANA CLARA SOUZA" in _cards(g)["names_uscis"]


def test_one_letter_between_the_birth_certificate_and_a_notice_still_asks_as_master_did():
    """Master's cross-check flagged SOUZA against SOUSA and MARIA against MARIO; the timeline must too (now the attorney's card)."""
    g = _birth_only(notice=I360.replace("EXEMPLO SOUZA, ANA CLARA", "EXEMPLO SOUSA, ANA CLARA"))
    assert _v(g, "applicant.family_name") == "EXEMPLO SOUZA" and "EXEMPLO SOUSA" in _cards(g)["names_uscis"]
    g = _birth_only(name="MARIA EXEMPLO SOUZA", notice=I360.replace("EXEMPLO SOUZA, ANA CLARA", "EXEMPLO SOUZA, MARIO"))
    assert _v(g, "applicant.given_name") == "MARIA" and "MARIO EXEMPLO SOUZA" in _cards(g)["names_uscis"]


def test_the_attorney_may_list_a_notices_spelling_as_an_other_name_on_the_card():
    g = FactGraph("exemplo")
    g.set_by_review(name_events.ALSO_KEY, "ANE CLARA EXEMPLO SOUZA", "Ana Attorney", "USCIS's spelling, listed")
    g.add_source("applicant.birth_certificate_name", "certidao.pdf", "birth_certificate", "ANA CLARA EXEMPLO SOUZA", "ANA CLARA EXEMPLO SOUZA", 0.8)
    notice = I360.replace("EXEMPLO SOUZA, ANA CLARA", "EXEMPLO SOUZA, ANE CLARA")
    _add(g, "i360.pdf", "i360_approval", extract_i360(notice) + extract_notice(notice))
    assemble(g)
    assert (_v(g, "applicant.other_name1_given"), _v(g, "applicant.other_name1_family")) == ("ANE CLARA", "EXEMPLO SOUZA")


def test_with_no_name_setting_document_nothing_is_settled():
    g = FactGraph("exemplo")
    _add(g, "i360.pdf", "i360_approval", extract_i360(I360) + extract_notice(I360))
    g.add_source("applicant.family_name", "portal questionnaire", "intake_questionnaire", "SOUZA", "SOUZA", 0.95, tier=3)
    assemble(g)
    assert g.get("applicant.family_name").status == "conflict"  # as before K1: the sources-disagree card, the box blank
    assert [f.kind for f in name_events.flags(g)] == ["names"]  # and the names card asks for the birth certificate


def test_another_documents_spelling_is_shown_on_the_card_never_silent():
    g = _graph(extra=(("i94.pdf", "i94", [ExtractedField("applicant.family_name", "SOUZA", "SOUZA", 0.9),
                                         ExtractedField("applicant.given_name", "ANA", "ANA", 0.9)]),))
    data = name_events.payload(g)
    assert [(s["name"], s["doc"]) for s in data["also_seen"]] == [("ANA SOUZA", "i94.pdf")]
    assert "Also seen on the" in _cards(g)["names"] and ": ANA SOUZA." in _cards(g)["names"]


def test_a_third_other_name_goes_to_part_14_and_the_report_says_so():
    g = _graph(extra=(("decreto.pdf", "name_change_order", extract_order(ORDER)),),)
    for key, value in (("questionnaire.other_name1_given", "ANINHA"), ("questionnaire.other_name1_family", "SOUZA")):
        g.add_source(key, "portal questionnaire", "intake_questionnaire", value, value, 0.95, tier=3)
    assemble(g)
    assert _v(g, "applicant.other_name3_family") == "SOUZA"
    n = next(k for k in range(1, 30) if _v(g, f"applicant.p14_block{k}_text") and "OTHER NAMES USED" in _v(g, f"applicant.p14_block{k}_text"))
    assert (_v(g, f"applicant.p14_block{n}_page"), _v(g, f"applicant.p14_block{n}_part"), _v(g, f"applicant.p14_block{n}_item")) == ("1", "1", "2")
    assert "ANINHA SOUZA" in _v(g, f"applicant.p14_block{n}_text")
    report = [f for f in name_events.flags(g) if f.kind == "names_overflow"]
    assert report and report[0].level == "informational" and "1 more goes in Part 14" in report[0].message


def test_the_court_order_sentence_stops_the_former_name_at_be():
    v = {f.fact_key: f.normalized_value for f in extract_order(
        "ORDER ON CHANGE OF NAME\nIt is ordered that the name of Ana Clara Exemplo Souza be changed to Ana Clara Teste.\nDated: October 1, 2026\n")}
    assert v["applicant.name_change.former_name"] == "ANA CLARA EXEMPLO SOUZA"
    assert v["applicant.name_change.new_name"] == "ANA CLARA TESTE"


# --- the second verification of K1: undated changes, no name-setting document, the casing ------------------------------


def test_a_dated_marriage_beside_an_undated_court_order_is_no_order_of_events_and_a_person_chooses():
    undated_order = ORDER.replace("Date: 09/15/2026\n", "")
    g = _graph(extra=(("decreto.pdf", "name_change_order", extract_order(undated_order)),))
    data = name_events.payload(g)
    assert data["unordered"] is True and "could not be read" in data["why"]
    for key in ("applicant.given_name", "applicant.family_name", "applicant.other_name1_given", "applicant.other_name1_family"):
        assert g.get(key).tier == 3, key  # written for a person to confirm, never as fact
    card = _cards(g)["names"]
    assert "Neither is taken as the order of events" in card and "earlier names" not in card
    assert g.get("applicant.name_current").tier == 3


def test_an_undated_marriage_alone_stays_the_pick_but_at_tier_3():
    g = _graph(marriage=MA_AFTER.replace("Date of Marriage: JULY 1, 2026 ", ""))
    data = name_events.payload(g)
    assert data["unordered"] is False and data["current"]["kind"] == "marriage" and "could not be read" in data["why"]
    assert _v(g, "applicant.family_name") == "EXEMPLO SOUZA TESTE" and g.get("applicant.family_name").tier == 3
    assert g.get("applicant.other_name1_family").tier == 3


def test_with_no_name_setting_document_disagreeing_documents_open_the_card_and_ask_for_the_certificate():
    g = FactGraph("exemplo")
    _add(g, "i360.pdf", "i360_approval", extract_i360(I360) + extract_notice(I360))
    _add(g, "receipt.pdf", "i765_approval", extract_notice(I765_RECEIPT))  # under EXEMPLO SOUZA TESTE
    g.add_source("applicant.given_name", "portal questionnaire", "intake_questionnaire", "ANA CLARA", "ANA CLARA", 0.95, tier=3)
    g.add_source("applicant.family_name", "portal questionnaire", "intake_questionnaire", "EXEMPLO SOUZA", "EXEMPLO SOUZA", 0.95, tier=3)
    assemble(g)
    assert _v(g, "applicant.family_name") == "EXEMPLO SOUZA"  # what the documents give, as before
    card = _cards(g)["names"]
    assert "No birth certificate, marriage certificate or court order" in card and "Ask the client for the birth certificate" in card
    assert "ANA CLARA EXEMPLO SOUZA TESTE (the USCIS I-765 receipt notice of 09/05/2026)" in card
    assert "names_uscis" not in _cards(g)


def test_a_document_name_keeps_its_capitals_on_the_card():
    g = _graph(extra=(("i94.pdf", "i94", [ExtractedField("applicant.family_name", "SOUZA", "SOUZA", 0.9),
                                         ExtractedField("applicant.given_name", "ANA", "ANA", 0.9)]),))
    card = _cards(g)["names"]
    seen = name_events.payload(g)["also_seen"][0]["what"]
    assert f"Also seen on the {seen}: ANA SOUZA." in card and seen[:1].isupper()  # "the I-94 arrival record", never "the i-94 ..."


# --- K6: a marriage certificate that does not say which name the client uses now ---------------------------------------------------

# The SHAPE of a real Massachusetts certified copy issued by a city clerk, as OCR reads it (with its noise): the header, the two columns,
# and NO "Surname after Marriage" or "Name after Marriage" row anywhere. The client is Party B, under her birth name. Everyone is made up.
MA_REAL_SHAPE = """The Commonwealth of Massachusetts
CITY OF EXEMPLO FALLS
Certificate of Marriage
Record Number: 2026-000123
Date of Marriage: JULY 1, 2026 Place of Marriage: EXEMPLO FALLS, MA
Party A Party B
Name: JOAO PEDRO TESTE Name: ANA CLARA EXEMPLO SOUZA iped
Residence: 10 EXAMPLE STREET, #3, WORCESTER, MA pcitonces 10 EXAMPLE STREET, #3, WORCESTER, MA
Age: 22 Age: 20
Date of Birth: MAY 2, 2004 Date of Birth: MARCH 14, 2006
Occupation: COOK Occupation: STUDENT
Place of Birth: BOGOTA, COLOMBIA Place of Birth: SAO PAULO, BRAZIL S
Name of Parent: ROSA TESTE DIAS (TESTE DIAS) Name of Parent: MARIA EXEMPLO SOUZA (EXEMPLO SOUZAI
Name of Parent: CARLOS TESTE RUIZ (TESTE RUIZ) Name of Parent: JOSE EXEMPLO SOUZA (EXEMPLO SOUZA)
Number of Marriage: FIRST Number of Marriage: FIRST
Widowed or Divorced: ----- Widowed or Divorced: -----
Marriage solemnized by (Name, Official Station and Residence): PAT EXEMPLO, JUSTICE OF THE PEACE, EXEMPLO FALLS, MA
Date of Record: JULY 8, 2026
I, the undersigned, hereby certify that I am the City Clerk of Exemplo Falls and that the above is a true copy of the record.
"""
ASKED = "No attributable post-marriage name was read for the client on the marriage certificate of 07/01/2026. Check the client's printed field, then choose the name every form will carry."
BIRTH, MARRIED = "ANA CLARA EXEMPLO SOUZA", "ANA CLARA EXEMPLO SOUZA TESTE"


def _policies(g):
    from rules.policy import load_policy_profile, run_policies

    run_policies(g, load_policy_profile(schema_path.path("law", "policy_sijs"), firm=False))
    return g


def test_the_real_certificates_shape_prints_no_name_after_marriage_and_the_client_is_party_b():
    from assemble import applicant_party

    v = {f.fact_key: f.normalized_value for f in extract_marriage(MA_REAL_SHAPE)}
    assert (v["marriage.party_b.name"], v["marriage.party_b.dob"]) == (BIRTH, "2006-03-14")
    assert not any(k.endswith(("surname_after", "name_after")) for k in v)
    g = _graph(marriage=MA_REAL_SHAPE)
    assert applicant_party(g) == "party_b" and [e["kind"] for e in name_events.events(g)] == ["birth", "uscis", "client"]


def test_with_no_name_after_marriage_the_card_asks_and_nothing_is_inferred():
    g = _policies(_graph(marriage=MA_REAL_SHAPE))
    data = name_events.payload(g)
    assert data["question"]["open"] is True and data["why"] == ASKED and data["names"] == [BIRTH] and data["decided"] is False
    cards = _cards(g)
    assert cards["names"].startswith(ASKED) and "no NOT APPLICABLE" in cards["names"] and "names_uscis" not in cards
    assert (_v(g, "applicant.given_name"), _v(g, "applicant.family_name")) == ("ANA CLARA", "EXEMPLO SOUZA")
    assert g.get("applicant.given_name").tier == g.get("applicant.family_name").tier == 3  # the birth name, for a person to confirm
    assert g.get("applicant.other_name1_family") is None and g.get("applicant.na.other_names") is None  # item 2 blank, never NOT APPLICABLE
    assert g.get(name_events.QUESTION_KEY).value == "Yes" and g.get(name_events.CURRENT_KEY) is None  # nothing picked for the reviewer
    for text in (cards["names"], data["why"]):
        assert "—" not in text and " -- " not in text and "applicant." not in text and ".pdf" not in text and "2026-07" not in text


def test_choosing_the_name_as_it_stands_closes_the_question_and_not_applicable_returns():
    g = _policies(_graph(marriage=MA_REAL_SHAPE, reviews={name_events.CURRENT_KEY: BIRTH}))
    data = name_events.payload(g)
    assert data["decided"] is True and data["question"]["open"] is False and "Chosen by Pat Paralegal" in data["why"]
    assert "names" not in _cards(g) and g.get(name_events.QUESTION_KEY) is None
    assert _v(g, "applicant.family_name") == "EXEMPLO SOUZA" and g.get("applicant.family_name").tier == 1
    assert _v(g, "applicant.na.other_names") == "NOT APPLICABLE"  # she said she never used another name, and now nothing says otherwise


def test_a_name_typed_in_the_empty_boxes_is_the_settled_name_dated_and_signed_and_the_birth_name_goes_to_item_2():
    g = _policies(_graph(marriage=MA_REAL_SHAPE, reviews={name_events.CHOSEN_GIVEN: "ANA CLARA", name_events.CHOSEN_FAMILY: "EXEMPLO SOUZA TESTE"}))
    person = next(e for e in name_events.events(g) if e["kind"] == "person")
    assert (person["name"], person["date"], person["who"], person["tier"]) == (MARRIED, "2026-10-04", "Pat Paralegal", 3)
    assert person["what"] == "Chosen on the review screen by Pat Paralegal"
    assert (_v(g, "applicant.given_name"), _v(g, "applicant.family_name")) == ("ANA CLARA", "EXEMPLO SOUZA TESTE")
    assert g.get("applicant.family_name").review.resolved_by == "Pat Paralegal"  # the decision is the sign-off: no second card to confirm it
    assert (_v(g, "applicant.other_name1_given"), _v(g, "applicant.other_name1_family")) == ("ANA CLARA", "EXEMPLO SOUZA")
    assert g.get("applicant.na.other_names") is None
    cards = _cards(g)
    assert "names" not in cards  # decided
    assert f"USCIS knows the client as {BIRTH} (the USCIS I-360 approval notice of 08/20/2025); this filing will say {MARRIED}" in cards["names_uscis"]


def test_the_clients_own_answers_are_offered_and_shown_but_never_settle_by_themselves():
    said = {name_events.CLIENT_CURRENT: "Ana Clara Exemplo Souza Teste", name_events.CLIENT_BIRTH: "Ana Clara Exemplo Souza",
            name_events.CLIENT_CHANGED: "Yes"}
    g = _graph(marriage=MA_REAL_SHAPE, answers=said)
    data = name_events.payload(g)
    assert data["names"] == [BIRTH, MARRIED] and data["client_wrote"] == {"current": MARRIED, "birth": BIRTH, "changed": "Yes"}
    assert _v(g, "applicant.family_name") == "EXEMPLO SOUZA" and g.get("applicant.family_name").tier == 3  # not the client's answer
    card = _cards(g)["names"]
    assert f"The client wrote: {MARRIED} (current legal name)." in card and "The client answered that the name changed." in card
    g = _graph(marriage=MA_REAL_SHAPE, answers=said, reviews={name_events.CURRENT_KEY: MARRIED})  # a person chooses the client's answer
    assert _v(g, "applicant.family_name") == "EXEMPLO SOUZA TESTE" and _v(g, "applicant.other_name1_family") == "EXEMPLO SOUZA"


def test_a_court_order_after_the_marriage_answers_the_question_and_one_before_it_does_not():
    later = _graph(marriage=MA_REAL_SHAPE, extra=(("decreto.pdf", "name_change_order", extract_order(ORDER)),))  # 09/15/2026
    assert name_events.payload(later)["question"] is None and _v(later, "applicant.family_name") == "TESTE"
    earlier = _graph(marriage=MA_REAL_SHAPE, extra=(("decreto.pdf", "name_change_order", extract_order(ORDER.replace("09/15/2026", "01/15/2026"))),))
    assert name_events.payload(earlier)["question"]["open"] is True


def test_the_questionnaire_asks_in_four_languages_and_its_answers_are_the_clients_own_tier_3():
    import sys

    from portal.bank import all_questions, answers_to_facts, load_bank, visible

    sys.path.insert(0, str(ROOT / "tools"))
    from portal_english import english_words

    bank = load_bank()
    asked = all_questions(bank)
    words = {"name_changed": "Did your name change when you married or at any other time?",
             "name_current": "What is your current legal name, exactly as on your most recent legal document?",
             "name_birth": "What was your name at birth?"}
    order = [q["id"] for s in bank["sections"] for q in s["questions"]]
    assert order.index("other_names") < order.index("name_changed") < order.index("name_current") < order.index("name_birth")
    for qid, en in words.items():
        q = asked[qid]
        assert q["label"]["en"] == en and q["_note"].startswith("DRAFT")
        for lg in ("pt", "es", "en", "ht"):
            for text in (q["label"][lg], q["help"][lg]):  # every line in the four languages, help on every line
                assert text and "—" not in text and " -- " not in text, (qid, lg)
                if lg != "en":
                    assert not english_words(text), (qid, lg, english_words(text))
    assert not visible(asked["name_current"], {"name_changed": "No"}) and visible(asked["name_current"], {"name_changed": "Yes"})
    facts = {f.fact_key: f.normalized_value for f in answers_to_facts(
        {"name_changed": "Yes", "name_current": "Ana Clara Exemplo Souza Teste", "name_birth": "Ana Clara Exemplo Souza"}, bank)}
    assert facts[name_events.CLIENT_CURRENT] == MARRIED and facts[name_events.CLIENT_BIRTH] == BIRTH and facts[name_events.CLIENT_CHANGED] == "Yes"
    attorney = (ROOT / "docs" / "attorney_review.md").read_text(encoding="utf-8")
    assert all(en in attorney for en in words.values())  # listed for the attorney to confirm


def test_the_clients_answer_alone_with_no_certificate_never_moves_the_name():
    g = _graph(marriage=None, answers={name_events.CLIENT_CURRENT: "Ana Clara Exemplo Souza Teste", name_events.CLIENT_CHANGED: "Yes"})
    assert _v(g, "applicant.family_name") == "EXEMPLO SOUZA"  # the birth certificate's name stands; the client's answer is offered on the card
    data = name_events.payload(g)
    assert data["current"]["kind"] == "birth" and MARRIED in data["names"] and "names" in _cards(g)
    assert g.get("applicant.other_name1_family") is None and g.get(name_events.CLIENT_CURRENT).tier == 3


def test_a_name_setting_document_dated_after_the_persons_choice_reopens_the_card():
    """Verification of K6, S1: a name typed on 10/04/2026, then a court order dated 11/01/2026. Nothing is settled from the newer order without
    a person, and it is never filed as an "other name": the card opens again with the typed name shown for now."""
    later = ORDER.replace("09/15/2026", "11/01/2026")
    g = _policies(_graph(marriage=MA_REAL_SHAPE, extra=(("decreto.pdf", "name_change_order", extract_order(later)),),
                         reviews={name_events.CHOSEN_GIVEN: "ANA CLARA", name_events.CHOSEN_FAMILY: "EXEMPLO SOUZA TESTE"}))
    data = name_events.payload(g)
    assert data["decided"] is False and data["stale"] == {"by": "Pat Paralegal", "on": "2026-10-04", "name": "ANA CLARA TESTE", "unseen": False}
    assert "dated after the choice Pat Paralegal made on 10/04/2026: choose again" in data["why"]
    assert "names" in _cards(g)
    assert _v(g, "applicant.family_name") == "EXEMPLO SOUZA TESTE" and g.get("applicant.family_name").tier == 3
    assert g.get("applicant.family_name").review is None  # no longer signed off: a person confirms again
    others = [_v(g, f"applicant.other_name{n}_family") for n in (1, 2)]
    assert "TESTE" not in others and others[0] == "EXEMPLO SOUZA"  # the court's newer name is never listed as a former name
    view = name_events.view(g)
    assert view["typed_by_person"] == MARRIED and view["stale"]["name"] == "ANA CLARA TESTE"
    # the same with a choice among the names (K1's own card): a court order after the decision reopens it
    g = _graph(extra=(("decreto.pdf", "name_change_order", extract_order(later)),), reviews={name_events.CURRENT_KEY: MARRIED})
    assert name_events.payload(g)["decided"] is False and "names" in _cards(g) and _v(g, "applicant.family_name") == "EXEMPLO SOUZA TESTE"
    # and an order dated before the choice is part of what the person chose from
    g = _graph(extra=(("decreto.pdf", "name_change_order", extract_order(ORDER)),), reviews={name_events.CURRENT_KEY: MARRIED})
    assert name_events.payload(g)["decided"] is True and "names" not in _cards(g)


PARENTS = """The Commonwealth of Massachusetts
Certificate of Marriage
Date of Marriage: JUNE 1, 2000 Place of Marriage: WORCESTER, MA
Party A Party B
Name: JOSE EXEMPLO SOUZA Name: MARIA EXEMPLO LIMA
Residence: 10 EXAMPLE STREET, #3, WORCESTER, MA 10 EXAMPLE STREET, #3, WORCESTER, MA
Date of Birth: MAY 2, 1975 Date of Birth: MARCH 3, 1978
Place of Birth: SAO PAULO, BRAZIL Place of Birth: SAO PAULO, BRAZIL
Number of Marriage: FIRST Number of Marriage: FIRST
"""


def test_a_parents_certificate_beside_the_clients_never_hides_the_question():
    """Verification of K6, S4: parents' certificates are common in SIJ folders. Each certificate is read on its own columns, so the client's
    no-field certificate still asks, and NOT APPLICABLE is not written meanwhile."""
    from assemble import applicant_marriages, applicant_party

    parents = (("pais.pdf", "marriage_certificate", extract_marriage(PARENTS)),)
    g = _policies(_graph(marriage=MA_REAL_SHAPE, extra=parents))
    assert applicant_marriages(g) == [("casamento.pdf", "party_b", True)] and applicant_party(g) == "party_b"
    data = name_events.payload(g)
    assert data["question"]["open"] is True and data["question"]["doc"] == "casamento.pdf" and data["why"] == ASKED
    assert "names" in _cards(g) and g.get("applicant.na.other_names") is None and g.get("applicant.other_name1_family") is None
    # the parents' certificate alone names no one the case knows: nothing asked, NOT APPLICABLE as before
    alone = _policies(_graph(marriage=PARENTS))
    assert applicant_marriages(alone) == [] and name_events.payload(alone)["question"] is None
    assert _v(alone, "applicant.na.other_names") == "NOT APPLICABLE"
    # with the client's certificate that prints the field, the parents' one does not get in the way either
    g = _graph(marriage=MA_AFTER, extra=parents)
    assert _v(g, "applicant.family_name") == "EXEMPLO SOUZA TESTE"


def test_a_parent_who_shares_the_clients_name_is_never_the_client():
    """Fictional fixture helper."""
    from assemble import applicant_marriages

    mother = PARENTS.replace("MARIA EXEMPLO LIMA", "ANA CLARA EXEMPLO SOUZA").replace(
        "Number of Marriage: FIRST Number of Marriage: FIRST", "Surname after Marriage: EXEMPLO SOUZA Surname after Marriage: EXEMPLO SOUZA RAMOS")
    g = _policies(_graph(marriage=mother))
    assert applicant_marriages(g) == [] and [e["kind"] for e in name_events.events(g)] == ["birth", "uscis", "client"]
    assert (_v(g, "applicant.given_name"), _v(g, "applicant.family_name")) == ("ANA CLARA", "EXEMPLO SOUZA") and g.get("applicant.family_name").tier == 1
    assert "RAMOS" not in str(_v(g, "applicant.other_name1_family")) and name_events.payload(g)["question"] is None


def test_a_certificate_with_no_date_of_birth_for_the_client_only_asks():
    """Re-verification of K6, F1: the name matches but the certificate prints no date of birth for that party: possibly the client's. It may
    open the question (Tier 3), never set a name at Tier 1, even when it prints a name after marriage."""
    from assemble import applicant_marriages

    no_dob = MA_AFTER.replace("Date of Birth: MARCH 14, 2006 Date of Birth: MAY 2, 2004\n", "")
    g = _policies(_graph(marriage=no_dob))
    assert applicant_marriages(g) == [("casamento.pdf", "party_a", False)]
    assert "marriage" not in [e["kind"] for e in name_events.events(g)]
    data = name_events.payload(g)
    assert data["question"]["open"] is True and "no date of birth that shows it is the client" in data["why"]
    assert "it prints a name after marriage (EXEMPLO SOUZA TESTE)" in data["why"] and "names" in _cards(g)
    assert _v(g, "applicant.family_name") == "EXEMPLO SOUZA" and g.get("applicant.family_name").tier == 3
    assert g.get("applicant.na.other_names") is None and g.get("applicant.other_name1_family") is None


def test_two_certificates_that_both_name_the_client_ask_instead_of_writing_not_applicable():
    second = (("casamento2.pdf", "marriage_certificate", extract_marriage(MA_REAL_SHAPE.replace("JULY 1, 2026", "AUGUST 3, 2026"))),)
    g = _policies(_graph(marriage=MA_REAL_SHAPE, extra=second))
    data = name_events.payload(g)
    assert data["question"]["open"] is True and data["question"]["doc"] == "casamento2.pdf"  # the later marriage's certificate is shown
    assert data["why"].startswith("No attributable post-marriage name was read for the client on the marriage certificate of 08/03/2026")
    assert "names" in _cards(g) and g.get("applicant.na.other_names") is None


def test_the_clients_yes_or_not_sure_keeps_not_applicable_out_and_a_yes_with_no_name_asks():
    """Verification of K6, S5: no NOT APPLICABLE while the client says the name changed (or is not sure)."""
    g = _policies(_graph(marriage=None, answers={name_events.CLIENT_CURRENT: "Ana Clara Exemplo Souza Teste", name_events.CLIENT_CHANGED: "Yes"}))
    assert g.get("applicant.na.other_names") is None and "names" in _cards(g)  # the client's own name is offered on K1's card
    g = _policies(_graph(marriage=None, answers={name_events.CLIENT_CHANGED: "Yes"}))  # Yes, but no name typed (an import, a paper answer)
    data = name_events.payload(g)
    assert data["question"]["open"] is True and data["question"]["kind"] == "client_said" and data["question"]["doc"] is None
    card = _cards(g)["names"]
    assert card.startswith("The client answered that the name changed, but no new name was established from the document readings")
    assert g.get("applicant.na.other_names") is None and g.get(name_events.SAID_KEY).value == "Yes"
    assert _v(g, "applicant.family_name") == "EXEMPLO SOUZA" and g.get("applicant.family_name").tier == 3
    g = _policies(_graph(marriage=None, answers={name_events.UNSURE_CHANGED: "Did your name change?"}))  # "I'm not sure"
    assert g.get("applicant.na.other_names") is None and name_events.payload(g)["question"] is None
    g = _policies(_graph(marriage=None, answers={name_events.CLIENT_CHANGED: "No"}))  # No: as before
    assert _v(g, "applicant.na.other_names") == "NOT APPLICABLE" and g.get(name_events.SAID_KEY) is None


TYPED_ON_CARD = {name_events.CHOSEN_GIVEN: "ANA CLARA", name_events.CHOSEN_FAMILY: "EXEMPLO SOUZA TESTE"}


def test_a_name_setting_document_that_was_not_on_the_card_reopens_it_whatever_its_date():
    """Re-verification of K6, F2: the choice records the name-setting documents it was made over (here only the birth certificate). A court
    order that arrives afterwards, dated 09/15/2026 (before the choice) or undated, reopens the card; nothing from it is settled or listed."""
    over = {name_events.OVER_KEY: '["certidao.pdf"]'}
    for order in (ORDER, ORDER.replace("Date: 09/15/2026\n", "")):
        g = _policies(_graph(marriage=MA_REAL_SHAPE, extra=(("decreto.pdf", "name_change_order", extract_order(order)),), reviews=TYPED_ON_CARD | over))
        data = name_events.payload(g)
        assert data["decided"] is False and data["stale"]["unseen"] is True and data["stale"]["name"] == "ANA CLARA TESTE", order
        assert data["why"].startswith("A document that was not on the card when the choice was made has arrived")
        assert "names" in _cards(g) and _v(g, "applicant.family_name") == "EXEMPLO SOUZA TESTE" and g.get("applicant.family_name").tier == 3
        assert "TESTE" not in [_v(g, f"applicant.other_name{n}_family") for n in (1, 2)]  # the order's name is not filed as an earlier name
    # K1's own pick: the married name chosen over the birth certificate and the marriage certificate, then the 09/15 order arrives
    g = _graph(extra=(("decreto.pdf", "name_change_order", extract_order(ORDER)),),
               reviews={name_events.CURRENT_KEY: MARRIED, name_events.OVER_KEY: '["casamento.pdf", "certidao.pdf"]'})
    data = name_events.payload(g)
    assert data["decided"] is False and data["stale"]["unseen"] is True and "names" in _cards(g)
    assert _v(g, "applicant.family_name") == "EXEMPLO SOUZA TESTE" and g.get("applicant.family_name").tier == 3
    assert _v(g, "applicant.other_name1_family") == "EXEMPLO SOUZA" and _v(g, "applicant.other_name2_family") is None
    # a decision saved before the record was kept: an undated order cannot be shown to be older than the choice, so it reopens too
    g = _graph(extra=(("decreto.pdf", "name_change_order", extract_order(ORDER.replace("Date: 09/15/2026\n", ""))),),
               reviews={name_events.CURRENT_KEY: MARRIED})
    assert name_events.payload(g)["stale"]["unseen"] is True and "names" in _cards(g) and _v(g, "applicant.other_name2_family") is None
    # the order was on the card when the person chose: the choice stands
    g = _graph(extra=(("decreto.pdf", "name_change_order", extract_order(ORDER)),),
               reviews={name_events.CURRENT_KEY: MARRIED, name_events.OVER_KEY: '["casamento.pdf", "certidao.pdf", "decreto.pdf"]'})
    assert name_events.payload(g)["decided"] is True and "names" not in _cards(g)


def test_the_attorney_card_names_the_typed_choice_in_plain_words():
    g = _graph(marriage=MA_REAL_SHAPE, reviews={name_events.CHOSEN_GIVEN: "ANA CLARA", name_events.CHOSEN_FAMILY: "EXEMPLO SOUZA TESTE"})
    card = _cards(g)["names_uscis"]
    assert f"this filing will say {MARRIED} (the name chosen on the review screen by Pat Paralegal on 10/04/2026)" in card, card


def test_a_certificate_that_prints_the_name_after_marriage_never_asks():
    assert name_events.payload(_graph())["question"] is None  # MA_AFTER: the field is there, the K1 timeline answers
    # MA_DASHED: a dash in the client's column (the spouse's prints SAME): the client's field does not say, so it asks
    assert name_events.payload(_graph(marriage=MA_DASHED))["question"]["open"] is True


@pytest.mark.parametrize("word", ["SAME", "NO CHANGE", "UNCHANGED"])
def test_a_field_that_prints_no_change_asks_nothing_and_item_2_is_not_applicable(word):
    """Verification of K6 (K1's case b): SAME in the client's own field says the name did not change. No event, no card, and the firm's
    NOT APPLICABLE in items 2 and 2.a under the existing policy."""
    from fill import load_field_map, map_facts_to_fields

    text = MA_AFTER.replace("EXEMPLO SOUZA TESTE Surname after Marriage: TESTE ip", f"{word} Surname after Marriage: TESTE")
    v = {f.fact_key: f.normalized_value for f in extract_marriage(text)}
    assert v["marriage.party_a.name_unchanged"] == word and "marriage.party_a.surname_after" not in v
    g = _policies(_graph(marriage=text))
    assert [e["kind"] for e in name_events.events(g)] == ["birth", "uscis", "client"]  # never a name event
    assert name_events.payload(g)["question"] is None and name_events.flags(g) == [] and g.get(name_events.QUESTION_KEY) is None
    assert (_v(g, "applicant.given_name"), _v(g, "applicant.family_name")) == ("ANA CLARA", "EXEMPLO SOUZA")
    values = map_facts_to_fields(g, load_field_map(schema_path.path("field_map", "i485"))).values
    assert {v for k, v in values.items() if "Pt1Line2_" in k or "Pt1Line2a_" in k} == {"NOT APPLICABLE"}


@pytest.mark.parametrize("printed", ["-----", "NONE", "N/A", ""])
def test_a_dash_an_empty_field_none_or_na_is_not_stated_and_asks(printed):
    text = MA_AFTER.replace("Surname after Marriage: EXEMPLO SOUZA TESTE Surname after Marriage: TESTE ip",
                            f"Surname after Marriage: {printed} Surname after Marriage: TESTE")
    v = {f.fact_key: f.normalized_value for f in extract_marriage(text)}
    assert not any(k.startswith("marriage.party_a.") and k.endswith(("_after", "_unchanged")) for k in v), v
    assert name_events.payload(_graph(marriage=text))["question"]["open"] is True


def test_same_only_in_the_spouses_column_still_asks_about_the_client():
    text = MA_AFTER.replace("Surname after Marriage: EXEMPLO SOUZA TESTE Surname after Marriage: TESTE ip",
                            "Surname after Marriage:  Surname after Marriage: SAME")
    v = {f.fact_key: f.normalized_value for f in extract_marriage(text)}
    assert v["marriage.party_b.name_unchanged"] == "SAME" and not any(k.startswith("marriage.party_a.") and "_after" in k for k in v)
    assert name_events.payload(_graph(marriage=text))["question"]["open"] is True
