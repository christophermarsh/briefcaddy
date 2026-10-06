"""Document-processing helper."""

from __future__ import annotations

import re

from .arrival import parse_date, read_arrival, split_place
from .base import ExtractedField
from .eoir_notice import a_number

_ENTRY_LINE = re.compile(r"^\s*(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+(?P<time>Unknown\s+Time|\d{1,2}:\d{2}(?:\s*[AaPp]\.?[Mm]\.?)?)\s*,\s*(?P<rest>.+?)\s*$", re.M | re.I)
_MANNER_WORDS = (
    (re.compile(r"without\s+inspection", re.I), "present without admission or parole"),
    (re.compile(r"\bparole[d]?\b", re.I), "paroled"),
    (re.compile(r"\badmitted\b|\binspected\s+and\s+admitted\b", re.I), "admitted"),
)


def extract(text: str) -> list[ExtractedField]:
    fields: list[ExtractedField] = []
    number = a_number(text)
    if number:
        fields.append(ExtractedField("i213.file_number", number, "A" + number, 0.85))

    place_done = date_done = False
    line = _ENTRY_LINE.search(text)
    if line:
        raw = re.sub(r"\s+", " ", line.group(0)).strip()
        fields.append(ExtractedField("i213.entry_line", raw, raw, 0.9))
        when = parse_date(line.group("date"))
        if when:
            fields.append(ExtractedField("i213.entry_date", raw, when, 0.9))
            date_done = True
        parts = [p.strip() for p in line.group("rest").split(",")]
        if len(parts) >= 2:
            manner_text, place_text = parts[-1], ", ".join(parts[:-1])
            manner = next((name for pattern, name in _MANNER_WORDS if pattern.search(manner_text)), None)
            if manner:
                fields.append(ExtractedField("i213.entry_manner", raw, manner, 0.85))
            place = split_place(place_text)
            if place.text and not re.fullmatch(r"unknown", place.text, re.I):
                fields.append(ExtractedField("i213.entry_place", raw, place.text.upper(), 0.85))
                place_done = True
                if place.city and place.state:
                    fields.append(ExtractedField("i213.entry_city", raw, place.city, 0.85))
                    fields.append(ExtractedField("i213.entry_state", raw, place.state, 0.85))

    narrative = read_arrival(text)
    if narrative is not None:
        if not place_done:
            fields.append(ExtractedField("i213.entry_place", narrative.raw, narrative.place.text.upper(), 0.85))
            if narrative.place.city and narrative.place.state:
                fields.append(ExtractedField("i213.entry_city", narrative.raw, narrative.place.city, 0.85))
                fields.append(ExtractedField("i213.entry_state", narrative.raw, narrative.place.state, 0.85))
        if not date_done and narrative.date:
            fields.append(ExtractedField("i213.entry_date", narrative.raw, narrative.date, 0.85))
            fields.append(ExtractedField("i213.entry_date_qualifier", narrative.raw, narrative.qualifier, 0.85))
    return fields
