"""Naturalization (src/naturalization.py, the N-400 in schemas/packets/companion_forms.json).

The form checks run against the real blank N-400 (edition 01/20/25,
schemas/forms/n400/template.pdf): its box names are often wrong, so the map was
built from each box's position and the printed wording -- these tests pin
the boxes whose names mislead. The rules come from the N-400 Instructions
(schemas/law/naturalization.json). Every client value here is CONSTRUCTED:
made-up names, A-Numbers, dates and addresses.
"""

import json
from datetime import date

from pypdf import PdfReader

import journey
import naturalization
from factgraph import FactGraph
from fill.companion import fill_companions, load_profile
from fill.cover_letter import fee_text, fees_for, load_config, mail_to
import schema_path

TODAY = date(2026, 10, 1)


def _graph(**facts):
    g = FactGraph("c")
    for key, value in facts.items():
        g.add_source(key.replace("__", "."), "test", "test", value, value, 1.0)
    return g


BASE = dict(applicant__family_name="EXEMPLO", applicant__given_name="ANA", applicant__a_number="A099999999", applicant__dob="1990-03-14",
            applicant__sex="F", applicant__country_of_birth="BRAZIL", applicant__citizenship="BRAZIL", applicant__marital_status="Single",
            applicant__physical_street="10 EXAMPLE ST", applicant__physical_city="SOMERVILLE", applicant__physical_state="MA",
            applicant__physical_zip="02143", applicant__physical_address_since="2023-06-01", n400__lpr_date="2021-07-15")


def test_the_boxes_whose_names_mislead_get_the_right_answers(tmp_path):
    g = _graph(**BASE, n400__p9_3="Yes", n400__p9_17f="No", n400__p9_31="Yes", n400__parent_citizen_before_18="No",
               n400__fee_reduction="Yes", n400__household_size="4", n400__trips="08/01/2024 - 08/20/2024 BRAZIL")
    naturalization.derive(g, TODAY)
    profile = load_profile()
    profile["forms"] = {"n400": profile["forms"]["n400"]}
    result = fill_companions(g, tmp_path, profile)["n400"]
    assert result["too_long"] == []
    v = {name.rsplit(".", 1)[-1] if "\\." not in name else name: f.get("/V") for name, f in PdfReader(tmp_path / "n400_filled.pdf").get_fields().items()}
    assert v["P4_Line1_State[0]"] == " MA"                      # the dropdown's own option text
    assert v["Part1_Eligibility[2]"] == "/A"                     # Part 1, A: general provision (not married)
    assert v["P2_Line9_DateBecamePermanentResident[0]"] == "07/15/2021"
    assert v["P9_Line3[0]"] == "/Y"                              # item 3's Yes box is [0] (most items: [1])
    assert v["P12_Line17f[1]"] == "/N"                           # 17.f's No box is [1]
    assert v["P12_Line31[1]"] == "/Y"                            # 31: both tooltips say "No"; [1] is Yes
    assert v["P2_Line10_claimdisability[0]"] == "/N"             # named "disability": Part 2, item 10 (parent a citizen)
    assert v["P10_Line1_Citizen[1]"] == "/Y"                     # page 11: Part 10, the fee reduction -- not the spouse's citizenship
    assert v["P10_Line3_HouseHoldSize[0]"] == "4"
    assert v["P8_Line1_DateLeft1[0]"] == "08/01/2024" and v["P9_Line1_Countries1[0]"] == "BRAZIL"
    assert v.get("P10_Line4a_FamilyName[0]") in (None, "")      # Part 5, 4-8: only for a spouse-based filing


def test_when_the_client_can_file():
    g = _graph(**BASE)
    e = naturalization.eligibility(g, TODAY)
    assert (e["basis"], e["earliest"], e["file_from"], e["ready"]) == ("General", "2026-04-16", "2026-10-01", True)  # 07/15/2026 less 90 days
    assert e["presence"]["days_in_us"] >= 913
    # three months in the state, not the 5 years, can be what decides the date
    g = _graph(**(BASE | {"applicant__physical_address_since": "2026-03-01"}))
    e = naturalization.eligibility(g, date(2026, 4, 1))
    assert (e["earliest"], e["earliest_why"].startswith("3 months in the state")) == ("2026-06-01", True)
    # the spouse of a citizen: 3 years
    g = _graph(**(BASE | {"n400__basis": "Spouse of U.S. citizen", "n400__lpr_date": "2023-09-01"}))
    e = naturalization.eligibility(g, TODAY)
    assert (e["years"], e["earliest"], e["presence"]["needed"]) == (3, "2026-06-03", 548)


def test_long_trips_and_too_little_time_in_the_us():
    six_months = "01/10/2025 - 08/20/2025 BRAZIL"
    g = _graph(**(BASE | {"n400__trips": six_months}))
    e = naturalization.eligibility(g, TODAY)
    assert any("over 6 months" in n for n in e["notes"]) and e["ready"]
    g = _graph(**(BASE | {"n400__trips": "01/10/2023 - 03/01/2024 BRAZIL"}))
    e = naturalization.eligibility(g, TODAY)
    assert e["file_from"] is None and any("year or more" in b for b in e["blockers"])
    # many short trips: physical presence decides
    trips = "\n".join(f"01/02/{y} - 07/20/{y} BRAZIL" for y in range(2022, 2027))
    g = _graph(**(BASE | {"n400__trips": trips}))
    e = naturalization.eligibility(g, TODAY)
    assert e["presence"]["days_in_us"] < 913 and (e["file_from"] is None or e["file_from"] > "2026-10-01")
    trips, bad = naturalization.parse_trips("08/01/2024 - 08/20/2024 BRAZIL\nlast summer, Portugal\nNONE")
    assert [t["days"] for t in trips] == [18] and bad == ["last summer, Portugal"]


def test_the_forms_histories_come_from_the_questionnaire():
    g = _graph(**BASE, applicant__ssn="999-00-1234", n400__ssa_card="Yes", applicant__nta_present="Yes",
               questionnaire__prior_address1_street="1 OLD RD", questionnaire__prior_address1_city="LOWELL", questionnaire__prior_address1_state="MA",
               questionnaire__prior_address1_date_from="2018-01-01", questionnaire__prior_address1_date_to="2020-12-31",
               questionnaire__prior_address2_street="2 NEWER AVE", questionnaire__prior_address2_city="MALDEN", questionnaire__prior_address2_state="MA",
               questionnaire__prior_address2_date_from="2021-01-01", questionnaire__prior_address2_date_to="2023-05-31",
               questionnaire__child1_name="BIA EXEMPLO", questionnaire__child1_dob="2015-04-02",
               questionnaire__child2_name="CAIO EXEMPLO", questionnaire__child2_dob="2005-04-02")
    naturalization.derive(g, TODAY)
    v = lambda k: g.get(k).value if g.get(k) else None  # noqa: E731
    assert v("n400.address1_street") == "2 NEWER AVE"   # most recent first; the 2018-2020 address is outside the 5 years
    assert v("n400.address2_street") is None
    assert (v("n400.total_children"), v("n400.child1_name")) == ("1", "BIA EXEMPLO")  # under 18 only
    assert v("n400.ssn") == "999-00-1234"
    assert v("n400.p9_20") == "Yes"                       # the Notice to Appear: a suggestion the client confirms
    assert v("n400.spouse_family_name") is None


def test_what_stops_the_packet(tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps({"classifications": {"nta.pdf": "notice_to_appear", "court.pdf": "criminal_record"}}), encoding="utf-8")
    g = _graph(**(BASE | {"n400__lpr_date": "2023-01-01"}))
    out = " ".join(naturalization.problems(tmp_path, TODAY, graph=g, questions=[]))
    assert "INA 318" in out and "15.b" in out and "Too early: the client can file from 10/03/2027" in out


def test_the_fee_reduction_and_the_mailing_address():
    assert naturalization.fee_reduction_limit(_graph(n400__household_size="4")) == 132000  # 400% of $33,000 (I-864P 2026, 48 states)
    assert naturalization.fee_reduction_limit(_graph(n400__household_size="1")) is None
    config = load_config(schema_path.path("cover_letter", "n400"))
    assert mail_to(config, {"physical_state": "MA"})[0][2] == "P.O. BOX 4060"          # Elgin: unlike Massachusetts' family filings (Chicago)
    assert mail_to(config, {"physical_state": "TX"})[0][2] == "P.O. BOX 660060"
    assert fee_text(config) == "Enclosed is the filing fee of $760 for Form N-400 (Form G-1055, Fee Schedule, edition 10/01/26)."
    assert "$380" in fee_text(fees_for(config, {"fee_reduction": "Yes"})) and "$760" in fee_text(fees_for(config, {}))


def test_the_timeline_follows_the_case_to_citizenship(tmp_path, monkeypatch):
    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})
    d = tmp_path / "b"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"classifications": {"card.pdf": "green_card"}}), encoding="utf-8")
    g = _graph(petitioner__lpr_date="2021-07-15")  # the green-card reader's keys: this card is the client's own
    j = journey.journey(d, TODAY, graph=g)
    assert (j["track"], j["stage"], j["citizenship_from"]) == ("naturalization", "resident", "2026-04-16")
    g.add_source("folder.uscis_case.IOE0999000010.receipt_20261001", "n400.pdf", "uscis_notice", "IOE0999000010", "N-400 RECEIPT, 2026-10-01", 0.9)
    assert journey.journey(d, TODAY, graph=g)["stage"] == "n400_pending"
    g.add_source("folder.uscis_case.IOE0999000010.approval_20270301", "ok.pdf", "uscis_notice", "IOE0999000010", "N-400 APPROVAL, 2027-03-01", 0.9)
    j = journey.journey(d, date(2027, 3, 2), graph=g)
    assert j["stage"] == "oath"                      # approved: a citizen only at the oath ceremony (INA 337)
    assert any(x["id"] == "oath.record" for x in j["steps"])
    journey.mark(d, "oath", "Paula", value={"date": "2027-03-20", "time": "10:00 AM", "place": "USCIS Boston Field Office"})
    j = journey.journey(d, date(2027, 3, 2), graph=g)
    assert j["stage"] == "oath" and any(x["id"] == "oath" and x["owner"] == "client" for x in j["deadlines"])
    assert journey.client_view(j, "pt")["appointments"][0]["what"].startswith("Cerimônia de juramento")
    j = journey.journey(d, date(2027, 3, 21), graph=g)
    assert j["stage"] == "citizen" and journey.client_view(j, "pt")["title"] == "Cidadão dos Estados Unidos"
