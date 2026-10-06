"""Document-processing helper."""

from __future__ import annotations

import json
import re
import schema_path
from .base import ExtractedField, find_after_label, normalize_date


def number_issue(value) -> str | None:
    spec = json.loads(schema_path.path("register", "reader_formats").read_text(encoding="utf-8"))["i94_number"]
    return None if re.fullmatch(spec["pattern"], str(value).strip().upper()) else spec["message"]


def _date_field(key: str, raw: str, confidence: float) -> ExtractedField:
    value = normalize_date(raw)
    return ExtractedField(key, raw, value, confidence, reading_issues=[] if value else [
        "The printed date could not be parsed using supported formats. Check the original; enter the date or leave it blank. "
        "A non-date admission notation is not converted into an expiry date."])


def extract(text: str) -> list[ExtractedField]:
    fields: list[ExtractedField] = []

    i94_number = find_after_label(text, "Admission \\(I-94\\) Record Number", "Admission I-94 Record Number")
    if i94_number:
        issue = number_issue(i94_number)
        fields.append(ExtractedField("applicant.i94_number", i94_number, None if issue else i94_number.upper(), 0.98,
                                     reading_issues=[issue] if issue else []))

    admit_until = find_after_label(text, "Admit Until Date")
    if admit_until:
        fields.append(_date_field("applicant.i94_admit_until_date", admit_until, 0.9))

    arrival = find_after_label(text, "Arrival/Issued Date", "Most Recent Date of Entry", "Date of Entry")
    if arrival:
        fields.append(_date_field("applicant.i94_arrival_date", arrival, 0.9))

    admission_class = find_after_label(text, "Class\\s*of\\s*Admission")
    if admission_class:
        fields.append(
            ExtractedField("applicant.i94_class_of_admission", admission_class, admission_class.upper(), 0.95)
        )

    citizenship = find_after_label(text, "Citizenship")
    if citizenship:
        fields.append(ExtractedField("applicant.citizenship", citizenship, citizenship.upper(), 0.95))

    # Supporting implementation.
    # Supporting implementation.
    # DOB than a driver's license scan, whose OCR text often has no
    # adjacent "DOB" label at all (src/extract/drivers_license.py).
    dob = find_after_label(text, "Birth Date")
    if dob:
        fields.append(_date_field("applicant.dob", dob, 0.9))

    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    document_number = find_after_label(text, "Document Number")
    if document_number:
        fields.append(
            ExtractedField("applicant.travel_document_number", document_number, document_number.upper(), 0.9)
        )

    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    family_name = find_after_label(text, "Last/Surname", "Last Name/Surname")
    if family_name:
        fields.append(ExtractedField("applicant.family_name", family_name, family_name.upper(), 0.9))
        fields.append(ExtractedField("applicant.i94_family_name", family_name, family_name.upper(), 0.9))

    given_name = find_after_label(text, "First \\(Given\\) Name", "Given Name")
    if given_name:
        fields.append(ExtractedField("applicant.given_name", given_name, given_name.upper(), 0.9))
        fields.append(ExtractedField("applicant.i94_given_name", given_name, given_name.upper(), 0.9))

    return fields
