"""The firm's wording library, learned from approvals (src/wordings.py, brief L3): an approved Part 14 explanation is kept as a firm wording with its
slots, the next case with the same facts is offered it with its slots refilled and cited, the local model may only choose a number among the firm's own
wordings, and no client's name, A-Number, receipt, date of birth or address is in a wording. Everyone here is made up (the Exemplo family); every receipt,
A-Number and address is invented."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

import clock
import drafting
import events
import part14_explain as px
import part14_voice
import restricted
import settings
import wordings
from review.state import reviewed_graph
from rules import approval
from test_part14_explanations import ABSENTIA, APPROVED_I360, ARRIVED, CLIENT, GRANTED, I360, K, NAME, NTA, ORDER_OR_DENIED, SIJ, court, entry, hearing, make_case, yes
import schema_path


@pytest.fixture
def firm(tmp_path, monkeypatch):
    """The firm's own files in a scratch folder, and the clock at 10/05/2026 (the same set-up as tests/test_part14_explanations.py)."""
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "portal"))
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    return tmp_path


PRE = "Yes, I was placed in removal proceedings before the immigration court."
ITEM = K["in_removal_proceedings"]
# the second client's own facts: every identifier differs from the first's
NAME2 = {"applicant.family_name": ("EXEMPLO LIMA", *NTA), "applicant.given_name": ("MARIA", *NTA), "applicant.a_number": ("A099000222", *NTA),
         "applicant.dob": ("2008-05-09", *NTA)}
I360_2 = {"folder.uscis_case.IOE0955555555.approval_20250610": ("I-360 APPROVAL (class SL6, Special Immigrant-Juvenile), 2025-06-10", *I360),
          "folder.notice.IOE0955555555.approval_20250610.date": ("2025-06-10", *I360)}
ABSENTIA_2 = court(hearing("2025-03-04", "Removal ordered in absentia", "2025-03-04"))
GRANTED_2 = court(hearing("2025-03-04", "Decision: relief granted", "2025-03-04"))


def base(firm_dir: Path) -> Path:
    return wordings.root(firm_dir / "clients")


def library(firm_dir: Path) -> list[dict]:
    return wordings.every(base(firm_dir))


def approve_as_suggested(case: Path, key: str = ITEM, who: str = "Sam Attorney") -> dict:
    return px.approve(case, key, who, "attorney")


# -- abstraction: the approved text with its slots ------------------------------------------------------------------------------------------


def test_an_approval_is_kept_with_its_slots_and_none_of_the_cases_facts(firm):
    case = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("in_removal_proceedings"), ABSENTIA)
    view = approve_as_suggested(case)
    (one,) = library(firm)
    assert one["text"] == ("Yes, I was placed in removal proceedings before the immigration court. On {decision_date}, the immigration judge ordered me removed in "
                           "absentia. I am applying to adjust status based on my approved Special Immigrant Juvenile petition (Form I-360, receipt number "
                           "{i360_receipt}), approved on {i360_approved}.")
    raw = json.dumps(one)
    for value in ("02/10/2025", "IOE0912345678", "05/01/2025", "EXEMPLO", "A099000111", "2007"):
        assert value not in raw
    assert (one["form"], one["edition"], one["key"], one["part"], one["item"], one["voice"], one["status"], one["origin"]) == (
        "i485", "09/18/26", ITEM, "9", "14", "client", "approved", "approval")
    assert one["approved"]["who"] == "Sam Attorney" and one["approved"]["role"] == "attorney" and one["approved"]["at"].startswith("2026-10-05")
    assert [(s["name"], s["kind"]) for s in one["slots"]] == [("decision_date", "fact"), ("i360_receipt", "fact"), ("i360_approved", "fact")]
    assert one["uses"][0]["case"] == "case-exemplo" and wordings.count(one) == 1
    assert one["pattern"] == {"present": ["nta", "i360_approved", "ewi", "court:ordered_in_absentia"], "absent": ["admit_until"]}  # the facts that were true on the case
    kept = next(e for e in view["entries"] if e["key"] == ITEM)["approval"]["library"]
    assert kept == {"how": "new", "id": one["id"], "why": ""}
    # the file is where the dictionary says, owner-only
    path = base(firm) / "i485" / "in_removal_proceedings" / f"{one['id']}.json"
    assert path.is_file()
    rows = [r for r in events.rows(events.base_path()) if r["kind"] == "wordings"]
    assert [(r["action"], r["what"], r["who"], r["case"]) for r in rows] == [("learned", "Kept a firm wording for Part 9, item 14", "Sam Attorney", None)]  # a ledger row, no value in it


def test_what_a_person_wrote_each_time_becomes_a_slot_by_laying_the_text_over_the_wording(firm):
    case = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("in_removal_proceedings"), ORDER_OR_DENIED)
    shipped = entry(case, "in_removal_proceedings")["text"]
    assert "[what the judge decided: ordered removal, or denied relief (say which)]" in shipped
    px.edit(case, ITEM, shipped.replace("[what the judge decided: ordered removal, or denied relief (say which)]", "denied the application for relief and ordered me removed"),
            "Paula Paralegal", "paralegal")
    approve_as_suggested(case)
    (one,) = library(firm)
    assert "the immigration judge {judge_decided}." in one["text"] and "denied the application" not in one["text"]
    assert ("judge_decided", "typed") in [(s["name"], s["kind"]) for s in one["slots"]]  # written by a person each time: a blank on the next case


def test_only_a_place_can_stay_as_text_a_date_a_number_and_a_name_are_always_blanks(firm):
    case = make_case(firm, NAME | ARRIVED | yes("committed_crime"))
    text = "Yes, I was cited for driving without a license at the Worcester District Court on 03/03/2024, and paid 150 dollars. The charge was dismissed by Judge Maria Prado."
    px.edit(case, K["committed_crime"], text, "Paula Paralegal", "paralegal")
    preview = px.keep_preview(case, K["committed_crime"])
    assert {o["token"]: o["type"] for o in preview["offers"]} == {"Worcester District Court": "place"}  # the only thing offered
    blanked = {o["token"]: o["type"] for o in preview["blanked"]}
    assert blanked["03/03/2024"] == "date" and blanked["150"] == "number" and blanked["Judge Maria Prado"] == "name"
    assert "03/03/2024" not in preview["text"] and "150" not in preview["text"] and "Prado" not in preview["text"] and "Worcester" not in preview["text"]
    # the attorney keeps the place and says "keep" for the rest too: the rest is ignored
    px.approve(case, K["committed_crime"], "Sam Attorney", "attorney", slots={"Worcester District Court": "keep", "03/03/2024": "keep", "150": "keep", "Judge Maria Prado": "keep"})
    (one,) = library(firm)
    assert one["text"] == "Yes, I was cited for driving without a license at the Worcester District Court on {date_1}, and paid {number_1} dollars. The charge was dismissed by {name_1}."
    assert "03/03/2024" not in json.dumps(one) and "Prado" not in json.dumps(one)


def test_no_client_name_a_number_receipt_date_of_birth_or_address_survives(firm):
    """The masking proof: a text that carries the client's identifiers every way a person writes them is kept with none of them."""
    facts = NAME | ARRIVED | yes("committed_crime") | {
        "applicant.physical_street": ("RUA DAS FLORES 100", *CLIENT), "applicant.physical_city": ("SPRINGFIELD", *CLIENT), "applicant.physical_zip": ("01103", *CLIENT),
        "applicant.daytime_phone": ("413-555-0142", *CLIENT), "applicant.email": ("ana.exemplo@example.invalid", *CLIENT),
        "applicant.father_given_name": ("JOAO", *CLIENT), "applicant.father_family_name": ("EXEMPLO SOUZA", *CLIENT),
        "applicant.travel_document_number": ("FX1234567", *I360), "applicant.birth_city": ("GOIANIA", *CLIENT)}
    case = make_case(firm, facts, name="case-one")  # (a case id can be a client's name: it is in the file by design, closed by may_open; the proof is about the words)
    text = ("Yes, Ana Clara Exemplo Souza (born April 2, 2007; A-099-000-111; receipt IOE0912345678) lives at Rua das Flores 100, Springfield 01103, phone (413) 555-0142, "
            "ana.exemplo@example.invalid. Her father Joao Exemplo Souza was cited on 03/03/2024. Date of birth 04/02/2007 and 2007-04-02, SSN 123-45-6789, passport 123456789. "
            "Her travel document FX1234567 and another, BR7654321, were kept. She was born in Goiania. My cousin joao prado came. Rita paid the fine. Pedro Lima drove.")
    px.edit(case, K["committed_crime"], text, "Paula Paralegal", "paralegal")
    px.approve(case, K["committed_crime"], "Sam Attorney", "attorney", slots={"03/03/2024": "keep"})
    (one,) = library(firm)
    graph = reviewed_graph(case)
    alive = ("ana", "clara", "exemplo", "souza", "joao", "a-099", "099", "000-111", "ioe0912345678", "april 2", "2007", "04/02", "rua", "flores", "100", "springfield", "01103",
             "413", "555", "0142", "example.invalid", "123-45-6789", "123456789", "ana.exemplo", "fx1234567", "br7654321", "goiania", "rita", "pedro", "lima", "03/03/2024")
    held = json.dumps(one).lower()
    assert [w for w in alive if w in held] == []
    assert wordings.leaks(one["text"], graph) == []
    # nothing of the case is in a single file of the library, nor in the ledger
    for path in base(firm).rglob("*"):
        if path.is_file():
            body = path.read_text(encoding="utf-8").lower()
            assert [w for w in alive if w in body] == [], path
    ledger = json.dumps(list(events.rows(events.base_path()))).lower()
    assert [w for w in alive if re.search(rf"\b{re.escape(w)}\b", ledger)] == []
    assert wordings.leaks("Her name is Ana Clara Exemplo Souza", graph) and wordings.leaks("born April 2, 2007", graph) and wordings.leaks("A099000111 IOE0912345678", graph)
    assert wordings.leaks("Her passport FX1234567", graph) and wordings.leaks("another passport BR7654321", None) and wordings.leaks("Rita paid the fine.", None)
    assert wordings.leaks("The charge was dismissed on 03/03/2024", None) and wordings.leaks("He paid 150 dollars", None)


def test_a_document_number_a_name_that_opens_a_sentence_and_a_lower_case_name_in_the_facts_are_blanks_on_every_case(firm):
    """The verifier's probes: each is a blank on an ordinary case, on a restricted case and in a past filing's text."""
    facts = NAME | yes("committed_crime") | {"applicant.travel_document_number": ("FX1234567", *I360), "applicant.father_given_name": ("JOAO", *CLIENT),
                                             "applicant.father_family_name": ("PRADO", *CLIENT)}
    text = ("Yes, I was cited; my passport FX1234567 was kept and my other passport is BR7654321. My cousin joao prado came. Rita paid the fine. Pedro Lima drove. "
            "Then Maria Exemplo, her mother, came to the hearing.")
    for name, shut in (("case-ordinary", False), ("case-restricted", True)):
        case = make_case(firm, facts, name=name)
        if shut:
            restricted.mark(case, True, "A minor's case", "Sam Attorney", "attorney")
        px.edit(case, K["committed_crime"], text, "Paula Paralegal", "paralegal")
        px.approve(case, K["committed_crime"], "Sam Attorney", "attorney")
    for one in library(firm):
        for held in ("FX1234567", "BR7654321", "joao", "prado", "Rita", "Pedro", "Lima", "Maria", "Exemplo"):
            assert held.lower() not in one["text"].lower(), (held, one["text"])
        assert wordings.leaks(one["text"], None, restricted=True) == []
    assert len(library(firm)) == 1  # the same words on both cases: one wording
    # an import's text: the same, with the form's own boxes
    done = wordings.abstract("Maria Exemplo, her mother, came to the hearing. My passport FX1234567 was kept.", restricted=True, extra_identity={"applicant.family_name": "EXEMPLO"})
    assert done["text"] == "{name_1} {name_2}, her mother, came to the hearing. My passport {person_1} was kept."


def test_a_text_that_starts_with_a_word_that_opens_sentences_is_not_taken_for_a_name(firm):
    done = wordings.abstract("Then I was cited. However the charge was dismissed. The judge said so. Yes, I paid.", restricted=True)
    assert done["text"] == "Then I was cited. However the charge was dismissed. The judge said so. Yes, I paid."


def test_a_yes_a_no_or_a_date_that_is_not_one_is_never_taken_for_an_identifier(firm):
    facts = NAME | yes("committed_crime") | {"applicant.other_dobs": ("Yes", *CLIENT), "applicant.has_other_a_numbers": ("No", *CLIENT), "applicant.mailing_in_care_of": ("N/A", *CLIENT),
                                             "applicant.other_dob_year": ("2007", *CLIENT), "applicant.employer1_name": ("I", *CLIENT)}
    case = make_case(firm, facts)
    ids = wordings.identity(reviewed_graph(case))
    assert not [p for p in ids["phrases"] + ids["tokens"] + ids["dates"] if p.lower() in ("yes", "no", "n/a", "i")]
    assert "2007" in ids["dates"] or "2007-04-02" in ids["dates"]
    px.edit(case, K["committed_crime"], "Yes, I was cited and I said No.", "Paula Paralegal", "paralegal")
    px.approve(case, K["committed_crime"], "Sam Attorney", "attorney")
    assert library(firm)[0]["text"] == "Yes, I was cited and I said No."  # not one word of it is a slot


def test_a_wording_that_still_holds_an_identifier_is_not_kept_and_the_approval_stands(firm, monkeypatch):
    case = make_case(firm, NAME | ARRIVED | yes("committed_crime"))
    px.edit(case, K["committed_crime"], "Yes, I was cited on 03/03/2024.", "Paula Paralegal", "paralegal")
    real = wordings.abstract
    monkeypatch.setattr(wordings, "abstract", lambda *a, **k: real(*a, **k) | {"text": "Yes, I am Ana Clara Exemplo Souza and I was cited."})  # a failure of the masking itself
    view = px.approve(case, K["committed_crime"], "Sam Attorney", "attorney")
    e = next(x for x in view["entries"] if x["key"] == K["committed_crime"])
    assert e["state"] == "approved" and e["approval"]["library"]["how"] == "not_kept" and "still holds" in e["approval"]["library"]["why"]
    assert library(firm) == []
    assert [r["action"] for r in events.rows(events.base_path()) if r["kind"] == "wordings"] == ["not_kept"]


# -- the offer: ranking, refill, citation, blanks --------------------------------------------------------------------------------------------


def test_the_next_case_with_the_same_facts_is_offered_the_wording_with_its_own_facts_cited(firm):
    first = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("in_removal_proceedings"), ABSENTIA)
    approve_as_suggested(first)
    (one,) = library(firm)
    second = make_case(firm, NAME2 | ARRIVED | I360_2 | yes("in_removal_proceedings"), ABSENTIA_2, name="case-lima")
    e = entry(second, "in_removal_proceedings")
    (offer,) = e["firm"]["offers"]
    assert offer["id"] == one["id"] and e["suggestion"]["firm"] == e["firm"]["offers"]
    assert offer["text"] == (PRE + " On 03/04/2025, the immigration judge ordered me removed in absentia. I am applying to adjust status based on my approved Special "
                             "Immigrant Juvenile petition (Form I-360, receipt number IOE0955555555), approved on 06/10/2025.")
    assert offer["wrote"] == "The office wrote this on 1 case, approved by Sam Attorney on 10/05/2026."
    assert offer["facts"] == ["a Notice to Appear is in the folder", "the Form I-360 is approved", "the last arrival was without admission or parole",
                              "the court record shows a removal order in absentia"] and offer["lacks"] == [] and offer["differ"] == 0
    by_name = {s["name"]: s for s in offer["slots"]}  # each slot is filled by the same rule as the shipped wording and cited the same way
    assert by_name["decision_date"]["value"] == "03/04/2025" and by_name["decision_date"]["sources"][0]["words"].startswith("The court record on the case page")
    assert by_name["i360_receipt"]["value"] == "IOE0955555555" and by_name["i360_receipt"]["sources"][0]["words"] == "The I-360 approval notice"
    # nothing is applied: the text is still the shipped suggestion until a person picks and an attorney approves
    assert e["how"] == "suggested" and e["state"] == "draft" and e["approval"] is None


def test_a_wording_approved_where_the_i360_was_approved_is_left_out_on_a_case_with_none(firm):
    first = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("in_removal_proceedings"), ABSENTIA)
    approve_as_suggested(first)
    # no I-360 notice in this folder: the wording says "my approved Special Immigrant Juvenile petition", so it is not offered, and the card counts it
    second = make_case(firm, NAME2 | ARRIVED | yes("in_removal_proceedings"), ABSENTIA_2, name="case-lima")
    e = entry(second, "in_removal_proceedings")
    assert e["firm"]["offers"] == [] and e["firm"]["left_out"] == 1


def test_a_blank_where_the_case_lacks_the_fact_and_the_clients_own_answer_is_not_a_fact(firm):
    first = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("in_removal_proceedings"), ABSENTIA)
    approve_as_suggested(first)
    # the court record has no date for the order here: the date is a blank, as for the shipped wording
    undated = court(hearing("2025-03-04", "Removal ordered in absentia"))
    second = make_case(firm, NAME2 | ARRIVED | I360_2 | yes("in_removal_proceedings"), undated, name="case-lima")
    (offer,) = entry(second, "in_removal_proceedings")["firm"]["offers"]
    assert "On [the date of the judge's decision]," in offer["text"] and offer["blanks"] == ["[the date of the judge's decision]"] and offer["differ"] == 0


def test_facts_first_then_the_closest_text_then_the_most_cases(firm):
    plain = make_case(firm, NAME | ARRIVED | yes("in_removal_proceedings"), ABSENTIA, name="case-plain")  # no I-360: the shipped wording has no SIJ sentence
    approve_as_suggested(plain)
    with_i360 = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("in_removal_proceedings"), ABSENTIA, name="case-sij")
    approve_as_suggested(with_i360)
    reworded = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("in_removal_proceedings"), ABSENTIA, name="case-reworded")
    px.edit(reworded, ITEM, PRE + " On 02/10/2025, the judge ordered me removed because I did not appear. " + SIJ, "Paula Paralegal", "paralegal")
    approve_as_suggested(reworded)
    new = make_case(firm, NAME2 | ARRIVED | I360_2 | yes("in_removal_proceedings"), ABSENTIA_2, name="case-lima")
    offers = entry(new, "in_removal_proceedings")["firm"]["offers"]
    texts = {o["id"]: o for o in offers}
    assert len(offers) == 3
    # the I-360 is true here: the wording approved with none differs by one fact and comes last; the two with the same facts come first, the closer text (the
    # shipped one) before the reworded one
    assert [o["differ"] for o in offers] == [0, 0, 1]
    assert offers[0]["similarity"] > offers[1]["similarity"] and "because I did not appear" in offers[1]["text"] and "removed in absentia" in offers[0]["text"]
    assert offers[2]["now"] == ["the Form I-360 is approved"] and "approved Special Immigrant Juvenile" not in offers[2]["text"]
    assert all(o["ranked_by"] == "facts" for o in offers) and len(texts) == 3
    # the same facts and the same text on two cases: the wording is one wording, used on two cases, and comes before one used on one
    again = make_case(firm, NAME2 | ARRIVED | I360_2 | yes("in_removal_proceedings"), ABSENTIA_2, name="case-lima-2")
    approve_as_suggested(again)
    again2 = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("in_removal_proceedings"), ABSENTIA, name="case-sij-2")
    px.edit(again2, ITEM, PRE + " On 02/10/2025, the judge ordered me removed because I did not appear. " + SIJ, "Paula Paralegal", "paralegal")
    approve_as_suggested(again2)
    assert sorted(wordings.count(r) for r in library(firm)) == [1, 2, 2]  # not five wordings


def test_a_wording_approved_under_another_court_result_is_left_out_and_counted(firm):
    granted = make_case(firm, NAME | ARRIVED | yes("in_removal_proceedings"), GRANTED)
    px.edit(granted, ITEM, entry(granted, "in_removal_proceedings")["text"].replace("[the relief the judge granted]", "cancellation of removal"), "Paula Paralegal", "paralegal")
    approve_as_suggested(granted)
    absentia = make_case(firm, NAME2 | ARRIVED | yes("in_removal_proceedings"), ABSENTIA_2, name="case-lima")
    e = entry(absentia, "in_removal_proceedings")
    assert e["firm"]["offers"] == [] and e["firm"]["left_out"] == 1
    same = make_case(firm, NAME2 | ARRIVED | yes("in_removal_proceedings"), GRANTED_2, name="case-lima-granted")
    (offer,) = entry(same, "in_removal_proceedings")["firm"]["offers"]
    assert offer["text"] == PRE + " On 03/04/2025, the immigration judge granted [the relief the judge granted]."  # what the judge granted is written each time: a blank
    assert offer["blanks"] == ["[the relief the judge granted]"] and offer["differ"] == 0


def test_an_item_with_no_shipped_wording_is_offered_the_offices_own(firm):
    first = make_case(firm, NAME | yes("committed_crime"))
    px.edit(first, K["committed_crime"], "Yes, I was cited for driving without a license and the charge was dismissed.", "Paula Paralegal", "paralegal")
    px.approve(first, K["committed_crime"], "Sam Attorney", "attorney")
    second = make_case(firm, NAME2 | yes("committed_crime"), name="case-lima")
    e = entry(second, "committed_crime")
    assert e["suggestion"] is None and e["text"] == "" and e["state"] == "empty"
    (offer,) = e["firm"]["offers"]
    assert offer["text"] == "Yes, I was cited for driving without a license and the charge was dismissed."
    assert offer["facts"] == ["the client is in immigration court and no result is recorded"] and offer["wrote"].startswith("The office wrote this on 1 case")
    # the voice and the office matter: another voice is not offered
    settings.save("firm", {part14_voice.KEY: "office"}, "Sam Attorney")
    approval.approve(part14_voice.PRACTICE_ID, "Sam Attorney", "attorney")
    assert entry(second, "committed_crime")["firm"]["offers"] == []


# -- the paralegal's pick, the attorney's approval, versions ----------------------------------------------------------------------------------


def test_a_pick_goes_into_the_text_like_an_edit_and_nothing_is_applied_until_the_attorney_approves(firm):
    first = make_case(firm, NAME | yes("committed_crime"))
    px.edit(first, K["committed_crime"], "Yes, I was cited for driving without a license.", "Paula Paralegal", "paralegal")
    px.approve(first, K["committed_crime"], "Sam Attorney", "attorney")
    (one,) = library(firm)
    second = make_case(firm, NAME2 | yes("committed_crime"), name="case-lima")
    assert px.problems(second) == ["Part 9, item 23 says Yes and has no explanation in Part 14."]
    px.pick(second, K["committed_crime"], one["id"], "Paula Paralegal", "paralegal")
    e = entry(second, "committed_crime")
    assert e["text"] == "Yes, I was cited for driving without a license." and e["how"] == "edited" and e["edit"]["firm_wording"] is True
    assert e["pick"]["wording"] == one["id"] and e["approval"] is None and px.problems(second) == ["Part 9, item 23 says Yes and has no explanation in Part 14."]
    with pytest.raises(PermissionError):
        px.approve(second, K["committed_crime"], "Paula Paralegal", "paralegal")
    with pytest.raises(ValueError, match="not one the firm offers"):
        px.pick(second, K["committed_crime"], "w-nothing", "Paula Paralegal", "paralegal")
    view = px.approve(second, K["committed_crime"], "Sam Attorney", "attorney")  # unchanged from the offer: the same wording, one more case
    assert next(x for x in view["entries"] if x["key"] == K["committed_crime"])["approval"]["library"] == {"how": "used", "id": one["id"], "why": ""}
    (again,) = library(firm)
    assert wordings.count(again) == 2 and [u["via"] for u in again["uses"]] == ["learned", "offer"]
    assert px.problems(second) == []
    rows = [r for r in events.rows(events.base_path()) if r["kind"] == "decisions"]
    assert any(r["action"] == "picked" and r["what"] == "Chose a firm wording for Part 9, item 23" and r["who"] == "Paula Paralegal" for r in rows)


def test_an_edited_offer_that_is_approved_becomes_a_new_version_linked_to_its_parent(firm):
    first = make_case(firm, NAME | yes("committed_crime"))
    px.edit(first, K["committed_crime"], "Yes, I was cited for driving without a license.", "Paula Paralegal", "paralegal")
    px.approve(first, K["committed_crime"], "Sam Attorney", "attorney")
    (parent,) = library(firm)
    second = make_case(firm, NAME2 | yes("committed_crime"), name="case-lima")
    px.pick(second, K["committed_crime"], parent["id"], "Paula Paralegal", "paralegal")
    px.edit(second, K["committed_crime"], "Yes, I was cited for driving without a license. The charge was dismissed.", "Paula Paralegal", "paralegal")
    view = px.approve(second, K["committed_crime"], "Sam Attorney", "attorney")
    kept = next(x for x in view["entries"] if x["key"] == K["committed_crime"])["approval"]["library"]
    assert kept["how"] == "version"
    by_id = {r["id"]: r for r in library(firm)}
    child = by_id[kept["id"]]
    assert child["parent"] == parent["id"] and child["number"] == 2 and child["text"].endswith("The charge was dismissed.") and wordings.count(child) == 1
    assert [e["case"] for e in by_id[parent["id"]]["edits"]] == ["case-lima"] and wordings.count(by_id[parent["id"]]) == 1  # the parent was edited on this case, not used
    # the share of cases where the paralegal edited what was offered: 1 of the 1 time it was picked
    row = wordings.row(by_id[parent["id"]])
    assert (row["picked"], row["edited"], row["share"]) == (1, 1, 100)
    # a third case: the parent is picked and used as it is
    third = make_case(firm, NAME2 | yes("committed_crime"), name="case-lima-3")
    px.pick(third, K["committed_crime"], parent["id"], "Paula Paralegal", "paralegal")
    px.approve(third, K["committed_crime"], "Sam Attorney", "attorney")
    row = wordings.row(next(r for r in library(firm) if r["id"] == parent["id"]))
    assert (row["cases"], row["picked"], row["edited"], row["share"]) == (2, 2, 1, 50)


def test_back_to_the_suggestion_drops_the_pick(firm):
    first = make_case(firm, NAME | yes("committed_crime"))
    px.edit(first, K["committed_crime"], "Yes, I was cited for driving without a license.", "Paula Paralegal", "paralegal")
    px.approve(first, K["committed_crime"], "Sam Attorney", "attorney")
    (one,) = library(firm)
    second = make_case(firm, NAME2 | yes("committed_crime"), name="case-lima")
    px.pick(second, K["committed_crime"], one["id"], "Paula Paralegal", "paralegal")
    assert px.read(second)["picks"]
    px.revert(second, K["committed_crime"], "Paula Paralegal", "paralegal")
    assert px.read(second)["picks"] == {} and entry(second, "committed_crime")["pick"] is None


def test_an_approval_taken_back_stops_counting_and_a_wording_nobody_else_approved_is_not_offered(firm):
    first = make_case(firm, NAME | yes("committed_crime"))
    px.edit(first, K["committed_crime"], "Yes, I was cited for driving without a license.", "Paula Paralegal", "paralegal")
    px.approve(first, K["committed_crime"], "Sam Attorney", "attorney")
    second = make_case(firm, NAME2 | yes("committed_crime"), name="case-lima")
    assert len(entry(second, "committed_crime")["firm"]["offers"]) == 1
    px.undo(first, K["committed_crime"], "Sam Attorney", "attorney")
    (one,) = library(firm)  # the wording stays
    assert wordings.count(one) == 0 and one["uses"][0]["withdrawn"]["who"] == "Sam Attorney"
    assert entry(second, "committed_crime")["firm"]["offers"] == []
    # a change to the text after the approval takes it back the same way
    px.approve(first, K["committed_crime"], "Sam Attorney", "attorney")
    assert wordings.count(library(firm)[0]) == 1 and len(entry(second, "committed_crime")["firm"]["offers"]) == 1
    px.edit(first, K["committed_crime"], "Yes, I was cited for driving without a license in 2024.", "Paula Paralegal", "paralegal")
    assert wordings.count(library(firm)[0]) == 0


# -- retire, never delete -----------------------------------------------------------------------------------------------------------------


def test_a_retired_wording_is_not_offered_and_stays_for_the_cases_it_was_used_on(firm):
    first = make_case(firm, NAME | yes("committed_crime"))
    px.edit(first, K["committed_crime"], "Yes, I was cited for driving without a license.", "Paula Paralegal", "paralegal")
    px.approve(first, K["committed_crime"], "Sam Attorney", "attorney")
    (one,) = library(firm)
    with pytest.raises(PermissionError):
        wordings.retire(base(firm), [one["id"]], "Paula Paralegal", "paralegal")
    with pytest.raises(ValueError, match="Enter your name"):
        wordings.retire(base(firm), [one["id"]], "", "attorney")
    wordings.retire(base(firm), [one["id"]], "Sam Attorney", "attorney", "the office now says it differently")
    (kept,) = library(firm)
    assert kept["status"] == "retired" and kept["retired"]["who"] == "Sam Attorney" and wordings.cases_of(kept) == ["case-exemplo"] and kept["text"] == one["text"]
    assert entry(make_case(firm, NAME2 | yes("committed_crime"), name="case-lima"), "committed_crime")["firm"]["offers"] == []
    # approving the same words again on another case does not bring it back
    again = make_case(firm, NAME2 | yes("committed_crime"), name="case-lima-2")
    px.edit(again, K["committed_crime"], "Yes, I was cited for driving without a license.", "Paula Paralegal", "paralegal")
    view = px.approve(again, K["committed_crime"], "Sam Attorney", "attorney")
    assert next(x for x in view["entries"] if x["key"] == K["committed_crime"])["approval"]["library"]["how"] == "not_kept"
    assert library(firm)[0]["status"] == "retired" and len(library(firm)) == 1
    assert [r["action"] for r in events.rows(events.base_path()) if r["kind"] == "wordings"][-1] == "retired"


# -- the local model: a number among the firm's own candidates, never a word ---------------------------------------------------------------------


TEXTS = ("Yes, I was cited for driving without a license.", "Yes, I received a traffic citation and the charge was dismissed.")


def two_wordings(firm) -> Path:
    """Two wordings the office approved for item 23, and a new case that is offered both: (the new case's folder)."""
    for n, text in enumerate(TEXTS):
        case = make_case(firm, NAME | yes("committed_crime"), name=f"case-old-{n}")
        px.edit(case, K["committed_crime"], text, "Paula Paralegal", "paralegal")
        px.approve(case, K["committed_crime"], "Sam Attorney", "attorney")
    return make_case(firm, NAME2 | yes("committed_crime"), name="case-new")


def switch_on(practice: bool = True) -> None:
    settings.save("drafting", {"case_questions": "on"}, "Sam Attorney")
    if practice:
        approval.approve(wordings.RANK_ID, "Sam Attorney", "attorney")


def order(case: Path) -> list[str]:
    return [o["text"] for o in entry(case, "committed_crime")["firm"]["offers"]]


def test_the_model_is_off_until_an_attorney_switches_it_on_and_approves_the_practice(firm, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the model was called")

    monkeypatch.setattr(wordings, "ask_model", refuse)
    monkeypatch.setattr(drafting, "local_model", refuse)
    new = two_wordings(firm)
    before = order(new)
    assert len(before) == 2 and entry(new, "committed_crime")["firm"]["model"] == {
        "on": False, "why": "The local model is off. An attorney can switch it on in Settings (Drafting and models).", "can": False}
    with pytest.raises(ValueError, match="The local model is off"):
        px.rank(new, K["committed_crime"], "Paula Paralegal", "paralegal")
    switch_on(practice=False)
    with pytest.raises(ValueError, match="practice for choosing among the firm's wordings is not yet approved"):
        px.rank(new, K["committed_crime"], "Paula Paralegal", "paralegal")
    assert order(new) == before  # the fact-pattern ranking, with no model


def test_the_model_returns_one_number_the_chosen_wording_goes_first_and_it_is_logged(firm):
    new = two_wordings(firm)
    before = order(new)
    switch_on()
    assert entry(new, "committed_crime")["firm"]["model"]["can"] is True
    seen = []

    def model(prompt):
        seen.append(prompt)
        return "2", "a local model"

    px.rank(new, K["committed_crime"], "Paula Paralegal", "paralegal", model=model)
    after = entry(new, "committed_crime")["firm"]
    assert [o["text"] for o in after["offers"]] == [before[1], before[0]] and [o["ranked_by"] for o in after["offers"]] == ["model", "model"]
    # it was handed the approved practice word for word, what is true here, and the firm's own wordings with their slots: no value of the case
    (prompt,) = seen
    assert prompt.startswith(wordings.RANK_PRACTICE) and "True on this case:" in prompt and "- the client is in immigration court and no result is recorded" in prompt
    assert "[1] " + TEXTS[0] in prompt or "[1] " + TEXTS[1] in prompt
    assert not any(w in prompt for w in ("MARIA", "EXEMPLO", "A099000222", "case-new"))
    kept = px.read(new)["ranked"][K["committed_crime"]]
    assert after["ranked"]["chosen"] == after["offers"][0]["id"] and not after["ranked"]["refused"]
    assert kept["chosen"] == after["offers"][0]["id"] and kept["raw"] == "2" and kept["model"] == "a local model" and kept["by"]["who"] == "Paula Paralegal" and not kept["refused"]
    assert len(kept["candidates"]) == 2
    rows = [r for r in events.rows(events.base_path()) if r["kind"] == "decisions" and r["action"] == "ranked"]
    assert rows[-1]["what"] == "Asked the local model to put the firm's wordings for Part 9, item 23 in order of fit" and rows[-1]["who"] == "Paula Paralegal"
    # every text offered is one of the firm's own: the model wrote none
    assert {o["text"] for o in after["offers"]} == set(TEXTS)
    # a change in the facts makes the stored order stale: the fact-pattern ranking again
    assert order(make_case(firm, NAME2 | yes("committed_crime"), name="case-other")) == before


def test_a_wording_learned_on_a_restricted_case_holds_no_value_from_it(firm):
    shut = make_case(firm, NAME | ARRIVED | yes("committed_crime"), name="case-shut")
    restricted.mark(shut, True, "A minor's case", "Sam Attorney", "attorney")
    assert restricted.is_restricted(shut)
    text = "Yes, I was cited in Springfield, Massachusetts on 03/03/2024 for driving without a license; my mother Rita Prado paid 150 dollars."
    px.edit(shut, K["committed_crime"], text, "Paula Paralegal", "paralegal")
    assert px.keep_preview(shut, K["committed_crime"])["restricted"] is True
    # "keep this as text" is not an option on a restricted case: every date, place, name and number is a slot
    px.approve(shut, K["committed_crime"], "Sam Attorney", "attorney", slots={"03/03/2024": "keep", "Springfield, Massachusetts": "keep", "150": "keep", "Rita Prado": "keep"})
    (one,) = library(firm)
    assert wordings.leaks(one["text"], None, restricted=True) == []
    for held in ("03/03/2024", "Springfield", "Massachusetts", "150", "Rita", "Prado"):
        assert held not in one["text"]
    assert one["text"] == ("Yes, I was cited in {place_1} on {date_1} for driving without a license; my mother {name_1} paid {number_1} dollars.")
    # used by someone who may open that case (an attorney: every attorney may): offered, with blanks where the case has no fact
    other = make_case(firm, NAME2 | yes("committed_crime"), name="case-open")
    (offer,) = entry(other, "committed_crime")["firm"]["offers"]
    assert offer["text"] == "Yes, I was cited in [a place] on [a date] for driving without a license; my mother [a name] paid [a number] dollars."


def test_a_restricted_case_is_absent_for_a_reader_who_may_not_open_it_in_every_count_line_list_and_offer(firm, app):
    """The stricter rule: a wording whose only live uses are restricted cases the reader may not open is absent for that reader; one that is also used on an open
    case is shown with that case alone counted, its approver and date from that case, and no fact of the restricted case."""
    text, text2 = "Yes, I was cited for driving without a license.", "Yes, I received a traffic citation."
    shut_only = make_case(firm, NAME | yes("committed_crime"), name="case-shut-only")
    restricted.mark(shut_only, True, "A minor's case", "Sam Attorney", "attorney")
    px.edit(shut_only, K["committed_crime"], text2, "Paula Paralegal", "paralegal")
    px.approve(shut_only, K["committed_crime"], "Sam Attorney", "attorney")
    both = {}
    for name in ("case-shut", "case-open"):
        both[name] = make_case(firm, NAME | yes("committed_crime"), name=name)
        if name == "case-shut":
            restricted.mark(both[name], True, "A minor's case", "Ana Attorney", "attorney")
        px.edit(both[name], K["committed_crime"], text, "Paula Paralegal", "paralegal")
        px.approve(both[name], K["committed_crime"], "Ana Attorney" if name == "case-shut" else "Sam Attorney", "attorney")
    new = make_case(firm, NAME2 | yes("committed_crime"), name="case-new")
    unnamed = lambda case: not case.startswith("case-shut")  # a paralegal not named on either restricted case  # noqa: E731
    # the offer on an open case: one wording (the one an open case also used), counted once, no restricted case in its line
    with wordings.reading_as(unnamed):
        offers = entry(new, "committed_crime")["firm"]["offers"]
    assert [o["text"] for o in offers] == [text] and offers[0]["uses"] == 1 and offers[0]["wrote"] == "The office wrote this on 1 case, approved by Sam Attorney on 10/05/2026."
    assert offers[0]["by"] == "Sam Attorney"  # not Ana Attorney, who approved it on the restricted case
    assert {o["text"] for o in entry(new, "committed_crime")["firm"]["offers"]} == {text, text2}  # the product's own work, and an attorney, see every case
    # Settings
    paralegal = app.wordings_view(PARALEGAL)
    ws = paralegal["items"][0]["wordings"]
    assert [w["text"] for w in ws] == [text] and (ws[0]["cases"], ws[0]["case_list"]) == (1, ["case-open"]) and "case_list_hidden" not in ws[0]
    assert "case-shut" not in json.dumps(paralegal) and text2 not in json.dumps(paralegal) and ws[0]["by"] == "Sam Attorney"
    boss = app.wordings_view(ATTORNEY)
    assert {w["text"] for w in boss["items"][0]["wordings"]} == {text, text2} and max(w["cases"] for w in boss["items"][0]["wordings"]) == 2
    # the download is the attorney's, and holds every case an attorney may open
    assert "case-shut-only" in json.dumps(json.loads(app.wordings_export(ATTORNEY)))
    # the offer through the review app, as the paralegal
    view = app.explain_view("case-new", PARALEGAL)
    got = next(x for x in view["entries"] if x["key"] == K["committed_crime"])["firm"]["offers"]
    assert [o["text"] for o in got] == [text] and got[0]["uses"] == 1 and "case-shut" not in json.dumps(view)
    # Reports: counts of the cases the reader may open only
    from review import reports

    rows = lambda hidden: reports._wordings(firm / "clients", hidden)["rows"]  # noqa: E731
    assert [(r["cases"], r["picked"]) for r in rows(frozenset({"case-shut", "case-shut-only"}))] == [(1, 0)]
    assert sorted(r["cases"] for r in rows(frozenset())) == [1, 2]


# -- never written by a model -----------------------------------------------------------------------------------------------------------------


def test_the_library_never_calls_a_model_to_learn_offer_or_pick(firm, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a model was called")

    monkeypatch.setattr(drafting, "local_model", refuse)
    monkeypatch.setattr(drafting, "smooth_with_model", refuse)
    monkeypatch.setattr(wordings, "ask_model", refuse)
    new = two_wordings(firm)
    (offer, *_) = entry(new, "committed_crime")["firm"]["offers"]
    px.pick(new, K["committed_crime"], offer["id"], "Paula Paralegal", "paralegal")
    px.approve(new, K["committed_crime"], "Sam Attorney", "attorney")
    src = (Path(wordings.__file__)).read_text(encoding="utf-8")
    assert src.count("drafting.local_model(") == 1 and "smooth_with_model" not in src  # the one call to a model, in ask_model(), which only rank() calls


# -- what the attorney reads ---------------------------------------------------------------------------------------------------------------------


def test_the_attorney_reads_the_models_practice_and_every_fact_the_card_names(firm):
    review = " ".join((Path(__file__).resolve().parent.parent / "docs" / "attorney_review.md").read_text(encoding="utf-8").split())
    assert " ".join(wordings.RANK_PRACTICE.split()) in review
    for words in wordings._TOKEN_WORDS.values():
        assert words in review, words
    for line in ("The office wrote this on N cases, approved by {name} on {date}.", "Kept as one of the firm's own wordings, for the next case with the same facts.",
                 "This is a restricted case: every place becomes a blank too."):
        assert line in review
    # the practice is a practice the attorney approves like the others, and a change to its words needs the approval again
    entry_ = wordings.practice_entry()
    assert entry_["id"] == "PRACTICE:WORDING-RANK" and entry_["plain_text"] == wordings.RANK_PRACTICE and entry_ in approval.catalog()
    assert " -- " not in wordings.RANK_PRACTICE and "—" not in wordings.RANK_PRACTICE


# -- the library is the firm's: plain files, in the export and in the dictionary ---------------------------------------------------------------------


def test_the_library_is_plain_files_in_the_firms_export_and_the_dictionary(firm):
    import sys
    import zipfile

    import records

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import export_firm

    first = make_case(firm, NAME | yes("committed_crime"), name="case-first")
    px.edit(first, K["committed_crime"], "Yes, I was cited for driving without a license.", "Paula Paralegal", "paralegal")
    px.approve(first, K["committed_crime"], "Sam Attorney", "attorney")
    (one,) = library(firm)
    rel = f"wordings/i485/committed_crime/{one['id']}.json"
    assert records.coverage("firm", rel) == "listed" and records.by_id("wordings")["exported"] is True
    # plain JSON a person can read without us
    body = json.loads((firm / rel).read_text(encoding="utf-8"))
    assert body["text"] == "Yes, I was cited for driving without a license." and body["approved"]["who"] == "Sam Attorney" and body["status"] == "approved"
    for field in body:
        assert field in {n for n, *_ in records.by_id("wordings")["fields"]} | {"uses", "edits"}, field  # nothing the dictionary does not describe
    (firm / "portal").mkdir()
    done = export_firm.everything(export_firm.default_where(firm / "clients", firm / "portal", firm / "users.json"), who="Sam Attorney", role="attorney", via="staff")
    with zipfile.ZipFile(done["path"]) as z:
        assert f"firm/{rel}" in z.namelist() and json.loads(z.read(f"firm/{rel}"))["id"] == one["id"]
    dictionary = (Path(__file__).resolve().parent.parent / "docs" / "data_dictionary.md").read_text(encoding="utf-8")
    assert "The firm's wordings" in dictionary and "data/wordings/<form>/<answer>/<id>.json" in dictionary and "Learned from an attorney's approval" in dictionary


# -- the review app: who sees which case, who changes the library --------------------------------------------------------------------------------

PASSWORD = "a long enough password for the test"  # secret-scan: allow
PARALEGAL = {"role": "paralegal", "email": "jane@firm.example", "name": "Jane Doe"}
ATTORNEY = {"role": "attorney", "email": "sam@firm.example", "name": "Sam Attorney"}


@pytest.fixture
def app(firm):
    from review.auth import Accounts
    from review.server import ReviewApp

    repo = Path(__file__).resolve().parent.parent
    accounts = Accounts(firm / "staff.json")
    for email, name, role in ((PARALEGAL["email"], PARALEGAL["name"], "paralegal"), (ATTORNEY["email"], ATTORNEY["name"], "attorney")):
        accounts.change_password(email, accounts.add(email, name, role), PASSWORD)
    return ReviewApp(firm / "clients", schema_path.path("field_map", "i485", schema_path.schemas_in(repo)), schema_path.path("template", "i485", schema_path.schemas_in(repo)), None, accounts=accounts)


def test_the_library_on_settings_closes_a_restricted_cases_id_to_someone_who_may_not_open_it(firm, app):
    text = "Yes, I was cited for driving without a license."
    for name in ("case-open", "case-shut"):
        case = make_case(firm, NAME | yes("committed_crime"), name=name)
        if name == "case-shut":
            restricted.mark(case, True, "A minor's case", "Sam Attorney", "attorney")
        px.edit(case, K["committed_crime"], text, "Paula Paralegal", "paralegal")
        px.approve(case, K["committed_crime"], "Sam Attorney", "attorney")
    (one,) = library(firm)
    assert wordings.cases_of(one) == ["case-open", "case-shut"]
    seen = app.wordings_view(PARALEGAL)
    w = seen["items"][0]["wordings"][0]
    assert (w["cases"], w["case_list"], seen["can_edit"]) == (1, ["case-open"], False)  # only the cases this reader may open are counted or named
    assert "case-shut" not in json.dumps(seen)
    boss = app.wordings_view(ATTORNEY)
    assert boss["items"][0]["wordings"][0]["case_list"] == ["case-open", "case-shut"] and boss["can_edit"] is True
    # the library changes by an attorney only; the export too
    with pytest.raises(PermissionError, match="attorney's"):
        app.wordings_change({"action": "retire", "ids": [one["id"]], "reviewer": "Jane Doe"}, PARALEGAL, "paralegal")
    with pytest.raises(PermissionError, match="attorney's"):
        app.wordings_export(PARALEGAL)
    with pytest.raises(ValueError, match="Unknown change"):
        app.wordings_change({"action": "delete", "ids": [one["id"]], "reviewer": "Sam Attorney"}, ATTORNEY, "attorney")  # there is no delete
    after = app.wordings_change({"action": "retire", "ids": [one["id"]], "reviewer": "Sam Attorney", "why": "the office says it another way"}, ATTORNEY, "attorney")
    assert after["items"][0]["wordings"][0]["status"] == "retired" and library(firm)[0]["retired"]["why"] == "the office says it another way"
    exported = json.loads(app.wordings_export(ATTORNEY))
    assert [x["id"] for x in exported["wordings"]] == [one["id"]] and exported["wordings"][0]["cases"] == ["case-open", "case-shut"]
    assert "No value of any client" in exported["about"]


def test_the_card_actions_go_through_the_review_app(firm, app):
    first = make_case(firm, NAME | yes("committed_crime"), name="case-first")
    px.edit(first, K["committed_crime"], "Yes, I was cited in Springfield on 03/03/2024 and paid 150 dollars.", "Paula Paralegal", "paralegal")
    shown = app.explain_change("case-first", {"action": "keep_preview", "key": K["committed_crime"], "reviewer": "Sam Attorney"}, "attorney")  # nothing is written
    assert [o["token"] for o in shown["offers"]] == ["Springfield"] and sorted(o["token"] for o in shown["blanked"]) == ["03/03/2024", "150"] and library(firm) == []
    app.explain_change("case-first", {"action": "approve", "key": K["committed_crime"], "reviewer": "Sam Attorney", "slots": {"150": "keep", "03/03/2024": "keep", "Springfield": "slot"}},
                       "attorney")
    (one,) = library(firm)
    assert one["text"] == "Yes, I was cited in {place_1} on {date_1} and paid {number_1} dollars."
    second = make_case(firm, NAME2 | yes("committed_crime"), name="case-second")
    view = app.explain_change("case-second", {"action": "pick", "key": K["committed_crime"], "wording": one["id"], "reviewer": "Paula Paralegal"}, "paralegal")
    e = next(x for x in view["entries"] if x["key"] == K["committed_crime"])
    assert e["text"] == "Yes, I was cited in [a place] on [a date] and paid [a number] dollars." and e["pick"]["wording"] == one["id"] and second.is_dir()
    with pytest.raises(ValueError, match="local model is off"):
        app.explain_change("case-second", {"action": "rank", "key": K["committed_crime"], "reviewer": "Paula Paralegal"}, "paralegal")


# -- the Decision log's Undo, the edits that stop counting, the download in words ----------------------------------------------------------------


def _approved_pair(firm, text="Yes, I was cited for driving without a license."):
    first = make_case(firm, NAME | yes("committed_crime"), name="case-a")
    px.edit(first, K["committed_crime"], text, "Paula Paralegal", "paralegal")
    px.approve(first, K["committed_crime"], "Sam Attorney", "attorney")
    return first, make_case(firm, NAME2 | yes("committed_crime"), name="case-b")


def test_the_decision_logs_undo_of_the_approval_takes_it_back_from_the_wording(firm, app):
    first, second = _approved_pair(firm)
    key = K["committed_crime"]
    assert wordings.count(library(firm)[0]) == 1 and len(entry(second, "committed_crime")["firm"]["offers"]) == 1
    app.undo("case-a", {"item_id": px.approval_id(key), "reviewer": "Sam Attorney"}, "attorney")  # what /api/undo does
    (one,) = library(firm)  # the wording stays
    assert wordings.count(one) == 0 and one["uses"][0]["withdrawn"]["who"] == "Sam Attorney"
    assert entry(second, "committed_crime")["firm"]["offers"] == []  # with no other live approval it is not offered
    assert px.read(first)["approvals"] == {} and px.read(first)["history"][-1]["why"] == "reopened in the Decision log" and entry(first, "committed_crime")["approval"] is None
    assert [r["action"] for r in events.rows(events.base_path()) if r["kind"] == "wordings"][-1] == "withdrawn"
    # approved again: counts again
    px.approve(first, key, "Sam Attorney", "attorney")
    assert wordings.count(library(firm)[0]) == 1 and len(entry(second, "committed_crime")["firm"]["offers"]) == 1


def test_a_paralegals_undo_of_her_own_text_after_the_approval_takes_the_approval_back_from_the_wording(firm, app):
    first, second = _approved_pair(firm)
    key = K["committed_crime"]
    app.undo("case-a", {"item_id": px.text_id(key), "reviewer": "Paula Paralegal"}, "paralegal")
    e = entry(first, "committed_crime")
    assert e["approval"] is None and e["state"] == "empty" and px.read(first)["approvals"] == {}
    assert wordings.count(library(firm)[0]) == 0 and entry(second, "committed_crime")["firm"]["offers"] == []


def test_the_decision_logs_undo_of_a_picked_text_drops_the_pick(firm, app):
    _first, second = _approved_pair(firm)
    key = K["committed_crime"]
    px.pick(second, key, library(firm)[0]["id"], "Paula Paralegal", "paralegal")
    assert px.read(second)["picks"]
    app.undo("case-b", {"item_id": px.text_id(key), "reviewer": "Paula Paralegal"}, "paralegal")
    assert px.read(second)["picks"] == {} and entry(second, "committed_crime")["pick"] is None


def test_an_edit_made_on_a_case_whose_approval_was_taken_back_stops_counting_in_the_share_edited(firm):
    first, _ = _approved_pair(firm)
    (parent,) = library(firm)
    second = make_case(firm, NAME2 | yes("committed_crime"), name="case-lima")
    px.pick(second, K["committed_crime"], parent["id"], "Paula Paralegal", "paralegal")
    px.edit(second, K["committed_crime"], "Yes, I was cited for driving without a license. The charge was dismissed.", "Paula Paralegal", "paralegal")
    px.approve(second, K["committed_crime"], "Sam Attorney", "attorney")
    assert wordings.row(next(r for r in library(firm) if r["id"] == parent["id"]))["share"] == 100
    px.undo(second, K["committed_crime"], "Sam Attorney", "attorney")
    row = wordings.row(next(r for r in library(firm) if r["id"] == parent["id"]))
    assert (row["picked"], row["edited"], row["share"]) == (0, 0, None)  # the edit was withdrawn with the approval it led to


def test_the_download_is_words_with_no_code_of_ours(firm):
    first, _ = _approved_pair(firm)
    made = wordings.export_all(base(firm))
    (one,) = made["wordings"]
    assert one["answer"].startswith("Part 9, item 23: ") and one["status"] == "Approved" and one["voice"] == "first person (Yes, I ...)" and one["where_it_came_from"] == "Approved on a case"
    assert one["text"] == "Yes, I was cited for driving without a license." and one["approved_by"] == "Sam Attorney" and one["approved_on"] == "10/05/2026" and one["cases"] == ["case-a"]
    body = json.dumps(made)
    for code in ("applicant.part9", "court:", "origin_id", "pattern_tokens", '"key"', "committed_crime"):
        assert code not in body, code
    assert set(one) >= {"true_where_it_was_approved", "times_picked", "times_edited_after_picking", "used_on_cases", "history"}
