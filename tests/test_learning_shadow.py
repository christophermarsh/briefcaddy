"""Shadow mode (src/learning): a local decision model's answer recorded next
to the rules' -- never acted on, never able to stop a client."""

import json
from types import SimpleNamespace

import pytest

from learning import decision, report, shadow
from learning.store import connect
import schema_path


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_SHADOW", "1")
    path = tmp_path / "learning.json"
    path.write_text(json.dumps({"shadow": {"document_type": {"enabled": True, "model": "nimble:test", "threshold": 0.9}}}))
    return path


DOCS = [("passport.pdf", "PASSAPORTE ..."), ("stamps.pdf", "ENTRY STAMP 2019"), ("q.pdf", "Questionário"), ("blank.pdf", "  "),
        ("ssn.pdf", "SOCIAL SECURITY 123-45-6789"), ("i360.pdf", "NOTICE OF ACTION")]
RULES = {"passport.pdf": "passport", "stamps.pdf": "unclassified", "q.pdf": "intake_questionnaire", "blank.pdf": "unclassified",
         "ssn.pdf": "ssn_card", "i360.pdf": "i360_approval"}
ANSWERS = {"PASSAPORTE ...": ("passport", 0.99), "ENTRY STAMP 2019": ("passport", 0.95), "SOCIAL SECURITY 123-45-6789": ("work_permit", 0.97),
           "NOTICE OF ACTION": ("uscis_notice", 0.6)}


def test_off_unless_enabled_and_never_in_tests_by_default(tmp_path):
    assert shadow.observe_document_types("c1", DOCS, {}, db_path=tmp_path / "l.db", ask=lambda t: 1 / 0) == 0  # I485_SHADOW=0
    assert not (tmp_path / "l.db").exists()


def test_answers_are_recorded_beside_the_rules_and_the_report_reads_them(tmp_path, settings):
    classes = {k: SimpleNamespace(doc_type=v) for k, v in RULES.items()}
    n = shadow.observe_document_types("c1", DOCS, classes, db_path=tmp_path / "l.db", settings_path=settings,
                                      ask=lambda text: (*ANSWERS[text], 0.4))
    assert n == 4  # the questionnaire and the empty page are not asked about
    rows = connect(tmp_path / "l.db").execute("select doc, rules, answer, probability, mode, wording from model_runs").fetchall()
    assert {r["doc"] for r in rows} == {"passport.pdf", "stamps.pdf", "ssn.pdf", "i360.pdf"}
    assert all(r["mode"] == "shadow" and r["wording"] for r in rows)
    [m] = report.document_type_report(tmp_path / "l.db", threshold=0.9)["models"]
    assert (m["agree"], m["rescued"], m["unsure"], m["disagree"]) == (2, 1, 0, 1)  # an unsure dissent (0.6) counts as agreeing
    assert {x["doc"] for x in m["examples"]} == {"stamps.pdf", "ssn.pdf"} and m["median_ms"] == 400


def test_a_missing_model_costs_one_error_row_and_never_the_client(tmp_path, settings, monkeypatch):
    def down(text):
        raise decision.DecisionModelError("nimble:test: ConnectError")
    n = shadow.observe_document_types("c1", DOCS, {}, db_path=tmp_path / "l.db", settings_path=settings, ask=down)
    assert n == 0
    assert connect(tmp_path / "l.db").execute("select count(*) from model_runs where error is not null").fetchone()[0] == 1

    import batch

    monkeypatch.setattr("learning.shadow.observe_document_types", lambda *a, **k: 1 / 0)
    batch.shadow_document_types("c1", DOCS, {})  # swallowed: processing carries on


def test_the_decision_model_answer_is_read_from_its_probabilities(monkeypatch):
    sent = {}

    def fake_post(url, payload, timeout):
        sent.update(url=url, payload=payload)
        return {"answers": {"kind": {"type": "choice", "choice": "ssn_card", "probabilities": {"ssn_card": 0.97, "other": 0.03}, "confidence": 0.9}}}
    monkeypatch.setattr("vision.ollama._post_http", fake_post)
    kind, p, _ = decision.document_type("SOCIAL SECURITY" + "x" * 9000, "nimble:test", max_chars=100)
    assert (kind, p) == ("ssn_card", 0.97)
    assert sent["url"].endswith("/v1/systemone") and len(sent["payload"]["state"]["document_text"]) == 100
    assert set(sent["payload"]["questions"]["kind"]["criteria"]) == set(decision.DOCUMENT_TYPES)
    assert shadow.wording_version(decision.DOCUMENT_TYPES) != shadow.wording_version(decision.DOCUMENT_TYPES | {"other": "Anything else"})


# --- labels: what documents really are, from people's own work ---------------------------

from learning import labels  # noqa: E402

SCHEMA = {"exhibits": [{"id": "admission", "types": ["i94"]}, {"id": "passport", "types": ["passport", "visa"]}]}


def _bundle(tmp_path, moved=None):
    d = tmp_path / "c1"
    d.mkdir(exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"classifications": {"a.pdf": "i94", "b.pdf": "unclassified", "q.pdf": "intake_questionnaire",
                                                                 "c.pdf": "ssn_card"}}))
    if moved:
        (d / "packet_choices.json").write_text(json.dumps({"files": moved}))
    return d


def test_labels_come_from_filing_and_packet_moves(tmp_path):
    db = tmp_path / "l.db"
    d = _bundle(tmp_path, moved={"b.pdf": "passport"})
    assert labels.record_packet_move(d, "b.pdf", "passport", "Jane", SCHEMA, db_path=db)  # a two-type exhibit: "one of these"
    assert not labels.record_packet_move(d, "c.pdf", "leave_out", "Jane", SCHEMA, db_path=db)  # leaving out says nothing
    assert labels.record_confirmed(d, "filed", "Sam", db_path=db) == 2  # a.pdf and c.pdf; not the moved, unplaced or questionnaire
    truth = labels.current(connect(db))
    assert truth == {("c1", "b.pdf"): ["passport", "visa"], ("c1", "a.pdf"): ["i94"], ("c1", "c.pdf"): ["ssn_card"]}
    labels.record_packet_move(d, "a.pdf", "passport", "Jane", SCHEMA, db_path=db)  # a later label replaces the earlier one
    assert labels.current(connect(db))[("c1", "a.pdf")] == ["passport", "visa"]


def test_the_report_scores_against_labels_and_suggests_a_threshold_only_with_evidence(tmp_path, settings):
    db = tmp_path / "l.db"
    d = _bundle(tmp_path)
    labels.record_confirmed(d, "filed", "Sam", db_path=db)
    classes = {"a.pdf": SimpleNamespace(doc_type="i94"), "c.pdf": SimpleNamespace(doc_type="ssn_card")}
    answers = {"I-94 record": ("i94", 0.98), "SSA card": ("work_permit", 0.93)}
    shadow.observe_document_types("c1", [("a.pdf", "I-94 record"), ("c.pdf", "SSA card")], classes, db_path=db, settings_path=settings,
                                  ask=lambda t: (*answers[t], 0.5))
    [m] = report.document_type_report(db)["models"]
    assert (m["labelled"], m["model_right"], m["rules_right"]) == (2, 1, 2)
    assert m["suggested_threshold"] is None  # two labels are not evidence
    assert report.suggest_threshold([0.97] * 25, [0.93]) == {"threshold": 0.94, "would_place": 25, "of": 26, "wrong_answers_seen": 1}
    assert report.suggest_threshold([0.97] * 25, [0.995])["threshold"] is None  # wrong while 99% sure: fix the wording first


def test_the_review_app_records_labels_when_a_case_is_filed(tmp_path):
    from pathlib import Path

    from review.server import ReviewApp
    from test_review import TEMPLATE, _REPO, client as make_client  # noqa: F401

    bundle = make_client.__wrapped__(tmp_path)
    app = ReviewApp(bundle.parent, schema_path.path("field_map", "i485"), TEMPLATE, None)
    app.set_filed(bundle.name, {"reviewer": "Sam", "filing": "i485", "mailed_on": "2026-01-05", "carrier": "USPS",
                                "tracking": "9400100000000000000000", "override": "test: no packet built"})
    truth = labels.current(connect(Path(bundle.parent.parent) / "learning.db"))
    assert truth[(bundle.name, "i94.pdf")] == ["i94"] and (bundle.name, "q.pdf") not in truth
