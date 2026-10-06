"""Asylum in immigration court: the I-589 filed with the court (DHS's instructions
for applications in immigration court, revised June 8, 2026) and a case the
asylum office refers to the judge (8 CFR 208.14(c)(1)). Every client value is
CONSTRUCTED.
"""

import json
from datetime import date

import packet
from factgraph import FactGraph

TODAY = date(2026, 10, 1)


def _case(tmp_path, **facts):
    d = tmp_path / "case"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"classifications": {}}), encoding="utf-8")
    g = FactGraph("c")
    for key, value in ({"applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA", "applicant.dob": "1996-03-14"}
                       | {k.replace("__", "."): v for k, v in facts.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    g.save(d / "fact_graph.json")
    return d, g


def _notice(g, receipt, form, kind, when):
    g.add_source(f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)


def test_in_court_the_i589_goes_to_the_judge_not_uscis(tmp_path):
    d, _ = _case(tmp_path, applicant__nta_present="Yes", asylum__court="now")
    schema = packet.for_case(packet.load_filing("i589"), d)
    assert schema["cover_letter"] is False and schema["forms"] == ["i589"] and "immigration court" in schema["title"]
    hand = " ".join(h["text"] for h in schema["handwork"])
    assert "EOIR Payment Portal" in hand and "Nothing is sent to USCIS" in hand and "800-375-5283" in hand and "EOIR-33" in hand
    assert "variants" not in schema and schema["variant"] == "in_court"


def test_with_uscis_the_packet_is_unchanged(tmp_path):
    d, _ = _case(tmp_path, asylum__court="never")
    schema = packet.for_case(packet.load_filing("i589"), d)
    assert schema["cover_letter"] == "cover_letter_i589.json" and "g28_i589" in schema["forms"]


def test_an_unaccompanied_child_files_with_uscis_even_in_court(tmp_path):
    d, _ = _case(tmp_path, applicant__nta_present="Yes", asylum__court="now", asylum__uac="Yes")
    assert packet.for_case(packet.load_filing("i589"), d)["cover_letter"] == "cover_letter_i589.json"


def test_a_notice_to_appear_after_the_i589_is_a_referral(tmp_path, monkeypatch):
    import journey

    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})
    d, g = _case(tmp_path, applicant__nta_present="Yes", asylum__court="never")
    _notice(g, "ZLA2690000601", "I-589", "receipt", "2026-05-01")
    j = journey.journey(d, TODAY, graph=g)
    assert j["stage"] == "asylum_pending"
    referral = next(s for s in j["steps"] if s["id"] == "asylum_referral")
    assert "8 CFR 208.14(c)(1)" in referral["text"] and referral["urgent"]
    assert j["next_filings"][0]["filing"] == "eoir28"                         # the appearance comes first


def test_asylum_problems_no_longer_block_a_court_filing(tmp_path):
    import asylum

    d, g = _case(tmp_path, applicant__nta_present="Yes", asylum__court="now")
    assert asylum.where_to_file(g)["with"] == "court"
    assert not any("filed with the immigration court that has the case" in p for p in asylum.problems(d, TODAY))
