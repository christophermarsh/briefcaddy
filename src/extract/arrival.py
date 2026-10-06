"""Document-processing helper."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .names import fold_name

# Supporting implementation.
STATE_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT",
    "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL",
    "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT",
    "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "PUERTO RICO": "PR", "GUAM": "GU", "VIRGIN ISLANDS": "VI", "AMERICAN SAMOA": "AS", "NORTHERN MARIANA ISLANDS": "MP",
}
STATE_CODES = frozenset(STATE_NAMES.values())

_DATE = r"(?:[A-Za-z]{3,9}\.?\s+\d{1,2}\s*,?\s*\d{4}|\d{1,2}/\d{1,2}/\d{4})"
_PLACE_STATE = re.compile(r"^(?P<city>[A-Za-z][A-Za-z .'\-]*?)\s*,\s*(?P<state>[A-Za-z][A-Za-z .]*?)\.?$")
# Supporting implementation.
ARRIVED = re.compile(
    rf"(?:arrived\s+in|entered|came\s+into|crossed\s+into)\s+the\s+United\s+States\s+(?P<at>at\s+or\s+near|near|at)\s+(?P<place>[^;]+?)\s*,?\s+"
    rf"(?P<qualifier>on\s+or\s+about|on|in\s+or\s+about)\s+(?P<date>{_DATE})", re.I)
# Supporting implementation.
ARRIVED_LOOSE = re.compile(
    r"(?:arrived\s+in|entered|came\s+into|crossed\s+into)\s+the\s+United\s+States\s+(?P<at>at\s+or\s+near|near|at)\s+(?P<place>[^;]+?)\s*,?\s+"
    r"(?P<qualifier>on\s+or\s+about|on|in\s+or\s+about)\s+(?P<date>[^;]+?)\s*(?:;|\.\s*$|$)", re.I)
# Supporting implementation.
_NOT_A_CITY = re.compile(r"\b(?:river|bridge|port\s+of\s+entry|border|near|checkpoint|crossing|international|airport)\b|^the\b", re.I)
NOT_ADMITTED = re.compile(r"\bnot\s+(?:then\s+)?(?:been\s+)?admitted\s+or\s+paroled\b|\bwithout\s+(?:being\s+)?(?:inspected|inspection)\b", re.I)
ADMITTED = re.compile(r"\byou\s+were\s+admitted\s+to\s+the\s+United\s+States\b", re.I)


def parse_date(raw: str) -> str | None:
    """Document-processing helper."""
    from .eoir_notice import _date

    return _date(re.sub(r"\s*,\s*", ", ", re.sub(r"\s+", " ", raw).strip()))


@dataclass
class Place:
    text: str                  # Supporting implementation.
    city: str | None = None    # Supporting implementation.
    state: str | None = None   # Supporting implementation.


def split_place(text: str) -> Place:
    """Document-processing helper."""
    printed = re.sub(r"\s+", " ", text or "").strip(" .;")
    m = _PLACE_STATE.match(printed)
    if m:
        state = re.sub(r"\s+", " ", m.group("state")).upper().strip()
        code = state if state in STATE_CODES else STATE_NAMES.get(state)
        city = fold_name(m.group("city"))
        if code and city and not _NOT_A_CITY.search(m.group("city").strip()):
            return Place(printed, city, code)
    return Place(printed)


def city_and_token(text: str) -> tuple[str, str] | None:
    """Document-processing helper."""
    printed = re.sub(r"\s+", " ", text or "").strip(" .;")
    m = _PLACE_STATE.match(printed)
    if not m or split_place(printed).state:
        return None
    city = fold_name(m.group("city"))
    if not city or _NOT_A_CITY.search(m.group("city").strip()):
        return None
    return city, m.group("state").strip()


@dataclass
class Arrival:
    place: Place
    date: str | None           # Supporting implementation.
    qualifier: str             # Supporting implementation.
    raw: str                   # Supporting implementation.


def read_arrival(text: str) -> Arrival | None:
    """Document-processing helper."""
    flat = re.sub(r"\s+", " ", text or "")
    m = ARRIVED.search(flat) or ARRIVED_LOOSE.search(flat)
    if not m:
        return None
    return Arrival(split_place(m.group("place")), parse_date(m.group("date")), re.sub(r"\s+", " ", m.group("qualifier")).lower(), m.group(0))
