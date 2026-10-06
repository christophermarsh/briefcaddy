"""Stage 5 -- field mapping (docs/ARCHITECTURE.md sections 5-6;
docs/GRAPH_MODEL.md's "what field mapping reads"). Reads
schemas/forms/i485/field_map.json: fact-graph key -> its AcroForm target(s), and
produces the flat {acroform_field: value} dict the fill stage
(fill_pdf.py) writes straight into the PDF.

Built against the real blank I-485 now checked in at
schemas/forms/i485/template.pdf (790 AcroForm fields across 24 pages). That form
confirmed something ARCHITECTURE.md's own illustrative field name didn't
show: most fields are plain text, but Yes/No answers and multi-option
biographic fields (eye color) are each a *separate* Btn field per option,
selected by setting that one field to its own on-value (e.g. "/BN" for
eye color "Brown") rather than writing a value into a single shared field.
Height is two Ch (dropdown) fields, Feet and Inches, not one field --
src/extract/drivers_license.py's combined "5'5\"" fact value gets split at
mapping time rather than changing what extractors produce.

A field-map entry is either a plain list of AcroForm field names (shorthand
for "type": "text" -- the fact's value is written into every field in the
list, e.g. a name repeated on more than one line), or a dict with an
explicit "type":

- "text": {"type": "text", "fields": [...]} -- same as the plain-list form.
- "yes_no": {"type": "yes_no", "yes": [field, on_value], "no": [field, on_value]}
  -- for a fact whose resolved value is literally "Yes" or "No".
- "choice_by_value": {"type": "choice_by_value", "options": {value: [field, on_value], ...}}
  -- selects the one field matching the fact's actual value.
- "height_feet_inches": {"type": "height_feet_inches", "feet_field": ..., "inches_field": ...}
  -- parses a "F'I\"" fact value into its two Ch fields.
- "date": {"type": "date", "fields": [...]} -- reformats an ISO
  (YYYY-MM-DD) fact value into the form's own MM/DD/YYYY, per this form's
  own field tooltips ("Enter the 2-digit Month, 2-digit Day, and 4-digit
  Year"). Every date fact in this pipeline is stored as ISO internally
  (src/extract/base.py's normalize_date) for consistent comparison/sorting
  -- confirmed by generating a real filled PDF and finding the DOB field
  literally read "2002-02-20" instead of "02/20/2002" before this existed.
- "digits_only": {"type": "digits_only", "fields": [...]} -- strips
  everything but digits from the fact value. Needed because this
  pipeline's own normalized values keep their readable formatting
  (applicant.ssn as "681-53-4454", applicant.a_number as "A201821016"),
  but the real form's SSN and A-Number fields are both /MaxLen 9,
  expecting exactly 9 raw digits -- confirmed by generating a real filled
  PDF and finding pypdf had silently truncated both (fill_pdf.py now also
  refuses to write a value that would overflow a field's /MaxLen, as a
  second line of defense against this same class of bug elsewhere).
- "digits_split": {"type": "digits_split", "fields": [...]} -- writes one
  digit per field, in order. The real form's Weight field is three
  separate single-digit boxes (Pt7Line4_Weight1/2/3), not one field.
  Requires an exact digit-count match to len(fields); a value with a
  different number of digits is left unmapped rather than guessing how
  to pad or truncate it.
- "money": {"type": "money", "fields": [...]} -- an amount in dollars and
  cents written the way a box on a form writes it ("1500.00" -> "1,500.00");
  a value that is not an amount is left unmapped (the EOIR-26A's lines).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Any

from factgraph import FactGraph

_HEIGHT_VALUE = re.compile(r"(\d+)'(\d+)\"")
NOT_A_DATE = ("NOT APPLICABLE", "N/A")  # what a date or number box holds when the paper it asks about is marked absent (src/absence.py)


@dataclass
class MappingResult:
    values: dict[str, Any] = field(default_factory=dict)
    # Resolved facts with no entry in the field map, or whose value didn't
    # match anything the map knows how to place (e.g. an eye color code
    # the real form has no box for) -- surfaced rather than silently
    # dropped. A resolved Tier 1/2 fact that never reaches the form is
    # exactly the kind of silent gap the validation stage
    # (docs/ARCHITECTURE.md section 7) exists to catch; this is the
    # earliest point that gap is knowable.
    unmapped_facts: list[str] = field(default_factory=list)


def load_field_map(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("fact_to_acroform", {})


def _resolve(fact_value: Any, mapping: Any) -> dict[str, Any]:
    if isinstance(mapping, list):
        mapping = {"type": "text", "fields": mapping}

    mapping_type = mapping.get("type", "text")

    if mapping_type == "text":
        # Filled without accents, as the firm does ("SÃO PAULO" -> "SAO PAULO").
        if isinstance(fact_value, str):
            import unicodedata

            fact_value = "".join(c for c in unicodedata.normalize("NFKD", fact_value) if not unicodedata.combining(c))
            # a dropdown's own option text: the N-400's state lists hold " MA" (a leading space), not "MA"
            fact_value = mapping.get("prefix", "") + fact_value
        return {acroform_field: fact_value for acroform_field in mapping["fields"]}

    if mapping_type == "yes_no":
        if fact_value not in ("Yes", "No"):
            return {}
        acroform_field, on_value = mapping["yes" if fact_value == "Yes" else "no"]
        return {acroform_field: on_value}

    if mapping_type == "choice_by_value":
        option = mapping["options"].get(fact_value)
        if option is None:
            return {}
        if option and isinstance(option[0], (list, tuple)):  # the same answer ticked in several places (I-360 Parts 1 and 3)
            return {field: on for field, on in option}
        acroform_field, on_value = option
        return {acroform_field: on_value}

    if mapping_type == "height_feet_inches":
        match = _HEIGHT_VALUE.fullmatch(str(fact_value))
        if not match:
            return {}
        return {mapping["feet_field"]: match.group(1), mapping["inches_field"]: match.group(2)}

    if mapping_type in ("date", "month_year", "digits_only") and str(fact_value).upper() in NOT_A_DATE:
        # a date or number box that does not apply to the client (no such paper: src/absence.py) reads what the form's instructions say
        return {acroform_field: str(fact_value).upper() for acroform_field in mapping["fields"]}

    if mapping_type == "date":
        try:
            formatted = _date.fromisoformat(str(fact_value)).strftime("%m/%d/%Y")
        except ValueError:
            return {}
        return {acroform_field: formatted for acroform_field in mapping["fields"]}

    if mapping_type == "month_year":  # the I-589's history tables ask "From (Mo/Yr)"
        try:
            formatted = _date.fromisoformat(str(fact_value)).strftime("%m/%Y")
        except ValueError:
            return {}
        return {acroform_field: formatted for acroform_field in mapping["fields"]}

    if mapping_type == "money":  # an amount as a box on the fee waiver request writes it: 1,234.56 (src/eoir26a.py)
        import eoir26a

        amount = eoir26a.parse_money(fact_value)
        return {acroform_field: eoir26a.fmt(amount) for acroform_field in mapping["fields"]} if amount is not None else {}

    if mapping_type == "digits_only":
        digits = re.sub(r"\D", "", str(fact_value))
        if not digits:
            return {}
        return {acroform_field: digits for acroform_field in mapping["fields"]}

    if mapping_type == "digits_split":
        digits = re.sub(r"\D", "", str(fact_value))
        fields = mapping["fields"]
        if len(digits) != len(fields):
            return {}
        return dict(zip(fields, digits))

    raise ValueError(f"unknown field-map type: {mapping_type!r}")


def map_facts_to_fields(graph: FactGraph, field_map: dict[str, Any], form_id: str | None = None) -> MappingResult:
    """Only resolved facts (tier 1 or 2) are written -- a fact still in
    conflict or missing has no business reaching a filled PDF. The one other thing written is what a box reads when the office recorded that
    the client has no such paper (src/absence.py marked_value: NOT APPLICABLE, N/A, or the word that form's instructions give, form_id naming the
    form): applied here, where the form is filled, so the case's facts stay empty and no rule or filing reads the placeholder as an answer."""
    result = MappingResult()
    for fact_key, fact in graph.all_facts().items():
        if fact.status != "resolved":
            continue
        if fact.value is None or fact.value == "":
            continue  # left blank on purpose (e.g. FactGraph.blank_by_review)
        mapping = field_map.get(fact_key)
        if not mapping:
            result.unmapped_facts.append(fact_key)
            continue
        resolved = _resolve(fact.value, mapping)
        if not resolved:
            result.unmapped_facts.append(fact_key)
            continue
        result.values.update(resolved)
    import absence

    for key in absence.box_keys():
        mapping = field_map.get(key)
        placeholder = absence.marked_value(graph, key, form_id) if mapping else None
        if placeholder is not None:
            for name, value in _resolve(placeholder, mapping).items():
                result.values.setdefault(name, value)
    return result
