"""The family packet's supplements (src/family.py): Form I-485 Supplement A for a client adjusting under INA 245(i)
(8 CFR 245.10; Form G-1055 10/01/26: $1,000, $0 under 17 or with a Form I-817), one Form I-864A per household member
whose income the sponsor counts, and Form I-864EZ in place of the I-864 when the attorney confirms it fits (I-864EZ
Instructions 08/24/26). Every client value is CONSTRUCTED."""

from datetime import date

from pypdf import PdfReader

import family
import packet
from test_family import _case

ROW = {"summary": {"name": "ANA SOUZA"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}
SUPA = {"family.adjust_245i": "Yes", "supa.basis": family.SUPA_BASES[1], "supa.filed_on": "2001-04-15", "supa.present_2000_12_21": "Yes",
        "supa.receipt_number": "EAC0112345678", "supa.fee_exemption": family.SUPA_PAYS, "supa.bar_c": "Yes"}


def _boxes(path):
    return {k.rsplit(".", 1)[-1]: v.get("/V") for k, v in PdfReader(str(path)).get_fields().items()}


def _letter(d, m, name="packet_family.pdf"):
    first = next(s["first_page"] for s in m["sections"] if s["tab"] == "Cover letter")
    return " ".join(" ".join(p.extract_text() for p in PdfReader(str(d / name)).pages[first - 1:first + 1]).split())  # the lines as one text


def test_supplement_a_joins_the_packet_with_its_own_payment(tmp_path, monkeypatch):
    monkeypatch.setattr(packet, "_today", lambda: date(2026, 10, 1))
    d = _case(tmp_path)
    assert "i485supa" not in packet.for_case(packet.load_filing("family"), d)["forms"]  # only when the attorney marks INA 245(i)
    family.answer(d, SUPA, "Sam")
    s = family.status(d)
    asked = {q["key"]: q for q in s["questions"]}
    assert asked["supa.principal_family_name"]["value"] == "SOUZA"  # the client is the principal beneficiary (1.b)
    assert asked["supa.category"]["value"] == "SPOUSE OF U.S. CITIZEN"
    assert not any(p.startswith("Supplement A") for p in s["problems"]), s["problems"]
    assert any(n["title"].startswith("Supplement A") and "$1,000" in n["text"] for n in s["notes"])
    from review.state import reviewed_graph

    assert reviewed_graph(d).get("applicant.part2.adjusting_under_245i").value == "Yes"  # the I-485's own Part 2, item 4
    schema = packet.for_case(packet.load_filing("family"), d)
    assert schema["forms"] == ["g28_i130", "i130", "i130a", "g28", "i485", "i485supa", "i864", "i765"]
    m = packet.build(d, ROW, "Sam", packet.load_filing("family"))
    tabs = [s["tab"] for s in m["sections"]]
    assert tabs[tabs.index("I-485"):tabs.index("I-485") + 3] == ["I-485", "G-1450 (I-485 Supplement A, $1,000)", "I-485 Supplement A"]
    assert [p["amount"] for p in m["payments"]] == [675, 1440, 1000, 260]  # one payment each (src/payment.py)
    supa = _boxes(d / "i485supa_filled.pdf")
    assert supa["Part2_Line1_Checkbox[1]"] == "/B" and supa["Part3_Line1_Checkbox[2]"] == "/C" and supa["Part3_Line1_Checkbox[0]"] in (None, "/Off")
    assert (supa["Part1_Line1_FamilyName[0]"], supa["Part2_Line3_FamilyName[0]"], supa["Part2_Line2_ReceiptNumber[0]"]) == ("SOUZA", "SOUZA", "EAC0112345678")
    assert supa["Part1_Line5_DateOfBirth[0]"] == "05/01/1999" and supa["Part2_Line5_Category[0]"] == "SPOUSE OF U.S. CITIZEN"
    assert _boxes(d / "g28_filled.pdf")["Line1b_ListFormNumber[0]"] == "I-485, I-485A, I-765"  # Supplement A's number in Form G-1055
    letter = _letter(d, m)
    assert "I-485 Supplement A - Adjustment of Status Under Section 245(i)" in letter
    assert "Form I-485 Supplement A, $1,000" in letter and "$3,375" in letter
    assert any(c["text"].startswith("Supplement A: the qualifying petition's receipt") for c in m["checklist"])


def test_the_supplement_a_sum_and_who_is_grandfathered(tmp_path):
    d = _case(tmp_path)
    family.answer(d, SUPA | {"supa.fee_exemption": family.SUPA_UNDER_17}, "Sam")
    out = " ".join(family.status(d)["problems"])
    assert "unmarried and under 17 when the I-485 is filed (8 CFR 245.10(c)(1))" in out  # the client is 27
    from review.state import reviewed_graph

    g = family.derive(reviewed_graph(d))
    assert family.supa_fee(g, date(2026, 10, 1))[0] == 0
    family.answer(d, {"supa.fee_exemption": family.SUPA_FAMILY_UNITY}, "Sam")
    g = family.derive(reviewed_graph(d))
    assert family.supa_fee(g, date(2026, 10, 1)) == (0, "the spouse or unmarried child under 21 of a legalized alien, with the Form I-817 receipt or "
                                                        "approval notice (Form G-1055)")
    assert any("Form I-817 receipt or approval notice" in h["text"] for h in packet.for_case(packet.load_filing("family"), d)["handwork"])
    family.answer(d, {"supa.filed_on": "2002-01-10", "supa.bar_c": "No"}, "Sam")
    out = " ".join(family.status(d)["problems"])
    assert "doesn't grandfather the client" in out and "245.10(a)(1)" in out and "no bar to adjusting under INA 245(a) is marked" in out
    family.answer(d, {"supa.filed_on": "1997-03-01"}, "Sam")
    assert "choose the basis for those dates (1.a or 1.c)" in " ".join(family.status(d)["problems"])


def test_one_i864a_per_household_member_the_client_without_dependents_signs_none(tmp_path, monkeypatch):
    monkeypatch.setattr(packet, "_today", lambda: date(2026, 10, 1))
    d = _case(tmp_path)
    family.answer(d, {"i864.current_income": "20000", "i864a.count": "2", "i864a1.relationship": family.HM_PARENT, "i864a1.family_name": "EXEMPLO",
                      "i864a1.given_name": "ROSA", "i864a1.dob": "1955-02-02", "i864a1.country_of_birth": "USA", "i864a1.employment": "Retired",
                      "i864a1.income": "15000", "i864a1.tax_year1": "2025", "i864a1.tax_income1": "15000",
                      "i864a2.relationship": family.HM_IMMIGRANT_SPOUSE, "i864a2.employment": "Employed", "i864a2.occupation": "CASHIER",
                      "i864a2.employer": "SHOP INC", "i864a2.income": "18000", "i864a2.tax_year1": "2025", "i864a2.tax_income1": "18000"}, "Sam")
    s = family.status(d)
    asked = {q["key"]: q["value"] for q in s["questions"]}
    assert asked["i864a2.family_name"] == "SOUZA" and asked["i864a1.city"] == "Boston"  # the client's own facts; the sponsor's address
    assert not any("I-864" in p and "below" in p for p in s["problems"])  # $53,000 counted for 2: above $27,050
    assert any("The client's own income is counted without one" in n["text"] for n in s["notes"])
    schema = packet.for_case(packet.load_filing("family"), d)
    assert schema["forms"][schema["forms"].index("i864"):][:2] == ["i864", "i864a_1"] and "i864a_2" not in schema["forms"]
    m = packet.build(d, ROW, "Sam", packet.load_filing("family"))
    assert "I-864A (1)" in [s["tab"] for s in m["sections"]]
    assert {(s["form"], s["who"]) for s in m["signatures"]} >= {("I-864A (1)", "household member"), ("I-864A (1)", "petitioner")}
    a = _boxes(d / "i864a_1_filled.pdf")
    assert (a["P1_Line1a_FamilyName[0]"], a["P1_Line1b_GivenName[0]"], a["P1_Line5_DateOfBirth[0]"]) == ("EXEMPLO", "ROSA", "02/02/1955")
    assert a["P2_Line1-3_Checkbox[2]"] == "/C" and a["P2_Line3_A_Relationship[2]"] == "/3" and a["P3_Line5_Employment[0]"] == "/C"
    assert a["P5_SponsorName[0]"] == "MICHAEL EXEMPLO" and a["P5_Line1a_FamilyName[0]"] == "SOUZA" and a["P5_IntendingMigrants[0]"] == "1"
    assert a["P4_Line1_CB[0]"] == "/Y" and (a["P4_Line2a_TaxYear[0]"], a["P4_Line2a_TotalIncome[0]"]) == ("2025", "15000")
    assert a["P1_Line2_CityOrTown[0]"] == "Boston" and a["Part9_Iamfluent[0]"] == "ROSA EXEMPLO"
    i864 = _boxes(d / "i864_filled.pdf")
    assert (i864["P6_Line3_Name[0]"], i864["P6_Line4_Relationship[0]"], i864["P6_Line5_CurrentIncome[0]"]) == ("ROSA EXEMPLO", "PARENT", "15000")
    assert i864["P6_Line6_Name[0]"] == "ANA SOUZA" and i864["P6_Line15_TotalHouseholdIncome[0]"] == "53000"
    assert i864["P6_Line16_CompletedForm[0]"] == "/Y" and i864["P6_Line17_NotNeedComplete[0]"] == "/Y" and i864["P6_Line17_Name[0]"] == "ANA SOUZA"
    assert "Household Member’s I-864A - Contract Between Sponsor and Household Member (ROSA EXEMPLO)" in _letter(d, m)
    family.answer(d, {"i864a1.dob": "2010-01-01"}, "Sam")
    assert any("Household member 1 is under 18" in p for p in family.status(d)["problems"])


def test_the_i864ez_when_the_attorney_confirms_it_fits(tmp_path, monkeypatch):
    monkeypatch.setattr(packet, "_today", lambda: date(2026, 10, 1))
    d = _case(tmp_path)
    assert any(n["title"] == "Form I-864EZ" and n["text"].startswith("May fit") for n in family.status(d)["notes"])
    family.answer(d, {"i864.use_ez": "Yes"}, "Sam")
    assert family.status(d)["problems"] == [p for p in family.status(d)["problems"] if "I-864EZ" not in p]
    schema = packet.for_case(packet.load_filing("family"), d)
    assert "i864ez" in schema["forms"] and "i864" not in schema["forms"]
    m = packet.build(d, ROW, "Sam", packet.load_filing("family"))
    ez = _boxes(d / "i864ez_filled.pdf")
    assert ez["P1_Line1a_Checkbox[1]"] == "/Y" and ez["P1_Line1b_Checkbox[1]"] == "/Y" and ez["P1_Line1c_Checkbox[1]"] == "/Y"
    assert (ez["Part3_Line1a_FamilyName[0]"], ez["P3_Line10_SSN[0]"], ez["P3_Line12_Checkbox[0]"]) == ("EXEMPLO", "999012345", "/A")
    assert (ez["Part2_Line1a_FamilyName[0]"], ez["P4_Line1f_AddTogether[0]"], ez["P5_Line4_AnnualIncome[0]"]) == ("SOUZA", "2", "52300")
    assert ez["P5_Line1_Checkbox[0]"] == "/E" and ez["P5_Line2b_NameofEmployer[0]"] == "ACME LLC"
    letter = _letter(d, m)
    assert "Petitioner’s I-864EZ - Affidavit of Support" in letter and "Forms I-130A and I-864EZ have no filing fee" in letter
    family.answer(d, {"petitioner.employer1_name": "SELF-EMPLOYED"}, "Sam")
    assert any(p.startswith("Form I-864EZ can't be used: the sponsor is self-employed") for p in family.status(d)["problems"])
