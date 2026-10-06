"""Asylum (src/asylum.py, the I-589 in schemas/packets/companion_forms.json).

The form checks run against the real blank I-589 (edition 07/28/26): its
boxes were mapped by position, and these tests pin the ones that mislead.
The rules come from the I-589 Instructions and USCIS's I-589 page
(schemas/law/asylum.json). Every client value is CONSTRUCTED.
"""

import json
from datetime import date

from pypdf import PdfReader

import asylum
import fees
import journey
from factgraph import FactGraph
from fill.companion import fill_companions, load_profile
from fill.cover_letter import fee_text, fees_for, load_config, mail_to
import schema_path

TODAY = date(2026, 10, 1)
BASE = {"applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA", "applicant.a_number": "A099999999", "applicant.dob": "1995-03-14",
        "applicant.sex": "F", "applicant.country_of_birth": "HONDURAS", "applicant.birth_city": "SAN PEDRO SULA", "applicant.citizenship": "HONDURAS",
        "applicant.marital_status": "Single", "applicant.physical_street": "10 EXAMPLE ST", "applicant.physical_city": "SOMERVILLE",
        "applicant.physical_state": "MA", "applicant.physical_zip": "02143", "applicant.i94_arrival_date": "2026-02-10"}


def _graph(**extra):
    g = FactGraph("c")
    for key, value in (BASE | extra).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _values(pdf):
    import re

    return {re.split(r"(?<!\\)\.", n)[-1]: f.get("/V") for n, f in PdfReader(str(pdf)).get_fields().items()}


def test_the_boxes_that_mislead_get_the_right_answers(tmp_path):
    g = _graph(**{"asylum.has_children": "No", "asylum.mother_location": "DECEASED", "applicant.mother_given_name": "MARIA",
                  "applicant.mother_family_name": "EXEMPLO", "applicant.physical_address_since": "2026-03-01",
                  "asylum.family_assisted": "Yes", "asylum.b1a": "Yes", "asylum.b1a_explain": "Threats in 2025.", "asylum.basis_social_group": "Yes"})
    asylum.derive(g, TODAY)
    profile = load_profile()
    profile["forms"] = {"i589": profile["forms"]["i589"]}
    assert fill_companions(g, tmp_path, profile)["i589"]["too_long"] == []
    v = _values(tmp_path / "i589_filled.pdf")
    assert v["ChildrenCheckbox[1]"] == "/Y"            # the box on /Y says "I do NOT have any children"
    assert v.get("ChildrenCheckbox[0]") in (None, "/Off")
    assert v["CheckBoxAIII5\\.m[0]"] == "/1"           # the mother's "deceased" box, named with an escaped dot
    assert v["DateTimeField24[0]"] == "03/2026"        # Part A.III, 2: residence "From (Mo/Yr)"
    assert v["PtD_ckboxynd1[0]"] == "/2"               # Part D: family helped -> Yes (its on-value is /2)
    assert v["CheckBoxsocial[0]"] == "/Y" and v["ckboxyn1a[0]"] == "/Y" and v["TextField14[0]"] == "Threats in 2025."
    assert v["TextField1[4]"] == "SAN PEDRO SULA, HONDURAS" and v["PtAILine1_ANumber[0]"] == "099999999"


def test_the_one_year_deadline():
    y = asylum.one_year(_graph(), TODAY)
    assert (y["deadline"], y["mail_by"], y["level"]) == ("2027-02-09", "2027-01-26", "ok")
    late = asylum.one_year(_graph(**{"applicant.i94_arrival_date": "2025-06-01"}), TODAY)
    assert late["level"] == "late" and "changed or extraordinary" in late["text"]
    minor = asylum.one_year(_graph(**{"applicant.dob": "2010-01-01", "applicant.i94_arrival_date": "2025-06-01"}), TODAY)
    assert minor["minor_at_arrival"] and "unaccompanied alien child" in minor["text"]
    uac = asylum.one_year(_graph(**{"applicant.dob": "2010-01-01", "applicant.i94_arrival_date": "2025-06-01", "asylum.uac": "Yes"}), TODAY)
    assert uac["level"] == "ok" and "doesn't apply" in uac["text"]
    g = _graph(**{"applicant.i94_arrival_date": "2025-06-01"})
    asylum.derive(g, TODAY)
    assert g.get("asylum.c5").value == "Yes"  # Part C, 5: filing more than 1 year after the last arrival


def test_where_it_is_filed():
    assert asylum.where_to_file(_graph(**{"asylum.court": "now"}))["with"] == "court"
    assert asylum.where_to_file(_graph(**{"asylum.court": "now", "asylum.uac": "Yes"}))["with"] == "uscis"  # a UAC files with USCIS even in court
    assert asylum.where_to_file(_graph(**{"applicant.nta_present": "Yes"}))["with"] == "unknown"
    config = load_config(schema_path.path("cover_letter", "i589"))
    assert mail_to(config, {"physical_state": "MA"})[0][2] == "P.O. BOX 6893"      # Chicago
    assert mail_to(config, {"physical_state": "FL"})[0][2] == "P.O. BOX 653080"    # Dallas


def test_what_uscis_rejects_and_the_attorney_must_see(tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps({"classifications": {}}), encoding="utf-8")
    g = _graph(**{"asylum.b1a": "Yes", "asylum.b1b": "Yes", "asylum.ms_l": "Yes"})
    asylum.derive(g, TODAY)
    out = " ".join(asylum.problems(tmp_path, TODAY, graph=g, questions=[]))
    assert "USCIS rejects an I-589 with these blank" in out and "Part B, 2" in out
    assert "A Yes without its explanation" in out and "Part B, 1A" in out
    assert "no basis checked and no explanation" in out
    assert "Ms. L" in out


def test_a_long_answer_goes_on_the_forms_own_supplement_b_page_and_copies_of_it(tmp_path):
    story = "On March 3, 2025, men from the gang came to the store and said they would kill my brother. " * 80
    g = _graph(**{"asylum.b1b": "Yes", "asylum.b1b_explain": story, "applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA"})
    asylum.derive(g, TODAY)
    assert g.get("asylum.b1b_explain").value == "SEE SUPPLEMENT B / ATTACHED SHEET: PART B, QUESTION 1.B."
    blocks = asylum.supplement_blocks(g)
    assert len(blocks) == 1 and (blocks[0].page, blocks[0].part, blocks[0].item) == asylum.spot("b1b")  # the form's own page, part and question
    profile = load_profile()
    profile["forms"] = {"i589": profile["forms"]["i589"]}
    fill_companions(g, tmp_path, profile)
    copies = asylum.attach_supplements(tmp_path, g)
    assert copies >= 1
    pdf = PdfReader(str(tmp_path / "i589_filled.pdf"))
    assert len(pdf.pages) == 12 + copies
    fields = pdf.get_fields()
    box = lambda name: str(next(v for k, v in fields.items() if k == name or k.endswith("." + name))["/V"])  # noqa: E731
    assert box("TextField32[0]").startswith("On March 3, 2025")  # the form's own Supplement B page holds the start
    assert box("TextField32[0]__copy1").startswith("(continued)")  # a copy of that page carries the rest
    assert box("TextField31[0]__copy1") == "B" and box("TextField31[1]__copy1") == "1.B"
    assert box("SupBApplicantName[0]__copy1") == "ANA EXEMPLO"
    assert "Form I-589 Supplement B" in pdf.pages[-1].extract_text()  # the form's own page, footer and all


def test_fees_change_on_their_date_and_the_letter_states_the_right_one():
    assert fees.load(date(2026, 10, 15))["pl_119_21"]["annual_asylum"] == 102
    assert fees.load(date(2026, 10, 16))["pl_119_21"]["annual_asylum"] == 105  # USCIS alert of 09/30/2026
    assert [c["fee"] for c in fees.upcoming(["pl_119_21.i765_asylum_initial"], 30, TODAY)] == ["pl_119_21.i765_asylum_initial"]
    config = load_config(schema_path.path("cover_letter", "i589"))
    assert "asylum application fee of $100" in fee_text(fees_for(config, {}))
    ms_l = fees_for(config, {"ms_l": "Yes"})
    assert "no Public Law 119-21 asylum fee is due" in fee_text(ms_l) and ms_l["no_payment"]


def test_the_timeline_follows_an_asylum_case(tmp_path, monkeypatch):
    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})
    d = tmp_path / "b"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"classifications": {}}), encoding="utf-8")
    g = _graph(**{"asylum.b1b": "Yes"})
    j = journey.journey(d, TODAY, graph=g)
    assert (j["track"], j["stage"]) == ("asylum", "asylum_ready")
    assert next(x for x in j["deadlines"] if x["id"] == "one_year")["date"] == "2027-02-09"
    g.add_source("folder.uscis_case.IOE0999000030.receipt_20261101", "r.pdf", "uscis_notice", "IOE0999000030", "I-589 RECEIPT, 2026-11-01", 0.9)
    j = journey.journey(d, date(2026, 11, 2), graph=g)
    assert j["stage"] == "asylum_pending" and not any(x["id"] == "one_year" for x in j["deadlines"])
    g.add_source("folder.uscis_case.IOE0999000030.approval_20270601", "a.pdf", "uscis_notice", "IOE0999000030", "I-589 APPROVAL, 2027-06-01", 0.9)
    j = journey.journey(d, date(2027, 6, 2), graph=g)
    assert j["stage"] == "asylee" and any("green card (I-485)" in e["what"] and e["date"] == "2028-06-01" for e in j["timeline"])
    assert journey.client_view(j, "es")["title"] == "Asilo concedido"
