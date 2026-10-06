"""Filed with the immigration judge, on paper the office writes (src/court_pleading.py): the bond request
(src/bond.py; 8 CFR 1003.19, 1236.1(d); Matter of Guerra) and the motions to reopen and reconsider
(src/court_motion.py; 8 CFR 1003.23(b), 1003.24, 1241.1). Their quotes, deadlines and fees each with the
source read on 10/02/2026; the courts from EOIR's pages; the packet's order (Practice Manual, 2020 version:
the proposed order and the proof of service at the bottom); the case page's offers. Every client value is CONSTRUCTED.
"""

import json
from datetime import date

import pytest
from pypdf import PdfReader, PdfWriter

import bond
import court_motion
import court_pleading
import filing_questions
import packet
from factgraph import FactGraph
import schema_path

TODAY = date(2026, 10, 2)
BASE = {"applicant.family_name": "EXEMPLO SOUZA", "applicant.given_name": "MARIA", "applicant.dob": "1990-03-14", "applicant.a_number": "A099000777",
        "applicant.physical_street": "10 EXAMPLE STREET", "applicant.physical_unit_type": "APT", "applicant.physical_apt": "2",
        "applicant.physical_city": "SPRINGFIELD", "applicant.physical_state": "MA", "applicant.physical_zip": "01103",
        "applicant.first_time_in_us": "Yes", "applicant.i94_arrival_date": "2014-07-15", "applicant.last_arrival_manner": "ADMITTED",
        "applicant.i94_class_of_admission": "B2", "applicant.last_arrival_city": "BOSTON", "applicant.last_arrival_state": "MA",
        "firm.preparer_given_name": "ANA", "firm.preparer_family_name": "EXEMPLO", "firm.business_name": "EXAMPLE LAW LLP", "firm.street": "1 EXAMPLE PLAZA",
        "firm.city": "BOSTON", "firm.state": "MA", "firm.zip": "02101", "firm.phone": "6175550100", "firm.email": "ANA@EXAMPLE.COM", "firm.eoir_id": "ZZ999999",
        "firm.licensing_authority": "MASSACHUSETTS", "firm.attorney_bar_number": "000000"}
BOND = {"bond.custody_location": "EXAMPLE COUNTY HOUSE OF CORRECTION, EXAMPLETOWN, MA", "bond.detained_since": "2026-09-20", "bond.dhs_bond": "No bond",
        "bond.amount_asked": "3000", "bond.prior_ruling": "No", "bond.mandatory": "No", "bond.final_order": "No", "bond.court": "Boston Immigration Court",
        "eoir.electronic_service": "No", "eoir.dhs_address": "OPLA BOSTON, 1 EXAMPLE WAY, BOSTON, MA 02101", "bond.family_ties": "A U.S. CITIZEN SON, 8",
        "bond.employment": "COOK SINCE 2016", "bond.appearances": "EVERY CHECK-IN", "bond.criminal": "None", "bond.violations": "Overstayed a B-2 admission",
        "bond.flight": "None", "bond.burden": "THE ATTORNEY'S PARAGRAPH ON THE BURDEN."}
ROW = {"summary": {"name": "MARIA EXEMPLO SOUZA"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}


def _graph(**extra):
    g = FactGraph("c")
    for key, value in (BASE | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _text(path):
    return " ".join(" ".join(p.extract_text() for p in PdfReader(str(path)).pages).split())


def _case(tmp_path, g, status=None, docs=None):
    d = tmp_path / "case"
    d.mkdir(exist_ok=True)
    g.save(d / "fact_graph.json")
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    docs = docs or {"nta.pdf": "notice_to_appear", "statement.pdf": "declaration"}
    for name in docs:
        w = PdfWriter()
        w.add_blank_page(width=612, height=792)
        with open(source / name, "wb") as fh:
            w.write(fh)
    (d / "meta.json").write_text(json.dumps({"source_folder": str(source), "classifications": docs}), encoding="utf-8")
    if status is not None:
        (d / "status.json").write_text(json.dumps(status), encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def _fixed_day(monkeypatch):
    import clock
    import journey

    monkeypatch.setattr(clock, "today", lambda: TODAY)
    monkeypatch.setattr(packet, "_today", lambda: TODAY)
    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})


# -- the courts ---------------------------------------------------------------------------------------------------------


def test_the_courts_are_eoir_s_own_pages():
    courts = court_pleading.courts()
    assert set(courts) == {"Boston Immigration Court", "Chelmsford Immigration Court", "Miami Immigration Court",
                           "Miami Krome (Detained) Immigration Court", "Orlando Immigration Court"}
    assert courts["Boston Immigration Court"]["address"] == ["JFK Federal Building", "15 New Sudbury Street, Room 320", "Boston, MA 02203"]
    assert courts["Orlando Immigration Court"]["address"][-1] == "Orlando, FL 32801" and courts["Miami Krome (Detained) Immigration Court"]["detained"]
    assert all(c["page"].startswith("https://www.justice.gov/eoir/") and c["page_updated"] == "October 1, 2026" for c in courts.values())
    assert court_pleading.match("Krome North SPC, Miami") == "Miami Krome (Detained) Immigration Court"
    assert court_pleading.match("Boston Immigration Court") == "Boston Immigration Court" and court_pleading.match("Newark") is None
    g = _graph(bond__court=court_pleading.OTHER, bond__court_other="Example Immigration Court", bond__court_other_street="1 COURT ST",
               bond__court_other_city="EXAMPLETOWN, NJ 07000")
    assert court_pleading.court_of(g, "bond") is None                                  # no source: never a guessed address
    assert any("where the address was read" in p for p in court_pleading.court_problems(g, "bond"))
    g = _graph(bond__court=court_pleading.OTHER, bond__court_other="Example Immigration Court", bond__court_other_street="1 COURT ST",
               bond__court_other_city="EXAMPLETOWN, NJ 07000", bond__court_other_source="justice.gov, read 10/02/2026")
    assert court_pleading.court_of(g, "bond")["city"] == "EXAMPLETOWN, NJ"


# -- the bond request -------------------------------------------------------------------------------------------------


def test_the_bond_request_quotes_the_rule_and_guerra_and_goes_to_the_court(tmp_path):
    g = _graph(**{k.replace(".", "__"): v for k, v in BOND.items()})
    bond.derive(g, TODAY)
    v = lambda k: g.get(k).value  # noqa: E731
    assert v("bond.fixed_address") == "10 EXAMPLE STREET APT 2, SPRINGFIELD, MA 01103" and v("bond.entry") == bond.ENTRY[0]
    assert v("bond.manner_of_entry") == "Admitted at BOSTON, MA on 07/15/2014 as a B2 nonimmigrant (Form I-94)"
    assert v("bond.residence") == "Since 07/15/2014 (12 years)"
    assert bond.fee(g, TODAY)[0] == 0
    notes = " ".join(n["text"] for n in bond.notes(g, TODAY))
    for said in ("8 CFR 1003.19(c)", "Vizcaino Aybar, 29 I&N Dec. 736", "Matter of Guerra, 24 I&N Dec. 37, 40", "15 New Sudbury Street",
                 "8 CFR 1003.32(c)", "EOIR-43", "1236.1(d)(1)"):
        assert said in notes, said
    assert "in duplicate" not in notes                                                  # only a motion is filed in duplicate (1003.23(b)(1)(ii))
    d = _case(tmp_path, g)
    assert bond.problems(d, g, TODAY) == []
    bond.render(d, g, TODAY)
    cover, request = _text(d / "bond_cover.pdf"), _text(d / "bond_request.pdf")
    assert "DETAINED" in cover and "BOSTON, MASSACHUSETTS" in cover and "A 099 000 777" in cover and "In bond proceedings" in cover
    assert "EOIR ID: ZZ999999" in cover and "DRAFT" not in cover
    for said in ("EXAMPLE COUNTY HOUSE OF CORRECTION", "DHS has not set a bond", "on a bond of $3,000", "Immigration Court having jurisdiction over the place of detention",
                 "shall form no part of, any deportation or removal hearing", "a threat to national security, a danger to the community at large",
                 "1. Whether the alien has a fixed address in the United States.", "9. The alien's manner of entry to the United States.",
                 "A U.S. CITIZEN SON, 8", "THE ATTORNEY'S PARAGRAPH ON THE BURDEN.", "Attorney for the Respondent"):
        assert said in request, said
    assert "1003.19(e)" not in request and "not properly included" not in request      # not a later request; not mandatory detention
    order, service = _text(d / "bond_order.pdf"), _text(d / "bond_service.pdf")
    assert "ORDER OF THE IMMIGRATION JUDGE" in order and "posting a bond of" in order and "request be" in order
    assert "OPLA BOSTON, 1 EXAMPLE WAY" in service and "ICE Office of the Principal Legal Advisor" in service


def test_who_the_judge_can_t_release_is_warned_and_a_later_request_needs_a_change(tmp_path):
    d = _case(tmp_path, _graph())
    ewi = _graph(**{k.replace(".", "__"): v for k, v in BOND.items()} | {"applicant__last_arrival_manner": "WITHOUT ADMISSION OR PAROLE",
                                                                         "bond__mandatory": "Yes", "bond__final_order": "Yes", "bond__prior_ruling": "Yes"})
    bond.derive(ewi, TODAY)
    assert ewi.get("bond.entry").value == bond.ENTRY[3]
    notes = {n["title"]: n["text"] for n in bond.notes(ewi, TODAY)}
    assert "Matter of Yajure Hurtado, 29 I&N Dec. 216 (BIA 2025)" in notes["Not admitted"] and notes["Not admitted"].endswith("The attorney decides how to proceed.")
    assert "(h)(2)(ii)" in notes["Mandatory detention"] and "Matter of W-F-D-, 29 I&N Dec. 854" in notes["A final order"]
    assert any("8 CFR 1003.19(e)" in p for p in bond.problems(d, ewi, TODAY))           # a later request: the material change
    arriving = _graph(bond__entry=bond.ENTRY[4])
    assert "8 CFR 1003.19(h)(2)(i)(B)" in {n["title"]: n["text"] for n in bond.notes(arriving, TODAY)}["An arriving alien"]
    released = _graph(bond__released="Yes", bond__released_on="2026-09-20")
    assert any("ended 09/27/2026" in p and "1236.1(d)(1)" in p for p in bond.problems(d, released, TODAY))
    soon = _graph(bond__released="Yes", bond__released_on="2026-09-30")
    assert "due by 10/07/2026" in {n["title"]: n["text"] for n in bond.notes(soon, TODAY)}["When"]
    ewi_rendered = _graph(**{k.replace(".", "__"): v for k, v in BOND.items()} | {"bond__mandatory": "Yes", "bond__prior_ruling": "Yes",
                                                                                  "bond__changed": "A NEW U.S. CITIZEN CHILD"})
    bond.render(d, ewi_rendered, TODAY)
    request = _text(d / "bond_request.pdf")
    assert "not properly included" in request and "have changed materially since the prior bond redetermination" in request
    assert "A NEW U.S. CITIZEN CHILD" in request


# -- the motions ------------------------------------------------------------------------------------------------------


def test_the_deadlines_count_from_the_final_order():
    g = _graph(ijmotion__kind="Motion to reconsider", ijmotion__final_on="2026-09-01")
    assert court_motion.deadline(g)[0] == date(2026, 10, 1)                            # 30 days, a Thursday
    g = _graph(ijmotion__kind="Motion to reopen", ijmotion__final_on="2026-09-01")
    assert court_motion.deadline(g)[0] == date(2026, 11, 30)                           # 90 days, a Monday
    g = _graph(ijmotion__kind="Motion to reopen an in absentia order", ijmotion__order_date="2026-06-01", ijmotion__absentia_ground="Exceptional circumstances")
    assert court_motion.deadline(g)[0] == date(2026, 11, 30)                           # 180 days (a Saturday): Monday
    g = _graph(ijmotion__kind="Motion to reopen an in absentia order", ijmotion__order_date="2020-06-01", ijmotion__absentia_ground="No notice of the hearing")
    assert court_motion.deadline(g) == (None, "For lack of notice, or federal or state custody through no fault of the client: at any time (8 CFR 1003.23(b)(4)(ii)).")
    g = _graph(ijmotion__kind="Motion to reopen", ijmotion__final_on="2020-01-01", ijmotion__country_conditions="Yes")
    assert court_motion.deadline(g)[0] is None and "1003.23(b)(4)(i)" in court_motion.deadline(g)[1]
    g = _graph(ijmotion__kind="Motion to reopen", ijmotion__final_on="2020-01-01", ijmotion__joint="Yes")
    assert court_motion.deadline(g)[0] is None and "1003.23(b)(4)(iv)" in court_motion.deadline(g)[1]
    # (b)(4)(iv) lifts (b)(1)'s limits only for "a motion to reopen agreed upon by all parties and jointly filed"
    g = _graph(ijmotion__kind="Motion to reconsider", ijmotion__final_on="2026-09-01", ijmotion__joint="Yes")
    assert court_motion.deadline(g)[0] == date(2026, 10, 1)                            # a joint reconsider keeps its 30 days
    g = _graph(ijmotion__kind="Motion to reopen an in absentia order", ijmotion__order_date="2026-06-01",
               ijmotion__absentia_ground="Exceptional circumstances", ijmotion__joint="Yes")
    assert court_motion.deadline(g)[0] == date(2026, 11, 30)                           # (b)(4)(ii)'s 180 days aren't (b)(1)'s


def test_the_day_counts_are_the_timeline_s(tmp_path):
    """One source: schemas/registers/journey.json court_decisions, read by the motion and by the case's timeline (journey._after_decision)."""
    import journey

    rules = journey.settings()["court_decisions"]
    assert [court_motion.days(k) for k in court_motion.KINDS] == [rules["reopen_days"], rules["reconsider_days"], rules["in_absentia_reopen_days"]]
    cases = [(decided, outcome, kind) for decided in ("2026-07-03", "2026-09-10", "2026-12-24")
             for outcome, kind in (("Decision: removal ordered or relief denied", "Motion to reopen"),
                                   ("Decision: removal ordered or relief denied", "Motion to reconsider"),
                                   ("Removal ordered in absentia", "Motion to reopen an in absentia order"))]
    for n, (decided, outcome, kind) in enumerate(cases):
        status = {"journey": {"hearings": [{"id": "h.0", "date": decided, "kind": "Individual (merits)",
                                            "result": {"outcome": outcome, "decision_date": decided, "appeal_waived": True}}]}}
        base = tmp_path / str(n)
        base.mkdir()
        g = _graph(applicant__nta_present="Yes", ijmotion__kind=kind, ijmotion__absentia_ground="Exceptional circumstances")
        on_timeline = {x["id"].rsplit(".", 1)[-1]: x["date"] for x in journey.journey(_case(base, g, status), TODAY, graph=g)["deadlines"]}
        court_motion.from_record(status, g)
        assert court_motion.deadline(g)[0].isoformat() == on_timeline["reconsider" if kind == "Motion to reconsider" else "reopen"], (decided, kind)


def test_the_fee_from_eoir_s_page_or_none_by_the_regulation():
    import fees

    eoir = fees.load(TODAY)["eoir"]
    assert (eoir["ij_motion"], eoir["motion_no_fee_relief"]) == (1095, 950)
    assert court_motion.fee(_graph(ijmotion__kind="Motion to reopen"), TODAY)[0] == 1095
    assert court_motion.fee(_graph(ijmotion__kind="Motion to reopen", ijmotion__no_fee_relief="Yes"), TODAY)[0] == 950
    no_notice = court_motion.fee(_graph(ijmotion__kind="Motion to reopen an in absentia order", ijmotion__absentia_ground="No notice of the hearing"), TODAY)
    assert no_notice[0] == 0 and "1003.24(b)(2)(iii)" in no_notice[1]
    exceptional = _graph(ijmotion__kind="Motion to reopen an in absentia order", ijmotion__absentia_ground="Exceptional circumstances")
    assert court_motion.fee(exceptional, TODAY)[0] == 1095                              # (C)(i) has no exemption
    assert court_motion.fee(_graph(ijmotion__kind="Motion to reconsider", ijmotion__joint="Yes"), TODAY)[0] == 0
    notes = {n["title"]: n["text"] for n in court_motion.notes(_graph(ijmotion__kind="Motion to reopen", ijmotion__fee_waiver="Yes"), TODAY)}
    assert "Form EOIR-26A" in notes["Fee"] and "15 days" in notes["Fee"] and "1003.24(d)" in notes["Fee"]
    import payment

    g = _graph(ijmotion__kind="Motion to reopen")
    assert payment.payments(packet.load_filing("court_motion"), None, g, TODAY) == []  # EOIR's portal: never a G-1450


def test_the_case_page_supplies_the_order_and_when_it_became_final(tmp_path):
    absentia = {"journey": {"hearings": [{"id": "hearing.2026-08-03.0", "date": "2026-08-03", "kind": "Master calendar", "court": "Boston Immigration Court",
                                          "judge": "EXAMPLE", "result": {"outcome": "Removal ordered in absentia", "decision_date": "2026-08-03"}}]}}
    g = _graph()
    court_motion.from_record(absentia, g)
    v = lambda k: g.get(k).value  # noqa: E731
    assert (v("ijmotion.kind"), v("ijmotion.order_date"), v("ijmotion.final_on")) == ("Motion to reopen an in absentia order", "2026-08-03", "2026-08-03")
    assert v("ijmotion.court") == "Boston Immigration Court" and v("ijmotion.judge") == "EXAMPLE"
    waived = {"journey": {"hearings": [{"id": "h.0", "date": "2026-09-10", "kind": "Individual (merits)", "court": "Orlando",
                                        "result": {"outcome": "Decision: removal ordered or relief denied", "decision_date": "2026-09-10", "appeal_waived": True}}]}}
    g = _graph()
    court_motion.from_record(waived, g)
    assert g.get("ijmotion.final_on").value == "2026-09-10" and g.get("ijmotion.court").value == "Orlando Immigration Court"
    ran_out = {"journey": {"hearings": [{"id": "h.0", "date": "2026-09-01", "kind": "Individual (merits)",
                                         "result": {"outcome": "Decision: removal ordered or relief denied", "decision_date": "2026-09-01"}}]}}
    g = _graph()
    court_motion.from_record(ran_out, g)
    assert g.get("ijmotion.final_on").value == "2026-09-11"                            # 10 days, received by the Board: then final (1241.1(c))
    appealed = ran_out | {"filings": [{"filing": "bia", "mailed_on": "2026-09-08"}]}
    g = _graph()
    court_motion.from_record(appealed, g)
    assert g.get("ijmotion.final_on") is None and g.get("ijmotion.appeal_pending").value == "Yes"


def test_what_stops_a_motion_each_with_its_rule(tmp_path):
    d = _case(tmp_path, _graph())
    g = _graph(ijmotion__kind="Motion to reopen", ijmotion__final_on="2026-01-02", ijmotion__appeal_pending="Yes", ijmotion__departed="Yes",
               ijmotion__prior_motion="Yes", ijmotion__judicial="Yes", ijmotion__criminal="Yes", ijmotion__fee_waiver="No",
               ijmotion__court="Chelmsford Immigration Court", eoir__electronic_service="Yes")
    out = " ".join(court_motion.problems(d, g, TODAY))
    for said in ("unless jurisdiction is vested with the Board", "shall not be made", "Matter of M-M-L-J-, 29 I&N Dec. 843", "Past the deadline (04/02/2026)",
                 "its nature and date, the court", "its current status"):
        assert said in out, said
    free = _graph(ijmotion__kind="Motion to reopen an in absentia order", ijmotion__absentia_ground="In federal or state custody, through no fault of the client",
                  ijmotion__fee_waiver="Yes", eoir__electronic_service="Yes")
    assert any("no fee waiver request is needed" in p for p in court_motion.problems(d, free, TODAY))
    filed = {n["title"]: n["text"] for n in court_motion.notes(g, TODAY)}["How it is filed"]
    assert "ECAS Case Portal" in filed and "25 MB" in filed and "1003.32(a)" in filed
    paper = {n["title"]: n["text"] for n in court_motion.notes(_graph(ijmotion__court="Chelmsford Immigration Court"), TODAY)}["How it is filed"]
    assert "in duplicate (8 CFR 1003.23(b)(1)(ii))" in paper and "150 Apollo Drive, Suite 100" in paper


def test_the_motion_packet_in_the_practice_manual_s_order(tmp_path):
    answers = {"ijmotion.kind": "Motion to reopen", "ijmotion.order_date": "2026-08-14", "ijmotion.final_on": "2026-08-14", "ijmotion.appeal_pending": "No",
               "ijmotion.departed": "No", "ijmotion.prior_motion": "No", "ijmotion.new_facts": "THE CLIENT'S U.S. CITIZEN SPOUSE'S I-130 WAS APPROVED.",
               "ijmotion.why_new": "THE APPROVAL CAME AFTER THE HEARING.", "ijmotion.relief": "Form I-485 with Form I-130 approval",
               "ijmotion.judicial": "No", "ijmotion.criminal": "No", "ijmotion.fee_waiver": "Yes", "ijmotion.court": "Boston Immigration Court",
               "eoir.electronic_service": "No", "eoir.dhs_address": "OPLA BOSTON, 1 EXAMPLE WAY, BOSTON, MA 02101",
               "ijmotion.argument": "THE ATTORNEY'S ARGUMENT."}
    d = _case(tmp_path, _graph())
    filing_questions.answer("court_motion", d, answers, "Sam")
    status = filing_questions.status("court_motion", d, TODAY)
    assert not [q for q in status["questions"] if q["required"] and q["value"] is None], status["problems"]
    titles = {q["section"] for q in status["questions"]}
    assert "The new facts (motion to reopen)" in titles and "The errors (motion to reconsider)" not in titles   # only the chosen motion's questions
    schema = packet.for_case(packet.load_filing("court_motion"), d)
    assert schema["forms"] == ["eoir28", "ijmotion_cover", "eoir26a", "ijmotion_motion", "ijmotion_order", "ijmotion_service"]
    assert schema["cover_letter"] is False and "court_motion" not in __import__("enotice").LOCKBOX_FILINGS
    m = packet.build(d, ROW, "Sam", schema)
    tabs = [s["tab"] for s in m["sections"]]
    assert tabs[:4] == ["EOIR-28", "Cover page", "EOIR-26A", "Motion"] and tabs[-2:] == ["Proposed order", "Proof of service"]
    assert any(t.startswith("Exhibit") for t in tabs[4:-2]) and not m["payments"]
    motion = _text(d / "ijmotion_motion.pdf")
    for said in ("moves the Immigration Judge to reopen these proceedings", "must be filed within 90 days of the date of entry of a final administrative order",
                 "This motion is filed within 90 days of the final order", "shall state the new facts that will be proven",
                 "THE CLIENT'S U.S. CITIZEN SPOUSE'S I-130 WAS APPROVED.", "FORM I-485 WITH FORM I-130 APPROVAL", "THE ATTORNEY'S ARGUMENT.",
                 "has not been and is not the subject of any judicial proceeding", "is not the subject of any pending criminal proceeding under the Act",
                 "Form EOIR-26A, accompanies this motion", "The order became final on 08/14/2026"):
        assert said in motion, said
    order = _text(d / "ijmotion_order.pdf")
    assert "the respondent's Motion to Reopen" in order and "The proceedings are reopened." in order and "biometrics" in order
    waiver = {k.rsplit(".", 1)[-1]: f.get("/V") for k, f in PdfReader(str(d / "eoir26a_filled.pdf")).get_fields().items()}
    assert waiver["Name Last First Middle"] == "EXEMPLO SOUZA, MARIA" and waiver["Alien A Number"] == "A099000777" and waiver["EOIR ID Number"]
    assert waiver["Print name of alien filing the form"] == "MARIA EXEMPLO SOUZA"
    boxes = {k.rsplit(".", 1)[-1]: f for k, f in PdfReader(str(d / "eoir26a_filled.pdf")).get_fields().items()}
    for name in ("IncomeEmployment", "ExpenseOther", "MonthIncome", "TotalTot"):   # EOIR's template ships "0.00" / "0": never a declared $0 income
        assert boxes[name].get("/V") in (None, "") and boxes[name].get("/DV") in (None, ""), name
    assert any("write every line" in c["text"] for c in m["checklist"])
    assert "the Form EOIR-26A (fee waiver request)" in _text(d / "ijmotion_service.pdf")
    cover = _text(d / "ijmotion_cover.pdf")
    assert "DETAINED" not in cover and "JOINT MOTION" not in cover
    eoir28 ={k.rsplit(".", 1)[-1]: f.get("/V") for k, f in PdfReader(str(d / "eoir28_filled.pdf")).get_fields().items()}
    assert "099000777" in str(eoir28)                                                   # the appearance filed with the motion
    filing_questions.answer("court_motion", d, {"ijmotion.fee_waiver": "No"}, "Sam")
    assert "eoir26a" not in packet.for_case(packet.load_filing("court_motion"), d)["forms"]


def test_a_joint_motion_and_a_detained_client_on_the_cover(tmp_path):
    """Practice Manual 3.3(c)(vi), 3.2(a), (e) (2020 version): DETAINED and JOINT MOTION on the cover; no proof of service for a joint motion."""
    held = {"journey": {"hearings": [{"id": "h.0", "date": "2026-09-01", "kind": "Individual (merits)", "court": "Boston Immigration Court", "detained": True,
                                      "result": {"outcome": "Decision: removal ordered or relief denied", "decision_date": "2026-09-01", "appeal_waived": True}}]}}
    d = _case(tmp_path, _graph(), held)
    filing_questions.answer("court_motion", d, {"ijmotion.kind": "Motion to reconsider", "ijmotion.joint": "Yes", "ijmotion.prior_motion": "Yes",
                                                "ijmotion.stay": "Yes", "eoir.electronic_service": "No"}, "Sam")
    g = filing_questions.graph_for("court_motion", d, TODAY)
    assert g.get("ijmotion.detained").value == "Yes"                                    # the hearing on the case page says detained
    assert court_motion.deadline(g)[0] == date(2026, 10, 1)                            # a joint reconsider keeps (b)(1)'s 30 days
    assert any("M-M-L-J-" in p for p in court_motion.problems(d, g, TODAY))            # ... and its one-motion limit
    assert court_motion.fee(g, TODAY)[0] == 0                                          # but any joint motion has no fee (1003.24(b)(2)(v))
    assert "no proof of service" in {n["title"]: n["text"] for n in court_motion.notes(g, TODAY)}["How it is filed"]
    schema = packet.for_case(packet.load_filing("court_motion"), d)
    assert "ijmotion_service" not in schema["forms"] and "eoir26a" not in schema["forms"]
    court_motion.render(d, g, TODAY)
    cover, motion, order = _text(d / "ijmotion_cover.pdf"), _text(d / "ijmotion_motion.pdf"), _text(d / "ijmotion_order.pdf")
    assert "DETAINED" in cover and "JOINT MOTION" in cover
    assert "shall not apply to a motion to reopen" not in motion and "must be filed within 30 days" in motion
    assert "HEREBY ORDERED that the motion be" in order and "the request be" not in order   # "...and Request for a Stay": still a motion
    krome = _graph(ijmotion__court="Miami Krome (Detained) Immigration Court")
    assert court_pleading.detained(krome, "ijmotion")                                  # a detained court


def test_ecas_s_size_limit_and_the_motion_s_questions_note(tmp_path):
    d = _case(tmp_path, _graph())
    (d / "packet_court_motion.pdf").write_bytes(b"%PDF-" + b"0" * 26_000_000)
    g = _graph(eoir__electronic_service="Yes")
    assert any("ECAS takes documents of 25 MB or less" in p for p in court_motion.problems(d, g, TODAY))
    assert not any("25 MB" in p for p in court_motion.problems(d, _graph(eoir__dhs_address="OPLA"), TODAY))   # on paper: no limit
    assert "once" not in court_motion.MORE_QUESTIONS and "Which motion" in court_motion.MORE_QUESTIONS
    notes = " ".join(n["text"] for n in bond.notes(_graph(bond__entry=bond.ENTRY[3], bond__final_order="Yes"), TODAY))
    assert "(BIA 2025), its headnote" in notes and "(BIA 2026), its headnote" in notes
    assert "Practice Manual 3.1(a)(iii), 2020 version" in court_pleading.how_filed(_graph(), "ijmotion", duplicate=True)


def test_the_client_never_reads_uscis_for_a_court_filing():
    import journey

    filings = [{"filing": "bia", "mailed_on": "2026-09-08"}, {"filing": "address", "mailed_on": "2026-09-09"},
               {"filing": "court_motion", "mailed_on": "2026-09-10"}, {"filing": "i90", "mailed_on": "2026-09-11"},
               {"filing": "i589", "variant": "in_court", "mailed_on": "2026-09-12"}, {"filing": "something_new", "mailed_on": "2026-09-13"}]
    said = [e["text"] for e in journey._client_events({"filings": filings}, "en")]
    assert said == ["We filed your application (Form I-589) with the immigration court on 09/12/2026.", "We mailed your application (Form I-90) to USCIS on 09/11/2026.",
                    "We filed your appeal (Form EOIR-26) with the Board of Immigration Appeals on 09/08/2026."]
    pt = [e["text"] for e in journey._client_events({"filings": filings[:1]}, "pt")]
    assert pt == ["Apresentamos o seu recurso (Formulário EOIR-26) ao Conselho de Recursos de Imigração (BIA) em 08/09/2026."]
    import prefile

    assert prefile._us("2026-10-13") == "10/13/2026" and {"court_bond", "court_motion"} <= prefile.COURT_FILINGS


def test_the_bond_packet_and_its_proof_of_service_at_the_bottom(tmp_path):
    d = _case(tmp_path, _graph())
    filing_questions.answer("court_bond", d, BOND | {"bond.entry": bond.ENTRY[0], "bond.residence": "SINCE 2014", "bond.manner_of_entry": "ADMITTED B-2"}, "Sam")
    schema = packet.for_case(packet.load_filing("court_bond"), d)
    plan = packet.plan(d, ROW, schema)
    assert [f["id"] for f in plan["forms"] if f["after_exhibits"]] == ["bond_order", "bond_service"]
    m = packet.build(d, ROW, "Sam", schema)
    tabs = [s["tab"] for s in m["sections"]]
    assert tabs[:3] == ["EOIR-28", "Cover page", "Bond request"] and tabs[-2:] == ["Proposed order", "Proof of service"] and not m["payments"]


def test_the_case_page_offers_bond_when_detained_and_the_motions_after_an_order(tmp_path):
    import journey

    g = _graph(applicant__nta_present="Yes")
    held = {"journey": {"hearings": [{"id": "hearing.2026-10-20.0", "date": "2026-10-20", "kind": "Master calendar", "court": "Boston Immigration Court",
                                      "detained": True}]}}
    d = _case(tmp_path, g, held)
    offers = {f["filing"]: f for f in journey.journey(d, TODAY, graph=g)["next_filings"]}
    assert offers["court_bond"]["now"] and "the client is detained" in offers["court_bond"]["label"]
    absentia = {"journey": {"hearings": [{"id": "hearing.2026-08-03.0", "date": "2026-08-03", "kind": "Master calendar", "court": "Boston Immigration Court",
                                          "result": {"outcome": "Removal ordered in absentia", "decision_date": "2026-08-03"}}]}}
    (d / "status.json").write_text(json.dumps(absentia), encoding="utf-8")
    j = journey.journey(d, TODAY, graph=g)
    offer = next(f for f in j["next_filings"] if f["filing"] == "court_motion")
    assert offer["label"] == "Motion to reopen the in absentia order: by 02/01/2027 for exceptional circumstances; at any time for lack of notice or custody"
    assert "court_bond" not in {f["filing"] for f in j["next_filings"]}
    waived = {"journey": {"hearings": [{"id": "h.0", "date": "2026-09-10", "kind": "Individual (merits)",
                                        "result": {"outcome": "Decision: removal ordered or relief denied", "decision_date": "2026-09-10", "appeal_waived": True}}]}}
    (d / "status.json").write_text(json.dumps(waived), encoding="utf-8")
    offer = next(f for f in journey.journey(d, TODAY, graph=g)["next_filings"] if f["filing"] == "court_motion")
    assert offer == {"filing": "court_motion", "now": True, "label": "Motion to reopen (by 12/09/2026) or reconsider (by 10/13/2026) with the judge"}  # 30 days:
    # Saturday 10/10, and Monday 10/12 is Columbus Day
    filed = waived | {"filings": [{"filing": "court_motion", "mailed_on": "2026-10-01"}]}
    (d / "status.json").write_text(json.dumps(filed), encoding="utf-8")
    j = journey.journey(d, TODAY, graph=g)
    assert "court_motion" not in {f["filing"] for f in j["next_filings"]}


def test_the_register_and_the_lists_name_them():
    import maintenance

    ids = {i["id"]: i for i in maintenance.registry()["items"]}
    for iid in ("immigration_court_addresses", "court_bond_rules", "court_motion_rules", "ecas_filing", "form_eoir26a"):
        assert ids[iid]["party"] == "provider" and ids[iid]["source"] and ids[iid]["last_checked"] == ("2026-10-03" if iid == "form_eoir26a" else "2026-10-02"), iid  # form_eoir26a was read again on 10/03/2026 (brief L4)
    assert "src/court_motion.py (fee)" in ids["eoir_fees"]["where"]
    assert filing_questions.module("court_bond") is bond and filing_questions.module("court_motion") is court_motion
    assert {"court_bond", "court_motion"} <= set(packet.FILINGS)
    online = json.loads((schema_path.path("law", "online_filing")).read_text(encoding="utf-8"))["filings"]
    assert online["court_bond"]["online"] is None and online["court_motion"]["online"] is None
    page = (schema_path.ROOT.parent / "src" / "review" / "static" / "index.html").read_text(encoding="utf-8")
    assert '["court_bond", ' in page and '["court_motion", ' in page
