"""The VAWA self-petition (src/vawa.py): the second I-360 classification, beside
the Special Immigrant Juvenile -- who qualifies (INA 204(a)(1)(A)(iii)-(vii),
(B)(ii)-(v); 8 CFR 204.2(c), (e); USCIS Policy Manual Vol. 3 Part D), the
2-year rules, the safe mailing address (8 U.S.C. 1367), the "Attn: 1367"
lockboxes, the $0 fees (G-1055 10/01/26), the I-485 in the same envelope, the
filled boxes, and that the SIJ map is untouched. Every client value is
CONSTRUCTED ("Maria Exemplo", "Jose Exemplo Abusador").
"""

import json
import re
from datetime import date

import pytest
from pypdf import PdfReader

import enotice
import filing_questions
import packet
import vawa
from factgraph import FactGraph
from fill.companion import fill_companions, load_profile
import schema_path

TODAY = date(2026, 10, 2)
OFFICE = {"firm.business_name": "EXEMPLO LAW LLP", "firm.street": "1 EXAMPLE PLAZA", "firm.city": "BOSTON", "firm.state": "MA", "firm.zip": "02108",
          "firm.preparer_family_name": "ATTORNEY", "firm.preparer_given_name": "ANA", "firm.attorney_bar_number": "999999", "firm.phone": "6175550100",
          "firm.email": "office@example.com"}
SPOUSE = {"applicant.family_name": "EXEMPLO", "applicant.given_name": "MARIA", "applicant.dob": "1990-05-04", "applicant.sex": "F",
          "applicant.country_of_birth": "BRAZIL", "applicant.marital_status": "Married", "applicant.a_number": "A099999901",
          "applicant.physical_street": "77 HOME ST", "applicant.physical_city": "SOMERVILLE", "applicant.physical_state": "MA",
          "applicant.physical_zip": "02143", "applicant.mailing_street": "77 HOME ST", "applicant.mailing_city": "SOMERVILLE",
          "applicant.mailing_state": "MA", "applicant.mailing_zip": "02143", "applicant.daytime_phone": "6175550199",
          "vawa.classification": "Spouse", "vawa.abuser_status": vawa.USC_BORN, "vawa.abuser_family_name": "EXEMPLO ABUSADOR",
          "vawa.abuser_given_name": "JOSE", "vawa.abuser_dob": "1985-01-02", "vawa.abuser_country_of_birth": "UNITED STATES",
          "vawa.times_married": "1", "vawa.marriage_date": "2020-06-14", "vawa.marriage_place": "BOSTON, MA", "vawa.marriage_status": vawa.STILL_MARRIED,
          "vawa.good_faith": "Yes", "vawa.remarried": "No", "vawa.married_in_proceedings": "No", "vawa.lived_from": "2020-06-14",
          "vawa.lived_to": "2026-03-01", "vawa.lives_with_abuser": "No", "vawa.last_street": "12 SHARED AVE", "vawa.last_apt": "3",
          "vawa.last_city": "REVERE", "vawa.last_state": "MA", "vawa.last_zip": "02151", "vawa.last_from": "2023-01-01", "vawa.last_to": "2026-03-01",
          "vawa.abused": "Yes", "vawa.abused_who": "The client", "vawa.gmc_concern": "No", "vawa.in_us": "Yes",
          "applicant.part9.in_removal_proceedings": "No", "applicant.part9.worked_without_authorization": "No",
          "vawa.safe_address": vawa.OFFICE, "vawa.request_ead": "Yes", "vawa.concurrent_i485": "No"}


def _graph(base=SPOUSE, **extra):
    g = FactGraph("c")
    for key, value in (OFFICE | base | {k.replace("__", "."): v for k, v in extra.items()}).items():
        if value is not None:
            g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _notice(g, receipt, form, kind, when):
    g.add_source(f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)


def _fill(g, tmp_path, *form_ids):
    profile = load_profile()
    profile["forms"] = {f: profile["forms"][f] for f in form_ids}
    fill_companions(g, tmp_path, profile)


def _boxes(pdf):
    return {re.split(r"(?<!\\)\.", n)[-1]: f.get("/V") for n, f in PdfReader(str(pdf)).get_fields().items()}


def test_a_citizens_spouse_files_alone_with_the_offices_safe_address(tmp_path):
    g = vawa.derive(_graph(), TODAY)
    assert vawa.problems(tmp_path, g, TODAY) == []
    assert vawa.variant(g) is None and vawa.fee(g, TODAY)[0] == 0
    v = lambda k: g.get(k).value  # noqa: E731
    assert (v("vawa.mail_street"), v("vawa.mail_city"), v("vawa.mail_in_care_of")) == ("1 EXAMPLE PLAZA", "BOSTON", "EXEMPLO LAW LLP")
    assert v("vawa.form_marriage_date") == "06/14/2020" and v("vawa.i485_attached") == "No" and v("vawa.other_petitions") == "No"
    letter = vawa.letter(g, TODAY)
    assert letter["mail_to"] == ["USCIS", "ATTN: 1367", "P.O. BOX 8075", "CHICAGO, IL 60680-8075"] and letter["no_payment"]
    assert "Classification: VAWA self-petitioning spouse" in letter["re_lines"] and "Protected under 8 U.S.C. 1367" in letter["re_lines"]
    assert "No filing fee" in letter["fees"] and "G-1055" in letter["fees"]
    _fill(g, tmp_path, "i360_vawa", "g28_vawa")
    f = _boxes(tmp_path / "i360_vawa_filled.pdf")
    assert f["Pt2Line1[7]"] == "/I" and all(f.get(f"Pt2Line1[{i}]") in (None, "/Off") for i in (2, 10, 11))     # I (spouse); never C (SIJ)
    assert f["Pt1Line7_StreetNumberName[0]"] == f["Pt3Line2_StreetNumberName[0]"] == "1 EXAMPLE PLAZA"            # the safe address, twice
    assert f["Pt1Line7_InCareofName[0]"] == "EXEMPLO LAW LLP" and f.get("Pt1Line1_FamilyName[0]") in (None, "")   # Part 1, 1-6: skipped
    assert "77 HOME" not in json.dumps({k: str(x) for k, x in f.items()}) and "12 SHARED" in f["Pt10Line10_StreetNumberName[0]"]
    assert (f["Pt10Line1_FamilyName[0]"], f["Pt10Line5_Checkbox[0]"], f["Pt9Line8a_DateOfMarriage[0]"]) == ("EXEMPLO ABUSADOR", "/A", "06/14/2020")
    assert f["Pt10Line9_DateFrom[0]"] == "06/14/2020" and f["Pt10Line12[0]"] == "/Y" and f["Pt4Line7[1]"] == "/N" and f["Pt3Line1_FamilyName[0]"] == "EXEMPLO"
    assert all(f.get(n) in (None, "", "/Off") for n in f if n.startswith("Pt8"))                                    # Part 8 (SIJ) left alone
    g28 = _boxes(tmp_path / "g28_vawa_filled.pdf")
    assert g28["Line12a_StreetNumberName[0]"] == "1 EXAMPLE PLAZA" and g28["Line1b_ListFormNumber[0]"] == "I-360"   # the client's address: the safe one


def test_no_safe_address_or_the_abusers_one_stops_the_packet(tmp_path):
    none = vawa.derive(_graph(vawa__safe_address=None), TODAY)
    assert any("No safe mailing address chosen" in p for p in vawa.problems(tmp_path, none, TODAY))
    half = vawa.derive(_graph(vawa__safe_address=vawa.ELSEWHERE, vawa__safe_street="PO BOX 12"), TODAY)
    assert any("No complete safe mailing address" in p for p in vawa.problems(tmp_path, half, TODAY))
    shared = _graph(vawa__safe_address=vawa.ELSEWHERE, vawa__safe_street="12 Shared Ave.", vawa__safe_city="Revere", vawa__safe_state="MA",
                    vawa__safe_zip="02151")
    assert any("shared with the abuser" in p for p in vawa.problems(tmp_path, vawa.derive(shared, TODAY), TODAY))
    friend = vawa.derive(_graph(vawa__safe_address=vawa.ELSEWHERE, vawa__safe_in_care_of="ANA EXEMPLO AMIGA", vawa__safe_street="PO BOX 12",
                                vawa__safe_city="CAMBRIDGE", vawa__safe_state="MA", vawa__safe_zip="02139"), TODAY)
    assert vawa.problems(tmp_path, friend, TODAY) == [] and friend.get("vawa.mail_street").value == "PO BOX 12"


def test_the_2_year_rules_and_the_childs_age(tmp_path):
    def probs(base=SPOUSE, **kw):
        return " ".join(vawa.problems(tmp_path, vawa.derive(_graph(base, **kw), TODAY), TODAY))

    late = probs(vawa__marriage_status=vawa.DIVORCED, vawa__marriage_ended_on="2024-01-10", vawa__divorce_connected="Yes")
    assert "Too late" in late and "01/09/2026" in late
    ok = _graph(vawa__marriage_status=vawa.DIVORCED, vawa__marriage_ended_on="2025-06-01", vawa__divorce_connected="Yes")
    assert vawa.deadline(ok, TODAY)["date"] == date(2027, 5, 31) and vawa.problems(tmp_path, vawa.derive(ok, TODAY), TODAY) == []
    assert "not connected" in probs(vawa__marriage_status=vawa.DIVORCED, vawa__marriage_ended_on="2025-06-01", vawa__divorce_connected="No")
    widow = _graph(vawa__marriage_status=vawa.DIED, vawa__marriage_ended_on="2026-02-01")
    assert vawa.deadline(widow, TODAY)["date"] == date(2028, 1, 31)
    assert "permanent resident spouse died" in probs(vawa__abuser_status=vawa.LPR, vawa__marriage_status=vawa.DIED, vawa__marriage_ended_on="2026-02-01")
    assert "not related to domestic violence" in probs(vawa__abuser_lost_status_on="2026-01-01", vawa__abuser_lost_status_dv="No")
    assert "remarriage" in probs(vawa__remarried="Yes") and "good faith" in probs(vawa__good_faith="No")
    assert "battery or extreme cruelty" in probs(vawa__abused="No")
    child = SPOUSE | {"vawa.classification": "Child", "applicant.marital_status": "Single", "vawa.marriage_date": None, "vawa.marriage_place": None}
    assert vawa.deadline(_graph(child, applicant__dob="2008-05-04"), TODAY)["date"] == date(2029, 5, 3)    # the day before 21
    assert "central reason" in probs(child, applicant__dob="2004-05-04", vawa__delay_central_reason="No")
    assert "Too late" not in probs(child, applicant__dob="2004-05-04", vawa__delay_central_reason="Yes")
    assert "25 or older" in probs(child, applicant__dob="2000-05-04")
    assert "unmarried" in probs(child, applicant__marital_status="Married")
    assert vawa.derive(_graph(child), TODAY).get("vawa.form_marriage_date").value == "N/A"                # Part 10, 8: N/A for a child
    parent = SPOUSE | {"vawa.classification": "Parent"}
    assert "U.S. citizen son or daughter" in probs(parent, vawa__abuser_status=vawa.LPR)
    assert "21 or older" in probs(parent, vawa__abuser_dob="2010-01-01")
    assert "Living abroad" in probs(vawa__in_us="No", vawa__abroad_basis="None of these")
    assert "Living abroad" not in probs(vawa__in_us="No", vawa__abroad_basis=vawa.ABROAD[2])


def test_the_i485_in_the_same_envelope(tmp_path):
    g = vawa.derive(_graph(vawa__concurrent_i485="Yes"), TODAY)
    assert vawa.variant(g) == "concurrent" and vawa.problems(tmp_path, g, TODAY) == [] and vawa.fee(g, TODAY)[0] == 0
    v = lambda k: g.get(k).value  # noqa: E731
    assert (v("vawa.other_petitions"), v("vawa.other_petitions_count"), v("vawa.i485_attached"), v("companion.g28_forms_vawa")) == ("Yes", "1", "Yes", "I-360, I-485")
    assert "Filed concurrently: I-485 Application to Register Permanent Residence or Adjust Status" in vawa.letter(g, TODAY)["re_lines"]
    vawa.render(tmp_path, g, TODAY)
    f = _boxes(tmp_path / vawa.I485_OUTPUT)
    assert f["Pt2Line3a_CB[12]"] == "/3a12" and f["Pt3Line1_CB[3]"] == "/3" and f["Pt9Line56_CB[5]"] == "/5"   # VAWA spouse, no I-864, no public charge
    assert f["Pt1Line18_CurrentStreetNumberName[0]"] == "1 EXAMPLE PLAZA" and f["Pt1Line18_YN[1]"] == "/N"  # the safe mailing address
    assert g.get("applicant.mailing_street").value == "77 HOME ST"                                            # the case keeps the client's own
    lpr = vawa.derive(_graph(vawa__concurrent_i485="Yes", vawa__abuser_status=vawa.LPR), TODAY)
    assert any("visa is available" in p for p in vawa.problems(tmp_path, lpr, TODAY))
    assert vawa.problems(tmp_path, vawa.derive(_graph(vawa__concurrent_i485="Yes", vawa__abuser_status=vawa.LPR, vawa__visa_available="Yes"),
                                               TODAY), TODAY) == []
    abroad = vawa.derive(_graph(vawa__concurrent_i485="Yes", vawa__in_us="No", vawa__abroad_basis=vawa.ABROAD[2]), TODAY)
    assert any("no I-485 can be filed" in p for p in vawa.problems(tmp_path, abroad, TODAY))


def test_after_the_i360_is_filed_only_the_i485_is_left(tmp_path):
    g = _graph()
    _notice(g, "EAC2690000001", "I-360", "receipt", "2026-08-01")
    vawa.derive(g, TODAY)
    assert vawa.variant(g) == "i485_only" and vawa.deadline(g, TODAY) is None
    hidden = filing_questions.hidden_sections(vawa, g)
    assert "The classification and the abuser (Part 2, Part 10)" in hidden and "The safe mailing address (Part 1, 7)" not in hidden
    assert vawa.letter(g, TODAY)["re_lines"][0].startswith("Application: I-485") and vawa.fee(g, TODAY)[0] == 0
    schema = packet.load_filing("vawa")
    assert schema["variants"]["i485_only"]["forms"] == ["g28_vawa", "i485_vawa"]


def test_the_sij_i360_is_untouched_and_never_confused():
    profile = load_profile()["forms"]
    sij, own = profile["i360"]["map"], profile["i360_vawa"]["map"]
    assert sij["companion.i360_classification"]["options"] == {"C": ["Pt2Line1[2]", "/C"]} and not any(k.startswith("vawa.") for k in sij)
    fields = json.dumps(own)
    assert "Pt2Line1[2]" not in fields and "Pt8" not in fields and own["vawa.classification"]["options"]["Child"] == ["Pt2Line1[11]", "/J"]
    g = _graph()
    _notice(g, "EAC2690000001", "I-360", "receipt", "2026-08-01")
    assert not filing_questions.sij(g)                                       # a VAWA I-360 is not an SIJ petition: no SIJ fee exemptions


def test_the_attn_1367_lockboxes_by_state():
    from filing_questions import lockbox

    states = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
              "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
              "WV", "WI", "WY"}
    assert all(lockbox(vawa.CHART, s)[0] for s in states)
    listed = [s for b in json.loads((schema_path.path("law", vawa.CHART)).read_text(encoding="utf-8"))["lockboxes"].values() for s in b["states"]]
    assert len(listed) == len(set(listed))
    assert lockbox(vawa.CHART, "FL")[0][2] == "P.O. BOX 4205" and lockbox(vawa.CHART, "TX")[0][2] == "P.O. BOX 650745"
    assert lockbox(vawa.CHART, "CA")[0][2] == "P.O. BOX 20020" and all(lockbox(vawa.CHART, s)[0][1] == "ATTN: 1367" for s in states)


def test_fees_receipt_email_and_registries(tmp_path, monkeypatch):
    import fees
    import payment
    import settings

    paper = fees.load(TODAY)["paper"]
    assert paper["i360_vawa"] == 0 and paper["i485_vawa"] == 0
    assert payment.payments(packet.load_filing("vawa"), tmp_path, vawa.derive(_graph(), TODAY), TODAY) == []
    assert filing_questions.module("vawa") is vawa and "vawa" in enotice.LOCKBOX_FILINGS
    real = settings.overlay
    monkeypatch.setattr(settings, "overlay", lambda name, default: {"recipient": "client"} if name == "enotice" else real(name, default))
    g = _graph(applicant__email="maria@example.com")
    assert enotice._contact(g, tmp_path, "vawa")["email"] != "maria@example.com"          # the office, never the client's own inbox
    assert enotice._contact(g, tmp_path, "i360")["email"] == "maria@example.com"


def test_the_case_path(tmp_path, monkeypatch):
    import journey

    d = tmp_path / "case"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"classifications": {}}), encoding="utf-8")
    g = _graph(vawa__marriage_status=vawa.DIVORCED, vawa__marriage_ended_on="2025-06-01", vawa__divorce_connected="Yes")
    j = journey.journey(d, TODAY, graph=g)
    assert (j["track"], j["stage"], j["track_name"]) == ("vawa", "vawa_ready", "VAWA self-petition") and j["next_filings"][0]["filing"] == "vawa"
    assert any(x["id"] == "vawa_deadline" and x["date"] == "2027-05-31" for x in j["deadlines"])
    _notice(g, "EAC2690000001", "I-360", "receipt", "2026-08-01")
    assert journey.journey(d, TODAY, graph=g)["stage"] == "vawa_pending"
    _notice(g, "EAC2690000001", "I-360", "approval", "2026-09-20")
    j = journey.journey(d, TODAY, graph=g)
    assert j["stage"] == "vawa_approved" and j["next_filings"][0]["filing"] == "vawa" and j["next_filings"][0]["now"]
    view = journey.client_view(j, "en")
    shown = " ".join([view["title"], view["now"], *(p["name"] for p in view["path"]), *(json.dumps(h) for h in view["happened"])]).lower()
    assert "abuse" not in shown and "vawa" not in shown, shown                                  # someone else may see the client's screen


@pytest.mark.parametrize("filing", ["vawa"])
def test_the_packet_schema_names_real_forms_and_documents(filing):
    schema = packet.load_filing(filing)
    forms = load_profile()["forms"]
    assert all(f in forms for f in schema["forms"]) and schema["exhibits"][0]["id"] == "declaration" and schema["exhibits"][0]["required"]
    for name, var in schema["variants"].items():
        assert all(f in forms or f in var.get("generated", {}) for f in var["forms"]), name
