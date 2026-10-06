"""Document-processing helper."""

from __future__ import annotations

import re
from datetime import date

from .base import ExtractedField
import clock

_LINE1 = re.compile(r"[IL1|]AUSA([0-9OILSB]{9})([0-9OILSB])")
_LINE2 = re.compile(r"([0-9OILSB]{6})([0-9OILSB])([MFX<])([0-9OILSB]{6})([0-9OILSB])([A-Z]{3})")
_LINE3 = re.compile(r"^([A-Z]+(?:<[A-Z]+)*)<<([A-Z]+(?:<[A-Z]+)*)<*\s*$", re.M)
_CATEGORY = re.compile(r"\d{3}-\d{3}-\d{3}\s+([ACac][O0]?\d{1,2}[A-Za-z]?)\b")
_DIGIT_FIX = str.maketrans("OILSB", "01158")

# Supporting implementation.
_COUNTRY = {"BRA": "BRAZIL", "ITA": "ITALY", "COL": "COLOMBIA", "HTI": "HAITI", "DOM": "DOMINICAN REPUBLIC",
            "CUB": "CUBA", "ECU": "ECUADOR", "GTM": "GUATEMALA", "HND": "HONDURAS", "SLV": "EL SALVADOR",
            "MEX": "MEXICO", "VEN": "VENEZUELA", "PER": "PERU", "BOL": "BOLIVIA", "PRT": "PORTUGAL"}


def check_digit(digits: str) -> int:
    """Document-processing helper."""
    total = 0
    for i, ch in enumerate(digits):
        value = int(ch) if ch.isdigit() else 0
        total += value * (7, 3, 1)[i % 3]
    return total % 10


def _yymmdd(raw: str, future_ok: bool) -> str | None:
    yy, mm, dd = int(raw[:2]), int(raw[2:4]), int(raw[4:6])
    century = 2000 if (future_ok or 2000 + yy <= clock.today().year) else 1900
    try:
        return date(century + yy, mm, dd).isoformat()
    except ValueError:
        return None


def extract(text: str) -> list[ExtractedField]:
    fields: list[ExtractedField] = []
    compact = text.replace(" ", "")

    line1 = _LINE1.search(compact)
    if line1:
        number, check = line1.group(1).translate(_DIGIT_FIX), line1.group(2).translate(_DIGIT_FIX)
        if check_digit(number) == int(check):
            fields.append(ExtractedField("applicant.a_number", line1.group(0), f"A{number}", 0.99))

    line2 = _LINE2.search(compact)
    if line2:
        dob_raw, dob_check, sex, exp_raw, exp_check, nationality = line2.groups()
        dob_raw, dob_check = dob_raw.translate(_DIGIT_FIX), dob_check.translate(_DIGIT_FIX)
        if check_digit(dob_raw) == int(dob_check):
            dob = _yymmdd(dob_raw, future_ok=False)
            if dob:
                fields.append(ExtractedField("applicant.dob", dob_raw, dob, 0.98))
        if sex in "MF":
            fields.append(ExtractedField("applicant.sex", sex, sex, 0.95))
        exp_raw, exp_check = exp_raw.translate(_DIGIT_FIX), exp_check.translate(_DIGIT_FIX)
        if check_digit(exp_raw) == int(exp_check):
            expiry = _yymmdd(exp_raw, future_ok=True)
            if expiry:
                fields.append(ExtractedField("applicant.ead_expiration_date", exp_raw, expiry, 0.98))
        if nationality in _COUNTRY:
            fields.append(ExtractedField("applicant.citizenship", nationality, _COUNTRY[nationality], 0.9))

    line3 = _LINE3.search(text)
    if line3:
        family, given = (part.replace("<", " ").strip() for part in line3.groups())
        fields.append(ExtractedField("applicant.family_name", line3.group(1), family, 0.9))
        fields.append(ExtractedField("applicant.given_name", line3.group(2), given, 0.9))

    category = _CATEGORY.search(text)
    if category:
        raw = category.group(1).upper().replace("O", "0")
        normalized = raw[0] + raw[1:].zfill(2) if raw[1:].isdigit() else raw
        fields.append(ExtractedField("applicant.ead_category", category.group(1), normalized, 0.8))

    return fields
