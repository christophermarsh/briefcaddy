"""The cover letter (src/fill/cover_letter.py): what it lists, the
priority-date check, and its place on top of the packet."""

import io
from datetime import date

import pytest
from pypdf import PdfReader

from fill import cover_letter as cl

TODAY = date(2026, 10, 1)
FACTS = {"given_name": "ANA", "middle_name": None, "family_name": "SAMPLE DA SILVA", "a_number": "A99000001", "sex": "F",
         "country_of_birth": "BRAZIL", "i360_priority_date": "2025-02-10"}


def config(month="October 2026", all_cutoff="2025-06-01", **cutoffs):
    c = cl.load_config()
    c["visa_bulletin"]["month"] = month
    c["visa_bulletin"]["eb4_cutoff"]["ALL CHARGEABILITY"] = all_cutoff
    c["visa_bulletin"]["eb4_cutoff"].update(cutoffs)
    return c


def plan(types, forms=("g28", "i485", "i765"), docs=None):
    files = [{"doc": d, "type": t} for d, t in (docs or [(f"{t}.pdf", t) for t in types])]
    return {"forms": [{"id": f} for f in forms], "exhibits": [{"files": files}]}


def test_the_letter_lists_only_what_the_packet_holds_in_the_firms_order():
    forms, docs = cl.enclosures(plan(["i94", "i360_approval", "passport", "work_permit", "ssn_card"]), config())
    assert forms[0].startswith("Applicant’s G-28") and forms[-1].startswith("Applicant’s Sealed I-693")
    assert docs[:6] == ["Copy of I-797 Notice of Action for I-360 Petition for Amerasian, Widow(er), or Special Immigrant Approval",
                        "4 Passport Pictures of the Applicant", "Copy of Applicant’s Passport", "Copy of Applicant’s Employment Authorization Card",
                        "Copy of Applicant’s Social Security Card", "Copy of Applicant’s Most Recent I-94"]
    assert not any("Driver" in d for d in docs)  # not in the packet, not in the letter
    # without the I-765: two photos and no I-765 line
    forms, docs = cl.enclosures(plan(["i360_approval"], forms=("g28", "i485")), config())
    assert not any("I-765" in f for f in forms) and "2 Passport Pictures of the Applicant" in docs
    # a birth certificate says "and Translation" only when one is there
    _, docs = cl.enclosures(plan([], docs=[("certidao.pdf", "birth_certificate")]), config())
    assert "Copy of Applicant’s Birth Certificate" in docs
    _, docs = cl.enclosures(plan([], docs=[("certidao.pdf", "birth_certificate"), ("certidao translation.pdf", "birth_certificate")]), config())
    assert "Copy of Applicant’s Birth Certificate and Translation" in docs


@pytest.mark.parametrize("cfg, facts, problem", [
    (config(month=None, all_cutoff=None), FACTS, "Set this month's Visa Bulletin"),
    (config(month="September 2026"), FACTS, "update it for October 2026"),
    (config(all_cutoff="2024-01-01"), FACTS, "not before the October 2026 EB-4 cut-off"),
    (config(), FACTS | {"i360_priority_date": None}, "No I-360 priority date"),
    # chargeability is the country of birth: a Mexican-born client is held to the Mexico cut-off
    (config(MEXICO="2020-01-01"), FACTS | {"country_of_birth": "MEXICO"}, "(01/01/2020, MEXICO)"),
])
def test_the_packet_waits_until_the_priority_date_is_shown_current(cfg, facts, problem):
    _, problems = cl.priority(facts, cfg, TODAY)
    assert any(problem in p for p in problems), problems


def test_a_current_priority_date_needs_nothing():
    values, problems = cl.priority(FACTS, config(), TODAY)
    assert not problems and values["current"] and values["area"] == "ALL CHARGEABILITY"
    assert cl.priority(FACTS, config(all_cutoff="C"), TODAY)[0]["current"]


def test_the_letter_reads_like_the_firms():
    pdf = cl.render(plan(["i360_approval", "passport"]), FACTS, config(), TODAY, draft=False)
    text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    for words in ("GEORGES |", "COTE LLP", "VIA USPS PRIORITY", "P.O. BOX 805887", "October 1, 2026",
                  "Applicant: Ana Sample da Silva (A# 099 000 001)", "with respect to her immigration matters",
                  "priority date of February 10, 2025", "cut-off date of June 01, 2025".replace(" 01,", " 1,"), "Andrew G. FictionalCoverExample, Esq."):
        assert words in " ".join(text.split()), words
    assert "DRAFT" not in text
    assert "DRAFT" in PdfReader(io.BytesIO(cl.render(plan([]), FACTS, config(), TODAY, draft=True))).pages[0].extract_text()


def test_names_and_numbers_are_written_the_way_the_firm_writes_them():
    assert cl.full_name({"given_name": "LUIZA", "family_name": "DOS SANTOS DA SILVA"}) == "Luiza dos Santos da Silva"
    assert cl.a_number("A123456789") == "A# 123 456 789" and cl.a_number("A99000001") == "A# 099 000 001" and cl.a_number(None) == ""


def test_a_letter_without_its_priority_date_paragraph_says_so_in_the_packets_problems():
    """The letter leaves the paragraph out (never a gap); the packet's problem for it says the paragraph is left out, so nothing is silent."""
    unset = config(month=None, all_cutoff=None)
    _, problems = cl.priority(FACTS, unset, TODAY)
    assert any("Set this month's Visa Bulletin" in p and "leaves out its priority-date paragraph" in p for p in problems), problems
    _, problems = cl.priority(FACTS | {"i360_priority_date": None}, config(), TODAY)
    assert any("No I-360 priority date" in p and "leaves out its priority-date paragraph" in p for p in problems), problems
    # and it really is left out of the letter
    text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(cl.render(plan([]), FACTS, unset, TODAY, draft=True))).pages)
    assert "Priority Date Eligibility" not in text and "[" not in text.replace("[DRAFT", "")
    # a current bulletin: the paragraph is in, and the problem is gone
    assert not cl.priority(FACTS, config(), TODAY)[1]
