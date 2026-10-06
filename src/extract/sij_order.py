"""Document-processing helper."""

from __future__ import annotations

import re

from .base import ExtractedField, normalize_date

STATES = {
    "MASSACHUSETTS": "MA", "NEW YORK": "NY", "NEW JERSEY": "NJ", "CONNECTICUT": "CT", "RHODE ISLAND": "RI", "NEW HAMPSHIRE": "NH",
    "FLORIDA": "FL", "TEXAS": "TX", "CALIFORNIA": "CA", "MARYLAND": "MD", "VIRGINIA": "VA", "PENNSYLVANIA": "PA", "GEORGIA": "GA",
    "NORTH CAROLINA": "NC", "ILLINOIS": "IL", "WASHINGTON": "WA", "COLORADO": "CO", "TENNESSEE": "TN", "LOUISIANA": "LA",
}

_COURT = re.compile(r"^\s*((?:[A-Z][A-Za-z.&,' ]*\s)?(?:PROBATE AND FAMILY COURT|JUVENILE COURT|FAMILY COURT|SUPERIOR COURT|CIRCUIT COURT|"
                    r"DISTRICT COURT|PROBATE COURT|CHANCERY DIVISION|FAMILY PART)[A-Za-z.,' -]*)$", re.I | re.M)
_DOCKET = re.compile(r"(?:Docket|Case|Index|File)\s*(?:No\.?|Number|#)\s*[:.]?\s*([A-Z0-9][A-Z0-9 /.-]{3,24}[A-Z0-9])", re.I)
_DATE = re.compile(r"(?:Date(?:d)?|Entered|So ordered(?: this)?)\s*[:,]?\s*((?:\d{1,2}/\d{1,2}/\d{2,4})|(?:[A-Z][a-z]+ \d{1,2}, \d{4})|(?:\d{1,2}(?:st|nd|rd|th)? day of [A-Z][a-z]+,? \d{4}))", re.I)
_REUNIFY = re.compile(r"reunification[^.]{0,200}?not\s+(?:a\s+)?viable[^.]*\.", re.I | re.S)
# Supporting implementation.
_OPTIONS = re.compile(r"abuse\W+neglect\W+abandonment", re.I)
_CJP37 = re.compile(r"CJP\s*37|JUDGMENT\s+AND\s+FINDINGS\s+ON\s+COMPLAINT\s+FOR\s+DEPENDENCY", re.I)
FORM_MARK = "=== FILLED FORM FIELDS ==="
_CJP37_FIELD = "BodyPage1[0].S8[0].CheckBox3"
_BEST = re.compile(r"not\s+in\s+(?:the\s+)?(?:child's|minor's|juvenile's|his|her|their|the\s+best\s+interest)[^.]{0,40}?best\s+interests?\s+"
                   r"(?:to\s+be\s+)?(?:returned|to\s+return)[^.]{0,120}?\bto\s+([A-Z][A-Za-z ]+?)(?:,|\.| the| which| his| her|$)", re.I | re.S)
_DEPENDENT = re.compile(r"dependent\s+(?:up)?on\s+(?:the|this)\s+(?:juvenile\s+)?court|legally\s+committed\s+to|placed\s+(?:in|under)\s+the\s+custody\s+of|"
                        r"appointed\s+(?:as\s+)?(?:the\s+)?(?:child's\s+|minor's\s+)?guardian", re.I)
_PLACED = re.compile(r"(?:custody\s+of|committed\s+to|(?:remain|continue)\s+in\s+the\s+care\s+of|appointed\s+(?:as\s+)?(?:the\s+)?(?:child's\s+|minor's\s+)?guardian[^,.]{0,30}?:?)\s+"
                     r"(?:the\s+)?([A-Z][A-Za-z.' -]{3,60}?)(?:,|\.|\s+(?:who|on|as|pursuant|under))", re.S)


def _sentence(text: str, match: re.Match) -> str:
    start = text.rfind(".", 0, match.start()) + 1
    end = text.find(".", match.end() - 1)
    return re.sub(r"\s+", " ", text[start:end + 1 if end != -1 else None]).strip()


def form_block(path) -> str:
    """Document-processing helper."""
    from pypdf import PdfReader

    try:
        fields = PdfReader(str(path)).get_fields() or {}
    except Exception:  # noqa: BLE001 -- an unreadable form is just a scan
        return ""
    if not any(_CJP37_FIELD in name for name in fields):
        return ""
    lines = [f"{name}={v.get('/V')}" for name, v in fields.items() if v.get("/V") not in (None, "", "/Off")]
    return "\n" + FORM_MARK + "\n" + "\n".join(lines) if lines else ""


def _from_cjp37_fields(block: str) -> list[ExtractedField]:
    v = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
    get = lambda part: next((val for name, val in v.items() if name.endswith(part)), None)  # noqa: E731
    on = lambda part: get(part) not in (None, "/Off", "Off")  # noqa: E731
    src, out = "CJP 37 form field", []
    add = lambda key, value, conf=0.97: out.append(ExtractedField(key, src, value, conf))  # noqa: E731
    if get("S1[0].docketno[0]"):
        add("sij.docket_number", get("S1[0].docketno[0]").strip().upper())
    if get("S1[0].DropDownList1[0]"):
        add("sij.court_name", f"PROBATE AND FAMILY COURT, {get('S1[0].DropDownList1[0]').strip().upper()}")
    if get("S15[0].DateTimeField1[0]"):
        iso = normalize_date(get("S15[0].DateTimeField1[0]"))
        if iso:
            add("sij.order_date", iso)
    name = lambda prefix: " ".join(x for x in (get(f"{prefix}TextField4[0]"), get(f"{prefix}TextField4[1]"), get(f"{prefix}TextField4[2]")) if x).upper()  # noqa: E731
    one = {g: on(f"BodyPage1[0].S8[0].CheckBox3[{i}]") for i, g in enumerate(("abuse", "neglect", "abandonment", "similar"))}
    two = {g: on(f"Subform1[0].S8[0].CheckBox3[{i}]") for i, g in enumerate(("abuse", "neglect", "abandonment", "similar"))}
    if any(one.values()) or any(two.values()):
        both = any(one.values()) and (any(two.values()) or on("Subform1[0].S8[0].CheckBox2[0]"))
        add("sij.reunification_parents", "both" if both else "one")
        if not both:  # Supporting implementation.
            first, mi, last = get("S3[0].TextField4[0]"), get("S3[0].TextField4[1]"), get("S3[0].TextField4[2]")
            parent = " ".join(x for x in (first, mi, last) if x).upper()
            if parent:
                add("sij.parent_name", parent)
        for g in ("abuse", "neglect", "abandonment", "similar"):
            if one[g] or two[g]:
                add(f"sij.ground_{g}", "Yes")
        basis = " / ".join(x.strip() for x in (get("BodyPage1[0].S8[0].LG1[0]"), get("Subform1[0].S8[0].LG1[0]")) if x and x.strip())
        if basis:
            add("sij.similar_basis", basis, 0.9)
    country = get("S10[0].TextField4[0]")
    if country:
        from .places import country_name

        add("sij.best_interest_country", country_name(country) or country.strip().upper())
    carer = name("S10[0].S13[0].") or name("S12[0].S13[0].")
    if carer:
        add("sij.placed_with", carer, 0.95)
    return out


def extract(text: str) -> list[ExtractedField]:
    out: list[ExtractedField] = []
    text, _, block = text.partition(FORM_MARK)
    flat = re.sub(r"[ \t]+", " ", text)
    cjp37 = bool(_CJP37.search(flat))
    if cjp37:
        # Supporting implementation.
        out.append(ExtractedField("sij.order_form", "CJP 37", "CJP 37", 0.95))
        out.append(ExtractedField("sij.court_state", "Massachusetts Trial Court", "MA", 0.95))
        out.append(ExtractedField("sij.declared_dependent", "CJP 37 item 5: Child is dependent on this Court", "Yes", 0.9))
        out.append(ExtractedField("sij.best_interest_determined", "CJP 37 item 8: not in Child's best interest to return", "Yes", 0.9))
        country = re.search(r"last\s+habitual\s+residence\s+of\s*(?:\(Country\))?\s*([A-Z][A-Za-z ]{2,30})", flat)
        from .places import country_name

        if country and country_name(country.group(1).strip()):
            out.append(ExtractedField("sij.best_interest_country", country.group(0), country_name(country.group(1).strip()), 0.8))
        if block:
            out += _from_cjp37_fields(block)

    given = {f.fact_key for f in out}  # Supporting implementation.
    court = _COURT.search(flat) if "sij.court_name" not in given else None
    if cjp37 and court:
        out.append(ExtractedField("sij.court_name", court.group(0).strip(), "PROBATE AND FAMILY COURT", 0.85))
    elif court:
        name = re.sub(r"\s+", " ", court.group(1)).strip(" ,.").upper()
        out.append(ExtractedField("sij.court_name", court.group(0).strip(), name, 0.8))
    state = None if cjp37 else next((code for st, code in STATES.items() if re.search(rf"(?:COMMONWEALTH|STATE)\s+OF\s+{st}\b", flat, re.I)), None)
    if state:
        out.append(ExtractedField("sij.court_state", state, state, 0.9))
    docket = _DOCKET.search(flat) if "sij.docket_number" not in given else None
    if docket and re.search(r"\d", docket.group(1)):  # Supporting implementation.
        out.append(ExtractedField("sij.docket_number", docket.group(0), docket.group(1).strip().upper(), 0.85))
    dates = [m for m in _DATE.finditer(flat)] if "sij.order_date" not in given else []
    if dates:  # Supporting implementation.
        raw = dates[-1].group(1)
        iso = normalize_date(re.sub(r"(\d)(?:st|nd|rd|th) day of ([A-Za-z]+),?", r"\2 \1,", raw))
        if iso:
            out.append(ExtractedField("sij.order_date", dates[-1].group(0), iso, 0.75))

    have = {f.fact_key for f in out}
    dependent = _DEPENDENT.search(flat) if "sij.declared_dependent" not in have else None
    if dependent:
        out.append(ExtractedField("sij.declared_dependent", _sentence(flat, dependent), "Yes", 0.85))
    placed = _PLACED.search(flat) if "sij.placed_with" not in have else None
    if placed and not re.fullmatch(r"(?:FIRST NAME|LAST NAME|MI)\b.*", placed.group(1).strip(), re.I):
        out.append(ExtractedField("sij.placed_with", _sentence(flat, placed), re.sub(r"\s+", " ", placed.group(1)).strip().upper(), 0.7))

    reunify = _REUNIFY.search(flat)
    if reunify and (cjp37 or _OPTIONS.search(_sentence(flat, reunify))):
        reunify = None  # Supporting implementation.
    if reunify:
        said = _sentence(flat, reunify)
        if re.search(r"\bboth\b|\bparents\b|\beither\s+parent", said, re.I):
            out.append(ExtractedField("sij.reunification_parents", said, "both", 0.8))
        else:
            one = re.search(r"\b(father|mother)\b(?:,?\s+([A-Z][A-Za-z' -]{3,40}?))?(?:,|\s+is|\s+due|\s+has|\.)", said)
            if one:
                out.append(ExtractedField("sij.reunification_parents", said, "one", 0.8))
                if one.group(2):
                    out.append(ExtractedField("sij.parent_name", said, one.group(2).strip().upper(), 0.7))
        for ground in ("abuse", "neglect", "abandonment"):
            if re.search(rf"\b{ground}(?:ed)?\b", said, re.I):
                out.append(ExtractedField(f"sij.ground_{ground}", said, "Yes", 0.85))
        similar = re.search(r"similar\s+basis\s+under\s+(?:state|[A-Z][a-z]+)\s+law\s*(?:\(([^)]+)\)|[:,]\s*([^.;]+))?", said, re.I)
        if similar:
            out.append(ExtractedField("sij.ground_similar", said, "Yes", 0.8))
            if similar.group(1) or similar.group(2):
                out.append(ExtractedField("sij.similar_basis", said, (similar.group(1) or similar.group(2)).strip(), 0.6))

    best = _BEST.search(flat) if "sij.best_interest_determined" not in have else None
    if best:
        out.append(ExtractedField("sij.best_interest_determined", _sentence(flat, best), "Yes", 0.85))
        from .places import country_name

        country = country_name(best.group(1))
        if country:
            out.append(ExtractedField("sij.best_interest_country", _sentence(flat, best), country, 0.8))
    return out
