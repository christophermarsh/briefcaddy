"""The T visa (src/t_visa.py, src/t_visa_declaration.py): the four requirements
of INA 101(a)(15)(T)(i) and 8 CFR 214.202 with their exceptions, the family
members 8 CFR 214.211 allows, no fee (Form G-1055 10/01/26), the lockbox by
state (uscis.gov/i-914), the filled I-914, a Supplement A per family member and
the I-192 in one package, Supplement B optional and asked for separately, and
the T visa track. Every client value is CONSTRUCTED.
"""

import json
import re
from datetime import date
from pathlib import Path

from pypdf import PdfReader, PdfWriter

import enotice
import fees
import filing_questions
import journey
import packet
import t_visa
import travel
from factgraph import FactGraph
from fill import fill_pdf
from fill.companion import fill_companions, load_profile
import schema_path

REPO = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 1)
BASE = {"applicant.family_name": "EXEMPLO SOUZA", "applicant.given_name": "ANA CLARA", "applicant.dob": "2001-03-14", "applicant.sex": "F",
        "applicant.marital_status": "Single", "applicant.country_of_birth": "BRAZIL", "applicant.citizenship": "BRAZIL",
        "applicant.physical_street": "10 EXAMPLE ST", "applicant.physical_city": "SOMERVILLE", "applicant.physical_state": "MA",
        "applicant.physical_zip": "02143", "applicant.i94_class_of_admission": "B2", "applicant.i94_admit_until_date": "2027-01-01",
        "firm.business_name": "EXAMPLE LAW LLP", "firm.street": "1 EXAMPLE PLAZA", "firm.city": "BOSTON", "firm.state": "MA", "firm.zip": "02108"}
ELIGIBLE = {"tvisa.victim": "Yes", "tvisa.cooperated": "Yes", "tvisa.exempt": "No", "tvisa.present": "Yes", "tvisa.hardship": "Yes",
            "tvisa.reported": "Yes", "tvisa.report_agency": "EXAMPLE CITY POLICE, HUMAN TRAFFICKING UNIT", "tvisa.report_street": "5 EXAMPLE AVE",
            "tvisa.report_city": "SOMERVILLE", "tvisa.report_state": "MA", "tvisa.report_zip": "02143", "tvisa.report_case_number": "HT-0001",
            "tvisa.under_18": "No", "tvisa.complied": "Yes", "tvisa.first_entry": "Yes", "tvisa.entry_on_account": "Yes", "tvisa.ead": "Yes",
            "tvisa.family_count": "0", "tvisa.safe_mailing": "The firm's office", "tvisa.statement_signed": "Yes", "tvisa.supb_wanted": "No",
            "tvisa.p4_all_no": "Yes"}


def _graph(**extra):
    g = FactGraph("t")
    for key, value in (BASE | ELIGIBLE | {k.replace("__", "."): v for k, v in extra.items()}).items():
        if value is not None:  # None: left out, for the case to derive
            g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _notice(g, receipt, form, kind, when, **extra):
    slug = f"{kind}_{when.replace('-', '')}"
    g.add_source(f"folder.uscis_case.{receipt}.{slug}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)
    for name, value in extra.items():
        g.add_source(f"folder.notice.{receipt}.{slug}.{name}", f"{kind}.pdf", "uscis_notice", value, value, 0.85)


def test_the_four_requirements_and_the_cooperation_exceptions(tmp_path):
    g = t_visa.derive(_graph(), TODAY)
    assert t_visa.problems(tmp_path, g, TODAY) == []
    bad = t_visa.derive(_graph(tvisa__victim="No", tvisa__present="No", tvisa__hardship="No", tvisa__cooperated="No", tvisa__complied="No",
                               tvisa__reported="No", tvisa__report_circumstances="Afraid of the trafficker."), TODAY)
    out = " ".join(t_visa.problems(tmp_path, bad, TODAY))
    for cite in ("214.202(a)", "214.202(b)", "214.202(d)", "214.202(c)", "214.208(b)"):
        assert cite in out, cite
    # the age exception: under 18 at an act of trafficking excuses the cooperation and the report (8 CFR 214.208(e)(2))
    young = t_visa.derive(_graph(tvisa__cooperated="No", tvisa__complied="No", tvisa__reported="No", tvisa__exempt="Yes", tvisa__under_18=None,
                                 tvisa__trafficking_began="2017-06-01", tvisa__report_circumstances="Was a child."), TODAY)
    assert young.get("tvisa.under_18").value == "Yes" and young.get("tvisa.exception").value == "Under 18 at an act of trafficking"
    assert t_visa.problems(tmp_path, young, TODAY) == []
    adult = t_visa.derive(_graph(tvisa__under_18=None, tvisa__trafficking_began="2023-06-01"), TODAY)
    assert adult.get("tvisa.under_18").value == "No"
    # the trauma exception needs its evidence (8 CFR 214.208(e)(1)); the signed statement is required initial evidence (214.204(c)(1))
    trauma = t_visa.derive(_graph(tvisa__exempt="Yes", tvisa__exception="Physical or psychological trauma", tvisa__statement_signed="No"), TODAY)
    out = " ".join(t_visa.problems(tmp_path, trauma, TODAY))
    assert "214.208(e)(1)" in out and "214.204(c)(1)" in out


def test_no_fee_and_the_lockbox_for_the_clients_state(tmp_path):
    paper = fees.load(TODAY)["paper"]
    assert (paper["i914"], paper["i914a"], paper["i914b"], paper["i192_t"], paper["i131_t"]) == (0, 0, 0, 0, 0)   # Form G-1055 10/01/26
    g = t_visa.derive(_graph(), TODAY)
    assert t_visa.fee(g, TODAY)[0] == 0
    lines, box = t_visa.address(g)
    assert box == "Elgin" and lines[1:3] == ["ATTN: I-914", "P.O. BOX 4221"]                                     # Massachusetts
    florida = t_visa.derive(_graph(applicant__physical_state="FL"), TODAY)
    assert t_visa.address(florida) == (["USCIS", "ATTN: I-914", "P.O. BOX 20200", "PHOENIX, AZ 85036-0200"], "Phoenix")
    chart = json.loads((schema_path.path("law", "uscis_lockboxes_i914")).read_text(encoding="utf-8"))["lockboxes"]
    listed = [s for b in chart.values() for s in b["states"]]
    states = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
              "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
              "WV", "WI", "WY"}
    assert states <= set(listed) and len(listed) == len(set(listed))
    letter = t_visa.letter(g, TODAY)
    assert letter["no_payment"] and "No filing fee is required for Form I-914" in letter["fees"] and "P.O. BOX 4221" in letter["mail_to"]
    assert "i914" in enotice.LOCKBOX_FILINGS and "i914b" not in enotice.LOCKBOX_FILINGS                          # the G-1145 on the I-914 only
    # an I-131 for a person seeking T status costs nothing either (G-1055, the I-131 exemptions)
    ap = t_visa.derive(_graph(i131__type=travel.AP_I485), TODAY)
    assert travel.fee(ap, TODAY)[0] == 0


def test_family_members_8_cfr_214_211(tmp_path):
    members = {"tvisa.family_count": "4",
               "tvisa.m1.relationship": "Parent", "tvisa.m1.given_name": "MARIA", "tvisa.m1.family_name": "EXEMPLO", "tvisa.m1.dob": "1975-01-01",
               "tvisa.m2.relationship": "Child", "tvisa.m2.given_name": "LUCAS", "tvisa.m2.family_name": "EXEMPLO", "tvisa.m2.dob": "2004-01-01",
               "tvisa.m3.relationship": "Unmarried sibling under 18", "tvisa.m3.given_name": "BIA", "tvisa.m3.family_name": "EXEMPLO",
               "tvisa.m3.dob": "2007-01-01", "tvisa.m4.relationship": "Child of my spouse", "tvisa.m4.given_name": "TOM", "tvisa.m4.family_name": "EXEMPLO",
               "tvisa.m4.dob": "2015-01-01"}
    g = t_visa.derive(_graph(**{k.replace(".", "__"): v for k, v in members.items()}), TODAY)
    out = " ".join(t_visa.member_problems(g, TODAY))   # the client is 25: a spouse and children only, unless there is danger of retaliation
    assert "MARIA EXEMPLO" in out and "214.211(a)(1), (a)(3)" in out
    assert "LUCAS EXEMPLO: a child must be under 21" in out and "BIA EXEMPLO: a sibling must be under 18" in out
    assert "TOM EXEMPLO: a derivative's child" in out
    safe = t_visa.derive(_graph(**{k.replace(".", "__"): v for k, v in members.items()}, tvisa__m1__retaliation="Yes", tvisa__m4__retaliation="Yes"), TODAY)
    assert not any(p.startswith(("MARIA", "TOM")) for p in t_visa.member_problems(safe, TODAY))
    minor = t_visa.derive(_graph(applicant__dob="2007-05-01", tvisa__family_count="1", tvisa__m1__relationship="Parent",
                                 tvisa__m1__given_name="MARIA", tvisa__m1__family_name="EXEMPLO"), TODAY)
    assert t_visa.member_problems(minor, TODAY) == []                                     # under 21: a parent qualifies outright
    assert t_visa.case_forms(["g28_i914", "i914"], g) == ["g28_i914", "i914", "i914a_1", "i914a_2", "i914a_3", "i914a_4"]
    assert g.get("tvisa.applying_for_family").value == "Yes" and g.get("tvisa.child1_given_name").value == "LUCAS"   # Part 5 lists the child


def _boxes(pdf):
    return {re.split(r"(?<!\\)\.", n)[-1]: x.get("/V") for n, x in PdfReader(str(pdf)).get_fields().items()}


def test_the_i914_is_filled_from_the_case(tmp_path):
    g = t_visa.derive(_graph(), TODAY)
    profile = load_profile()
    profile["forms"] = {"i914": profile["forms"]["i914"]}
    result = fill_companions(g, tmp_path, profile)["i914"]
    f = _boxes(tmp_path / "i914_filled.pdf")
    assert f["Part2_FamilyName[0]"] == "EXEMPLO SOUZA" and f["P1_Line2_DateOfBirth[0]"] == "03/14/2001" and f["Female[0]"] == "/2"
    assert f["CheckBox1[0]"] == "/1"                                            # Part 1: not filed before
    assert f["P1_Line8_StreetNumberName[0]"] == "1 EXAMPLE PLAZA" and f["P1_Line8_InCareofName[0]"] == "EXAMPLE LAW LLP"   # the safe address
    assert f["P3_Line12g_CurrentNon[0]"] == "B2"                                # the I-94 hasn't expired
    assert f["Q1_yes[0]"] == "/1" and f["Q5_yes[0]"] == "/Y" and f["Q11_no[0]"] == "/1"
    assert f["P3_Line5_CaseNumber[0]"] == "HT-0001" and "HUMAN TRAFFICKING UNIT" in f["P10_Line2d_AdditionalInfo[0]"]   # the agency's name in Part 9
    assert f["Dq1a_no[0]"] == "/N" and f["Dq3a_no[0]"] == "/1" and f["Dq23c_no[0]"] == "/1"   # every Part 4 answer No
    assert not [k for k in result["left_blank"] if k.startswith("tvisa.p4_")]


def _case(tmp_path):
    g = FactGraph("t1")
    for k, v in BASE.items():
        if not k.startswith("firm."):
            g.add_source(k, "doc.pdf", "passport", v, v, 0.95)
    d = tmp_path / "bundle"
    d.mkdir()
    g.save(d / "fact_graph.json")
    source = tmp_path / "source"
    source.mkdir()
    docs = {"statement.pdf": "declaration", "passport.pdf": "passport", "i94.pdf": "i94", "certidao.pdf": "birth_certificate"}
    for name in docs:
        w = PdfWriter()
        w.add_blank_page(width=612, height=792)
        with open(source / name, "wb") as fh:
            w.write(fh)
    (d / "meta.json").write_text(json.dumps({"source_folder": str(source), "classifications": docs}))
    fill_pdf(schema_path.path("template", "i485"), {}, d / "i485_filled.pdf")
    return d


def test_the_package_a_supplement_a_each_and_the_i192(tmp_path, monkeypatch):
    monkeypatch.setattr(packet, "_today", lambda: TODAY)
    d = _case(tmp_path)
    answers = ELIGIBLE | {"tvisa.current_status": "B2", "tvisa.family_count": "2", "tvisa.p4_all_no": "No", "tvisa.i192": "Yes",
                          "i192.grounds": "Entered without a visa because the trafficker brought her (8 CFR 212.16(b)(2)).", "i192.prior_request": "No",
                          "i192.six_months": "Yes", "i192.prior_applications": "No", "i192.denied": "No", "i192.arrested": "No",
                          "tvisa.m1.relationship": "Spouse", "tvisa.m1.family_name": "EXEMPLO", "tvisa.m1.given_name": "JOAO", "tvisa.m1.dob": "1998-02-02",
                          "tvisa.m1.sex": "Male", "tvisa.m1.marital_status": "Married", "tvisa.m1.country_of_birth": "BRAZIL", "tvisa.m1.citizenship": "BRAZIL",
                          "tvisa.m1.in_us": "Yes", "tvisa.m1.lives_with_client": "Yes", "tvisa.m1.ever_in_court": "No", "tvisa.m1.p4_all_no": "Yes",
                          "tvisa.m2.relationship": "Child", "tvisa.m2.family_name": "EXEMPLO", "tvisa.m2.given_name": "LIA", "tvisa.m2.dob": "2020-04-04",
                          "tvisa.m2.sex": "Female", "tvisa.m2.marital_status": "Single", "tvisa.m2.country_of_birth": "BRAZIL", "tvisa.m2.citizenship": "BRAZIL",
                          "tvisa.m2.in_us": "No", "tvisa.m2.ever_in_court": "No", "tvisa.m2.p4_all_no": "Yes"}
    answers |= {f"tvisa.p4_{k}": "No" for k, _ in t_visa.PART4} | {"tvisa.p4_9e": "Yes"}
    filing_questions.answer("i914", d, answers, "Sam")
    status = filing_questions.status("i914", d, TODAY)
    assert not [q for q in status["questions"] if q["required"] and q["value"] is None], status["problems"]
    schema = packet.load_filing("i914")
    row = {"summary": {"name": "ANA CLARA EXEMPLO SOUZA"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}
    plan = packet.plan(d, row, schema)
    assert [f["id"] for f in plan["forms"]] == ["g28_i914", "i914", "i914a_1", "i914a_2", "i192"]
    assert not any("Supplement B" in p for p in plan["problems"])               # optional: never waited for (8 CFR 214.204(e))
    m = packet.build(d, row, "Sam", schema)
    tabs = [s["tab"] for s in m["sections"]]
    assert tabs[:4] == ["G-1145", "Cover letter", "G-28 (I-914)", "I-914"] and not m["payments"]   # lockbox: the G-1145 on top; no fee
    assert "I-914 Supplement A (1)" in tabs and "I-192" in tabs
    assert m["mail_to"][2] == "P.O. BOX 4221"
    a1, a2 = _boxes(d / "i914a_1_filled.pdf"), _boxes(d / "i914a_2_filled.pdf")
    assert a1["GivenName[0]"] == "JOAO" and a1["pAFamilyMember[0]"] == "/H" and a1["P1Line3_StreetNumberName[0]"] == "10 EXAMPLE ST"
    assert a1["P2Line4_I914Status[1]"] == "/C" and a1["P2Line1_GivenName[0]"] == "ANA CLARA"      # filed together; Part 2 is the client
    assert a2["GivenName[0]"] == "LIA" and a2["pAFamilyMember[1]"] == "/C" and a2["Q1_no[0]"] == "/N" and a2["pELine3aYesNo[1]"] == "/N"
    i192 = _boxes(d / "i192_filled.pdf")
    assert i192["P1_Line1_CB[1]"] == "/A" and "trafficker" in i192["P2_Line26_GroundsofInadmissibility[0]"] and i192["P2_Line30_CB[0]"] == "/A"   # item 30 Yes
    i914 = _boxes(d / "i914_filled.pdf")
    assert i914["Dq10e_yes[0]"] == "/1" and i914["Q11_yes[0]"] == "/1"
    g28 = _boxes(d / "g28_i914_filled.pdf")
    assert g28["Line1b_ListFormNumber[0]"] == "I-914, I-914A, I-192"
    who = {s["who"] for s in m["signatures"]}
    assert {"client", "attorney", "family member"} <= who


def test_supplement_b_is_asked_for_separately(tmp_path, monkeypatch):
    monkeypatch.setattr(packet, "_today", lambda: TODAY)
    d = _case(tmp_path)
    filing_questions.answer("i914", d, {"tvisa.reported": "Yes", "tvisa.report_agency": "EXAMPLE COUNTY DISTRICT ATTORNEY", "tvisa.report_street": "9 COURT ST",
                                        "tvisa.report_city": "CAMBRIDGE", "tvisa.report_state": "MA", "tvisa.report_zip": "02141", "tvisa.supb_wanted": "Yes"}, "Sam")
    s = filing_questions.status("i914b", d, TODAY)
    by = {q["key"]: q["value"] for q in s["questions"]}
    assert by["tvisa.lea_name"] == "EXAMPLE COUNTY DISTRICT ATTORNEY" and by["tvisa.lea_city"] == "CAMBRIDGE"   # from Part 3, 5
    assert s["problems"] == []
    schema = packet.load_filing("i914b")
    row = {"summary": {"name": "ANA CLARA EXEMPLO SOUZA"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}
    m = packet.build(d, row, "Sam", schema)
    assert [x["tab"] for x in m["sections"]] == ["Request letter", "I-914 Supplement B"] and not m["payments"]   # no G-1145, no fee, no cover letter
    letter = " ".join(p.extract_text() for p in PdfReader(str(d / "lea_request_letter.pdf")).pages)
    assert "EXAMPLE COUNTY DISTRICT ATTORNEY" in letter and "214.204(e)(4)" in letter
    b = _boxes(d / "i914b_filled.pdf")
    assert b["P1Line1_FamilyName[0]"] == "EXEMPLO SOUZA" and b["GivenNameFirstName[0]"] == "EXAMPLE COUNTY DISTRICT ATTORNEY"
    assert not b.get("P3Line2_Other[0]") and not b.get("P3_Line5_SSN[0]")      # the official writes the claim; the SSN is theirs to add
    # tracked on the T visa: requested, not back, and the I-914 still doesn't wait
    filing_questions.answer("i914b", d, {"tvisa.supb_requested_on": "2026-09-15", "tvisa.supb_returned": "Not yet"}, "Sam")
    notes = " ".join(n["text"] for n in filing_questions.status("i914", d, TODAY)["notes"])
    assert "Requested 09/15/2026, not back yet: the I-914 can be filed without it" in notes
    assert not any("Supplement B" in p for p in filing_questions.problems("i914", d, TODAY))


def test_the_t_visa_track(tmp_path):
    d = tmp_path / "bundle"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"classifications": {}}), encoding="utf-8")
    (d / "fact_graph.json").write_text("{}", encoding="utf-8")
    g = _graph()
    j = journey.journey(d, TODAY, g)
    assert j["track"] == "t_visa" and j["stage"] == "t_ready" and j["next_filings"][0]["filing"] == "i914"
    _notice(g, "EAC2690001234", "I-914", "receipt", "2026-06-01")
    assert journey.journey(d, TODAY, g)["stage"] == "t_pending"
    _notice(g, "EAC2690001234", "I-914", "approval", "2026-09-01", valid_to="2030-08-31")
    j = journey.journey(d, TODAY, g)
    assert j["stage"] == "t_status" and any(x["id"] == "t_status_ends" and x["date"] == "2030-08-31" for x in j["deadlines"])
    assert journey.client_view(j, "pt")["title"] == "Status T aprovado"
