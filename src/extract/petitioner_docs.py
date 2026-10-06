"""Document-processing helper."""

from __future__ import annotations

import re

from .base import ExtractedField, normalize_date

_DATE = r"(\d{1,2}[ /.-][A-Z]{3}[ /.-]\d{2,4}|\d{1,2}/\d{1,2}/\d{2,4}|[A-Z][a-z]+ \d{1,2}, \d{4}|\d{2} [A-Z]{3} \d{4})"


def _rekey(fields: list[ExtractedField], keep: set[str]) -> list[ExtractedField]:
    out = []
    for f in fields:
        short = f.fact_key.split(".", 1)[1]
        if f.fact_key.startswith("applicant.") and short in keep:
            out.append(ExtractedField("petitioner." + short, f.raw_value, f.normalized_value, f.confidence))
    return out


def us_passport(text: str) -> list[ExtractedField]:
    from .passport import extract as passport

    out = _rekey(passport(text), {"family_name", "given_name", "middle_name", "dob", "sex", "birth_city", "country_of_birth"})
    out.append(ExtractedField("petitioner.status", "U.S. passport", "USC", 0.95))
    out.append(ExtractedField("petitioner.us_passport", "U.S. passport", "Yes", 0.95))
    return out


def citizenship_certificate(text: str) -> list[ExtractedField]:
    out = [ExtractedField("petitioner.status", "certificate of naturalization / citizenship", "USC", 0.9),
           ExtractedField("petitioner.has_certificate", "certificate in the folder", "Yes", 0.9)]
    if re.search(r"CERTIFICATE\s+OF\s+CITIZENSHIP|N-56[01]|CONSULAR\s+REPORT\s+OF\s+BIRTH|FS-240", text, re.I):
        out.append(ExtractedField("petitioner.citizenship_how", "certificate of citizenship / consular report of birth", "parents", 0.8))
    elif re.search(r"NATURALIZATION|N-5[57]0", text, re.I):
        out.append(ExtractedField("petitioner.citizenship_how", "certificate of naturalization", "naturalization", 0.85))
    number = re.search(r"Certificate\s*(?:No\.?|Number|#)\s*[:.]?\s*([A-Z]?\d{6,9})", text, re.I)
    if number:
        out.append(ExtractedField("petitioner.certificate_number", number.group(0), number.group(1).upper(), 0.8))
    a = re.search(r"(?:USCIS\s*/?\s*A-?Number|A-?Number|Registration\s+No\.?)\s*[:.]?\s*A?\s*(\d{3}[ -]?\d{3}[ -]?\d{3})", text, re.I)
    if a:
        out.append(ExtractedField("petitioner.a_number", a.group(0), "A" + re.sub(r"\D", "", a.group(1)), 0.8))
    when = re.search(r"(?:Date\s+of\s+Naturalization|became\s+a\s+citizen[^.]{0,40}?on|Date\s+of\s+Issuance)\s*[:.]?\s*" + _DATE, text, re.I)
    if when and normalize_date(when.group(1)):
        out.append(ExtractedField("petitioner.certificate_date", when.group(0), normalize_date(when.group(1)), 0.7))
    return out


def green_card(text: str) -> list[ExtractedField]:
    out = [ExtractedField("petitioner.status", "permanent resident card", "LPR", 0.9)]
    a = re.search(r"USCIS\s*#\s*[:.]?\s*A?\s*(\d{3}[ -]?\d{3}[ -]?\d{3})", text, re.I)
    if a:
        out.append(ExtractedField("petitioner.a_number", a.group(0), "A" + re.sub(r"\D", "", a.group(1)), 0.85))
    cat = re.search(r"Category\s*[:.]?\s*([A-Z]{1,2}\d{1,2})\b", text)
    if cat:
        out.append(ExtractedField("petitioner.lpr_class", cat.group(0), cat.group(1), 0.8))
    since = re.search(r"Resident\s+Since\s*[:.]?\s*" + _DATE, text, re.I)
    if since and normalize_date(since.group(1)):
        out.append(ExtractedField("petitioner.lpr_date", since.group(0), normalize_date(since.group(1)), 0.8))
    return out


def us_birth_certificate(text: str) -> list[ExtractedField]:
    out = [ExtractedField("petitioner.status", "U.S. birth certificate", "USC", 0.85),
           ExtractedField("petitioner.citizenship_how", "born in the United States", "birth", 0.85),
           ExtractedField("petitioner.country_of_birth", "U.S. birth certificate", "USA", 0.85)]
    return out


def tax_return(text: str) -> list[ExtractedField]:
    """Document-processing helper."""
    year = re.search(r"Form\s*1040[^\n]{0,60}?\b(20\d{2})\b|Tax\s+(?:Period|Year)[^\n]{0,20}?\b(20\d{2})\b|for\s+the\s+year\s+Jan\.?\s*1[^\n]{0,30}?(20\d{2})", text, re.I)
    agi = re.search(r"(?:adjusted\s+gross\s+income|total\s+income)[^\n$\d]{0,80}\$?\s*([\d,]{3,}(?:\.\d{2})?)", text, re.I)
    if not (year and agi):
        return []
    y = next(g for g in year.groups() if g)
    return [ExtractedField(f"i864.tax_return_{y}", agi.group(0), agi.group(1).replace(",", "").split(".")[0], 0.75)]
