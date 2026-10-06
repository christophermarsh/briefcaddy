"""Case notes and tasks (src/case_notes.py), on every case and every prospect: a note is never edited (a correction is a new note), a task is never deleted (done keeps who and when),
tasks are in My work for their person, on What's due by date and on the calendar feed because they are written in the shape the deadlines a person sets have, a restricted case's
notes are closed by the same gate as the rest of it, and every change is one row of the ledger that never carries the text. Everyone here is made up."""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

import case_notes
import clock
import deadlines_set
import events
import journey
import prospects
import restricted
import settings
import schema_path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

PEOPLE = [{"email": "jane@firm.example", "name": "Jane Paralegal", "role": "paralegal"}, {"email": "sam@firm.example", "name": "Sam Attorney", "role": "attorney"}]


@pytest.fixture
def firm(tmp_path, monkeypatch):
    import people_world

    data = tmp_path / "data"
    clients = data / "clients"
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    for var, path in (("I485_QUERY_DB", data / "query.db"), ("I485_EVENTS", data / "events.jsonl"), ("I485_CONFLICTS", data / "conflict_checks.jsonl"), ("I485_CASES", clients),
                      ("PORTAL_DATA", data / "portal"), ("PORTAL_BASE_URL", "http://testserver"), ("I485_INDEX", data / "index.db"), ("I485_INBOX", data / "inbox")):
        monkeypatch.setenv(var, str(path))
    monkeypatch.delenv("I485_PROSPECTS", raising=False)
    people_world.make(clients)
    (data / "portal").mkdir(parents=True)
    return {"data": data, "clients": clients, "portal": data / "portal"}


@pytest.fixture
def server(firm):
    from review.auth import Accounts
    from review.server import COOKIE, ReviewApp, make_handler, serve

    accounts = Accounts(firm["data"] / "staff.json")
    for email, name, role in (("jane@firm.example", "Jane Paralegal", "paralegal"), ("kim@firm.example", "Kim Paralegal", "paralegal"),
                              ("sam@firm.example", "Sam Attorney", "attorney")):
        accounts.add(email, name, role)
    restricted.name_person(firm["clients"] / "case-rosa", "kim@firm.example", True, "Sam Attorney", "attorney", "Kim Paralegal")
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


def unfolded(feed: bytes) -> str:
    """The calendar file's text with its folded lines put back together (a line is cut at 75 octets: src/calendar_feed.py)."""
    return feed.decode("utf-8").replace("\r\n ", "")


def person(srv, email: str) -> str:
    return srv["app"].person_id(email)


def ledger(firm) -> list[dict]:
    return list(events.rows(firm["data"] / "events.jsonl"))


# -- notes -----------------------------------------------------------------------------------------------------------------------------


def test_a_note_is_never_edited_and_a_correction_is_a_new_note_that_says_which(tmp_path):
    d = tmp_path / "case"
    d.mkdir()
    first = case_notes.add_note(d, "  Client called   about the hearing.\n\nSecond line.  ", "Jane Paralegal", "paralegal")
    assert first["id"] == "n.1" and first["text"] == "Client called about the hearing.\n\nSecond line." and first["by"] == "Jane Paralegal" and first["role"] == "paralegal"
    fix = case_notes.add_note(d, "Correction: it was the bond hearing.", "Sam Attorney", "attorney", corrects="n.1")
    assert fix["id"] == "n.2" and fix["corrects"] == "n.1"
    saved = json.loads((d / case_notes.FILE).read_text(encoding="utf-8"))
    assert saved["notes"][0] == first and saved["version"] == 1  # the earlier note is exactly as it was
    assert [n["id"] for n in case_notes.notes(d)] == ["n.2", "n.1"]  # newest first
    # nothing edits or deletes: the module has no such call, and a correction of nothing is refused
    assert not [name for name in dir(case_notes) if name.startswith(("edit", "delete", "remove", "update"))]
    with pytest.raises(LookupError):
        case_notes.add_note(d, "x", "Sam Attorney", corrects="n.9")
    for bad, words in (("", "Write the note"), ("   ", "Write the note"), ("x" * 4001, "too long")):
        with pytest.raises(ValueError, match=words):
            case_notes.add_note(d, bad, "Jane Paralegal")
    with pytest.raises(ValueError, match="Enter your name"):
        case_notes.add_note(d, "x", "")


def test_the_case_page_adds_notes_and_there_is_no_way_to_change_or_delete_one(server, firm):
    first = ok(server, "jane", "/api/case-notes", {"client": "case-ana", "action": "note", "text": "Spoke with the mother."})
    assert [n["text"] for n in first["notes"]] == ["Spoke with the mother."] and first["notes"][0]["by"] == "Jane Paralegal" and first["notes"][0]["role"] == "paralegal"
    again = ok(server, "sam", "/api/case-notes", {"client": "case-ana", "action": "note", "text": "She did not mean the father.", "corrects": first["notes"][0]["id"]})
    assert [n["by"] for n in again["notes"]] == ["Sam Attorney", "Jane Paralegal"] and again["notes"][0]["corrects"] == "n.1" and again["notes"][1]["text"] == "Spoke with the mother."
    for action in ("edit", "delete", "remove", "update"):
        status, raw = call(server, "sam", "/api/case-notes", {"client": "case-ana", "action": action, "id": "n.1", "text": "changed"})
        assert status == 400 and "add a note" in json.loads(raw)["error"], action
    assert ok(server, "jane", "/api/case-notes?client=case-ana")["notes"][1]["text"] == "Spoke with the mother."
    # the ledger: one row each, the sentence never carries what was written
    rows = [r for r in ledger(firm) if r["kind"] == "notes"]
    assert [(r["action"], r["who"], r["case"]) for r in rows] == [("note_added", "Jane Paralegal", "case-ana"), ("note_corrected", "Sam Attorney", "case-ana")]
    assert "mother" not in json.dumps(rows) and "father" not in json.dumps(rows)


# -- tasks -----------------------------------------------------------------------------------------------------------------------------


def test_a_task_is_a_deadline_a_person_sets_so_every_list_that_reads_deadlines_reads_it(server, firm):
    body = {"client": "case-ana", "action": "task", "title": "Call the court clerk about the order", "date": "2026-10-08", "who": person(server, "jane@firm.example"),
            "note": "Ask for the certified copy."}
    out = ok(server, "sam", "/api/case-notes", body)
    [task] = out["tasks"]["open"]
    assert task["title"] == "Call the court clerk about the order" and task["date"] == "2026-10-08" and task["who_name"] == "Jane Paralegal" and task["by"] == "Sam Attorney"
    assert task["days_left"] == 3 and task["done"] is None and out["staff"][0]["name"] and "email" not in json.dumps(out["staff"])
    # the same record I2 wrote: a row of deadlines_set.json with the same keys, and "task": true
    saved = deadlines_set.load(firm["clients"] / "case-ana")["deadlines"][0]
    assert saved["task"] is True and saved["who"] == "jane@firm.example" and saved["id"] == "set.1" and saved["role"] == "paralegal"
    assert {"id", "title", "date", "who", "who_name", "role", "note", "by", "at", "done"} <= set(saved)
    # the case's timeline reads it as a deadline with its person, and so do My work, What's due and the month view
    day = journey.journey(firm["clients"] / "case-ana")["deadlines"]
    [mine] = [d for d in day if d["what"] == "Call the court clerk about the order"]
    assert mine["set"] and mine["task"] and mine["who"] == "jane@firm.example" and mine["owner"] == "paralegal" and mine["days_left"] == 3
    work = ok(server, "jane", "/api/work?owner=paralegal")
    assert any(d["what"] == "Call the court clerk about the order" and d["client"] == "case-ana" for d in work["deadlines"])
    assert not any(d["what"] == "Call the court clerk about the order" for d in ok(server, "sam", "/api/work?owner=attorney")["deadlines"])  # it is Jane's, not the attorney's
    due = ok(server, "sam", "/api/deadlines?page=1")["items"]
    assert [d["date"] for d in due if d["what"] == "Call the court clerk about the order"] == ["2026-10-08"]
    month = ok(server, "sam", "/api/month?month=2026-10&scope=firm")
    assert any(e["what"] == "Call the court clerk about the order" for w in month["weeks"] for c in w for e in c["events"])
    # the calendar address of Jane carries it, by its title and the person responsible
    token = server["app"].feeds().make("jane@firm.example", "person", "Jane Paralegal")
    ics = unfolded(server["app"].calendar_file(token, "http://testserver"))
    assert "Call the court clerk about the order" in ics and "Responsible: Jane Paralegal." in ics and "DTSTART;VALUE=DATE:20261008" in ics


def test_a_task_with_nobody_named_is_the_owners_and_an_unknown_person_is_refused(server, firm):
    out = ok(server, "jane", "/api/case-notes", {"client": "case-ana", "action": "task", "title": "Order the birth certificate", "date": "2026-10-09"})
    assert out["tasks"]["open"][0]["who_name"] is None
    status, raw = call(server, "jane", "/api/case-notes", {"client": "case-ana", "action": "task", "title": "x", "date": "2026-10-09", "who": "somebody-not-on-the-list"})
    assert status == 400 and "staff list" in json.loads(raw)["error"]
    for bad, words in (({"title": ""}, "Say what"), ({"date": ""}, "Choose the date"), ({"date": "2026-02-30"}, "not a date"), ({"date": "1999-01-01"}, "between the years")):
        status, raw = call(server, "jane", "/api/case-notes", {"client": "case-ana", "action": "task", "title": "x", "date": "2026-10-09"} | bad)
        assert status == 400 and words in json.loads(raw)["error"], bad
    # naming the person later
    task = out["tasks"]["open"][0]
    named = ok(server, "sam", "/api/case-notes", {"client": "case-ana", "action": "task_assign", "id": task["id"], "who": person(server, "sam@firm.example")})
    assert named["tasks"]["open"][0]["who_name"] == "Sam Attorney"
    assert any(d["what"] == "Order the birth certificate" for d in ok(server, "sam", "/api/work?owner=attorney")["deadlines"])


def test_done_keeps_who_and_when_and_a_task_is_never_deleted(server, firm):
    ok(server, "jane", "/api/case-notes", {"client": "case-ana", "action": "task", "title": "Send the packet to the client", "date": "2026-10-07"})
    out = ok(server, "jane", "/api/case-notes", {"client": "case-ana", "action": "task_done", "id": "set.1"})
    assert out["tasks"]["open"] == [] and out["tasks"]["done"][0]["done"]["by"] == "Jane Paralegal" and out["tasks"]["done"][0]["done"]["at"].startswith("2026-10-05")
    kept = deadlines_set.load(firm["clients"] / "case-ana")["deadlines"]
    assert len(kept) == 1 and kept[0]["done"]["by"] == "Jane Paralegal" and kept[0]["task"]  # the row is still in the file
    assert not any(d["what"] == "Send the packet to the client" for d in ok(server, "jane", "/api/work?owner=paralegal")["deadlines"])
    assert not any(d["what"] == "Send the packet to the client" for d in ok(server, "sam", "/api/deadlines?page=1")["items"])
    # done twice changes nothing; there is no delete; a deadline that is not a task is not reachable through the tasks
    again = ok(server, "sam", "/api/case-notes", {"client": "case-ana", "action": "task_done", "id": "set.1"})
    assert again["tasks"]["done"][0]["done"]["by"] == "Jane Paralegal"
    assert call(server, "sam", "/api/case-notes", {"client": "case-ana", "action": "task_delete", "id": "set.1"})[0] == 400
    deadlines_set.add(firm["clients"] / "case-ana", "A deadline a person set", "2026-10-20", None, "", "Sam Attorney", PEOPLE)
    status, raw = call(server, "sam", "/api/case-notes", {"client": "case-ana", "action": "task_done", "id": "set.2"})
    assert status == 404 and "No such task" in json.loads(raw)["error"]
    assert [r["action"] for r in ledger(firm) if r["kind"] == "notes"] == ["task_added", "task_done"]  # done twice is one row
    assert "packet" not in json.dumps([r for r in ledger(firm) if r["kind"] == "notes"])


# -- a prospect's tasks -------------------------------------------------------------------------------------------------------------------


def test_a_prospects_task_is_in_my_work_and_whats_due_and_on_the_calendar_but_the_prospect_is_in_no_count(server, firm):
    pid = ok(server, "jane", "/api/prospect-new", {"name": "Lia Exemplo Prospecto", "phone": "(555) 010-4444", "language": "pt"})["id"]
    ok(server, "jane", "/api/prospect-change", {"prospect": pid, "action": "task", "title": "Call Lia back in the evening", "date": "2026-10-06", "who": person(server, "jane@firm.example")})
    work = ok(server, "jane", "/api/work?owner=paralegal")
    [mine] = [d for d in work["deadlines"] if d["what"] == "Call Lia back in the evening"]
    assert mine["client"] == f"prospect:{pid}" and mine["name"] == "Lia Exemplo Prospecto" and mine["days_left"] == 1
    assert not any(d["what"] == "Call Lia back in the evening" for d in ok(server, "sam", "/api/work?owner=attorney")["deadlines"])  # it is Jane's: the attorney's list does not have it
    due = ok(server, "sam", "/api/deadlines?page=1")
    overview = ok(server, "sam", "/api/overview")
    assert [d["client"] for d in due["items"] if d["what"] == "Call Lia back in the evening"] == [f"prospect:{pid}"]
    assert all(r["id"] != pid for r in overview["clients"]) and sum(s["count"] for s in overview["stages"]) == overview["counts"]["total"]
    token = server["app"].feeds().make("jane@firm.example", "person", "Jane Paralegal")
    ics = unfolded(server["app"].calendar_file(token, "http://testserver"))
    assert "Call Lia back in the evening" in ics and "Lia Exemplo Prospecto" in ics
    # done: it leaves the lists; the row stays in the prospect's folder
    ok(server, "jane", "/api/prospect-change", {"prospect": pid, "action": "task_done", "id": "set.1"})
    assert not any(d["what"] == "Call Lia back in the evening" for d in ok(server, "jane", "/api/work?owner=paralegal")["deadlines"])
    assert deadlines_set.load(firm["data"] / "prospects" / pid)["deadlines"][0]["done"]["by"] == "Jane Paralegal"
    rows = [r for r in ledger(firm) if r["kind"] == "notes"]
    assert [r["case"] for r in rows] == [f"prospect:{pid}"] * 2 and "Lia" not in json.dumps(rows)


def test_a_prospect_that_became_a_client_or_was_declined_has_left_the_work_lists(server, firm):
    pid = ok(server, "jane", "/api/prospect-new", {"name": "Lia Exemplo Prospecto", "phone": "(555) 010-4444", "language": "pt"})["id"]
    ok(server, "jane", "/api/prospect-change", {"prospect": pid, "action": "task", "title": "A task on the call", "date": "2026-10-06"})
    assert any(d["what"] == "A task on the call" for d in ok(server, "sam", "/api/deadlines?page=1")["items"])
    rec = prospects.read(firm["data"] / "prospects" / pid)
    rec["declined"] = {"by": "Sam Attorney", "at": clock.stamp(), "on": "2026-10-05", "letter": "L1"}
    prospects._write(firm["data"] / "prospects" / pid, rec)
    assert not any(d["what"] == "A task on the call" for d in ok(server, "sam", "/api/deadlines?page=1")["items"])


def test_a_client_with_no_case_file_yet_has_notes_and_tasks_and_they_are_on_the_lists(server, firm):
    from portal.store import PortalStore

    PortalStore(firm["portal"]).add_client("new-client-exemplo", "Nova Exemplo Cliente", email="nova@example.com", language="pt")
    out = ok(server, "jane", "/api/case-notes", {"client": "new-client-exemplo", "action": "task", "title": "Send the new client the list of documents", "date": "2026-10-07",
                                                 "who": person(server, "jane@firm.example")})
    assert out["tasks"]["open"][0]["title"].startswith("Send the new client")
    ok(server, "jane", "/api/case-notes", {"client": "new-client-exemplo", "action": "note", "text": "Prefers calls after 5 pm."})
    mine = [d for d in ok(server, "jane", "/api/work?owner=paralegal")["deadlines"] if d["what"].startswith("Send the new client")]
    assert [(d["client"], d["name"]) for d in mine] == [("new-client-exemplo", "Nova Exemplo Cliente")]
    assert [d["client"] for d in ok(server, "sam", "/api/deadlines?page=1")["items"] if d["what"].startswith("Send the new client")] == ["new-client-exemplo"]
    ok(server, "jane", "/api/case-notes", {"client": "new-client-exemplo", "action": "task_done", "id": "set.1"})
    assert not [d for d in ok(server, "jane", "/api/work?owner=paralegal")["deadlines"] if d["what"].startswith("Send the new client")]


# -- the gate -----------------------------------------------------------------------------------------------------------------------------


def test_a_restricted_cases_notes_and_tasks_are_closed_like_the_rest_of_it(server, firm):
    ok(server, "sam", "/api/case-notes", {"client": "case-rosa", "action": "note", "text": "She is staying at her sister's address."})
    ok(server, "sam", "/api/case-notes", {"client": "case-rosa", "action": "task", "title": "Meet Rosa at the shelter", "date": "2026-10-06"})
    nothing_get = call(server, "jane", "/api/case-notes?client=nobody-here")
    assert nothing_get[0] == 404
    assert call(server, "jane", "/api/case-notes?client=case-rosa") == nothing_get
    for action, extra in (("note", {"text": "x"}), ("task", {"title": "x", "date": "2026-10-09"}), ("task_done", {"id": "set.1"}), ("task_assign", {"id": "set.1"})):
        a = call(server, "jane", "/api/case-notes", {"client": "case-rosa", "action": action} | extra)
        b = call(server, "jane", "/api/case-notes", {"client": "nobody-here", "action": action} | extra)
        assert a == b and a[0] == 404, action
    # nowhere in a list of Jane's: not the note, not the task
    for route in ("/api/work?owner=paralegal", "/api/overview", "/api/deadlines?page=1", "/api/month?month=2026-10&scope=firm", "/api/clients", "/api/search?q=sister"):
        text = call(server, "jane", route)[1].decode()
        assert "sister" not in text.replace('"query": "sister"', "") and "shelter" not in text, route
    # Kim, named on the case, reads and writes them
    assert ok(server, "kim", "/api/case-notes?client=case-rosa")["notes"][0]["text"].startswith("She is staying")
    assert any(d["what"] == "Meet Rosa at the shelter" for d in ok(server, "kim", "/api/deadlines?page=1")["items"])
    token = server["app"].feeds().make("jane@firm.example", "person", "Jane Paralegal")
    assert "shelter" not in server["app"].calendar_file(token, "http://testserver").decode("utf-8")
