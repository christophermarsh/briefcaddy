"""Part 14 on the form's own page: what doesn't fit the form's own Additional
Information boxes goes on copies of the form's own Additional Information
page, never on a sheet of the product's design.

The form's own instructions (every USCIS template carries them on that page):
"If you need extra space ... you may make copies of this page to complete and
file with this application or attach a separate sheet of paper. Type or print
your name and A-Number (if any) at the top of each sheet; indicate the Page
Number, Part Number, and Item Number to which your answer refers; and sign
and date each sheet." So a copy here is that page: the template's own page,
its labels, lines and footer as the form prints them ("Page 24 of 24" stays),
with its boxes filled: family, given and middle name, the A-Number, and each
entry's Page, Part and Item boxes and its text.

How a page is found (find_page): by what the template says about its own
boxes (their tooltips "Enter Page Number", "Enter Part Number", "Enter Item
Number", "Enter Additional Information"), not by a page number typed here, so
a new edition with the page moved or a box added is read as it is. Each entry
is the Page, Part and Item boxes that sit together and the text box under
them. The form's own boxes take the first entries in order; the rest go on
copies of the page, in order (place_blocks). Each copy's boxes are new fields
with names of their own (the template's name and a suffix), so a PDF reader
shows every copy's values and not the first page's repeated.

An entry is wrapped to its box (the box's width in the field's own font and
size, its height in lines) and never cut: what doesn't fit one box continues
in the next, headed "(continued)", with the same Page, Part and Item.

A form whose template has no such page gets a plain sheet only where the form
itself says to attach one (schemas/law/part14_pages.json holds the form's own line
and its page; a test reads it back from the template), and nothing otherwise.

Everything is drawn with pypdf and plain PDF operators: no new dependency.
"""

from __future__ import annotations

import functools
import io
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, StreamObject, TextStringObject
import schema_path


REGISTRY = schema_path.path("law", "part14_pages")
COPY_MARK = "__copy"  # a copy's field names: the template's name, this, the copy's number

WIDTH, HEIGHT = 612, 792
MARGIN = 54
LINE = 12.5  # Courier 10 pt (the plain sheet)


@dataclass
class Block:
    page: str
    part: str
    item: str
    text: str  # lines separated by "\n"
    source: str = ""  # the line it was composed from, for the review bundle ("questionnaire line prior_address2")


def _pdf_string(text: str) -> str:
    raw = text.encode("cp1252", errors="replace").decode("latin-1")
    return "(" + raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def plain(text: str) -> str:
    """Without accents, as every other box is filled ("SAO PAULO"): src/fill/field_map.py."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


# -- reading the template's own page ----------------------------------------------------------

@dataclass(frozen=True)
class Box:
    name: str  # the template's full field name
    short: str  # its last name part
    rect: tuple[float, float, float, float]
    tip: str
    max_len: int | None
    flags: int
    font: str  # "CourierNewPS-BoldMT"
    size: float

    @property
    def width(self) -> float:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> float:
        return self.rect[3] - self.rect[1]


@dataclass(frozen=True)
class Entry:
    page: Box | None
    part: Box | None
    item: Box | None
    text: Box
    label: str  # the form's own number for the entry ("2"), from its tooltip


@dataclass(frozen=True)
class AdditionalPage:
    index: int  # 0-based page of the template
    entries: tuple[Entry, ...]
    family: tuple[Box, ...]
    given: tuple[Box, ...]
    middle: tuple[Box, ...]
    full_name: tuple[Box, ...]
    a_number: tuple[Box, ...]


def _inherited(node: Any, key: str) -> Any:
    while node is not None:
        if key in node:
            return node[key]
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
    return None


def _reader(template: Path | str) -> PdfReader:
    reader = PdfReader(str(template))
    if reader.is_encrypted:
        reader.decrypt("")
    return reader


def _box(annot: Any) -> Box | None:
    names, node = [], annot
    while node is not None:
        if "/T" in node:
            names.append(str(node["/T"]))
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
    if not names or _inherited(annot, "/FT") != "/Tx":
        return None
    default = str(_inherited(annot, "/DA") or "")
    font = re.search(r"/(\S+)\s+([\d.]+)\s+Tf", default)
    rect = tuple(float(x) for x in annot["/Rect"])
    rect = (min(rect[0], rect[2]), min(rect[1], rect[3]), max(rect[0], rect[2]), max(rect[1], rect[3]))
    max_len = _inherited(annot, "/MaxLen")
    return Box(".".join(reversed(names)), names[0], rect, str(_inherited(annot, "/TU") or ""), int(max_len) if max_len else None,
               int(_inherited(annot, "/Ff") or 0), font.group(1) if font else "Helv", (float(font.group(2)) if font else 0.0) or 10.0)


_NAME_PARTS = (("family", re.compile(r"Family|Line\d[a]_Name")), ("given", re.compile(r"Given|Line\d[b]_Name")),
               ("middle", re.compile(r"Midd?dle|Mild?dle|Line\d[c]_Name")), ("full_name", re.compile(r"ApplicantName|FullName")),
               ("a_number", re.compile(r"Alien|ANum|A_?Number")))
MULTILINE = 4096


def _kind(box: Box) -> str | None:
    tip = " ".join(box.tip.split()).lower()
    if re.search(r"enter (the )?page number", tip):
        return "page"
    if re.search(r"enter (the )?part number", tip):
        return "part"
    if re.search(r"enter (the )?(item|question) number", tip):
        return "item"
    if re.search(r"enter (the )?additional information", tip) and box.flags & MULTILINE:
        return "text"
    return None


def _stamp(path: Path | str) -> int:
    """A file's change time: a template replaced in place at an edition change is read again, not served from a cache."""
    return Path(path).stat().st_mtime_ns


def _printed_labels(page: Any) -> list[tuple[float, float, str]]:
    """The entry numbers the page prints on their own ("2.", "3.") with their places: what a person calls each entry."""
    seen: list[tuple[float, float, str]] = []

    def visit(text, cm, tm, _font, _size):
        frag = text.replace("\xa0", " ").strip()
        if re.fullmatch(r"\d{1,2}\.", frag):
            seen.append((tm[4] * cm[0] + tm[5] * cm[2] + cm[4], tm[4] * cm[1] + tm[5] * cm[3] + cm[5], frag[:-1]))

    page.extract_text(visitor_text=visit)
    return seen


def _label_of(labels: list[tuple[float, float, str]], box: Box) -> str:
    """The number the page prints beside an entry's first box (left of it, level with its caption just above): "" when it prints none."""
    near = [(abs(y - box.rect[3] - 8) + abs(x - box.rect[0]) * 0.3, text) for x, y, text in labels
            if x <= box.rect[0] + 2 and box.rect[3] - 4 <= y <= box.rect[3] + 40]
    return min(near)[1] if near else ""


def _find_page(template: str) -> AdditionalPage | None:
    return _read_page(template, _stamp(template))


@functools.lru_cache(maxsize=64)
def _read_page(template: str, _changed: int) -> AdditionalPage | None:
    reader = _reader(template)
    for index in range(len(reader.pages) - 1, -1, -1):
        boxes = [b for b in (_box(a.get_object()) for a in reader.pages[index].get("/Annots") or []) if b]
        kinds = {b.name: _kind(b) for b in boxes}
        parts = [b for b in boxes if kinds[b.name] == "part"]
        texts = [b for b in boxes if kinds[b.name] == "text"]
        if not (parts and texts):
            continue
        items = [b for b in boxes if kinds[b.name] == "item"]
        pages = [b for b in boxes if kinds[b.name] == "page"]

        def nearest(box: Box, among: list[Box], limit: float = 1e9) -> Box | None:
            best = min(among, key=lambda o: abs(o.rect[0] - box.rect[0]) + abs(o.rect[1] - box.rect[1]), default=None)
            return best if best and abs(best.rect[0] - box.rect[0]) + abs(best.rect[1] - box.rect[1]) <= limit else None

        entries = []
        labels = _printed_labels(reader.pages[index])
        for part in parts:
            item, page = nearest(part, items), nearest(part, pages, 200)
            left = min((b for b in (page, part, item) if b), key=lambda b: b.rect[0])
            under = [t for t in texts if t.rect[0] - 3 <= left.rect[0] <= t.rect[2] and t.rect[3] <= left.rect[1] + 3]
            if not under:
                continue
            text = max(under, key=lambda t: t.rect[3])
            entries.append((left, Entry(page, part, item, text, _label_of(labels, left))))
        entries.sort(key=lambda e: (0 if e[0].rect[0] < WIDTH / 2 else 1, -e[0].rect[1]))
        used = {b.name for _, e in entries for b in (e.page, e.part, e.item, e.text) if b}
        names: dict[str, list[Box]] = {key: [] for key, _ in _NAME_PARTS}
        for b in boxes:
            if b.name in used or kinds[b.name] or "PDF417" in b.name:
                continue
            for key, pattern in _NAME_PARTS:
                if pattern.search(b.short):
                    names[key].append(b)
                    break
        return AdditionalPage(index, tuple(e for _, e in entries), *(tuple(names[k]) for k, _ in _NAME_PARTS))
    return None


def find_page(template: Path | str) -> AdditionalPage | None:
    """The template's Additional Information page, or None when the form has none."""
    return _find_page(str(template))


# -- fitting text to a box ---------------------------------------------------------------------

def _font_dict(root: Any, name: str) -> Any:
    form = root.get("/AcroForm")
    font = ((form or {}).get("/DR") or {}).get("/Font", {}).get(f"/{name}") if form else None
    return font.get_object() if font is not None else None


def _spacing(font: Any, size: float) -> tuple[float, float]:
    """(line height, the top of the first line below the box's top) at this size, from the font's own bounding box:
    the way a reader regenerates a text box's look (a box that the reader redraws must hold what we counted)."""
    box = (font.get("/FontDescriptor") or {}).get("/FontBBox") if font is not None else None
    if not box:
        return size * 1.2, size * 0.9
    box = [float(x) for x in box]
    return (box[3] - box[1]) * size / 1000, box[3] * size / 1000


def _font_info(template: str, name: str) -> tuple[int, tuple[float, ...] | None, tuple[float, float, float]]:
    return _read_font_info(template, name, _stamp(template))


@functools.lru_cache(maxsize=64)
def _read_font_info(template: str, name: str, _changed: int) -> tuple[int, tuple[float, ...] | None, tuple[float, float, float]]:
    """(first char, widths, (bbox height, bbox top, descender) in 1/1000 em) of a form font."""
    font = _font_dict(_reader(template).trailer["/Root"], name)
    widths = (int(font.get("/FirstChar", 0)), tuple(float(w) for w in font["/Widths"])) if font is not None and "/Widths" in font else (0, None)
    leading, top = _spacing(font, 1000)
    return widths[0], widths[1], (leading, top, 250.0)


def char_width(template: Path | str, box: Box, char: str) -> float:
    """One character's width in the box's own font and size (a Courier face: 600 units to the em)."""
    first, widths, _ = _font_info(str(template), box.font)
    code = ord(plain(char).encode("cp1252", errors="replace").decode("latin-1") or "?")
    if widths and first <= code < first + len(widths):
        return widths[code - first] * box.size / 1000
    return 0.6 * box.size


def text_width(template: Path | str, box: Box, text: str) -> float:
    return sum(char_width(template, box, c) for c in text)


def box_lines(template: Path | str, box: Box) -> int:
    """How many lines the box holds in its own font and size, counted the way a reader draws them: the first line's
    baseline a font's height below the top, each next one a line height lower, the last one clear of the bottom."""
    _, _, (leading, top, descender) = _font_info(str(template), box.font)
    return max(1, int((box.height - (top + descender) * box.size / 1000) // (leading * box.size / 1000)) + 1)


def wrap(template: Path | str, box: Box, text: str) -> list[str]:
    """The text as lines no wider than the box (its width less the field's own margin), in the box's font and size;
    a line that is already short stays, a word longer than a line is broken, nothing is dropped."""
    room = box.width - 4
    out: list[str] = []
    for paragraph in plain(text).rstrip().split("\n"):
        words, line = paragraph.rstrip().split(" "), ""
        for word in words:
            candidate = f"{line} {word}" if line else word
            if text_width(template, box, candidate) <= room:
                line = candidate
                continue
            if line:
                out.append(line)
                line = ""
            while text_width(template, box, word) > room:  # a word wider than the box: break it where it fits
                cut = len(word)
                while cut > 1 and text_width(template, box, word[:cut]) > room:
                    cut -= 1
                out.append(word[:cut])
                word = word[cut:]
            line = word
        out.append(line)
    return out


@dataclass
class Placement:
    block: int  # which entry (the index in the blocks given)
    chunk: int  # 0 = the entry's first box; 1... = the boxes it continues in
    page: str
    part: str
    item: str
    lines: list[str]
    copy: int  # 0 = the form's own page; 1... = the copies after the form
    slot: int  # which entry on the page (0-based)
    label: str  # the form's own number for that box ("2")


@dataclass
class Layout:
    template: str
    placements: list[Placement] = field(default_factory=list)
    slots: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def copies(self) -> int:
        return max((p.copy for p in self.placements), default=0)

    def landed(self, block: int) -> list[Placement]:
        return [p for p in self.placements if p.block == block]

    def where(self, block: int) -> str:
        """Plain words: where an entry landed ("the form's own Part 14 page, box 2" / "copy 1 of the Part 14 page, box 3; continues in box 4")."""
        spots = [("the form's own Part 14 page" if p.copy == 0 else f"copy {p.copy} of the Part 14 page") + f", box {p.label or p.slot + 1}"
                 for p in self.landed(block)]
        if not spots:
            return "not placed"
        return spots[0] + ("; continues in " + ", ".join(spots[1:]) if len(spots) > 1 else "")


def place_blocks(template: Path | str, blocks: Iterable[Block]) -> Layout:
    """Lays the entries out in the template's own boxes, in order: the form's own page first, then copies of it.
    Each entry's lines are wrapped to the box; one that is longer than a box continues in the next, headed
    "(continued)". The Page, Part and Item boxes have a length of their own (their MaxLen): a value that would not
    fit one is left out of its box and listed in problems, never cut."""
    page = find_page(template)
    layout = Layout(str(template))
    if page is None or not page.entries:
        raise ValueError(f"{Path(template).name} has no Additional Information page to copy")
    layout.slots = len(page.entries)
    k = 0
    for number, block in enumerate(blocks):
        values = {}
        for key in ("page", "part", "item"):
            value = str(getattr(block, key) or "")
            box = getattr(page.entries[0], key)
            if box is not None and box.max_len and len(value) > box.max_len:
                layout.problems.append(f"The {key} number {value!r} is longer than the form's box allows ({box.max_len}): left blank.")
                value = ""
            values[key] = value
        slot = page.entries[k % layout.slots]
        lines = wrap(template, slot.text, block.text)
        chunk = 0
        while True:
            slot = page.entries[k % layout.slots]
            room = box_lines(template, slot.text)
            head = [] if chunk == 0 else ["(continued)"]
            if room - len(head) < 1:
                head = []
            take = lines[:room - len(head)]
            layout.placements.append(Placement(number, chunk, values["page"], values["part"], values["item"], head + take,
                                               k // layout.slots, k % layout.slots, slot.label))
            lines = lines[len(take):]
            k += 1
            chunk += 1
            if not lines:
                break
    return layout


# -- writing the boxes ------------------------------------------------------------------------

def _a_digits(a_number: str) -> str:
    return re.sub(r"\D", "", a_number or "")


def _name_values(page: AdditionalPage, family: str, given: str, middle: str, a_number: str) -> dict[str, str]:
    """{a box's short name: its value} for the name and A-Number boxes on the page (accents out, as everywhere)."""
    out: dict[str, str] = {}
    for boxes, value in ((page.family, family), (page.given, given), (page.middle, middle),
                         (page.full_name, " ".join(x for x in (given, middle, family) if x)), (page.a_number, _a_digits(a_number))):
        for box in boxes:
            if value:
                out[box.name] = plain(value)
    return out


def own_page_values(template: Path | str, layout: Layout) -> dict[str, str]:
    """{full field name: text} for the form's own Additional Information boxes: the entries that landed on the form's
    page, and a blank for every other box on it (so a value the field map put there cannot linger)."""
    page = find_page(template)
    out: dict[str, str] = {}
    for entry in page.entries:
        for box in (entry.page, entry.part, entry.item, entry.text):
            if box is not None:
                out[box.name] = ""
    for p in layout.placements:
        if p.copy == 0:
            entry = page.entries[p.slot]
            for box, value in ((entry.page, p.page), (entry.part, p.part), (entry.item, p.item), (entry.text, "\n".join(p.lines))):
                if box is not None:
                    out[box.name] = value
    return out


def _appearance(writer: PdfWriter, box: Box, lines: list[str]) -> Any:
    """A text box's appearance, drawn at the box's own font and size with the line spacing capacity was counted at
    (the library's own appearance spaces lines by the font's whole bounding box, far wider, and clips the rest)."""
    font = writer._root_object["/AcroForm"]["/DR"]["/Font"][f"/{box.font}"]
    leading, top = _spacing(font.get_object(), box.size)
    width, height = box.width, box.height
    ops = [f"/Tx BMC q 2 0 {width - 4:.2f} {height:.2f} re W n BT /{box.font} {box.size} Tf 0 g {leading:.2f} TL",
           f"2 {height - top:.2f} Td"]
    ops += [f"{_pdf_string(line)} Tj T*" for line in lines]
    ops.append("ET Q EMC")
    stream = StreamObject()
    stream.set_data("\n".join(ops).encode("latin-1"))
    stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                   NameObject("/BBox"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(round(width, 2)), NumberObject(round(height, 2))]),
                   NameObject("/Resources"): DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject(f"/{box.font}"): font})})})
    return writer._add_object(stream)


def _widget(page: PageObject, name: str) -> Any:
    for annot in page.get("/Annots") or []:
        annot = annot.get_object()
        if annot.get("/T") == name:
            return annot
        names, node = [], annot
        while node is not None:
            if "/T" in node:
                names.append(str(node["/T"]))
            parent = node.get("/Parent")
            node = parent.get_object() if parent is not None else None
        if ".".join(reversed(names)) == name:
            return annot
    return None


def _draw_text_box(writer: PdfWriter, page: PageObject, box: Box, lines: list[str], name: str | None = None) -> None:
    widget = _widget(page, name or box.name)
    if widget is None or not any(lines):
        return
    target = widget if "/T" in widget else widget["/Parent"].get_object()
    target[NameObject("/V")] = TextStringObject("\n".join(lines))
    widget[NameObject("/AP")] = DictionaryObject({NameObject("/N"): _appearance(writer, box, lines)})


def _copy_page(writer: PdfWriter, source: PageObject, widgets: list[Any], page: AdditionalPage, number: int,
               values: dict[str, tuple[str, list[str] | None]]) -> PageObject:
    """One copy of the template's page (source, its boxes taken off) appended to the writer, the boxes in widgets
    new fields named for the copy."""
    new = writer.add_page(source)
    form = writer._root_object["/AcroForm"]
    annots = ArrayObject()
    new[NameObject("/Annots")] = annots
    boxes = {b.name: b for e in page.entries for b in (e.page, e.part, e.item, e.text) if b}
    boxes |= {b.name: b for group in (page.family, page.given, page.middle, page.full_name, page.a_number) for b in group}
    for annot in widgets:
        box = _box(annot.get_object())
        if box is not None and "PDF417" in box.name:  # the form's own page-identifying barcode field, as a photocopy of the page would carry it
            code = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
                                     NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject(f"{box.short.replace('.', '_')}{COPY_MARK}{number}"),
                                     NameObject("/F"): NumberObject(4), NameObject("/Ff"): NumberObject(box.flags), NameObject("/P"): new.indirect_reference,
                                     NameObject("/Rect"): ArrayObject([NumberObject(round(x, 3)) for x in box.rect])})
            for key in ("/AP", "/MK", "/V", "/DA"):
                kept = _inherited(annot.get_object(), key)
                if kept is not None:
                    code[NameObject(key)] = kept.clone(writer) if hasattr(kept, "clone") else kept
            ref = writer._add_object(code)
            annots.append(ref)
            form["/Fields"].append(ref)
            continue
        if box is None or box.name not in boxes:
            continue
        name = f"{box.short.replace('.', '_')}{COPY_MARK}{number}"
        field_ = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
                                   NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject(name),
                                   NameObject("/F"): NumberObject(4), NameObject("/P"): new.indirect_reference,
                                   NameObject("/Rect"): ArrayObject([NumberObject(round(x, 3)) for x in box.rect]),
                                   NameObject("/DA"): TextStringObject(f"/{box.font} {box.size:.2f} Tf 0 g"),
                                   NameObject("/TU"): TextStringObject(box.tip.strip())})
        flags = box.flags & ~1  # a copy's name boxes are typed in like any other: not read-only
        if flags:
            field_[NameObject("/Ff")] = NumberObject(flags)
        if box.max_len:
            field_[NameObject("/MaxLen")] = NumberObject(box.max_len)
        for key in ("/MK", "/Q"):  # the box's look and its alignment (the Page, Part and Item boxes are centred on the form's own page)
            kept = _inherited(annot.get_object(), key)
            if kept is not None:
                field_[NameObject(key)] = kept.clone(writer) if hasattr(kept, "clone") else kept
        ref = writer._add_object(field_)
        annots.append(ref)
        form["/Fields"].append(ref)
        if box.name in values:
            text, lines = values[box.name]
            if lines is not None:
                _draw_text_box(writer, new, box, lines, name)
            elif text:
                writer.update_page_form_field_values(new, {name: text}, auto_regenerate=None)
    return new


def finish_part14(pdf_path: Path | str, blocks: list[Block], template: Path | str, family: str = "", given: str = "", middle: str = "",
                  a_number: str = "") -> Layout:
    """Puts every Part 14 entry on the filled form at pdf_path: the form's own Additional Information boxes first
    (rewritten, so what the field map put there is replaced by the wrapped entries), then copies of the template's
    own page after the form's last page, each with the name and A-Number filled. Returns the layout (what landed where)."""
    layout = place_blocks(template, blocks) if blocks else Layout(str(template))
    page = find_page(template)
    writer = PdfWriter(clone_from=str(pdf_path))
    own = writer.pages[page.index]
    values = own_page_values(template, layout) if blocks else {}
    if values:
        writer.update_page_form_field_values(own, values, auto_regenerate=None)
        for entry in page.entries:
            _draw_text_box(writer, own, entry.text, [x for x in values.get(entry.text.name, "").split("\n")])
    names = _name_values(page, family, given, middle, a_number)
    blank = {}  # the form's own page names the person too: filled here only where the form's fill left a name box empty
    for name, value in names.items():
        widget = _widget(own, name)
        target = None if widget is None else (widget if "/V" in widget or "/T" in widget else widget["/Parent"].get_object())
        if target is not None and not str(target.get("/V") or ""):
            blank[name] = value
    if blank:
        writer.update_page_form_field_values(own, blank, auto_regenerate=None)
    source =_reader(template).pages[page.index] if layout.copies else None
    widgets = list(source.get("/Annots") or []) if source is not None else []
    if source is not None:
        del source[NameObject("/Annots")]
    for p in range(1, layout.copies + 1):
        copy_values: dict[str, tuple[str, list[str] | None]] = {name: (value, None) for name, value in names.items()}
        for placed in layout.placements:
            if placed.copy != p:
                continue
            entry = page.entries[placed.slot]
            for box, value in ((entry.page, placed.page), (entry.part, placed.part), (entry.item, placed.item)):
                if box is not None:
                    copy_values[box.name] = (value, None)
            copy_values[entry.text.name] = ("\n".join(placed.lines), placed.lines)
        _copy_page(writer, source, widgets, page, p, copy_values)
    if layout.copies or values or blank:
        with open(pdf_path, "wb") as fh:
            writer.write(fh)
    return layout


# -- a plain sheet, only where the form itself says to attach one -------------------------------

def registry() -> dict[str, Any]:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def attach_sheet_allowed(form: str) -> dict[str, Any] | None:
    """The form's own line saying to attach a sheet ({"page": 7, "line": "..."}), or None when it says nothing of the kind."""
    entry = registry()["forms"].get(form) or {}
    return entry if entry.get("sheet") else None


class _Page:
    def __init__(self):
        self.ops: list[str] = []

    def text(self, x: float, y: float, s: str, font: str = "F1", size: float = 10) -> None:
        self.ops.append(f"BT /{font} {size} Tf {x:.1f} {y:.1f} Td {_pdf_string(s)} Tj ET")

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.8) -> None:
        self.ops.append(f"{width} w {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S")

    def box(self, x: float, y: float, w: float, h: float) -> None:
        self.ops.append(f"0.6 w {x:.1f} {y:.1f} {w:.1f} {h:.1f} re S")

    def to_page(self, writer: PdfWriter) -> PageObject:
        page = PageObject.create_blank_page(width=WIDTH, height=HEIGHT)
        fonts = DictionaryObject()
        for name, base in (("F1", "Courier"), ("F2", "Helvetica-Bold"), ("F3", "Helvetica")):
            fonts[NameObject(f"/{name}")] = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject(f"/{base}"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})
        stream = StreamObject()
        stream.set_data("\n".join(["0 g 0 G"] + self.ops).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/MediaBox")] = ArrayObject([NumberObject(0), NumberObject(0), NumberObject(WIDTH), NumberObject(HEIGHT)])
        return page


def _sheet_header(p: _Page, title: str, family: str, given: str, middle: str, a_number: str, sheet: int, sheets: int) -> float:
    p.text(MARGIN, HEIGHT - 50, title, "F2", 12)
    p.text(WIDTH - MARGIN - 90, HEIGHT - 50, f"Sheet {sheet} of {sheets}", "F3", 9)
    p.line(MARGIN, HEIGHT - 58, WIDTH - MARGIN, HEIGHT - 58)
    y = HEIGHT - 80
    for x, label, value in ((MARGIN, "Family Name (Last Name)", family), (MARGIN + 180, "Given Name (First Name)", given),
                            (MARGIN + 360, "Middle Name", middle)):
        p.text(x, y, label, "F3", 8)
        p.text(x, y - 14, plain(value or ""), "F1", 10)
    p.text(MARGIN, y - 36, "A-Number", "F3", 8)
    digits = _a_digits(a_number)
    p.text(MARGIN, y - 50, (f"A-{digits}" if digits else "NONE"), "F1", 10)
    p.line(MARGIN, y - 60, WIDTH - MARGIN, y - 60)
    return y - 84


def plain_sheet_pdf(form: str, title: str, blocks: list[Block], family: str = "", given: str = "", middle: str = "", a_number: str = "") -> bytes:
    """A plain sheet (bytes) for a form whose own text says to attach one; b"" when there is nothing to add. Refused for any
    other form: a form with no Additional Information page and no such line gets nothing."""
    if not blocks:
        return b""
    if attach_sheet_allowed(form) is None:
        raise ValueError(f"{form} does not say to attach a sheet, so none is made")
    top, bottom, per_line = HEIGHT - 164, 112, 80
    pages: list[list[tuple[Block, list[str]]]] = [[]]
    room = top - bottom
    for block in blocks:
        import textwrap

        lines = [ln for para in plain(block.text).split("\n") for ln in (textwrap.wrap(para, per_line) or [""])]
        need = 30 + LINE * max(1, len(lines)) + 34
        if need > room and pages[-1]:
            pages.append([])
            room = top - bottom
        pages[-1].append((block, lines))
        room -= need
    writer = PdfWriter()
    for sheet, page_blocks in enumerate(pages, start=1):
        p = _Page()
        y = _sheet_header(p, title, family, given, middle, a_number, sheet, len(pages))
        for block, lines in page_blocks:
            for x, label, value in ((MARGIN, "Page Number", block.page), (MARGIN + 90, "Part Number", block.part), (MARGIN + 180, "Item Number", block.item)):
                p.text(x, y, label, "F3", 8)
                p.box(x, y - 20, 80, 16)
                p.text(x + 4, y - 16, value or "", "F1", 10)
            height = LINE * max(1, len(lines)) + 8
            p.box(MARGIN, y - 28 - height, WIDTH - 2 * MARGIN, height)
            for i, line in enumerate(lines):
                p.text(MARGIN + 6, y - 28 - LINE * (i + 1) + 2, line, "F1", 10)
            y -= 30 + LINE * max(1, len(lines)) + 34
        p.line(MARGIN, 92, MARGIN + 260, 92)
        p.text(MARGIN, 80, "Signature of Applicant", "F3", 8)
        p.line(MARGIN + 320, 92, WIDTH - MARGIN, 92)
        p.text(MARGIN + 320, 80, "Date of Signature (mm/dd/yyyy)", "F3", 8)
        writer.add_page(p.to_page(writer))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
