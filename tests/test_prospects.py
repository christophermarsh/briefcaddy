"""The first call (src/prospects.py): a person who called and is not a client yet. The record in a folder of its own, the first-contact questionnaire in four languages
(a prospect answers on the portal through the same kind of link as a client, or the office types the answers in), becoming a client with the answers carried over and the conflict
search first, declining with the non-engagement letter, a protected kind restricted from the start, the list (paged, a CSV) and the counts a prospect is never in.
Everyone here is made up ("Lia Exemplo Prospecto", "Rosa Exemplo Prospecto")."""

from __future__ import annotations

import csv
import io
import json
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

import clock
import engagement
import events
import prospects
import restricted
from portal.app import create_app
from portal.bank import all_questions, bank_for, languages, localized
from portal.store import PortalStore
from rules import approval

sys.path.insert(0, str(Path(__file__).resolve().parent))
import people_world  # noqa: E402
import schema_path
from communication_fixture import installation, approve_client, accepted_link

REPO = Path(__file__).resolve().parent.parent
NAME = "Lia Exemplo Prospecto"
CALL = {"name": NAME, "phone": "(555) 010-4444", "email": "lia.prospecto@example.com", "language": "es", "how_heard": "A friend from church", "called_on": "2026-10-02",
        "taken_by": "Jane Paralegal", "note": "Called about her nephew, who is 17.", "consent": {"email": True}}


@pytest.fixture
def firm(tmp_path, monkeypatch):
    data = installation(tmp_path, monkeypatch)
    clients = data / "clients"
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    for var, path in (("I485_QUERY_DB", data / "query.db"), ("I485_EVENTS", data / "events.jsonl"), ("I485_CONFLICTS", data / "conflict_checks.jsonl"), ("I485_CASES", clients),
                      ("I485_RULES_APPROVED", data / "rules_approved.json"), ("PORTAL_DATA", data / "portal"), ("PORTAL_BASE_URL", "http://testserver"),
                      ("I485_INDEX", data / "index.db"), ("I485_INBOX", data / "inbox")):
        monkeypatch.setenv(var, str(path))
    for var in ("SMTP_HOST", "TWILIO_ACCOUNT_SID", "PORTAL_OUTBOX_FULL_LINKS"):
        monkeypatch.delenv(var, raising=False)
    people_world.make(clients)
    (data / "portal").mkdir(parents=True, exist_ok=True)
    return {"data": data, "clients": clients, "portal": data / "portal", "store": PortalStore(data / "portal"), "root": tmp_path}


@pytest.fixture
def server(firm):
    from review.auth import Accounts
    from review.server import COOKIE, ReviewApp, make_handler, serve

    accounts = Accounts(firm["data"] / "review_users.json")
    for email, name, role in (("jane@firm.example", "Jane Paralegal", "paralegal"), ("kim@firm.example", "Kim Paralegal", "paralegal"),
                              ("sam@firm.example", "Sam Attorney", "attorney")):
        accounts.add(email, name, role)
    app = ReviewApp(firm["clients"], schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, portal_root=firm["portal"], accounts=accounts)
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cookie = lambda email: f"{COOKIE}={accounts.session_for(email, how='test')[0]}"  # noqa: E731
    yield {"base": f"http://127.0.0.1:{port}", "jane": cookie("jane@firm.example"), "kim": cookie("kim@firm.example"), "sam": cookie("sam@firm.example"), "app": app,
           "accounts": accounts}
    httpd.shutdown()


def call(srv, who, path, body=None) -> tuple[int, bytes]:
    headers = {"X-Review-App": "1", "Cookie": srv[who]} | ({"Content-Type": "application/json"} if body is not None else {})
    req = urllib.request.Request(srv["base"] + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def ok(srv, who, path, body=None) -> dict:
    status, raw = call(srv, who, path, body)
    assert status == 200, (path, status, raw[:300])
    return json.loads(raw)


def new(srv, who="jane", **extra) -> str:
    return ok(srv, who, "/api/prospect-new", CALL | extra)["id"]


def change(srv, by, pid, action, **body) -> dict:
    return ok(srv, by, "/api/prospect-change", {"prospect": pid, "action": action} | body)


def approve():
    approval.approve(engagement.PRACTICE_ID, "Sam Attorney", "attorney")


def ledger(firm) -> list[dict]:
    return list(events.rows(firm["data"] / "events.jsonl"))


def portal_events(store, client):
    path = store.client_dir(client) / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def consented_prospect(firm, pid):
    """Explicit fictional documented signoff, not imported CALL.consent."""
    store = prospects.store(firm["portal"])
    record = prospects.read(prospects.dir_of(firm["clients"], pid))
    prospects.ensure_portal(store, record, "Fictional Staff")
    approve_client(store, pid)
    return store


# -- the record --------------------------------------------------------------------------------------------------------------------


def test_a_call_is_recorded_in_a_folder_of_its_own_and_is_in_no_client_list_or_count(server, firm):
    clients_before = json.loads(call(server, "sam", "/api/overview")[1])
    pid = new(server)
    assert pid == "prospect-lia-exemplo-prospecto"
    folder = firm["data"] / "prospects" / pid
    rec = json.loads((folder / "prospect.json").read_text(encoding="utf-8"))
    assert rec["name"] == NAME and rec["language"] == "es" and rec["how_heard"] == "A friend from church" and rec["called_on"] == "2026-10-02" and rec["taken_by"] == "Jane Paralegal"
    assert rec["created_by"] == "Jane Paralegal" and rec["version"] == 1 and rec["kind"] is None and rec["history"][0]["what"] == "Recorded a first call"
    # the prospect is in no list of clients, no stage count, no report, and not in the portal's clients
    for route in ("/api/clients", "/api/overview", "/api/reports"):
        assert "Prospecto" not in call(server, "sam", route)[1].decode() and pid not in call(server, "sam", route)[1].decode(), route
    assert ok(server, "sam", "/api/search?q=Prospecto")["total"] == 0
    after = json.loads(call(server, "sam", "/api/overview")[1])
    assert after["stages"] == clients_before["stages"] and len(after["clients"]) == len(clients_before["clients"])
    assert pid not in firm["store"].clients() and not (firm["portal"] / "clients" / pid).exists()
    assert ok(server, "sam", "/api/reports")["cases"] == 3  # the made-up firm's three cases: the prospect is not a fourth
    # but it is on the Prospects list, new, with its first note by the person who took the call
    listed = ok(server, "jane", "/api/prospects")
    assert [r["id"] for r in listed["rows"]] == [pid] and listed["rows"][0]["stage"] == "new" and listed["counts"]["new"] == 1 and listed["total"] == 1
    page = ok(server, "jane", "/api/prospect?prospect=" + pid)
    assert page["notes"][0]["text"] == "Called about her nephew, who is 17." and page["notes"][0]["by"] == "Jane Paralegal" and page["stage_name"] == "New"
    # one ledger row, naming the prospect as "prospect:<id>" and never a name, a number or an answer
    rows = [r for r in ledger(firm) if r["kind"] == "prospects"]
    assert rows[0]["case"] == f"prospect:{pid}" and rows[0]["who"] == "Jane Paralegal" and rows[0]["action"] == "created"
    text = json.dumps(rows)
    assert "Lia" not in text and "555" not in text and "church" not in text


def test_a_call_needs_a_way_to_reach_the_person_and_real_choices(server):
    for bad, words in (({"phone": "", "email": ""}, "phone number or an email"), ({"phone": "+1234567"}, "full country-code"), ({"email": "nope"}, "unsupported_email"),
                       ({"language": "xx"}, "Choose the person's language"), ({"called_on": "2099-01-01"}, "can't be in the future"), ({"called_on": "10/02/2026"}, "calendar"),
                       ({"kind": "chef"}, "kind of case"), ({"office": "mars"}, "office"), ({"consent": {"sms": True}, "phone": ""}, "phone number"), ({"name": "L"}, "full name")):
        status, raw = call(server, "jane", "/api/prospect-new", CALL | bad)
        assert status == 400 and words in json.loads(raw)["error"], (bad, raw)
    assert ok(server, "jane", "/api/prospects")["total"] == 0
    # a name with an id taken already gets a number
    assert new(server) == "prospect-lia-exemplo-prospecto" and new(server) == "prospect-lia-exemplo-prospecto-2"


# -- the questionnaire ---------------------------------------------------------------------------------------------------------------


def test_the_first_contact_questionnaire_is_in_four_languages_and_every_question_is_listed_for_the_attorney():
    bank = bank_for({"filing": "first_contact"})
    questions = all_questions(bank)
    assert len(questions) >= 15 and bank["documents"] == []
    review = (REPO / "docs" / "attorney_review.md").read_text(encoding="utf-8")
    for qid, q in questions.items():
        for lang in languages():
            assert q["label"].get(lang), (qid, lang)
        assert q["label"]["en"] != q["label"]["pt"], qid
        assert " -- " not in json.dumps(q, ensure_ascii=False) and "—" not in json.dumps(q, ensure_ascii=False), qid
        if qid.startswith("fc_"):  # a question of its own: its English words are in the attorney's list
            assert q["label"]["en"] in review, qid
    for lang in languages():
        shown = localized(bank, lang, {}, help={"sections": {}, "questions": {}, "faq": []})
        assert [s["id"] for s in shown] == ["fc_you", "fc_from", "fc_here", "fc_family", "fc_court", "fc_ask"]
        assert all(s["title"] for s in shown)
    # the court and police contact is asked as yes or no (with "not sure"), never as a story
    for qid in ("in_proceedings", "arrested", "fc_family_court", "fc_papers"):
        assert questions[qid]["type"] == "yes_no" and [o["value"] for o in questions[qid]["options"]] == ["Yes", "No", "Unsure"]
    # only what the office cannot do without is required; a client's own questionnaire list never offers this one
    assert {q for q, v in questions.items() if v.get("required", True)} == {"given_name", "family_name", "fc_asking"}
    from review import front_desk

    assert "first_contact" not in {x["id"] for x in front_desk.questionnaires()}
    # an answer to a question the green card's questionnaire asks too keeps its id, so it carries to a client
    green = all_questions(bank_for({"filing": "i485"}))
    shared = {q for q in questions if not q.startswith("fc_")}
    assert shared and shared <= set(green) and all(questions[q]["type"] == green[q]["type"] for q in shared)


def test_a_prospect_answers_on_the_phone_in_spanish_and_the_office_sees_who_answered(server, firm):
    pid = new(server)
    token = accepted_link(consented_prospect(firm, pid), pid)
    phone = TestClient(create_app(firm["portal"], base_url="http://testserver", secure_cookies=False))
    assert phone.get(f"/l/{token}", follow_redirects=False).status_code == 303
    me = phone.get("/api/me").json()
    assert me["filing"] == "first_contact" and me["language"] == "es" and me["documents"] == [] and me["first_name"] == "Lia"
    assert me["sections"][0]["title"] == "Información sobre usted" and me["missing"] == ["given_name", "family_name", "fc_asking"]
    h = {"X-Portal": "1"}
    answers = {"given_name": "Lia", "family_name": "Exemplo Prospecto", "birth_country": "Brasil", "dob": "2008-05-04", "in_proceedings": "No", "fc_asking": "Quiero preguntar por mi sobrino."}
    saved = phone.put("/api/answers", json=answers, headers=h).json()
    assert saved["errors"] == {} and saved["missing"] == [] and saved["progress"] == 100
    # a prospect has no thread with the office and no documents, and the client-only routes answer 401
    for path in ("/api/message", "/api/upload", "/api/agreement", "/api/task"):
        assert phone.post(path, json={"text": "x"}, headers=h).status_code in (401, 422), path
    assert phone.post("/api/submit", json={"agree": True, "signature": "Lia Exemplo Prospecto"}, headers=h).json()["status"] == "submitted"
    # the office: waiting for the attorney, the answers each marked as the prospect's
    page = ok(server, "jane", "/api/prospect?prospect=" + pid)
    assert page["stage"] == "waiting" and page["stage_name"] == "Waiting for the attorney" and page["answered"] == 6
    given = {q["id"]: q for s in page["sections"] for q in s["questions"]}
    assert given["given_name"]["answer"] == "Lia" and given["given_name"]["who"] == "the prospect" and given["dob"]["answer"] == "2008-05-04"
    assert given["in_proceedings"]["shown"] == "No" and given["fc_asking"]["answer"].startswith("Quiero")
    assert json.loads((firm["data"] / "prospects" / pid / "answers.json").read_text(encoding="utf-8"))["family_name"] == "Exemplo Prospecto"  # a copy kept with the prospect
    # the attorney's My work lists the prospect, the paralegal's does not
    assert [x["prospect"] for x in ok(server, "sam", "/api/work?owner=attorney")["prospects"]] == [pid]
    assert "prospects" not in ok(server, "jane", "/api/work?owner=paralegal")
    # the portal's own rows are in the firm's ledger as the prospect's, by the person who answered
    rows = [r for r in ledger(firm) if r["case"] == f"prospect:{pid}"]
    assert {r["kind"] for r in rows} == {"prospects", "notes"} and any(r["action"] == "answered" and r["role"] == "client" for r in rows)
    assert not any("Lia" in r["what"] for r in rows)
    assert pid not in firm["store"].clients()


def test_the_office_types_the_answers_in_from_the_call_and_each_says_who_typed_it(server, firm):
    pid = new(server)
    status, raw = call(server, "jane", "/api/prospect-change", {"prospect": pid, "action": "answers", "answers": {"dob": "next spring"}})
    assert status == 400 and "Check the answer to" in json.loads(raw)["error"] and "date" in json.loads(raw)["error"]
    assert call(server, "jane", "/api/prospect-change", {"prospect": pid, "action": "answers", "answers": {}})[0] == 400
    page = change(server, "jane", pid, "answers", answers={"given_name": "Lia", "family_name": "Exemplo Prospecto", "dob": "2008-05-04", "arrested": "No", "fc_asking": "About her nephew"})
    given = {q["id"]: q for s in page["sections"] for q in s["questions"]}
    assert page["stage"] == "answering" and page["answered"] == 5 and given["given_name"]["who"] == "typed in by Jane Paralegal" and given["arrested"]["shown"] == "No"
    # the prospect changes one of them on their phone: it is theirs again
    token = accepted_link(consented_prospect(firm, pid), pid)
    phone = TestClient(create_app(firm["portal"], base_url="http://testserver", secure_cookies=False))
    phone.get(f"/l/{token}", follow_redirects=False)
    assert phone.get("/api/me").json()["answers"]["given_name"] == "Lia"  # what the office typed is what the prospect sees
    phone.put("/api/answers", json={"given_name": "Lia Maria"}, headers={"X-Portal": "1"})
    page = ok(server, "jane", "/api/prospect?prospect=" + pid)
    given = {q["id"]: q for s in page["sections"] for q in s["questions"]}
    assert given["given_name"]["answer"] == "Lia Maria" and given["given_name"]["who"] == "the prospect" and given["family_name"]["who"] == "typed in by Jane Paralegal"
    # the ledger says answers were typed, never which
    row = [r for r in ledger(firm) if r["action"] == "answers_typed"][0]
    assert row["what"] == "Typed in 5 first-contact answer(s) from the call" and row["who"] == "Jane Paralegal"


def test_sending_the_questions_goes_to_the_channels_the_person_agreed_to_and_the_link_on_screen_is_the_attorneys(server, firm):
    """Required staff adapter acceptance; run after current-principal handoff lands."""
    pid = new(server)
    consented_prospect(firm, pid)
    sent = change(server, "jane", pid, "send")
    assert sent["delivery"]["status"] == "queued" and "no mail server" in sent["delivery"]["text"] and sent["stage"] == "new"
    outbox = [json.loads(x) for x in (firm["portal"] / "outbox.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(outbox) == 1 and outbox[0]["to"] == "lia.prospecto@example.com" and outbox[0]["subject"] == "Case Review: unas preguntas antes de hablar"
    assert "/l/…" in outbox[0]["body"]  # only the last characters of the link are kept
    assert call(server, "jane", "/api/prospect-change", {"prospect": pid, "action": "link"})[0] == 403  # a working credential: an attorney's
    page = ok(server, "sam", "/api/prospect?prospect=" + pid)
    assert page["questions_sent"] is None and page["questions_attempt"]["by"] == "Jane Paralegal" and page["can_show_link"]
    link = change(server, "sam", pid, "link")["link"]
    assert link["hours"] == 72
    with TestClient(create_app(firm["portal"], base_url="http://testserver", secure_cookies=False)) as phone:
        result = phone.get(link["url"].replace("http://testserver", ""), follow_redirects=False)
        assert result.headers["location"] == "/consent"
        assert phone.get("/api/communication/consent").json()["consent_only"] is True
        assert phone.get("/api/me").status_code == 401


@pytest.mark.parametrize("approved", [False, True])
def test_first_question_attempt_without_acceptance_keeps_new_stage_and_success_empty(firm, monkeypatch, approved):
    from portal.notify import Notifier
    made = prospects.create(firm["clients"], CALL, "Jane Paralegal", "paralegal")
    pid = made["id"]
    if approved:
        consented_prospect(firm, pid)
    calls = []
    original = Notifier._email
    monkeypatch.setattr(Notifier, "_email", lambda self, *args: calls.append(args) or original(self, *args))
    outcome = prospects.send_questions(firm["clients"], firm["portal"], pid, "Jane Paralegal", "paralegal")
    record = prospects.read(prospects.dir_of(firm["clients"], pid))
    profile = prospects.store(firm["portal"]).profile(pid)
    assert outcome["delivery"]["status"] == ("queued" if approved else "none")
    assert record["questions_sent"] is None and prospects.stage(record, record["portal"]) == "new"
    assert record["questions_attempt"]["result"] == outcome["delivery"]
    assert profile["last_invite_attempt"] == record["questions_attempt"]
    assert not profile.get("invited_at") and not profile.get("last_invite_at")
    assert len(calls) == int(approved)
    rows = ledger(firm)
    assert any(row["action"] == "questions_attempt" for row in rows)
    assert not any(row["action"] == "questions_sent" for row in rows)
    assert not any(row["event"] == "invited" for row in portal_events(prospects.store(firm["portal"]), pid))


def test_queued_then_accepted_email_and_later_held_preserve_only_actual_success(firm, monkeypatch):
    from portal.notify import Notifier
    from portal import communication_consent as consent
    from communication_fixture import STAFF
    made = prospects.create(firm["clients"], CALL, "Jane Paralegal", "paralegal")
    pid = made["id"]
    store = consented_prospect(firm, pid)
    first = prospects.send_questions(firm["clients"], firm["portal"], pid, "Jane Paralegal", "paralegal")
    assert first["delivery"]["status"] == "queued" and not store.profile(pid).get("invited_at")
    calls = []
    monkeypatch.setattr(Notifier, "_email", lambda self, *args: calls.append(args) or "sent")
    accepted = prospects.send_questions(firm["clients"], firm["portal"], pid, "Jane Paralegal", "paralegal", again=True)
    record = prospects.read(prospects.dir_of(firm["clients"], pid))
    successful = record["questions_sent"]
    profile = store.profile(pid)
    assert accepted["sent"][0]["result"] == "sent" and accepted["delivery"]["status"] == "sent"
    assert successful["result"] == accepted["delivery"] and prospects.stage(record, record["portal"]) == "answering"
    assert profile["last_invite_at"] == successful["at"] and profile["invited_at"]
    invited_events = [row for row in portal_events(store, pid) if row["event"] == "invited"]
    consent.revoke(store.communication_scope(), store, pid, ["email"], actor_email=STAFF)
    held = prospects.send_questions(firm["clients"], firm["portal"], pid, "Jane Paralegal", "paralegal", again=True)
    now = prospects.read(prospects.dir_of(firm["clients"], pid))
    assert held["delivery"]["status"] == "none" and len(calls) == 1
    assert now["questions_sent"] == successful and now["questions_attempt"]["result"] == held["delivery"]
    assert store.profile(pid)["invited_at"] == profile["invited_at"]
    assert store.profile(pid)["last_invite_at"] == profile["last_invite_at"]
    assert [row for row in portal_events(store, pid) if row["event"] == "invited"] == invited_events


# -- becoming a client ---------------------------------------------------------------------------------------------------------------


def test_becoming_a_client_runs_the_conflict_search_first_and_carries_the_answers_notes_tasks_and_what_could_apply_for(server, firm):
    pid = new(server)
    change(server, "jane", pid, "answers", answers={"given_name": "Lia", "family_name": "Exemplo Prospecto", "dob": "2008-05-04", "in_proceedings": "No",
                                                     "fc_asking": "About her nephew", "birth_country": "Brasil"})
    change(server, "jane", pid, "note", text="Second call: she has a hearing date letter.")
    change(server, "jane", pid, "task", title="Ask her to bring the letter", date="2026-10-12", who="", who_name="")
    change(server, "jane", pid, "apply", question="sij.under21", answer="yes")
    prefill = ok(server, "jane", "/api/prospect?prospect=" + pid)["prefill"]
    assert prefill["prospect"] == pid and prefill["name"] == NAME and prefill["dob"] == "2008-05-04" and prefill["language"] == "es"
    token = accepted_link(consented_prospect(firm, pid), pid)  # prior accepted link stops when they become a client
    # the search first: Add a client is refused without a recorded decision, as for any client
    body = {"name": prefill["name"], "phone": prefill["phone"], "email": prefill["email"], "language": "es", "consent": {"email": True}, "prospect": pid, "filing": "i485"}
    assert call(server, "jane", "/api/client-add", body)[0] == 400
    assert ledger(firm) and not (firm["clients"] / "lia-exemplo-prospecto").exists()
    found = ok(server, "jane", "/api/conflict-search", {"purpose": "add", "name": NAME, "dob": "05/04/2008"})
    added = ok(server, "jane", "/api/client-add", body | {"conflict": {"search": found["id"], "decision": "none"}})
    assert added["id"] == "lia-exemplo-prospecto" and added["from_call"] == {"answers": 5, "notes": 2, "tasks": 1, "apply_for": 1, "restricted": False}
    # the answers the client's questionnaire asks too are the client's portal answers now (the first-contact ones stay with the call)
    client_answers = firm["store"].answers("lia-exemplo-prospecto")
    assert client_answers["given_name"] == "Lia" and client_answers["dob"] == "2008-05-04" and client_answers["in_proceedings"] == "No" and "fc_asking" not in client_answers
    assert firm["store"].profile("lia-exemplo-prospecto")["prospect"] == pid
    # the notes, the task and the answer to what-could-apply-for are on the case, with who and when as they were
    case = firm["clients"] / "lia-exemplo-prospecto"
    notes = json.loads((case / "notes.json").read_text(encoding="utf-8"))["notes"]
    assert [n["by"] for n in notes] == ["Jane Paralegal"] * 2 and all(n["carried"] == pid for n in notes) and notes[0]["at"].startswith("2026-10-05")
    deadlines = json.loads((case / "deadlines_set.json").read_text(encoding="utf-8"))["deadlines"]
    assert deadlines[0]["title"] == "Ask her to bring the letter" and deadlines[0]["task"] and deadlines[0]["carried_from"].startswith(pid + "|")
    assert json.loads((case / "apply_for.json").read_text(encoding="utf-8"))["answers"]["sij.under21"]["by"] == "Jane Paralegal"
    # the prospect: became a client, out of the open lists, its link stopped
    page = ok(server, "jane", "/api/prospect?prospect=" + pid)
    assert page["stage"] == "client" and page["became"]["id"] == "lia-exemplo-prospecto" and page["prefill"] is None
    assert ok(server, "jane", "/api/prospects")["counts"]["client"] == 1
    assert prospects.store(firm["portal"]).redeem_link(token) is None
    # the task came over, so it is on the lists once, as the case's: not as the prospect's
    due = [d for d in ok(server, "sam", "/api/deadlines?page=1")["items"] if d["what"] == "Ask her to bring the letter"]
    assert [d["client"] for d in due] == ["lia-exemplo-prospecto"]
    # the case page shows the first call, answers and who gave them
    call_card = ok(server, "sam", "/api/case-notes?client=lia-exemplo-prospecto")["first_call"]
    assert call_card["called_on"] == "2026-10-02" and call_card["taken_by"] == "Jane Paralegal" and any(a["label"].startswith("What would you like to ask") for a in call_card["answers"])
    # nothing more can be done as a prospect: no second client from the same call
    again = call(server, "jane", "/api/client-add", body | {"name": "Lia Outra Exemplo", "conflict": {"search": found["id"], "decision": "none"}})
    assert again[0] == 400 and "client now" in json.loads(again[1])["error"]
    rows = [r["action"] for r in ledger(firm) if r["case"] == f"prospect:{pid}"]
    assert "became_client" in rows


def test_a_conflict_decision_that_declines_leaves_the_prospect_as_it_was(server, firm):
    pid = new(server)
    found = ok(server, "jane", "/api/conflict-search", {"purpose": "add", "name": NAME})
    body = {"name": NAME, "phone": "(555) 010-4444", "language": "es", "consent": {}, "prospect": pid, "filing": "i485", "conflict": {"search": found["id"], "decision": "declined"}}
    assert ok(server, "jane", "/api/client-add", body)["declined"] is True
    assert ok(server, "jane", "/api/prospect?prospect=" + pid)["stage"] == "new" and not (firm["clients"] / "lia-exemplo-prospecto").exists()


# -- declining ---------------------------------------------------------------------------------------------------------------------------


def test_declining_is_the_attorneys_and_makes_the_non_engagement_letter_and_stops_the_link(server, firm):
    pid = new(server)
    token = accepted_link(consented_prospect(firm, pid), pid)
    status, raw = call(server, "sam", "/api/prospect-change", {"prospect": pid, "action": "decline", "reason": "The matter is not one the firm takes."})
    assert status == 400 and "approves the letters' wording" in json.loads(raw)["error"]  # the firm's own wording, approved first (src/engagement.py)
    approve()
    assert call(server, "jane", "/api/prospect-change", {"prospect": pid, "action": "decline", "reason": "x"})[0] == 403
    assert call(server, "sam", "/api/prospect-change", {"prospect": pid, "action": "decline", "reason": ""})[0] == 400
    page = change(server, "sam", pid, "decline", reason="The matter is not one the firm takes.", additions="We suggest the bar's referral service.")
    assert page["stage"] == "declined" and page["declined"]["by"] == "Sam Attorney" and page["declined"]["on"] == "2026-10-05" and page["can_decline"]
    [letter] = page["letters"]
    assert letter["name"].startswith("Non-engagement letter")
    pdf = ok_bytes(server, "sam", f"/api/prospect-letter.pdf?prospect={pid}&id={letter['id']}")
    text = " ".join(" ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages).split())
    assert NAME in text and "We suggest the bar's referral service." in text
    # the link stops that day, and nothing more is sent
    assert prospects.store(firm["portal"]).redeem_link(token) is None
    status, raw = call(server, "sam", "/api/prospect-change", {"prospect": pid, "action": "send"})
    assert status == 400 and "did not take this case" in json.loads(raw)["error"]
    listed = ok(server, "jane", "/api/prospects?stage=declined")
    assert [r["id"] for r in listed["rows"]] == [pid] and listed["rows"][0]["declined_on"] == "2026-10-05"
    assert (firm["data"] / "prospects" / pid / "engagement.json").exists()
    rows = [r["action"] for r in ledger(firm) if r["case"] == f"prospect:{pid}"]
    assert "declined" in rows


def ok_bytes(srv, who, path) -> bytes:
    status, raw = call(srv, who, path)
    assert status == 200, (path, status, raw[:200])
    return raw


# -- a protected kind ------------------------------------------------------------------------------------------------------------------


def test_a_prospect_of_a_protected_kind_is_restricted_from_the_start_and_is_the_one_answer_a_made_up_id_gets(server, firm):
    made = ok(server, "jane", "/api/prospect-new", CALL | {"name": "Rosa Exemplo Prospecto", "kind": "vawa", "email": "rosa.prospecto@example.com"})
    pid = made["id"]
    assert made["restricted"] and "VAWA" in made["law"] and "no invitation" in made["note"].lower()
    folder = firm["data"] / "prospects" / pid
    assert restricted.is_restricted(folder) and (folder / "access.json").exists()
    assert json.loads((folder / "access.json").read_text(encoding="utf-8"))["marked"]["reason"].startswith("Added as VAWA self-petition")
    # Jane took the call, and may not open it now: the page, the list, the CSV and every change answer as for an id that is nothing
    nothing = call(server, "jane", "/api/prospect?prospect=nobody-here")
    assert nothing[0] == 404
    assert call(server, "jane", "/api/prospect?prospect=" + pid) == nothing
    assert call(server, "jane", f"/api/prospect-letter.pdf?prospect={pid}&id=L1") == call(server, "jane", "/api/prospect-letter.pdf?prospect=nobody-here&id=L1")
    for action in ("note", "task", "apply", "send", "link", "answers", "waiting", "decline", "nonsense"):
        a = call(server, "jane", "/api/prospect-change", {"prospect": pid, "action": action, "text": "x", "title": "x", "date": "2026-10-12", "reason": "x"})
        b = call(server, "jane", "/api/prospect-change", {"prospect": "nobody-here", "action": action, "text": "x", "title": "x", "date": "2026-10-12", "reason": "x"})
        assert a == b and a[0] == 404, action
    for route in ("/api/prospects", "/api/prospects.csv"):
        got = call(server, "jane", route)
        assert got[0] == 200 and b"Rosa" not in got[1] and pid.encode() not in got[1], route
    as_attorney = ok(server, "sam", "/api/prospects")
    assert [r["id"] for r in as_attorney["rows"]] == [pid]
    # the people an attorney names may open it; the first thing it never does is message the person by itself
    assert call(server, "kim", "/api/prospect?prospect=" + pid)[0] == 404
    restricted.name_person(folder, "kim@firm.example", True, "Sam Attorney", "attorney", "Kim Paralegal")
    assert call(server, "kim", "/api/prospect?prospect=" + pid)[0] == 200
    sent = change(server, "sam", pid, "send")
    assert sent["delivery"]["status"] == "hand" and not (firm["portal"] / "outbox.jsonl").exists()
    # Successful current-principal handover is covered by the separate pending adapter node below.
    # every opening is in the view log with the restricted mark
    views = [json.loads(x) for x in server["app"].views_log.read_text(encoding="utf-8").splitlines()]
    assert any(v["client"] == f"prospect:{pid}" and v["kind"] == "prospect" and v.get("restricted") for v in views)
    # a restricted prospect's tasks are on no list of anyone not named on it
    change(server, "sam", pid, "task", title="Call Rosa on the safe phone", date="2026-10-06", who="", who_name="")
    for route in ("/api/work?owner=paralegal", "/api/deadlines?page=1","/api/month?month=2026-10&scope=firm"):
        assert "safe phone" not in call(server, "jane", route)[1].decode(), route
        assert "safe phone" in call(server, "sam", route)[1].decode() or route == "/api/work?owner=paralegal", route
    assert "safe phone" in call(server, "kim", "/api/deadlines?page=1")[1].decode()
    # it becomes a client restricted too: the kind carries to the case, and nothing is invited
    found = ok(server, "sam", "/api/conflict-search", {"purpose": "add", "name": "Rosa Exemplo Prospecto"})
    added = ok(server, "sam", "/api/client-add", {"name": "Rosa Exemplo Prospecto", "phone": "(555) 010-4444", "language": "es", "consent": {}, "prospect": pid, "track": "vawa",
                                                  "filing": "i485", "conflict": {"search": found["id"], "decision": "none"}})
    assert added["restricted"] and restricted.is_restricted(firm["clients"] / "rosa-exemplo-prospecto")


def test_a_protected_prospect_current_attorney_hands_over_consent_only_access(server, firm):
    """Required staff adapter node: no legacy missing-actor fallback."""
    made = ok(server, "sam", "/api/prospect-new", CALL | {"name": "Rosa Exemplo Prospecto", "kind": "vawa", "email": "rosa.prospecto@example.com"})
    pid = made["id"]
    assert made["restricted"]
    assert call(server, "jane", "/api/prospect-change", {"prospect": pid, "action": "link"})[0] == 404
    # Actual fictional wording review permits assisted signoff; it grants no client channel.
    import base64
    ref = ok(server, "sam", "/api/client-wording", {"action": "evidence", "content_base64": base64.b64encode(b'{"fictional":true,"review":"actual wording review"}').decode()})["evidence"]
    ok(server, "sam", "/api/client-wording", {"action": "attorney_review", "language": "es", "evidence": ref})
    ok(server, "sam", "/api/client-wording", {"action": "translation_review", "language": "es", "evidence": ref,
       "reviewer_name": "Fictional qualified reviewer", "qualification": "Fictional actual qualified review"})
    link = change(server, "sam", pid, "link")["link"]
    assert link["url"] and link["hours"] == 72
    with TestClient(create_app(firm["portal"], base_url="http://testserver", secure_cookies=False)) as phone:
        response = phone.get(link["url"].replace("http://testserver", ""), follow_redirects=False)
        assert response.headers["location"] == "/consent"
        assert phone.get("/api/communication/consent").json()["consent_only"] is True
        assert phone.get("/api/me").status_code == 401


# -- the list ---------------------------------------------------------------------------------------------------------------------------------


def test_the_list_is_paged_searched_and_filtered_and_the_csv_holds_every_row_and_runs_no_formula(server, firm):
    for n in range(53):
        prospects.create(firm["clients"], CALL | {"name": f"Pessoa {n:02d} Exemplo", "called_on": f"2026-09-{1 + n % 28:02d}", "email": f"p{n}@example.com"}, "Jane Paralegal")
    prospects.create(firm["clients"], CALL | {"name": "=HYPERLINK Exemplo", "email": "formula@example.com"}, "Jane Paralegal")
    first = ok(server, "jane", "/api/prospects")
    assert first["total"] == 54 and first["pages"] == 2 and len(first["rows"]) == 50 and first["page"] == 1 and first["per_page"] == 50
    second = ok(server, "jane", "/api/prospects?page=2")
    assert len(second["rows"]) == 4 and not {r["id"] for r in first["rows"]} & {r["id"] for r in second["rows"]}
    assert ok(server, "jane", "/api/prospects?page=99")["page"] == 2
    assert ok(server, "jane", "/api/prospects?q=pessoa%2007")["total"] == 1 and ok(server, "jane", "/api/prospects?stage=waiting")["total"] == 0
    assert call(server, "jane", "/api/prospects?stage=chef")[0] == 400 and call(server, "jane", "/api/prospects?page=x")[0] == 400
    # the newest call first
    days = [r["called_on"] for r in first["rows"]]
    assert days == sorted(days, reverse=True)
    status, raw = call(server, "jane", "/api/prospects.csv")
    assert status == 200
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows[0][:3] == ["Name", "Phone", "Email"] and len(rows) == 55
    assert any(r[0] == "'=HYPERLINK Exemplo" for r in rows)  # a name that starts like a formula is written as text
    assert all(len(r) == 12 for r in rows)
    assert call(server, "jane", "/api/prospects.csv?stage=new")[0] == 200


def test_the_stages_follow_the_record():
    base = {"questions_sent": None, "waiting": None, "declined": None, "became_client": None, "typed": {}}
    assert prospects.stage(base, {}) == "new"
    assert prospects.stage(base | {"questions_sent": {"at": "x"}}, {}) == "answering"
    assert prospects.stage(base, {"status": "started"}) == "answering" and prospects.stage(base | {"typed": {"a": {}}}, {}) == "answering"
    assert prospects.stage(base, {"status": "submitted"}) == "waiting" and prospects.stage(base | {"waiting": {"by": "x"}}, {"status": "started"}) == "waiting"
    assert prospects.stage(base | {"became_client": {"id": "x"}, "waiting": {"by": "x"}}, {"status": "submitted"}) == "client"
    assert prospects.stage(base | {"declined": {"on": "x"}, "became_client": {"id": "x"}}, {}) == "declined"


def test_waiting_for_the_attorney_is_marked_by_anyone_on_staff(server, firm):
    pid = new(server)
    page = change(server, "jane", pid, "waiting")
    assert page["stage"] == "waiting" and page["waiting"]["by"] == "Jane Paralegal"
    assert [x["name"] for x in ok(server, "sam", "/api/work?owner=attorney")["prospects"]] == [NAME]
    assert ledger(firm)[-1]["what"] == "Marked ready for the attorney"


# -- the verifier's findings (brief I4, after verification) -------------------------------------------------------------------------


def test_a_prospects_id_has_a_form_of_its_own_and_never_a_clients_so_no_row_of_it_lands_on_a_clients_history(server, firm):
    """A restricted VAWA prospect whose name makes the id of an existing client (case-ana): nothing written for it is keyed by that id."""
    made = ok(server, "sam", "/api/prospect-new", CALL | {"name": "Case Ana", "kind": "vawa", "email": "caseana@example.com", "phone": "(555) 010-3434"})
    pid = made["id"]
    assert pid == "prospect-case-ana" and pid != "case-ana"
    approve()
    change(server, "sam", pid, "decline", reason="Not a matter the firm takes.")
    change(server, "sam", pid, "note", text="Called again.")
    mine = [r for r in ledger(firm) if r["case"] and r["case"].endswith("case-ana")]
    assert {r["case"] for r in mine} == {"prospect:prospect-case-ana"} and {"restricted", "declined", "note_added"} <= {r["action"] for r in mine}
    assert [r for r in ledger(firm) if r["case"] == "case-ana"] == []  # the client's own history holds nothing of it
    shown = json.dumps(ok(server, "jane", "/api/case_events?client=case-ana&page=1"))
    assert "declined" not in shown and "Restricted the case from the start" not in shown
    # an id a client has (a case folder, a client held before its case file, a client in the portal) is never given: the next free one is
    (firm["clients"] / "prospect-bob-exemplo").mkdir()  # a client folder holding only a conflict check
    firm["store"].add_client("prospect-ines-exemplo", "Prospect Ines Exemplo", email="ines@example.com", language="pt")
    assert prospects.new_id(firm["clients"], "Bob Exemplo") == "prospect-bob-exemplo-2"
    assert prospects.new_id(firm["clients"], "Ines Exemplo", firm["portal"]) == "prospect-ines-exemplo-2"
    assert prospects.new_id(firm["clients"], "Ines Exemplo") == "prospect-ines-exemplo"  # without the portal's list it is the folders that decide
    # the restriction record is in the prospect's own folder; a person named on it is named there (the ledger names the prospect, never the bare id)
    assert (firm["data"] / "prospects" / pid / "access.json").exists() and not (firm["clients"] / "case-ana" / "access.json").exists()
    restricted.name_person(firm["data"] / "prospects" / pid, "kim@firm.example", True, "Sam Attorney", "attorney", "Kim Paralegal")
    assert [r["case"] for r in ledger(firm) if r["action"] == "named"] == ["prospect:prospect-case-ana"]


def test_a_prospect_that_becomes_a_restricted_client_is_closed_the_same_way_in_every_view(server, firm):
    pid = new(server)
    change(server, "jane", pid, "answers", answers={"given_name": "Lia", "fc_asking": "My husband hits me SECRETFREE"})
    change(server, "jane", pid, "note", text="SECRETNOTE about the abuse")
    change(server, "jane", pid, "apply", question="vawa.abuse", answer="yes")
    assert call(server, "jane", "/api/prospect?prospect=" + pid)[0] == 200
    found = ok(server, "sam", "/api/conflict-search", {"purpose": "add", "name": NAME})
    added = ok(server, "sam", "/api/client-add", {"name": NAME, "phone": CALL["phone"], "email": CALL["email"], "language": "es", "consent": {}, "prospect": pid, "track": "vawa",
                                                  "filing": "i485", "conflict": {"search": found["id"], "decision": "none"}})
    assert added["restricted"] and restricted.is_restricted(firm["clients"] / added["id"])
    nothing = call(server, "jane", "/api/prospect?prospect=nobody-here")
    assert nothing[0] == 404 and call(server, "jane", "/api/prospect?prospect=" + pid) == nothing and call(server, "jane", "/api/case-notes?client=" + added["id"]) == nothing
    for route in ("/api/prospects", "/api/prospects.csv"):
        text = call(server, "jane", route)[1].decode("utf-8-sig")
        assert added["id"] not in text and NAME not in text, route
    assert ok(server, "jane", "/api/prospects")["counts"]["client"] == 0  # not even counted for someone who may not open it
    assert not ok(server, "jane", "/api/prospects")["rows"] and ok(server, "sam", "/api/prospects")["rows"][0]["became_client"] == added["id"]
    # the prospect's own record is closed too, not only the gate asking the case
    assert restricted.is_restricted(firm["data"] / "prospects" / pid)
    assert call(server, "sam", "/api/prospect?prospect=" + pid)[0] == 200


def test_an_attorneys_mark_on_the_case_later_closes_the_prospect_too(server, firm):
    pid = new(server)
    change(server, "jane", pid, "answers", answers={"given_name": "Lia", "fc_asking": "ABOUT THE MATTER"})
    found = ok(server, "jane", "/api/conflict-search", {"purpose": "add", "name": NAME})
    added = ok(server, "jane", "/api/client-add", {"name": NAME, "phone": CALL["phone"], "language": "es", "consent": {}, "prospect": pid, "filing": "i485",
                                                   "conflict": {"search": found["id"], "decision": "none"}})
    assert call(server, "jane", "/api/prospect?prospect=" + pid)[0] == 200 and "Became a client" in call(server, "jane", "/api/prospects")[1].decode()
    restricted.mark(firm["clients"] / added["id"], True, "A minor's case.", "Sam Attorney", "attorney")
    nothing = call(server, "jane", "/api/prospect?prospect=nobody-here")
    assert call(server, "jane", "/api/prospect?prospect=" + pid) == nothing
    assert not ok(server, "jane", "/api/prospects")["rows"] and added["id"] not in call(server, "jane", "/api/prospects.csv")[1].decode("utf-8-sig")
    restricted.name_person(firm["clients"] / added["id"], "kim@firm.example", True, "Sam Attorney", "attorney", "Kim Paralegal")  # named on the case: opens the prospect too
    assert call(server, "kim", "/api/prospect?prospect=" + pid)[0] == 200


def test_a_restricted_prospect_closes_the_case_before_anything_of_it_is_written_and_its_named_people_come_along(server, firm):
    pid = ok(server, "sam", "/api/prospect-new", CALL | {"kind": "vawa", "email": "r@example.com"})["id"]
    restricted.name_person(firm["data"] / "prospects" / pid, "kim@firm.example", True, "Sam Attorney", "attorney", "Kim Paralegal")
    found = ok(server, "sam", "/api/conflict-search", {"purpose": "add", "name": NAME})
    # another track at the front desk: the case is closed anyway, because the first call was
    added = ok(server, "sam", "/api/client-add", {"name": NAME, "phone": CALL["phone"], "language": "es", "consent": {}, "prospect": pid, "track": "sij", "filing": "i485",
                                                  "conflict": {"search": found["id"], "decision": "none"}})
    case = firm["clients"] / added["id"]
    assert added["restricted"] and added["carried_restriction"] and restricted.is_restricted(case)
    assert [p["email"] for p in restricted.record(case)["people"]] == ["kim@firm.example"]
    assert call(server, "kim", "/api/case-notes?client=" + added["id"])[0] == 200 and call(server, "jane", "/api/case-notes?client=" + added["id"])[0] == 404
    # the ledger: the case's restriction row comes before anything else of the case (the record first, then the rest)
    rows = [r for r in ledger(firm) if r["case"] == added["id"]]
    assert rows[0]["kind"] == "access" and rows[0]["action"] == "restricted"


def test_a_correction_keeps_pointing_at_the_note_it_corrects_when_the_notes_go_to_the_case(server, firm):
    import case_notes

    pid = new(server)
    change(server, "jane", pid, "note", text="First: born 2008")
    change(server, "jane", pid, "note", text="Another note")
    change(server, "jane", pid, "note", text="Correction: born 2009", corrects="n.2")  # n.1 is the call's own note
    found = ok(server, "jane", "/api/conflict-search", {"purpose": "add", "name": NAME})
    added = ok(server, "jane", "/api/client-add", {"name": NAME, "phone": CALL["phone"], "language": "es", "consent": {}, "prospect": pid, "filing": "i485",
                                                   "conflict": {"search": found["id"], "decision": "none"}})
    case = firm["clients"] / added["id"]
    notes = sorted(case_notes.notes(case), key=lambda n: int(n["id"].split(".")[1]))
    assert [n["text"] for n in notes] == ["Called about her nephew, who is 17.", "First: born 2008", "Another note", "Correction: born 2009"]
    assert notes[3]["corrects"] == notes[1]["id"] and all(n["corrects"] is None for n in notes[:3])
    assert case_notes.carry(firm["data"] / "prospects" / pid, case, pid)["notes"] == 0  # carried twice: nothing new, the references stay


def test_only_an_answer_the_clients_questionnaire_accepts_is_carried_and_i_would_rather_not_say_is_there(server, firm):
    bank = all_questions(bank_for({"filing": "first_contact"}))
    assert bank["entry_how"]["options"][-1]["value"] == "prefer_not" and bank["entry_how"]["options"][-1]["label"]["en"] == "I'd rather not say"
    assert not bank["entry_how"].get("required") and not bank["fc_family_us_who"].get("required") and "I'd rather not say" in bank["fc_family_us_who"]["help"]["en"]
    pid = new(server)
    change(server, "jane", pid, "answers", answers={"given_name": "Lia", "entry_how": "prefer_not", "fc_in_us": "Yes"})
    found = ok(server, "jane", "/api/conflict-search", {"purpose": "add", "name": NAME})
    added = ok(server, "jane", "/api/client-add", {"name": NAME, "phone": CALL["phone"], "language": "es", "consent": {}, "prospect": pid, "filing": "i485",
                                                   "conflict": {"search": found["id"], "decision": "none"}})
    carried = firm["store"].answers(added["id"])
    assert carried["given_name"] == "Lia" and "entry_how" not in carried  # the green card's question has no such choice: it stays with the call


def test_the_export_holds_a_prospects_answers_even_when_nobody_opened_the_prospect(firm):
    import zipfile

    sys.path.insert(0, str(REPO / "tools"))
    import export_firm

    rec = prospects.create(firm["clients"], CALL, "Jane Paralegal", portal_root=firm["portal"])
    st = prospects.store(firm["portal"])
    prospects.ensure_portal(st, rec, "Jane Paralegal")
    st.save_answers(rec["id"], {"given_name": "Lia", "fc_asking": "ANSWERED ON THE PHONE, NEVER OPENED"})
    assert not (firm["data"] / "prospects" / rec["id"] / "answers.json").exists()  # nobody opened it: the copy was never made
    done = export_firm.everything(export_firm.default_where(firm["clients"], firm["portal"], firm["data"] / "users.json"), who="Sam Attorney", role="attorney")
    with zipfile.ZipFile(done["path"]) as z:
        names = z.namelist()
        assert f"firm/prospects/{rec['id']}/answers.json" in names and f"firm/prospects/{rec['id']}/prospect.json" in names
        assert "NEVER OPENED" in z.read(f"firm/prospects/{rec['id']}/answers.json").decode("utf-8")
        assert not [n for n in names if "portal/prospects" in n]


def test_add_a_client_never_gives_a_client_a_prospects_form_of_id(server, firm):
    from review import front_desk

    pid = new(server)
    assert pid == "prospect-lia-exemplo-prospecto"
    name = "Prospect Lia Exemplo Prospecto"  # a name whose slug is exactly the prospect's id
    assert front_desk.new_client_id(firm["store"], firm["clients"], name) == "client-prospect-lia-exemplo-prospecto"
    found = ok(server, "jane", "/api/conflict-search", {"purpose": "add", "name": name})
    added = ok(server, "jane", "/api/client-add", {"name": name, "phone": "(555) 010-5555", "language": "pt", "consent": {}, "filing": "i485",
                                                   "conflict": {"search": found["id"], "decision": "none"}})
    assert added["id"] == "client-prospect-lia-exemplo-prospecto" and not added["id"].startswith(prospects.PREFIX)
    assert (firm["data"] / "prospects" / pid / "prospect.json").exists() and not (firm["clients"] / pid).exists()
    # a second such name steps aside again with a number, and a prospect's folder name is refused even when the prefix is not what matches
    assert front_desk.new_client_id(firm["store"], firm["clients"], name) == "client-prospect-lia-exemplo-prospecto-2"
    (firm["data"] / "prospects" / "plain-name-exemplo").mkdir()
    assert front_desk.new_client_id(firm["store"], firm["clients"], "Plain Name Exemplo") == "plain-name-exemplo-2"
