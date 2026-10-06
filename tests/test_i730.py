"""Form I-730 for an asylee's spouse and children (src/i730.py): one petition per relative within 2 years of the grant
(8 CFR 208.21(c)-(d)), the relationship as of the grant (208.21(b)), no fee (Form G-1055 10/01/26), mailed to the Phoenix
P.O. Box 20018 (uscis.gov/i-730) with the G-1145; the deadline on the case's timeline. Every client value is CONSTRUCTED."""

import json
from datetime import date, timedelta
from pathlib import Path

from pypdf import PdfReader, PdfWriter

import filing_questions
import i730
import journey
import packet
from factgraph import FactGraph

REPO = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 1)
ROW = {"summary": {"name": "ANA EXEMPLO"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}


def _asylee(tmp_path, granted="2025-01-15", **extra):
    g = FactGraph("asy1")
    facts = {"applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA", "applicant.dob": "1990-03-14", "applicant.sex": "F",
             "applicant.country_of_birth": "VENEZUELA", "applicant.citizenship": "VENEZUELA", "applicant.a_number": "A099000111",
             "applicant.physical_street": "10 EXAMPLE ST", "applicant.physical_city": "Boston", "applicant.physical_state": "MA",
             "applicant.physical_zip": "02110", "applicant.daytime_phone": "6175550100", "applicant.marriage_date": "2015-06-01",
             "applicant.spouse_family_name": "EXEMPLO", "applicant.spouse_given_name": "JOSE", "applicant.spouse_dob": "1988-08-08",
             "applicant.spouse_country_of_birth": "VENEZUELA"} | {k.replace("__", "."): v for k, v in extra.items()}
    for k, v in facts.items():
        g.add_source(k, "doc.pdf", "passport", v, v, 0.95)
    g.add_source(f"folder.uscis_case.ZLA2590000101.approval_{granted.replace('-', '')}", "approval.pdf", "uscis_notice", "ZLA2590000101",
                 f"I-589 APPROVAL, {granted}", 0.9)
    d = tmp_path / "bundle"
    d.mkdir()
    g.save(d / "fact_graph.json")
    source = tmp_path / "source"
    source.mkdir()
    docs = {"approval.pdf": "uscis_notice", "marriage.pdf": "marriage_certificate", "child birth.pdf": "birth_certificate", "i94.pdf": "i94"}
    for name in docs:
        w = PdfWriter()
        w.add_blank_page(width=612, height=792)
        with open(source / name, "wb") as fh:
            w.write(fh)
    (d / "meta.json").write_text(json.dumps({"source_folder": str(source), "classifications": docs}))
    return d


def _answer(d, values):
    return filing_questions.answer("i730", d, values, "Sam")


RELATIVES = {"i730.principal": "Yes", "i730.granted_city": "BOSTON", "i730.granted_state": "MA", "i730.count": "2",
             "i730.r1_relationship": "Spouse", "i730.r1_sex": "M", "i730.r1_citizenship": "VENEZUELA", "i730.r1_location": "Outside the United States",
             "i730.r1_street": "CALLE 5 # 10", "i730.r1_city": "CARACAS", "i730.r1_region": "DISTRITO CAPITAL", "i730.r1_country": "VENEZUELA",
             "i730.r1_consulate": "BOGOTA, COLOMBIA",
             "i730.r2_relationship": "Unmarried child: biological", "i730.r2_family_name": "EXEMPLO", "i730.r2_given_name": "LUIS",
             "i730.r2_dob": "2016-02-02", "i730.r2_sex": "M", "i730.r2_country_of_birth": "VENEZUELA", "i730.r2_citizenship": "VENEZUELA",
             "i730.r2_location": "In the United States", "i730.r2_street": "10 EXAMPLE ST", "i730.r2_city": "Boston", "i730.r2_region": "MA",
             "i730.r2_postal_code": "02110", "i730.r2_country": "USA"}


def test_the_deadline_two_years_from_the_grant_is_on_the_timeline(tmp_path):
    d = _asylee(tmp_path)
    j = journey.journey(d, TODAY)
    assert j["stage"] == "asylee"
    dl = next(x for x in j["deadlines"] if x["id"] == "i730")
    assert dl["date"] == "2027-01-14" and "8 CFR 208.21(d)" in dl["what"]  # the day before the 2nd anniversary of 01/15/2025
    assert any(s["id"] == "i730" for s in j["steps"])
    nxt = next(f for f in j["next_filings"] if f["filing"] == "i730")
    assert nxt["label"] == "Spouse or children (I-730): USCIS must receive it by 01/14/2027"
    journey.mark(d, "done", "Sam", "i730")  # no spouse or child to bring: the attorney marks it done
    assert not any(x["id"] == "i730" for x in journey.journey(d, TODAY)["deadlines"])


def test_one_i730_per_relative_no_fee_to_the_phoenix_lockbox(tmp_path, monkeypatch):
    monkeypatch.setattr(packet, "_today", lambda: TODAY)
    d = _asylee(tmp_path)
    s = _answer(d, RELATIVES)
    asked = {q["key"]: q["value"] for q in s["questions"]}
    assert asked["i730.status"] == "Asylee" and asked["asylee.granted_on"] == "2025-01-15"
    assert (asked["i730.r1_family_name"], asked["i730.r1_given_name"], asked["i730.r1_dob"]) == ("EXEMPLO", "JOSE", "1988-08-08")  # the spouse in the case
    assert s["problems"] == [], s["problems"]
    text = " ".join(n["text"] for n in s["notes"])
    assert "by 01/14/2027" in text and "$0" in text and "P.O. Box 20018" in text
    schema = packet.for_case(packet.load_filing("i730"), d)
    assert schema["forms"] == ["g28_i730_1", "i730_1", "g28_i730_2", "i730_2"]
    hand = " ".join(h["text"] for h in schema["handwork"])
    assert "I-730 (2): a copy of both sides of the relative's Form I-94" in hand and "I-730 (2): the relative is in the U.S. and 14" not in hand  # Luis is 10
    m = packet.build(d, ROW, "Sam", packet.load_filing("i730"))
    tabs = [x["tab"] for x in m["sections"]]
    assert tabs[:2] == ["G-1145", "Cover letter"] and m["payments"] == []  # a lockbox filing; nothing to pay
    assert tabs[2:6] == ["G-28 (I-730, 1)", "I-730 (1)", "G-28 (I-730, 2)", "I-730 (2)"]
    one = {k.rsplit(".", 1)[-1]: v.get("/V") for k, v in PdfReader(str(d / "i730_1_filled.pdf")).get_fields().items()}
    assert one["Beneficiary[0]"] == "/S" and one["Status[1]"] == "/ASL" and one["Pt1Line21_DateofAsyleeStatus[0]"] == "01/15/2025"
    count = {".".join(k.split(".")[-2:]): v.get("/V") for k, v in PdfReader(str(d / "i730_1_filled.pdf")).get_fields().items()}  # page 12 reuses the names
    assert (count["P4[0].TextField1[2]"], count["P4[0].TextField1[1]"], count["P4[0].TextField1[0]"]) == ("2", "1", "2")
    assert (one["Pt2_Line1_FamilyName[0]"], one["Pt2_Line1_GivenName[0]"], one["P2_Line2_City[0]"], one["P2_Line2_Province[0]"]) == \
        ("EXEMPLO", "JOSE", "CARACAS", "DISTRITO CAPITAL")
    assert one["Part2_Beneficiary[0]"] == "/B" and one["Pt2_CityCountry[0]"] == "BOGOTA, COLOMBIA"
    assert one["Part3_2year[0]"] == "/Y" and one["Part3_2year[1]"] in (None, "/Off")  # Part 3: No -- the printed No box is [0], value /Y
    assert (one["Part2_Line12_FamilyName[0]"], one["Part2_Line13_DateofPresentMarriage[0]"]) == ("EXEMPLO", "06/01/2015")  # his spouse is the client
    assert (one["Pt1Line1_FamilyName[0]"], one["Part1_Item9_AlienNum[0]"]) == ("EXEMPLO", "099000111")
    two = {k.rsplit(".", 1)[-1]: v.get("/V") for k, v in PdfReader(str(d / "i730_2_filled.pdf")).get_fields().items()}
    assert two["Beneficiary[1]"] == "/U" and two["Child[0]"] == "/BC" and two["Part2_Beneficiary[1]"] == "/A"
    assert (two["P2_Line2_ZipCode[0]"], two["DateofBirth[1]"]) == ("02110", "02/02/2016")
    g28 = {k.rsplit(".", 1)[-1]: v.get("/V") for k, v in PdfReader(str(d / "g28_i730_2_filled.pdf")).get_fields().items()}
    assert g28["Line1b_ListFormNumber[0]"] == "I-730" and g28["Line4_Checkbox[3]"] == "/P"
    first = next(x["first_page"] for x in m["sections"] if x["tab"] == "Cover letter")
    letter = " ".join(" ".join(p.extract_text() for p in PdfReader(str(d / "packet_i730.pdf")).pages[first - 1:first + 1]).split())
    assert "P.O. Box 20018" in letter and "No filing fee is required for Form I-730" in letter
    assert "Petitioner’s I-730 - Refugee/Asylee Relative Petition for LUIS EXEMPLO (Unmarried child)" in letter


def test_late_married_after_the_grant_or_a_derivative(tmp_path):
    d = _asylee(tmp_path, granted="2023-05-01", applicant__marriage_date="2024-02-02")
    s = _answer(d, RELATIVES | {"i730.principal": "No", "i730.r1_consulate": ""})
    out = " ".join(s["problems"])
    assert "explain in Part 3 why the I-730 is late" in out and "8 CFR 208.21(d)" in out
    assert "married 02/02/2024, after the grant on 05/01/2023" in out and "208.21(b)" in out
    assert "can't file an I-730" in out and "the U.S. embassy or consulate" in out
    assert any(n["level"] == "warn" and "has passed" in n["text"] for n in s["notes"])
    g = filing_questions.graph_for("i730", d, TODAY)
    assert g.get("i730.late").value == "Yes" and i730.last_day(date(2023, 5, 1)) == date(2025, 4, 30)
    assert i730.fee(g, TODAY)[0] == 0
    from fill.companion import fill_companions, load_profile

    profile = load_profile()
    fill_companions(i730.person_graph(g, "i730", 1), tmp_path, {**profile, "forms": {"i730_1": packet.instance("i730_1", profile["forms"])}})
    late = {k.rsplit(".", 1)[-1]: v.get("/V") for k, v in PdfReader(str(tmp_path / "i730_1_filled.pdf")).get_fields().items()}
    assert late["Part3_2year[1]"] == "/N" and late["Part3_2year[0]"] in (None, "/Off")  # Part 3: Yes -- the printed Yes box is [1], value /N


def test_a_refugee_counts_from_the_admission(tmp_path):
    d = _asylee(tmp_path, applicant__i94_class_of_admission="RE1", applicant__i94_arrival_date="2025-03-01")
    _answer(d, {"i730.status": "Refugee", "asylee.refugee_admitted_on": "2025-03-01"})
    g = filing_questions.graph_for("i730", d, TODAY)
    begun, what = i730.start(g)
    assert begun == date(2025, 3, 1) and "207.7(d)" in what and i730.last_day(begun) == date(2027, 2, 28)
    assert any("Form I-590" in h["text"] for h in i730.case_schema({"forms": [], "handwork": []}, g, TODAY)["handwork"])
    assert date(2027, 2, 28) - date(2025, 3, 1) < timedelta(days=731)
