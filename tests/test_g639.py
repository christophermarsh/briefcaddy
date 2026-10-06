"""The FOIA request for the client's USCIS file (src/g639.py): filed online at first.uscis.gov since 01/22/2026 (USCIS's FOIA page,
updated 01/27/2026), no fee with the request (G-1055 10/01/26; the form's Part 3), the firm as the third-party requestor with the
client's consent, and the filled form. Every client value is CONSTRUCTED.
"""

import re
from datetime import date

from pypdf import PdfReader

import enotice
import g639
import journey
import packet
from factgraph import FactGraph
from fill.companion import fill_companions, load_profile

TODAY = date(2026, 10, 2)


def _graph(**extra):
    g = FactGraph("c")
    for key, value in ({"applicant.family_name": "EXEMPLO SOUZA", "applicant.given_name": "ANA CLARA", "applicant.dob": "1990-03-14",
                        "applicant.country_of_birth": "BRAZIL", "applicant.a_number": "A099000123", "applicant.physical_street": "10 EXAMPLE ST",
                        "applicant.physical_city": "SOMERVILLE", "applicant.physical_state": "MA", "applicant.physical_zip": "02143",
                        "firm.preparer_family_name": "EXEMPLO", "firm.preparer_given_name": "MARIA", "firm.street": "1 FIRM ST", "firm.city": "Boston",
                        "firm.state": "MA", "firm.zip": "02110", "firm.phone": "6175550100", "firm.email": "office@example.test"}
                       | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _notice(g, receipt, form, kind, when):
    g.add_source(f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)


def _boxes(path):
    return {re.split(r"(?<!\\)\.", n)[-1]: x.get("/V") for n, x in PdfReader(str(path)).get_fields().items()}


def test_the_request_fills_itself_from_the_case():
    g = _graph(g639__scope=g639.ALL)
    _notice(g, "IOE0999000111", "I-485", "receipt", "2025-05-01")
    _notice(g, "IOE0999000222", "I-765", "receipt", "2025-05-02")
    g639.derive(g, TODAY)
    v = lambda k: g.get(k).value  # noqa: E731
    assert v("g639.type") == g639.TYPE and v("g639.relationship") == g639.RELATIONSHIP and v("g639.scope_text") == "Complete A-File (all documents)"
    assert (v("g639.receipt_1"), v("g639.receipt_2")) == ("IOE0999000222", "IOE0999000111") and g.get("g639.receipt_3") is None   # newest first
    assert v("g639.mailing_city") == "SOMERVILLE" and v("g639.mailing_country") == "USA"


def test_online_only_no_fee_and_what_it_says(tmp_path):
    g = _graph(g639__scope=g639.ALL, g639__court_hearing="Yes")
    g639.derive(g, TODAY)
    assert g639.fee(g, TODAY)[0] == 0
    text = " ".join(n["text"] for n in g639.notes(g, TODAY))
    assert "first.uscis.gov" in text and "01/22/2026" in text and "gives no mailing address" in text and "up to $25" in text
    assert "CBP" in text and "Notice to Appear" in text
    assert "g639" not in enotice.LOCKBOX_FILINGS   # not mailed to a lockbox: no G-1145
    schema = packet.load_filing("g639")
    assert schema["cover_letter"] is False and schema["forms"] == ["g639"] and schema["g28"] is False
    assert any("first.uscis.gov" in h["text"] for h in schema["handwork"])


def test_a_typed_in_request_is_never_ready_to_mail_and_more_questions_wait_only_for_the_scope(tmp_path):
    import json

    import prefile

    _graph().save(tmp_path / "fact_graph.json")
    (tmp_path / "packet_g639.json").write_text(json.dumps({"built_at": "2026-10-02T10:00:00+00:00", "built_by": "Jane", "forms": [], "draft": False, "problems": []}), encoding="utf-8")
    ready = next(c for c in prefile.check(tmp_path, "g639", TODAY)["checks"] if c["id"] == "draft")
    assert ready["title"] == "Ready to enter online" and "mail" not in ready["title"]
    assert g639.more_to_come(_graph()) is True and g639.more_to_come(_graph(g639__scope=g639.ALL)) is False


def test_what_stops_the_request(tmp_path):
    out = lambda g: " ".join(g639.problems(tmp_path, g, TODAY))  # noqa: E731
    assert "none is chosen" in out(_graph(g639__scope=g639.SPECIFIC))
    assert "none is chosen" not in out(_graph(g639__scope=g639.SPECIFIC, g639__doc_i485="Yes"))
    assert "Notice to Appear" in out(_graph(g639__court_hearing="Yes"))
    nobody = _graph(applicant__a_number="")
    g639.derive(nobody, TODAY)
    assert "No A-Number and no receipt number" in out(nobody)
    assert "the attorney's name isn't set" in out(_graph(firm__preparer_family_name=""))


def test_the_filled_form(tmp_path):
    g = _graph(g639__scope=g639.SPECIFIC, g639__doc_i485="Yes", g639__doc_n400="Yes", g639__doc_apprehensions="Yes", g639__doc_apprehensions_date="2019-02-03",
               g639__court_hearing="No", g639__consent_to_pay="Yes", applicant__mother_family_name="EXEMPLO", applicant__mother_given_name="MARIA")
    _notice(g, "IOE0999000111", "I-485", "receipt", "2025-05-01")
    g639.derive(g, TODAY)
    profile = load_profile()
    profile["forms"] = {"g639": profile["forms"]["g639"]}
    done = fill_companions(g, tmp_path, profile)
    f = _boxes(tmp_path / "g639_filled.pdf")
    assert f["Pt1Line1B_Checkbox[0]"] == "/B" and f["Pt1Line2_CheckboxJ[0]"] == "/J" and f["Pt1Line2_CheckboxL[0]"] == "/L"   # 1.B, and I-485 and N-400
    assert f["Pt1Line2A_RequestorDateSigned[0]"] == "02/03/2019" and f["Pt3_RequestorCheckbox[0]"] == "/ 1"
    assert f["Pt2Line1A_ANumber[0]"] == f["Pt2Line1A_ANumber[1]"] == "099000123" and f["Pt2Line4A_ReceiptNumber[0]"] == "IOE0999000111"
    assert f["Pt2Line8_Unit[0]"] in (None, "/Off") and f["Pt2Line8_CityOrTown[0]"] == "SOMERVILLE"
    assert f["Pt4Line1_FamilyName[0]"] == "EXEMPLO" and f["Pt4Line2_StreetNumberName[0]"] == "1 FIRM ST" and f["Pt4Line3_CheckboxA[0]"] == "/ 1"
    assert f["P4_Option1_Checkbox1[0]"] == "/8a" and f["Pt2Line10_GivenName[0]"] == "MARIA"
    assert not [k for k in done["g639"]["left_blank"] if k in ("applicant.dob", "applicant.family_name", "g639.mailing_street")]


def test_the_takeover_step_says_where_the_request_is_built(tmp_path):
    g = _graph()
    _notice(g, "IOE0999000111", "I-485", "receipt", "2025-05-01")
    j = journey.journey(tmp_path, TODAY, graph=g)
    foia = next(t for t in j["takeover"] if t["id"] == "takeover.foia")
    assert "first.uscis.gov" in foia["text"] and "(G-639)" in foia["text"]
