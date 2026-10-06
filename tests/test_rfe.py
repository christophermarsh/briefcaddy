"""Answering a USCIS request (src/rfe.py): the items, the response packet,
the checks before mailing, and the mailing that closes the request.

CONSTRUCTED case: a made-up client, an RFE notice recorded the way
extract/uscis_notice.py records one, and blank one-page PDFs standing in
for the folder's documents.
"""

import json
from datetime import date, timedelta

import pytest
from pypdf import PdfReader

import journey
import rfe
from factgraph import FactGraph

from test_packet import _client, filled  # noqa: F401 -- the module-scoped blank I-485 fixture

TODAY = date(2026, 10, 1)
KEY = "IOE0999000020_rfe_2026-09-15"
DOCS = {"rfe.pdf": "uscis_notice", "birth.pdf": "birth_certificate", "translation.pdf": "translation_certification", "passport.pdf": "passport"}


@pytest.fixture
def case(tmp_path, filled, monkeypatch):  # noqa: F811
    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})
    d = _client(tmp_path, filled, DOCS)
    g = FactGraph("c")
    for key, value in {"applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA", "applicant.a_number": "A099999999"}.items():
        g.add_source(key, "passport.pdf", "passport", value, value, 0.98)
    slug = "IOE0999000020.rfe_20260915"
    g.add_source(f"folder.uscis_case.{slug}", "rfe.pdf", "uscis_notice", "IOE0999000020", "I-485 REQUEST FOR EVIDENCE, 2026-09-15", 0.9)
    g.add_source(f"folder.notice.{slug}.date", "rfe.pdf", "uscis_notice", "2026-09-15", "2026-09-15", 0.85)
    g.add_source(f"folder.notice.{slug}.due", "rfe.pdf", "uscis_notice", "2026-12-08", "2026-12-08", 0.85)
    g.save(d / "fact_graph.json")
    return d


def test_an_open_request_and_what_its_response_still_needs(case):
    [req] = rfe.requests(case)
    assert (req["key"], req["due"], req["answered"]) == (KEY, "2026-12-08", False)
    p = rfe.plan(case, KEY, TODAY)
    assert p["mail_by"] == "2026-12-01"  # the due date less the firm's 7-day mailing margin
    assert any("Copy each item" in x for x in p["problems"]) and any("address the notice says" in x for x in p["problems"])
    with pytest.raises(ValueError, match="Not in the client's folder"):
        rfe.save(case, KEY, [{"text": "Birth certificate", "docs": ["nothing.pdf"]}], [], None, "Paralegal")


def test_the_response_packet_and_the_mailing_that_closes_the_request(case):
    rfe.save(case, KEY, [{"text": "1. A copy of your birth certificate with a certified English translation.", "docs": ["birth.pdf", "translation.pdf"]},
                         {"text": "Evidence of your continuous physical presence", "docs": [], "note": "The client has lived at one address since 2019; see the lease."}],
             "USCIS\nATTN: RFE RESPONSE\nP.O. BOX 999\nLEE'S SUMMIT, MO 64002", None, "Paralegal")
    p = rfe.plan(case, KEY, TODAY)
    assert p["ready"] and [it["exhibit"] for it in p["items"]] == ["A", None] and p["send_to"][0] == "USCIS"
    m = rfe.build(case, KEY, "Paralegal", TODAY)
    reader = PdfReader(case / "rfe" / f"{KEY}_response.pdf")
    letter = reader.pages[0].extract_text()
    assert "Response to Request for Evidence dated September 15, 2026" in letter and "IOE0999000020" in letter and "Exhibit A" in letter
    # the letter, then the notice on top of the evidence (its sheet + 1 page), then Exhibit A (its sheet + 2 documents)
    texts = [page.extract_text() or "" for page in reader.pages]
    notice_sheet = next(i for i, t in enumerate(texts) if "notice (IOE0999000020" in t)
    exhibit_sheet = next(i for i, t in enumerate(texts) if "Exhibit A" in t and "Item 1" in t)
    assert exhibit_sheet == notice_sheet + 2 and len(texts) == exhibit_sheet + 3 == m["pages"] and not m["draft"]
    check = rfe.check(case, KEY, TODAY)
    assert check["ready"], check["checks"]
    # inside the mailing margin: courier
    late = rfe.check(case, KEY, date(2026, 12, 3))
    assert next(c for c in late["checks"] if c["id"] == "due")["level"] == "warn"
    # a change after the build: rebuild
    rfe.save(case, KEY, rfe.state(case, KEY)["items"], rfe.state(case, KEY)["send_to"], None, "Paralegal")
    assert next(c for c in rfe.check(case, KEY, TODAY)["checks"] if c["id"] == "fresh")["level"] == "fail"
    rfe.build(case, KEY, "Paralegal", TODAY)
    rec = rfe.record(case, KEY, TODAY.isoformat(), "FedEx", "7700 1234 5678", "Attorney", today=TODAY)
    assert rec["filing"] == "rfe" and rec["form"] == "I-485"
    # the request is closed, the case's own filing untouched, and the client hears about it in their words
    assert rfe.requests(case)[0]["answered"]
    j = journey.journey(case, TODAY)
    mark = "IOE0999000020.rfe.2026-09-15"
    assert not any(s["id"] == mark for s in j["steps"]) and not any(d["id"] == mark for d in j["deadlines"])  # off everyone's list
    status = json.loads((case / "status.json").read_text())
    assert status["journey"]["done"][mark]["by"] == "Attorney" and "FedEx" in status["journey"]["done"][mark]["note"]
    assert "filed_at" not in status
    assert journey.client_view(j, "pt")["happened"][0]["text"] == "Enviamos ao USCIS as informações que ele pediu sobre o seu Formulário I-485 em 01/10/2026."


def test_a_response_is_never_recorded_by_a_paralegal_or_without_tracking(case):
    with pytest.raises(PermissionError):
        rfe.record(case, KEY, TODAY.isoformat(), "USPS", "9400", "Paralegal", role="paralegal", today=TODAY)
    with pytest.raises(ValueError, match="tracking"):
        rfe.record(case, KEY, TODAY.isoformat(), "USPS", "", "Attorney", today=TODAY)
    with pytest.raises(ValueError, match="Not ready to mail"):
        rfe.record(case, KEY, TODAY.isoformat(), "USPS", "9400", "Attorney", today=TODAY)  # nothing built yet
    with pytest.raises(ValueError, match="future"):
        rfe.record(case, KEY, (TODAY + timedelta(days=1)).isoformat(), "USPS", "9400", "Attorney", override="x", today=TODAY)
