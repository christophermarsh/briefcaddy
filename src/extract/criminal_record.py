"""Document-processing helper."""

from __future__ import annotations

import re

from .base import ExtractedField, normalize_date

_DOCKET = re.compile(r"\b(\d{4}CR\d{6})\b")
_COUNT = re.compile(r"^\s*(\d{1,2})\s+\d{3}/[\w/]+\s+(.+?)\s+c\d{3}\s*§\s*\S+\s+(\d{2}/\d{2}/\d{4})", re.M)


def extract(text: str) -> list[ExtractedField]:
    fields = [ExtractedField("applicant.criminal_record_present", "CRIMINAL DOCKET", "Yes", 0.95)]
    docket = _DOCKET.search(text)
    if docket:
        fields.append(ExtractedField("applicant.criminal_docket_number", docket.group(1), docket.group(1), 0.9))
    counts = _COUNT.findall(text)
    if counts:
        listed = "; ".join(f"{desc.strip()} ({normalize_date(day) or day})" for _, desc, day in counts)
        fields.append(ExtractedField("applicant.criminal_offenses", listed, listed, 0.8))
    if re.search(r"\bdismiss", text, re.I):
        fields.append(ExtractedField("applicant.criminal_disposition_mentions_dismissal", "dismiss", "Yes", 0.6))
    return fields
