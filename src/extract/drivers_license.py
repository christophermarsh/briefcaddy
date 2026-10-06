"""Document-processing helper."""

from __future__ import annotations

import re

from classify.barcode import parse_aamva

from .base import ExtractedField, cm_to_feet_inches, find_after_label, normalize_date

_EYE_COLOR_CODES = {
    "BLK": "Black",
    "BLU": "Blue",
    "BRO": "Brown",
    "GRY": "Gray",
    "GRN": "Green",
    "HAZ": "Hazel",
    "MAR": "Maroon",
    "PNK": "Pink",
    "DIC": "Dichromatic",
}

# Supporting implementation.
_AAMVA_DIRECT_FIELDS = {
    "DCS": "applicant.family_name",
    "DAC": "applicant.given_name",
    "DAD": "applicant.middle_name",
    "DAG": "applicant.address_street",
    "DAI": "applicant.address_city",
    "DAJ": "applicant.address_state",
    "DAK": "applicant.address_zip",
    "DAQ": "applicant.drivers_license_number",
}

# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
_AAMVA_DATE_FORMATS = ("%m%d%Y", "%Y%m%d")

_AAMVA_HEIGHT = re.compile(r"(\d+)\s*(in|cm)", re.I)

_EYE_COLOR_UNLABELED = re.compile(r"\b(" + "|".join(_EYE_COLOR_CODES) + r")\b")

_HEIGHT_PATTERN = re.compile(r"(\d)[^\d]{1,3}0?(\d{1,2})")

# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# the card (a date, a license number). This is checked *before* any
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
_HEIGHT_QUOTE_ANCHORED = re.compile(r'(\d)[^\d]{1,3}(\d{2})"')


def _normalize_height(raw: str) -> str | None:
    compact = re.fullmatch(r"(\d)(\d{2})", raw.strip())
    if compact:
        return f"{compact.group(1)}'{int(compact.group(2))}\""
    match = _HEIGHT_PATTERN.search(raw)
    if match:
        return f"{match.group(1)}'{int(match.group(2))}\""
    return None


def _extract_from_aamva_barcode(text: str) -> list[ExtractedField]:
    from datetime import datetime

    parsed = parse_aamva(text)
    if not parsed:
        return []

    fields: list[ExtractedField] = []

    for code, fact_key in _AAMVA_DIRECT_FIELDS.items():
        value = parsed.get(code)
        if value:
            fields.append(ExtractedField(fact_key, value, value, 0.97))

    eye_color_raw = parsed.get("DAY")
    if eye_color_raw:
        normalized = _EYE_COLOR_CODES.get(eye_color_raw.upper()[:3])
        if normalized:
            fields.append(ExtractedField("applicant.eye_color", eye_color_raw, normalized, 0.97))

    dob_raw = parsed.get("DBB")
    if dob_raw:
        for fmt in _AAMVA_DATE_FORMATS:
            try:
                normalized = datetime.strptime(dob_raw.strip(), fmt).date().isoformat()
                fields.append(ExtractedField("applicant.dob", dob_raw, normalized, 0.95))
                break
            except ValueError:
                continue

    height_raw = parsed.get("DAU")
    if height_raw:
        match = _AAMVA_HEIGHT.match(height_raw.strip())
        if match:
            value, unit = int(match.group(1)), match.group(2).lower()
            normalized = cm_to_feet_inches(value) if unit == "cm" else f"{value // 12}'{value % 12}\""
            fields.append(ExtractedField("applicant.height", height_raw, normalized, 0.95))

    return fields


def extract(text: str) -> list[ExtractedField]:
    barcode_fields = _extract_from_aamva_barcode(text)
    if barcode_fields:
        return barcode_fields

    fields: list[ExtractedField] = []

    eye_color_raw = find_after_label(text, "Eyes", "Eye Color", "EYES")
    eye_confidence = 0.95
    if not eye_color_raw:
        # Real MA license OCR reads "Eyes" as "iseves" -- badly garbled,
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        fallback = _EYE_COLOR_UNLABELED.search(text.upper())
        if fallback:
            eye_color_raw = fallback.group(1)
            eye_confidence = 0.8
    if eye_color_raw:
        code = eye_color_raw.strip().upper()[:3]
        normalized = _EYE_COLOR_CODES.get(code)
        if normalized:
            fields.append(ExtractedField("applicant.eye_color", eye_color_raw.strip(), normalized, eye_confidence))

    quote_match = _HEIGHT_QUOTE_ANCHORED.search(text)
    if quote_match:
        height_raw, confidence = quote_match.group(0), 0.95
    else:
        height_raw = find_after_label(text, "Height", "HGT")
        confidence = 0.95 if height_raw else 0.0
    if height_raw:
        normalized = _normalize_height(height_raw)
        if normalized:
            fields.append(ExtractedField("applicant.height", height_raw.strip(), normalized, confidence))

    dob_raw = find_after_label(text, "DOB", "Date of Birth")
    if dob_raw:
        normalized = normalize_date(dob_raw)
        if normalized:
            fields.append(ExtractedField("applicant.dob", dob_raw.strip(), normalized, 0.9))

    return fields
