"""After a USCIS denial (src/motion.py, src/hearing_request.py): the I-290B and
the N-336 -- which decisions can be appealed (USCIS's "When to Use Form I-290B"
chart), the 30/33-day deadline, the fees (G-1055 10/01/26), where each is
mailed, the filled boxes, and the journey putting it first. Every client value
is CONSTRUCTED.
"""

import json
import re
from datetime import date

import pytest
from pypdf import PdfReader

import hearing_request
import motion
from factgraph import FactGraph
from fill.companion import fill_companions, load_profile

TODAY = date(2026, 10, 1)
BASE = {"applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA", "applicant.a_number": "A099999999", "applicant.dob": "2004-03-14",
        "applicant.physical_street": "10 EXAMPLE ST", "applicant.physical_city": "SOMERVILLE", "applicant.physical_state": "MA", "applicant.physical_zip": "02143"}


def _graph(**extra):
    g = FactGraph("c")
    for key, value in (BASE | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _notice(g, receipt, form, kind, when):
    g.add_source(f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)


def _short(pdf):
    return {re.split(r"(?<!\\)\.", n)[-1]: f.get("/V") for n, f in PdfReader(str(pdf)).get_fields().items()}


@pytest.fixture
def case(tmp_path, monkeypatch):
    import journey

    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})
    d = tmp_path / "case"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"classifications": {}}), encoding="utf-8")
    return d


def test_an_sij_i485_denial_a_motion_free_to_chicago(case):
    import journey

    g = _graph(applicant__i360_receipt_number="IOE0999000001")
    _notice(g, "IOE0999000123", "I-485", "receipt", "2026-03-01")
    _notice(g, "IOE0999000123", "I-485", "denial", "2026-09-20")
    first = journey.journey(case, TODAY, graph=g)["next_filings"][0]
    assert first["filing"] == "i290b" and first["now"] and "by 10/20/2026 (30 days; 33 if USCIS mailed it)" in first["label"]
    motion.derive(g, TODAY)
    assert (g.get("motion.form").value, g.get("motion.receipt").value, g.get("motion.decision_date").value) == ("I-485", "IOE0999000123", "2026-09-20")
    assert motion.due(g) == date(2026, 10, 20)                                       # 30 days until the attorney says it was mailed
    assert motion.fee(g, TODAY)[0] == 0                                              # G-1055: SIJ, a motion on the I-485
    assert motion.mail_to(g)[0] == motion.SIJ_LINES                                  # Chicago, P.O. Box 5510
    g.add_source("motion.kind", "test", "test", motion.KINDS[0], motion.KINDS[0], 1.0)
    assert any("can't be appealed" in p for p in motion.problems(case, g, TODAY))   # USCIS's chart: an I-485 -- a motion only
    letter = motion.letter(g, TODAY)
    assert letter["no_payment"] and "Special Immigrant Juvenile" in letter["fees"] and "I-485" in letter["re_lines"][1]


def test_a_work_permit_denial_costs_800_and_goes_to_phoenix(case):
    g = _graph(motion__mailed="Yes", motion__kind="Motion to reopen")
    _notice(g, "EAC2690000555", "I-765", "denial", "2026-08-01")
    motion.derive(g, TODAY)
    assert motion.fee(g, TODAY)[0] == 800 and motion.mail_to(g)[0] == motion.OTHER_LINES
    assert motion.due(g) == date(2026, 9, 3)                                         # 33 days: mailed
    assert any("Past the deadline (09/03/2026)" in p for p in motion.problems(case, g, TODAY))


def test_the_i290b_boxes(case, tmp_path):
    g = _graph(motion__kind="Motion to reconsider", motion__office="Boston (BOS)", motion__basis="The decision misread the evidence.")
    _notice(g, "EAC2690000555", "I-765", "denial", "2026-09-25")
    motion.derive(g, TODAY)
    profile = load_profile()
    profile["forms"] = {"i290b": profile["forms"]["i290b"]}
    fill_companions(g, tmp_path, profile)
    v = _short(tmp_path / "i290b_filled.pdf")
    assert v["P2_Line2_checkbox[1]"] == "/reconsider" and v["P2_Line2_checkbox[0]"] in (None, "/Off")
    assert v["P3_Line6_USCISOffice[0]"] == "NER BOS" and v["Pt2_Line3_ReceiptNumber[0]"] == "EAC2690000555"
    assert v["P2_Line2_Formnumberappeal[0]"] == "I-765" and v["Pt1Line6_CityOrTown[0]"] == "SOMERVILLE"


def test_a_denied_n400_gets_the_n336(case, tmp_path):
    import journey

    g = _graph(n336__military="No", n336__reason="The client passed the civics test.", n336__office="Lawrence Field Office")
    _notice(g, "IOE0999000701", "N-400", "denial", "2026-09-15")
    first = journey.journey(case, TODAY, graph=g)["next_filings"][0]
    assert first["filing"] == "n336" and first["now"]
    hearing_request.derive(g, TODAY)
    assert g.get("n336.receipt").value == "IOE0999000701" and g.get("n336.mailing_city").value == "SOMERVILLE"
    assert hearing_request.fee(g, TODAY)[0] == 830
    lines, name = hearing_request.mail_to(g)
    assert name == "Elgin" and lines[2] == "P.O. BOX 4088"                           # MA: the N-336's own chart, not the N-400's
    assert hearing_request.problems(case, g, TODAY) == []
    military = _graph(n336__military="Yes")
    assert hearing_request.fee(military, TODAY)[0] == 0
    profile = load_profile()
    profile["forms"] = {"n336": profile["forms"]["n336"]}
    fill_companions(g, tmp_path, profile)
    v = _short(tmp_path / "n336_filled.pdf")
    assert v["Pt2Line1_ExplainEligibility[0]"] == "IOE0999000701" and v["Pt2Line4_No[0]"] == "/N"
    assert v["Pt3Line6_Email[0]"] == "The client passed the civics test." and v["AlienNumber[3]"] == "099999999"
    assert motion.problems(case, _graph(motion__form="N-400"), TODAY)[0].startswith("A denied N-400 gets a hearing on Form N-336")


def test_every_state_has_one_n336_lockbox():
    from filing_questions import lockbox

    states = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
              "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
              "WV", "WI", "WY"}
    assert all(lockbox("uscis_lockboxes_n336", s)[0] for s in states)


def test_a_lost_hearing_puts_the_bia_appeal_first(case, tmp_path):
    import bia
    import filing_questions
    import journey

    journey.mark(case, "hearing", "Paula", value={"date": "2026-09-24", "kind": "Individual (merits)", "court": "Boston Immigration Court"})
    h = journey.journey(case, TODAY, graph=_graph())["hearings"][0]["id"]
    journey.mark(case, "hearing_result", "Paula", item=h, value={"outcome": "Decision: removal ordered or relief denied", "decision_date": "2026-09-24"})
    first = journey.journey(case, TODAY, graph=_graph())["next_filings"][0]
    assert first["filing"] == "bia" and first["now"] and "by 10/05/2026" in first["label"]     # 10 days: 10/04 is a Sunday
    g = filing_questions.derive(bia, case, _graph(bia__reasons="The judge misapplied the law.", eoir__dhs_address="ICE OPLA, BOSTON MA"), TODAY)
    assert g.get("bia.decision_date").value == "2026-09-24" and g.get("bia.last_hearing").value == "Boston Immigration Court"
    assert g.get("bia.date_merits").value == "2026-09-24" and g.get("bia.parties").value == "ANA EXEMPLO: A099999999"
    assert bia.due(g) == date(2026, 10, 5) and bia.fee(g, TODAY)[0] == 1060                      # EOIR's fee page, 10/01/2026
    notes = " ".join(n["text"] for n in bia.notes(g, TODAY))
    assert "RECEIVE it by 10/05/2026" in notes and "EOIR Payment Portal" in notes and "still say 30 days" in notes
    asylum = filing_questions.derive(bia, case, _graph(bia__asylum="Yes"), TODAY)
    assert bia.due(asylum) == date(2026, 10, 26)                                                # 30 days: an asylum decision
    profile = load_profile()
    profile["forms"] = {k: profile["forms"][k] for k in ("eoir26", "eoir27")}
    fill_companions(g, tmp_path, profile)
    v = {n: f.get("/V") for n, f in PdfReader(str(tmp_path / "eoir26_filled.pdf")).get_fields().items()}
    assert v["5"] == "/Merits proceedings appeal" and v["Date 5.1_af_date"] == "09/24/2026" and v["2"] == "/Respondent/Applicant"
    assert v["4. Last hearing"] == "Boston Immigration Court" and v["12. Address"] == "ICE OPLA, BOSTON MA"
    w = {n: f.get("/V") for n, f in PdfReader(str(tmp_path / "eoir27_filled.pdf")).get_fields().items()}
    assert w["LastName"] == "EXEMPLO" and w["AlienNumber"] == "A099999999" and w["City"] == "SOMERVILLE"
