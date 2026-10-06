"""A green card under the Cuban Adjustment Act (src/cuban_adjustment.py): the I-485's
Part 2, 3.f box, Part 2 item 2, Part 3's 1.e and Part 9 item 56 from the case;
one year of physical presence (8 CFR 245.2(a)(2)(ii)); the fees (G-1055
10/01/26: $1,440, $950 under 14 with a parent, the I-765 $260 with it); the
family-based lockbox (CAA) and the non-family one (HRIFA dependents); the case
page's track and next filing. Every client value is CONSTRUCTED.
"""

import json
from datetime import date

import pytest

import cuban_adjustment as caa
from factgraph import FactGraph
from fill import load_field_map
from fill.field_map import map_facts_to_fields
import schema_path

TODAY = date(2026, 10, 1)
BASE = {"applicant.family_name": "EXEMPLO", "applicant.given_name": "MARIA", "applicant.a_number": "A099999998", "applicant.dob": "1994-05-20",
        "applicant.sex": "F", "applicant.country_of_birth": "CUBA", "applicant.citizenship": "CUBA", "applicant.physical_street": "10 EXAMPLE ST",
        "applicant.physical_city": "MIAMI", "applicant.physical_state": "FL", "applicant.physical_zip": "33125",
        "applicant.i94_arrival_date": "2025-03-10", "applicant.i94_class_of_admission": "DT", "applicant.last_arrival_manner": "PAROLED"}
FIELDS = load_field_map(schema_path.path("field_map", "i485"))


def _graph(**extra):
    g = FactGraph("c")
    for key, value in (BASE | {k.replace("__", "."): v for k, v in extra.items()}).items():
        if value is not None:
            g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _answer(g, **answers):
    for key, value in answers.items():
        g.add_source(key.replace("__", "."), "attorney", "review", value, value, 1.0)
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


def test_a_paroled_cuban_gets_the_caa_boxes_on_the_i485(case):
    g = caa.case_facts(_graph(), case)
    v = lambda k: g.get(k).value  # noqa: E731
    assert v("applicant.filing_category") == caa.CAA and v("applicant.filing_as") == "Principal"
    assert v("applicant.public_charge_exemption") == caa.CAA and v("applicant.affidavit_of_support_exemption") == "Not required"
    values = map_facts_to_fields(g, FIELDS).values
    assert values["form1[0].#subform[6].Pt2Line3f_CB[0]"] == "/3f0"                 # Part 2, 3.f: The Cuban Adjustment Act
    assert values["form1[0].#subform[4].Pt2Line2_CB[0]"] == "/1fA"                  # Part 2, 2: principal applicant
    assert values["form1[0].#subform[7].Pt3Line1_CB[4]"] == "/4"                    # Part 3, 1.e: no Affidavit of Support
    assert values["form1[0].#subform[17].Pt9Line56_CB[7]"] == "/7"                  # Part 9, 56: Cuban Adjustment Act
    assert values["form1[0].#subform[6].Pt2Line4_CB[0]"] == "/N"                    # not INA 245(i)


def test_the_panel_suggests_from_the_case_and_the_year_decides_when(case):
    g = caa.derive(_graph(), TODAY)
    v = lambda k: g.get(k).value  # noqa: E731
    assert v("caa.basis") == caa.NATIVE and v("caa.entry") == caa.PAROLED and v("caa.entry_date") == "2025-03-10"
    assert v("caa.present_since") == "2025-03-10" and v("caa.present_one_year") == "Yes"
    assert caa.eligible_on(g) == date(2026, 3, 10)
    _answer(g, caa__with_i765="Yes")
    assert caa.problems(case, g, TODAY) == []
    early = caa.derive(_graph(applicant__i94_arrival_date="2026-02-01"), TODAY)
    assert early.get("caa.present_one_year") is None
    assert any("Too early" in p and "02/01/2027" in p for p in caa.problems(case, early, TODAY))
    # the year may come before the parole: the attorney sets the day presence began
    before = caa.derive(_answer(_graph(applicant__i94_arrival_date="2026-02-01"), caa__present_since="2025-01-15"), TODAY)
    assert caa.eligible_on(before) == date(2026, 1, 15) and not any("Too early" in p for p in caa.problems(case, before, TODAY))


def test_no_admission_or_parole_stops_it_and_an_i220a_goes_to_the_attorney(case):
    g = _answer(caa.derive(_graph(applicant__last_arrival_manner="WITHOUT ADMISSION OR PAROLE"), TODAY))
    assert g.get("caa.entry").value == caa.EWI
    assert any("212(d)(5)(A)" in p and "not eligible" in p for p in caa.problems(case, g, TODAY))
    g = caa.derive(_graph(questionnaire__release_document="i220a"), TODAY)
    assert g.get("caa.entry").value == caa.I220A
    assert any("I-220A" in p and "the attorney confirms" in p for p in caa.problems(case, g, TODAY))
    old = _answer(_graph(), caa__entry_date="1958-12-01")
    assert any("01/01/1959" in p for p in caa.problems(case, old, TODAY))


def test_fees_payments_and_where_it_is_mailed(case):
    g = caa.derive(_graph(), TODAY)
    assert caa.fee(g, TODAY)[0] == 1440                                              # G-1055 10/01/26: the general I-485 fee
    pays = caa.payments(g, TODAY, ["g28", "i485", "i765"])
    assert [(p[0], p[2]) for p in pays] == [("i485", 1440), ("i765", 260)]           # the I-765 with a pending I-485 filed with its fee
    assert caa.payments(g, TODAY, ["g28", "i485"]) == [("i485", "I-485", 1440, "Form I-485 filing fee")]
    child = _answer(caa.derive(_graph(applicant__dob="2015-06-01"), TODAY), caa__with_parent="Yes")
    assert caa.fee(child, TODAY)[0] == 950                                           # under 14, filed with a parent's I-485
    lines, name = caa.mail_to(g)                                                     # FL: the family-based chart (USCIS's I-485 page)
    assert name == "Chicago" and lines == ["USCIS", "ATTN: AOS", "P.O. BOX 805887", "CHICAGO, IL 60680"]
    ma = _graph(applicant__physical_state="MA", applicant__physical_city="BOSTON")
    assert caa.mail_to(ma)[0][2] == "P.O. BOX 805887"                                 # MA too
    letter = caa.letter(_answer(g, caa__with_i765="Yes"), TODAY)
    assert "Pub. L. 89-732" in letter["re_lines"][1] and "(c)(9)" in letter["re_lines"][2]
    assert "Form I-485, $1,440; Form I-765, $260" in letter["fees"] and letter["mail_to"][2] == "P.O. BOX 805887"
    notes = " ".join(n["text"] for n in caa.notes(g, TODAY))
    assert "From 03/10/2026" in notes and "sealed envelope" in notes and "Supplement A" in notes


def test_the_packet_pays_each_form_on_its_own_and_carries_the_g1145(case):
    import enotice
    import packet
    import payment

    schema = packet.load_filing("caa")
    pays = payment.payments(schema, case, caa.derive(_graph(), TODAY), TODAY)
    assert [(p["form_id"], p["form"], p["amount"]) for p in pays] == [("i485", "I-485", 1440), ("i765", "I-765", 260)]
    assert schema["variants"]["no_ead"]["forms"] == ["g28", "i485"]
    no_ead = payment.payments(schema | schema["variants"]["no_ead"], case, caa.derive(_graph(), TODAY), TODAY)
    assert [p["form"] for p in no_ead] == ["I-485"]
    assert enotice.wanted(schema)                                                    # a lockbox filing: Form G-1145 on top


def test_a_spouse_files_as_a_derivative_living_with_the_principal(case):
    g = _answer(_graph(applicant__country_of_birth="BRAZIL", applicant__citizenship="BRAZIL"), applicant__filing_category=caa.CAA,
                caa__basis=caa.SPOUSE, applicant__principal_family_name="EXEMPLO", applicant__principal_given_name="JOSE",
                applicant__principal_a_number="A099999997", caa__principal_i485="Pending with USCIS", caa__resides_with_principal="No")
    caa.case_facts(g, case)
    values = map_facts_to_fields(g, FIELDS).values
    assert values["form1[0].#subform[4].Pt2Line2_CB[1]"] == "/1fB"                  # Part 2, 2: derivative
    assert values["form1[0].#subform[4].Pt2Line2_FamilyName[0]"] == "EXEMPLO"
    assert values["form1[0].#subform[4].Pt2Line2_AlienNumber[0]"] == "099999997"
    assert any("reside with the Cuban principal" in p for p in caa.problems(case, g, TODAY))
    assert "Spouse or Child of a Qualifying Cuban" in caa.letter(g, TODAY)["re_lines"][1]


def test_an_abused_spouse_is_not_built_here(case):
    g = _answer(_graph(), caa__abused="Yes")
    assert caa.mail_to(g) == (None, None)
    assert any("8 U.S.C. 1367" in p for p in caa.problems(case, g, TODAY))


def test_a_hrifa_dependent_files_with_the_non_family_chart(case):
    g = _answer(_graph(applicant__country_of_birth="HAITI", applicant__citizenship="HAITI", applicant__physical_state="MA"),
                applicant__filing_category=caa.HRIFA, hrifa__relationship=caa.SON_DAUGHTER, hrifa__relationship_at_grant="Yes",
                hrifa__other_basis="No")
    caa.case_facts(g, case)
    caa.derive(g, TODAY)
    values = map_facts_to_fields(g, FIELDS).values
    assert values["form1[0].#subform[6].Pt2Line3f_CB[2]"] == "/3f3"                 # Part 2, 3.f: dependent status under HRIFA
    assert values["form1[0].#subform[17].Pt9Line56_CB[11]"] == "/11"                # Part 9, 56: HRIFA dependent
    assert g.get("hrifa.haitian_national").value == "Yes"
    lines, name = caa.mail_to(g)
    assert name == "Elgin" and lines[2] == "P.O. BOX 4115"                           # the non-family chart ("Asylum, Refugee, or HRIFA")
    assert any("12/31/1995" in p for p in caa.problems(case, g, TODAY))              # a son or daughter: since 1995
    assert "Section 902" in caa.letter(g, TODAY)["re_lines"][1]


def test_an_sij_case_or_another_category_is_left_alone(case):
    g = caa.case_facts(_graph(applicant__i360_receipt_number="IOE0999000001"), case)
    assert g.get("applicant.filing_category") is None
    g = caa.case_facts(_graph(applicant__filing_category="Spouse of U.S. citizen"), case)
    assert g.get("applicant.public_charge_exemption") is None
    assert "wrong packet" in caa.problems(case, g, TODAY)[0]


def test_the_case_page_offers_the_caa_green_card(case):
    import journey

    g = caa.case_facts(_graph(), case)
    j = journey.journey(case, TODAY, graph=g)
    assert j["track"] == "caa" and j["track_name"] == "Cuban Adjustment Act" and j["stage"] == "caa_ready"
    first = j["next_filings"][0]
    assert first["filing"] == "caa" and first["now"] and first["label"].startswith("Green card under the Cuban Adjustment Act")
    assert not any(d["id"] == "age_21" for d in j["deadlines"])                        # not the SIJ track's 21st birthday
    early = caa.case_facts(_graph(applicant__i94_arrival_date="2026-02-01"), case)
    j = journey.journey(case, TODAY, graph=early)
    assert j["stage"] == "caa_wait" and not j["next_filings"][0]["now"] and "from 02/01/2027" in j["next_filings"][0]["label"]
    assert any("Cuban Adjustment Act" in e["what"] and e["date"] == "2027-02-01" for e in j["timeline"])
    assert journey.client_view(j, "es")["title"] == "Esperando 1 año en EE. UU."
    _notice(g, "IOE0999000888", "I-485", "receipt", "2026-09-20")                     # filed: the green card application is pending
    j = journey.journey(case, TODAY, graph=g)
    assert j["stage"] == "i485_pending" and not any(f["filing"] == "caa" for f in j["next_filings"])


def test_a_cuban_asylum_applicant_sees_the_caa_as_an_option(case):
    import journey

    g = _graph()
    _notice(g, "ZLA2590000701", "I-589", "receipt", "2025-06-01")
    j = journey.journey(case, TODAY, graph=caa.case_facts(g, case))
    assert j["track"] == "asylum" and g.get("applicant.filing_category") is None      # its own track: no CAA box on the I-485
    assert any(f["filing"] == "caa" and not f["now"] for f in j["next_filings"])
