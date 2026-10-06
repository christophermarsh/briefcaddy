"""The firm's clock (src/clock.py): the zone is a setting, "today" is the office's, old stamps are read by one rule.

The change-over night: 2026-10-15 at 11:30 PM Eastern is already 2026-10-16 03:30 in UTC. A server that dates in UTC would
apply the fee increase effective 10/16/2026, count a deadline due 10/15 as late, and print 10/16 on a record built that
evening (docs/research/buyer_walkthrough_3.md, finding 4). Every case below freezes the clock there.
"""

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import clock
import settings
import schema_path

EASTERN = ZoneInfo("America/New_York")
EVE = datetime(2026, 10, 15, 23, 30, tzinfo=EASTERN)  # 2026-10-16 03:30 UTC
FEES = json.loads((schema_path.path("law", "fees")).read_text(encoding="utf-8"))


@pytest.fixture
def firm(tmp_path, monkeypatch):
    """The firm's own settings file, empty: the shipped defaults."""
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    return tmp_path


@pytest.fixture
def eve(firm, monkeypatch):
    monkeypatch.setattr(clock, "_now_override", EVE)
    return EVE


# -- the zone, a setting --------------------------------------------------------------------------------------------

def test_the_default_zone_is_eastern(firm):
    assert clock.zone_name() == "America/New_York" == clock.DEFAULT_ZONE
    field = next(f for s in settings.specs() if s["id"] == "firm" for f in s["fields"] if f["key"] == clock.FIELD)
    assert field["value"] == "America/New_York" and field["type"] == "zone"
    assert field["options"][0] == ["America/New_York", "Eastern (New York, Boston, Miami)"]  # the U.S. zones first


def test_the_zone_is_set_on_the_settings_page(firm, monkeypatch):
    settings.save("firm", {clock.FIELD: "America/Los_Angeles"}, "Ana Attorney")
    assert clock.zone_name() == "America/Los_Angeles"
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc))  # 7 PM on 10/15 in Los Angeles
    assert clock.today() == date(2026, 10, 15) and clock.stamp() == "2026-10-15T19:00:00-07:00"
    saved = json.loads(settings.PATH.read_text(encoding="utf-8"))["firm"]
    assert saved["values"][clock.FIELD] == "America/Los_Angeles" and saved["updated_by"] == "Ana Attorney"


@pytest.mark.parametrize("bad", ["Eastern", "America/Bostn", "EST5EDT; rm -rf", "GMT+5"])
def test_a_zone_not_in_the_database_is_refused(firm, bad):
    with pytest.raises(ValueError, match="Time zone: choose a time zone from the list"):
        settings.save("firm", {clock.FIELD: bad}, "Ana Attorney")
    assert clock.zone_name() == "America/New_York"


def test_a_hand_edited_bad_zone_falls_back_to_the_default(firm):
    settings.PATH.write_text(json.dumps({"firm": {"values": {clock.FIELD: "Mars/Olympus"}}}), encoding="utf-8")
    assert clock.zone_name() == "America/New_York"


# -- now, today, stamp, local_date ----------------------------------------------------------------------------------

def test_now_today_and_stamp_are_the_offices(eve):
    now = clock.now()
    assert now.tzinfo is not None and now.utcoffset() == timedelta(hours=-4)
    assert now == datetime(2026, 10, 16, 3, 30, tzinfo=timezone.utc)
    assert clock.today() == date(2026, 10, 15)  # UTC already says 10/16
    assert clock.stamp() == "2026-10-15T23:30:00-04:00" and clock.stamp("seconds") == "2026-10-15T23:30:00-04:00"
    assert clock.parse(clock.stamp()) == now


def test_a_naive_frozen_clock_is_the_offices_wall_clock(firm, monkeypatch):
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 15, 23, 30))
    assert clock.now() == EVE and clock.today() == date(2026, 10, 15)


def test_the_real_clock_is_aware_and_in_the_zone(firm):
    now = clock.now()
    assert now.tzinfo is not None and abs(now - datetime.now(timezone.utc)) < timedelta(minutes=1)
    assert clock.today() == datetime.now(EASTERN).date()


@pytest.mark.parametrize("stored, office_day", [
    ("2026-10-16T03:30:00+00:00", date(2026, 10, 15)),        # an old stamp, written in UTC with its offset
    ("2026-10-16T03:30:00Z", date(2026, 10, 15)),             # the portal's old submit stamp (time.gmtime, "Z")
    ("2026-10-16T03:30:00", date(2026, 10, 15)),              # no offset: read as UTC, the rule for old stamps
    ("2026-10-16T03:30:00.123456", date(2026, 10, 15)),
    ("2026-10-15T23:30:00-04:00", date(2026, 10, 15)),        # a new stamp, with the office's offset
    ("2026-10-16T05:00:00+02:00", date(2026, 10, 15)),        # any offset: the instant, then the office's date
    ("2026-10-16", date(2026, 10, 16)),                       # a plain date is itself, never shifted
    ("2026-01-15T04:59:00+00:00", date(2026, 1, 14)),         # winter: Eastern is UTC-5
    ("2026-01-15T05:00:00+00:00", date(2026, 1, 15)),
])
def test_local_date_gives_the_offices_date(firm, stored, office_day):
    assert clock.local_date(stored) == office_day
    assert clock.day(stored) == office_day.isoformat() and clock.us_date(stored) == office_day.strftime("%m/%d/%Y")


@pytest.mark.parametrize("nothing", [None, "", "not a date", "2026-13-45"])
def test_nothing_to_read_is_nothing(firm, nothing):
    assert clock.local_date(nothing) is None and clock.day(nothing) == "" and clock.us_date(nothing) == ""


def test_naive_old_stamps_are_read_as_utc(firm):
    assert clock.parse("2026-10-02T23:05:00") == datetime(2026, 10, 2, 23, 5, tzinfo=timezone.utc)
    assert clock.local("2026-10-03T00:05:00").strftime("%m/%d/%Y %I:%M %p") == "10/02/2026 08:05 PM"


def test_stamps_sort_by_the_instant_never_the_string(firm):
    old = "2026-10-02T23:00:00+00:00"  # 7 PM Eastern, written before the change
    new = "2026-10-02T20:30:00-04:00"  # 8:30 PM Eastern, written after it: later, though its string sorts first
    assert new < old and clock.key(old) < clock.key(new)
    assert max([old, new], key=clock.key) == new and sorted([new, None, old], key=clock.key) == [None, old, new]


# -- the change-over night: everything that compares against "today" ------------------------------------------------

def test_the_fee_is_still_the_old_one_at_1130_pm_on_the_eve(eve, monkeypatch):
    import fees

    old, new = FEES["pl_119_21"]["annual_asylum"], next(c["amount"] for c in FEES["scheduled"] if c["fee"] == "pl_119_21.annual_asylum")
    change = next(c for c in FEES["scheduled"] if c["fee"] == "pl_119_21.annual_asylum")
    assert change["effective"] == "2026-10-16" and old != new
    assert fees.load()["pl_119_21"]["annual_asylum"] == old  # 03:30 UTC on 10/16, but 10/15 at the office
    assert [c["fee"] for c in fees.upcoming(["pl_119_21.annual_asylum"])] == ["pl_119_21.annual_asylum"]  # the pre-mailing check still warns
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 16, 0, 0, tzinfo=EASTERN))  # midnight at the office
    assert fees.load()["pl_119_21"]["annual_asylum"] == new and fees.upcoming(["pl_119_21.annual_asylum"]) == []


def test_a_deadline_due_today_is_not_late_at_1130_pm(eve):
    from review.overview import deadlines

    rows = [{"id": "c1", "summary": {"name": "Ana Clara Exemplo Souza"},
             "journey": {"deadlines": [{"id": "rfe.response", "date": "2026-10-15", "what": "Answer the RFE", "owner": "attorney"},
                                       {"id": "one_year", "date": "2026-08-16", "what": "The asylum one-year deadline", "owner": "attorney"},
                                       {"id": "court.appeal", "date": "2026-08-15", "what": "The BIA appeal", "owner": "attorney"}]}}]
    due = {d["id"]: d for d in deadlines(rows)}
    assert due["rfe.response"]["days_left"] == 0 and due["rfe.response"]["level"] != "overdue"  # UTC would say late
    assert due["one_year"]["days_left"] == -60 and due["one_year"]["level"] == "overdue"  # not yet "passed": that is after 60 days
    assert due["court.appeal"]["days_left"] == -61 and due["court.appeal"]["level"] == "passed"  # the other side of the boundary


def test_the_overnight_runs_late_count_uses_the_offices_today(eve, tmp_path, monkeypatch):
    import overnight
    from review import overview

    out = tmp_path / "out"
    (out / "c1").mkdir(parents=True)
    (out / "c1" / "fact_graph.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(overview, "journey_row", lambda d: {"deadlines": [{"id": "rfe.response", "date": "2026-10-15", "what": "x", "owner": "attorney"}]})
    text = overnight.journeys(out)
    assert "1 deadline(s) in the next 14 days" in text and "LATE" not in text
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 16, 0, 1, tzinfo=EASTERN))
    assert "1 LATE" in overnight.journeys(out)


def test_until_and_the_morning_report_are_the_offices_time(eve):
    import overnight

    stop = overnight._until("06:30")
    assert stop == datetime(2026, 10, 16, 6, 30, tzinfo=EASTERN)  # the next 6:30 AM at the office (10:30 UTC)
    assert overnight._clock("2026-10-16T03:30:00+00:00") == "23:30"
    started = clock.now() - timedelta(hours=1)
    report = overnight._report([], {"unchanged": 0}, [], None, started, 0)
    assert report.startswith("Overnight run 10/15/2026 22:30: 1 h 00 min")


def test_the_visa_bulletin_month_is_the_offices(firm, monkeypatch):
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 31, 23, 30, tzinfo=EASTERN))  # November 1 in UTC
    assert settings._months() == ["November 2026", "October 2026", "September 2026", "August 2026"]  # next, this, the two before
    import maintenance

    findings = {i["id"]: i["findings"] for i in maintenance.status()}  # Keeping current: "it should be <this month>"
    bulletin = [f for fs in findings.values() for f in fs if "Visa Bulletin setting" in f]
    assert bulletin and all(f.endswith("it should be October 2026.") for f in bulletin), bulletin


def test_records_built_in_the_evening_carry_the_offices_date(firm, monkeypatch):
    from portal.engine import _skip_record
    from review.state import us_date

    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 2, 20, 5, tzinfo=EASTERN))  # the buyer's evening
    assert us_date(clock.stamp()) == "10/02/2026"  # the review bundle's "Built ..." and the accuracy record's
    decision = {"reviewer": "Paulo Paralegal", "at": "2026-10-03T00:05:00+00:00"}  # a decision stamped in UTC before this change
    assert "(Paulo Paralegal, 10/02/2026)" in _skip_record("t1", "q1", "applicant.dob", decision)["why"]
    assert us_date("2026-10-03") == "10/03/2026"  # a plain date is never shifted


def test_the_case_status_quota_day_is_uscis_not_the_firms(firm):
    import case_status

    utc = datetime(2026, 10, 16, 3, 30, tzinfo=timezone.utc)
    assert case_status._quota_day(utc) == case_status._quota_day(utc.astimezone(EASTERN)) == "2026-10-15"


# -- durations: real hours, even the night the clocks go back -------------------------------------------------------

FALL_BACK = datetime(2026, 11, 1, 0, 40, tzinfo=EASTERN)  # EDT (04:40 UTC); at 2:00 the clocks go back to 1:00 EST


def _later(**delta) -> datetime:
    """FALL_BACK plus real time (on UTC: a timedelta added to a zone-aware value is wall-clock time)."""
    return FALL_BACK.astimezone(timezone.utc) + timedelta(**delta)


def test_wall_clock_arithmetic_is_why_durations_use_utc(firm, monkeypatch):
    monkeypatch.setattr(clock, "_now_override", FALL_BACK)
    assert (clock.now() + timedelta(hours=2)) - _later() == timedelta(hours=3)  # Python's wall clock: 02:40 EST is three real hours
    assert clock.utcnow() == FALL_BACK and clock.utcnow().utcoffset() == timedelta(0)
    assert (clock.utcnow() + timedelta(hours=2)) - FALL_BACK == timedelta(hours=2)


def test_a_two_hour_link_expires_after_two_real_hours(firm, tmp_path, monkeypatch):
    from communication_fixture import installation, approve_client, accepted_link
    from portal import communication_consent as consent
    from portal import store as store_mod

    monkeypatch.setattr(store_mod, "LINK_TTL", timedelta(hours=2))
    monkeypatch.setattr(consent, "LINK_TTL", timedelta(hours=2))
    data = installation(tmp_path / "firm", monkeypatch)
    (data / "clients" / "pilot-1").mkdir()
    store = store_mod.PortalStore(data / "portal")
    store.add_client("pilot-1", "Ana Clara Exemplo Souza", email="ana@fictional.example", language="en")
    monkeypatch.setattr(clock, "_now_override", FALL_BACK)
    approve_client(store, "pilot-1")
    early, late = accepted_link(store, "pilot-1"), accepted_link(store, "pilot-1")
    assert {clock.parse(row["expires"]) for row in store._auth()["links"].values()} == {_later(hours=2)}
    monkeypatch.setattr(clock, "_now_override", _later(hours=1, minutes=59))  # 1:39 AM EST
    assert store.redeem_link(early)
    monkeypatch.setattr(clock, "_now_override", _later(hours=2, minutes=1))  # 1:41 AM EST: wall-clock arithmetic would allow it until 2:40
    assert store.redeem_link(late) is None


def test_a_two_hour_staff_session_ends_after_two_real_hours(firm, tmp_path, monkeypatch):
    from review.auth import Accounts

    settings.save("sign_in", {"idle_minutes": "120"}, "Ana Attorney")
    accounts = Accounts(tmp_path / "staff.json")
    temporary = accounts.add("jane@firm.example", "Jane Doe", "paralegal")
    accounts.change_password("jane@firm.example", temporary, "a long enough passphrase")
    monkeypatch.setattr(clock, "_now_override", FALL_BACK)
    token, _ = accounts.sign_in("jane@firm.example", "a long enough passphrase")
    sessions = json.loads((tmp_path / "staff.json").read_text(encoding="utf-8"))["sessions"]
    assert [clock.parse(s["expires"]) - FALL_BACK for s in sessions.values()] == [timedelta(hours=2)]
    monkeypatch.setattr(clock, "_now_override", _later(hours=2, minutes=1))
    assert accounts.session_user(token) is None

def test_writer_clock_snapshot_avoids_repeated_settings_io(monkeypatch):
    import settings
    calls = []
    monkeypatch.setattr(settings, "mtime", lambda: calls.append(1) or 1)
    monkeypatch.setattr(settings, "values", lambda _: {clock.FIELD: "America/New_York"})
    clock._cache.clear()
    with clock.cached_zone():
        for _ in range(1000):
            assert clock.now().tzinfo is not None
        with clock.cached_zone():
            clock.stamp()
        assert len(calls) == 1
    monkeypatch.setattr(settings, "mtime", lambda: calls.append(1) or 2)
    monkeypatch.setattr(settings, "values", lambda _: {clock.FIELD: "America/Los_Angeles"})
    assert clock.zone_name() == "America/Los_Angeles"
    assert len(calls) == 2
