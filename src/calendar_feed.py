"""The calendar feed: every deadline, hearing, biometrics appointment and interview the product knows, as a calendar a person subscribes to.

The firm's calendar is its own (Clio's, Outlook's, Google's): the product feeds it and does not replace it. A person subscribes once, by
address, in Outlook, Google Calendar or an iPhone (no sign-in, no OAuth, no dependency): the address is

    /calendar/<token>.ics        one secret address per person (their own list), and a firm address for an attorney (every case)

A token is 32 random bytes (secrets.token_urlsafe(32)). It is shown once, when it is made (Settings, My calendar); the server keeps only its
SHA-256 (data/calendar_feeds.json), so a token cannot be read back. "Make a new address" revokes the old one. An unknown, revoked or
made-up token gets the same 404 as a case id that does not exist (server.UNKNOWN). The token is a secret: never in the access log (a feed
request is one row a day per token, with the person and the kind, never the token), never in the event ledger (it says only that an
address was made or revoked).

Who sees what (the rule of src/restricted.py, per subscriber): the feed is built for its token's person. A case that person may open
(ReviewApp.may_open) appears with the client's name, the rule sentence and the link to the case page; a restricted case they may not
open is not in their feed at all (not even as a date), as it is in no list of the screens. The firm address is for attorneys
only (an attorney may open every case); a token made for the firm address stops working when its person is no longer an attorney.

Written by hand to RFC 5545: CRLF lines folded at 75 octets, text escaped, an all-day event for a deadline (DTSTART;VALUE=DATE), a timed
event in UTC for an appointment with a time (so no VTIMEZONE is needed and every program shows the firm's local time), the court's
address in LOCATION when the hearing's court is one the product has an address for (schemas/law/immigration_courts.json).

The file is built per request from the same rows What's due uses and cached a minute per person and kind (CACHE_SECONDS).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import clock
import schema_path

FILE = "calendar_feeds.json"
VERSION = 1
KINDS = ("person", "firm")
CACHE_SECONDS = 60
PAST_DAYS = 30  # a late deadline stays on its day for a month; older ones are history
PRODID = "-//Case Review//Deadlines//EN"
APPOINTMENT_MINUTES = 60  # the length shown for a timed event: the notices do not say how long it lasts
_TIME = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([AaPp])?\.?[Mm]?\.?\s*$")
MIN_YEAR, MAX_YEAR = 2000, 2100  # the years a date is written in (the month view's range): a date outside it is left out and said on the console
_clock = time.monotonic  # the cache's clock (a test moves it)


# -- the tokens ----------------------------------------------------------------------------------------------------------------


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Feeds:
    """data/calendar_feeds.json: {"version": 1, "feeds": {sha256(token): {"email", "kind", "made_at", "made_by", "last_logged"}}}. The token itself is
    never stored."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (OSError, ValueError):
            data = {}
        feeds = data.get("feeds") if isinstance(data, dict) else None
        return {"version": VERSION, "feeds": dict(feeds) if isinstance(feeds, dict) else {}}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the hashes open a person's calendar: owner only
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=1))
        os.replace(tmp, self.path)

    def make(self, email: str, kind: str, by: str) -> str:
        """A new address for this person (kind "person" or "firm"): the old one of the same kind stops working. Returns the token, once."""
        if kind not in KINDS:
            raise ValueError("Choose which calendar address to make.")
        email = str(email or "").strip().lower()
        token = secrets.token_urlsafe(32)
        with self._lock:
            data = self._load()
            data["feeds"] = {h: f for h, f in data["feeds"].items() if not (f.get("email") == email and f.get("kind") == kind)}
            data["feeds"][_hash(token)] = {"email": email, "kind": kind, "made_at": clock.stamp(), "made_by": by or email, "last_logged": None}
            self._save(data)
        return token

    def revoke(self, email: str, kind: str) -> bool:
        email = str(email or "").strip().lower()
        with self._lock:
            data = self._load()
            kept = {h: f for h, f in data["feeds"].items() if not (f.get("email") == email and f.get("kind") == kind)}
            if len(kept) == len(data["feeds"]):
                return False
            data["feeds"] = kept
            self._save(data)
        return True

    def find(self, token: str) -> dict[str, Any] | None:
        """The feed a token opens ({"email", "kind", "made_at", "hash"}), or None for any token that is not a current one."""
        token = str(token or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", token):
            return None
        h = _hash(token)
        entry = self._load()["feeds"].get(h)
        return dict(entry, hash=h) if isinstance(entry, dict) else None

    def status(self, email: str) -> dict[str, Any]:
        """What the person's Settings page shows: for each kind, when the current address was made (None: none yet). Never a token."""
        email = str(email or "").strip().lower()
        out: dict[str, Any] = {k: None for k in KINDS}
        for f in self._load()["feeds"].values():
            if f.get("email") == email and f.get("kind") in KINDS:
                out[f["kind"]] = f.get("made_at")
        return out

    def first_today(self, h: str) -> bool:
        """True the first time this token is used on the firm's day (and remembers it): the access log gets one row a day per token, not one per poll."""
        today = clock.today().isoformat()
        with self._lock:
            data = self._load()
            entry = data["feeds"].get(h)
            if not entry or entry.get("last_logged") == today:
                return False
            entry["last_logged"] = today
            self._save(data)
        return True


# -- the events ----------------------------------------------------------------------------------------------------------------


def parse_time(text: Any) -> tuple[int, int] | None:
    """(hour, minute) from "9:00 AM", "09:30", "1:30 pm", "9 AM"; None when it cannot be read (the event is then all-day, the time said in its words)."""
    m = _TIME.match(str(text or ""))
    if not m:
        return None
    hour, minute, ap = int(m[1]), int(m[2] or 0), (m[3] or "").lower()
    if ap:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ap == "p" else 0)
    return (hour, minute) if hour < 24 and minute < 60 else None


def court_address(name: Any) -> str | None:
    """The court's name and address as EOIR's page gives it (schemas/law/immigration_courts.json) for a hearing's court, else what was typed."""
    text = " ".join(str(name or "").split())
    if not text:
        return None
    try:
        courts = json.loads((schema_path.path("law", "immigration_courts")).read_text(encoding="utf-8")).get("courts") or {}
    except (OSError, ValueError):
        courts = {}
    found = next((c for k, c in courts.items() if k.lower() == text.lower()), None)
    return f"{text}, {', '.join(found['address'])}" if found and found.get("address") else text


def _short(text: str, limit: int = 110) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def mine(d: dict[str, Any], person: dict[str, Any]) -> bool:
    """Whether a deadline is on this person's own list: assigned to them; or, with nobody assigned, owned by their role (a date the client must
    keep, a hearing or an appointment, is on everyone's: the attorney and the paralegal both prepare it)."""
    if d.get("who"):
        return d["who"] == person["email"]
    return d.get("owner") in (person.get("role"), "client")


def build(rows: list[dict[str, Any]], person: dict[str, Any], kind: str, may_see, base_url: str, today: date | None = None) -> list[dict[str, Any]]:
    """The events for one subscriber. rows: every case's overview row (id, summary.name, journey.deadlines), restricted ones included;
    may_see(person, case id, deadline): the app's own gate (ReviewApp.may_open, and a confidential document's deadline for staff not named on the case). kind "firm": every deadline; "person": the person's own list (mine())."""
    today = today or clock.today()
    out = []
    for row in rows:
        case = str(row.get("id") or "")
        for d in ((row.get("journey") or {}).get("deadlines") or []):
            try:
                when = date.fromisoformat(str(d["date"])[:10])
                if not MIN_YEAR <= when.year <= MAX_YEAR:
                    raise ValueError("a date outside the years the product writes")
            except (KeyError, ValueError) as exc:
                if not isinstance(exc, KeyError):
                    sys.stderr.write(f"calendar feed: a deadline's date could not be written ({type(exc).__name__}); the event is left out\n")
                continue
            if (when - today).days < -PAST_DAYS or (kind == "person" and not mine(d, person)):
                continue
            if d.get("owner") == "client" and when < today:
                continue  # an appointment that has passed is history
            uid = hashlib.sha256(f"{case}|{d.get('id')}".encode()).hexdigest()[:32] + "@case-review"
            if not may_see(person, case, d):
                continue  # a case this person may not open (or a confidential document's date) is left out, as on What's due and the month view
            event: dict[str, Any] = {"uid": uid, "date": when, "time": None, "minutes": APPOINTMENT_MINUTES}
            appt = d.get("appt") or {}
            name = (row.get("summary") or {}).get("name")
            name = str(name).title() if name else case
            clock_time = parse_time(appt.get("time")) if appt else None
            if clock_time:
                event["time"] = clock_time
            what = str(d.get("what") or "")
            head = (appt.get("title") + (f" at {appt['time']}" if appt.get("time") and not clock_time else "")) if appt.get("title") else _short(what)
            parts = [what]
            if (d.get("expiry") or {}).get("rule"):
                parts.append("The rule: " + str(d["expiry"]["rule"]))
            if appt.get("judge"):
                parts.append(f"Judge {appt['judge']}.")
            if d.get("who_name"):
                parts.append(f"Responsible: {d['who_name']}.")
            if d.get("note"):
                parts.append(f"Note: {d['note']}")
            url = f"{base_url}/#{quote(case, safe='')}"
            parts.append(f"The case: {url}")
            event |= {"title": f"{name}: {head}", "description": "\n\n".join(parts), "url": url,
                      "location": court_address(appt.get("place")) if appt.get("kind") == "hearing" else (appt.get("place") or None) if appt else None}
            out.append(event)
    out.sort(key=lambda e: (e["date"], e["time"] or (0, 0), e["title"]))
    return out


# -- RFC 5545 --------------------------------------------------------------------------------------------------------------------


_CONTROLS = re.compile("[\x00-\x08\x0b-\x1f\x7f]")  # RFC 5545 TEXT allows no control character but tab; a line break is written as \n


def _escape(text: str) -> str:
    """A text value as RFC 5545 writes it. A name read from a scan can hold anything: every control character goes (NUL, 0x01, 0x7f ...), tab stays,
    a line break becomes \\n."""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROLS.sub("", text)
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str) -> str:
    """A content line folded so no line is longer than 75 octets (RFC 5545 3.1): the break is never inside a UTF-8 character."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    pieces, current, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > (75 if not pieces else 74):
            pieces.append(current)
            current, size = "", 0
        current += ch
        size += n
    pieces.append(current)
    return "\r\n ".join(pieces)


def _utc(day: date, hm: tuple[int, int]) -> datetime:
    return datetime(day.year, day.month, day.day, hm[0], hm[1], tzinfo=clock.zone()).astimezone(timezone.utc)


class Stamps:
    """When each event last changed, kept in memory: DTSTAMP, LAST-MODIFIED and SEQUENCE are stable for an event whose content is the same, and move
    (SEQUENCE only upward: it is the minute of the change) when its content does, so a calendar program sees a change as a change and a rebuild as nothing.
    A restart of the server starts every event again at that moment, which is never earlier than before."""

    def __init__(self) -> None:
        self._seen: dict[str, tuple[str, datetime]] = {}
        self._lock = threading.Lock()

    def modified(self, event: dict[str, Any], now: datetime) -> datetime:
        content = hashlib.sha256(json.dumps([str(event.get(k)) for k in ("date", "time", "title", "description", "location", "url")]).encode()).hexdigest()
        with self._lock:
            known = self._seen.get(event["uid"])
            if known and known[0] == content:
                return known[1]
            if len(self._seen) > 200000:  # a runaway map: forget it, the events are stamped again
                self._seen = {}
            self._seen[event["uid"]] = (content, now)
            return now


def ics(events: list[dict[str, Any]], name: str, now: datetime | None = None, stamps: Stamps | None = None) -> bytes:
    """The calendar file: one VEVENT per event. All-day when the event has no time (DTEND is the next day); else timed, in UTC. An event that cannot be
    written (a date past the calendar's last day, say) is left out and said on the console, never a dropped connection for everyone who holds the address."""
    now = (now or clock.utcnow()).astimezone(timezone.utc)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{_escape(name)}",
             "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for e in events:
        try:
            modified = (stamps.modified(e, now) if stamps else now).astimezone(timezone.utc)
            stamp = f"{modified:%Y%m%dT%H%M%SZ}"
            one = ["BEGIN:VEVENT", f"UID:{e['uid']}", f"DTSTAMP:{stamp}", f"LAST-MODIFIED:{stamp}", f"SEQUENCE:{int(modified.timestamp() // 60)}"]
            if e["time"]:
                start = _utc(e["date"], e["time"])
                one += [f"DTSTART:{start:%Y%m%dT%H%M%SZ}", f"DTEND:{start + timedelta(minutes=e['minutes']):%Y%m%dT%H%M%SZ}"]
            else:
                one += [f"DTSTART;VALUE=DATE:{e['date']:%Y%m%d}", f"DTEND;VALUE=DATE:{e['date'] + timedelta(days=1):%Y%m%d}", "TRANSP:TRANSPARENT"]
            one.append(f"SUMMARY:{_escape(e['title'])}")
            if e.get("description"):
                one.append(f"DESCRIPTION:{_escape(e['description'])}")
            if e.get("location"):
                one.append(f"LOCATION:{_escape(e['location'])}")
            if e.get("url"):
                one.append(f"URL:{_CONTROLS.sub('', str(e['url']))}")
            one += ["STATUS:CONFIRMED", "END:VEVENT"]
        except (OverflowError, ValueError, KeyError, TypeError) as exc:
            sys.stderr.write(f"calendar feed: an event could not be written ({type(exc).__name__}); it is left out\n")
            continue
        lines += one
    lines.append("END:VCALENDAR")
    return ("\r\n".join(fold(x) for x in lines) + "\r\n").encode("utf-8")


# -- the cache -------------------------------------------------------------------------------------------------------------------


class Cache:
    """A built value per key, kept CACHE_SECONDS: a calendar program polls every hour or so, and what is built per request is every case's deadlines.
    One build at a time for a key (single flight): requests that arrive while it is being built wait for it and share the result. clear() forgets
    everything: an access change (who may open a case, a role) must show on the next request, not after the minute."""

    def __init__(self) -> None:
        self._items: dict[tuple, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._building: dict[tuple, threading.Lock] = {}
        self._generation = 0

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._generation += 1

    def _fresh(self, key: tuple):
        with self._lock:
            hit = self._items.get(key)
        return hit[1] if hit and _clock() - hit[0] < CACHE_SECONDS else None

    def get(self, key: tuple, make) -> Any:
        found = self._fresh(key)
        if found is not None:
            return found
        with self._lock:
            gate = self._building.setdefault(key, threading.Lock())
        with gate:  # the first request builds; the others, held here, find it built
            found = self._fresh(key)
            if found is not None:
                return found
            with self._lock:
                generation = self._generation
            value = make()
            with self._lock:
                if generation == self._generation:  # not cleared while it was being built
                    self._items[key] = (_clock(), value)
                    if len(self._items) > 500:
                        now = _clock()
                        self._items = {k: v for k, v in self._items.items() if now - v[0] < CACHE_SECONDS}
        return value


def is_open(case_dir: str | Path) -> bool:
    """False for a case that has ended (the engagement's end states, src/engagement.py: closed, declined, withdrawn ...): it leaves the feed, the month
    view and the reminders as it leaves My work. True for every case when the engagement module is not there yet, so this stays one line at the merge."""
    try:
        import engagement
    except ImportError:
        return True
    ended = getattr(engagement, "end_info", None)
    if ended is None:
        return True
    declined = getattr(engagement, "conflict_declined", lambda d: None)  # a client the conflict search declined (src/conflicts.py) is ended too
    try:
        return not (ended(Path(case_dir)) or declined(Path(case_dir)))
    except Exception:  # noqa: BLE001 -- a case whose end cannot be read stays on the lists: the deadline must not vanish
        return True
