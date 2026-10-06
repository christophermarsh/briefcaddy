"""The trust polish a buyer's first hour turned up (docs/research/buyer_walkthrough.md): no placeholder in a
letter, a numbered list that starts at (1), "queued" when nothing was sent, a count that explains itself, a client
not asked what the office decided, a year written as a year, a long-passed deadline that isn't an alarm.
Everything runs on the made-up world (tests/e2e/world.py) or on constructed facts."""
# ruff: noqa: F811 -- imported pytest fixture is intentionally injected by name

from __future__ import annotations

import io
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from pypdf import PdfReader

from fill import cover_letter as cl
from portal.notify import Notifier, delivery
import schema_path
from test_communication_consent import firm, granted  # noqa: F401 -- isolated consent fixture

_REPO = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 1)
FACTS = {"given_name": "ANA", "middle_name": None, "family_name": "SAMPLE DA SILVA", "a_number": "A99000001", "sex": "F",
         "country_of_birth": "BRAZIL", "i360_priority_date": "2025-02-10"}


def _text(pdf: bytes) -> str:
    return " ".join(" ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages).split())


def _config(month="October 2026", cutoff="2025-06-01"):
    c = cl.load_config()
    c["visa_bulletin"]["month"] = month
    c["visa_bulletin"]["eb4_cutoff"] = {"ALL CHARGEABILITY": cutoff} if cutoff else {}
    return c


def _plan(forms=("g28", "i485", "i765"), types=("i360_approval", "passport")):
    return {"forms": [{"id": f} for f in forms], "exhibits": [{"files": [{"doc": f"{t}.pdf", "type": t} for t in types]}]}


# 1. a letter never carries a placeholder ---------------------------------------------------------


@pytest.mark.parametrize("month, cutoff", [(None, None), ("October 2026", None), (None, "2025-06-01")])
def test_a_letter_without_the_bulletin_facts_has_no_bracket_and_the_packet_is_held(month, cutoff):
    config = _config(month, cutoff)
    text = _text(cl.render(_plan(), FACTS, config, TODAY, draft=False))
    assert "[" not in text and "cut-off" not in text and "Priority Date Eligibility" not in text  # the sentence is left out
    assert "Filing Fees" in text and "Sincerely" in text  # the rest of the letter is whole
    _, problems = cl.priority(FACTS, config, TODAY)
    assert any("Set this month's Visa Bulletin" in p and "Settings" in p for p in problems)  # packet.plan() adds these: "Ready to mail?" blocks


def test_a_letter_with_the_bulletin_facts_still_has_the_paragraph():
    text = _text(cl.render(_plan(), FACTS, _config(), TODAY, draft=False))
    assert "Priority Date Eligibility" in text and "October 2026" in text and "[" not in text


def test_a_gap_the_letter_cannot_leave_out_blocks_the_packet():
    plan, config = _plan(), _config()
    assert cl.gaps(plan, FACTS, config) == []
    assert any("client's name" in p for p in cl.gaps(plan, {**FACTS, "given_name": None, "family_name": None}, config))
    assert any("gap" in p for p in cl.gaps(plan, FACTS, config | {"fees": "Pay {i130} and [fee]."}))


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def test_no_letter_template_reads_like_a_gap():
    # "[set by the filing's module]" is overwritten by the filing's own module; any other bracket in a template would print
    for path in schema_path.glob("cover_letter"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("_about", None)
        for text in _strings(data):
            assert "[" not in text or text == "[set by the filing's module]", (path.stem, text)


# 2. the forms list starts at (1) --------------------------------------------------------------------


def test_the_forms_are_numbered_from_one_over_what_is_listed():
    text = _text(cl.render(_plan(forms=("g28", "i485")), FACTS, _config(), TODAY, draft=False))
    forms = text.split("FORMS")[1].split("SUPPORTING DOCUMENTS")[0]
    numbers = re.findall(r"\((\d+)\)", forms)
    assert numbers == [str(i) for i in range(1, len(numbers) + 1)] and len(numbers) >= 3
    assert forms.strip().startswith("(1) This cover letter;") and "(2)" in forms  # the firm's convention: the letter is (1), the forms follow
    assert forms.strip().endswith(".") and ";" in forms  # the last item ends the list


# 3. "Sent" only when something was sent -------------------------------------------------------------


def test_a_dry_run_is_reported_as_queued_never_sent(firm):
    scope, store, client = firm
    granted(firm)  # actual fictional evidence; imported booleans authorize nothing
    results = Notifier(scope.portal / "outbox.jsonl", env={}, cases_root=scope.cases, store=store).send(store.profile(client), "request")
    d = delivery(results, now=None)
    assert d["status"] == "queued" and d["text"].startswith("Queued, no mail server configured")
    assert "Sent" not in d["text"] and re.match(r"\d{4}-\d{2}-\d{2}T", d["at"])
    assert all(row["result"] == "skipped" for row in results if row["channel"] != "email")
    both = delivery([{"channel": channel, "result": "dry-run (outbox)"} for channel in ("email", "sms")])
    assert both["status"] == "queued" and "text service" in both["text"] and "Sent" not in both["text"]
    assert delivery([{"channel": "email", "result": "sent"}])["status"] == "sent"
    failed = delivery([{"channel": "email", "result": "failed: SMTPException"}])
    assert failed["status"] == "failed" and failed["text"] == "Failed by email; check the mail settings"  # no exception class on screen
    assert delivery([{"channel": "email", "result": "skipped", "why": "no consent"}])["status"] == "none"


# the made-up world for the review-app items ----------------------------------------------------------


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    sys.path.insert(0, str(_REPO / "tests" / "e2e"))
    import world as w

    return w.build(tmp_path_factory.mktemp("polish") / "w")


@pytest.fixture
def app(world, tmp_path):
    """A review app on a private copy of the made-up world (a decision writes into it)."""
    import shutil

    from review.server import ReviewApp

    root = tmp_path / "w"
    shutil.copytree(world["root"], root)
    # These tests settle a planted SSN conflict. Review its actual retained
    # fictional card's subject first, then rebuild the client's confirmation
    # task through the real engine. Other evidence remains independently held.
    import documents
    import subject_attribution as subjects
    from portal.engine import sync_confirmations
    from portal.store import PortalStore
    case = root / "clients" / "demo-ana"
    row = next(r for r in subjects.views(case) if any(f["key"] == "applicant.ssn" for f in r["facts"]))
    person = next(p for p in documents.read(case)["case_subjects"]["people"] if p["case_role"] == "applicant")
    subjects.assign(case, row["instance_id"], row["fingerprint"], {"holder": person["id"]},
                    "Fictional Source Reviewer", "paralegal", note="Reviewed the retained fictional SSN card for this test applicant.")
    sync_confirmations(PortalStore(root / "portal"), "demo-ana", case)
    return ReviewApp(root / "clients", schema_path.path("field_map", "i485"), schema_path.path("template", "i485"),
                     schema_path.path("law", "policy_sijs"), portal_root=root / "portal")


def _open(app, client):
    items = app.items(client)
    return items, sum(len(c["item_ids"]) for c in items["cards"])


def test_a_decision_moves_an_item_from_open_to_decided_and_names_what_it_unlocks(app):
    before, open_before = _open(app, "demo-ana")
    assert not before["done"]
    # a plain confirmation moves one item and adds none
    plain = next(i for i in before["open"] if "confirm" in i["actions"] and i["group"] == "questionnaire")
    out = app.decide("demo-ana", {"item_id": plain["id"], "action": "confirm", "reviewer": "Paulo Paralegal"})
    after, open_after = _open(app, "demo-ana")
    assert out["recorded"] == 1 and out["unlocked"] == [] and len(after["done"]) == 1
    assert open_after == open_before - 1 and plain["id"] not in {i["id"] for i in after["open"]}
    # settling the Social Security number lets the firm's SSA policy answer its two follow-ups: they are new, and the app says so
    ssn = next(i for i in after["open"] if i["id"] == "fact:applicant.ssn")
    value = ssn["facts"][0]["sources"][-1]["value"]
    out = app.decide("demo-ana", {"item_id": ssn["id"], "action": "set", "values": {"applicant.ssn": value}, "reviewer": "Paulo Paralegal",
                                  "evidence_fingerprints": ssn["evidence_fingerprints"]}, "paralegal")
    final, open_final = _open(app, "demo-ana")
    assert len(final["done"]) == 2 and ssn["id"] not in {i["id"] for i in final["open"]}
    assert len(out["unlocked"]) == 2 and open_final == open_after - 1 + len(out["unlocked"])  # nothing rises without being named
    assert all(u["title"] and "applicant." not in u["title"] for u in out["unlocked"])


# 5. what the office decided is not asked of the client again -------------------------------------------


def test_a_decided_confirmation_leaves_the_clients_list_and_the_file_says_why(app, world):
    from portal.store import PortalStore

    store = PortalStore(app.portal_root)
    assert any(t["kind"] == "confirm" and t["question"] == "ssn" for t in store.tasks("demo-ana"))  # the client would be asked
    items, _ = _open(app, "demo-ana")
    ssn = next(i for i in items["open"] if i["id"] == "fact:applicant.ssn")
    out = app.decide("demo-ana", {"item_id": ssn["id"], "action": "set", "reviewer": "Paulo Paralegal", "note": "SS card controls",
                                  "values": {"applicant.ssn": ssn["facts"][0]["sources"][-1]["value"]},
                                  "evidence_fingerprints": ssn["evidence_fingerprints"]}, "paralegal")
    assert out["client_not_asked"] == 1
    assert not [t for t in store.tasks("demo-ana") if t["kind"] == "confirm" and t["question"] == "ssn"]
    skipped = store.skipped_tasks("demo-ana")
    assert skipped[0]["fact"] == "applicant.ssn" and "already decided" in skipped[0]["why"] and "Paulo Paralegal" in skipped[0]["why"]
    done = app.items("demo-ana")["done"][0]
    assert done["client_not_asked"] == skipped[0]["why"]  # shown on the decision in the log


def test_a_question_the_office_decided_after_adding_it_is_not_sent(app):
    from portal.store import PortalStore

    items, _ = _open(app, "demo-ana")
    ssn = next(i for i in items["open"] if i["id"] == "fact:applicant.ssn")
    asked = app.ask_client("demo-ana", {"reviewer": "Paulo Paralegal", "text": "Please help us check your answer: SSN", "queue": True,
                                        "facts": ["applicant.ssn"]})
    assert asked["waiting"] == 1
    app.decide("demo-ana", {"item_id": ssn["id"], "action": "set", "reviewer": "Paulo Paralegal",
                            "values": {"applicant.ssn": ssn["facts"][0]["sources"][-1]["value"]},
                            "evidence_fingerprints": ssn["evidence_fingerprints"]}, "paralegal")
    sent = app.ask_send("demo-ana", {"reviewer": "Paulo Paralegal"})
    assert sent["count"] == 0 and sent["skipped"] == 1 and "already decided" in sent["why"]
    request = PortalStore(app.portal_root).requests("demo-ana")[-1]
    assert request["status"] == "skipped" and "already decided" in request["skipped_why"]
    # and a new ask about a decided card is refused outright
    again = app.ask_client("demo-ana", {"reviewer": "Paulo Paralegal", "text": "Please help us check your answer: SSN", "queue": True,
                                        "facts": ["applicant.ssn"]})
    assert again["skipped"] is True


def test_the_pipelines_task_list_skips_what_was_decided():
    from portal.bank import answers_to_facts
    from portal.engine import client_tasks
    from batch import process_documents

    answers = {"has_ssn": "Yes", "ssn": "123-45-6780"}
    result = process_documents("pilot", [("ssn_card-1.pdf", "YOUR SOCIAL SECURITY CARD\n123-45-6789\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION")],
                               answers=answers_to_facts(answers))
    assert [t for t in client_tasks(answers, [], result.graph, "en") if t["kind"] == "confirm"]
    skipped: list = []
    decision = {"reviewer": "Paulo Paralegal", "at": "2026-10-02T16:47:00+00:00", "item": {"facts": ["applicant.ssn"]}}
    tasks = client_tasks(answers, [], result.graph, "en", decided={"applicant.ssn": decision}, skipped=skipped)
    assert not [t for t in tasks if t["kind"] == "confirm"]
    assert skipped[0]["task"] == "confirm:ssn" and "10/02/2026" in skipped[0]["why"]


# 6. a year is written as a year ------------------------------------------------------------------------


def test_a_year_in_the_forms_own_wording_is_not_split():
    from review.state import tidy_tooltip

    assert tidy_tooltip("74. Since April 1, 19 97, have you been unlawfully present?") == "74. Since April 1, 1997, have you been unlawfully present?"
    assert tidy_tooltip("on or after April 1, 20 07 (D H S)") == "on or after April 1, 2007 (DHS)"
    assert tidy_tooltip("Items 19 97 stay as they are") == "Items 19 97 stay as they are"  # only a date's year is rejoined


def test_no_card_question_shows_a_split_year(app):
    seen = []
    for client in ("demo-ana", "case-sij"):
        data = app.items(client)
        seen += [c["title"] for c in data["cards"]] + [f["label"] for c in data["cards"] for f in c["facts"]]
    assert seen and not [t for t in seen if "19 97" in t or "D H S" in t]
    assert "Since April 1, 1997, have you been unlawfully present" in app.catalog.label("applicant.part9.unlawfully_present_since_1997")


# 7. a deadline long past is history, not an alarm ----------------------------------------------------------


def _row(deadline_id: str, days: int, client: str = "case-x") -> dict:
    return {"id": client, "summary": {"name": "ANA"}, "journey": {"deadlines": [
        {"id": deadline_id, "date": (date.today() - timedelta(days=days)).isoformat(), "what": "x", "owner": "attorney"}]}}


# one id of each kind: history once missed (grey) and still actionable (red). A display rule, not a legal one.
HISTORY = ["one_year", "vawa_deadline", "i730", "hearing-1.decision.appeal", "hearing-1.decision.reopen", "hearing-1.decision.reconsider",
           "IOE0999000300.denial.2020-01-02"]  # asylum bar, VAWA, I-730, BIA appeal, motions, N-336 / I-290B after a denial
ACTIONABLE = ["IOE0999000300.rfe.2020-01-02", "IOE0999000300.noid.2020-01-02", "ead", "i751", "i90", "t_status_ends", "daca", "u_supb",
              "move-1.ar11", "move-1.eoir33", "age_21", "age_18_state", "cancellation.file"]


@pytest.mark.parametrize("deadline_id", HISTORY)
def test_a_missed_deadline_that_is_history_is_grey(deadline_id):
    from review.overview import deadlines

    assert deadlines([_row(deadline_id, 2271)], horizon_days=60)[0]["level"] == "passed"
    assert deadlines([_row(deadline_id, 5)], horizon_days=60)[0]["level"] == "overdue"  # recent: still late news


@pytest.mark.parametrize("deadline_id", ACTIONABLE)
def test_a_missed_deadline_that_is_still_actionable_stays_red(deadline_id):
    from review.overview import deadlines

    assert deadlines([_row(deadline_id, 2271)], horizon_days=60)[0]["level"] == "overdue"


def test_only_what_is_still_actionable_counts_as_late_in_my_work():
    from review.overview import my_work

    work = my_work([_row("one_year", 2271), _row("ead", 2271, "case-y"), _row("i90", 5, "case-z")], "attorney")
    assert work["counts"]["late"] == 2  # the 2020 asylum bar is history; the lapsed work permit and the recent I-90 are late


# 5b. an acknowledgement settles no answer ---------------------------------------------------------------


def test_only_a_confirmation_a_value_or_a_blank_counts_as_the_office_deciding(tmp_path):
    from portal.engine import decided_facts

    def decision(action):
        return {"action": action, "reviewer": "Paulo", "at": "2026-10-02T16:00:00+00:00", "item": {"facts": [f"applicant.{action}"]}}

    (tmp_path / "decisions.json").write_text(json.dumps({a: decision(a) for a in ("confirm", "set", "blank", "acknowledge")}), encoding="utf-8")
    assert set(decided_facts(tmp_path)) == {"applicant.confirm", "applicant.set", "applicant.blank"}
