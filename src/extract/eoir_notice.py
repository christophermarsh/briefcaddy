"""Document-processing helper."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .base import normalize_date
from .uscis_notice import USPS_STATES

# Supporting implementation.
EOIR = re.compile(r"Executive\s+Office\s+for\s+Immigration\s+Review|\bIMMIGRATION\s+COURT\b|\bIMMIGRATION\s+JUDGE\b", re.I)
HEARING = re.compile(r"NOTICE\s+OF\s+(?:(?:IN-?\s?PERSON|INTERNET-?\s?BASED|VIDEO|TELEPHONIC)\s+)?HEARING", re.I)
DECISION = re.compile(r"(?:ORDER|DECISION)\s+(?:AND\s+ORDER\s+)?OF\s+THE\s+IMMIGRATION\s+JUDGE|\bDECISION\s+AND\s+ORDER\b", re.I)

_DATE = r"(\d{1,2}/\d{1,2}/\d{4}|[A-Z][a-z]{2,8}\.?\s+\d{1,2},?\s+\d{4})"
_TIME = r"(\d{1,2}:\d{2}\s*[AaPp]\.?\s?[Mm]\.?)"
_WHEN = re.compile(rf"(?:Immigration\s+Judge\s+on|Hearing\s+Date\s*:?|hearing\s+on)\s*{_DATE}(?:\s*(?:at|,)?\s*{_TIME})?", re.I)
# Supporting implementation.
_SCHEDULED = re.compile(rf"scheduled\s+for\b[^.]{{0,160}}?\bon\s+{_DATE}(?:\s*(?:at|,)?\s*{_TIME})?", re.I)
HEAD_LINES = 20  # Supporting implementation.
_KIND = re.compile(r"scheduled\s+for\s+an?\s+(MASTER|INDIVIDUAL|MERITS|BOND)\b", re.I)
KINDS = {"MASTER": "Master calendar", "INDIVIDUAL": "Individual (merits)", "MERITS": "Individual (merits)", "BOND": "Bond"}
_A_NUMBER = re.compile(r"(?:\bFILE\s*(?:NO\.?|NUMBER)?\s*:?\s*|\bA-?\s*(?:NUMBER|NO\.?|#)\s*:?\s*)A?\s*-?\s*(\d{2,3})[\s-]?(\d{3})[\s-]?(\d{3})\b", re.I)
_NAME = re.compile(r"^\s*(?:RE|IN\s+THE\s+MATTER\s+OF|RESPONDENT(?:'S)?(?:\s+NAME)?)\s*:\s*([A-Z][A-Z'. -]+,\s*[A-Z][A-Z'. -]+?)\s*(?:\s{2,}|\t|FILE\b|$)", re.I | re.M)
_CITY_STATE_ZIP = re.compile(r"[A-Za-z][A-Za-z .'-]*,?\s+([A-Z]{2})\s+\d{5}(?:-\d{4})?\s*$")


@dataclass
class CourtMail:
    kind: str                       # Supporting implementation.
    a_number: str | None = None     # Supporting implementation.
    name: str | None = None         # Supporting implementation.
    date: str | None = None         # Supporting implementation.
    time: str | None = None         # Supporting implementation.
    hearing_kind: str | None = None  # Supporting implementation.
    place: str | None = None        # Supporting implementation.
    dates: list[str] | None = None  # Supporting implementation.


def _date(raw: str) -> str | None:
    raw = re.sub(r"\s+", " ", raw).replace(" ,", ",").strip()
    found = normalize_date(raw)
    if found:
        return found
    for fmt in ("%b %d, %Y", "%b. %d, %Y", "%b %d %Y"):  # Supporting implementation.
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _time(raw: str | None) -> str | None:
    if not raw:
        return None
    m = re.match(r"(\d{1,2}):(\d{2})\s*([AaPp])", raw)
    return f"{int(m.group(1))}:{m.group(2)} {m.group(3).upper()}M" if m else None


def a_number(text: str) -> str | None:
    m = _A_NUMBER.search(text)
    return ("".join(m.groups())).zfill(9) if m else None


def _place(text: str, start: int) -> str | None:
    """Document-processing helper."""
    after = text[start:start + 400]
    m = re.match(r"\s*at\s*:?\s*", after, re.I)
    if not m:
        return None
    lines = [x.strip() for x in after[m.end():].splitlines()][:4]
    block = []
    for line in lines:
        if not line:
            if block:
                break
            continue
        block.append(line)
        end = _CITY_STATE_ZIP.search(line)
        if end and end.group(1) in USPS_STATES:
            return re.sub(r"\s+", " ", ", ".join(block))[:200]
    return None


def parse(text: str) -> CourtMail | None:
    """Document-processing helper."""
    if not EOIR.search(text or ""):
        return None
    # Supporting implementation.
    # Supporting implementation.
    head = "\n".join(text.splitlines()[:HEAD_LINES])
    kind = "decision" if DECISION.search(text) else "hearing" if HEARING.search(head) else "other"
    mail = CourtMail(kind=kind, a_number=a_number(text))
    name = _NAME.search(text)
    mail.name = re.sub(r"\s+", " ", name.group(1)).strip(" .") if name else None
    if kind == "hearing":
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        when = _SCHEDULED.search(text)
        if when is None:
            found = list(_WHEN.finditer(text))
            dates = list(dict.fromkeys(d for d in (_date(m.group(1)) for m in found) if d))
            if len(dates) > 1:
                mail.dates = dates
            elif found:
                when = found[0]
        if when:
            mail.date = _date(when.group(1))
            mail.time = _time(when.group(2))
            mail.place = _place(text, when.end())
        k = _KIND.search(text)
        mail.hearing_kind = KINDS[k.group(1).upper()] if k else None
    return mail
