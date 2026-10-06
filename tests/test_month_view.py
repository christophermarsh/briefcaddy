# ruff: noqa: F811  (the fixtures imported from the other test files are used as arguments)
"""The month view on What's due (review app /api/month, src/closures.py): a plain grid in the firm's zone, Monday first, the federal holidays and the
courts' closures marked from schemas/registers/court_closures.json, each day with its events, and a sentence on screen for a list that could not be read.
Everyone is made up (the world of tests/test_restricted.py)."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

import clock
import closures
import deadlines_set
import settings
from test_calendar_feed import dated, iso  # noqa: F401
from test_restricted import app, call, server, sign_in, world  # noqa: F401

FILE = json.loads(closures.FILE.read_text(encoding="utf-8"))
PEOPLE = [{"email": "jane@firm.example", "name": "Jane Doe", "role": "paralegal"}, {"email": "sam@firm.example", "name": "Sam Attorney", "role": "attorney"}]


@pytest.fixture
def october(monkeypatch):
    """The firm's clock at 10:30 pm on 10/03/2026 in Boston: the UTC date is already the 4th."""
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 3, 22, 30))


def month(server, cookie, month_: str, scope: str = "firm") -> dict:
    status, text = call(server + f"/api/month?month={month_}&scope={scope}", cookie)
    assert status == 200, text
    return json.loads(text)


def cells(m: dict) -> dict[str, dict]:
    return {c["date"]: c for w in m["weeks"] for c in w}


def test_the_grid_is_monday_first_in_the_firms_zone_and_covers_whole_weeks(server, october):
    jane = sign_in(server, "jane@firm.example")
    m = month(server, jane, "2026-10")
    assert m["label"] == "October 2026" and m["weekdays"] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] and (m["previous"], m["next"]) == ("2026-09", "2026-11")
    assert len(m["weeks"]) == 5 and all(len(w) == 7 for w in m["weeks"])
    assert all(date.fromisoformat(w[0]["date"]).weekday() == 0 and w[5]["weekend"] and w[6]["weekend"] for w in m["weeks"])
    assert m["weeks"][0][0]["date"] == "2026-09-28" and m["weeks"][0][0]["in_month"] is False and m["weeks"][0][3]["date"] == "2026-10-01" and m["weeks"][0][3]["in_month"]
    assert m["weeks"][-1][-1]["date"] == "2026-11-01"
    today = [c for c in cells(m).values() if c["today"]]
    assert [c["date"] for c in today] == ["2026-10-03"]  # the firm's date, not the server's (UTC is already the 4th)
    assert len(month(server, jane, "2027-02")["weeks"]) == 4 and len(month(server, jane, "2026-11")["weeks"]) == 6  # February 2027 starts on a Monday and is exactly four weeks
    for bad in ("2026-13", "2026-1", "x", "1999-01"):
        assert call(server + f"/api/month?month={bad}", jane)[0] == 400


def test_every_closure_in_the_file_is_on_its_day_and_nothing_else_is_marked(server, october):
    jane = sign_in(server, "jane@firm.example")
    expected = {(d["date"], kind, d["name"]) for kind in closures.LISTS for d in FILE[kind]["dates"] if d["date"] < "2028"}
    seen = {(c["date"], x["kind"], x["name"]) for year in (2026, 2027) for mo in range(1, 13) for c in cells(month(server, jane, f"{year}-{mo:02d}")).values() for x in c["closures"]}
    assert seen == expected and len(expected) > 40
    # a few by name: Columbus Day (federal, 10/12/2026), Thanksgiving (federal and Florida), and the labels the screen shows
    october_cells = cells(month(server, jane, "2026-10"))
    assert [(x["kind"], x["name"], x["label"]) for x in october_cells["2026-10-12"]["closures"]] == [("federal", "Columbus Day", "Federal holiday")]
    assert october_cells["2026-10-13"]["closures"] == []
    nov = cells(month(server, jane, "2026-11"))
    assert {x["kind"] for x in nov["2026-11-26"]["closures"]} == {"federal", "florida"} and [x["kind"] for x in nov["2026-11-27"]["closures"]] == ["florida"]
    # a closure on a day outside the month but inside the grid's last week is marked there too (Sunday 11/01/2026 ends October's grid; 12/25 is in December's)
    assert [x["kind"] for x in cells(month(server, jane, "2026-12"))["2026-12-25"]["closures"]] == ["federal", "florida"]


def test_the_file_is_what_the_official_pages_said_and_says_so():
    """The dates are copied, never worked out: each list names its page and the day it was read; Massachusetts' page was not read, so its list is empty."""
    for kind in closures.LISTS:
        entry = FILE[kind]
        assert entry["source"].startswith("https://") and set(entry) >= {"name", "source", "read_on", "years", "dates", "note"}
        assert entry["dates"] == sorted(entry["dates"], key=lambda d: d["date"])
        assert all(int(d["date"][:4]) in entry["years"] or d["date"] == "2027-12-31" for d in entry["dates"])  # OPM lists 12/31/2027 under 2028
        assert bool(entry["dates"]) == bool(entry["read_on"]) == bool(entry["years"]), kind  # a list is empty exactly when its page was not read
    assert FILE["massachusetts"]["dates"] == [] and "refused" in FILE["massachusetts"]["note"]
    assert FILE["federal"]["read_on"] == "2026-10-03" and "opm.gov" in FILE["federal"]["source"]
    assert {"date": "2026-10-12", "name": "Columbus Day"} in FILE["federal"]["dates"] and {"date": "2026-11-26", "name": "Thanksgiving Day"} in FILE["federal"]["dates"]
    text = closures.FILE.read_text(encoding="utf-8")
    assert text == json.dumps(FILE, indent=2, ensure_ascii=False) + "\n"  # a re-dump is byte for byte the file


def test_a_list_that_could_not_be_read_says_so_in_a_sentence_on_the_screen(server, october):
    jane = sign_in(server, "jane@firm.example")
    notes = month(server, jane, "2026-10")["notes"]
    assert notes == ["Massachusetts court closures are not listed here: the official page could not be read. Add them under Settings, Court closures the firm adds."]
    assert any(n.startswith("Florida court closures for 2028 are not listed here yet") for n in month(server, jane, "2028-03")["notes"])
    assert any(n.startswith("Federal holidays for 2029") for n in month(server, jane, "2029-03")["notes"])
    for n in notes:
        assert "—" not in n and " -- " not in n and ".json" not in n


def test_an_emptied_list_is_said_not_taken_for_a_year_without_closures(server, october, monkeypatch):
    jane = sign_in(server, "jane@firm.example")
    emptied = json.loads(json.dumps(FILE))
    emptied["federal"].update(dates=[], years=[], read_on=None)
    monkeypatch.setattr(closures, "data", lambda path=closures.FILE: emptied)
    m = month(server, jane, "2026-10")
    assert all(not c["closures"] for c in cells(m).values())
    assert [n for n in m["notes"] if n.startswith("Federal holidays are not listed here")]
    assert [s["read_on"] for s in m["sources"] if s["id"] == "federal"] == [None]


def test_the_florida_days_are_named_as_the_sixth_district_court_of_appeals_and_the_page_says_which_courts_follow_which_list(server, october):
    jane = sign_in(server, "jane@firm.example")
    m = month(server, jane, "2026-09")
    rosh = next(x for x in cells(m)["2026-09-11"]["closures"])
    assert (rosh["kind"], rosh["name"], rosh["short"]) == ("florida", "Rosh Hashanah", "Rosh Hashanah (Sixth DCA)")
    assert rosh["label"] == "Florida appellate court closure (Sixth District Court of Appeal)"
    assert [x["short"] for x in cells(m)["2026-09-07"]["closures"]] == ["Labor Day", "Labor Day (Sixth DCA)"]  # federal and Florida, each as itself
    says = m["follows"]
    assert "The federal holidays are the days the Office of Personnel Management lists" in says and "Sixth District Court of Appeal's own list" in says
    assert "a Florida trial court follows its own circuit's list" in says and "Settings, Court closures the firm adds" in says
    assert "—" not in says and " -- " not in says


def test_the_firms_closures_are_checked_for_years_and_repeats():
    for bad, words in (("01/01/1999 Old", "the year must be between 2000 and 2100"), ("01/01/3000 Far", "the year must be between 2000 and 2100"),
                       ("12/24/2026 Closed\n12/24/2026 closed", "Line 2 repeats line 1"), ("2/30/2026 x", "not a date")):
        with pytest.raises(ValueError, match=words):
            closures.parse_lines(bad)
    assert [d["date"] for d in closures.parse_lines("12/24/2026 Closed\n12/24/2026 Another closure\n\n12/25/2026 Closed")] == ["2026-12-24", "2026-12-24", "2026-12-25"]


def test_the_firms_own_closures_come_from_settings_and_are_marked_as_the_firms(server, october):
    jane = sign_in(server, "jane@firm.example")
    settings.save("closures", {"added": "10/16/2026 Courthouse closed for a training day\n12/23/2026 Boston court closed early"}, "Sam Attorney")
    try:
        m = cells(month(server, jane, "2026-10"))
        assert [(x["kind"], x["name"], x["label"]) for x in m["2026-10-16"]["closures"]] == [("firm", "Courthouse closed for a training day", "Closed (added by the firm)")]
        assert [x["kind"] for x in cells(month(server, jane, "2026-12"))["2026-12-23"]["closures"]] == ["firm"]
        for bad, words in (("2026-10-16 x", "start with the date as MM/DD/YYYY"), ("13/45/2026 x", "not a date"), ("10/16/2026", "start with the date")):
            with pytest.raises(ValueError, match=words):
                settings.save("closures", {"added": bad}, "Sam Attorney")
        # the text is kept as typed once it is read: a second save of the same lines is the same
        saved = settings.values("closures")["added"]
        assert saved.splitlines()[0] == "10/16/2026 Courthouse closed for a training day"
    finally:
        settings.save("closures", {"added": ""}, "Sam Attorney")
    assert cells(month(server, jane, "2026-10"))["2026-10-16"]["closures"] == []


def test_each_day_lists_its_events_with_the_level_and_the_person_and_a_person_sees_their_own(server, dated, october):
    d = dated / "case-ana"
    deadlines_set.add(d, "Urgent one", "2026-10-05", "jane@firm.example", "", "Sam Attorney", PEOPLE)
    deadlines_set.add(d, "Later one", "2026-10-28", "sam@firm.example", "", "Sam Attorney", PEOPLE)
    deadlines_set.add(d, "Nobody named, attorney role", "2026-10-20", "", "", "Sam Attorney", PEOPLE)
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    firm = cells(month(server, jane, "2026-10", "firm"))
    one, later = firm["2026-10-05"]["events"][0], firm["2026-10-28"]["events"][0]
    assert (one["what"], one["level"], one["who_name"], one["client"], one["days_left"]) == ("Urgent one", "urgent", "Jane Doe", "case-ana", 2)
    assert later["level"] in ("soon", "later") and later["who_name"] == "Sam Attorney"
    mine = cells(month(server, jane, "2026-10", "me"))
    assert [e["what"] for e in mine["2026-10-05"]["events"]] == ["Urgent one"] and mine["2026-10-28"]["events"] == []  # Sam's is Sam's
    sams = cells(month(server, sam, "2026-10", "me"))
    assert [e["what"] for e in sams["2026-10-28"]["events"]] == ["Later one"] and sams["2026-10-05"]["events"] == []
    assert json.loads(call(server + "/api/month?month=2026-10", jane)[1])["scope"] == "me"  # the default is the person's own


def test_the_month_view_leaves_a_restricted_case_out_for_staff_not_named_on_it(server, dated, october):
    import journey

    journey.mark(dated / "case-rosa", "hearing", "Sam Attorney", value={"date": "2026-10-22", "time": "9:00 AM", "kind": "Master calendar", "court": "Secret Court Exemplo"})
    jane, kim, sam = sign_in(server, "jane@firm.example"), sign_in(server, "kim@firm.example"), sign_in(server, "sam@firm.example")
    for who, sees in ((jane, False), (kim, True), (sam, True)):
        text = call(server + "/api/month?month=2026-10&scope=firm", who)[1]
        assert ("case-rosa" in text) is sees and ("Secret Court" in text) is sees
