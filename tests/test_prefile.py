"""Ready to mail? (src/prefile.py) -- the checks before a packet leaves the
office, and the record of the mailing -- and one person's work list across
every case (src/review/overview.py, my_work).

The forms are the real blank I-485, filled here with a CONSTRUCTED client
(made-up name, A-Number, date of birth); the live-check results are
written by each test, as the nightly run would.
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

import journey
import prefile
from factgraph import FactGraph
from fill import fill_pdf, load_field_map
from fill.field_map import map_facts_to_fields
from review.overview import my_work
from review.state import save_bundle
from synthetic_documents import process_retained_documents
from extract.base import ExtractedField
import documents
import subject_attribution

import schema_path

PERSON = {"applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA", "applicant.a_number": "A099999999", "applicant.dob": "2006-03-14"}
LIVE_OK = {"form_i485": {"ok": True, "ours": "09/18/26", "uscis": "09/18/26"}, "fee_schedule": {"ok": True}, "lockbox_chart": {"ok": True}}


def _case(tmp_path, filled_name="EXEMPLO", built_offset=timedelta(seconds=5), live=LIVE_OK, live_age_days=0, draft=False):
    d = tmp_path / "c1"
    d.mkdir()
    # These four fields were always deliberately supplied by this fixture.
    # Retain their fictional source and explicitly attribute its holder; this
    # is not a claim that the narrow passport reader extracts these fields.
    labels = {"applicant.family_name": "Family name", "applicant.given_name": "Given name",
              "applicant.a_number": "A-Number", "applicant.dob": "Date of birth"}
    text = "PASSPORT\nREPUBLICA FEDERATIVA DO BRASIL\nP<BRAEXEMPLO<<ANA" + "<" * 30 + "\n" + "\n".join(f"{labels[key]}: {value}" for key, value in PERSON.items())
    source = tmp_path / "source"
    result = process_retained_documents(d.name, source, [], manual_evidence=[("passport.pdf", text)])
    part = result.boundary_plans["passport.pdf"]["instances"][0]
    assert part["type"] == "passport" and part["state"] == "supported" and part["first"] == part["last"] == 0
    for graph in (result.graph, result.raw_graph):
        for key, value in PERSON.items():
            field = ExtractedField(key, f"{labels[key]}: {value}", value, 0.98, page=0)
            graph.add_source(key, "passport.pdf", "passport", field.raw_value, value, field.confidence,
                             page=0, **subject_attribution.provenance(part, "passport", field))
    save_bundle(result, d, source)
    row = subject_attribution.views(d)[0]
    person = documents.read(d)["case_subjects"]["people"][0]["id"]
    subject_attribution.assign(d, row["instance_id"], row["fingerprint"], {"holder": person},
                               "Fictional Source Reviewer", "paralegal")
    assert subject_attribution.views(d)[0]["bound"] and subject_attribution.views(d)[0]["current"]
    meta = json.loads((d / "meta.json").read_text())
    meta["classifications"]["order.pdf"] = "sij_order"  # preserve the original mailing fixture's case context
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    # the I-485 as filled (a different name stands for a form filled before a correction)
    fmap = load_field_map(schema_path.path("field_map", "i485"))
    shown = FactGraph("c1")
    for key, value in (PERSON | {"applicant.family_name": filled_name}).items():
        shown.add_source(key, "passport.pdf", "passport", value, value, 0.98)
    fill_pdf(schema_path.path("template", "i485"), map_facts_to_fields(shown, fmap).values, d / "i485_filled.pdf")
    built = datetime.now(timezone.utc) + built_offset
    (d / "packet.json").write_text(json.dumps({
        "built_at": built.isoformat(), "built_by": "Paralegal", "draft": draft, "problems": ["Missing: Proof of age."] if draft else [],
        "forms": [{"id": "i485", "short": "I-485", "file": "i485_filled.pdf"}], "sha256": "ab" * 32,
        "signatures": [{"form": "I-485", "who": "client", "what": "Part 10", "page": 21}, {"form": "I-485", "who": "attorney", "what": "Part 12", "page": 23}],
        "mail_to": ["USCIS", "ATTN: AOS", "P.O. BOX 805887", "CHICAGO, IL 60680"], "fee": "No fee (SIJ)."}), encoding="utf-8")
    at = (datetime.now(timezone.utc) - timedelta(days=live_age_days)).isoformat()
    (tmp_path / "live.json").write_text(json.dumps({"at": at, "results": live}), encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def live(monkeypatch, tmp_path):
    real = prefile.check
    monkeypatch.setattr(prefile, "check", lambda d, filing, today=None, live_path=None: real(d, filing, today, tmp_path / "live.json"))


def _levels(result):
    return {c["id"]: c["level"] for c in result["checks"]}


def test_a_correct_current_packet_is_ready_to_mail(tmp_path):
    r = prefile.check(_case(tmp_path), "i485")
    levels = _levels(r)
    assert r["ready"], r["checks"]
    assert levels["fresh"] == levels["draft"] == levels["edition_i485"] == levels["identity"] == "pass"
    assert "P.O. BOX 805887" in next(c["text"] for c in r["checks"] if c["id"] == "lockbox_chart")
    assert "Client: page 21 (I-485). Attorney: page 23 (I-485)." in next(c["text"] for c in r["checks"] if c["id"] == "signatures")


def test_what_stops_a_packet(tmp_path):
    d = _case(tmp_path, filled_name="EXEMPLA", built_offset=timedelta(minutes=-5), live=LIVE_OK | {"form_i485": {"ok": False, "ours": "09/18/26", "uscis": "12/01/26"}},
              live_age_days=10, draft=True)
    r = prefile.check(d, "i485")
    levels = _levels(r)
    assert not r["ready"]
    assert levels["fresh"] == "fail"           # the case files changed after the build
    assert levels["draft"] == "fail"
    assert levels["edition_i485"] == "fail" and "12/01/26" in next(c["text"] for c in r["checks"] if c["id"] == "edition_i485")
    assert levels["live"] == "warn"            # the sources weren't checked this week
    identity = next(c for c in r["checks"] if c["id"] == "identity")
    assert identity["level"] == "fail" and "EXEMPLA" in identity["text"] and "EXEMPLO" in identity["text"]


def test_the_mailing_is_recorded_and_the_timeline_reads_it(tmp_path, monkeypatch):
    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})
    d = _case(tmp_path, filled_name="EXEMPLA", draft=True)
    with pytest.raises(ValueError, match="Not ready to mail"):
        prefile.record_filing(d, "i485", date.today().isoformat(), "USPS", "9400100000000000000000", "Attorney")
    with pytest.raises(PermissionError):
        prefile.record_filing(d, "i485", date.today().isoformat(), "USPS", "9400", "Paralegal", override="x", role="paralegal")
    with pytest.raises(ValueError, match="tracking number"):
        prefile.record_filing(d, "i485", date.today().isoformat(), "USPS", "", "Attorney", override="client turns 21 tomorrow")
    rec = prefile.record_filing(d, "i485", date.today().isoformat(), "USPS", "9400100000000000000000", "Attorney", override="client turns 21 tomorrow")
    assert rec["failed_checks"] and rec["override"] == "client turns 21 tomorrow" and rec["mail_to"][2] == "P.O. BOX 805887"
    j = journey.journey(d, date.today())
    mailed = next(e for e in j["timeline"] if e["kind"] == "filed")
    assert mailed["what"].startswith("Mailed:") and "USPS 9400100000000000000000" in mailed["what"] and "Mailed despite" in mailed["what"]
    # the client sees the mailing in their own words -- never the override or the tracking details
    happened = journey.client_view(j, "pt")["happened"]
    assert happened[0]["text"] == f"Enviamos o seu pedido (Formulário I-485) ao USCIS em {date.today().strftime('%d/%m/%Y')}."
    # 50 days later, no receipt: the paralegal checks the tracking first
    later = journey.journey(d, date.today() + timedelta(days=50))
    assert any(s["id"] == "no_receipt" and "9400100000000000000000" in s["text"] for s in later["steps"])
    prefile.undo_filing(d, "Attorney")
    assert prefile.filings(d) == [] and "filed_at" not in json.loads((d / "status.json").read_text())


def test_one_list_per_person_most_pressing_first():
    today = date.today()
    rows = [
        {"id": "a", "summary": {"name": "ANA"}, "fix": 2, "check": 1, "attorney": 0, "blocking": 0,
         "journey": {"stage_name": "I-360 filed", "deadlines": [{"id": "rfe", "date": (today + timedelta(days=5)).isoformat(), "what": "Answer the RFE", "owner": "attorney"}],
                     "steps": [{"id": "x", "owner": "paralegal", "text": "Add the receipt", "urgent": False}]}},
        {"id": "b", "summary": {"name": "BEA"}, "fix": 0, "check": 0, "attorney": 3, "blocking": 1,
         "journey": {"stage_name": "Gathering the case", "deadlines": [{"id": "appt", "date": (today + timedelta(days=2)).isoformat(), "what": "Biometrics", "owner": "client"}],
                     "steps": [{"id": "y", "owner": "paralegal", "text": "No date of birth", "urgent": True}]}},
    ]
    w = my_work(rows, "paralegal")
    assert [s["id"] for s in w["steps"]] == ["y", "x"]                  # urgent first
    assert [d["what"] for d in w["deadlines"]] == ["Biometrics"]        # the client's appointment: the paralegal confirms it
    assert [r["client"] for r in w["review"]] == ["a"]
    w = my_work(rows, "attorney")
    assert [d["what"] for d in w["deadlines"]] == ["Answer the RFE"] and w["review"][0]["blocking"] == 1 and w["steps"] == []
