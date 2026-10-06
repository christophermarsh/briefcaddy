"""The I-360 petition (Special Immigrant Juvenile), the step before the I-485:
the state court's order read (extract/sij_order.py), the 21st-birthday
deadline and the questions (src/i360.py), the filled form, and its packet."""

import json
from datetime import date

from pypdf import PdfReader, PdfWriter

import i360
import packet
from classify import classify_text
from extract.sij_order import extract
from factgraph import FactGraph
import schema_path

MA_ORDER = """COMMONWEALTH OF MASSACHUSETTS
THE TRIAL COURT
PROBATE AND FAMILY COURT
Suffolk Division                                   Docket No. SU24P1234GD
SPECIAL IMMIGRANT JUVENILE FINDINGS
In the matter of: ANA EXEMPLO SOUZA
After hearing, the Court makes the following findings pursuant to 8 U.S.C. s. 1101(a)(27)(J) and 8 C.F.R. s. 204.11:
1. The child has been placed under the custody of MARIA EXEMPLO LIMA, appointed as guardian.
2. Reunification of the child with the father, JOSE EXEMPLO SOUZA, is not viable due to abandonment and neglect.
3. It is not in the child's best interest to be returned to Brazil, the child's country of nationality.
SO ORDERED.
Justice of the Probate and Family Court
Date: 03/14/2024
"""


def _values(text):
    return {f.fact_key: f.normalized_value for f in extract(text)}


def test_the_court_order_is_read_for_part_8():
    v = _values(MA_ORDER)
    assert v["sij.court_state"] == "MA" and v["sij.docket_number"] == "SU24P1234GD" and v["sij.order_date"] == "2024-03-14"
    assert v["sij.declared_dependent"] == "Yes" and v["sij.placed_with"] == "MARIA EXEMPLO LIMA"
    assert (v["sij.reunification_parents"], v["sij.parent_name"]) == ("one", "JOSE EXEMPLO SOUZA")
    assert v["sij.ground_abandonment"] == v["sij.ground_neglect"] == "Yes" and "sij.ground_abuse" not in v
    assert v["sij.best_interest_determined"] == "Yes" and v["sij.best_interest_country"] == "BRAZIL"
    both = _values(MA_ORDER.replace("with the father, JOSE EXEMPLO SOUZA,", "with both parents"))
    assert both["sij.reunification_parents"] == "both" and "sij.parent_name" not in both


def test_the_rules_place_an_order_by_its_findings_not_a_title_alone():
    assert classify_text(MA_ORDER).doc_type == "sij_order"
    assert classify_text("SPECIAL IMMIGRANT JUVENILE FINDINGS").doc_type == "unclassified"  # unverified: a title alone isn't enough


def test_the_21st_birthday_decides_whether_the_petition_can_be_filed():
    today = date(2026, 10, 1)
    assert i360.age_check("2004-09-30", today)["level"] == "blocking"
    soon = i360.age_check("2005-11-15", today)
    assert soon["level"] == "urgent" and soon["days_left"] == 45 and "in-person" in soon["text"]
    assert i360.age_check("2008-01-01", today)["level"] == "ok"
    assert i360.turns_21("2008-02-29") == date(2029, 3, 1)
    assert i360.age_check(None, today)["level"] == "check"


def _case(tmp_path, sij=True, approved=False, dob="2008-05-01"):
    g = FactGraph("c1")
    facts = {"applicant.family_name": "EXEMPLO SOUZA", "applicant.given_name": "ANA", "applicant.dob": dob, "applicant.country_of_birth": "BRAZIL",
             "applicant.sex": "F", "applicant.marital_status": "Single", "applicant.a_number": "A099000001", "applicant.mailing_street": "10 EXAMPLE ST",
             "applicant.mailing_unit_type": "APT", "applicant.mailing_apt": "2", "applicant.mailing_city": "Boston", "applicant.mailing_state": "MA",
             "applicant.mailing_zip": "02110", "applicant.physical_state": "MA"}
    for k, v in facts.items():
        g.add_source(k, "doc.pdf", "passport", v, v, 0.95)
    if sij:
        for f in extract(MA_ORDER):
            g.add_source(f.fact_key, "order.pdf", "sij_order", f.raw_value, f.normalized_value, f.confidence)
    if approved:
        g.add_source("applicant.i360_receipt_number", "approval.pdf", "i360_approval", "IOE0912345678", "IOE0912345678", 0.98)
    d = tmp_path / "bundle"
    d.mkdir()
    g.save(d / "fact_graph.json")
    source = tmp_path / "source"
    source.mkdir()
    docs = {"order.pdf": "sij_order", "certidao.pdf": "birth_certificate", "passport.pdf": "passport", "social.pdf": "ssn_card"}
    if approved:
        docs["approval.pdf"] = "i360_approval"
    for name in docs:
        w = PdfWriter()
        w.add_blank_page(width=612, height=792)
        with open(source / name, "wb") as fh:
            w.write(fh)
    (d / "meta.json").write_text(json.dumps({"source_folder": str(source), "classifications": docs}))
    return d


def test_questions_show_their_sources_and_answers_are_saved_as_decisions(tmp_path):
    d = _case(tmp_path)
    s = i360.status(d, date(2026, 10, 1))
    by = {x["key"]: x for x in s["questions"]}
    assert by["sij.best_interest_determined"]["value"] == "Yes" and by["sij.best_interest_determined"]["sources"][0]["type"] == "sij_order"
    assert by["sij.hhs_custody"]["value"] is None and any("not answered" in p for p in s["problems"])
    i360.answer(d, {"sij.hhs_custody": "No", "sij.under_court_jurisdiction": "Yes", "applicant.part9.in_removal_proceedings": "No",
                    "applicant.part9.worked_without_authorization": "No", "sij.residing_in_placement": "Yes"}, "Sam")
    i360.answer(d, {"sij.hhs_custody": "No"}, "Sam")  # a later answer never erases the others
    s = i360.status(d, date(2026, 10, 1))
    assert {x["key"]: x["value"] for x in s["questions"]}["sij.under_court_jurisdiction"] == "Yes"
    assert not s["problems"]
    try:
        i360.answer(d, {"sij.hhs_custody": "Maybe"}, "Sam")
        raise AssertionError("an answer outside the choices is refused")
    except ValueError:
        pass


def test_the_i360_packet_fills_part_8_and_files_the_order_first(tmp_path, monkeypatch):
    monkeypatch.setattr(packet, "_today", lambda: date(2026, 10, 1))
    monkeypatch.setattr(i360, "age_check", lambda dob, today=None: {"level": "ok", "text": "fine", "turns_21": None, "days_left": 999})
    d = _case(tmp_path)
    i360.answer(d, {"sij.hhs_custody": "No", "sij.under_court_jurisdiction": "Yes", "applicant.part9.in_removal_proceedings": "No",
                    "applicant.part9.worked_without_authorization": "No"}, "Sam")
    schema = packet.load_filing("i360")
    row = {"summary": {"name": "ANA EXEMPLO SOUZA"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}
    plan = packet.plan(d, row, schema)
    assert [ex["id"] for ex in plan["exhibits"]] == ["court_order", "birth", "passport"]
    assert {f["doc"] for f in plan["left_out"]} == {"social.pdf"} and plan["filing"] == "i360"
    manifest = packet.build(d, row, "Sam", schema)
    assert (d / "packet_i360.pdf").exists() and not (d / "packet.pdf").exists()  # never replaces the I-485 packet
    assert [s["tab"] for s in manifest["sections"]][:5] == ["G-1145", "G-1450 (I-360, $250)", "Cover letter", "G-28 (I-360)", "I-360"]  # the G-1145, then the card authorization, on top
    form = {k.rsplit(".", 1)[-1]: v.get("/V") for k, v in PdfReader(str(d / "i360_filled.pdf")).get_fields().items()}
    assert form["Pt2Line1[2]"] == "/C"  # Special Immigrant Juvenile
    # the form's Part 1 note: an SIJ skips items 1-6 and gives a mailing address in item 7; the name goes in Part 3
    assert not form.get("Pt1Line1_FamilyName[0]") and form["Pt3Line1_FamilyName[0]"] == "EXEMPLO SOUZA"
    assert not form.get("Pt1Line6_Unit[1]") and form["Pt1Line7_Unit[1]"] == form["Pt3Line2_Unit[1]"] == "/ APT "  # one answer, ticked in item 7 and Part 3
    assert form["Pt1Line7_StreetNumberName[0]"] == form["Pt3Line2_StreetNumberName[0]"]
    assert form["Pt8Line3a[0]"] == "/O" and form["Pt8Line4b_NameOfParent[0]"] == "JOSE EXEMPLO SOUZA"
    assert form["Pt8Line3A_Checkbox[3]"] == "/C" and form["Pt8Line3A_Checkbox[2]"] == "/B"  # abandonment, neglect
    assert form["Pt8Line5[0]"] == "/Y" and form["Pt8Line6a[1]"] == "/N" and form["Pt4Line7[1]"] == "/N"
    letter = PdfReader(str(d / "packet_i360.pdf")).pages[next(s["first_page"] for s in manifest["sections"] if s["tab"] == "Cover letter") - 1].extract_text()
    assert "I-360" in letter and "P.O. BOX 805887" in letter and "Priority Date" not in letter
    assert "State Court Order" in letter


def test_an_approved_petition_or_a_missing_order_keeps_the_packet_a_draft(tmp_path):
    d = _case(tmp_path, approved=True)
    plan = packet.plan(d, {"blocking": 0, "fix": 0, "check": 0, "attorney": 0}, packet.load_filing("i360"))
    assert any("already has an approved I-360" in p for p in plan["problems"]) and not plan["ready"]
    assert any(f["doc"] == "approval.pdf" for f in plan["left_out"])


def test_the_uscis_address_follows_the_clients_state():
    from fill.cover_letter import load_config, mail_to

    config = load_config(schema_path.path("cover_letter", "i360"))
    assert mail_to(config, {"physical_state": "MA"})[0][2] == "P.O. BOX 805887"
    assert mail_to(config, {"physical_state": "TX"})[0][3].startswith("DALLAS")
    assert mail_to(config, {"physical_state": "NY"})[0][3].startswith("CAROL STREAM")
    lines, problems = mail_to(config, {})
    assert problems and "lockbox" in problems[0]
    assert config["letterhead"] == load_config()["letterhead"]  # the firm's letterhead, signer and closing come from the I-485 letter


# --- the Massachusetts forms (CJP 37 judgment, CJP 35 complaint), real wording --------------

CJP37_TEXT = """CJP 37 (11/8/24)  Disposition Code:  JCD ofpage
PURSUANT TO G. L. c. 119, § 39M
JUDGMENT AND FINDINGS ON
COMPLAINT FOR DEPENDENCY
Massachusetts Trial Court
Probate and Family Court
Docket No.
Division
5. Child is dependent on this Court for his/her protection, well-being, care and custody, findings, rulings, and orders or
referrals to support the health, safety, welfare of Child or to remedy the effects on Child of abuse, neglect, abandonment,
or similar circumstances.
6. abuse neglect abandonment or
a similar basis under Massachusetts law namely:
Reunification of Child with Parent One is not a viable option due to
8. It is not in Child's best interest to return to his/her and/or his/her parents' country of nationality or last habitual residence of
(Country)
"""
CJP35_TEXT = """CJP 35 (11/8/24) ofpage
COMPLAINT FOR DEPENDENCY Massachusetts Trial Court
Probate and Family Court
PURSUANT TO G. L. c. 119, § 39M
Reunification with Parent One is not a viable option for Child due to:
abuse neglect abandonment a similar basis under state law, namely: or
7. It is not in Child's best interest to return to
WHEREFORE, Plaintiff/Child requests that the Court:
"""
P = "form1[0].BodyPage1[0]."
FILLED = "\n".join(["=== FILLED FORM FIELDS ===", f"{P}S1[0].docketno[0]=SU24E0123QC", f"{P}S1[0].DropDownList1[0]=Suffolk",
                    f"{P}S3[0].TextField4[0]=JOSE", f"{P}S3[0].TextField4[2]=EXEMPLO SOUZA", f"{P}S8[0].CheckBox3[1]=/1", f"{P}S8[0].CheckBox3[2]=/1",
                    f"{P}S10[0].TextField4[0]=Brazil", f"{P}S10[0].S13[0].TextField4[0]=MARIA", f"{P}S10[0].S13[0].TextField4[2]=EXEMPLO LIMA",
                    f"{P}S15[0].DateTimeField1[0]=03/14/2024"])


def test_the_massachusetts_judgment_is_the_order_and_the_complaint_is_not():
    assert classify_text(CJP37_TEXT).doc_type == "sij_order"
    assert classify_text(CJP35_TEXT).doc_type != "sij_order"  # it asks for the findings; it isn't them


def test_a_scanned_cjp37_never_guesses_its_tick_boxes():
    v = _values(CJP37_TEXT)
    assert v["sij.order_form"] == "CJP 37" and v["sij.declared_dependent"] == v["sij.best_interest_determined"] == "Yes"
    assert not any(k.startswith(("sij.ground_", "sij.reunification", "sij.parent_name")) for k in v)  # every option is printed on every copy
    assert "sij.docket_number" not in v  # "Docket No." followed by "Division" is a label, not a number


def test_a_filled_cjp37_is_read_from_its_own_fields():
    v = _values(CJP37_TEXT + FILLED)
    assert (v["sij.docket_number"], v["sij.court_name"], v["sij.order_date"]) == ("SU24E0123QC", "PROBATE AND FAMILY COURT, SUFFOLK", "2024-03-14")
    assert (v["sij.reunification_parents"], v["sij.parent_name"]) == ("one", "JOSE EXEMPLO SOUZA")
    assert v["sij.ground_neglect"] == v["sij.ground_abandonment"] == "Yes" and "sij.ground_abuse" not in v
    assert (v["sij.best_interest_country"], v["sij.placed_with"]) == ("BRAZIL", "MARIA EXEMPLO LIMA")
    both = _values(CJP37_TEXT + FILLED + f"\n{P}Subform1[0].S8[0].CheckBox3[0]=/1")
    assert both["sij.reunification_parents"] == "both" and "sij.parent_name" not in both and both["sij.ground_abuse"] == "Yes"


def test_the_review_panel_says_why_a_scanned_cjp37_needs_reading(tmp_path):
    d = _case(tmp_path, sij=False)
    g = FactGraph.load(d / "fact_graph.json")
    for f in extract(CJP37_TEXT):
        g.add_source(f.fact_key, "order.pdf", "sij_order", f.raw_value, f.normalized_value, f.confidence)
    g.save(d / "fact_graph.json")
    assert any("CJP 37" in p and "tick boxes" in p for p in i360.status(d, date(2026, 10, 1))["problems"])
