# ruff: noqa: F811  (the fixtures imported from the other test files are used as arguments)
"""Deadlines a person sets on a case, and the person responsible for any deadline (src/deadlines_set.py): added on the case page, named, marked done and
never deleted, and shown wherever the product's own deadlines show. Everyone is made up (the world of tests/test_restricted.py)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

import deadlines_set
import events
import journey
from test_calendar_feed import dated, iso, make, read_ics  # noqa: F401
from test_restricted import TODAY, app, call, server, sign_in, world  # noqa: F401

PEOPLE = [{"email": "jane@firm.example", "name": "Jane Doe", "role": "paralegal"}, {"email": "sam@firm.example", "name": "Sam Attorney", "role": "attorney"}]


def post(base, cookie, body):
    status, text = call(base + "/api/deadline", cookie, body)
    return status, json.loads(text)


def ledger(app):
    return list(events.rows(events.base_path(app.data_root.parent)))


def test_a_deadline_is_added_with_its_title_date_person_and_note_and_gets_the_next_id(world):
    d = world / "case-ana"
    row = deadlines_set.add(d, "  Send the checklist  ", iso(7), "jane@firm.example", "Use the template", "Sam Attorney", PEOPLE)
    assert (row["id"], row["title"], row["date"], row["who"], row["who_name"], row["note"], row["by"], row["done"]) == \
        ("set.1", "Send the checklist", iso(7), "jane@firm.example", "Jane Doe", "Use the template", "Sam Attorney", None)
    assert deadlines_set.add(d, "Second", iso(8), "", "", "Sam Attorney", PEOPLE)["id"] == "set.2"
    saved = json.loads((d / deadlines_set.FILE).read_text(encoding="utf-8"))
    assert saved["version"] == 1 and [x["id"] for x in saved["deadlines"]] == ["set.1", "set.2"] and saved["assigned"] == {}
    j = journey.journey(d, TODAY)
    mine = next(x for x in j["deadlines"] if x["id"] == "set.1")
    assert (mine["what"], mine["date"], mine["owner"], mine["who"], mine["who_name"], mine["set"], mine["level"]) == \
        ("Send the checklist", iso(7), "paralegal", "jane@firm.example", "Jane Doe", True, "urgent")
    assert next(x for x in j["deadlines"] if x["id"] == "set.2")["owner"] == "paralegal"  # nobody named: the paralegal's by default
    assert journey.summary(j)["deadlines"][0].keys() >= {"id", "date", "what", "owner"}


def test_what_is_checked(world):
    d = world / "case-ana"
    for args, words in (((" ", iso(1), "", "", "Sam", PEOPLE), "what the deadline is for"), (("x", "", "", "", "Sam", PEOPLE), "Choose the date"),
                        (("x", "10/05/2026", "", "", "Sam", PEOPLE), "Choose the date"), (("x", iso(1), "nobody@firm.example", "", "Sam", PEOPLE), "staff list"),
                        (("x", iso(1), "", "", "", PEOPLE), "Enter your name"),
                        # a date the calendar and the month view cannot hold, and one that is not a day: said in words, never Python's error text
                        (("x", "9999-12-31", "", "", "Sam", PEOPLE), "between the years 2000 and 2100"), (("x", "0001-01-01", "", "", "Sam", PEOPLE), "between the years 2000 and 2100"),
                        (("x", "1900-06-01", "", "", "Sam", PEOPLE), "between the years 2000 and 2100"), (("x", "2101-01-01", "", "", "Sam", PEOPLE), "between the years 2000 and 2100"),
                        (("x", "2026-02-30", "", "", "Sam", PEOPLE), "That is not a date: check the day and the month")):
        with pytest.raises(ValueError, match=words):
            deadlines_set.add(d, *args)
    assert not (d / deadlines_set.FILE).exists()
    # without staff accounts the person responsible is a typed name
    assert deadlines_set.add(d, "x", iso(1), "", "", "Typed Name", [], "Pat Exemplo")["who_name"] == "Pat Exemplo"


def test_a_deadline_the_product_worked_out_gets_a_person_responsible_by_an_assignment_and_is_not_changed(dated):
    d = dated / "case-ana"
    before = journey.journey(d, TODAY)
    rfe = next(x for x in before["deadlines"] if x["what"].startswith("Answer the request for evidence"))
    assert "who" not in rfe
    deadlines_set.assign(d, rfe["id"], "sam@firm.example", "Jane Doe", PEOPLE)
    saved = json.loads((d / deadlines_set.FILE).read_text(encoding="utf-8"))
    assert saved["deadlines"] == [] and saved["assigned"][rfe["id"]]["email"] == "sam@firm.example" and saved["assigned"][rfe["id"]]["by"] == "Jane Doe"
    after = journey.journey(d, TODAY)
    again = next(x for x in after["deadlines"] if x["id"] == rfe["id"])
    assert (again["who"], again["who_name"]) == ("sam@firm.example", "Sam Attorney")
    assert {k: v for k, v in again.items() if k not in ("who", "who_name")} == {k: v for k, v in rfe.items()}  # the deadline itself is the same
    assert next(x for x in journey.summary(after)["deadlines"] if x["id"] == rfe["id"])["who_name"] == "Sam Attorney"
    deadlines_set.assign(d, rfe["id"], "", "Jane Doe", PEOPLE)  # taken off
    assert "who" not in next(x for x in journey.journey(d, TODAY)["deadlines"] if x["id"] == rfe["id"])
    with pytest.raises(ValueError, match="staff list"):
        deadlines_set.assign(d, rfe["id"], "stranger@firm.example", "Jane Doe", PEOPLE)


def test_done_hides_a_deadline_and_keeps_who_and_when_and_nothing_is_ever_deleted(world, app):
    d = world / "case-ana"
    deadlines_set.add(d, "One", iso(3), "jane@firm.example", "", "Sam Attorney", PEOPLE)
    deadlines_set.add(d, "Two", iso(4), "", "", "Sam Attorney", PEOPLE)
    deadlines_set.done(d, "set.1", "Jane Doe")
    j = journey.journey(d, TODAY)
    assert [x["id"] for x in j["deadlines"] if x.get("set")] == ["set.2"]  # hidden from the open list
    gone = j["deadlines_done"]
    assert len(gone) == 1 and (gone[0]["id"], gone[0]["what"], gone[0]["done_by"], gone[0]["who_name"]) == ("set.1", "One", "Jane Doe", "Jane Doe")
    assert gone[0]["done_at"]
    saved = json.loads((d / deadlines_set.FILE).read_text(encoding="utf-8"))
    assert [x["id"] for x in saved["deadlines"]] == ["set.1", "set.2"] and saved["deadlines"][0]["done"]["by"] == "Jane Doe"  # kept
    assert not hasattr(deadlines_set, "delete") and not hasattr(deadlines_set, "remove")
    assert deadlines_set.add(d, "Three", iso(5), "", "", "Sam Attorney", PEOPLE)["id"] == "set.3"  # an id is never used twice
    with pytest.raises(ValueError, match="done"):
        deadlines_set.assign(d, "set.1", "sam@firm.example", "Sam", PEOPLE)
    with pytest.raises(LookupError):
        deadlines_set.done(d, "set.99", "Sam")
    deadlines_set.done(d, "set.1", "Someone Else")  # done twice: the first stands
    assert json.loads((d / deadlines_set.FILE).read_text(encoding="utf-8"))["deadlines"][0]["done"]["by"] == "Jane Doe"


def test_every_change_is_one_ledger_row_that_never_carries_the_title_note_or_date(world, app):
    d = world / "case-ana"
    deadlines_set.add(d, "Confidential title words", iso(3), "jane@firm.example", "Private note words", "Sam Attorney", PEOPLE)
    deadlines_set.assign(d, "IOE.rfe.2026-10-01", "sam@firm.example", "Sam Attorney", PEOPLE)
    deadlines_set.done(d, "set.1", "Sam Attorney")
    rows = [r for r in events.rows(events.base_path(app.data_root.parent)) if r["case"] == "case-ana" and r["action"].startswith("deadline_")][-3:]  # the ledger is shared by the session
    assert [r["action"] for r in rows] == ["deadline_added", "deadline_assigned", "deadline_done"]
    text = json.dumps(rows)
    assert "Confidential" not in text and "Private" not in text and iso(3) not in text and "jane@" not in text


def test_a_deadline_shows_in_what_is_due_my_work_the_case_page_the_month_view_and_the_feed(server, dated):
    jane, kim, sam = sign_in(server, "jane@firm.example"), sign_in(server, "kim@firm.example"), sign_in(server, "sam@firm.example")
    status, j = post(server, jane, {"client": "case-ana", "action": "add", "title": "Call the consulate", "date": iso(6), "who": "kim@firm.example", "note": "Ask for the date"})
    assert status == 200
    case_page = next(x for x in j["deadlines"] if x["what"] == "Call the consulate")
    assert (case_page["who_name"], case_page["owner"], case_page["set_by"]) == ("Kim Exemplo", "paralegal", "Jane Doe")
    # the choices carry a name, a role and an id, never an e-mail address; nor does the deadline's person responsible
    assert {p["name"] for p in j["staff"]} == {"Jane Doe", "Kim Exemplo", "Sam Attorney"} and all(set(p) == {"id", "name", "role"} for p in j["staff"])
    assert "@firm.example" not in json.dumps(j) and case_page["who"] == next(p["id"] for p in j["staff"] if p["name"] == "Kim Exemplo")
    status, j = post(server, jane, {"client": "case-ana", "action": "add", "title": "By id", "date": iso(8), "who": case_page["who"]})  # the page chooses by id
    assert status == 200 and next(x for x in j["deadlines"] if x["what"] == "By id")["who_name"] == "Kim Exemplo"
    overview = json.loads(call(server + "/api/deadlines?page=1", jane)[1])
    assert any(x["what"] == "Call the consulate" and x["client"] == "case-ana" and x["who_name"] == "Kim Exemplo" for x in overview["items"])  # What's due
    kim_work = json.loads(call(server + "/api/work?owner=paralegal", kim)[1])
    jane_work = json.loads(call(server + "/api/work?owner=paralegal", jane)[1])
    assert any(x["what"] == "Call the consulate" for x in kim_work["deadlines"])  # My work: hers, she is responsible
    assert not any(x["what"] == "Call the consulate" for x in jane_work["deadlines"])  # not Jane's: someone else is named
    month = json.loads(call(server + f"/api/month?month={TODAY + timedelta(days=6):%Y-%m}&scope=firm", jane)[1])
    assert any(e["what"] == "Call the consulate" for w in month["weeks"] for c in w for e in c["events"])
    path = make(server, kim)
    events_ = read_ics(call(server + path)[1].encode())[1]
    assert any("Call the consulate" in e["SUMMARY"][1] and "Responsible: Kim Exemplo." in e["DESCRIPTION"][1] for e in events_)
    # marked done: gone from every list, kept on the case page's Done list
    status, j = post(server, kim, {"client": "case-ana", "action": "done", "id": case_page["id"]})
    assert status == 200 and not any(x["what"] == "Call the consulate" for x in j["deadlines"]) and j["deadlines_done"][0]["done_by"] == "Kim Exemplo"
    assert not any(x["what"] == "Call the consulate" for x in json.loads(call(server + "/api/deadlines?page=1", jane)[1])["items"])
    # the person responsible for a deadline the product worked out
    rfe = next(x for x in j["deadlines"] if x["what"].startswith("Answer the request for evidence"))
    status, j = post(server, jane, {"client": "case-ana", "action": "assign", "id": rfe["id"], "who": "sam@firm.example"})
    assert status == 200 and next(x for x in j["deadlines"] if x["id"] == rfe["id"])["who_name"] == "Sam Attorney"
    assert any(x["id"] == rfe["id"] for x in json.loads(call(server + "/api/work?owner=attorney", sam)[1])["deadlines"])


def test_the_deadline_route_is_gated_like_every_case_route_and_says_what_is_wrong(server, dated):
    jane, kim = sign_in(server, "jane@firm.example"), sign_in(server, "kim@firm.example")
    assert post(server, jane, {"client": "case-rosa", "action": "add", "title": "x", "date": iso(3)}) == (404, {"error": "unknown client"})  # restricted: Jane is not named
    assert post(server, jane, {"client": "nobody-here", "action": "add", "title": "x", "date": iso(3)}) == (404, {"error": "unknown client"})
    assert post(server, kim, {"client": "case-rosa", "action": "add", "title": "Named staff may", "date": iso(3)})[0] == 200
    assert post(server, kim, {"client": "case-ana", "action": "add", "title": "", "date": iso(3)})[1]["error"].startswith("Say what")
    assert post(server, kim, {"client": "case-ana", "action": "add", "title": "x", "date": iso(3), "who": "stranger@firm.example"})[0] == 400
    assert post(server, kim, {"client": "case-ana", "action": "delete", "id": "set.1"})[0] == 400  # there is no delete
