"""Document-processing helper."""

from __future__ import annotations

import re

from .base import ExtractedField, find_after_label, find_date_after_label, normalize_date

# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
_RECEIPT_NUMBER = re.compile(r"\b[A-Z]{3}\d{10}\b")

# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
_A_NUMBER = re.compile(r"\bA\s?\d{3}\s?\d{3}\s?\d{3}\b")


def extract(text: str) -> list[ExtractedField]:
    fields: list[ExtractedField] = []

    receipt_match = _RECEIPT_NUMBER.search(text)
    if receipt_match:
        receipt_number = receipt_match.group(0)
        fields.append(ExtractedField("applicant.i360_receipt_number", receipt_number, receipt_number, 0.98))

    a_number_match = _A_NUMBER.search(text)
    if a_number_match:
        raw = a_number_match.group(0)
        normalized = "A" + re.sub(r"\D", "", raw)[-9:]
        fields.append(ExtractedField("applicant.a_number", raw, normalized, 0.9))

    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    beneficiary = re.search(r"Beneficiary[^\n]*\n\s*[^\n]*?\b([A-Z][A-Z'-]+(?: [A-Z][A-Z'-]+)*),\s*([A-Z][A-Z'-]+(?: [A-Z][A-Z'-]+)*)", text)
    if beneficiary:
        family, given = beneficiary.group(1), beneficiary.group(2)
        raw = f"{family}, {given}"
        fields.append(ExtractedField("applicant.family_name", raw, family, 0.9))
        fields.append(ExtractedField("applicant.given_name", raw, given, 0.9))

    priority_date = find_date_after_label(text, "Priority Date")
    if priority_date:
        normalized = normalize_date(priority_date)
        if normalized:
            fields.append(ExtractedField("applicant.i360_priority_date", priority_date, normalized, 0.9))

    current_status = find_after_label(text, "Current Status")
    if current_status:
        fields.append(ExtractedField("applicant.i360_current_status", current_status, current_status.strip("."), 0.95))

    return fields
