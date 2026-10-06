"""The firm's clock: what "now" and "today" are, in the firm's time zone.

A deadline, a fee's effective date, the Visa Bulletin month, "late" and "passed", the overnight run's
--until, the date printed on a review bundle or beside a decision: each is a date or a time in the
office's time zone, never the server's. A server that keeps UTC is already "tomorrow" after 8 pm in
Boston, which put 10/03/2026 on a bundle built on the evening of 10/02/2026 (docs/research/
buyer_walkthrough_3.md, finding 4). Everything in src/ that reads the clock goes through here:

    clock.now()               aware, in the firm's zone
    clock.utcnow()            the same instant in UTC: for durations and time-to-live arithmetic
    clock.today()             the firm's date (deadlines, fees, the bulletin month compare against it)
    clock.stamp()             the ISO string a file stores, with the firm's offset ("2026-10-02T20:05:00-04:00")
    clock.parse(value)        a stored stamp as an aware datetime (the rule for old stamps below)
    clock.local_date(value)   a stored stamp (or a plain date) as the firm's date
    clock.us_date(value)      the same as the screen writes it, MM/DD/YYYY

The zone is a setting: the Settings page, "Main office", "Time zone" (an IANA name, checked against the
zone database). Default America/New_York: both of the first firm's offices (Massachusetts and Florida)
are Eastern, but the product is sold to other firms, so it is never a constant.

Stored stamps (decisions.md 10/02/2026, "the firm's clock"):
  - written from now on: stamp(), with the firm's offset. Comparisons between stamps are between
    instants (parse() first), never between strings.
  - written before: every writer in src/'s history stamped UTC, with an offset: datetime.now(timezone.utc)
    ("+00:00"), the portal's submit time.gmtime() ("Z"), the browser's toISOString() ("Z"). None wrote a
    naive local time (git log -S "datetime.now().isoformat" and -S "utcnow" over src/ find nothing). So a
    stamp WITHOUT an offset is read as UTC, the convention every writer followed. Nothing on disk is
    rewritten: parse() reads the old and the new alike, and local_date() gives either one's office date.
  - a plain date ("2026-10-02": mailed on, due, effective) has no time and no zone: it is that date and is
    never shifted.

Durations: Python adds a timedelta to a zone-aware datetime on the wall clock, so now() + 2 hours at 00:40
on 11/01/2026 (the night the clocks go back) gives 02:40 EST, three real hours later; and subtracting two
datetimes that carry the same zone ignores the change too. A time-to-live, a lockout or a run's elapsed
time is therefore computed on utcnow(); dates and wall-clock times ("the next 6:30") on now().

Tests freeze the clock in one place: monkeypatch.setattr(clock, "_now_override", datetime(...)). An
aware value is that instant; a naive one is the office's wall-clock time.
"""

from __future__ import annotations

import re
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone, tzinfo
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

DEFAULT_ZONE = "America/New_York"  # both of the first firm's offices (Massachusetts and Florida) are Eastern
FIELD = "office.time_zone"  # its key in the Settings page's "Main office" section (src/settings.py)
_now_override: datetime | None = None  # the tests' frozen clock
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_cache: dict[str, Any] = {}
_operation_zone = ContextVar("clock_operation_zone", default=None)


@contextmanager
def cached_zone():
    """Use one settings snapshot while a writer operation holds its settings gate."""
    token = _operation_zone.set(zone_name())
    try:
        yield
    finally:
        _operation_zone.reset(token)


@lru_cache(maxsize=1)
def zones() -> frozenset[str]:
    """Every IANA zone this machine knows (the system's zone database, or the tzdata package where there is none)."""
    return frozenset(available_timezones())


def valid(name: Any) -> bool:
    return isinstance(name, str) and name in zones()


def zone_name() -> str:
    """The firm's IANA zone: the Settings page's, else America/New_York. Read again only when the settings file changes."""
    import settings

    saved = _operation_zone.get()
    if saved is not None:
        return saved

    key = (str(settings.PATH), settings.mtime())
    if _cache.get("key") != key:
        name = str(settings.values("firm").get(FIELD) or "").strip()
        _cache.update(key=key, name=name if valid(name) else DEFAULT_ZONE)
    return _cache["name"]


def zone() -> tzinfo:
    """The firm's zone. Without a zone database at all (a Windows machine without the tzdata package) the server's own
    zone, with a warning: the old behaviour, never a crash."""
    try:
        return ZoneInfo(zone_name())
    except (ZoneInfoNotFoundError, ValueError):
        if not _cache.get("warned"):
            _cache["warned"] = True
            warnings.warn(f"No time zone database: dates use this computer's zone, not {zone_name()} (pip install tzdata).", RuntimeWarning, stacklevel=2)
        return datetime.now().astimezone().tzinfo or timezone.utc


def now() -> datetime:
    """Now, aware, in the firm's zone."""
    tz = zone()
    if _now_override is not None:
        at = _now_override
        return at.replace(tzinfo=tz) if at.tzinfo is None else at.astimezone(tz)
    return datetime.now(timezone.utc).astimezone(tz)


def utcnow() -> datetime:
    """Now in UTC (frozen with the same _now_override): add a time-to-live or measure a duration on this, never on now()."""
    return now().astimezone(timezone.utc)


def today() -> date:
    """The firm's date: what every deadline, fee date and bulletin month compares against."""
    return now().date()


def stamp(timespec: str = "auto") -> str:
    """Now as a file stores it: ISO 8601 with the firm's offset."""
    return now().isoformat(timespec=timespec)


def parse(value: Any) -> datetime | None:
    """A stored stamp as an aware datetime. With an offset (or "Z"): that instant. Without one: UTC (the module docstring:
    every earlier writer stamped UTC). A plain date: its midnight in the firm's zone. Anything else: None."""
    if isinstance(value, datetime):
        at = value
    elif isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=zone())
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if _DATE.fullmatch(text):
            d = date.fromisoformat(text)
            return datetime(d.year, d.month, d.day, tzinfo=zone())
        try:
            at = datetime.fromisoformat(text)
        except ValueError:
            return None
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at


_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


def key(value: Any) -> datetime:
    """A sort key for stored stamps: by the instant, never the string ("23:00+00:00" is before "20:00-04:00"); none sorts first."""
    return parse(value) or _EARLIEST


def local(value: Any) -> datetime | None:
    """A stored stamp in the firm's zone (for a time of day on the screen or in a record)."""
    at = parse(value)
    return at.astimezone(zone()) if at else None


def local_date(value: Any) -> date | None:
    """A stored stamp, with or without an offset, as the firm's date. A plain date is itself (never shifted)."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if _DATE.fullmatch(text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    at = local(value)
    return at.date() if at else None


def day(value: Any) -> str:
    """local_date() as "YYYY-MM-DD" ("" when there is none): in place of slicing a stamp's first ten characters."""
    d = local_date(value)
    return d.isoformat() if d else ""


def us_date(value: Any) -> str:
    """The firm's date of a stamp or a plain date as the screen writes it: "10/02/2026" ("" when there is none)."""
    d = local_date(value)
    return d.strftime("%m/%d/%Y") if d else ""
