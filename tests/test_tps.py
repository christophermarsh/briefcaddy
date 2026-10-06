"""Temporary Protected Status (src/tps.py): offered only while a registration period is open (8 CFR 244.17; USCIS's TPS page, updated
09/09/2026), switched on by the dates in schemas/law/tps.json and nothing else, the fees (G-1055 10/01/26 and the FY 2027 alert), the
lockbox by country and state, the filled I-821 and I-765, and the case path. Every client value is CONSTRUCTED.
"""

import json
import re
import shutil
from datetime import date

import pytest
from pypdf import PdfReader

import enotice
import journey
import packet
import tps
from factgraph import FactGraph
from filing_questions import STATES
from fill.companion import fill_companions, load_profile

TODAY = date(2026, 10, 2)


def _graph(**extra):
    g = FactGraph("c")
    for key, value in ({"applicant.family_name": "EXEMPLO SOUZA", "applicant.given_name": "ANA CLARA", "applicant.dob": "1990-03-14", "applicant.sex": "F",
                        "applicant.country_of_birth": "SUDAN", "applicant.citizenship": "SUDAN", "applicant.a_number": "A099000123",
                        "applicant.physical_street": "10 EXAMPLE ST", "applicant.physical_city": "SOMERVILLE", "applicant.physical_state": "MA",
                        "applicant.physical_zip": "02143"} | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


@pytest.fixture
def reopened(tmp_path, monkeypatch):
    """USCIS publishes a re-registration period for Sudan: the dates are copied into the country's entry, nothing else changes."""
    path = tmp_path / "tps.json"
    shutil.copy(tps.DATA, path)
    d = json.loads(path.read_text(encoding="utf-8"))
    d["countries"]["Sudan"]["periods"].append({"kind": "re-registration", "from": "2026-09-15", "to": "2026-11-14", "source": "TEST notice", "url": "https://example.test/fr"})
    path.write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setattr(tps, "DATA", path)
    return path


def test_the_data_is_the_pages_and_covers_every_state():
    d = tps.data()
    assert {n for n, c in d["countries"].items() if c["status"] == "designated"} == {"El Salvador", "Lebanon", "Sudan", "Ukraine"}
    assert d["countries"]["Venezuela"]["status"] == "terminated" and "Haiti" in d["terminated"] and len(d["part7"]["items"]) == 71
    for name, c in d["countries"].items():
        states = [s for box in c["lockboxes"].values() for s in box["states"]]
        if "*" not in states and states:
            assert sorted(states) == sorted(set(STATES.values())), name   # every state, DC and the territories, once
    assert tps.country_of(_graph())[0] == "Sudan" and tps.country_of(_graph(applicant__citizenship="UKRAINIAN"))[0] == "Ukraine"
    assert tps.country_of(_graph(applicant__citizenship="BRAZIL")) == (None, None)


def test_nothing_is_open_today_so_nothing_is_offered(tmp_path):
    w = tps.window(_graph(), TODAY)
    assert w["kind"] == "closed" and w["last"]["to"] == "2025-03-18" and not tps.offered(_graph(), TODAY)
    problems = " ".join(tps.problems(tmp_path, _graph(), TODAY))
    assert "No registration period is open for Sudan" in problems and "8 CFR 244.17" in problems and "03/18/2025" in problems
    gone = tps.window(_graph(applicant__citizenship="VENEZUELA"), TODAY)
    assert gone["kind"] == "terminated" and "terminated" in " ".join(tps.problems(tmp_path, _graph(applicant__citizenship="VENEZUELA"), TODAY))
    assert tps.window(_graph(applicant__citizenship="HAITI"), TODAY)["kind"] == "terminated"
    assert tps.window(_graph(applicant__citizenship="BRAZIL"), TODAY)["kind"] == "unlisted"
    assert tps.window(_graph(applicant__citizenship="BRAZIL"), TODAY)["country"] == "BRAZIL"


def test_a_reopened_designation_switches_it_on_through_the_dates_alone(tmp_path, reopened):
    g = _graph(tps__type=tps.REREG, tps__granted_by="USCIS", applicant__ead_category="A12", applicant__ead_expiration_date="2026-04-19")
    assert tps.offered(g, TODAY) and not tps.offered(g, date(2026, 11, 15)) and not tps.offered(g, date(2026, 9, 14))   # the notice's own days, not a fixed window
    out = " ".join(tps.problems(tmp_path, g, TODAY))
    assert "No registration period" not in out and "only the initial registration period is open" not in out
    assert any(n["title"] == "The period is open" and "11/14/2026" in n["text"] for n in tps.notes(g, TODAY))
    first_time = _graph(tps__type=tps.INITIAL)       # a re-registration period is for clients who already have TPS
    assert "can't register for the first time now" in " ".join(tps.problems(tmp_path, first_time, TODAY))


def test_the_case_path_offers_it_only_for_a_designated_country(tmp_path, reopened):
    def offer(g, day=TODAY):
        return next((f for f in journey.journey(tmp_path, day, graph=g)["next_filings"] if f["filing"] == "tps"), None)

    open_now = offer(_graph())
    assert open_now and open_now["now"] is True and "re-registration period is open until 11/14/2026" in open_now["label"]
    later = offer(_graph(), date(2026, 12, 1))
    assert later is None   # a closed country is not a next filing (it stays on the More filings list)
    assert offer(_graph(applicant__citizenship="VENEZUELA")) is None and offer(_graph(applicant__citizenship="BRAZIL")) is None
    g = _graph()
    g.add_source("folder.uscis_case.IOE0999000777.receipt_20260920", "r.pdf", "uscis_notice", "IOE0999000777", "I-821 RECEIPT, 2026-09-20", 0.9)
    assert offer(g) is None   # already filed in this period


def test_nothing_closed_is_a_next_filing_and_a_citizen_stays_the_end_of_the_road(tmp_path):
    def next_ones(g):
        return [f["filing"] for f in journey.journey(tmp_path, TODAY, graph=g)["next_filings"]]

    assert "tps" not in next_ones(_graph(applicant__citizenship="EL SALVADOR"))            # a Salvadoran case: no period is open
    citizen = _graph(applicant__citizenship="UKRAINE")
    citizen.add_source("folder.uscis_case.IOE0999000321.approval_20200601", "a.pdf", "uscis_notice", "IOE0999000321", "N-400 APPROVAL, 2020-06-01", 0.9)
    journey.mark(tmp_path, "oath", "Paralegal", value={"date": "2020-07-04"})
    j = journey.journey(tmp_path, TODAY, graph=citizen)
    assert j["stage"] == "citizen" and j["next_filings"] == []


def test_a_venezuelan_client_gets_a_panel_not_an_error(tmp_path):
    g = _graph(applicant__citizenship="VENEZUELA")
    out = tps.notes(g, TODAY)
    assert any(n["title"] == "Terminated" for n in out) and not any("Continuous residence" in n["title"] for n in out)
    assert any("terminated" in p for p in tps.problems(tmp_path, g, TODAY))


def test_the_lockbox_by_country_and_state():
    line = lambda country, state: tps.address(_graph(applicant__citizenship=country, applicant__physical_state=state))[0][2]  # noqa: E731
    assert line("UKRAINE", "MA") == "P.O. Box 4464" and line("UKRAINE", "FL") == "P.O. Box 4464" and line("UKRAINE", "CA") == "P.O. Box 24047"
    assert line("EL SALVADOR", "MA") == "P.O. Box 8635" and line("EL SALVADOR", "FL") == "P.O. Box 4091" and line("EL SALVADOR", "TX") == "P.O. Box 660864"
    assert line("SUDAN", "MA") == line("LEBANON", "FL") == "P.O. Box 6943"
    assert tps.address(_graph(applicant__citizenship="VENEZUELA"))[0] is None
    assert "g1145" not in enotice.LOCKBOX_FILINGS and "tps" in enotice.LOCKBOX_FILINGS


def test_the_fees_and_one_payment_each():
    g = _graph(tps__type=tps.INITIAL)
    tps.derive(g, TODAY)
    assert tps.fees(g, TODAY) == {"i821": 510, "biometrics": 30, "i765": 520, "i765_pl": 560}
    assert tps.fees(g, date(2026, 10, 16))["i821"] == 520 and tps.fees(g, date(2026, 10, 16))["i765_pl"] == 570     # the FY 2027 alert
    pays = tps.payments(g, TODAY, ["g28_tps", "i821", "i765_tps"])
    assert [(p[1], p[2]) for p in pays] == [("I-821", 510), ("I-821 biometrics", 30), ("I-765", 520), ("I-765", 560)]
    rereg = _graph(tps__type=tps.REREG, applicant__ead_category="A12", applicant__ead_expiration_date="2026-04-19")
    tps.derive(rereg, TODAY)
    f = tps.fees(rereg, TODAY)
    assert (f["i821"], f["biometrics"], f["i765"], f["i765_pl"]) == (0, 30, 520, 280)
    assert tps.forms_for(_graph(tps__ead="No"), ["g28_tps", "i821", "i765_tps"]) == ["g28_tps", "i821"]
    assert tps.forms_for(_graph(tps__ead="Yes"), ["g28_tps", "i821", "i765_tps"]) == ["g28_tps", "i821", "i765_tps"]


def test_what_stops_an_application(tmp_path, reopened):
    out = lambda g: " ".join(tps.problems(tmp_path, g, TODAY))  # noqa: E731
    assert "after Sudan's continuous residence date, 08/16/2023" in out(_graph(tps__entry_date="2024-01-05"))
    assert "after Sudan's" not in out(_graph(tps__entry_date="2020-01-05"))
    assert "needs the A-Number" in out(_graph(tps__type=tps.REREG, applicant__a_number=""))
    assert "Evidence of the client's identity and nationality" in out(_graph()) and "continuous residence in the U.S." in out(_graph())
    assert "certified court dispositions" in out(_graph(tps__q15a="Yes"))
    note = " ".join(n["text"] for n in tps.notes(_graph(tps__q8a="Yes", tps__q15a="Yes"), TODAY))
    assert "Yes to 8.A, 15.A" in note


def test_the_forms_fill_from_the_case(tmp_path):
    answers = {"tps__type": tps.REREG, "tps__granted_by": "USCIS", "tps__ead": "Yes", "tps__birth_city": "KHARTOUM", "tps__marital_status": "Single",
               "tps__last_entry_date": "2019-04-02", "tps__entry_date": "2019-04-02", "tps__entry_status": "B-2", "tps__current_status": "TPS",
               "tps__in_proceedings": "No", "tps__traveled_elsewhere": "No", "applicant__ead_category": "A12", "applicant__ead_expiration_date": "2026-04-19"}
    answers |= {f"tps__q{x['item']}": "No" for x in tps.data()["part7"]["items"]}
    answers["tps__q15a"] = "Yes"
    g = tps.derive(_graph(**answers), TODAY)
    profile = load_profile()
    profile["forms"] = {k: profile["forms"][k] for k in ("g28_tps", "i821", "i765_tps")}
    done = fill_companions(g, tmp_path, profile)

    def boxes(name):
        return {re.split(r"(?<!\\)\.", n)[-1]: x.get("/V") for n, x in PdfReader(str(tmp_path / name)).get_fields().items()}

    f = boxes("i821_filled.pdf")
    assert f["Part1_Item1_ApplicationType[1]"] == "/1b" and f["Part1_Item2_GrantedTPSU[0]"] == "/U" and f["Part1_Item3_EADApp[0]"] == "/A"
    assert f["Part1_TPScountry[0]"] == f["Part7_Item1_CountryResidence[0]"] == "SUDAN" and f["Part2_Item5_YN[0]"] == "/Y" and f["Part2_Item4_State[0]"].strip() == "MA"
    assert f["Part2_Item7_AlienNumber[0]"] == "099000123" and f["Part2_Item12_Sex[1]"] == "/F" and f["Part2_Item17_MaritalStatus[0]"] == "/S"
    assert f["Part7_Item13a_YN[0]"] == "/Y" and f["Part7_Item13a_YN[1]"] in (None, "/Off") and f["Part7_Item4a_YN[1]"] == "/N"   # 15.A is Yes, 8.A is No
    assert f["Part7_Item18c_YN[1]"] == "/N"   # item 20.C, whose tooltip names 20.A and 20.B
    e = boxes("i765_tps_filled.pdf")
    assert (e["section_1[0]"], e["section_2[0]"]) == ("a", "12") and e["Part1_Checkbox[2]"] == "/3"      # (a)(12), a renewal
    assert e["Line23_StatusLastEntry[0]"] == "B-2" and e["Line19_Checkbox[1]"] == "/Y"   # item 24: the status at the last entry; item 12: an I-765 filed before
    first = tps.derive(_graph(**{k: v for k, v in answers.items() if not k.startswith("applicant__ead")}), TODAY)
    assert first.get("tps_ead.previous_filed").value == "No" and first.get("tps_ead.entry_status").value == "B-2"   # an initial: item 12 is No, not blank
    assert boxes("g28_tps_filled.pdf")["Line1b_ListFormNumber[0]"] == "I-821, I-765"
    assert not [k for k in done["i821"]["left_blank"] if k.startswith("tps.q") or k in ("applicant.family_name", "applicant.dob", "tps.mailing_street")]


def test_the_packet_is_on_the_engine():
    forms = load_profile()["forms"]
    schema = packet.load_filing("tps")
    assert schema["forms"] == ["g28_tps", "i821", "i765_tps"] and all(f in forms for f in schema["forms"])
    assert forms["i765_tps"]["template"] == forms["ead"]["template"] and not any(k.startswith("ead.") for k in forms["i765_tps"]["map"])
    letter = tps.letter(_graph(tps__type=tps.INITIAL, tps__ead="Yes"), TODAY)
    assert "$510 for Form I-821" in letter["fees"] and "$30 for the I-821 biometric" in letter["fees"] and "(c)(19)" in letter["re_lines"][1]
