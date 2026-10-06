"""The days the federal government and the courts of the firm's two states are closed, for the month view on What's due.

    schemas/registers/court_closures.json   federal holidays (OPM), Massachusetts court closures (mass.gov), Florida court closures (flcourts.gov):
                                  each list with the page it was copied from and the date it was read. A date is copied, never worked out.
    Settings, Court closures the firm adds   the firm's own days, one a line ("12/24/2026 Courthouse closed"): a closure a page does not
                                  list, a circuit's own closure, or any list whose page could not be read

A list whose page could not be read is empty in the file, and the month view says so in a sentence (notes()) that tells the firm to add the
days under Settings: an empty list is never taken to mean "no closures".
"""

from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any
import schema_path

FILE = schema_path.path("register", "court_closures")
KINDS = {"federal": "Federal holiday", "massachusetts": "Massachusetts court closure", "florida": "Florida appellate court closure (Sixth District Court of Appeal)",
         "firm": "Closed (added by the firm)"}
SHORT = {"florida": " (Sixth DCA)"}  # said on the day itself: the Florida list is one appellate court's, not every Florida court's
MIN_YEAR, MAX_YEAR = 2000, 2100  # the years a day may be written in (the month view's range)
LISTS = ("federal", "massachusetts", "florida")
SECTION = "closures"  # the Settings section the firm's own days are kept in
FIELD = "added"
_LINE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})\s+(.+)$")


@lru_cache(maxsize=2)
def _load(path: str, mtime: float) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def data(path: Path = FILE) -> dict[str, Any]:
    return _load(str(path), path.stat().st_mtime)


def parse_lines(text: Any) -> list[dict[str, str]]:
    """The firm's days from the Settings box: one a line, "MM/DD/YYYY name". Raises ValueError, in words, for a line that is not one."""
    out: list[dict[str, str]] = []
    seen: dict[tuple[str, str], int] = {}
    for n, line in enumerate(str(text or "").splitlines(), 1):
        line = " ".join(line.split())
        if not line:
            continue
        m = _LINE.match(line)
        if not m:
            raise ValueError(f"Line {n}: start with the date as MM/DD/YYYY, then what the day is (for example 12/24/2026 Courthouse closed).")
        try:
            day = date(int(m[3]), int(m[1]), int(m[2]))
        except ValueError:
            raise ValueError(f"Line {n}: {m[1]}/{m[2]}/{m[3]} is not a date.") from None
        if not MIN_YEAR <= day.year <= MAX_YEAR:
            raise ValueError(f"Line {n}: the year must be between {MIN_YEAR} and {MAX_YEAR}.")
        name = m[4][:80]
        again = seen.setdefault((day.isoformat(), name.lower()), n)
        if again != n:
            raise ValueError(f"Line {n} repeats line {again}: each closed day is entered once.")
        out.append({"date": day.isoformat(), "name": name})
    return out


def firm_added() -> list[dict[str, str]]:
    """The days the firm added under Settings (a box nobody can read is no days)."""
    import settings

    try:
        return parse_lines(settings.values(SECTION).get(FIELD))
    except (ValueError, OSError):
        return []


def by_day(start: date, end: date) -> dict[str, list[dict[str, str]]]:
    """{YYYY-MM-DD: [{"name", "kind", "label"}]} for the closures from start to end (inclusive), every list and the firm's own."""
    out: dict[str, list[dict[str, str]]] = {}
    rows = [(k, d) for k in LISTS for d in data()[k]["dates"]] + [("firm", d) for d in firm_added()]
    for kind, d in rows:
        if start.isoformat() <= d["date"] <= end.isoformat():
            out.setdefault(d["date"], []).append({"name": d["name"], "kind": kind, "label": KINDS[kind], "short": d["name"] + SHORT.get(kind, "")})
    return out


def notes(year: int) -> list[str]:
    """One sentence for each official list that does not cover this year (its page could not be read, or has not published the year yet):
    the month view shows them, so an empty list is never mistaken for a year with no closures."""
    out = []
    for k in LISTS:
        entry = data()[k]
        if not entry["dates"]:
            out.append(f"{entry['name']} are not listed here: the official page could not be read. Add them under Settings, Court closures the firm adds.")
        elif year not in entry["years"]:
            out.append(f"{entry['name']} for {year} are not listed here yet. Add them under Settings, Court closures the firm adds.")
    return out


def sources() -> list[dict[str, Any]]:
    """Where each list came from, for the page: its name, official page, the day it was read (None: not read)."""
    return [{"id": k, "name": data()[k]["name"], "source": data()[k]["source"], "read_on": data()[k]["read_on"], "note": data()[k].get("note")} for k in LISTS]


def follows() -> str:
    """Which courts observe which list, for the sentence above the month view."""
    return ("The federal holidays are the days the Office of Personnel Management lists. The Florida days listed are the Sixth District Court of "
            "Appeal's own list, not every Florida court's: a Florida trial court follows its own circuit's list, and a Massachusetts court the state's. "
            "Which days the office or court you file with observes is for that office to say: add the days a court you file in observes under "
            "Settings, Court closures the firm adds.")
