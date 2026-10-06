"""An SIJ client in removal proceedings: the I-360 stays with USCIS; the I-485
is the judge's until the case is terminated (8 CFR 1245.2(a)(1)(i);
1003.18(c), (d)). Every client value is CONSTRUCTED.
"""

import json
from datetime import date

import journey
from factgraph import FactGraph

TODAY = date(2026, 10, 1)


def _case(tmp_path, name="case", **facts):
    d = tmp_path / name
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"classifications": {"nta.pdf": "notice_to_appear"}}), encoding="utf-8")
    g = FactGraph("c")
    for key, value in ({"applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA", "applicant.dob": "2009-03-14",
                        "applicant.i360_receipt_number": "IOE0999000001"} | {k.replace("__", "."): v for k, v in facts.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    g.save(d / "fact_graph.json")
    return d, g


def test_the_i360_pending_in_court_points_to_termination_or_closure(tmp_path, monkeypatch):
    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})
    d, g = _case(tmp_path)
    g.add_source("folder.uscis_case.IOE0999000001.receipt_20260801", "r.pdf", "uscis_notice", "IOE0999000001", "I-360 RECEIPT, 2026-08-01", 0.9)
    j = journey.journey(d, TODAY, graph=g)
    step = next(s for s in j["steps"] if s["id"] == "sij_court")
    assert "1003.18(d)(1)(ii)(B)" in step["text"] and "administratively close" in step["text"] and step["urgent"]


def test_the_i485_packet_waits_for_the_judge(tmp_path, monkeypatch):
    d, g = _case(tmp_path)
    assert journey.i485_court_problem(d, g)[0].startswith("The client is in removal proceedings")
    journey.mark(d, "hearing", "Paula", value={"date": "2026-09-01", "kind": "Master calendar"})
    h = journey.journey(d, TODAY, graph=g)["hearings"][0]["id"]
    journey.mark(d, "hearing_result", "Paula", item=h, value={"outcome": journey.TERMINATED, "decision_date": "2026-09-01"})
    assert journey.i485_court_problem(d, g) == []                                   # terminated: USCIS decides the I-485
    d2, g2 = _case(tmp_path, "arriving", applicant__arriving_alien="Yes")
    assert journey.i485_court_problem(d2, g2) == []                                 # an arriving alien: USCIS
