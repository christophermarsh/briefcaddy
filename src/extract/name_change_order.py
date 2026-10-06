"""Document-processing helper."""

from __future__ import annotations

import re

from .base import ExtractedField, normalize_date
from .names import fold_name, looks_like_name

_NAME = r"([A-Z][A-Za-z .'-]*[A-Za-z])"
# Supporting implementation.
_NEW = re.compile(rf"(?im)^\s*(?:New\s+Name|Name\s+(?:is\s+)?changed\s+to|Name\s+after\s+(?:the\s+)?change)\s*[:;]\s*{_NAME}\s*$")
# Supporting implementation.
_OLD = re.compile(rf"(?im)^\s*(?:Former\s+Name|Present\s+Name|Prior\s+Name|Name\s+before\s+(?:the\s+)?change)\s*[:;]\s*{_NAME}\s*$")
# Supporting implementation.
# Supporting implementation.
_SENTENCE = re.compile(r"name\s+of\s+([A-Z][A-Za-z .'-]*?[A-Za-z])\s*,?\s+(?:be\s+|is\s+)?(?:and\s+(?:hereby\s+)?is\s+)?(?:hereby\s+)?"
                       rf"changed\s+to\s+{_NAME}\s*[.,;\n]", re.I)
_DATE = re.compile(r"(?im)^\s*(?:Date(?:\s+of\s+(?:Decree|Order|Judgment))?|Dated|Entered|So\s+ordered(?:\s+on)?)\s*[:;,]?\s*"
                   r"(\d{1,2}/\d{1,2}/\d{4}|[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})")


def extract(text: str) -> list[ExtractedField]:
    fields: list[ExtractedField] = []
    new, old = _NEW.search(text), _OLD.search(text)
    sentence = _SENTENCE.search(text)
    new_name = fold_name(new.group(1)) if new else fold_name(sentence.group(2)) if sentence else ""
    old_name = fold_name(old.group(1)) if old else fold_name(sentence.group(1)) if sentence else ""
    if looks_like_name(new_name):
        fields.append(ExtractedField("applicant.name_change.new_name", (new or sentence).group(0).strip(), new_name, 0.8))
    if looks_like_name(old_name):
        fields.append(ExtractedField("applicant.name_change.former_name", (old or sentence).group(0).strip(), old_name, 0.8))
    when = _DATE.search(text)
    iso = normalize_date(re.sub(r"\s+", " ", when.group(1))) if when else None
    if iso:
        fields.append(ExtractedField("applicant.name_change.date", when.group(1), iso, 0.8))
    return fields
