"""The accuracy record (review/learning.py accuracy, /api/accuracy): counts from a made-up
log of review decisions, per source and per form item, over a period. Never an estimate."""

import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from review.learning import accuracy
import schema_path

_REPO = Path(__file__).resolve().parent.parent


def _fact(value, doc_type="passport", derived_by=None):
    return {"status": "resolved", "value": value, "derived_by": derived_by, "sources": [{"doc_type": doc_type, "doc_id": "x.pdf"}] if doc_type else []}


def _decision(action, key, at, values=None, **extra):
    return {"action": action, "values": values or {}, "reviewer": "Jane", "at": at, **extra,
            "item": {"id": "fact:" + key, "kind": "fact", "level": "review", "title": "t", "group": "g", "facts": [key]}}


def _client(root: Path, name: str, facts: dict, decisions: dict) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "fact_graph.json").write_text(json.dumps({"client_id": name, "facts": facts}))
    (d / "decisions.json").write_text(json.dumps(decisions))


@pytest.fixture
def log(tmp_path):
    """Two made-up clients: September and October decisions, one undone, one alert, one box confirmed empty."""
    _client(tmp_path, "c1", {
        "applicant.family_name": _fact("EXEMPLO"), "applicant.dob": _fact("2005-01-02"), "applicant.height": _fact("5'3\"", "drivers_license"),
        "applicant.weight_lbs": _fact("118", "intake_questionnaire"), "applicant.city": {"status": "missing", "value": None, "sources": []},
        "applicant.part9.x": _fact("No", None, "POLICY:PART9-DEFAULT-NO"),
    }, {
        "fact:applicant.family_name": _decision("confirm", "applicant.family_name", "2026-09-10T10:00:00+00:00"),
        "fact:applicant.dob": _decision("set", "applicant.dob", "2026-10-01T10:00:00+00:00", {"applicant.dob": "2005-02-01"}),
        "fact:applicant.height": _decision("blank", "applicant.height", "2026-10-01T11:00:00+00:00"),
        "fact:applicant.weight_lbs": _decision("set", "applicant.weight_lbs", "2026-10-02T09:00:00+00:00", {"applicant.weight_lbs": "120"},
                                               undone={"by": "Ana", "at": "2026-10-02T10:00:00+00:00"}),  # taken back: not counted
        "missing:applicant.city": _decision("set", "applicant.city", "2026-10-01T12:00:00+00:00", {"applicant.city": "SAMPLE CITY"}),
        "fact:applicant.part9.x": _decision("confirm", "applicant.part9.x", "2026-10-01T13:00:00+00:00"),
        "alert:applicant.nta": _decision("acknowledge", "applicant.nta", "2026-10-01T14:00:00+00:00"),
    })
    _client(tmp_path, "c2", {"applicant.family_name": _fact("SOUZA"), "applicant.dob": _fact("2004-03-04")}, {
        "fact:applicant.family_name": _decision("set", "applicant.family_name", "2026-10-01T10:00:00+00:00", {"applicant.family_name": "SOUZA"}),
        "fact:applicant.dob": _decision("confirm", "applicant.dob", "2026-10-02T10:00:00+00:00"),
    })
    (tmp_path / "not-processed").mkdir()
    return tmp_path


def test_totals_match_the_log(log):
    r = accuracy(log, label=lambda k: k.upper(), ref=lambda k: "Part 1")
    # c1: name confirmed, dob corrected, height left blank, city by hand, policy answer confirmed; c2: name retyped (confirmed), dob confirmed
    assert r["totals"] == {"confirmed": 4, "corrected": 1, "blanked": 1, "filled": 1, "reviewed": 7}
    assert r["clients"] == 2
    items = {g["key"]: g for g in r["by_item"]}
    assert items["applicant.dob"] == {"key": "applicant.dob", "label": "APPLICANT.DOB", "ref": "Part 1", "confirmed": 1, "corrected": 1,
                                           "blanked": 0, "filled": 0, "reviewed": 2}
    assert "applicant.weight_lbs" not in items and "applicant.nta" not in items  # undone; an alert
    sources = {g["label"]: g for g in r["by_source"]}
    assert sources["Passport reader"]["confirmed"] == 3 and sources["Passport reader"]["corrected"] == 1
    assert sources["Driver's license reader"]["blanked"] == 1 and sources["Firm policy PART9-DEFAULT-NO"]["confirmed"] == 1
    assert sources["Nothing (left empty)"]["filled"] == 1
    assert r["by_item"][0]["corrected"] + r["by_item"][0]["blanked"] == 1  # the items reviewers changed come first


def test_a_period_counts_only_its_decisions_and_says_no_data_when_empty(log):
    september = accuracy(log, "2026-09-01", "2026-09-30")
    assert september["totals"] == {"confirmed": 1, "corrected": 0, "blanked": 0, "filled": 0, "reviewed": 1} and september["clients"] == 1
    one_day = accuracy(log, "2026-10-02", "2026-10-02")
    assert one_day["totals"]["confirmed"] == 1 and one_day["totals"]["reviewed"] == 1  # the undone correction that day is not counted
    empty = accuracy(log, "2025-01-01", "2025-12-31")
    assert empty["totals"]["reviewed"] == 0 and empty["by_item"] == [] and empty["by_source"] == [] and empty["first"] is None
    assert any("Nothing is estimated" in m for m in empty["method"]) and empty["reference"].startswith("No data")
    with pytest.raises(ValueError):
        accuracy(log, "10/01/2026", None)
    with pytest.raises(ValueError):
        accuracy(log, "2026-10-02", "2026-10-01")


def test_the_accuracy_page_is_the_attorneys_and_exports_as_pdf(log):
    from review import bundle
    from review.server import ReviewApp

    app = ReviewApp(log, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None)
    with pytest.raises(PermissionError):
        app.accuracy({}, "paralegal")
    report = app.accuracy({"from": "2026-10-01", "to": "2026-10-31"}, "attorney")
    assert report["totals"]["reviewed"] == 6 and report["by_item"][0]["label"]
    pdf = bundle.accuracy_pdf(report, "Example Immigration Office", "Ana Attorney")
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "Accuracy record: 10/01/2026 to 10/31/2026" in text and "How these numbers are counted" in text
    assert "Example Immigration Office" in text and "INTERNAL REVIEW RECORD, NOT FOR FILING" in text
    empty = bundle.accuracy_pdf(app.accuracy({"from": "2025-01-01", "to": "2025-01-31"}, None), "Example Immigration Office")
    assert "No data" in "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(empty)).pages)
