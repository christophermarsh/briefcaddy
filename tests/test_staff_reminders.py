# ruff: noqa: F811  (the fixtures imported from the other test files are used as arguments)
"""E-mail reminders to staff (src/staff_reminders.py, the overnight run's one added call): the week before and the morning of a deadline, to the person
responsible, only with their own consent, naming the case only as the calendar feed's rule allows. Everyone is made up (the world of tests/test_restricted.py)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

import clock
import deadlines_set
import events
import overnight
import staff_reminders
from review.auth import Accounts
from test_restricted import PASSWORD, app, call, server, sign_in, world  # noqa: F401

PEOPLE = [{"email": "jane@firm.example", "name": "Jane Doe", "role": "paralegal"}, {"email": "kim@firm.example", "name": "Kim Exemplo", "role": "paralegal"},
          {"email": "sam@firm.example", "name": "Sam Attorney", "role": "attorney"}]


@pytest.fixture
def night(world, monkeypatch):
    """Staff accounts where the overnight run looks for them (data/review_users.json), and the firm's clock at 8 pm on 10/03/2026: the run is working for
    the morning of 10/04, and a week after that is 10/11."""
    data = world.parent
    accounts = Accounts(data / "review_users.json")
    for p in PEOPLE:
        accounts.change_password(p["email"], accounts.add(p["email"], p["name"], p["role"]), PASSWORD)
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 3, 20, 0))
    return world, data


def outbox(data: Path) -> list[dict]:
    path = data / "portal" / "outbox.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def staff_rows(data: Path) -> list[dict]:
    return [r for r in outbox(data) if r["to"].endswith("@firm.example")]


def consent(data: Path, *emails: str) -> None:
    for e in emails:
        staff_reminders.set_consent(data / staff_reminders.FILE, e, True, e.split("@")[0].title())


def test_nothing_is_sent_until_the_person_turns_it_on(night):
    out, data = night
    deadlines_set.add(out / "case-ana", "Due tomorrow morning", "2026-10-04", "jane@firm.example", "", "Sam Attorney", PEOPLE)
    assert staff_reminders.nightly(out, data) == "Staff reminders: nobody has turned them on."
    assert staff_rows(data) == []
    assert not staff_reminders.wants(data / staff_reminders.FILE, "jane@firm.example")  # off by default
    consent(data, "kim@firm.example")  # someone else's consent is not Jane's
    assert staff_reminders.nightly(out, data) == "Staff reminders: nothing to send." and staff_rows(data) == []


def test_the_morning_of_and_the_week_before_each_get_one_plain_email_to_the_person_responsible(night):
    out, data = night
    deadlines_set.add(out / "case-ana", "Due tomorrow morning", "2026-10-04", "jane@firm.example", "", "Sam Attorney", PEOPLE)
    deadlines_set.add(out / "case-ana", "Due in a week", "2026-10-11", "jane@firm.example", "", "Sam Attorney", PEOPLE)
    deadlines_set.add(out / "case-ana", "Due the day between", "2026-10-07", "jane@firm.example", "", "Sam Attorney", PEOPLE)  # neither: no reminder
    deadlines_set.add(out / "case-ana", "Nobody is responsible", "2026-10-04", "", "", "Sam Attorney", PEOPLE)  # nobody named: nothing is guessed
    deadlines_set.add(out / "case-ana", "Kim's, who did not agree", "2026-10-04", "kim@firm.example", "", "Sam Attorney", PEOPLE)
    deadlines_set.add(out / "case-bia", "Another case, same morning", "2026-10-04", "jane@firm.example", "", "Sam Attorney", PEOPLE)
    consent(data, "jane@firm.example")
    line = staff_reminders.nightly(out, data)
    assert line == "Staff reminders: 2 e-mail(s) for 3 deadline(s), 2 waiting in the outbox (no mail server)."
    rows = staff_rows(data)
    assert [r["to"] for r in rows] == ["jane@firm.example"] * 2 and {r["channel"] for r in rows} == {"email"}
    today = next(r for r in rows if "today" in r["subject"])
    week = next(r for r in rows if "in a week" in r["subject"])
    assert today["subject"].endswith("your deadlines today (10/04/2026)") and week["subject"].endswith("your deadlines in a week (10/11/2026)")
    assert "Hello Jane Doe," in today["body"] and "These deadlines are due today:" in today["body"]
    assert "- Due tomorrow morning (Ana Clara Exemplo Souza)" in today["body"] and "- Another case, same morning (Beatriz Exemplo Lima)" in today["body"]
    assert "Due in a week" in week["body"] and "on 10/11/2026" in week["body"]
    for r in rows:
        text = r["subject"] + r["body"]
        assert "Due the day between" not in text and "Nobody is responsible" not in text and "Kim's" not in text
        assert "—" not in text and " -- " not in text and ".json" not in text and "set." not in text  # plain words: no file names, ids or codes
        assert "turn these reminders off under Settings, My calendar" in r["body"]
    # a second run the same night sends nothing twice
    assert staff_reminders.nightly(out, data) == "Staff reminders: nothing to send." and len(staff_rows(data)) == 2
    # nothing goes to a client
    assert not [r for r in outbox(data) if r["to"] in ("rosa@example.com", "nova@example.com")]


def test_a_deadline_the_product_worked_out_reaches_whoever_is_named_on_it(night):
    out, data = night
    import journey
    from factgraph import FactGraph
    from test_journey import _notice

    ana = out / "case-ana"
    g = FactGraph.load(ana / "fact_graph.json")
    _notice(g, "rfe.pdf", "IOE0999000004", "I-485", "rfe", "2026-09-30", due="2026-10-11")
    g.save(ana / "fact_graph.json")
    rfe = next(d for d in journey.journey(ana, datetime(2026, 10, 3).date())["deadlines"] if d["what"].startswith("Answer the request for evidence"))
    deadlines_set.assign(ana, rfe["id"], "sam@firm.example", "Jane Doe", PEOPLE)
    consent(data, "sam@firm.example")
    assert staff_reminders.nightly(out, data).startswith("Staff reminders: 1 e-mail(s) for 1 deadline(s)")
    body = staff_rows(data)[0]["body"]
    assert staff_rows(data)[0]["to"] == "sam@firm.example" and "Answer the request for evidence on I-485 IOE0999000004 (Ana Clara Exemplo Souza)" in body


def test_a_restricted_case_is_left_out_for_a_person_who_may_not_open_it_and_named_for_one_who_may(night):
    out, data = night
    import restricted

    restricted.name_person(out / "case-rosa", "kim@firm.example", True, "Sam Attorney", "attorney", "Kim Exemplo")
    deadlines_set.add(out / "case-rosa", "Secret deadline words", "2026-10-04", "jane@firm.example", "", "Kim Exemplo", PEOPLE)  # Jane is not named on the case
    deadlines_set.add(out / "case-rosa", "Kim's own secret deadline", "2026-10-04", "kim@firm.example", "", "Kim Exemplo", PEOPLE)
    deadlines_set.add(out / "case-rosa", "Attorney's deadline", "2026-10-04", "sam@firm.example", "", "Kim Exemplo", PEOPLE)
    consent(data, "jane@firm.example", "kim@firm.example", "sam@firm.example")
    line = staff_reminders.nightly(out, data)
    assert "Rosa" not in line and "case-rosa" not in line
    by = {r["to"]: r["body"] for r in staff_rows(data)}
    assert "jane@firm.example" not in by  # her only deadline is on a case she may not open: nothing is sent, and nothing says it exists
    assert not any(s in json.dumps(staff_rows(data)) for s in ("Secret deadline words", "VAWA", "restricted"))
    assert "Kim's own secret deadline (Rosa Exemplo)" in by["kim@firm.example"]  # named on the case: named in the e-mail
    assert "Attorney's deadline (Rosa Exemplo)" in by["sam@firm.example"]  # every attorney may open every case


def test_the_overnight_run_calls_it_once_and_it_can_be_switched_off(night, monkeypatch):
    out, data = night
    source = (Path(overnight.__file__)).read_text(encoding="utf-8")
    assert source.count("staff_reminders_night(out_root, data_root)") == 1
    deadlines_set.add(out / "case-ana", "Due tomorrow morning", "2026-10-04", "jane@firm.example", "", "Sam Attorney", PEOPLE)
    consent(data, "jane@firm.example")
    monkeypatch.setenv("I485_STAFF_REMINDERS", "0")
    assert overnight.staff_reminders_night(out, data) == "Staff reminders: switched off." and staff_rows(data) == []
    monkeypatch.delenv("I485_STAFF_REMINDERS")
    assert overnight.staff_reminders_night(out, data).startswith("Staff reminders: 1 e-mail(s)")
    # a run that breaks says so and goes on
    monkeypatch.setattr(staff_reminders, "nightly", lambda *a: 1 / 0)
    assert overnight.staff_reminders_night(out, data) == "Staff reminders: couldn't run (ZeroDivisionError); they run again tomorrow night."


def test_the_run_works_for_the_coming_morning(night, monkeypatch):
    for hour, want in ((6, "2026-10-03"), (11, "2026-10-03"), (12, "2026-10-04"), (20, "2026-10-04")):
        monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 3, hour, 0))
        assert staff_reminders.target_day().isoformat() == want


def test_a_prospects_task_and_a_new_clients_task_are_reminded_too_and_a_restricted_prospects_is_left_out(night):
    """Tasks (src/case_notes.py) that no case timeline holds: an open prospect's, and a client's who has no case file yet (brief I4)."""
    import case_notes
    import prospects

    out, data = night
    call_ = {"phone": "(555) 010-4444", "language": "pt"}
    lia = prospects.create(out, call_ | {"name": "Lia Exemplo Prospecto"}, "Sam Attorney")
    case_notes.add_task(data / "prospects" / lia["id"], "Call Lia back", "2026-10-04", "jane@firm.example", "", "Sam Attorney", PEOPLE, prospect=True)
    rosa = prospects.create(out, call_ | {"name": "Rosa Exemplo Prospecto", "kind": "vawa"}, "Sam Attorney")  # restricted: Jane is not named on it
    case_notes.add_task(data / "prospects" / rosa["id"], "Call Rosa on the safe phone", "2026-10-04", "jane@firm.example", "", "Sam Attorney", PEOPLE, prospect=True)
    (out / "new-client").mkdir()  # a client with no case file yet
    case_notes.add_task(out / "new-client", "Send the new client the list", "2026-10-04", "jane@firm.example", "", "Sam Attorney", PEOPLE)
    consent(data, "jane@firm.example")
    staff_reminders.nightly(out, data)
    [row] = staff_rows(data)
    assert "Call Lia back" in row["body"] and "Lia Exemplo Prospecto" in row["body"] and "Send the new client the list" in row["body"]
    assert "safe phone" not in row["body"] and "Rosa" not in row["body"]


def test_the_switch_is_each_persons_own_on_my_calendar_and_leaves_a_ledger_row(server, app):
    jane = sign_in(server, "jane@firm.example")
    assert json.loads(call(server + "/api/calendar", jane)[1])["reminders"] is False
    status, text = call(server + "/api/calendar", jane, {"action": "reminders", "on": True})
    assert status == 200 and json.loads(text)["reminders"] is True
    assert staff_reminders.wants(app.data_root.parent / staff_reminders.FILE, "jane@firm.example")
    assert not staff_reminders.wants(app.data_root.parent / staff_reminders.FILE, "kim@firm.example")
    mine = [r for r in events.rows(events.base_path(app.data_root.parent)) if r["action"].startswith("reminders_")][-1]
    assert mine["who"] == "Jane Doe" and mine["what"] == "Jane Doe turned deadline reminders on for themselves" and "jane@" not in json.dumps(mine)
    assert json.loads(call(server + "/api/calendar", jane, {"action": "reminders", "on": False})[1])["reminders"] is False
