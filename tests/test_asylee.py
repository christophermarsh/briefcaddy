"""A green card for an asylee or a refugee (src/asylee.py): the category, the
grant date and the exemptions the I-485 gets from the case, one year after the
grant (8 CFR 209.1, 209.2), the fee (G-1055 10/01/26: an asylee $1,440, a
refugee $0) and the non-family lockbox. Every client value is CONSTRUCTED.
"""

import json
from datetime import date

import pytest

import asylee
from factgraph import FactGraph
from fill import load_field_map
from fill.field_map import map_facts_to_fields
import schema_path

TODAY = date(2026, 10, 1)
BASE = {"applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA", "applicant.a_number": "A099999999", "applicant.dob": "1996-03-14",
        "applicant.sex": "F", "applicant.country_of_birth": "VENEZUELA", "applicant.physical_street": "10 EXAMPLE ST",
        "applicant.physical_city": "SOMERVILLE", "applicant.physical_state": "MA", "applicant.physical_zip": "02143"}


def _graph(**extra):
    g = FactGraph("c")
    for key, value in (BASE | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _notice(g, receipt, form, kind, when):
    g.add_source(f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)


@pytest.fixture
def case(tmp_path, monkeypatch):
    import journey

    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})
    d = tmp_path / "case"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"classifications": {}}), encoding="utf-8")
    return d


def test_an_asylum_approval_fills_the_i485s_asylee_answers(case):
    g = _graph()
    _notice(g, "ZLA2590000601", "I-589", "approval", "2025-09-15")
    asylee.case_facts(g, case)
    v = lambda k: g.get(k).value  # noqa: E731
    assert v("applicant.filing_category") == asylee.ASYLEE and v("asylee.granted_on") == "2025-09-15"
    assert v("applicant.current_status_text") == "ASYLEE" and v("applicant.affidavit_of_support_exemption") == "Not required"
    assert v("applicant.public_charge_exemption") == "Asylee"
    values = map_facts_to_fields(g, load_field_map(schema_path.path("field_map", "i485"))).values
    assert values["form1[0].#subform[6].Pt2Line3d_AsyleeRefugeeCB[0]"] == "/3d0"
    assert values["form1[0].#subform[6].Pt2Line3d_Asylum[0]"] == "09/15/2025"
    assert values["form1[0].#subform[17].Pt9Line56_CB[3]"] == "/3"
    assert asylee.fee(g, TODAY)[0] == 1440                                   # G-1055: the general I-485 fee
    lines, name = asylee.mail_to(g)
    assert name == "Elgin" and lines[2] == "P.O. BOX 4115"                   # MA: the non-family chart (USCIS's I-485 page)
    letter = asylee.letter(g, TODAY)
    assert "$1,440" in letter["fees"] and not letter["no_payment"] and "209(b)" in letter["re_lines"][1]
    assert asylee.problems(case, g, TODAY) == []


def test_the_journey_points_to_the_green_card_a_year_after_the_grant(case):
    import journey

    g = _graph()
    _notice(g, "ZLA2590000601", "I-589", "approval", "2025-09-15")
    asylee.case_facts(g, case)
    j = journey.journey(case, TODAY, graph=g)
    assert j["track"] == "asylum" and j["stage"] == "asylee"
    first = j["next_filings"][0]
    assert first["filing"] == "asylee" and first["now"] and "from 09/15/2026" in first["label"]
    assert any("Can apply for a green card" in e["what"] and e["date"] == "2026-09-15" for e in j["timeline"])
    early = _graph()
    _notice(early, "ZLA2690000602", "I-589", "approval", "2026-03-01")
    asylee.case_facts(early, case)
    assert not journey.journey(case, TODAY, graph=early)["next_filings"][0]["now"]
    assert any("Too early" in p and "03/01/2027" in p for p in asylee.problems(case, early, TODAY))
    _notice(g, "IOE0999000777", "I-485", "receipt", "2026-09-20")         # filed: no longer due
    nf = next(f for f in journey.journey(case, TODAY, graph=g)["next_filings"] if f["filing"] == "asylee")
    assert not nf["now"] and nf["label"] == "Green card application filed (I-485)"


def test_a_judges_grant_counts(case):
    import journey

    journey.mark(case, "hearing", "Paula", value={"date": "2025-08-20", "kind": "Individual (merits)"})
    h = journey.journey(case, TODAY, graph=_graph())["hearings"][0]["id"]
    journey.mark(case, "hearing_result", "Paula", item=h, value={"outcome": "Decision: relief granted", "decision_date": "2025-08-20", "asylum": True})
    g = asylee.case_facts(_graph(asylum__uac="No"), case)
    assert g.get("applicant.filing_category").value == asylee.ASYLEE and g.get("asylee.granted_on").value == "2025-08-20"
    j = journey.journey(case, TODAY, graph=g)
    assert j["stage"] == "asylee" and "immigration judge" in j["why"]


def test_a_refugee_pays_nothing_and_has_no_public_charge_box(case):
    g = asylee.case_facts(_graph(applicant__i94_class_of_admission="RE", applicant__i94_arrival_date="2025-06-01"), case)
    assert g.get("applicant.filing_category").value == asylee.REFUGEE and g.get("asylee.refugee_admitted_on").value == "2025-06-01"
    assert g.get("applicant.public_charge_exemption") is None
    assert asylee.fee(g, TODAY)[0] == 0
    letter = asylee.letter(g, TODAY)
    assert letter["no_payment"] and "No filing fee" in letter["fees"] and "209(a)" in letter["re_lines"][1]
    notes = " ".join(n["text"] for n in asylee.notes(g, TODAY))
    assert "MUST apply" in notes and "vaccination record only" in notes and "no refugee box" in notes


def test_an_sij_case_is_left_alone(case):
    g = _graph(applicant__i360_receipt_number="IOE0999000001")
    _notice(g, "ZLA2590000601", "I-589", "approval", "2025-09-15")
    asylee.case_facts(g, case)
    assert g.get("applicant.filing_category") is None and g.get("asylee.granted_on") is None


def test_in_removal_proceedings_only_the_judge_takes_it(case):
    g = _graph(applicant__nta_present="Yes")
    _notice(g, "ZLA2590000601", "I-589", "approval", "2025-09-15")
    asylee.case_facts(g, case)
    assert any("8 CFR 209.2(c)" in p for p in asylee.problems(case, g, TODAY))
