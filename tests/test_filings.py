"""The filings on the generic question engine (src/filing_questions.py): the
I-90 (src/card_renewal.py), the I-131 (src/travel.py), the N-600
(src/certificate.py) and the I-751 (src/conditions.py) -- their fees (Form
G-1055, edition 10/01/26), their filing addresses (USCIS's charts in
schemas/), their deadlines, and the boxes the filled forms get. Every client
value is CONSTRUCTED.
"""

import json
import re
from datetime import date

import pytest
from pypdf import PdfReader

import card_renewal
import certificate
import conditions
import packet
import travel
from factgraph import FactGraph
from filing_questions import lockbox
from fill.companion import fill_companions, load_profile
import schema_path

TODAY = date(2026, 10, 1)
BASE = {"applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA", "applicant.a_number": "A099999999", "applicant.dob": "2004-03-14",
        "applicant.sex": "F", "applicant.country_of_birth": "BRAZIL", "applicant.citizenship": "BRAZIL", "applicant.physical_street": "10 EXAMPLE ST",
        "applicant.physical_city": "SOMERVILLE", "applicant.physical_state": "MA", "applicant.physical_zip": "02143"}
STATES_AND_DC = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
                 "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
                 "WV", "WI", "WY"}


def _graph(**extra):
    g = FactGraph("c")
    for key, value in (BASE | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _notice(g, receipt, form, kind, when):
    g.add_source(f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)


def _values(pdf):
    return {n: f.get("/V") for n, f in PdfReader(str(pdf)).get_fields().items()}


@pytest.mark.parametrize("chart", ["uscis_lockboxes_nfb", "uscis_lockboxes_i751", "uscis_lockboxes_n600"])
def test_every_state_has_exactly_one_lockbox(chart):
    for state in STATES_AND_DC:
        assert lockbox(chart, state)[0], f"{state} is not on {chart}"
    boxes = json.loads(schema_path.path("law", chart).read_text(encoding="utf-8"))["lockboxes"].values()
    listed = [s for b in boxes for s in b["states"]]
    assert len(listed) == len(set(listed)), "a state on two lockboxes"


def test_advance_parole_for_an_sij_client_costs_nothing_and_follows_the_receipt():
    g = _graph(applicant__i360_receipt_number="IOE0999000001")
    _notice(g, "IOE0999000123", "I-485", "receipt", "2026-05-01")
    travel.derive(g, TODAY)
    assert g.get("i131.type").value == travel.AP_I485 and g.get("i131.i485_receipt").value == "IOE0999000123"
    assert travel.fee(g, TODAY)[0] == 0                                   # G-1055: SIJ, every I-131 document
    lines, why = travel.address(g)
    assert lines[1:3] == ["ATTN: AOS", "P.O. BOX 805887"] and "family-based" in why   # IOE: the family chart (MA -> Chicago)
    eac = _graph(i131__type=travel.AP_I485, i131__i485_receipt="EAC2690001234")
    assert travel.address(eac)[0][2] == "P.O. BOX 4115"                  # EAC: the non-family chart (MA -> Elgin)


def test_travel_fees_without_sij():
    g = _graph(i131__type=travel.AP_I589)
    travel.derive(g, TODAY)
    assert travel.fee(g, TODAY)[0] == 630 and travel.address(g)[0][2] == "P.O. BOX 4115"
    young = _graph(i131__type=travel.RTD, i131__status_basis="Asylee", applicant__dob="2012-01-01")
    old = _graph(i131__type=travel.RTD, i131__status_basis="Asylee")
    refugee = _graph(i131__type=travel.RTD, i131__status_basis="Refugee")
    assert [travel.fee(x, TODAY)[0] for x in (young, old, refugee)] == [135, 165, 0]
    before = _graph()
    _notice(before, "IOE0888000123", "I-485", "receipt", "2023-06-01")    # filed with its fee before April 1, 2024, still pending
    travel.derive(before, TODAY)
    assert travel.fee(before, TODAY)[0] == 0


def test_the_i131_boxes_named_on_four_pages(tmp_path):
    g = _graph(i131__type=travel.AP_I589, i131__i589_receipt="ZLA2690000001", i131__trips="One trip", i131__in_proceedings="No")
    travel.derive(g, TODAY)
    profile = load_profile()
    profile["forms"] = {"i131": profile["forms"]["i131"]}
    fill_companions(g, tmp_path, profile)
    v = _values(tmp_path / "i131_filled.pdf")
    on = {n: x for n, x in v.items() if "CB_AppType" in n and x not in (None, "/Off")}
    assert on == {"form1[0].P2[0].CB_AppType[12]": "/6"}                  # only the page-2 box, never P1/P3/P4's CB_AppType[12]
    short = {re.split(r"(?<!\\)\.", n)[-1]: x for n, x in v.items()}
    assert short["P1_Line5B[0]"] == "ZLA2690000001" and short["P7_Line4_CB[0]"] == "/O" and short["P4_Line1_YesNo[1]"] == "/N"
    assert short["Part2_Line3_Country[0]"] == "USA" and short["Part2_Line1_FamilyName[0]"] == "EXEMPLO"


def test_rtd_problems_name_the_risky_answers(tmp_path):
    g = _graph(i131__type=travel.RTD, i131__status_basis="Asylee", i131__returned_to_that_country="Yes")
    travel.derive(g, TODAY)
    out = " ".join(travel.problems(tmp_path, g, TODAY))
    assert "returned to that country" in out and "can end refugee or asylee status" in out and "photo ID" in out


def test_the_i90_fee_and_address():
    g = _graph(i90__reason="Wrong because of a DHS error")
    card_renewal.derive(g, TODAY)
    letter = card_renewal.letter(g, TODAY)
    assert letter["no_payment"] and letter["mail_to"][2] == "P.O. BOX 21262"
    assert card_renewal.fee(_graph(i90__reason="Lost, stolen or destroyed"), TODAY) == 465


def test_n600_ina_320_dates():
    ok = _graph(n600__parent_citizen_how="Naturalization", n600__parent_naturalization_date="2020-05-01", n600__ever_lpr="Yes",
                n600__lpr_date="2019-06-01", n600__custody_before_18="Yes", n600__parent_lost_citizenship="No")
    assert certificate.citizen_since(ok) == (date(2020, 5, 1), [])          # the later of the parent's oath and the green card
    late = _graph(n600__parent_citizen_how="Naturalization", n600__parent_naturalization_date="2022-04-01", n600__ever_lpr="Yes",
                  n600__lpr_date="2019-06-01", n600__custody_before_18="Yes", applicant__dob="2004-03-14")
    since, why = certificate.citizen_since(late)
    assert since is None and "18th birthday (03/14/2022)" in why[0]     # the oath came 18 days after the 18th birthday
    old = _graph(applicant__dob="1980-01-01")
    assert "Child Citizenship Act" in certificate.citizen_since(old)[1][0]
    born_citizen = _graph(n600__parent_citizen_how="Birth in the United States", n600__ever_lpr="Yes", n600__lpr_date="2010-01-01",
                          n600__custody_before_18="Yes")
    assert certificate.citizen_since(born_citizen)[0] == date(2010, 1, 1)


def test_n600_address_and_fee():
    tx = _graph(applicant__physical_state="TEXAS")                       # a state written out still finds its lockbox
    assert certificate.address(tx)[0][2] == "P.O. BOX 20100"
    assert certificate.address(_graph())[0][2] == "P.O. BOX 4088"
    assert certificate.fee(_graph(), TODAY)[0] == 1385
    assert certificate.fee(_graph(n600__armed_forces="Yes", n600__filer="The client (adult child)"), TODAY)[0] == 0


def test_i751_the_90_day_window(tmp_path):
    g = _graph(i751__card_expires="2027-03-01", i751__basis=conditions.JOINT)
    conditions.derive(g, TODAY)
    out = " ".join(conditions.problems(tmp_path, g, TODAY))
    assert "can't be filed before 12/01/2026" in out                     # 90 days before 03/01/2027
    assert "Elgin" in conditions.address(g)[1]
    waiver = _graph(i751__card_expires="2027-03-01", i751__waiver_divorce="Yes", applicant__marital_status="Divorced")
    conditions.derive(waiver, TODAY)
    assert not conditions.joint(waiver)
    out = " ".join(conditions.problems(tmp_path, waiver, TODAY))
    assert "can't be filed before" not in out and "divorce or annulment decree" in out
    late = _graph(i751__card_expires="2026-09-01", i751__basis=conditions.JOINT, applicant__physical_state="TX")
    conditions.derive(late, TODAY)
    assert "a late petition needs the written reason" in " ".join(conditions.problems(tmp_path, late, TODAY))
    assert conditions.address(late)[0][2] == "P.O. BOX 21200"


def test_i751_derives_the_expiry_from_the_approval():
    g = _graph(applicant__marital_status="Married")
    _notice(g, "IOE0777000123", "I-485", "approval", "2025-01-15")
    conditions.derive(g, TODAY)
    assert g.get("i751.card_expires").value == "2027-01-15" and g.get("i751.basis").value == conditions.JOINT


def test_each_filing_has_its_own_hand_work(tmp_path):
    forms = [{"id": "g28_n400", "short": "G-28", "signatures": {}, "layout": None, "attorney_completes": []},
             {"id": "n400", "short": "N-400", "signatures": {}, "layout": None, "attorney_completes": []}]
    n400 = " ".join(c["text"] for c in packet._checklist([], [], [], forms, {}, packet.load_filing("n400")))
    assert "I-693" not in n400 and "SIJ" not in n400 and "Form G-28, signed" not in n400
    i485 = " ".join(c["text"] for c in packet._checklist([], [], [], [], {}, packet.load_filing("i485")))
    assert "I-693" in i485
    signer = [{"id": "i130", "short": "I-130", "signatures": {"petitioner": ["x", "Part 6, the petitioner's signature"]}, "layout": None, "attorney_completes": []}]
    assert "The petitioner signs" in packet._checklist([], [], [], signer, {}, packet.load_filing("family"))[0]["text"]


# -- immigration court, the immigrant visa ------------------------------------------


def _case_dir(tmp_path, classifications=None):
    d = tmp_path / "case"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"classifications": classifications or {}}), encoding="utf-8")
    return d


def test_hearings_entered_by_hand_reach_the_deadlines_and_the_portal(tmp_path, monkeypatch):
    import journey

    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})
    d = _case_dir(tmp_path, {"nta.pdf": "notice_to_appear"})
    with pytest.raises(ValueError):
        journey.mark(d, "hearing", "Paula", value={"date": "11/12/2026", "kind": "Master calendar"})  # the date as YYYY-MM-DD
    journey.mark(d, "hearing", "Paula", value={"date": "2026-11-12", "time": "9:00 AM", "kind": "Master calendar", "court": "Boston Immigration Court"})
    journey.mark(d, "hearing", "Paula", value={"date": "2026-09-01", "kind": "Master calendar"})
    j = journey.journey(d, TODAY, graph=_graph())
    assert [h["date"] for h in j["hearings"]] == ["2026-09-01", "2026-11-12"]
    assert any(x["date"] == "2026-11-12" and "must attend" in x["what"] and x["owner"] == "client" for x in j["deadlines"])
    texts = " ".join(s["text"] for s in j["steps"])
    assert "Record what happened at the 09/01/2026 hearing" in texts and "File the EOIR-28" in texts
    filings = next(x for x in j["deadlines"] if x["id"].endswith(".filings"))
    assert filings["date"] == "2026-10-28" and "when asking for a ruling" in filings["what"]   # 15 days before (Practice Manual 3.1(b))
    appts = journey.client_view(j, "es")["appointments"]
    assert appts[0]["what"].startswith("Audiencia en la corte de inmigración") and appts[0]["time"] == "9:00 AM"
    past = j["hearings"][0]["id"]
    journey.mark(d, "hearing_result", "Paula", item=past, value="Reset to 11/12/2026")
    j = journey.journey(d, TODAY, graph=_graph())
    assert "Record what happened" not in " ".join(s["text"] for s in j["steps"])
    assert any("Reset to 11/12/2026" in e["what"] for e in j["timeline"])


def test_the_consular_path_on_the_family_track(tmp_path):
    import journey

    d = _case_dir(tmp_path)
    g = _graph(petitioner__family_name="EXEMPLO", petitioner__status="U.S. citizen")
    _notice(g, "IOE0555000123", "I-130", "approval", "2026-06-01")
    j = journey.journey(d, TODAY, graph=g)
    assert (j["track"], j["stage"]) == ("family", "consular")              # I-130 approved, no I-485: NVC and the consulate
    g2 = _graph(petitioner__family_name="EXEMPLO", visa__nvc_case_number="RIO2026999001", visa__interview_date="2026-12-03",
                visa__consulate="RIO DE JANEIRO")
    j = journey.journey(d, TODAY, graph=g2)
    assert j["stage"] == "consular" and any(x["id"] == "visa.interview" for x in j["deadlines"])
    assert journey.client_view(j, "pt")["appointments"][0]["what"] == "Entrevista no consulado: RIO DE JANEIRO"
    g3 = _graph(petitioner__family_name="EXEMPLO", visa__nvc_case_number="RIO2026999001", visa__entry_date="2026-09-15")
    j = journey.journey(d, TODAY, graph=g3)
    assert j["stage"] == "resident" and j["citizenship_from"] == "2031-06-17"   # 5 years from entry, less 90 days


def test_the_visa_answer_sheet_and_checklist(tmp_path):
    import visa

    d = _case_dir(tmp_path, {"passport.pdf": "passport", "birth.pdf": "birth_certificate"})
    g = _graph(visa__military="No", visa__arrested="Yes", applicant__marital_status="Single")
    items = visa.checklist(d, g)
    found = {c["text"].split(" (")[0].split(":")[0]: bool(c["found"]) for c in items}
    assert found["Passport biographic page"] and found["Birth certificate"] and not found["Police certificate from the country of nationality and the country where the client lives now"]
    assert any(t.startswith("Court and prison records") for t in found) and not any(t.startswith("Military record") for t in found)
    assert not any(t.startswith("Marriage certificate") for t in found)
    assert "the attorney reviews" in " ".join(visa.problems(d, g, TODAY))
    visa.render(d, g, TODAY)
    text = " ".join(p.extract_text() for p in PdfReader(str(d / "visa_answer_sheet.pdf")).pages)
    assert "DS-260 answer sheet" in text and "EXEMPLO" in text and "ask the client" in text and "22 CFR 42.65" in text


def test_eoir28_fills_the_clients_own_address(tmp_path):
    import court

    g = _graph(applicant__middle_name="LUIZA", applicant__mailing_street="235 FIRM ST", applicant__mailing_same_as_physical="No")
    court.derive(g, TODAY)
    profile = load_profile()
    profile["forms"] = {"eoir28": profile["forms"]["eoir28"]}
    result = fill_companions(g, tmp_path, profile)["eoir28"]
    v = _values(tmp_path / "eoir28_filled.pdf")
    assert v["Address Line 1"] == "10 EXAMPLE ST" and v["MiddleName"] == "L"   # where the client lives, not the firm's mailing address
    assert v["EntryOfAppearanceCB"] == "/All" and v["Primary Atty/Rep"] == "/Primary" and v["ProBono_CB"] == "/No"
    assert "firm.eoir_id" in result["left_blank"]                              # never guessed: the attorney adds it once for the firm


def test_an_online_filing_needs_its_confirmation_number():
    from prefile import validate_mailing

    with pytest.raises(ValueError, match="confirmation number"):
        validate_mailing("2026-09-30", "Online", "", "Andrew", "attorney", TODAY)
    assert validate_mailing("2026-09-30", "Online", "EOIR-123", "Andrew", "attorney", TODAY) == date(2026, 9, 30)


def test_n400_eligibility_has_every_field_before_the_resident_date_is_known():
    import naturalization

    e = naturalization.eligibility(_graph(), TODAY)  # a new citizenship client: no green card date yet -- the page read these and crashed
    assert e["english_exemptions"] == [] and e["file_from"] is None and "No permanent-resident date" in e["blockers"][-1]


def test_court_filing_deadlines_count_like_the_practice_manual(tmp_path):
    import journey

    assert journey._next_business_day(date(2026, 11, 15)) == date(2026, 11, 16)    # a Sunday -> Monday
    assert journey._next_business_day(date(2026, 11, 11)) == date(2026, 11, 12)    # Veterans Day -> the next business day
    assert date(2026, 7, 3) in journey._federal_holidays(2026)                      # July 4, 2026 is a Saturday: observed Friday
    assert date(2026, 11, 26) in journey._federal_holidays(2026)                    # Thanksgiving, the 4th Thursday
    d = _case_dir(tmp_path)
    journey.mark(d, "hearing", "Paula", value={"date": "2026-11-26", "kind": "Individual (merits)"})
    journey.mark(d, "hearing", "Paula", value={"date": "2026-12-10", "kind": "Master calendar", "detained": True})
    j = journey.journey(d, TODAY, graph=_graph())
    due = {x["date"]: x["what"] for x in j["deadlines"] if x["id"].endswith(".filings")}
    assert list(due) == ["2026-11-12"] and "when asking" not in due["2026-11-12"]  # 11/26 - 15 = Veterans Day 11/11 -> 11/12
    assert any("Detained: the court sets the filing deadlines" in s["text"] for s in j["steps"])


def test_a_judges_decision_puts_the_appeal_and_motions_on_the_timeline(tmp_path):
    import journey

    d = _case_dir(tmp_path, {"nta.pdf": "notice_to_appear"})
    journey.mark(d, "hearing", "Paula", value={"date": "2026-09-24", "kind": "Individual (merits)"})
    h = journey.journey(d, TODAY, graph=_graph())["hearings"][0]["id"]
    with pytest.raises(ValueError, match="decision's date"):
        journey.mark(d, "hearing_result", "Paula", item=h, value={"outcome": "Decision: removal ordered or relief denied"})
    journey.mark(d, "hearing_result", "Paula", item=h, value={"outcome": "Decision: removal ordered or relief denied", "decision_date": "2026-09-24"})
    j = journey.journey(d, TODAY, graph=_graph())
    appeal = next(x for x in j["deadlines"] if x["id"].endswith(".appeal"))
    assert appeal["date"] == "2026-10-05" and "RECEIVED by the Board" in appeal["what"]   # 10 days = Sunday 10/04 -> Monday (8 CFR 1003.38(b))
    texts = " ".join(s["text"] for s in j["steps"])
    assert "Herrera-Nunez" in texts and "becomes final when the appeal period ends" in texts and "Record what happened" not in texts
    # an asylum decision that wasn't a 208(a)(2) denial: 30 days; appeal waived: the motion clocks run from the decision
    d2 = tmp_path / "b"
    d2.mkdir()
    journey.mark(d2, "hearing", "Paula", value={"date": "2026-09-24", "kind": "Individual (merits)"})
    h2 = journey.journey(d2, TODAY, graph=_graph())["hearings"][0]["id"]
    journey.mark(d2, "hearing_result", "Paula", item=h2, value={"outcome": "Decision: removal ordered or relief denied", "decision_date": "2026-09-24",
                                                                 "asylum": True})
    assert next(x for x in journey.journey(d2, TODAY, graph=_graph())["deadlines"] if x["id"].endswith(".appeal"))["date"] == "2026-10-26"  # 10/24 is a Saturday
    d3 = tmp_path / "c"
    d3.mkdir()
    journey.mark(d3, "hearing", "Paula", value={"date": "2026-09-24", "kind": "Individual (merits)"})
    h3 = journey.journey(d3, TODAY, graph=_graph())["hearings"][0]["id"]
    journey.mark(d3, "hearing_result", "Paula", item=h3, value={"outcome": "Decision: removal ordered or relief denied", "decision_date": "2026-09-24",
                                                                 "appeal_waived": True})
    due = {x["id"].rsplit(".", 1)[-1]: x["date"] for x in journey.journey(d3, TODAY, graph=_graph())["deadlines"]}
    assert "appeal" not in due and due["reconsider"] == "2026-10-26" and due["reopen"] == "2026-12-23"   # 30 days -> Sat -> Mon; 90 days


def test_an_in_absentia_order_and_a_grant(tmp_path):
    import journey

    d = _case_dir(tmp_path)
    journey.mark(d, "hearing", "Paula", value={"date": "2026-09-10", "kind": "Master calendar"})
    h = journey.journey(d, TODAY, graph=_graph())["hearings"][0]["id"]
    journey.mark(d, "hearing_result", "Paula", item=h, value={"outcome": "Removal ordered in absentia", "decision_date": "2026-09-10"})
    j = journey.journey(d, TODAY, graph=_graph())
    assert next(x for x in j["deadlines"] if x["id"].endswith(".reopen"))["date"] == "2027-03-09"   # 180 days
    d2 = tmp_path / "g"
    d2.mkdir()
    journey.mark(d2, "hearing", "Paula", value={"date": "2026-09-28", "kind": "Individual (merits)"})
    h2 = journey.journey(d2, TODAY, graph=_graph())["hearings"][0]["id"]
    journey.mark(d2, "hearing_result", "Paula", item=h2, value={"outcome": "Decision: relief granted", "decision_date": "2026-09-28"})
    j = journey.journey(d2, TODAY, graph=_graph())
    assert not any(x["id"].endswith(".appeal") for x in j["deadlines"])          # the client won: DHS's appeal window is shown, not a deadline
    assert any("DHS can appeal" in e["what"] and e["date"] == "2026-10-08" for e in j["timeline"])
    old = tmp_path / "o"
    old.mkdir()
    journey.mark(old, "hearing", "Paula", value={"date": "2026-09-01", "kind": "Master calendar"})
    ho = journey.journey(old, TODAY, graph=_graph())["hearings"][0]["id"]
    journey.mark(old, "hearing_result", "Paula", item=ho, value="Reset to 12/01/2026")        # a plain note still works
    assert journey.journey(old, TODAY, graph=_graph())["hearings"][0]["result"]["outcome"] == "Other"


def test_an_answer_must_fit_its_question(tmp_path, monkeypatch):
    import filing_questions

    saved = []
    monkeypatch.setattr("review.state.record_decision", lambda d, item, decision: saved.append(decision))
    monkeypatch.setattr(filing_questions, "status", lambda filing, d: {"ok": True})
    with pytest.raises(ValueError, match="choose one of Yes, No"):
        filing_questions.answer("i751", tmp_path, {"i751.marriage_place": "BOSTON", "i751.in_proceedings": "maybe"}, "Paula")
    assert saved == []                                     # nothing saved when any answer is wrong
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        filing_questions.answer("i751", tmp_path, {"i751.card_expires": "12/15/2026"}, "Paula")
    with pytest.raises(ValueError, match="not a question"):
        filing_questions.answer("i751", tmp_path, {"applicant.ssn": "123"}, "Paula")
    filing_questions.answer("i751", tmp_path, {"i751.card_expires": "2026-12-15", "i751.late_reason": ""}, "Paula")
    assert [d["action"] for d in saved] == ["set", "blank"]


def test_a_citizens_spouse_can_apply_for_citizenship_after_three_years(tmp_path):
    import journey

    d = _case_dir(tmp_path)
    g = _graph(petitioner__status="U.S. citizen", petitioner__family_name="EXEMPLO", family__relationship="spouse", applicant__marriage_date="2020-05-01")
    _notice(g, "IOE0999000901", "I-485", "approval", "2023-02-10")
    j = journey.journey(d, TODAY, graph=g)
    assert (j["stage"], j["citizenship_from"]) == ("resident", "2025-11-12")      # 3 years (INA 319(a)) less 90 days
    assert "consular" not in [x["id"] for x in j["stages"]]                       # adjusted in the U.S.: no consulate on the path
    parent = _graph(petitioner__status="U.S. citizen", petitioner__family_name="EXEMPLO", family__relationship="parent")
    _notice(parent, "IOE0999000902", "I-485", "approval", "2023-02-10")
    assert journey.journey(d, TODAY, graph=parent)["citizenship_from"] == "2027-11-12"   # 5 years for everyone else


def test_a_move_starts_the_ar11_and_eoir33_clocks(tmp_path, monkeypatch):
    import journey

    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})
    d = _case_dir(tmp_path, {"nta.pdf": "notice_to_appear"})
    journey.mark(d, "moved", "Paula", value={"date": "2026-09-28", "address": "12 New Road, Worcester MA"})
    j = journey.journey(d, TODAY, graph=_graph())
    due = {x["id"].rsplit(".", 1)[-1]: x for x in j["deadlines"] if x["id"].startswith("moved.")}
    assert due["ar11"]["date"] == "2026-10-08" and "8 CFR 265.1" in due["ar11"]["what"]          # 10 days
    assert due["eoir33"]["date"] == "2026-10-03" and due["eoir33"]["owner"] == "attorney"        # 5 days: a court case
    journey.mark(d, "done", "Paula", item=due["ar11"]["id"])
    j = journey.journey(d, TODAY, graph=_graph())
    assert [x["id"].rsplit(".", 1)[-1] for x in j["deadlines"] if x["id"].startswith("moved.")] == ["eoir33"]
    with pytest.raises(ValueError, match="future"):
        journey.mark(d, "moved", "Paula", value={"date": "2999-01-01"})
    no_court = tmp_path / "nc"
    no_court.mkdir()
    journey.mark(no_court, "moved", "Paula", value={"date": "2026-09-28"})
    assert not any("eoir33" in x["id"] for x in journey.journey(no_court, TODAY, graph=_graph())["deadlines"])


def test_work_permit_categories_fees_and_addresses(tmp_path):
    import work_permit as wp

    sij = _graph(applicant__i360_receipt_number="IOE0999000001")
    _notice(sij, "IOE0999000123", "I-485", "receipt", "2026-05-01")
    wp.derive(sij, TODAY)
    assert sij.get("ead.category").value == wp.C9 and wp.fee(sij, TODAY)[:2] == (0, None)          # SIJ: no fee
    assert wp.address(sij)[0] is None                                                              # the attorney picks the chart for an SIJ (c)(9)
    asylum = _graph()
    _notice(asylum, "ZLA2690000601", "I-589", "receipt", "2026-06-01")
    wp.derive(asylum, TODAY)
    assert asylum.get("ead.category").value == wp.C8 and asylum.get("ead.category_2").value == "8"
    assert wp.fee(asylum, TODAY)[:2] == (0, 560) and wp.fee(asylum, date(2026, 10, 16))[:2] == (0, 570)   # the Pub. L. 119-21 fee, adjusted 10/16
    assert wp.address(asylum)[0][1] == "ATTN: I-765 C08"
    assert "Too early" in " ".join(wp.problems(tmp_path, asylum, TODAY))                            # 06/01 + 150 days = 10/29/2026
    assert not any("Too early" in p for p in wp.problems(tmp_path, asylum, date(2026, 10, 29)))
    asylee = _graph()
    _notice(asylee, "ZLA2690000602", "I-589", "approval", "2026-03-01")
    wp.derive(asylee, TODAY)
    assert asylee.get("ead.category").value == wp.A5 and wp.fee(asylee, TODAY)[0] == 0
    renewal = _graph(ead__category=wp.A5, ead__reason="Renewal")
    assert wp.fee(renewal, TODAY)[0] == 520                                                         # an asylee's renewal is not free
    family = _graph(petitioner__family_name="EXEMPLO", ead__category=wp.C9)
    _notice(family, "IOE0999000777", "I-485", "receipt", "2025-01-10")
    assert wp.fee(family, TODAY)[0] == 260 and wp.address(family)[0][1] == "ATTN: AOS"            # filed with its fee after 04/01/2024; IOE: family chart
    old = _graph(petitioner__family_name="EXEMPLO", ead__category=wp.C9)
    _notice(old, "IOE0999000778", "I-485", "receipt", "2023-01-10")
    assert wp.fee(old, TODAY)[0] == 0
    letter = wp.letter(asylum, TODAY)
    assert "No filing fee is due for Form I-765" in letter["fees"] and "$560" in letter["fees"] and not letter["no_payment"]


def test_the_change_of_address_filing(tmp_path, monkeypatch):
    import address_change
    import filing_questions
    import journey

    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})
    d = _case_dir(tmp_path, {"nta.pdf": "notice_to_appear"})
    journey.mark(d, "moved", "Paula", value={"date": "2026-09-28", "address": "20 NEW ST, SOMERVILLE MA 02144"})
    assert journey.journey(d, TODAY, graph=_graph())["next_filings"][0]["filing"] == "address"
    # recorded, not yet entered: the case still holds the address the client left -- the AR-11 waits
    g = filing_questions.derive(address_change, d, _graph(applicant__nta_present="Yes", applicant__prior_address_street="45 OLD AVE"), TODAY)
    assert g.get("address.moved_on").value == "2026-09-28" and g.get("address.previous_street").value == "10 EXAMPLE ST"
    assert any(p.startswith("The new home address isn't entered yet: the case still has 10 EXAMPLE ST") for p in address_change.problems(d, g, TODAY))
    notes = " ".join(n["text"] for n in address_change.notes(g, TODAY))
    assert "20 NEW ST, SOMERVILLE MA 02144" in notes and "Due 10/08/2026" in notes and "Due 10/03/2026" in notes and "HARRISONBURG" in notes
    # the paralegal enters the new street: the AR-11's new address is theirs, its old one the address the case held
    g = _graph(applicant__nta_present="Yes", applicant__prior_address_street="45 OLD AVE")
    g.set_by_review("applicant.physical_street", "20 NEW ST", "Paula")
    g = filing_questions.derive(address_change, d, g, TODAY)
    assert g.get("address.previous_street").value == "10 EXAMPLE ST" and g.get("applicant.physical_street").value == "20 NEW ST"
    assert address_change.problems(d, g, TODAY) == ["The immigration court that has the case (for the EOIR-33)."]
    address_change.render(d, g, TODAY)
    text = " ".join(p.extract_text() for p in PdfReader(str(d / "address_change_sheet.pdf")).pages)
    assert "Change of address" in text and "20 NEW ST" in text and "10 EXAMPLE ST" in text and "EOIR-33" in text


def test_i751_pays_its_fee_by_card_unless_the_waiver_is_for_abuse(tmp_path):
    # G-1055 (10/01/26): $750 on paper; $0 for a waiver based on battery or extreme cruelty. Before 10/02/2026 the packet made no payment at all.
    import conditions
    import payment
    from factgraph import FactGraph

    g = FactGraph("c")
    g.add_source("i751.basis", "test", "test", conditions.JOINT, conditions.JOINT, 1.0)
    assert conditions.fee(g, date(2026, 10, 1)) == (750, "the I-751 fee (G-1055, paper filing)")
    pays = payment.payments({"filing": "i751", "forms": ["g28", "i751"]}, tmp_path, g, date(2026, 10, 1))
    assert [(p["form"], p["amount"]) for p in pays] == [("I-751", 750)]
    assert "paid by the enclosed Form G-1450" in conditions.letter(g, date(2026, 10, 1))["fees"]
    g.add_source("i751.waiver_abuse", "test", "test", "Yes", "Yes", 1.0)
    assert conditions.fee(g, date(2026, 10, 1))[0] == 0
    assert payment.payments({"filing": "i751", "forms": ["g28", "i751"]}, tmp_path, g, date(2026, 10, 1)) == []
    assert conditions.letter(g, date(2026, 10, 1))["no_payment"]
