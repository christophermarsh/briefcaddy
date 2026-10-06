"""The U visa (src/u_visa.py, src/u_certification.py): the certification asked of
the agency (Supplement B, Part 1 only), its six months (8 CFR 214.14(c)(2)(i)),
who qualifies and who can come along (INA 101(a)(15)(U); 214.14(a)(10), (f)),
the forms this case files, the fees (G-1055 10/01/26), where it is mailed
(uscis.gov/i-918) and the case path. Every client value is CONSTRUCTED.
"""

import json
import re
from datetime import date

import pytest
from pypdf import PdfReader

import u_certification
import u_visa
from factgraph import FactGraph
from filing_questions import lockbox
from fill.companion import fill_companions, load_profile
import schema_path

TODAY = date(2026, 10, 1)
STATES_AND_DC = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
                 "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
                 "WV", "WI", "WY"}
BASE = {"applicant.family_name": "EXEMPLO SOUZA", "applicant.given_name": "ANA", "applicant.middle_name": "CLARA", "applicant.dob": "1996-03-14",
        "applicant.sex": "F", "applicant.marital_status": "Single", "applicant.country_of_birth": "BRAZIL", "applicant.citizenship": "BRAZIL",
        "applicant.physical_street": "10 EXAMPLE ST", "applicant.physical_city": "SOMERVILLE", "applicant.physical_state": "MA", "applicant.physical_zip": "02143",
        "firm.business_name": "EXAMPLE LAW OFFICE", "firm.street": "1 EXAMPLE SQ", "firm.city": "BOSTON", "firm.state": "MA", "firm.zip": "02108",
        "uvisa.crime": "Felonious assault", "uvisa.crime_date": "2026-01-10", "uvisa.crime_place": "SOMERVILLE, MA", "uvisa.us_crime": "Yes",
        "uvisa.p2_abuse": "Yes", "uvisa.p2_information": "Yes", "uvisa.agency_name": "EXAMPLE CITY POLICE DEPARTMENT", "uvisa.supb_signed": "2026-08-20",
        "uvisa.supb_original": "Yes", "uvisa.safe_mailing": u_visa.SAFE[0], "uvisa.in_proceedings": "No", "uvisa.inadmissible": "No", "uvisa.members": "None"}


def _graph(**extra):
    g = FactGraph("c")
    for key, value in (BASE | {k.replace("__", "."): v for k, v in extra.items()}).items():
        if value is not None:
            g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _notice(g, receipt, form, kind, when):
    g.add_source(f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)


def _fields(pdf):
    return {re.split(r"(?<!\\)\.", n)[-1]: x.get("/V") for n, x in PdfReader(str(pdf)).get_fields().items()}


def _case_dir(tmp_path, classifications=None):
    d = tmp_path / "case"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"classifications": classifications or {}}), encoding="utf-8")
    return d


def test_a_ready_petition_fills_the_i918_and_goes_to_the_lockbox_for_the_state(tmp_path):
    g = u_visa.derive(_graph(), TODAY)
    assert u_visa.problems(tmp_path, g, TODAY) == []
    v = lambda k: g.get(k).value  # noqa: E731
    assert (v("uvisa.p2_victim"), v("uvisa.p2_supb"), v("uvisa.p2_under16"), v("uvisa.p4_family")) == ("Yes", "Yes", "No", "No")
    assert v("uvisa.safe_in_care_of") == "EXAMPLE LAW OFFICE" and v("uvisa.safe_street") == "1 EXAMPLE SQ"   # the office as the safe mailing address
    assert v("uvisa.g28_forms") == "I-918" and v("uvisa.i918_status") == "Pending"
    letter = u_visa.letter(g, TODAY)
    assert letter["mail_to"] == ["USCIS", "ATTN: 1367", "P.O. BOX 8075", "CHICAGO, IL 60680-8075"]     # Massachusetts: Chicago (uscis.gov/i-918)
    assert letter["no_payment"] and "$0" in letter["fees"] and "EXAMPLE CITY POLICE DEPARTMENT on 08/20/2026" in letter["forms"]["i918b_original"]
    florida = u_visa.derive(_graph(applicant__physical_state="FL"), TODAY)
    assert u_visa.letter(florida, TODAY)["mail_to"][2:] == ["P.O. BOX 4205", "CAROL STREAM, IL 60197-4205"]  # Florida: Elgin
    profile = load_profile()
    profile["forms"] = {"i918": profile["forms"]["i918"]}
    fill_companions(g, tmp_path, profile)
    f = _fields(tmp_path / "i918_filled.pdf")
    assert f["Pt1Line1a_FamilyName[0]"] == "EXEMPLO SOUZA" and f["P1_Line10_DateOfBirth[0]"] == "03/14/1996"
    assert f["P2_Line1_chbxyesno[0]"] == "/Yes" and f["P2_Line4_chbxyesno[0]"] == "/Yes" and f["P2_Line6_chbxyesno[1]"] == "/No"
    assert f["Pt2Line7a_chbxyesno[0]"] == "/N" and f["P4_26_chbxyesno[1]"] == "/No" and f["P1_Line9_checkboxes[0]"] == "/Female"
    assert f["P1_Line4a_InCareofName[0]"] == "EXAMPLE LAW OFFICE" and f["P1_Line3d_State[0]"].strip() == "MA"


def test_every_state_has_one_i918_lockbox_attn_1367():
    for state in STATES_AND_DC:
        assert lockbox(u_visa.CHART, state)[0], state
    boxes = json.loads((schema_path.path("law", u_visa.CHART)).read_text(encoding="utf-8"))["lockboxes"]
    listed = [s for b in boxes.values() for s in b["states"]]
    assert len(listed) == len(set(listed)) and all(b["usps"][1] == "ATTN: 1367" for b in boxes.values())
    import enotice

    assert "u_visa" in enotice.LOCKBOX_FILINGS and "u_cert" not in enotice.LOCKBOX_FILINGS   # a lockbox: the G-1145 on top; the request goes to the agency


def test_the_supplement_b_is_valid_six_months_from_its_signature(tmp_path):
    assert u_visa.supb_last_day(date(2026, 4, 1)) == date(2026, 9, 30)
    assert u_visa.supb_last_day(date(2026, 8, 31)) == date(2027, 2, 28)       # no Feb. 31: six months back from Mar. 1 is Sept. 1, past the signature
    late = u_visa.problems(tmp_path, u_visa.derive(_graph(uvisa__supb_signed="2026-03-31"), TODAY), TODAY)
    assert any("expired after 09/30/2026" in p and "214.14(c)(2)(i)" in p for p in late)
    assert u_visa.problems(tmp_path, u_visa.derive(_graph(uvisa__supb_signed="2026-04-02"), TODAY), TODAY) == []
    unsigned = " ".join(u_visa.problems(tmp_path, u_visa.derive(_graph(uvisa__supb_signed=None), TODAY), TODAY))
    assert "No signed Supplement B yet" in unsigned and "certification request" in unsigned
    copy = " ".join(u_visa.problems(tmp_path, _graph(uvisa__supb_original="No", uvisa__supb_signed="2026-10-05"), TODAY))
    assert "not a photocopy" in copy and "after today" in copy
    warn = next(n for n in u_visa.notes(_graph(uvisa__supb_signed="2026-04-10"), TODAY) if n["title"] == "The certification")
    assert warn["level"] == "warn" and "by 10/09/2026 (in 8 days)" in warn["text"] and "overnight" in warn["text"]
    # once the I-918 is with USCIS, the Supplement B that went with it no longer runs out
    filed = _graph(uvisa__supb_signed="2026-03-01")
    _notice(filed, "IOE0999001001", "I-918", "receipt", "2026-05-01")
    assert u_visa.problems(tmp_path, u_visa.derive(filed, TODAY), TODAY) == []


def test_who_qualifies_and_who_can_come_along(tmp_path):
    out = " ".join(u_visa.problems(tmp_path, _graph(uvisa__us_crime="No", uvisa__p2_abuse="No", uvisa__p2_information="No",
                                                    uvisa__crime=u_visa.SIMILAR), TODAY))
    for cite in ("(U)(i)(IV)", "(U)(i)(I)", "(U)(i)(II)", "(U)(iii)"):
        assert f"INA 101(a)(15){cite}" in out, cite
    members = dict(uvisa__members="3", uvisa__m1_relationship="Parent", uvisa__m1_given_name="ROSA", uvisa__m1_family_name="EXEMPLO",
                   uvisa__m2_relationship="Child", uvisa__m2_given_name="LUCAS", uvisa__m2_dob="2004-01-01", uvisa__m3_relationship="Spouse",
                   uvisa__m3_given_name="JOAO", uvisa__m3_perpetrator="Yes")
    out = " ".join(u_visa.problems(tmp_path, _graph(**members), TODAY))           # the client is 30
    assert "ROSA EXEMPLO: a parent qualifies only when the client is under 21" in out and "LUCAS: a child must be unmarried and under 21" in out
    assert "JOAO committed the crime in a family violence or trafficking context" in out and "214.14(f)(1)" in out
    young = dict(applicant__dob="2007-05-01", uvisa__members="2", uvisa__m1_relationship="Unmarried sibling under 18", uvisa__m1_given_name="BIA",
                 uvisa__m1_dob="2010-01-01", uvisa__m1_marital_status="Single", uvisa__m2_relationship="Parent", uvisa__m2_given_name="ROSA")
    assert u_visa.problems(tmp_path, _graph(**young), TODAY) == []                # a 19-year-old: a sibling under 18 and a parent qualify
    assert any("a sibling must be unmarried and under 18" in p for p in u_visa.problems(tmp_path, _graph(**(young | {"uvisa__m1_dob": "2008-01-01"})), TODAY))


def test_the_forms_this_case_files(tmp_path):
    forms = json.loads((schema_path.path("packet", "u_visa")).read_text(encoding="utf-8"))["forms"]
    assert u_visa.forms_for(_graph(), forms) == ["g28_i918", "i918"]
    g = _graph(uvisa__members="2", uvisa__inadmissible="Yes", i192__grounds="INA 212(a)(6)(A)(i): present without admission.",
               uvisa__m1_relationship="Spouse", uvisa__m1_family_name="EXEMPLO", uvisa__m1_given_name="JOAO", uvisa__m1_dob="1994-02-02",
               uvisa__m1_sex="Male", uvisa__m1_same_address="Yes", uvisa__m1_marital_status="Married")
    assert u_visa.forms_for(g, forms) == ["g28_i918", "i918", "i918a_1", "i918a_2", "i192"]
    u_visa.derive(g, TODAY)
    assert g.get("uvisa.g28_forms").value == "I-918, I-918 Supplement A, I-192" and g.get("uvisa.m1_city").value == "SOMERVILLE"
    profile = load_profile()
    profile["forms"] = {k: profile["forms"][k] for k in ("i918a_1", "i192")}
    fill_companions(g, tmp_path, profile)
    a = _fields(tmp_path / "i918a_1_filled.pdf")
    assert a["Part1_Line1_checkbox[0]"] == "/Spouse" and a["Pt3Line1b_GivenName[0]"] == "JOAO" and a["Pt1Line1a_FamilyName[0]"] == "EXEMPLO SOUZA"
    assert a["P3_Line12_checkbox[1]"] == "/Male" and a["Part2_Line5_checkbox[0]"] == "/Pending" and a["Pt3Line3c_CityTown[0]"] == "SOMERVILLE"
    w = _fields(tmp_path / "i192_filled.pdf")
    assert w["P1_Line1_CB[1]"] == "/A" and w["P2_Line26_GroundsofInadmissibility[0]"].startswith("INA 212(a)(6)(A)(i)")
    assert w["P2_Line6_CountryOfBirth[1]"] == "BRAZIL"                          # the country box (its twin [0] is the state)


def test_the_fees_come_from_the_g1055():
    import fees
    import packet
    import payment

    paper = fees.load(TODAY)["paper"]
    assert (paper["i918"], paper["i918a"], paper["i918b"], paper["i192"], paper["i192_u"]) == (0, 0, 0, 1100, 0)
    assert u_visa.fee(_graph(), TODAY)[0] == 0
    assert payment.payments(packet.load_filing("u_visa"), None, _graph(uvisa__inadmissible="Yes"), TODAY) == []   # nothing to pay: no G-1450


def test_the_certification_request_letter_and_part_1(tmp_path, monkeypatch):
    import offices

    monkeypatch.setattr(offices, "letter", lambda config, client_dir, state=None: config)
    d = _case_dir(tmp_path)
    g = _graph(uvisa__agency_case_number="2026-000123", uvisa__agency_attention="U visa certification unit", uvisa__agency_street="1 EXAMPLE PLAZA",
               uvisa__agency_city="SOMERVILLE", uvisa__agency_state="MA", uvisa__agency_zip="02143", uvisa__agency_source="the agency's web page, 10/01/2026",
               uvisa__supb_signed=None)
    u_certification.from_record({"filings": [{"filing": "u_cert", "mailed_on": "2026-09-30", "by": "Paula"}]}, g)
    assert g.get("uvisa.cert_requested").value == "2026-09-30"
    assert u_certification.problems(d, g, TODAY) == []
    u_certification.render(d, g, TODAY)
    text = " ".join(" ".join(p.extract_text() for p in PdfReader(str(d / u_certification.LETTER)).pages).split())
    for words in ("EXAMPLE CITY POLICE DEPARTMENT", "Attn: U visa certification unit", "2026-000123", "felonious assault", "Supplement B",
                  "six months", "8 CFR 214.14(a)(3)", "photocopy", "Ana Clara Exemplo Souza"):
        assert words in text, words
    assert "DRAFT" not in text
    profile = load_profile()
    profile["forms"] = {"i918b": profile["forms"]["i918b"]}
    result = fill_companions(g, d, profile)["i918b"]
    f = _fields(d / "i918b_filled.pdf")
    assert f["Pt1Line2a_FamilyName[0]"] == "EXEMPLO SOUZA" and f["Part1_5_checkboxes[0]"] == "/Female" and f["P1_Line4_DateOfBirth[0]"] == "03/14/1996"
    assert not f.get("Pt2Line1_CertifyingAgency[0]") and result["left_blank"] == []   # Parts 2-6: the certifying official's
    # no address yet: the letter is marked DRAFT
    u_certification.render(d, _graph(), TODAY)
    assert "DRAFT" in " ".join(p.extract_text() for p in PdfReader(str(d / u_certification.LETTER)).pages)


def test_the_case_path_on_the_u_track(tmp_path, monkeypatch):
    import journey

    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})
    d = _case_dir(tmp_path)
    j = journey.journey(d, TODAY, graph=_graph(uvisa__supb_signed=None))
    assert (j["track"], j["stage"]) == ("u_visa", "u_certification") and j["next_filings"][0] == {
        "filing": "u_cert", "label": "Ask the certifying agency to sign the certification (Supplement B)", "now": True}
    (d / "status.json").write_text(json.dumps({"filings": [{"filing": "u_cert", "mailed_on": "2026-09-15", "carrier": "USPS", "by": "Ana"}]}), encoding="utf-8")
    j = journey.journey(d, TODAY, graph=_graph(uvisa__supb_signed=None))
    assert j["stage"] == "u_certification" and j["next_filings"][0]["now"] is False and "requested 09/15/2026" in j["next_filings"][0]["label"]
    assert "Enviamos o pedido da certificação à autoridade em 15/09/2026." in [h["text"] for h in journey.client_view(j, "pt")["happened"]]
    j = journey.journey(d, TODAY, graph=_graph(uvisa__supb_signed="2026-09-20"))
    deadline = next(x for x in j["deadlines"] if x["id"] == "u_supb")
    assert j["stage"] == "u_ready" and deadline["date"] == "2027-03-19" and "214.14(c)(2)(i)" in deadline["what"]
    assert j["next_filings"][0]["filing"] == "u_visa" and j["next_filings"][0]["now"]
    g = _graph(uvisa__supb_signed="2026-09-20", uvisa__bfd_date="2026-12-01")
    _notice(g, "IOE0999001002", "I-918", "receipt", "2026-09-25")
    j = journey.journey(d, date(2027, 1, 5), graph=g)
    assert j["stage"] == "u_deferred" and not any(x["id"] == "u_supb" for x in j["deadlines"]) and "bona fide determination 12/01/2026" in j["why"]
    _notice(g, "IOE0999001002", "I-918", "approval", "2027-06-01")
    j = journey.journey(d, date(2027, 6, 5), graph=g)
    assert j["stage"] == "u_approved" and journey.client_view(j, "es")["title"] == "Visa U aprobada"
    with pytest.raises(ValueError):
        journey.mark(d, "track", "Ana", value="u")
    journey.mark(d, "track", "Ana", value="u_visa")
