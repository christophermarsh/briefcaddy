"""The case's own path (brief S3, src/path.py) on the made-up world (tools/make_world.py): a path edited by the paralegal derives nothing until the attorney
approves it; approved, it derives the documents, the questionnaire, the deadlines, the packet order and the client's page from the templates alone; a step
the templates do not know is refused; undo returns the template; a restricted case's path is the one answer a made-up id gets. Everyone is made up."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
import scale_world  # noqa: E402

import schema_path  # noqa: E402


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    w = scale_world.build(tmp_path_factory.mktemp("paths"), 30, staff=2, warm=False)
    w.patch.setenv("I485_ROSTER_BUDGET", "60")  # the tests change a case and look at once: every changed case is read before the answer (src/review/roster.py)
    yield w
    w.stop()


def labels():
    import journey

    return journey.settings()["client"]


def ledger(w) -> list[dict]:
    return [json.loads(x) for p in sorted(w.data.glob("events*.jsonl")) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def plain_sij(w) -> str:
    """An SIJ case, not restricted, with no path of its own yet."""
    import journey

    for case in w.manifest["case_ids"]:
        if case in w.manifest["restricted_ids"] or case in w.manifest["paths"]:
            continue
        if journey.journey(w.clients / case)["track"] == "sij":
            return case
    raise AssertionError("no SIJ case in the world")


def test_the_worlds_two_custom_paths_are_used_and_the_client_reads_them_in_four_languages_in_the_templates_words(world):
    import journey

    w = world
    assert sorted(w.manifest["paths"].values()) == ["court_added", "filing_removed"]
    for case, which in w.manifest["paths"].items():
        j = journey.journey(w.clients / case)
        ids = [x["id"] for x in j["stages"]]
        assert j["own_path"] and ids != j["template_stages"]
        if which == "filing_removed":
            assert "visa_wait" not in ids and "i131" not in [f["filing"] for f in j["next_filings"]]
        else:
            assert ids.index("state_court") == ids.index("family_ready") - 1
        for lang in ("pt", "es", "en", "ht"):
            shown = journey.client_view(j, lang)["path"]
            assert [x["id"] for x in shown] == [x for x in ids if x in labels()]
            assert all(x["name"] == labels()[x["id"]][lang][0] for x in shown), lang  # the template's own words, never typed


def test_a_paralegal_edits_nothing_is_derived_until_the_attorney_approves_and_then_everything_is(world):
    import journey
    import path

    w = world
    case = plain_sij(w)
    d = w.clients / case
    template = journey.journey(d)["template_stages"]
    steps = [{"id": x, "date": "2026-12-01" if x == "i485_ready" else None} for x in template if x != "visa_wait"]
    steps.insert(steps.index(next(s for s in steps if s["id"] == "i360_pending")) + 1, steps.pop(next(i for i, s in enumerate(steps) if s["id"] == "state_court")))
    _, status, out = w.call("/api/path", "paralegal", {"client": case, "action": "propose", "steps": steps, "removed": ["ead"], "reason": "The court order comes after the I-360 here."})
    assert status == 200, out
    change = out["proposed"]["change"]
    assert out["approved"] is None and "I-765 left out" in change and "removed" in change and "order changed" in change, change
    j = journey.journey(d)
    assert not j["own_path"] and [x["id"] for x in j["stages"]] == template and not any(x["id"].startswith("path.") for x in j["deadlines"])
    queue = w.get("/api/approvals?kind=path", "attorney")
    assert any(r["case"] == case and r["kind"] == "path" and "I-765 left out" in r["what"] for r in queue["rows"]), queue
    assert w.call("/api/path", "paralegal", {"client": case, "action": "approve"})[1] == 403
    _, status, out = w.call("/api/path", "attorney", {"client": case, "action": "approve"})
    assert status == 200 and out["proposed"] is None and out["approved"]["approved_by"] == "Ana Attorneyexemplo"
    j = journey.journey(d)
    ids = [x["id"] for x in j["stages"]]
    assert j["own_path"] and ids == [s["id"] for s in steps] and ids.index("state_court") > ids.index("i360_pending")
    # the deadline: the template's own words for the I-485 step, by the date set
    label = next(lbl for f, lbl, _ in journey.settings()["next_filings"]["i485_ready"] if f == "i485")
    assert any(x["id"] == "path.i485_ready.i485" and x["date"] == "2026-12-01" and x["what"] == label for x in j["deadlines"])
    assert "ead" not in [f["filing"] for f in j["next_filings"]]
    # the rest, against the templates' own records for the same filings
    der = out["derived"]
    assert der["packet_order"] == ["i360", "i485"]
    packets = [json.loads(schema_path.path("packet", f).read_text(encoding="utf-8")) for f in ("i360", "i485")]
    titles = {e["title"] for p in packets for e in p["exhibits"]}
    assert {x["title"] for x in der["documents"] if x.get("title")} <= titles and len([x for x in der["documents"] if x.get("title")]) >= 4
    assert [q["filing"] for q in der["questionnaire"]] == ["i360", "i485"] and der["questionnaire"][0]["none"] == path.NO_QUESTIONS and der["questionnaire"][1]["sections"]
    assert any(x["step"] == "state_court" and x["none"] == path.NO_RULE for x in path.derive([{"id": "state_court", "date": "2026-11-01"}])["deadlines"])
    shown = journey.client_view(j, "pt")["path"]
    assert [x["name"] for x in shown] == [labels()[i]["pt"][0] for i in ids if i in labels()]
    rows = [r for r in ledger(w) if r.get("kind") == "path" and r.get("case") == case]
    assert [r["action"] for r in rows] == ["proposed", "approved"] and rows[0]["who"] == "Paulo Paralegalexemplo" and rows[1]["who"] == "Ana Attorneyexemplo"


def test_a_step_or_filing_the_templates_do_not_know_and_a_change_without_a_reason_are_refused(world):
    w = world
    case = plain_sij(w)
    for body in ({"steps": [{"id": "intake"}, {"id": "Call the client's cousin"}], "reason": "x"}, {"steps": [{"id": "intake"}], "removed": ["form-x"], "reason": "x"},
                 {"steps": [{"id": "intake"}, {"id": "intake"}], "reason": "x"}, {"steps": [{"id": "intake"}, {"id": "i360_ready"}], "reason": ""},
                 {"steps": [{"id": "intake", "date": "next week"}], "reason": "x"}):
        _, status, out = w.call("/api/path", "attorney", {"client": case, "action": "propose", **body})
        assert status == 400, (body, out)


def test_an_attorneys_own_change_is_approved_with_it_undo_returns_the_template_and_a_refusal_is_recorded(world):
    import journey

    w = world
    case = plain_sij(w)
    d = w.clients / case
    template = journey.journey(d)["template_stages"]
    w.call("/api/path", "attorney", {"client": case, "action": "undo", "reason": "start again"})  # the earlier test's path, if any
    _, status, out = w.call("/api/path", "attorney", {"client": case, "action": "propose", "steps": [{"id": x} for x in template if x != "oath"], "reason": "test"})
    assert status == 200 and out["approved"]["approved_by"] == "Ana Attorneyexemplo" and out["proposed"] is None
    assert "oath" not in [x["id"] for x in journey.journey(d)["stages"]]
    assert w.call("/api/path", "paralegal", {"client": case, "action": "undo", "reason": "x"})[1] == 403
    _, status, out = w.call("/api/path", "attorney", {"client": case, "action": "undo", "reason": "the template was right"})
    assert status == 200 and out["approved"] is None and [x["id"] for x in journey.journey(d)["stages"]] == template
    w.call("/api/path", "paralegal", {"client": case, "action": "propose", "steps": [{"id": x} for x in template[:-1]], "reason": "no citizenship"})
    assert w.call("/api/path", "attorney", {"client": case, "action": "refuse", "reason": ""})[1] == 400
    _, status, out = w.call("/api/path", "attorney", {"client": case, "action": "refuse", "reason": "the client wants citizenship"})
    assert status == 200 and out["proposed"] is None and out["approved"] is None
    actions = [r["action"] for r in ledger(w) if r.get("kind") == "path" and r.get("case") == case]
    assert actions[-5:] == ["proposed", "approved", "undone", "proposed", "refused"]


def test_a_restricted_cases_path_is_the_one_answer_a_made_up_id_gets_for_a_paralegal_not_named(world):
    import restricted
    from review import server as srv

    w = world
    para = {"role": "paralegal", "email": w.manifest["people"]["paralegal"]["email"]}
    hidden = [c for c in w.manifest["restricted_ids"] if not restricted.visible_to(para, w.clients / c)]
    assert hidden
    for c in hidden[:2] + ["nobody-here"]:
        assert w.call("/api/path?client=" + quote(c), "paralegal")[1:] == (404, srv.UNKNOWN)
        assert w.call("/api/path", "paralegal", {"client": c, "action": "propose", "steps": [{"id": "intake"}], "reason": "x"})[1:] == (404, srv.UNKNOWN)
    assert w.call("/api/path?client=" + quote(hidden[0]), "attorney")[1] == 200
