"""Document-processing helper."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_DATE_FORMATS = (
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%Y %B %d",
    "%B %d, %Y",
    "%B %d %Y",
    "%d %B %Y",
)


@dataclass
class ExtractedField:
    """Document-processing helper."""

    fact_key: str
    raw_value: str
    normalized_value: Any
    confidence: float
    # Supporting implementation.
    page: int | None = None
    # Supporting implementation.
    reading_issues: list[str] = field(default_factory=list)


def find_after_label(text: str, *labels: str) -> str | None:
    """Document-processing helper."""
    for label in labels:
        match = re.search(rf"{label}\s*[:\-]?\s*([^\n\r]+)", text, re.I)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return None


def find_value_after_label(text: str, label: str, value_pattern: str, window: int = 150) -> str | None:
    """Document-processing helper."""
    label_match = re.search(label, text, re.I)
    if not label_match:
        return None
    value_match = re.search(value_pattern, text[label_match.end() : label_match.end() + window], re.I)
    return value_match.group(0) if value_match else None


_DATE_PATTERN = r"\d{1,2}/\d{1,2}/\d{4}|\d{4}\s+[A-Z][a-z]+\s+\d{1,2}"


def find_date_after_label(text: str, *labels: str, window: int = 150) -> str | None:
    """Document-processing helper."""
    for label in labels:
        value = find_value_after_label(text, label, _DATE_PATTERN, window=window)
        if value:
            return value
    return None


def normalize_date(raw: str) -> str | None:
    """Document-processing helper."""
    cleaned = raw.strip().rstrip(".,")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def cm_to_feet_inches(cm: float) -> str:
    """Document-processing helper."""
    total_inches = round(cm / 2.54)
    feet, inches = divmod(total_inches, 12)
    return f"{feet}'{inches}\""


def kg_to_lbs(kg: float) -> float:
    """Document-processing helper."""
    return round(kg * 2.20462)


def find_sex(text: str) -> str | None:
    """Document-processing helper."""
    value = find_value_after_label(text, r"Sex[^\n]*", r"\b[MF]\b")
    return value.upper() if value else None
