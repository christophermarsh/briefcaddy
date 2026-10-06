"""Where an answer sits on a form, in the form's own words: the page, the part
and the item a Part 14 entry has to name ("Page Number", "Part Number",
"Item Number" on the form's Additional Information page).

Nothing here is typed in: the page is the page of the template that holds the
field the product's field map fills for that answer, and the part and item are
the ones the form itself prints for it, read from the field's own tooltip in
the template ("Part 9. ... 73. Have you EVER entered the United States
without being inspected ...") and checked against the text printed on that
page. The edition's field names do not carry the printed item: this
edition's "Pt9Line75" is printed item 73 (the 01/20/25 edition printed it as
75), which is why an item is never taken from a field name or a memory.

When the edition changes, the template and the map change with it and these
numbers follow. An answer whose tooltip and printed page disagree (one tooltip
in this template names an item the page does not print) is refused with a
plain message rather than guessed.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

from pypdf import PdfReader
import schema_path

SCHEMAS = schema_path.ROOT


_EDITION = re.compile(r"Edition\s*(?:Date)?\s*(\d{2}/\d{2}/\d{2})")
_PART = re.compile(r"^\s*(?:Part|PART)\s+(\d+|[A-Z])\b")
_HEADING = re.compile(r"^Part\s+(\d+|[A-Z])\.\s+(?=[A-Z])")  # "Part 9. General Eligibility ..." as the page prints it
_BOTH = re.compile(r"(\d+)\.\s?([A-Z])\.\s+and\s+\1\.\s?([A-Z])\.")  # "2. A. and 2. B."
# "73." or "42. C." or "76.a." : a number and a dot, then a space or a letter ("7.Provide")
_ITEM = re.compile(r"(?<![\w.])(\d{1,3})(?:\.(?:\s?([A-Za-z])\.)?(?=\s|[A-Za-z]|$)|\s([A-Z])\.)")
# "35 B." / "43. F Participated": a sub-item letter written without its dot
_LOOSE_LETTER = re.compile(r"\.?\s([A-Za-z])(?=\.|\s[A-Z])")
# a number that is not this field's own item: "Part 14.", "Item Numbers 2. - 9.", "Child 2.", "Organization 1."
_NOT_AN_ITEM = re.compile(r"(?:\b(?:Part|Parts|Item Number|Item Numbers|Numbers|Number|Organization|Child|Parent|Page|Form|Section|Step|through|thru|to)\s+|[-,]\s*)$", re.I)
_LABEL = re.compile(r"^(\d{1,3})\.(?:\s?([a-z])\.)?$")  # an item label printed on its own


class NotFound(LookupError):
    """The answer has no printed place the product can vouch for."""


class EditionMismatch(NotFound):
    pass


def _reader(template: Path) -> PdfReader:
    reader = PdfReader(str(template))
    if reader.is_encrypted:
        reader.decrypt("")
    return reader


def template_edition(template: Path) -> str | None:
    """The edition a template prints ("09/18/26"), from its first pages."""
    reader = _reader(template)
    for page in list(reader.pages)[:2] + list(reader.pages)[-1:]:
        found = _EDITION.search(page.extract_text() or "")
        if found:
            return found.group(1)
    return None


def _stamp(path: str | Path) -> int:
    """A file's change time: a template replaced in place at an edition change is read again, not served from a cache."""
    return Path(path).stat().st_mtime_ns


def _index(template: str) -> dict:
    return _read_index(str(template), _stamp(template))


@functools.lru_cache(maxsize=32)
def _read_index(template: str, _changed: int) -> dict:
    """{"fields": {full name: (page, tooltip, top of the box, rect)}, "text": {page: text}, "headings": {page: [(y, part)]},
    "labels": {page: [(x, y, label)]}, "edition"}: one read of a template."""
    reader = _reader(Path(template))
    fields: dict[str, tuple[int, str, float, tuple[float, float, float, float]]] = {}
    headings: dict[int, list[tuple[float, str]]] = {}
    labels: dict[int, list[tuple[float, float, str]]] = {}
    for number, page in enumerate(reader.pages, start=1):
        for annot in page.get("/Annots") or []:
            annot = annot.get_object()
            names, node, tip = [], annot, annot.get("/TU")
            while node is not None:
                if "/T" in node:
                    names.append(str(node["/T"]))
                if tip is None:
                    tip = node.get("/TU")
                parent = node.get("/Parent")
                node = parent.get_object() if parent is not None else None
            if names and "/Rect" in annot:
                x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
                rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                fields.setdefault(".".join(reversed(names)), (number, str(tip or ""), rect[3], rect))
        found: list[tuple[float, str]] = []
        seen: list[tuple[float, float, str]] = []

        def visit(text, cm, tm, _font, _size, found=found, seen=seen):
            frag = text.replace("\xa0", " ").strip()
            if _LABEL.match(frag):  # "73." or "42.c." printed on its own: the label of the item whose boxes sit beside and below it
                seen.append((tm[4] * cm[0] + tm[5] * cm[2] + cm[4], tm[4] * cm[1] + tm[5] * cm[3] + cm[5], frag))
            heading = _HEADING.match(frag)
            if heading:
                y = tm[5] * cm[3] + cm[5]
                rest = frag[heading.end():]
                # "Part 14. Additional Information" in a sentence is not a heading ("Part 4. Additional Information About You" is)
                if y > 690 or not rest.startswith("Additional") or rest.startswith("Additional Information About"):
                    found.append((y, heading.group(1)))

        page.extract_text(visitor_text=visit)
        headings[number] = sorted(found, reverse=True)
        labels[number] = seen
    return {"fields": fields, "text": {n: p.extract_text() or "" for n, p in enumerate(reader.pages, start=1)}, "headings": headings,
            "labels": labels, "edition": template_edition(Path(template))}


def label_near(index: dict, page: int, rect: tuple[float, float, float, float]) -> tuple[str, str | None] | None:
    """(number, letter) of the item label the page prints nearest above or to the left of a box: the one a person reading the page
    would say the box belongs to. None when the page prints none above the box (the item then started on the page before)."""
    x0, _y0, x1, y1 = rect[0], rect[1], rect[2], rect[3]
    best = None
    for x, y, text in index["labels"].get(page, []):
        if y >= rect[1] - 2 and x <= x1:
            distance = abs(y - y1) + abs(x - x0) * 0.3
            if best is None or distance < best[0]:
                best = (distance, text)
    if best is None:
        return None
    m = _LABEL.match(best[1])
    return m.group(1), m.group(2)


def part_at(index: dict, page: int, top: float) -> str | None:
    """The Part whose heading the page prints nearest above this height (carried over from the page before when the
    page opens with no heading of its own)."""
    for n in range(page, 0, -1):
        above = [part for y, part in index["headings"].get(n, []) if n != page or y >= top - 2]
        if above:
            return above[-1] if n == page else index["headings"][n][-1][1]
    return None


def _first_field(spec: dict) -> str | None:
    """The first template field a map entry fills."""
    if spec.get("fields"):
        return spec["fields"][0]
    for side in ("yes", "no"):
        if spec.get(side):
            return spec[side][0]
    if spec.get("feet_field"):
        return spec["feet_field"]
    for option in (spec.get("options") or {}).values():
        first = option[0]
        return first[0] if isinstance(first, list) else first
    return None


def _lookup(index: dict, name: str) -> tuple[int, str, float] | None:
    fields = index["fields"]
    if name in fields:
        return fields[name]
    hits = [v for k, v in fields.items() if k == name or k.endswith("." + name)]
    return hits[0] if hits else None


def _item_candidates(tip: str) -> tuple[str, str, list[str | None]] | None:
    """(part, number, letters to try): the first item number in a tooltip that is not a reference to another
    part or item. A tooltip writes a sub-item three ways ("42. C.", "35 B.", "43. F Participated"), and a plain
    "A" can also be a word, so every letter found is tried against what the page prints."""
    text = tip.replace("\r", " ")
    part = _PART.match(text)
    if not part:
        return None
    for found in _ITEM.finditer(text, part.end()):
        if _NOT_AN_ITEM.search(text[:found.start()]):
            continue
        tried: list[str | None] = []
        if found.group(2) or found.group(3):
            tried.append((found.group(2) or found.group(3)).lower())
        loose = _LOOSE_LETTER.match(text, found.end(1))
        if loose and loose.group(1).lower() not in tried:
            tried.append(loose.group(1).lower())
        return part.group(1), found.group(1), [*tried, None]
    return None


def _printed_on(page_text: str, number: str, letter: str | None) -> bool:
    """The page prints this item's label: "73." (at the start of a line or run into the text before it) or "42.c."."""
    if letter:
        return bool(re.search(rf"(?<![\d.]){number}\.{letter}\.", page_text))
    return bool(re.search(rf"(?<![\d.]){number}\.(?![a-z]\.)(?:\s|[A-Za-z]|$)", page_text))


def where_is_in(template: Path, spec: dict | None, key: str = "", style: str = "i485") -> tuple[str, str, str]:
    """(page, part, item) of the box a map entry fills. style: "i485" (items printed "73." and "42.c.", numbered parts) or
    "i589" (Part letters, questions printed "1.A")."""
    index = _index(str(template))
    name = _first_field(spec) if spec else None
    if not name:
        raise NotFound(f"The form has no box filled from {key or 'that answer'}.")
    found = _lookup(index, name)
    if not found:
        raise NotFound(f"The form's template has no box for {key or name}.")
    page, tip, top, rect = found
    candidates = _item_candidates(tip)
    if not candidates:
        raise NotFound(f"The form does not print an item number for {key or name}.")
    part, number, letters = candidates
    printed = part_at(index, page, top)
    if printed is not None and printed != part:  # the tooltip says Part 3 where the page prints Part 4 there (the N-400's do)
        raise NotFound(f"The form's tooltip for {key or name} names Part {part} but page {page} prints Part {printed} there: check it by hand.")
    # what a person reading the page would call the box's item: the label printed nearest above or beside it. A tooltip that names another
    # number (a parent's box says "Parent 2. 7. Enter Date of Birth") is refused, however likely that number is to be printed somewhere.
    near = label_near(index, page, rect) if style == "i485" else None
    if near is not None and near[0] != number:
        raise NotFound(f"The form's tooltip for {key or name} names item {number} but the page prints item {near[0]} beside the box: check it by hand.")
    # an item can start on the page before the one holding its boxes (Part 1, 18's prior address sits on the page after the question)
    for letter in letters:
        if near is not None and near[1] and letter and near[1] != letter:
            continue
        if any(_printed_on(index["text"].get(p, ""), number, letter if style == "i485" else None) for p in (page, page - 1)):
            if style == "i589":  # the asylum form numbers a question "1.A", and one box can answer "2.A and 2.B"
                both = _BOTH.search(tip)
                if letter and both and both.group(1) == number and both.group(2).lower() == letter:
                    return str(page), part, f"{number}.{letter.upper()}-{number}.{both.group(3).upper()}"
                return str(page), part, number + (f".{letter.upper()}" if letter else "")
            return str(page), part, number + (f".{letter}." if letter else "")
    raise NotFound(f"The form's tooltip for {key or name} names item {number} but page {page} does not print it: check it by hand.")


def _i485_map(schemas: str) -> dict:
    path = schema_path.path("field_map", "i485", Path(schemas))
    return _read_map(str(path), _stamp(path))


@functools.lru_cache(maxsize=8)
def _read_map(path: str, _changed: int) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))["fact_to_acroform"]


def where_is(edition: str, field_key: str, form: str = "i485", schemas: Path = SCHEMAS) -> tuple[str, str, str]:
    """(page, part, item) of the answer the map fills from field_key, in this edition of the form.

    edition is the one the caller is filing ("09/18/26"); it must be the edition of the template and map
    the product holds, else EditionMismatch (a new edition is a new template and map, and a packet is
    held until they are in: src/editions.py)."""
    if form != "i485":
        # The other forms' tooltips do not vouch for their items (the N-400's name Part 3 where the page prints Part 4, and give child 1's
        # box item 1 where the page prints 2), so nothing is read from them until a form is checked the way the I-485 is.
        raise NotFound(f"The page, part and item are read from the I-485's own template only, not the {form.upper()}'s.")
    schemas = Path(schemas)
    template, spec = schema_path.path("template", "i485", schemas), _i485_map(str(schemas)).get(field_key)
    have = _index(str(template))["edition"]
    if edition != have:
        raise EditionMismatch(f"The product holds the {have} edition of this form, not the {edition} edition.")
    return where_is_in(template, spec, field_key)


def edition_of(form: str = "i485", schemas: Path = SCHEMAS) -> str | None:
    """The edition of the template the product fills for this form."""
    schemas = Path(schemas)
    if form == "i485":
        return _index(str(schema_path.path("template", "i485", schemas)))["edition"]
    from .companion import load_profile

    return _index(str(schema_path.named(load_profile(schema_path.path("packet", "companion_forms", schemas))["forms"][form]["template"], schemas)))["edition"]
