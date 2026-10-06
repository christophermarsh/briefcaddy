"""A card's instruction names only what the card can do. The owner's walk through a real case (10/04/2026): the card for a date the client wrote
differently from the I-94 said "Pick the right value (or type the correct one), then Save", but the date is not a box on the form, so the card
offered only Acknowledge and Ask the client and nothing to pick. Everything here is invented."""

from __future__ import annotations

import re


def _case(world, name="case-i94-date"):
    w = world["world"]
    d = w.clone(world["root"], "demo-ana", name)
    w._not_sij(d)
    w.drop_facts(d, "applicant.i94_", "applicant.last_arrival_", "questionnaire.entry_how", "questionnaire.entered_via_border", "questionnaire.visa_type")
    w.add_doc(d, "i94.pdf", "i94")
    w.add_fact(d, "applicant.i94_arrival_date", "2016-11-24", "i94.pdf")
    w.add_fact(d, "applicant.last_arrival_date_self_reported", "2016-11-23", "questionnaire.pdf")
    return d


def test_a_disagreement_about_something_not_on_the_form_does_not_say_to_pick_or_save(world, paralegal):
    _case(world)
    paralegal.open("case-i94-date", "fix")
    card = paralegal.page.locator("article.card", has_text="the client's answer differs from the document").first
    text = card.inner_text()
    assert "Sources disagree" in text and "11/23/2016" in text and "11/24/2016" in text, text
    assert "nothing to pick or save on this card" in text and "Ask the client which is right, then Acknowledge" in text, text
    assert "Pick the right value" not in text and "then Save" not in text, text
    names = [b.inner_text().strip() for b in card.locator("button").all()]
    assert "Acknowledge" in names and "Ask the client" in names and "Save" not in names and "Confirm" not in names, names
    assert not re.search(r"\bSave\b", " ".join(names))
