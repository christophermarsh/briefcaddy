"""Learning from corrections (src/review/learning.py)."""

import json

from review.learning import learning, outcomes


def _fact(value, doc_type="intake_questionnaire", derived_by=None, status="resolved"):
    return {"status": status, "value": value, "derived_by": derived_by,
            "sources": [{"doc_type": doc_type, "doc_id": "x.pdf"}] if doc_type else []}


def _decision(action, facts, values=None, kind="fact"):
    return {"action": action, "values": values or {}, "reviewer": "Jane", "at": "2026-10-01T12:00:00+00:00",
            "item": {"id": "i", "kind": kind, "level": "review", "title": "t", "group": "g", "facts": facts}}


def _bundle(root, name, facts, decisions):
    d = root / name
    d.mkdir(parents=True)
    (d / "fact_graph.json").write_text(json.dumps({"client_id": name, "facts": facts}))
    (d / "decisions.json").write_text(json.dumps(decisions))
    return d


def test_each_decision_says_whether_the_pipeline_was_right(tmp_path):
    d = _bundle(tmp_path, "c1", {
        "applicant.height": _fact("5'3\""),
        "applicant.weight_lbs": _fact("118"),
        "applicant.eye_color": _fact("BROWN"),
        "applicant.employer": _fact("SAMPLE CO"),
        "applicant.city": _fact(None, status="missing"),
        "applicant.part9.x": _fact("No", doc_type=None, derived_by="POLICY:NA-X"),
    }, {
        "fact:applicant.height": _decision("confirm", ["applicant.height"]),
        "fact:applicant.weight_lbs": _decision("set", ["applicant.weight_lbs"], {"applicant.weight_lbs": "128"}),
        "fact:applicant.eye_color": _decision("set", ["applicant.eye_color"], {"applicant.eye_color": "brown"}),  # same, retyped
        "fact:applicant.employer": _decision("blank", ["applicant.employer"]),
        "missing:applicant.city": _decision("set", ["applicant.city"], {"applicant.city": "SAMPLE CITY"}, kind="missing"),
        "fact:applicant.part9.x": _decision("confirm", ["applicant.part9.x"]),
        "alert:applicant.nta": _decision("acknowledge", ["applicant.nta"], kind="alert"),
    })
    got = {r["key"]: r["outcome"] for r in outcomes(d)}
    assert got == {"applicant.height": "confirmed", "applicant.weight_lbs": "corrected", "applicant.eye_color": "confirmed",
                   "applicant.employer": "blanked", "applicant.city": "filled", "applicant.part9.x": "confirmed"}
    sources = {r["key"]: r["source"] for r in outcomes(d)}
    assert sources["applicant.part9.x"] == "Firm policy NA-X" and sources["applicant.height"] == "Paper questionnaire (scan)"
    assert sources["applicant.city"] == "Nothing (left empty)"


def test_the_most_corrected_fields_come_first_across_clients(tmp_path):
    for n, value in enumerate(("118", "120")):
        _bundle(tmp_path, f"c{n}", {"applicant.weight_lbs": _fact(value), "applicant.height": _fact("5'3\"")}, {
            "fact:applicant.weight_lbs": _decision("set", ["applicant.weight_lbs"], {"applicant.weight_lbs": "150"}),
            "fact:applicant.height": _decision("confirm", ["applicant.height"]),
        })
    (tmp_path / "unprocessed").mkdir()  # no bundle: ignored
    report = learning(tmp_path, label=lambda k: k.upper())
    assert report["clients"] == 2 and report["decisions"] == 4
    top = report["fields"][0]
    assert top["key"] == "applicant.weight_lbs" and top["corrected"] == 2 and top["error_rate"] == 1.0 and top["label"] == "APPLICANT.WEIGHT_LBS"
    assert top["examples"][0] == {"client": "c0", "before": "118", "after": "150", "source": "Paper questionnaire (scan)"}
    scan = next(s for s in report["sources"] if s["source"] == "Paper questionnaire (scan)")
    assert scan["error_rate"] == 0.5 and report["overall"]["corrected"] == 2


def test_reader_accuracy_and_the_decision_log_call_the_same_decision_corrected():
    """The buyer saw "filled in by hand" in Reader accuracy for a decision the Decision log calls "Corrected"."""
    from pathlib import Path

    from review.learning import ACCURACY_METHOD

    assert any(line.startswith("Corrected (box was empty)") for line in ACCURACY_METHOD)
    assert not any("by hand" in line.lower() for line in ACCURACY_METHOD)
    page = (Path(__file__).resolve().parent.parent / "src" / "review" / "static" / "index.html").read_text(encoding="utf-8")
    assert "filled in by hand" not in page.lower() and "filled by hand" not in page.lower()
    assert 'set: ["check", "Corrected"]' in page  # the Decision log's word for a value a person entered or changed
