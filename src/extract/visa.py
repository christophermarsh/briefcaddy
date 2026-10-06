"""Document-processing helper."""

from __future__ import annotations

from datetime import datetime

from .base import ExtractedField, find_sex, find_value_after_label

# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
_DOB_PATTERN = r"\d{1,2}[A-Z]{3}\d{4}"


def extract(text: str) -> list[ExtractedField]:
    fields: list[ExtractedField] = []

    sex = find_sex(text)
    if sex:
        fields.append(ExtractedField("applicant.sex", sex, sex, 0.85))

    dob_raw = _find_dob(text)
    if dob_raw:
        try:
            normalized = datetime.strptime(dob_raw, "%d%b%Y").date().isoformat()
            fields.append(ExtractedField("applicant.dob", dob_raw, normalized, 0.85))
        except ValueError:
            pass

    return fields


def _find_dob(text: str) -> str | None:
    # Supporting implementation.
    # Supporting implementation.
    return find_value_after_label(text, r"Birth Date[^\n]*", _DOB_PATTERN)
