"""What a review card says, for someone who isn't a paralegal: the question
and the client's answer, never a code or a file name. Built from the
user's screenshots of the demo case (synthetic client, 10/2026): the "not
sure" alert, the Part 9 follow-up, the father's names, and "Open" on a
portal answer."""

from __future__ import annotations

from portal.bank import UNSURE
from review import answers_page
from review.state import SIGN_OFF, plain_message, question_text
from validate import Flag

ITEM_75 = ("75. If you answered \"Yes\" to Item Number 74., was a severe form of trafficking in persons at least one central reason "
           "for your unlawful presence in the United States? Select Yes. NOTE: Severe trafficking in persons involves sex trafficking")


def test_a_follow_up_is_titled_by_its_own_question():
    assert question_text(ITEM_75) == ("If you answered \"Yes\" to Item Number 74., was a severe form of trafficking in persons at least one "
                                      "central reason for your unlawful presence in the United States?")
    assert question_text("Pt9line77") == ""  # no question: the caller keeps its own title


def test_the_alerts_say_what_to_do_not_a_code():
    empty = {"evidence": [], "facts": []}
    unsure = plain_message(Flag("review", "applicant.part9.x", "CLIENT NOT SURE: in the portal questionnaire the client answered \"I'm not "
                                "sure\" to: Have you ever asked immigration or a judge not to deport you — for example, by applying for asylum?. "
                                "These answers are left blank.", kind="alert"), empty)
    assert "stay blank" in unsure and "CLIENT" not in unsure and "?." not in unsure
    follow = plain_message(Flag("review", "applicant.part9.pt9line77", "Part 9 item 75 (was trafficking a central reason for the unlawful "
                                "presence in item 74?): the main question is answered Yes, so this follow-up needs the attorney's answer.",
                                kind="missing"), {"evidence": [], "facts": [{"key": "applicant.part9.pt9line77"}]})
    assert follow.startswith("The form asks item 75 only when the main question above is Yes")


def test_the_sign_off_note_is_hidden_in_both_wordings():
    # today's validate.py wording, and the wording saved by runs before the dash cleanup
    for m in ("applicant.father_birth_family_name: Tier 3 (human-supplied or confirmed, not from a document). Needs sign-off.",
              "applicant.father_birth_family_name: Tier 3 (human-supplied or confirmed, not from a document) -- needs sign-off."):
        assert SIGN_OFF.search(m) and plain_message(Flag("review", "applicant.x", m), {"evidence": [], "facts": []}) is None
    rule = plain_message(Flag("review", "applicant.x", "applicant.x: derived by rule OVERSTAY-01: needs a human sign-off."), {"evidence": [], "facts": []})
    assert rule.startswith("Answered automatically by the overstay rule")


BANK = {"sections": [
    {"id": "about", "title": {"en": "About you", "pt": "Sobre você"}, "questions": [
        {"id": "dob", "type": "date", "label": {"en": "Date of birth", "pt": "Data de nascimento"}, "fact": "applicant.dob", "i485": "Part 1, Item 3"},
        {"id": "home", "type": "us_address", "label": {"en": "Home address"}, "facts": {"street": "applicant.street"},
         "fields": [{"id": "street", "type": "text", "label": {"en": "Street and number"}}, {"id": "zip", "type": "text", "label": {"en": "ZIP code"}}]},
        {"id": "other_names", "type": "repeat", "label": {"en": "Other names"}, "show_if": {"q": "used_other", "eq": "Yes"},
         "fields": [{"id": "given", "type": "text", "label": {"en": "Given name(s)"}}]},
    ]},
    {"id": "safety", "title": {"en": "Eligibility"}, "questions": [
        {"id": "relief", "type": "yes_no", "label": {"en": "Have you ever asked immigration or a judge not to deport you?", "pt": "Você já pediu?"},
         "fact": "applicant.part9.applied_relief_from_removal", "i485": "Part 9, Item 18",
         "options": [{"value": "Yes", "label": {"en": "Yes", "pt": "Sim"}}, {"value": "No", "label": {"en": "No", "pt": "Não"}}]},
        {"id": "s_court", "type": "checklist", "label": {"en": "Court: have any of these ever happened to you?"},
         "options": [{"value": "guilty", "label": {"en": "I pleaded guilty"}, "facts": ["applicant.part9.pleaded_guilty"], "items": "26"}]},
    ]},
]}


def test_open_on_a_portal_answer_shows_the_question_and_the_answer_in_words():
    answers = {"dob": "2006-03-14", "home": {"street": "10 Example Street", "zip": "01103"}, "relief": UNSURE, "s_court": {"none": True},
               "other_names": [{"given": "NEVER SHOWN"}]}
    page = answers_page.render("Ana Clara Exemplo Souza", answers, BANK, "pt", "2026-10-01T15:20:39Z")
    assert "Have you ever asked immigration or a judge not to deport you?" in page and "I&#x27;m not sure" in page
    assert "03/14/2006" in page and "Street and number: 10 Example Street · ZIP code: 01103" in page and "None of these" in page
    assert "Asked in Portuguese: Você já pediu?" in page and "On the I-485: Part 9, Item 18" in page
    assert 'id="applicant.part9.applied_relief_from_removal"' in page and 'id="applicant.part9.pleaded_guilty"' in page  # "Open" lands here
    assert "NEVER SHOWN" not in page  # a question the client was never shown
    assert "Unsure" not in page and "s_court" not in page and "{" not in page.split("<style")[0]
