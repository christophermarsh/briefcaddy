"""DACA renewals (src/daca.py): what USCIS takes today (renewals; initial requests
accepted but not processed, uscis.gov/DACA updated 01/24/2025), the renewal window
(150 to 120 days before the grant expires; a renewal until one year after), the
fees (G-1055 10/01/26: $85 and $520), the DACA lockbox by state, the filled I-821D,
I-765 (c)(33) and I-765WS, and the case path. Every client value is CONSTRUCTED.
"""

import json
import re
from datetime import date, timedelta

from pypdf import PdfReader

import daca
import enotice
import journey
import packet
from factgraph import FactGraph
from fill.companion import fill_companions, load_profile
import schema_path

TODAY = date(2026, 10, 2)
EXPIRES = TODAY + timedelta(days=135)  # inside the window: 150 to 120 days before


def _graph(**extra):
    g = FactGraph("c")
    for key, value in ({"applicant.family_name": "EXEMPLO SOUZA", "applicant.given_name": "ANA CLARA", "applicant.dob": "1999-03-14",
                        "applicant.country_of_birth": "MEXICO", "applicant.citizenship": "MEXICO", "applicant.a_number": "A099000123",
                        "applicant.sex": "F", "applicant.physical_street": "10 EXAMPLE ST", "applicant.physical_city": "SOMERVILLE",
                        "applicant.physical_state": "MA", "applicant.physical_zip": "02143", "applicant.ead_category": "C33",
                        "applicant.ead_expiration_date": EXPIRES.isoformat()}
                       | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _notice(g, receipt, form, kind, when):
    g.add_source(f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)


def test_the_renewal_window_and_what_uscis_takes():
    g = _graph()
    assert daca.recipient(g) and daca.expires(g) == (EXPIRES, "the (c)(33) work permit in the folder")
    kinds = {when: daca.window(g, when)["kind"] for when in (EXPIRES - timedelta(days=151), EXPIRES - timedelta(days=150), EXPIRES - timedelta(days=120),
                                                             EXPIRES - timedelta(days=119), EXPIRES, EXPIRES + timedelta(days=1),
                                                             EXPIRES.replace(year=EXPIRES.year + 1), EXPIRES.replace(year=EXPIRES.year + 1) + timedelta(days=1))}
    assert list(kinds.values()) == ["early", "open", "open", "late", "late", "expired", "expired", "initial"]
    daca.derive(g, TODAY)
    assert g.get("daca.request_type").value == "Renewal" and g.get("daca.renewal_expires").value == EXPIRES.isoformat()
    assert "accepts initial requests but does not process them" in daca.STATUS and "01/24/2025" in daca.STATUS


def test_too_early_and_an_initial_request_stop_the_packet(tmp_path):
    early = _graph(applicant__ead_expiration_date=(TODAY + timedelta(days=200)).isoformat())
    daca.derive(early, TODAY)
    assert any(p.startswith("Too early") and "150 days before" in p for p in daca.problems(tmp_path, early, TODAY))
    old = _graph(applicant__ead_expiration_date="2024-01-31")  # expired more than a year ago: an initial request
    daca.derive(old, TODAY)
    assert old.get("daca.request_type").value == "Initial request"
    assert any("does not process it now" in p and "uscis.gov/DACA" in p for p in daca.problems(tmp_path, old, TODAY))
    ended = _graph(daca__terminated="Yes")
    daca.derive(ended, TODAY)
    assert ended.get("daca.request_type").value == "Initial request"
    nothing = _graph(applicant__ead_category="C09", applicant__a_number="")
    out = " ".join(daca.problems(tmp_path, daca.derive(nothing, TODAY), TODAY))
    assert "expiration date isn't in the case" in out and "No A-Number" in out


def test_fees_the_lockbox_by_state_and_one_payment_each():
    assert daca.fees(TODAY) == (85, 520)
    assert daca.payments(_graph(), TODAY, ["g28_daca", "i821d", "i765_daca", "i765ws"]) == [
        ("i821d", "I-821D", 85, "Form I-821D filing fee"), ("i765_daca", "I-765", 520, "Form I-765 filing fee, category (c)(33)")]
    assert daca.address(_graph())[0] == ["USCIS", "ATTN: DACA", "P.O. BOX 5757", "CHICAGO, IL 60680-5757"]  # Massachusetts
    assert daca.address(_graph(applicant__physical_state="FL"))[0][2] == "P.O. BOX 660045"                     # Florida: Dallas
    assert daca.address(_graph(applicant__physical_state="CA"))[0][2] == "P.O. BOX 20700"                      # Phoenix
    assert daca.address(_graph(applicant__physical_state="AS"))[0] is None                                      # not on the page
    chart = json.loads((schema_path.path("law", "uscis_lockboxes_daca")).read_text(encoding="utf-8"))["lockboxes"]
    states = [s for box in chart.values() for s in box["states"]]
    assert len(states) == len(set(states)) == 54                                                                # 50 states, DC, GU, PR, VI
    letter = daca.letter(_graph(), TODAY)
    assert "$85 for Form I-821D and $520 for Form I-765" in letter["fees"] and letter["mail_to"][2] == "P.O. BOX 5757"
    assert "daca" in enotice.LOCKBOX_FILINGS


def test_the_forms_fill_from_the_case(tmp_path):
    answers = {"daca__detention": "Not in immigration detention", "daca__q5_proceedings": "No", "daca__continuous_residence": "Yes",
               "daca__present_since": "2020-05-01", "daca__moved": "No", "daca__left_us": "No", "daca__q8_without_advance_parole": "No",
               "daca__ws_income": "24000", "daca__ws_expenses": "21000", "daca__ws_assets": "1500"}
    answers |= {k.replace(".", "__"): "No" for k, _ in daca.PART4}
    answers["daca__p4_q1_arrested_us"] = "Yes"
    answers["daca__p4_q4_gang"] = "No"
    g = daca.derive(_graph(**answers), TODAY)
    profile = load_profile()
    profile["forms"] = {k: profile["forms"][k] for k in ("g28_daca", "i821d", "i765_daca", "i765ws")}
    done = fill_companions(g, tmp_path, profile)

    def boxes(name):
        return {re.split(r"(?<!\\)\.", n)[-1]: x.get("/V") for n, x in PdfReader(str(tmp_path / name)).get_fields().items()}

    f = boxes("i821d_filled.pdf")
    assert f["P1_Line2_Checkbox[0]"] == "/R" and f["P1_Line2_Date[0]"] == EXPIRES.strftime("%m/%d/%Y") and f["Part1_CB[1]"] == "/AM NOT"
    assert f["P1_Line7_ANumber[0]"] == "099000123" and f["P1_Line3a_Name[1]"] == "EXEMPLO SOUZA" and f["P1_Line4e_State[0]"] == "MA"
    assert f["P1_Line12_CountryRes[0]"] == "UNITED STATES" and f["P2_Line2b_Street[0]"] == "10 EXAMPLE ST"
    assert f["P4_Line1_Checkbox[0]"] == "/Yes" and f["P4_Line4_Checkbox[0]"] == "/No"     # item 1: Yes first; item 4: No first
    assert f["P2_Line8_Checkbox[1]"] == "/No" and f["P1_Line5_Checkbox[1]"] == "/N"
    e = boxes("i765_daca_filled.pdf")
    assert (e["section_1[0]"], e["section_2[0]"]) == ("c", "33") and e["Part1_Checkbox[2]"] == "/3" and e["Line24_CurrentStatus[0]"] == "Deferred action (DACA)"
    w = boxes("i765ws_filled.pdf")
    assert (w["Line1_IndividualAnnualIncome[0]"], w["Line2_IndividualAnnualExpense[0]"], w["Line3_TotalAssets[0]"]) == ("24000", "21000", "1500")
    assert boxes("g28_daca_filled.pdf")["Line1b_ListFormNumber[0]"] == "I-821D, I-765"
    assert not done["i765ws"]["left_blank"] and not [k for k in done["i765_daca"]["left_blank"] if k.startswith("ead.")]


def test_the_i765_borrows_the_work_permit_map_with_its_own_answers():
    forms = load_profile()["forms"]
    keys = set(forms["i765_daca"]["map"])
    assert {"daca_ead.reason", "daca_ead.category_2", "applicant.a_number"} <= keys and not any(k.startswith("ead.") for k in keys)
    assert forms["i765_daca"]["template"] == forms["ead"]["template"] and "companion.g28_forms_daca" in forms["g28_daca"]["map"]
    schema = packet.load_filing("daca")
    assert schema["forms"] == ["g28_daca", "i821d", "i765_daca", "i765ws"] and all(f in forms for f in schema["forms"])


def test_the_case_path_puts_the_renewal_on_the_timeline(tmp_path):
    g = _graph()
    j = journey.journey(tmp_path, TODAY, graph=g)
    assert (j["track"], j["stage"]) == ("daca", "daca_current")
    due = next(d for d in j["deadlines"] if d["id"] == "daca")
    assert due["date"] == (EXPIRES - timedelta(days=120)).isoformat() and not any(d["id"] == "ead" for d in j["deadlines"])
    nxt = next(f for f in j["next_filings"] if f["filing"] == "daca")
    assert nxt["now"] and "120 days before DACA expires" in nxt["label"]
    early = journey.journey(tmp_path, TODAY, graph=_graph(applicant__ead_expiration_date=(TODAY + timedelta(days=300)).isoformat()))
    assert not any(d["id"] == "daca" for d in early["deadlines"]) and any("DACA renewal can be filed" in e["what"] for e in early["timeline"])
    _notice(g, "IOE0999000777", "I-821D", "receipt", "2026-09-20")
    assert journey.journey(tmp_path, TODAY, graph=g)["stage"] == "daca_pending"
    other = journey.journey(tmp_path, TODAY, graph=_graph(applicant__ead_category="C09"))  # any other work permit keeps its own 180 days
    assert other["track"] != "daca" and any(d["id"] == "ead" for d in other["deadlines"])
