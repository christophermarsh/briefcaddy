"""Document-processing helper."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .base import ExtractedField, normalize_date

_RECEIPT = re.compile(r"\b([A-Z]{3})(\d{10})\b")
_CASE_TYPE = re.compile(r"\b[A-Z]{3}\d{10}\s+([I1l]|G)\s*-?\s*(\d{2,3}[A-Z]?)\s*-\s*([A-Z0-9 ,()'/.&-]{6,}?)\s*$", re.M)
_NOTICE_TYPES = [
    ("REQUEST FOR EVIDENCE", r"Request\s+for\s+(Initial\s+)?Evidence"),
    ("NOTICE OF INTENT TO DENY", r"Notice\s+of\s+Intent\s+to\s+(Deny|Revoke)"),
    ("DENIAL", r"Notice\s+Type:\s*Denial|has\s+been\s+denied|is\s+denied|we\s+denied"),
    ("REJECTION", r"Rejection\s+Notice|has\s+been\s+rejected"),
    ("APPROVAL", r"Approval\s+Notice|has\s+been\s+approved|USCIS\s+has\s+approved"),
    ("TRANSFER", r"Transfer\s+Notice"),
    ("BIOMETRICS APPOINTMENT", r"(ASC|Biometrics?)\s+Appointment"),
    ("INTERVIEW", r"Interview\s+Notice|Request\s+for\s+Applicant\s+to\s+Appear"),
    ("RECEIPT", r"Receipt\s+Notice|has\s+been\s+received|We\s+have\s+received"),
]
KIND = {"REQUEST FOR EVIDENCE": "rfe", "NOTICE OF INTENT TO DENY": "noid", "DENIAL": "denial", "REJECTION": "rejection",
        "APPROVAL": "approval", "TRANSFER": "transfer", "BIOMETRICS APPOINTMENT": "biometrics", "INTERVIEW": "interview", "RECEIPT": "receipt"}
_DATE = r"(\d{1,2}/\d{1,2}/\d{4}|[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})"
_TIME = r"(\d{1,2}:\d{2}\s*[AaPp]\.?[Mm]\.?)"
# Supporting implementation.
_DUE = re.compile(rf"(?:Response\s+Due(?:\s+Date)?|respond(?:\s+\w+){{0,6}}?\s+by|(?:submit|received)[^\n]{{0,80}}?\b(?:by|no\s+later\s+than))\s*:?\s*{_DATE}", re.I)
# Supporting implementation.
_APPT = re.compile(rf"(?:Date\s+and\s+Time\s+of\s+Appointment|Appointment\s+Date(?:\s+and\s+Time)?|^\s*Date|\bOn)\s*:?\s*{_DATE}(?:\s*(?:at|,|\n\s*Time\s*:)?\s*{_TIME})?", re.I | re.M)
_VALID = re.compile(rf"Valid\s+from\s+{_DATE}\s+to\s+{_DATE}", re.I)


@dataclass
class Notice:
    receipt: str | None = None
    form: str | None = None           # Supporting implementation.
    title: str | None = None          # Supporting implementation.
    notice_type: str | None = None    # Supporting implementation.
    notice_date: str | None = None
    received_date: str | None = None
    priority_date: str | None = None
    klass: str | None = None          # Supporting implementation.
    section: str | None = None        # Supporting implementation.
    due_date: str | None = None       # Supporting implementation.
    appointment: str | None = None    # Supporting implementation.
    valid_from: str | None = None     # Supporting implementation.
    valid_to: str | None = None       # Supporting implementation.
    addressed_to: str | None = None   # Supporting implementation.
    where: str | None = None          # Supporting implementation.
    bring: list[str] | None = None    # Supporting implementation.

    @property
    def kind(self) -> str:
        return KIND.get(self.notice_type or "", "notice")

    def describe(self) -> str:
        bits = [self.form or "form unknown", self.notice_type or "notice"]
        extra = ", ".join(x for x in (self.klass and f"class {self.klass}", self.section) if x)
        when = self.notice_date or self.received_date
        return " ".join(bits) + (f" ({extra})" if extra else "") + (f", {when}" if when else "")


def _date_below(text: str, label: str, index: int) -> str | None:
    """Document-processing helper."""
    # Supporting implementation.
    for m in re.finditer(rf"(?im)^[^\n]*{label}[^\n]*\n(?:[ \t]*\n)?([^\n]*)", text):
        dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", m.group(1))
        if len(dates) > index:
            return normalize_date(dates[index])
    return None


def parse(text: str) -> Notice:
    n = Notice()
    m = _RECEIPT.search(text)
    if m:
        n.receipt = m.group(1) + m.group(2)
    case = _CASE_TYPE.search(text)
    if case:
        prefix = "G" if case.group(1) == "G" else "I"
        n.form = f"{prefix}-{case.group(2)}"
        n.title = re.sub(r"\s+", " ", case.group(3)).strip(" .,-")
    for label, pattern in _NOTICE_TYPES:
        if re.search(pattern, text, re.I):
            n.notice_type = label
            break
    n.received_date = _date_below(text, r"Received\s+Date", 0)
    n.priority_date = _date_below(text, r"Priority\s+Date", 1)
    n.notice_date = _date_below(text, r"Notice\s+Date", 0)
    k = re.search(r"Class:\s*([A-Z0-9]{2,4})\b", text)
    n.klass = k.group(1) if k else None
    sec = re.search(r"Section:\s*([A-Za-z][A-Za-z ()/-]{3,60}?)\s*(?:\n|$)", text)
    n.section = sec.group(1).strip() if sec else None
    if n.kind in ("rfe", "noid"):
        due = _DUE.search(text)
        n.due_date = normalize_date(re.sub(r"\s+", " ", due.group(1)).replace(" ,", ",")) if due else None
    if n.kind in ("biometrics", "interview"):
        for m in _APPT.finditer(text):
            day = normalize_date(re.sub(r"\s+", " ", m.group(1)))
            if day and day != n.notice_date:  # Supporting implementation.
                n.appointment = day + (f" {m.group(2).upper().replace('.', '').replace('  ', ' ')}" if m.group(2) else "")
                break
    if n.form == "I-765" and n.kind == "approval":
        v = _VALID.search(text)
        n.valid_from = normalize_date(v.group(1)) if v else None
        n.valid_to = normalize_date(v.group(2)) if v else None
    if n.kind in ("biometrics", "interview"):
        n.where = appointment_place(text)
        n.bring = bring_list(text)
    n.addressed_to = addressed_to(text)
    return n


# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
_BRING_LABEL = re.compile(r"(?:Please\s+bring|You\s+(?:must|should|need\s+to)\s+bring|What\s+to\s+bring|Bring\s+the\s+following|Items\s+to\s+bring)\b", re.I)
_BULLET = re.compile(r"^[ \t]*(?:[-*\u2022\u00b7o]|\d{1,2}[.)])[ \t]+(?=\S)")
MAX_ITEMS, MAX_CHARS = 40, 600


def bring_list(text: str) -> list[str] | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _BRING_LABEL.search(line)
        if not m:
            continue
        head = re.sub(r"\s+", " ", line[m.start():]).strip()
        rest = re.sub(r"(?i)\bthe\s+following\b", "", head[m.end() - m.start():]).strip(" :.-")
        if rest:  # Supporting implementation.
            return [head] if len(head) <= MAX_CHARS else None
        out: list[str] = []
        following = lines[i + 1:]
        if following and not following[0].strip():
            following = following[1:]  # Supporting implementation.
        for raw in following:
            if not _BULLET.match(raw):
                break  # Supporting implementation.
            item = re.sub(r"\s+", " ", _BULLET.sub("", raw)).strip()
            if item:
                out.append(item)
        if not out:
            continue
        return out if len(out) <= MAX_ITEMS and all(len(x) <= MAX_CHARS for x in out) else None
    return None


# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
USPS_STATES = frozenset("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD "
                        "TN TX UT VT VA WA WV WI WY AS GU MP PR VI".split())  # Supporting implementation.
_WHERE_LABEL = re.compile(r"^[ \t]*(?:Please\s+appear\s+at|Appointment\s+Location|Place\s+of\s+Appointment|(?:Location|Place)(?!\s+of\b))"
                          r"\b[ \t]*(?::[ \t]*(.*)|)$", re.I)
_CITY_STATE_ZIP = re.compile(r"[A-Za-z][A-Za-z .'-]*,?\s+([A-Z]{2})\s+(?:\d{5}(?:-\d{4})?|\d{9})\s*$")
_STREET = re.compile(r"^\d+[A-Za-z]?\s+[A-Za-z]")


def appointment_place(text: str) -> str | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _WHERE_LABEL.match(line)
        if not m:
            continue
        block = [m.group(1).strip()] if m.group(1) and m.group(1).strip() else []
        candidates = [block[0]] if block else []
        for following in lines[i + 1:i + 5 - len(block)]:  # Supporting implementation.
            following = following.strip()
            if not following:
                break
            candidates.append(following)
        for n, last in enumerate(candidates):
            end = _CITY_STATE_ZIP.search(last)
            if end and end.group(1) in USPS_STATES and any(_STREET.match(x) for x in candidates[:n + 1]):
                return re.sub(r"\s+", " ", ", ".join(candidates[:n + 1]))[:200]
    return None


# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
_PERSON_LABEL = re.compile(r"\b(?:Petitioner|Applicant|Beneficiary)\b(?!\s+(?:to|Support|Contact))", re.I)
_A_NUM = re.compile(r"(?<![A-Z0-9])A\s?-?#?:?\s?(\d{2,3})[ -]?(\d{3})[ -]?(\d{3})(?!\d)")
_FAMILY_GIVEN = re.compile(r"^([A-Z][A-Z'-]+(?: [A-Z][A-Z'-]+)*), ?([A-Z][A-Z'-]+(?: [A-Z][A-Z'-]+)*)$")
_GIVEN_FAMILY = re.compile(r"^[A-Z][A-Za-z'-]+(?: [A-Z][A-Za-z'-]+){1,4}$")
_NOT_A_NAME = re.compile(r"\b(?:Notice|Date|Page|Receipt|Case|Type|Class|Section|Center|Services?|LLP|LLC|Law|Street|Box|Department|USCIS)\b", re.I)


def person_a_number(text: str) -> str | None:
    """Document-processing helper."""
    for line in text.splitlines():
        stripped = line.strip()
        m = _A_NUM.search(stripped)
        if m and (_PERSON_LABEL.search(stripped) or re.search(r"A-?\s?Number|Alien\s+(?:Registration\s+)?Number|A#", stripped, re.I)
                  or m.group(0) == stripped):
            return "".join(m.groups()).zfill(9)
    return None


_CLIENT_LABEL = re.compile(r"\b(?:Applicant|Beneficiary)\b(?!\s+(?:to|Support|Contact))", re.I)
_FAMILY_COMMA_GIVEN = re.compile(r"\b([A-Z][A-Z'-]+(?: [A-Z][A-Z'-]+)*), ?([A-Z][A-Z'-]+(?: [A-Z][A-Z'-]+)*)\s*$")


def client_name(text: str) -> tuple[str, str] | None:
    """Document-processing helper."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not _CLIENT_LABEL.search(line):
            continue
        for following in lines[i + 1:i + 3]:
            value = re.sub(r"\s+", " ", re.sub(r"\d{1,2}/\d{1,2}/\d{4}", " ", following)).strip()
            if not value:
                continue
            m = _FAMILY_COMMA_GIVEN.search(value)
            if m and not _NOT_A_NAME.search(m.group(0)):
                return m.group(1), m.group(2)
            break
    return None


def person_names(text: str) -> list[str]:
    """Document-processing helper."""
    lines = text.splitlines()
    out: list[str] = []
    for i, line in enumerate(lines):
        if not _PERSON_LABEL.search(line):
            continue
        for following in lines[i + 1:i + 3]:
            value = re.sub(r"\d{1,2}/\d{1,2}/\d{4}|\b\d+ of \d+\b", " ", following)
            value = re.sub(r"\s+", " ", _A_NUM.sub(" ", value)).strip(" ,")
            if not value:
                continue
            if (_FAMILY_GIVEN.match(value) or _GIVEN_FAMILY.match(value)) and not _NOT_A_NAME.search(value) and value not in out:
                out.append(value)
            break
    return out


# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
_FIRM_LINE = re.compile(r"^\s*([A-Z][A-Z&.,' -]{2,60}?\b(?:LLP|LLC|PLLC|P\.?C\.?|LAW(?:\s+(?:OFFICES?|GROUP|FIRM))?|ESQ\.?))\b", re.M)
_CARE_OF = re.compile(r"^\s*(?:c\s*/\s*o|clo|C/O)\s+([A-Z][A-Z&.,' -]{2,60}?)(?=\s*(?:[)|]|\d|Notice\s+Type|Class:|Section:|$))", re.M)


def addressed_to(text: str) -> str | None:
    names = []
    for m in list(_FIRM_LINE.finditer(text)) + list(_CARE_OF.finditer(text)):
        name = re.sub(r"\s+", " ", m.group(1)).strip(" ,.)")
        if name and name not in names and not re.search(r"(?i)\b(U\.?S\.?|USCIS|DEPARTMENT|HOMELAND)\b", name):
            names.append(name)
    return "; ".join(names[:3]) or None


def extract(text: str) -> list[ExtractedField]:
    n = parse(text)
    if not n.receipt:
        return []
    when = n.notice_date or n.received_date
    # Supporting implementation.
    key = f"{n.receipt}.{n.kind}" + (f"_{when.replace('-', '')}" if when else "")
    out = [ExtractedField(f"folder.uscis_case.{key}", n.receipt, n.describe(), 0.9)]
    for name, value in (("date", n.notice_date or n.received_date), ("due", n.due_date), ("appointment", n.appointment), ("valid_from", n.valid_from), ("valid_to", n.valid_to),
                        ("addressed_to", n.addressed_to), ("priority_date", n.priority_date), ("where", n.where),
                        ("bring", "\n".join(n.bring) if n.bring else None)):
        if value:
            out.append(ExtractedField(f"folder.notice.{key}.{name}", value, value, 0.85))
    named = client_name(text)
    if named:  # Supporting implementation.
        out.append(ExtractedField(f"folder.notice.{key}.name", f"{named[0]}, {named[1]}", f"{named[0]}, {named[1]}", 0.85))
    return out


# Supporting implementation.
# Supporting implementation.
BASIS_BY_FORM = {
    "I-130": "family-based (I-130)", "I-129F": "fiance(e) (I-129F)", "I-140": "employment-based (I-140)",
    "I-526": "investor (I-526)", "I-589": "asylum (I-589)", "I-590": "refugee (I-590)",
    "I-730": "asylee/refugee relative (I-730)", "I-914": "T nonimmigrant (I-914)", "I-918": "U nonimmigrant (I-918)",
}
