"""Document-processing helper."""

from __future__ import annotations

import re

from .arrival import ADMITTED, NOT_ADMITTED, STATE_CODES, STATE_NAMES, parse_date, read_arrival
from .base import ExtractedField, find_date_after_label, normalize_date

_OPTIONS = [
    ("arriving_alien", re.compile(r"^(.{0,8}?)You are an arriving alien", re.M | re.I)),
    ("not_admitted_or_paroled", re.compile(r"^(.{0,8}?)You are an alien present in the United States who has not been admitted", re.M | re.I)),
    ("admitted_removable", re.compile(r"^(.{0,8}?)You have been admitted to the United States", re.M | re.I)),
]
_UNMARKED = re.compile(r"\[\s*_*\s*\]|^\s*$")
_NATIVE = re.compile(r"native of\s+([A-Z][A-Z ]+?)\s+and a citizen of\s+([A-Z][A-Z ]+?)\s*[;,.]", re.I)

# Supporting implementation.
MANNERS = {"arriving_alien": "arriving alien", "not_admitted_or_paroled": "present without admission or parole",
           "admitted_removable": "admitted but removable"}

_ALLEGATIONS_START = re.compile(r"alleges\s+that\s+you\b[^\n]*\n", re.I)
_ALLEGATIONS_END = re.compile(r"on\s+the\s+basis\s+of\s+the\s+foregoing|it\s+is\s+charged", re.I)
_ALLEGATION_LINE = re.compile(r"^\s*(\d{1,2})\s*[.)]\s+(\S.*)$")
_CHARGE = re.compile(r"provisions?\s*(?:\(s\))?\s+of\s+law\s*:?[ \t]*\n?", re.I)
_CHARGE_END = re.compile(r"^\s*(?:YOU\s+ARE\s+ORDERED|Notice\s+to\s+Respondent|Form\s+I-862|Warning\b|Date\s*:|Signature)", re.I)
_RESIDING = re.compile(r"currently\s+residing\s+at\s*:?[ \t]*", re.I)
_ADDRESS_END = re.compile(r"^\s*(?:\[|X[xX]?\s|You\s+(?:are|have)\b|The\s+Department\s+of\s+Homeland|\(Number|In\s+the\s+Matter|Respondent\s*:|Subject\s+ID|FINS|File\s*No|Event\s*No|DOB)", re.I)
# Supporting implementation.
_CAPTION = re.compile(r"\(\s*(?:Number,\s*street|Area\s+code)[^)]*\)?", re.I)
# Supporting implementation.
_PHONE = re.compile(r"(?:\+\s*1\s*)?\(\s*(\d{3})\s*\)\s*-?\s*(\d{3})\s*-\s*(\d{4})\b")
_ORDERED = re.compile(r"appear\s+before\s+an?\s+immigration\s+judge[^\n]*?\bat\s*:?[ \t]*\n?", re.I)
_TIME = r"\d{1,2}:\d{2}\s*[AaPp]\.?\s?[Mm]\.?"
_WHEN = re.compile(rf"^\s*on\s+(?P<date>[A-Za-z]{{3,9}}\.?\s+\d{{1,2}},?\s+\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{4}})\s*(?:at\s+(?P<time>{_TIME}))?", re.I)
_TO_BE_SET = re.compile(r"^\s*on\s+a\s+date\s+to\s+be\s+(?:set|determined)", re.I)
_EVENT = re.compile(r"Event\s*(?:No|Number|#)\.?\s*:?\s*([A-Z0-9]{8,16})\b", re.I)


def _close(word: str, target: str, within: int = 2) -> bool:
    """Document-processing helper."""
    if abs(len(word) - len(target)) > within:
        return False
    previous = list(range(len(target) + 1))
    for i, a in enumerate(word, start=1):
        current = [i]
        for j, b in enumerate(target, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1] <= within


def read_address(printed: str) -> dict:
    """Document-processing helper."""
    tokens = re.sub(r",", " , ", printed or "").split()
    kept, unread = [], 0
    for token in tokens:
        if re.fullmatch(r"[A-Za-z][A-Za-z.'/-]*|\d+(?:-\d{4})?|#\w+|,", token):
            kept.append(token)
        else:
            unread += 1
    text = re.sub(r"\s*,\s*", ", ", " ".join(kept)).strip(" ,")
    upper = text.upper()
    words = [w for w in re.findall(r"[A-Z][A-Z.'-]*", upper) if len(w) >= 2]
    state = None
    for n in (3, 2, 1):  # Supporting implementation.
        for i in range(len(words) - n + 1):
            name = " ".join(words[i:i + n])
            if name in STATE_NAMES:
                state = STATE_NAMES[name]
                break
            if n == 1 and len(name) >= 6:
                close = next((s for s in STATE_NAMES if " " not in s and _close(name, s)), None)
                if close:
                    state = STATE_NAMES[close]
                    break
        if state:
            break
    if state is None:  # Supporting implementation.
        state = next((w for w in reversed(words) if w in STATE_CODES), None)
    found = re.search(r"(?<!\d)(\d{5})(?:-\d{4})?(?!\d)", printed or "")
    zip_code = found.group(1) if found else None
    street = re.search(r"(?<![\w#])(\d{1,6})\s+([A-Z][A-Z.'-]{2,})", upper)
    return {"text": text, "number": street.group(1) if street else None, "street": street.group(2).rstrip(".") if street else None,
            "state": state, "zip": zip_code, "readable": bool(zip_code and state and not unread)}


def _lines_after(text: str, start: int, stop: re.Pattern, limit: int = 3) -> list[str]:
    """Document-processing helper."""
    out: list[str] = []
    for line in text[start:].splitlines():
        if not line.strip():
            if out:
                break
            continue
        if stop.match(line) or len(out) >= limit:
            break
        out.append(re.sub(r"\s+", " ", line).strip())
    return out


def allegations(text: str) -> list[tuple[int, str]]:
    """Document-processing helper."""
    start = _ALLEGATIONS_START.search(text)
    block = text[start.end():] if start else text
    end = _ALLEGATIONS_END.search(block)
    block = block[:end.start()] if end else block
    out: list[tuple[int, str]] = []
    for line in block.splitlines():
        m = _ALLEGATION_LINE.match(line)
        if m:
            out.append((int(m.group(1)), m.group(2).strip()))
        elif out and line.strip():
            out[-1] = (out[-1][0], out[-1][1] + " " + line.strip())
    return [(n, re.sub(r"\s+", " ", t).strip()) for n, t in out]


def extract(text: str) -> list[ExtractedField]:
    fields = [ExtractedField("applicant.nta_present", "NOTICE TO APPEAR", "Yes", 0.95)]

    marked = [name for name, pattern in _OPTIONS if (m := pattern.search(text)) and not _UNMARKED.search(m.group(1))]
    if len(marked) == 1:  # Supporting implementation.
        fields.append(ExtractedField("applicant.nta_admission_status", marked[0], marked[0], 0.85))

    native = _NATIVE.search(text)
    if native:
        birth, citizen = native.group(1).strip().upper(), native.group(2).strip().upper()
        fields.append(ExtractedField("applicant.country_of_birth", native.group(1), birth, 0.9))
        fields.append(ExtractedField("applicant.citizenship", native.group(2), citizen, 0.9))
        fields.append(ExtractedField("nta.native_of", native.group(0), birth, 0.9))
        fields.append(ExtractedField("nta.citizen_of", native.group(0), citizen, 0.9))

    dob = find_date_after_label(text, "DOB")
    if dob:
        normalized = normalize_date(dob)
        if normalized:
            fields.append(ExtractedField("applicant.dob", dob, normalized, 0.85))

    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    arrivals: list[tuple[str, object]] = []
    other_arrivals: list[str] = []
    manners: set[str] = set()
    for number, said in allegations(text):
        fields.append(ExtractedField(f"nta.allegation{number}", said, said.rstrip(";,. "), 0.9))
        arrival = read_arrival(said)
        if arrival is not None:
            arrivals.append((said, arrival))
        elif re.search(r"\b(?:arrived|entered|came|crossed)\b.*\bUnited\s+States\b", said, re.I):
            other_arrivals.append(said)  # Supporting implementation.
        if re.match(r"you\b", said, re.I):
            if NOT_ADMITTED.search(said):
                manners.add("not_admitted_or_paroled")
            if ADMITTED.search(said):
                manners.add("admitted_removable")
    stated = len(arrivals) + len(other_arrivals)
    if stated == 1 and arrivals:
        arrival = arrivals[0][1]
        fields.append(ExtractedField("nta.arrival_place", arrival.raw, arrival.place.text.upper(), 0.9))
        if arrival.place.city and arrival.place.state:
            fields.append(ExtractedField("nta.arrival_city", arrival.raw, arrival.place.city, 0.9))
            fields.append(ExtractedField("nta.arrival_state", arrival.raw, arrival.place.state, 0.9))
        else:  # Supporting implementation.
            fields.append(ExtractedField("nta.arrival_text", arrival.raw, arrival.place.text, 0.9))
        if arrival.date:
            fields.append(ExtractedField("nta.arrival_date", arrival.raw, arrival.date, 0.9))
            fields.append(ExtractedField("nta.arrival_date_qualifier", arrival.raw, arrival.qualifier, 0.9))
    elif stated >= 1:
        said_all = [s for s, _a in arrivals] + other_arrivals
        fields.append(ExtractedField("nta.arrival_text", " | ".join(said_all), " | ".join(x.rstrip(";,. ") for x in said_all), 0.9))
        fields.append(ExtractedField("nta.arrival_count", str(stated), stated, 0.9))
    # Supporting implementation.
    # Supporting implementation.
    manner_said = next(iter(manners)) if len(manners) == 1 else None
    manner = marked[0] if len(marked) == 1 else manner_said
    if len(marked) == 1 and manners and manners != {marked[0]}:
        manner = None
    if manner:
        fields.append(ExtractedField("nta.arrival_manner", MANNERS[manner], MANNERS[manner], 0.85))

    charge = _CHARGE.search(text)
    if charge:
        lines = _lines_after(text, charge.end(), _CHARGE_END, limit=6)
        if lines:
            fields.append(ExtractedField("nta.charge", " | ".join(lines), "; ".join(lines), 0.85))

    residing = _RESIDING.search(text)
    if residing:
        same_line = re.split(r"\s*\(\s*Number", text[residing.end():].split("\n", 1)[0])[0].strip()  # Supporting implementation.
        lines = [same_line] if same_line else _lines_after(text, residing.end(), _ADDRESS_END, limit=3)
        lines = [ln for ln in (_CAPTION.sub("", ln).strip() for ln in lines) if ln]
        # Supporting implementation.
        phone = _PHONE.search(" ".join(lines))
        if phone:
            fields.append(ExtractedField("nta.respondent_phone", phone.group(0), f"({phone.group(1)}) {phone.group(2)}-{phone.group(3)}", 0.85))
            lines = [ln for ln in (_PHONE.sub("", ln).strip(" ,") for ln in lines) if ln]
        if lines:
            fields.append(ExtractedField("nta.respondent_address", " | ".join(lines), re.sub(r"\s+", " ", ", ".join(lines)).strip(), 0.85))

    ordered = _ORDERED.search(text)
    if ordered:
        place = _lines_after(text, ordered.end(), re.compile(r"^\s*on\s+", re.I), limit=3)
        after = text[ordered.end():].splitlines()
        when_line = next((ln for ln in after if re.match(r"\s*on\s+", ln, re.I)), None)
        if place:
            fields.append(ExtractedField("nta.hearing_place", " | ".join(place), re.sub(r"\s+", " ", ", ".join(place)).strip(), 0.85))
        if when_line:
            if _TO_BE_SET.match(when_line):
                fields.append(ExtractedField("nta.hearing_status", when_line.strip(), "to be set", 0.85))
            elif (m := _WHEN.match(when_line)) and parse_date(m.group("date")):
                fields.append(ExtractedField("nta.hearing_date", m.group(0).strip(), parse_date(m.group("date")), 0.85))
                if m.group("time"):
                    clock = re.match(r"(\d{1,2}):(\d{2})\s*([AaPp])", m.group("time"))
                    fields.append(ExtractedField("nta.hearing_time", m.group("time"), f"{int(clock.group(1))}:{clock.group(2)} {clock.group(3).upper()}M", 0.85))

    from .eoir_notice import a_number

    number = a_number(text)
    if number:
        raw = next((ln.strip() for ln in text.splitlines() if re.search(r"File\s*(?:No|Number)", ln, re.I) and re.search(r"\d{3}", ln)), number)
        fields.append(ExtractedField("nta.file_number", raw, "A" + number, 0.85))
    event = _EVENT.search(text)
    if event:
        fields.append(ExtractedField("nta.event_number", event.group(0), event.group(1).upper(), 0.85))

    return fields
