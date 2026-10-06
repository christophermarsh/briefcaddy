"""Document-processing helper."""

from __future__ import annotations

import re

from .base import ExtractedField

# Supporting implementation.
# Supporting implementation.
_SSN_PATTERN = re.compile(r"\b(\d{3})\s?[-=]\s?(\d{2})\s?[-=]\s?(\d{4})\b")


def extract(text: str) -> list[ExtractedField]:
    match = _SSN_PATTERN.search(text)
    if not match:
        return []
    raw = match.group(0)
    normalized = "-".join(match.groups())
    return [ExtractedField("applicant.ssn", raw, normalized, 0.97)]
