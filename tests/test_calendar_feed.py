# ruff: noqa: F811  (the fixtures imported from test_restricted are used as arguments)
"""The calendar feed (src/calendar_feed.py): the ICS file a person subscribes to, the tokened address, and who may see which case in it.

The file is read back by a reader written here (RFC 5545 unfolding, text unescaping), not by the code that wrote it. Everyone is made up:
the made-up restricted world of tests/test_restricted.py (Ana: an ordinary case; Rosa: a VAWA case, restricted by law; Jane and Kim, paralegals;
Sam, an attorney), with dates added to it: a hearing, a biometrics appointment, an interview, a deadline a person set.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import calendar_feed
import clock
import journey
from factgraph import FactGraph
from test_journey import _notice
from test_restricted import PASSWORD, TODAY, app, call, server, sign_in, world  # noqa: F401 -- the made-up restricted world and its review app
import schema_path

HEARING = "Secret Hearing Court Exemplo"  # the restricted case's court: it must never reach someone not named on the case
BOSTON = "Boston Immigration Court"
RULE = "Answer the request for evidence on I-485 IOE0999000004"


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


@pytest.fixture
def dated(world):
    """The world with dates: Ana has a hearing at the Boston court, a biometrics appointment, an interview and a request for evidence due;
    Rosa (restricted) has a hearing at a made-up court; Kim is named on Rosa's case."""
    ana, rosa = world / "case-ana", world / "case-rosa"
    g = FactGraph.load(ana / "fact_graph.json")
    _notice(g, "rfe.pdf", "IOE0999000004", "I-485", "rfe", iso(-3), due=iso(12))
    _notice(g, "bio.pdf", "IOE0999000004", "I-485", "biometrics", iso(-2), appointment=f"{iso(10)} 10:00 AM", where="Boston Application Support Center")
    _notice(g, "int.pdf", "IOE0999000005", "I-485", "interview", iso(-1), appointment=f"{iso(30)} 2:30 PM")
    g.save(ana / "fact_graph.json")
    journey.mark(ana, "hearing", "Sam Attorney", value={"date": iso(20), "time": "9:00 AM", "kind": "Master calendar", "court": BOSTON, "judge": "Exemplo"})
    journey.mark(rosa, "hearing", "Sam Attorney", value={"date": iso(21), "time": "11:00 AM", "kind": "Individual (merits)", "court": HEARING})
    import restricted

    restricted.name_person(rosa, "kim@firm.example", True, "Sam Attorney", "attorney", "Kim Exemplo")
    return world


# -- the reader: RFC 5545, written for the test ---------------------------------------------------------------------


def read_ics(raw: bytes) -> tuple[dict[str, str], list[dict[str, tuple[dict[str, str], str]]]]:
    """(the calendar's own properties, each event's properties). Fails on anything the standard does not allow: a line break that is not CRLF, a line
    over 75 octets, a property that does not parse, a missing END."""
    text = raw.decode("utf-8")
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", ""), "lines end with CRLF, and only with CRLF"
    physical = text[:-2].split("\r\n")
    assert all(len(line.encode("utf-8")) <= 75 for line in physical), "no physical line is longer than 75 octets"
    lines: list[str] = []
    for line in physical:
        if line.startswith((" ", "\t")):
            lines[-1] += line[1:]  # a folded line continues the one before
        else:
            lines.append(line)
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-1] == "END:VCALENDAR"
    calendar: dict[str, str] = {}
    events: list[dict] = []
    current: dict | None = None
    for line in lines[1:-1]:
        if line == "BEGIN:VEVENT":
            assert current is None
            current = {}
        elif line == "END:VEVENT":
            assert current is not None
            events.append(current)
            current = None
        else:
            m = re.fullmatch(r"([A-Z-]+)((?:;[A-Z-]+=[^;:]+)*):(.*)", line)
            assert m, f"not a content line: {line!r}"
            params = dict(p.split("=", 1) for p in m[2].split(";") if p)
            value = m[3]
            if m[1] in ("SUMMARY", "DESCRIPTION", "LOCATION", "X-WR-CALNAME"):
                value = re.sub(r"\\([\\;,nN])", lambda x: "\n" if x[1] in "nN" else x[1], value)
            (current if current is not None else calendar)[m[1]] = (params, value) if current is not None else value
    assert current is None
    return calendar, events


def feed(base: str, path: str, cookie: str | None = None):
    status, text = call(base + path, cookie)
    return status, text


def make(base: str, cookie: str, kind: str = "person") -> str:
    status, text = call(base + "/api/calendar", cookie, {"action": "make", "kind": kind})
    assert status == 200, text
    return json.loads(text)["path"]


def events_of(base: str, path: str) -> list[dict]:
    status, text = feed(base, path)
    assert status == 200, text
    return read_ics(text.encode("utf-8"))[1]


def titles(events) -> list[str]:
    return [e["SUMMARY"][1] for e in events]


# -- the file ----------------------------------------------------------------------------------------------------------


def test_the_firm_feed_is_a_valid_calendar_with_every_kind_of_event(server, dated):
    sam = sign_in(server, "sam@firm.example")
    path = make(server, sam, "firm")
    assert re.fullmatch(r"/calendar/[A-Za-z0-9_-]{43}\.ics", path)  # 32 random bytes
    status, text = call(server + path)
    assert status == 200
    calendar, events = read_ics(text.encode("utf-8"))
    assert calendar["VERSION"] == "2.0" and calendar["PRODID"].startswith("-//") and calendar["METHOD"] == "PUBLISH"
    for e in events:
        assert {"UID", "DTSTAMP", "DTSTART", "DTEND", "SUMMARY"} <= set(e), e
        assert re.fullmatch(r"[0-9a-f]{32}@case-review", e["UID"][1])
    assert len({e["UID"][1] for e in events}) == len(events), "every event has its own UID"
    by_title = {e["SUMMARY"][1]: e for e in events}
    # a deadline: all day, with the rule sentence and the link to the case page in its description
    rfe = next(e for t, e in by_title.items() if "request for evidence" in t.lower() and t.startswith("Ana"))
    assert rfe["DTSTART"] == ({"VALUE": "DATE"}, iso(12).replace("-", "")) and rfe["DTEND"] == ({"VALUE": "DATE"}, iso(13).replace("-", ""))
    assert RULE in rfe["DESCRIPTION"][1] and re.search(r"http://127\.0\.0\.1:\d+/#case-ana", rfe["DESCRIPTION"][1])
    assert rfe["URL"][1].endswith("/#case-ana")
    # a hearing: timed, in UTC, the court's address (EOIR's page) in the location
    hearing = next(e for t, e in by_title.items() if "Master calendar hearing" in t)
    start = datetime(*[int(x) for x in iso(20).split("-")], 9, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    assert hearing["DTSTART"] == ({}, start.strftime("%Y%m%dT%H%M%SZ")) and hearing["DTEND"][1] == (start + timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ")
    assert BOSTON in hearing["LOCATION"][1] and "15 New Sudbury Street" in hearing["LOCATION"][1] and "Boston, MA 02203" in hearing["LOCATION"][1]
    assert "Judge Exemplo" in hearing["DESCRIPTION"][1]
    # a biometrics appointment and an interview: timed, 10:00 AM and 2:30 PM in the firm's zone
    bio = next(e for t, e in by_title.items() if "Biometrics appointment" in t)
    assert bio["DTSTART"][1] == datetime(*[int(x) for x in iso(10).split("-")], 10, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    assert bio["LOCATION"][1] == "Boston Application Support Center"
    interview = next(e for t, e in by_title.items() if t.startswith("Ana") and "Interview" in t)
    assert interview["DTSTART"][1] == datetime(*[int(x) for x in iso(30).split("-")], 14, 30, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def test_text_is_escaped_and_long_lines_are_folded_without_splitting_a_character():
    event = {"uid": "a" * 32 + "@case-review", "date": date(2026, 10, 20), "time": None, "minutes": 60,
             "title": "Ana, Exemplo; a \\ line\nwith a break", "description": "É" * 90 + "\n\nsecond paragraph", "location": None, "url": None}
    raw = calendar_feed.ics([event], "Deadlines, for Ana", datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc))
    _, events = read_ics(raw)
    assert events[0]["SUMMARY"][1] == event["title"] and events[0]["DESCRIPTION"][1] == event["description"]
    assert b"\r\n " in raw  # folded
    assert calendar_feed.parse_time("9:00 AM") == (9, 0) and calendar_feed.parse_time("2:30 pm") == (14, 30) and calendar_feed.parse_time("12 AM") == (0, 0)
    assert calendar_feed.parse_time("14:05") == (14, 5) and calendar_feed.parse_time("afternoon") is None and calendar_feed.parse_time("13:00 PM") is None


def test_a_deadline_the_product_set_in_the_feed_carries_its_person_responsible_and_note(server, dated):
    import deadlines_set

    sam = sign_in(server, "sam@firm.example")
    path = make(server, sam, "firm")
    deadlines_set.add(dated / "case-ana", "Send the interview checklist", iso(5), "jane@firm.example", "Use the new template", "Sam Attorney",
                      [{"email": "jane@firm.example", "name": "Jane Doe", "role": "paralegal"}])
    mine = next(e for e in events_of(server, path) if "Send the interview checklist" in e["SUMMARY"][1])
    assert mine["DTSTART"] == ({"VALUE": "DATE"}, iso(5).replace("-", ""))
    assert "Responsible: Jane Doe." in mine["DESCRIPTION"][1] and "Note: Use the new template" in mine["DESCRIPTION"][1]


# -- who sees what -----------------------------------------------------------------------------------------------------


def test_a_restricted_case_is_not_on_the_calendar_of_a_paralegal_not_named_on_it_and_is_in_full_on_the_one_who_is(server, dated):
    jane, kim = sign_in(server, "jane@firm.example"), sign_in(server, "kim@firm.example")
    jane_raw = call(server + make(server, jane))[1]
    jane_events, kim_events = read_ics(jane_raw.encode("utf-8"))[1], events_of(server, make(server, kim))
    # not there at all: not a placeholder, not a date, not a word of it
    for secret in ("Rosa", "ROSA", "rosa", "case-rosa", "Individual (merits)", HEARING, "IOE0912345678", "VAWA", "restricted"):
        assert secret.lower() not in jane_raw.lower(), f"Jane's feed shows {secret!r}"
    assert iso(21).replace("-", "") not in jane_raw and "A restricted case" not in titles(jane_events)
    # Kim is named on it: she sees the case as a person who may open it does
    kim_titles = titles(kim_events)
    assert any(t.startswith("Rosa") and "Individual (merits) hearing" in t for t in kim_titles)
    assert any(HEARING in e.get("LOCATION", ({}, ""))[1] for e in kim_events) and any("/#case-rosa" in e.get("URL", ({}, ""))[1] for e in kim_events)
    assert any(e["DTSTART"][1].startswith(iso(21).replace("-", "")) for e in kim_events if e["SUMMARY"][1].startswith("Rosa"))
    # Ana's case is ordinary for both
    assert any(t.startswith("Ana") for t in titles(jane_events)) and any(t.startswith("Ana") for t in kim_titles)


def test_the_firm_feed_is_for_attorneys_and_every_attorney_may_open_every_case(server, dated):
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    status, text = call(server + "/api/calendar", jane, {"action": "make", "kind": "firm"})
    assert status == 403 and "attorney" in json.loads(text)["error"]
    firm = events_of(server, make(server, sam, "firm"))
    full = [e for e in events_of(server, make(server, sam, "firm")) if e["SUMMARY"][1].startswith("Rosa")]
    assert any("Individual (merits) hearing" in e["SUMMARY"][1] and HEARING in e["LOCATION"][1] and "/#case-rosa" in e["URL"][1] for e in full)  # in full: every attorney may open every case
    assert "A restricted case" not in titles(firm)
    state = json.loads(call(server + "/api/calendar", jane)[1])
    assert state["can_firm"] is False and state["firm"] is None


def test_a_token_opens_only_its_own_persons_view(server, dated):
    """The TOKENED route: Jane's token gives Jane's feed (the restricted case is not in it), Sam's person token gives Sam's (every case a name he may open), and a
    firm token made by Sam is not a person token. The feed is built for the token's person, never for the one who asks."""
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    jane_path, sam_path, firm_path = make(server, jane), make(server, sam), make(server, sam, "firm")
    assert len({jane_path, sam_path, firm_path}) == 3
    assert not any("Rosa" in t or "restricted" in t.lower() for t in titles(events_of(server, jane_path)))
    sam_titles = titles(events_of(server, sam_path))
    assert "A restricted case" not in sam_titles and any(t.startswith("Rosa") for t in sam_titles)
    # the cookie plays no part: Jane's token with Sam's cookie is still Jane's view
    status, text = call(server + jane_path, sam)
    assert status == 200 and "Rosa" not in text and "restricted" not in text.lower() and any(t.startswith("Ana") for t in titles(read_ics(text.encode("utf-8"))[1]))
    # Sam's own list (a person feed) holds what is his by role: not the paralegal-owned expiry or request deadlines, while the firm feed holds every one
    person, firm = titles(events_of(server, sam_path)), titles(events_of(server, firm_path))
    assert set(person) <= set(firm) and len(firm) >= len(person)


def test_a_confidential_documents_deadline_is_left_out_for_staff_not_named_on_the_case(server, dated):
    """A confidential type of document in an ordinary case: its date is for attorneys and the staff named on the case (src/restricted.py sees_confidential)."""
    deadline = {"id": "expiry.x", "date": iso(9), "what": "Passport expires: renew it", "owner": "paralegal", "expiry": {"confidential": "1367", "rule": "A rule"}}
    row = {"id": "case-ana", "summary": {"name": "ANA CLARA"}, "journey": {"deadlines": [deadline]}}
    jane = {"email": "jane@firm.example", "role": "paralegal"}

    def gate(person, case, d):
        return not (d.get("expiry") or {}).get("confidential")

    shown = calendar_feed.build([row], jane, "person", gate, "http://x", TODAY)
    assert shown == []
    assert [e["title"].startswith("Ana") for e in calendar_feed.build([row], jane, "person", lambda *a: True, "http://x", TODAY)] == [True]  # for someone who may see it


# -- the token ---------------------------------------------------------------------------------------------------------


def test_a_new_address_revokes_the_old_one_and_an_unknown_token_gets_the_answer_a_made_up_case_gets(server, dated, app):
    jane = sign_in(server, "jane@firm.example")
    old = make(server, jane)
    assert call(server + old)[0] == 200
    new = make(server, jane)
    assert new != old and call(server + new)[0] == 200
    revoked = call(server + old)
    made_up = call(server + "/calendar/" + "x" * 43 + ".ics")
    case = call(server + "/api/items?client=nobody-here", jane)
    assert revoked[0] == made_up[0] == case[0] == 404 and revoked[1] == made_up[1] == case[1]
    assert call(server + "/calendar/short.ics")[0] == 404 and call(server + "/calendar/" + "a" * 43)[0] == 404 and call(server + "/calendar/")[0] == 404
    # turning it off
    assert json.loads(call(server + "/api/calendar", jane, {"action": "revoke", "kind": "person"})[1])["person"] is None
    assert call(server + new)[0] == 404
    # an account turned off, or an attorney's firm address of someone made a paralegal: the same 404
    sam = sign_in(server, "sam@firm.example")
    firm = make(server, sam, "firm")
    assert call(server + firm)[0] == 200
    app.accounts.update("sam@firm.example", by="x", role="paralegal")
    assert call(server + firm)[0] == 404
    app.accounts.update("sam@firm.example", by="x", role="attorney")
    assert call(server + firm)[0] == 404  # the address ended when he stopped being an attorney: promoting him again does not bring it back
    assert (app.feeds().status("sam@firm.example")["firm"]) is None
    new_firm = make(server, sign_in(server, "sam@firm.example"), "firm")
    assert call(server + new_firm)[0] == 200
    app.accounts.update("sam@firm.example", by="x", active=False)
    assert call(server + new_firm)[0] == 404
    app.accounts.update("sam@firm.example", by="x", active=True)
    assert call(server + new_firm)[0] == 404  # turned off, then on: the old address stays dead


def test_the_token_is_never_kept_logged_or_in_the_ledger(server, dated, app, tmp_path):
    jane = sign_in(server, "jane@firm.example")
    path = make(server, jane)
    token = path.split("/")[2][:-4]
    call(server + path)
    stored = (app.data_root.parent / calendar_feed.FILE).read_text(encoding="utf-8")
    assert token not in stored and hashlib.sha256(token.encode()).hexdigest() in stored
    assert oct((app.data_root.parent / calendar_feed.FILE).stat().st_mode & 0o777) == "0o600"
    log = app.accounts.log_path.read_text(encoding="utf-8")
    assert token not in log and token[:12] not in log
    import events

    ledger = json.dumps(list(events.rows(events.base_path(app.data_root.parent))))
    assert token not in ledger and "calendar address was made for Jane Doe" in ledger
    state = call(server + "/api/calendar", jane)[1]
    assert set(json.loads(state)) == {"person", "firm", "can_firm", "reminders"} and token not in state  # the address is shown once, when made
    call(server + "/api/calendar", jane, {"action": "revoke", "kind": "person"})
    assert "A calendar address of Jane Doe was turned off" in json.dumps(list(events.rows(events.base_path(app.data_root.parent))))


def test_a_request_is_one_row_a_day_in_the_access_log_not_one_per_poll(server, dated, app, monkeypatch):
    jane = sign_in(server, "jane@firm.example")
    path = make(server, jane)

    def rows():
        return [json.loads(x) for x in app.accounts.log_path.read_text(encoding="utf-8").splitlines() if '"calendar_feed"' in x]

    for _ in range(5):
        assert call(server + path)[0] == 200
    assert len(rows()) == 1 and rows()[0]["email"] == "jane@firm.example" and rows()[0]["kind"] == "person"
    tomorrow = clock.today() + timedelta(days=1)
    monkeypatch.setattr(clock, "today", lambda: tomorrow)
    call(server + path)
    call(server + path)
    assert len(rows()) == 2
    assert call(server + "/calendar/" + "z" * 43 + ".ics")[0] == 404 and len(rows()) == 2  # a refused token is not a row


def test_the_feed_is_built_per_request_and_kept_a_minute(server, dated, app, monkeypatch):
    jane = sign_in(server, "jane@firm.example")
    path = make(server, jane)
    built, files = [], []
    real, real_build = app._calendar_rows, calendar_feed.build
    monkeypatch.setattr(app, "_calendar_rows", lambda: built.append(1) or real())
    monkeypatch.setattr(calendar_feed, "build", lambda *a, **k: files.append(1) or real_build(*a, **k))
    now = [1000.0]
    monkeypatch.setattr(calendar_feed, "_clock", lambda: now[0])
    first = call(server + path)[1]
    now[0] += 59
    assert call(server + path)[1] == first and len(built) == len(files) == 1
    now[0] += 2  # a minute and a second since the build
    call(server + path)
    assert len(built) == len(files) == 2
    # a different person has a file of their own, built from the same rows (every case's deadlines are read once a minute for everyone)
    other = make(server, sign_in(server, "kim@firm.example"))
    call(server + other)
    assert len(built) == 2 and len(files) == 3
    app.accounts.update("jane@firm.example", by="x", role="attorney")  # a role is part of the key: a new file
    call(server + path)
    assert len(built) == 2 and len(files) == 4


def test_eight_cold_requests_build_the_rows_and_each_file_once(server, dated, app, monkeypatch):
    import threading
    import time as _time

    paths = [make(server, sign_in(server, e)) for e in ("jane@firm.example", "kim@firm.example", "sam@firm.example")]
    built = []
    real = app._calendar_rows

    def slow():
        built.append(1)
        _time.sleep(0.5)
        return real()

    monkeypatch.setattr(app, "_calendar_rows", slow)
    app.forget_feeds()
    results = []
    threads = [threading.Thread(target=lambda p=paths[i % 3]: results.append(call(server + p)[0])) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200] * 8 and len(built) == 1  # one cold build for all of them, not eight


def test_the_console_never_holds_a_token_and_a_long_path_is_cut(server, dated, capsys):
    jane = sign_in(server, "jane@firm.example")
    path = make(server, jane)
    token = path.split("/")[2][:-4]
    capsys.readouterr()
    for _ in range(50):
        assert call(server + path)[0] == 200
    call(server + "/calendar/" + "a" * 10000 + ".ics")
    call(server + "/api/items?client=" + "b" * 10000, jane)
    err = capsys.readouterr().err
    assert token not in err and token[:10] not in err and "GET /calendar/<redacted>.ics -> 200" in err
    assert err.count("<redacted>") >= 51 and "a" * 50 not in err
    assert max(len(line) for line in err.splitlines()) < 260, "a long path is cut in the log"


def test_the_feed_needs_staff_accounts_and_answers_nothing_without_them(tmp_path):
    from review.server import ReviewApp

    root = tmp_path / "clients"
    root.mkdir()
    repo = Path(__file__).resolve().parent.parent
    bare = ReviewApp(root, schema_path.path("field_map", "i485", schema_path.schemas_in(repo)), schema_path.path("template", "i485", schema_path.schemas_in(repo)), None)
    assert bare.calendar_file("a" * 43, "http://x") is None
    with pytest.raises(LookupError):
        bare.calendar_state(None)
