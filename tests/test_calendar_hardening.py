# ruff: noqa: F811  (the fixtures imported from the other test files are used as arguments)
"""The calendar feed after its verification (src/calendar_feed.py, src/review/server.py): a date the calendar cannot write, control characters, a built
calendar that outlives an access change, an account turned off, stable stamps. Everyone is made up (the world of tests/test_restricted.py)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import calendar_feed
from test_calendar_feed import TODAY, dated, events_of, iso, make, read_ics, titles  # noqa: F401
from test_restricted import app, call, server, sign_in, world  # noqa: F401


def test_a_date_the_calendar_cannot_write_is_left_out_and_never_drops_the_connection(server, dated, capsys):
    """A deadline dated 12/31/9999 (or 0001, or one stored by an older version) must not crash the feed for everyone who holds the address."""
    sam = sign_in(server, "sam@firm.example")
    path = make(server, sam, "firm")
    rows = [{"id": "case-ana", "summary": {"name": "ANA"}, "journey": {"deadlines": [
        {"id": "x.1", "date": "9999-12-31", "what": "Far away", "owner": "attorney"}, {"id": "x.2", "date": "0001-01-01", "what": "Long ago", "owner": "attorney"},
        {"id": "x.3", "date": iso(3), "what": "Ordinary", "owner": "attorney"}]}}]
    built = calendar_feed.build(rows, {"email": "sam@firm.example", "role": "attorney"}, "firm", lambda *a: True, "http://x", TODAY)
    assert [e["title"] for e in built] == ["Ana: Ordinary"]
    # a date that gets past build() (an event handed to ics() directly) is left out there too
    bad = {"uid": "b" * 32 + "@case-review", "date": date(9999, 12, 31), "time": None, "minutes": 60, "title": "x", "description": "", "location": None, "url": None}
    _, events = read_ics(calendar_feed.ics([bad, built[0]], "x"))
    assert len(events) == 1 and "Ordinary" in events[0]["SUMMARY"][1]
    assert "left out" in capsys.readouterr().err
    assert call(server + path)[0] == 200


def test_control_characters_never_reach_the_file():
    nasty = "Ana\x00 Exemplo\x01 \x7f\x1b[31m\tTabbed line\r\nbreak"
    event = {"uid": "c" * 32 + "@case-review", "date": date(2026, 10, 20), "time": None, "minutes": 60, "title": nasty, "description": nasty, "location": nasty,
             "url": "http://x/#\x00a"}
    raw = calendar_feed.ics([event], nasty)
    text = raw.decode("utf-8")
    assert not [c for c in text if (ord(c) < 32 and c not in "\r\n\t") or ord(c) == 127], "a control character in the file"
    _, events = read_ics(raw)
    assert events[0]["SUMMARY"][1] == "Ana Exemplo [31m\tTabbed line\nbreak"
    assert events[0]["URL"][1] == "http://x/#a"


def test_the_built_calendar_does_not_outlive_an_access_change(server, dated):
    """A paralegal un-named from a restricted case must not keep its events for the rest of the minute; nor does a role change keep an old file."""
    kim, sam = sign_in(server, "kim@firm.example"), sign_in(server, "sam@firm.example")
    path = make(server, kim)  # Kim is named on Rosa's case
    assert any(t.startswith("Rosa") for t in titles(events_of(server, path)))
    status, text = call(server + "/api/access", sam, {"client": "case-rosa", "action": "unname", "email": "kim@firm.example"})
    assert status == 200, text
    assert not any("Rosa" in t for t in titles(events_of(server, path)))  # at once, not after the minute
    status, text = call(server + "/api/access", sam, {"client": "case-rosa", "action": "name", "email": "kim@firm.example"})
    assert status == 200 and any(t.startswith("Rosa") for t in titles(events_of(server, path)))
    call(server + "/api/access", sam, {"client": "case-ana", "action": "mark", "reason": "a minor"})  # an ordinary case made restricted
    assert not any(t.startswith("Ana") for t in titles(events_of(server, path)))
    call(server + "/api/access", sam, {"client": "case-ana", "action": "unmark"})
    assert any(t.startswith("Ana") for t in titles(events_of(server, path)))
    # a role change through the Staff section is a new file: an attorney now, every case in full
    jane = make(server, sign_in(server, "jane@firm.example"))
    assert not any(t.startswith("Rosa") for t in titles(events_of(server, jane)))
    status, text = call(server + "/api/staff", sam, {"action": "role", "email": "jane@firm.example", "role": "attorney"})
    assert status == 200, text
    assert any(t.startswith("Rosa") for t in titles(events_of(server, jane)))


def test_a_turned_off_account_loses_its_addresses_for_good(server, dated, app):
    sam = sign_in(server, "sam@firm.example")
    kim_path = make(server, sign_in(server, "kim@firm.example"))
    assert call(server + kim_path)[0] == 200
    assert call(server + "/api/staff", sam, {"action": "disable", "email": "kim@firm.example"})[0] == 200
    assert call(server + kim_path)[0] == 404
    assert call(server + "/api/staff", sam, {"action": "enable", "email": "kim@firm.example"})[0] == 200
    assert call(server + kim_path)[0] == 404  # not revived by turning her on again
    assert app.feeds().status("kim@firm.example") == {"person": None, "firm": None}


def test_an_unchanged_event_keeps_its_stamp_and_a_changed_one_moves_up():
    stamps = calendar_feed.Stamps()
    event = {"uid": "d" * 32 + "@case-review", "date": date(2026, 10, 20), "time": None, "minutes": 60, "title": "One", "description": "x", "location": None, "url": None}
    t1, t2 = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc), datetime(2026, 10, 3, 12, 30, tzinfo=timezone.utc)
    first, again = read_ics(calendar_feed.ics([event], "n", t1, stamps))[1][0], read_ics(calendar_feed.ics([event], "n", t2, stamps))[1][0]
    assert first["DTSTAMP"] == again["DTSTAMP"] == ({}, "20261003T120000Z") and first["LAST-MODIFIED"] == first["DTSTAMP"] and first["SEQUENCE"] == again["SEQUENCE"]
    changed = read_ics(calendar_feed.ics([event | {"title": "Two"}], "n", t2, stamps))[1][0]
    assert changed["DTSTAMP"] == ({}, "20261003T123000Z") and int(changed["SEQUENCE"][1]) > int(first["SEQUENCE"][1])


def test_an_ended_case_is_on_no_calendar_no_month_and_sends_no_reminder(server, dated, app, monkeypatch):
    """The hook for the engagement's end states (src/engagement.py, from brief I1): calendar_feed.is_open. Without that module every case is open."""

    import clock
    import deadlines_set
    import staff_reminders
    from review.auth import Accounts

    assert calendar_feed.is_open(dated / "case-ana") is True  # no engagement module here yet: every case stays on the lists
    people = [{"email": "kim@firm.example", "name": "Kim Exemplo", "role": "paralegal"}]
    deadlines_set.add(dated / "case-ana", "Ended case deadline", iso(1), "kim@firm.example", "", "Sam Attorney", people)
    kim = sign_in(server, "kim@firm.example")
    path = make(server, kim)
    month_url = f"/api/month?month={TODAY + timedelta(days=1):%Y-%m}&scope=firm"
    assert any("Ended case deadline" in t for t in titles(events_of(server, path))) and "Ended case deadline" in call(server + month_url, kim)[1]
    (dated / "case-ana" / "engagement.json").write_text(json.dumps({"end": {"state": "closed", "on": "2026-09-01"}}), encoding="utf-8")  # the case has ended (src/engagement.py)
    app.roster.touch("case-ana")
    app.forget_feeds()
    assert not any("Ana" in t for t in titles(events_of(server, path))) and "Ended case deadline" not in call(server + month_url, kim)[1]
    # the reminders: the case has ended, so nothing is sent
    data = dated.parent
    accounts = Accounts(data / "review_users.json")
    accounts.change_password("kim@firm.example", accounts.add("kim@firm.example", "Kim Exemplo", "paralegal"), "correct horse battery staple")
    staff_reminders.set_consent(data / staff_reminders.FILE, "kim@firm.example", True, "Kim Exemplo")
    monkeypatch.setattr(clock, "_now_override", datetime(TODAY.year, TODAY.month, TODAY.day, 20, 0))  # the coming morning is tomorrow
    assert staff_reminders.nightly(dated, data) == "Staff reminders: nothing to send."
    monkeypatch.setattr(calendar_feed, "is_open", lambda case_dir: True)
    assert staff_reminders.nightly(dated, data).startswith("Staff reminders: 1 e-mail(s)")


def test_the_open_hook_asks_the_engagement_for_an_end_state_or_a_conflict_decline(dated, monkeypatch):
    """With src/engagement.py on master, is_open is False for a case with an end state (closed, declined, withdrawn, transferred) and for a client
    the conflict search declined; True for an open case and when the engagement cannot be read."""
    import engagement

    assert calendar_feed.is_open(dated / "case-ana") is True
    monkeypatch.setattr(engagement, "end_info", lambda d: {"state": "closed", "name": "Closed", "since": "2026-10-01"} if d.name == "case-ana" else None)
    assert calendar_feed.is_open(dated / "case-ana") is False
    assert calendar_feed.is_open(dated / "case-rosa") is True
    monkeypatch.setattr(engagement, "conflict_declined", lambda d: {"state": "declined", "conflict": True} if d.name == "case-rosa" else None)
    assert calendar_feed.is_open(dated / "case-rosa") is False

    def boom(d):
        raise OSError("unreadable")
    monkeypatch.setattr(engagement, "end_info", boom)
    assert calendar_feed.is_open(dated / "case-ana") is True  # a case whose end cannot be read stays on the lists
