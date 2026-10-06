"""Cancellation of removal in immigration court (src/cancellation.py): which form
(EOIR-42A for a permanent resident, EOIR-42B otherwise), the statute's tests
(INA 240A(a), (b)(1), (c), (d)) with their citations, the fees (EOIR's forms
page, 10/01/2026), where the USCIS package goes (DHS's instructions for
applications in court, 06/08/2026), the filled boxes (including the ones whose
names don't say what they are), the packet's variant and the case page's offer.
Every client value is CONSTRUCTED.
"""

import json
import re
from datetime import date

import pytest
from pypdf import PdfReader

import cancellation
import packet
from factgraph import FactGraph
from fill.companion import fill_companions, load_profile
import schema_path

TODAY = date(2026, 10, 2)
BASE = {"applicant.family_name": "EXEMPLO SOUZA", "applicant.given_name": "MARIA", "applicant.dob": "1990-03-14", "applicant.sex": "F",
        "applicant.a_number": "A099000777", "applicant.birth_city": "SOROCABA", "applicant.country_of_birth": "BRAZIL", "applicant.citizenship": "BRAZIL",
        "applicant.physical_street": "10 EXAMPLE STREET", "applicant.physical_unit_type": "APT", "applicant.physical_apt": "2",
        "applicant.physical_city": "SPRINGFIELD", "applicant.physical_state": "MA", "applicant.physical_zip": "01103",
        "applicant.first_time_in_us": "Yes", "applicant.i94_arrival_date": "2014-07-15", "applicant.last_arrival_manner": "ADMITTED",
        "applicant.i94_class_of_admission": "B2", "applicant.last_arrival_city": "BOSTON", "applicant.last_arrival_state": "MA",
        "applicant.i94_family_name": "EXEMPLO SOUZA", "applicant.i94_given_name": "MARIA", "applicant.arrived_as_crewman": "No",
        "applicant.birth_certificate_name": "MARIA EXEMPLO SOUZA", "applicant.marital_status": "Single", "applicant.nta_present": "Yes"}


def _graph(**extra):
    g = FactGraph("c")
    for key, value in (BASE | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _notice(g, receipt, form, kind, when):
    g.add_source(f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)


def _case(tmp_path, g=None, docs=None, name="case"):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"classifications": docs or {}}), encoding="utf-8")
    if g is not None:
        g.save(d / "fact_graph.json")
    return d


def _short(pdf):
    return {re.split(r"(?<!\\)\.", n)[-1]: f.get("/V") for n, f in PdfReader(str(pdf)).get_fields().items()}


@pytest.fixture(autouse=True)
def _no_visa(monkeypatch):
    import journey

    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})


def test_a_non_resident_with_a_citizen_child_files_the_42b(tmp_path):
    g = _graph(cancel__relative1_is="U.S. citizen child", cancel__relative1_name="EXEMPLO, LUCAS", cancel__relative1_dob="2016-09-01",
               cancel__nta_served="2025-01-10", cancel__ordered_removed="No", cancel__false_testimony="Yes", cancel__visa_petition="No",
               cancel__history_explain="SEE ATTACHED", eoir__electronic_service="Yes")
    cancellation.derive(g, TODAY)
    v = lambda k: g.get(k).value  # noqa: E731
    assert v("cancel.form") == "EOIR-42B (not a permanent resident)" and v("cancel.presence_since") == "2014-07-15"
    assert v("cancel.full_name") == "EXEMPLO SOUZA, MARIA" and v("cancel.birth_name") == "EXEMPLO SOUZA, MARIA"   # the birth certificate agrees
    assert v("cancel.entry_manner") == "Inspected and admitted with a visa" and v("cancel.visa_type") == "B2" and v("cancel.married") == "No"
    assert v("cancel.box_child") == v("cancel.box_child_usc") == v("cancel.box_hardship") == "Yes" and g.get("cancel.box_spouse") is None
    assert cancellation.fee(g, TODAY) == (1690, "the EOIR-42B fee (EOIR's forms page)") and cancellation.biometrics_fee(TODAY) == 30
    assert cancellation.where(g) == (["USCIS", "ATTN: MDW EOIR", "P.O. BOX 660099", "DALLAS, TX 75266-0099"], "Dallas")   # Massachusetts
    notes = " ".join(n["text"] for n in cancellation.notes(g, TODAY))
    assert "$1,690" in notes and "$30 a person" in notes and "EOIR Payment Portal" in notes and "No Form G-1450" in notes
    assert "10 years are reached on 07/15/2024" in notes and "10 years and 179 days" in notes and "4,000" in notes and "1240.21(a)(2)" in notes
    assert "turns 21 on 09/01/2037" in notes
    d = _case(tmp_path, docs={"nta.pdf": "notice_to_appear"})
    assert cancellation.problems(d, g, TODAY) == []
    profile = load_profile()
    profile["forms"] = {"eoir42b": profile["forms"]["eoir42b"]}
    fill_companions(g, tmp_path, profile)
    f = _short(tmp_path / "eoir42b_filled.pdf")
    assert f["Full name"] == "EXEMPLO SOUZA, MARIA" and f["Anumber"] == "A099000777" and f["Birth Date"] == "03/14/1990"
    assert f["CurrentlyReside3"] == "SPRINGFIELD, MA 01103" and f["Residedsince"] == "07/15/2014" and f["21 I"] == "BOSTON, MA"   # '21 I' is item 20
    assert f["Removalchild"] == "/Yes" and f["remchildcit"] == "/Yes" and f["Removal spouse"] in (None, "/Off")
    assert f["Childname1"] == "EXEMPLO, LUCAS" and f["Immigration Status of ChildRow1"] == "U.S. CITIZEN"
    assert f["False Testimony"] == "/2" and f["Deported excluded or removed"] == "/No"         # item 62's Yes is /2 (its /0 is a stray box)
    assert f["Yes_28"] == "/On"                                                                 # the box named 'Yes_28' is item 63's No
    assert f["cancelled under section 240A of the INA"] == "SEE ATTACHED"                       # item 62's first explanation line
    assert f["No service needed I electronically filed this document and the opposing party is participating in ECAS"] == "/On"


def test_the_statute_s_bars_each_with_its_citation(tmp_path):
    d = _case(tmp_path, docs={"nta.pdf": "notice_to_appear"})
    short = _graph(cancel__nta_served="2023-01-10", cancel__long_absence="Yes", cancel__conviction_bar="Yes", cancel__good_moral_character="No",
                   cancel__crewman="Yes", cancel__persecutor="Yes", cancel__relative1_is="U.S. citizen child", cancel__relative1_name="EXEMPLO, LUCAS",
                   cancel__relative1_dob="2004-01-01")
    cancellation.derive(short, TODAY)
    out = " ".join(cancellation.problems(d, short, TODAY))
    for cite in ("240A(b)(1)(A), (d)(1)", "240A(d)(2)", "240A(b)(1)(C)", "240A(b)(1)(B)", "240A(c)(1)", "240A(c)(5)", "101(b)(1)", "Part 10"):
        assert cite in out, cite
    assert "8 years and 179 days" in out
    alone = _graph(cancel__nta_served="2025-01-10", eoir__dhs_address="OPLA BOSTON")
    cancellation.derive(alone, TODAY)
    assert any("No qualifying relative" in p and "240A(b)(1)(D)" in p for p in cancellation.problems(d, alone, TODAY))
    served = _graph(cancel__nta_served="2023-01-10", cancel__military_24_months="Yes", cancel__battered="Yes", eoir__dhs_address="OPLA BOSTON")
    cancellation.derive(served, TODAY)
    assert cancellation.problems(d, served, TODAY) == []                     # 240A(d)(3): 24 months of service excuse continuity
    nowhere = _graph(applicant__nta_present="No", applicant__physical_state="ZZ", eoir__dhs_address="OPLA")
    cancellation.derive(nowhere, TODAY)
    out = " ".join(cancellation.problems(_case(tmp_path, name="nowhere"), nowhere, TODAY))
    assert "8 CFR 1240.20(b)" in out and "lockboxes for court filings" in out


def test_a_permanent_resident_files_the_42a(tmp_path):
    g = _graph(applicant__physical_state="FL", cancel__nta_served="2025-01-10", cancel__aggravated_felony="Yes", cancel__history_explain="SEE ATTACHED",
               cancel__overstayed_vd="Yes", cancel__child_support="Yes", applicant__sex="M")
    _notice(g, "IOE0999000881", "I-485", "approval", "2023-03-01")
    cancellation.derive(g, TODAY)
    assert cancellation.is_42a(g) and g.get("cancel.lpr_date").value == "2023-03-01" and g.get("cancel.g28_forms").value == "EOIR-42A"
    assert cancellation.fee(g, TODAY)[0] == 730 and cancellation.where(g)[1] == "Chicago"   # Florida
    out = " ".join(cancellation.problems(_case(tmp_path, docs={"nta.pdf": "notice_to_appear"}), g, TODAY))
    assert "240A(a)(1)" in out and "03/01/2028" in out and "240A(a)(3)" in out and "Part 10" in out
    assert "the cap" not in " ".join(n["title"] for n in cancellation.notes(g, TODAY)).lower()   # the 4,000 cap is the EOIR-42B's
    profile = load_profile()
    profile["forms"] = {"eoir42a": profile["forms"]["eoir42a"]}
    fill_companions(g, tmp_path, profile)
    f = _short(tmp_path / "eoir42a_filled.pdf")
    assert f["Admitted on"] == "03/01/2023" and f["6 Gender"] == "/Male" and f["Group2"] == "/0" and f["I entered using a"] == "/On"
    assert f["56-2"] == "/Choice2" and f["56-1"] in (None, "/Off") and f["36"] == "/0"           # radio groups: Yes is the left box
    assert f["If you answered Yes to any of the above questions explain 1"] == "SEE ATTACHED"


def test_the_packet_is_the_court_s_and_its_variant_follows_the_form(tmp_path):
    g = _graph()
    d = _case(tmp_path, g, {"nta.pdf": "notice_to_appear"})
    schema = packet.for_case(packet.load_filing("cancellation"), d)
    assert schema["forms"] == ["g28_cancel", "eoir42b"] and schema["cover_letter"] is False and "variant" not in schema
    hand = " ".join(h["text"] for h in schema["handwork"])
    assert "EOIR Payment Portal" in hand and "pay.gov" in hand and "a copy of the signed G-28" in hand and "800-375-5283" in hand
    assert {"relatives", "hardship"} <= {ex["id"] for ex in schema["exhibits"] if ex.get("required")}
    import payment

    assert payment.payments(schema, d, cancellation.derive(g, TODAY), TODAY) == []   # EOIR fees: the EOIR portal and pay.gov, never a G-1450
    resident = _graph()
    _notice(resident, "IOE0999000881", "I-485", "approval", "2015-03-01")
    resident.save(d / "fact_graph.json")
    schema = packet.for_case(packet.load_filing("cancellation"), d)
    assert schema["variant"] == "eoir42a" and schema["forms"] == ["g28_cancel", "eoir42a"] and "permanent resident" in schema["title"]
    import enotice

    assert "cancellation" not in enotice.LOCKBOX_FILINGS                               # filed with the court: no G-1145


def test_the_case_page_offers_it_in_court_and_dates_it_once_chosen(tmp_path):
    import journey

    g = _graph()
    d = _case(tmp_path, docs={"nta.pdf": "notice_to_appear"})
    offer = next(f for f in journey.journey(d, TODAY, graph=g)["next_filings"] if f["filing"] == "cancellation")
    assert offer == {"filing": "cancellation", "now": False, "label": "Cancellation of removal (EOIR-42B), if the attorney chooses it"}
    hearing = {"id": "hearing.2026-11-20.0", "date": "2026-11-20", "kind": "Individual (merits)", "court": "Boston Immigration Court", "detained": False}
    (d / "status.json").write_text(json.dumps({"journey": {"hearings": [hearing]}}), encoding="utf-8")
    g.add_source("cancel.form", "decision", "review", "EOIR-42B (not a permanent resident)", "EOIR-42B (not a permanent resident)", 1.0)
    j = journey.journey(d, TODAY, graph=g)
    offer = next(f for f in j["next_filings"] if f["filing"] == "cancellation")
    assert offer["now"] and offer["label"] == "Cancellation of removal (EOIR-42B): the attorney chose it: file by 11/05/2026"
    due = next(x for x in j["deadlines"] if x["id"] == "cancellation.file")
    assert due["date"] == "2026-11-05" and "Practice Manual 3.1(b), 4.15(j)" in due["what"]     # 15 days before, a Thursday
    assert any(s["id"] == "cancellation" for s in j["steps"])
    resident = _graph()
    _notice(resident, "IOE0999000881", "I-485", "approval", "2015-03-01")
    (d / "status.json").unlink()
    offer = next(f for f in journey.journey(d, TODAY, graph=resident)["next_filings"] if f["filing"] == "cancellation")
    assert "for a permanent resident (EOIR-42A)" in offer["label"]
    outside = _graph(applicant__nta_present="")
    assert not any(f["filing"] == "cancellation" for f in journey.journey(_case(tmp_path, name="outside"), TODAY, graph=outside)["next_filings"])


def test_the_fees_charts_and_register_are_sourced():
    import fees
    import maintenance

    eoir = fees.load(TODAY)["eoir"]
    assert (eoir["eoir42a"], eoir["eoir42b"], eoir["dhs_biometrics"]) == (730, 1690, 30)
    notes = fees.load(TODAY)["notes"]
    assert "91 FR 54211" in notes["eoir42b"] and "G-1055" in notes["dhs_biometrics"]
    chart = json.loads(schema_path.path("law", cancellation.CHART).read_text(encoding="utf-8"))
    states = [s for box in chart["lockboxes"].values() for s in box["states"]]
    assert len(states) == len(set(states)) == 61 and "June 8, 2026" in chart["_source"]
    ids = {i["id"]: i for i in maintenance.registry()["items"]}
    for iid in ("form_eoir42a", "form_eoir42b", "eoir_application_fees", "cancellation_rules"):
        assert iid in ids and ids[iid]["source"], iid
    assert ids["eoir_application_fees"]["party"] == "firm" and ids["eoir_application_fees"]["firm_steps"]
