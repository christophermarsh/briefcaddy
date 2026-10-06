"""Document-processing helper."""

from __future__ import annotations

import re
from datetime import date

from .base import ExtractedField, cm_to_feet_inches, find_sex, find_value_after_label

_NATIONALITY_TOKENS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"BRASILEIRO\(A\)|BRASILEIRA|BRASILEIRO", re.I), "BRAZIL"),
    (re.compile(r"ITALIANA|ITALIANO", re.I), "ITALY"),
]

# Supporting implementation.
# Supporting implementation.
# _NATIONALITY_TOKENS and src/extract/drivers_license.py's AAMVA codes.
_EYE_COLOR_TOKENS_IT: list[tuple[re.Pattern, str]] = [
    (re.compile(r"MARRONI|MARRONE", re.I), "Brown"),
]


def extract(text: str) -> list[ExtractedField]:
    fields: list[ExtractedField] = []

    for pattern, country in _NATIONALITY_TOKENS:
        match = pattern.search(text)
        if match:
            fields.append(ExtractedField("applicant.citizenship", match.group(0), country, 0.9))
            break

    height_cm = find_value_after_label(text, r"STATURA[^\n]*", r"\d{2,3}")
    if height_cm:
        fields.append(
            ExtractedField("applicant.height", f"{height_cm} cm", cm_to_feet_inches(int(height_cm)), 0.85)
        )

    for pattern, color in _EYE_COLOR_TOKENS_IT:
        eye_color_raw = find_value_after_label(text, r"COLORE\s*DEGLI\s*OCCHI", pattern.pattern)
        if eye_color_raw:
            fields.append(ExtractedField("applicant.eye_color", eye_color_raw, color, 0.85))
            break

    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    sex = find_sex(text)
    if sex:
        fields.append(ExtractedField("applicant.sex", sex, sex, 0.85))

    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    mrz = read_mrz(text)
    if mrz:
        fields.append(ExtractedField(f"folder.passport.{mrz['number']}", mrz["raw"], f"{mrz['issuer']}|{mrz['expiry']}", 0.95))

    return fields


_LETTER_FIX = str.maketrans("0158", "OISB")
_DIGIT_FIX = str.maketrans("OQDILSBZ", "00011582")
_ISSUERS = {"BRA": "BRAZIL", "ITA": "ITALY", "PRT": "PORTUGAL", "COL": "COLOMBIA", "MEX": "MEXICO", "HTI": "HAITI",
            "DOM": "DOMINICAN REPUBLIC", "CUB": "CUBA", "ECU": "ECUADOR", "GTM": "GUATEMALA", "HND": "HONDURAS",
            "SLV": "EL SALVADOR", "VEN": "VENEZUELA", "PER": "PERU", "BOL": "BOLIVIA", "ESP": "SPAIN", "USA": "USA"}


def mrz_check_digit(field: str) -> int:
    """Document-processing helper."""
    total = 0
    for i, ch in enumerate(field):
        value = int(ch) if ch.isdigit() else (ord(ch) - 55 if "A" <= ch <= "Z" else 0)
        total += value * (7, 3, 1)[i % 3]
    return total % 10


_CONFUSIONS = {"0": "OQD", "O": "0Q", "Q": "O0", "D": "0", "1": "IL", "I": "1L", "L": "1I", "5": "S", "S": "5",
               "8": "B", "B": "8", "2": "Z", "Z": "2", "6": "G", "G": "6"}


def _repair(field: str, check: int) -> str | None:
    """Document-processing helper."""
    fixes = {field[:i] + alt + field[i + 1:] for i, ch in enumerate(field) for alt in _CONFUSIONS.get(ch, "")
             if mrz_check_digit(field[:i] + alt + field[i + 1:]) == check}
    return fixes.pop() if len(fixes) == 1 else None


def mrz_name(text: str) -> tuple[str, str] | None:
    """Document-processing helper."""
    line1 = re.search(r"^P[<A-Z ][A-Z]{3}([A-Z< ]*?)<<([A-Z< ]*)$", text, re.M)
    if not line1:
        return None
    surname = " ".join(line1.group(1).replace(" ", "").split("<")).strip()
    given = " ".join(w for w in line1.group(2).replace(" ", "").split("<") if w)
    return (surname, given) if surname and given else None


def read_mrz(text: str) -> dict | None:
    """Document-processing helper."""
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    line1 = re.search(r"^P[<A-Z ]([A-Z]{3})[A-Z< ]*<<[A-Z< ]*$", text, re.M)
    for raw in re.findall(r"^[A-Z0-9<][A-Z0-9< ]{40,50}$", text, re.M):
        line = raw.replace(" ", "")
        if len(line) < 28:
            continue
        number, n_check = line[0:9], line[9].translate(_DIGIT_FIX)
        dob, d_check = line[13:19].translate(_DIGIT_FIX), line[19].translate(_DIGIT_FIX)
        exp, e_check = line[21:27].translate(_DIGIT_FIX), line[27].translate(_DIGIT_FIX)
        if not (n_check.isdigit() and d_check.isdigit() and e_check.isdigit() and exp.isdigit() and dob.isdigit()):
            continue
        if mrz_check_digit(exp) != int(e_check) or mrz_check_digit(dob) != int(d_check):
            continue
        if mrz_check_digit(number) != int(n_check):
            number = _repair(number, int(n_check))  # Supporting implementation.
            if number is None:
                continue
        issuer = line1.group(1) if line1 else line[10:13].translate(_LETTER_FIX)
        yy, mm, dd = int(exp[:2]), int(exp[2:4]), int(exp[4:6])
        try:
            expiry = date(2000 + yy, mm, dd).isoformat()
        except ValueError:
            continue
        return {"number": number.replace("<", ""), "issuer": _ISSUERS.get(issuer, issuer), "expiry": expiry, "raw": raw.strip()}
    return None
