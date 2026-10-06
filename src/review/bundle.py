"""The review bundle: behind each built packet, a PDF that says where every
filled box came from (docs/design_plan.md Part 4.1).

One row per answer filled on the packet's forms (an answer the form repeats
in several boxes, like the A-Number on every page, is one row): the form and
its Part/Item, the value as filled, and its source:
  - a document: its file name and page, with the crop of the page where the
    answer is (the same page images and crop the review cards show). A scanned
    questionnaire's reader recorded the box. A document read from its text
    layer (a passport, an I-94, a notice) recorded no page and no box: the page
    is then where the value stands in the file, found by searching the text for
    it as quoted, as recorded and in its other written forms (_Scans.find), and
    the crop is a line above and below it with a thin box round each place the
    value stands; a row with no crop says why ("page N (no crop: the value is
    not written on the page in a form that can be searched)"). A crop that can't be drawn says
    "crop unavailable" and the reason is logged, never swallowed;
  - the client's own answer in the portal, with the question and the date;
  - a rule: its name and plain text (src/rules/definitions.py);
  - a reviewer: name, role, date and note (decisions.json);
  - the firm's policy: its plain text (schemas/law/policy_sijs.json), or the
    firm's details and fixed answers for a form.
Rows are in the form's own order: each form in the packet's order, then its
boxes top to bottom, left to right, page by page. Social Security numbers,
ITINs and bank or card numbers show their last four digits only.
Below the rows, for an asylum, U, T or VAWA packet, the client's declaration
(src/drafting.py): each paragraph's question, source, date and language, and
every edit (old and new) and grammar smoothing (before and after). Then every
rule and policy used, with the attorney's approval for volume use
(src/rules/approval.py) or "Not yet approved".

Built only from what the packet and the review already recorded:
fact_graph_reviewed.json, evidence.json, decisions.json and the filled forms
themselves (the boxes as filled). Nothing is computed again and nothing is
looked up again. Watermarked with the firm's name and "Internal review
record, not for filing"; stored next to the packet in the client's folder;
never mailed. The PDF is drawn with the packet's own tooling
(fill/continuation.py's page drawing, pypdf): no new dependency.

The accuracy record (accuracy_pdf) is drawn with the same tooling.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import textwrap
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, StreamObject

from fill.continuation import COPY_MARK, HEIGHT, MARGIN, WIDTH, _Page
import clock
import events
import schema_path

REPO = Path(__file__).resolve().parents[2]

NOT_FOR_FILING = "Internal review record, not for filing"
log = logging.getLogger(__name__)


# -- the crop the review cards show (server.py /api/crop uses this too) -----------------


def crop(image, box) -> Any:
    """Application helper with evidence-bound inputs."""
    return image.crop(_crop_region(image, box))


def _crop_region(image, box) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = (int(float(v)) for v in (box.split(",") if isinstance(box, str) else box))
    pad, pad_y = 12, (45 if y1 - y0 < 80 else 12)
    return max(0, x0 - pad), max(0, y0 - pad_y), min(image.width, x1 + pad), min(image.height, y1 + pad_y)


def crop_whole_lines(image, box, reach: int = 40) -> Any:
    """crop() for the review bundle (buyer visits 2, 3: a strip with the half-lines above and below cut through, and nothing to show which
    words are the answer). The top and bottom edges move out to the nearest blank row of the scan, at most `reach` pixels (a ruled line
    counts as blank), so no line of writing is cut in half; and a thin box is drawn round the box the reader recorded."""
    from PIL import Image, ImageDraw

    left, top, right, bottom = _crop_region(image, box)
    x0, y0, x1, y1 = (int(float(v)) for v in (box.split(",") if isinstance(box, str) else box))
    gray = image.convert("L").crop((left, 0, right, image.height))
    ink = gray.point(lambda p: 255 if p < 150 else 0).resize((1, image.height), Image.BOX).tobytes()  # per row: how much of it is dark
    clear = lambda y: ink[y] < 255 * 0.012 or ink[y] > 255 * 0.6  # noqa: E731 -- nothing written on the row, or a ruled line across it
    new_top, new_bottom = top, bottom
    for _ in range(reach):
        if clear(new_top) or new_top <= 0:
            break
        new_top -= 1
    if not clear(new_top):
        new_top = top  # no gap within reach: keep the strip as it was
    for _ in range(reach):  # the crop's last row is new_bottom - 1
        if clear(new_bottom - 1) or new_bottom >= image.height:
            break
        new_bottom += 1
    if not clear(min(new_bottom, image.height) - 1):
        new_bottom = bottom
    out = image.crop((left, new_top, right, new_bottom)).convert("L")
    ImageDraw.Draw(out).rectangle((x0 - 3 - left, y0 - 3 - new_top, x1 + 3 - left, y1 + 3 - new_top), outline=0, width=2)
    return out


def found_crop(image, found: dict[str, Any]) -> Any:
    """The scan around a value found in the page's text layer (_Scans.find): about a line above and below it, a few characters to
    each side (wider when the value is short, so the words around it can be read), and a thin box drawn around every place of the
    value shown. found: {"pdf_box" (the region), "marks" (each place, in points from the page's top left), "page_size"}."""
    from PIL import ImageDraw

    s = image.width / found["page_size"][0]  # pixels per point
    marks = [[v * s for v in m] for m in found.get("marks") or [found["pdf_box"]]]
    left, top = min(m[0] for m in marks), min(m[1] for m in marks)
    right, bottom = max(m[2] for m in marks), max(m[3] for m in marks)
    line = min(max(max(m[3] - m[1] for m in marks), 9 * s), 18 * s)  # one line of text, in pixels
    side, wide = 2.4 * line, 170 * s  # four characters either side; no narrower than 170 points, so what stands around the value shows
    span = min(max(right - left + 2 * side, wide), image.width)
    x0 = min(max(0, (left + right) / 2 - span / 2), image.width - span)
    x1 = x0 + span
    y0, y1 = max(0, top - 1.4 * line), min(image.height, bottom + 1.4 * line)
    bands = [[v * s for v in band] for band in found.get("lines") or []]
    above = [b for b in bands if b[1] <= top + 0.3 * line and b[1] < (top + bottom) / 2 and (top - b[1]) < 2 * line]
    below = [b for b in bands if b[0] >= bottom - 0.3 * line and b[0] > (top + bottom) / 2 and (b[0] - bottom) < 2 * line]
    if above:  # start and end between lines: the line above and the line below, whole
        y0 = max(0, above[-1][0] - 0.15 * line)
    if below:
        y1 = min(image.height, below[0][1] + 0.15 * line)
    region = (int(x0), int(y0), int(x1), int(y1))
    out = image.crop(region).convert("L")
    draw = ImageDraw.Draw(out)
    grow = 1.5 * s
    for ml, mt, mr, mb in marks:
        draw.rectangle((ml - grow - region[0], mt - grow - region[1], mr + grow - region[0], mb + grow - region[1]), outline=0, width=max(2, int(0.9 * s)))
    return out


# -- finding a value in a page's text ---------------------------------------------------------

_MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
_ID_VALUE = re.compile(r"^(?:A-?\d{8,9}|[A-Z]{3}-?\d{10}|(?=[A-Z0-9 -]*\d)[A-Z0-9][A-Z0-9 -]{5,})$")
MAX_PLACES = 12  # most places one rendering is looked for


def _fold_text(text: str) -> str:
    """Upper case, accents removed, one character for each character (so an index in the result is an index in the text)."""
    import unicodedata

    out = []
    for c in text:
        base = "".join(x for x in unicodedata.normalize("NFKD", c.upper()) if not unicodedata.combining(x))
        out.append(base[:1] or c)
    return "".join(out)


def _renderings(value: Any) -> list[tuple[str, str]]:
    """The ways a value may be written on a page: [(text, mode)]. "words": whole words, any run of spaces between them; "ids":
    letters and digits with or without spaces, dashes or dots between them (A099 000 123, A-099-000-123, 099000123); "bare": as
    written, anywhere (a date in a passport's machine-readable line)."""
    text = " ".join(str(value if value is not None else "").split())
    if not text:
        return []
    out: list[tuple[str, str]] = []
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        y, m, d = (int(g) for g in iso.groups())
        try:
            from datetime import date

            date(y, m, d)
        except ValueError:
            return [(text, "words")]
        month, abbr = _MONTHS[m - 1], _MONTHS[m - 1][:3]
        for form in (f"{m:02d}/{d:02d}/{y}", f"{m}/{d}/{y}", f"{d:02d}/{m:02d}/{y}", f"{d}/{m}/{y}", f"{y}-{m:02d}-{d:02d}", f"{d:02d} {abbr} {y}", f"{d} {abbr} {y}",
                     f"{month} {d}, {y}", f"{abbr} {d}, {y}", f"{d} {month} {y}", f"{y} {month} {d:02d}", f"{y} {month} {d}", f"{m:02d}.{d:02d}.{y}"):
            out.append((form, "words"))
        out.append((f"{y % 100:02d}{m:02d}{d:02d}", "bare"))  # the machine-readable line of a passport
        return out
    if _ID_VALUE.match(text.upper()):
        out.append((text, "ids"))
        if re.fullmatch(r"A-?\d{8,9}", text.upper()):
            out.append((text.upper().lstrip("A-"), "ids"))  # the number without its letter
        return out
    out.append((text, "words"))
    folded = _fold_text(text)
    try:
        from questionnaire.languages import COUNTRIES

        out += [(name, "words") for name, canon in COUNTRIES.items() if canon == folded and name != folded]  # BRAZIL is written BRASIL in Portuguese
    except Exception:  # noqa: BLE001 -- the other renderings still stand
        pass
    return out


def _needles(raw: Any, value: Any) -> list[tuple[str, str]]:
    """Every wording to look for, most specific first: what the reader quoted from the page, what it recorded as read, then the
    value's own renderings. Duplicates dropped."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []

    def add(items: list[tuple[str, str]]) -> None:
        for text, mode in items:
            key = (_fold_text(" ".join(text.split())), mode)
            if len(re.sub(r"\W", "", key[0])) >= 2 and key not in seen:
                seen.add(key)
                out.append((text, mode))

    said = " ".join(str(raw if raw is not None else "").split())
    for quoted in re.findall(r"(?<![A-Za-z0-9])['\"“‘]([^'\"”’]{4,60}?)['\"”’](?![A-Za-z0-9])", said):
        add([(quoted, "words")])  # "lists this parent as 'JOSE EXEMPLO SOUZA'"
    if 3 <= len(said) <= 60 and not re.search(r"[\[\](){}\\|^$*+?]", said):
        add(_renderings(said) if _ID_VALUE.match(said.upper()) else [(said, "words")])
    add(_renderings(value))
    return out


def _bands(pages: list, n: int) -> list[list[float]]:
    """[[top, bottom]] of each line of text on page n (points from the page's top), so a crop can start and end between lines."""
    _n, _folded, body, boxes, _size = next(p for p in pages if p[0] == n)
    chars = sorted((b for b, c in zip(boxes, body) if not c.isspace() and b[3] > b[1]), key=lambda b: (b[1] + b[3]) / 2)
    bands: list[list[float]] = []
    for _l, top, _r, bottom in chars:
        if bands and top < bands[-1][1] - 0.35 * (bands[-1][1] - bands[-1][0]):
            bands[-1] = [min(bands[-1][0], top), max(bands[-1][1], bottom)]
        else:
            bands.append([top, bottom])
    return [[round(t, 1), round(b, 1)] for t, b in bands]


def _pattern(text: str, mode: str):
    """The regular expression for one wording, over text folded by _fold_text."""
    folded = _fold_text(text)
    if mode == "ids":
        body = r"[\s\-./]?".join(re.escape(c) for c in re.sub(r"[\s\-./]", "", folded))
    elif mode == "bare":
        return re.compile(re.escape(folded))
    else:
        body = r"\s+".join(re.escape(w) for w in folded.split())
    return re.compile(r"(?<![A-Z0-9])" + body + r"(?![A-Z0-9])")


# -- drawing --------------------------------------------------------------------------------


class _Sheet(_Page):
    """A page of text, lines and scan crops (JPEG, embedded as they are)."""

    def __init__(self):
        super().__init__()
        self.images: list[tuple[str, bytes, int, int]] = []

    def image(self, jpeg: bytes, size: tuple[int, int], x: float, y: float, w: float, h: float) -> None:
        name = f"Im{len(self.images) + 1}"
        self.images.append((name, jpeg, size[0], size[1]))
        self.ops.append(f"q {w:.1f} 0 0 {h:.1f} {x:.1f} {y:.1f} cm /{name} Do Q")

    def to_page(self, writer: PdfWriter):
        page = super().to_page(writer)
        if self.images:
            xobjects = DictionaryObject()
            for name, data, w, h in self.images:
                stream = StreamObject()
                stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                               NameObject("/Width"): NumberObject(w), NameObject("/Height"): NumberObject(h),
                               NameObject("/ColorSpace"): NameObject("/DeviceGray"), NameObject("/BitsPerComponent"): NumberObject(8),
                               NameObject("/Filter"): NameObject("/DCTDecode")})
                stream.set_data(data)
                xobjects[NameObject(f"/{name}")] = writer._add_object(stream)
            page[NameObject("/Resources")][NameObject("/XObject")] = xobjects
        return page


def _wrap(text: Any, size: float, width: float) -> list[str]:
    # Helvetica averages about half its size per character
    lines: list[str] = []
    for part in str(text if text is not None else "").splitlines() or [""]:
        lines += textwrap.wrap(part, max(8, int(width / (size * 0.5))), break_long_words=True) or [""]
    return lines


def _watermark(p: _Sheet, firm: str) -> None:
    # 40 degrees across the middle of the page, light grey, under everything else
    p.ops[:0] = [f"q 0.88 g BT /F2 30 Tf 0.766 0.643 -0.643 0.766 120 170 Tm {_s(firm.upper())} Tj ET Q",
                 f"q 0.88 g BT /F2 22 Tf 0.766 0.643 -0.643 0.766 110 110 Tm {_s(NOT_FOR_FILING.upper())} Tj ET Q"]


def _s(text: str) -> str:
    from fill.continuation import _pdf_string

    return _pdf_string(text)


def _jpeg(image, max_w: float, max_h: float) -> tuple[bytes, tuple[int, int], float, float]:
    """The crop as a small grayscale JPEG, and its size on the page (points) within max_w x max_h."""
    image = image.convert("L")
    scale = min(max_w / image.width, max_h / image.height, 1.0)
    w, h = max(1.0, image.width * scale), max(1.0, image.height * scale)
    # twice the printed size in pixels is sharp enough to read handwriting on paper
    px = min(1.0, (2 * w) / image.width)
    if px < 1.0:
        image = image.resize((max(1, int(image.width * px)), max(1, int(image.height * px))))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=70)
    return buf.getvalue(), image.size, w, h


class _Doc:
    """Pages with a running header, a watermark and a footer; y runs down from the top."""

    def __init__(self, firm: str, header: str, footer: str):
        self.firm, self.header, self.footer = firm, header, footer
        self.pages: list[_Sheet] = []
        self.p: _Sheet | None = None
        self.y = 0.0
        self.on_new_page = None  # draws the column headings of a table that continues
        self.new_page()

    def new_page(self) -> None:
        self.p = _Sheet()
        self.pages.append(self.p)
        _watermark(self.p, self.firm)
        self.p.text(MARGIN, HEIGHT - 34, self.firm, "F2", 9)
        self.p.text(WIDTH - MARGIN - 190, HEIGHT - 34, NOT_FOR_FILING, "F2", 8)
        self.p.text(MARGIN, HEIGHT - 46, self.header, "F3", 8)
        self.p.line(MARGIN, HEIGHT - 52, WIDTH - MARGIN, HEIGHT - 52, 0.5)
        self.y = HEIGHT - 70
        if self.on_new_page:
            self.on_new_page()

    def need(self, height: float) -> None:
        if self.y - height < 56:
            self.new_page()

    def line_text(self, text: str, size: float = 9, font: str = "F3", x: float = MARGIN, width: float = WIDTH - 2 * MARGIN, gap: float = 3) -> None:
        for line in _wrap(text, size, width):
            self.need(size + 3)
            self.p.text(x, self.y, line, font, size)
            self.y -= size + 3
        self.y -= gap

    def heading(self, text: str, size: float = 12) -> None:
        self.need(size + 30)
        self.y -= 6
        self.p.text(MARGIN, self.y, text, "F2", size)
        self.y -= size + 6

    def finish(self) -> bytes:
        writer = PdfWriter()
        for n, page in enumerate(self.pages, start=1):
            page.text(MARGIN, 30, self.footer, "F3", 7)
            page.text(WIDTH - MARGIN - 60, 30, f"Page {n} of {len(self.pages)}", "F3", 7)
            writer.add_page(page.to_page(writer))
        writer.add_metadata({"/Title": self.header, "/Subject": NOT_FOR_FILING})
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()


# -- the rows -------------------------------------------------------------------------------


def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _us_date(iso: Any) -> str:
    from review.state import us_date

    return us_date(iso)


def _packet(client_dir: Path, filing: str | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    import packet

    schema = packet.for_case(packet.load_filing(filing), client_dir)
    return schema, _read(client_dir / schema.get("manifest", "packet.json"), None)


def _packet_title(client_dir: Path, filing: str | None) -> str:
    import packet

    return packet.filing_title(filing)


def paths(client_dir: Path, filing: str | None) -> tuple[Path, Path]:
    """(the bundle's PDF, its record), next to the packet: packet.pdf -> packet_review_bundle.pdf."""
    schema, _ = _packet(client_dir, filing)
    stem = Path(schema.get("packet_pdf", "packet.pdf")).stem
    return client_dir / f"{stem}_review_bundle.pdf", client_dir / f"{stem}_review_bundle.json"


def _form_map(fid: str, profile: dict[str, Any]) -> tuple[dict | None, Any, bool, set]:
    """(fact key -> field spec with full field names, a Catalog for its Part/Item labels, a family member's copy?, the form's fixed answers)."""
    from fill import load_field_map
    from fill.companion import field_map_for
    from review.state import Catalog

    if fid == "i485":
        fmap = load_field_map(schema_path.path("field_map", "i485"))
        return fmap, _catalog(fmap, schema_path.path("template", "i485")), False, set()
    import packet

    forms = profile["forms"]
    form = forms[fid] if fid in forms else packet.instance(fid, forms)
    if not form.get("template") or not form.get("map"):
        return None, None, False, set()
    fmap = field_map_for(form)
    return fmap, Catalog(fmap, schema_path.named(form["template"])), bool(form.get("instance_of")), set(profile.get("constants", {}))


def _template_of(fid: str, profile: dict[str, Any]) -> Path:
    if fid == "i485":
        return schema_path.path("template", "i485")
    import packet

    forms = profile["forms"]
    name = (forms[fid] if fid in forms else packet.instance(fid, forms)).get("template") or ""
    return schema_path.named(name) if name else Path("")


_P14_KEY = re.compile(r"(?P<prefix>[a-z0-9_]+)\.p14_block(?P<n>\d+)_(?P<part>page|part|item|text)")
_P14_LINES = {"prior_address": "address history", "prior_employer": "job and school history", "child": "children",
              "prior_spouse": "earlier marriages", "organization": "organizations", "other_a_numbers": "other A-Numbers"}


def _p14_line(raw: str) -> str:
    """Plain words for the line an entry was composed from ("composed from questionnaire.prior_address2" ->
    "the client's address history, line 2"); an entry with its own wording (a suggested explanation) keeps it."""
    m = re.fullmatch(r"composed from questionnaire\.([a-z_]+?)(\d*)", raw or "")
    if m:
        return f"the client's {_P14_LINES.get(m.group(1), m.group(1).replace('_', ' '))}" + (f", line {m.group(2)}" if m.group(2) else "")
    if not raw or re.search(r"[a-z]+_[a-z]+|\w\.\w", raw):  # a fact key is not words for a person
        return "the client's questionnaire"
    return raw


def _part14_landings(rows: list[dict[str, Any]], graph: Any, template: Path, short: str, sources_for) -> None:
    """On each Part 14 entry's row: the line it came from and which box of which copy of the form's Part 14 page it
    landed in (fill/continuation.py lays them out the same way as the fill did), and its whole text as the value
    (the form's own page shows the first box only). An entry past the form's own boxes has no box of its own in the
    field map, so it gets a row here: it is on a copy of the page and every entry is accounted for.
    sources_for(fact key) -> the row's sources."""
    from batch import part14_blocks
    from fill.continuation import find_page, place_blocks

    prefixes = {m.group("prefix") for r in rows if (m := _P14_KEY.fullmatch(r.get("key") or ""))}
    if not prefixes and template == schema_path.path("template", "i485"):
        prefixes = {"applicant"}
    for prefix in prefixes:
        if not template.is_file() or find_page(template) is None:
            continue
        entries = part14_blocks(graph, prefix)
        if not entries:
            continue
        layout = place_blocks(template, [b for _, b in entries])
        for index, (n, block) in enumerate(entries):
            key = f"{prefix}.p14_block{n}_text"
            row = next((r for r in rows if r.get("key") == key), None)
            if row is None:
                row = {"form": short, "key": key, "ref": "Part 14", "boxes": 1,
                       "label": f"Part 14, entry {n}: refers to page {block.page or '?'}, part {block.part or '?'}, item {block.item or '?'}",
                       "sources": sources_for(key)}
                rows.append(row)
            row["value"] = block.text
            row["landed"] = layout.where(index) + ("" if block.page and block.part and block.item
                                                   else ". The page, part or item is blank (the form could not vouch for it): enter it by hand")
            row["line"] = _p14_line(block.source)


_CATALOGS: dict[str, Any] = {}


def _catalog(fmap: dict, template: Path):
    from review.state import Catalog

    if str(template) not in _CATALOGS:
        _CATALOGS[str(template)] = Catalog(fmap, template)
    return _CATALOGS[str(template)]


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def _filled(pdf: Path) -> list[tuple[str, str, bool, str]]:
    """Every box with a value in a filled form, in the form's reading order (_positions): (full name, value, is a checkbox, tooltip)."""
    reader = PdfReader(str(pdf))
    if reader.is_encrypted:
        reader.decrypt("")
    places = _positions(reader)
    out = []
    for name, field in (reader.get_fields() or {}).items():
        value = field.get("/V")
        if value is None or str(value).strip() in ("", "/Off", "Off"):
            continue
        if "PDF417BarCode" in name:
            continue  # the form's own barcode, not an answer
        if COPY_MARK in name:
            continue  # a copy of the form's Part 14 page: its entries are the Part 14 rows' (where each landed is said there)
        out.append((name, str(value).strip(), field.get("/FT") == "/Btn", re.sub(r"\s+", " ", str(field.get("/TU") or "")).strip()))
    out.sort(key=lambda box: places.get(box[0], (10**6, 0, 0, 0.0)))  # stable: a box with no place on a page keeps the list's order, last
    return out


def _decision_index(client_dir: Path) -> dict[str, dict[str, Any]]:
    """fact key -> the latest decision in force that covers it."""
    from review.state import load_decisions

    out: dict[str, dict[str, Any]] = {}
    for d in sorted(load_decisions(client_dir).values(), key=lambda d: clock.key(d.get("at"))):
        for key in (d.get("item") or {}).get("facts", []):
            out[key] = d
    return out


_PORTAL_QUESTIONS: tuple[dict[str, str], list[tuple[re.Pattern, str]]] | None = None


def _portal_question(key: str) -> str:
    """The portal question (English) that fills a fact key: the question bank names each question's fact key(s)
    (src/portal/bank.py answers_to_facts), for the green-card questions and the N-400's. "" when none does."""
    global _PORTAL_QUESTIONS
    if _PORTAL_QUESTIONS is None:
        from portal.bank import all_questions, load_bank

        exact: dict[str, str] = {}
        patterns: list[tuple[re.Pattern, str]] = []
        for bank in (load_bank(), load_bank(filing="n400"), load_bank(filing="parole")):
            for question in all_questions(bank).values():
                label = re.sub(r"\s+[—–-]+\s+", ", ", (question.get("label") or {}).get("en") or "")  # no dash in the middle of a sentence on a page
                keys = ([question["fact"]] if question.get("fact") else []) + list((question.get("facts") or {}).values())
                for k in keys:
                    exact.setdefault(k, label)
                if question.get("fact_pattern"):
                    patterns.append((re.compile(re.escape(question["fact_pattern"]).replace(r"\{n\}", r"\d+").replace(r"\{part\}", r"\w+")), label))
        _PORTAL_QUESTIONS = (exact, patterns)
    exact, patterns = _PORTAL_QUESTIONS
    return exact.get(key) or next((label for pattern, label in patterns if pattern.fullmatch(key)), "")


class _Scans:
    """The client's source files, for the page and the crop of a row's document source.
    page_images(doc) -> the page images the review cards use (server.py page_image); without it they are rendered here from the case's
    source folder. source_path(doc) -> the file itself (server.py source_file). A file that can't be read is logged, never swallowed:
    the row then says "crop unavailable"."""

    def __init__(self, client_dir: Path, page_images=None, source_path=None):
        self.client_dir, self.page_images, self.source_path = Path(client_dir), page_images, source_path
        self._images: dict[str, Any] = {}
        self._found: dict[tuple, tuple] = {}

    def path(self, file: str) -> Path | None:
        try:
            if self.source_path is not None:
                return Path(self.source_path(file))
            folder = _read(self.client_dir / "meta.json", {}).get("source_folder")
            path = Path(folder) / file if folder else None
            return path if path is not None and path.is_file() else None
        except Exception as exc:  # noqa: BLE001 -- logged; the row says its crop is unavailable
            log.warning("review bundle: no source file for %s in %s: %s", file, self.client_dir.name, exc)
            return None

    def images(self, file: str):
        """The document's page images, or None (logged) when they can't be made."""
        if file not in self._images:
            try:
                if self.page_images is not None:
                    self._images[file] = self.page_images(file)
                else:
                    from questionnaire.pages import page_images as render

                    path = self.path(file)
                    if path is None:
                        raise FileNotFoundError(f"{file} is not in the case's source folder")
                    self._images[file] = render(path)
            except Exception as exc:  # noqa: BLE001 -- logged; the row says "crop unavailable"
                log.warning("review bundle: could not read the pages of %s in %s: %s", file, self.client_dir.name, exc)
                self._images[file] = None
        return self._images[file]

    def find(self, doc: str, raw: Any, value: Any = None) -> tuple[int | None, dict[str, Any] | None, str]:
        """(the 0-based page, the crop, why there is no crop) of a document source the reader gave no page or box for ("file.pdf" or
        "file.pdf#p3-4"). The page is where the value stands in the file's text layer, looked for as the reader quoted it, as it
        recorded it and in the other ways the value is written (a date as MM/DD/YYYY or "Month D, YYYY", a number with or without
        spaces and dashes, a country in Portuguese). The crop is returned for a value that stands in one place (boxed), or in several
        places close together on one page (all boxed, the crop says how many); a value that stands in places far apart on a page has the
        first place shown and the count said. A one-page document is page 1."""
        file, _, span = doc.partition("#")
        m = re.fullmatch(r"p(\d+)(?:-(\d+))?", span)
        first, last = (int(m.group(1)) - 1, int(m.group(2) or m.group(1)) - 1) if m else (None, None)
        key = (file, first, last, str(raw or "").strip(), str(value if value is not None else "").strip())
        if key not in self._found:
            self._found[key] = self._search(file, first, last, raw, value)
        return self._found[key]

    def _search(self, file: str, first: int | None, last: int | None, raw: Any, value: Any) -> tuple[int | None, dict[str, Any] | None, str]:
        path = self.path(file)
        if path is None:
            return None, None, "the file is not in the case's folder"
        needles = _needles(raw, value)
        lo = first or 0
        try:
            import pypdfium2 as pdfium

            from questionnaire.pages import PDFIUM_LOCK

            with PDFIUM_LOCK:
                pdf = pdfium.PdfDocument(str(path))
                try:
                    hi = len(pdf) - 1 if last is None else min(last, len(pdf) - 1)
                    pages = []  # (page index, folded text, the box of every character (left, top, right, bottom in points from the top left), size)
                    for n in range(lo, hi + 1):
                        page = pdf[n]
                        try:
                            text = page.get_textpage()
                            try:
                                body = text.get_text_range()
                                if body.strip() and len(body) == text.count_chars():
                                    h = page.get_height()
                                    boxes = [text.get_charbox(i) for i in range(len(body))]
                                    pages.append((n, _fold_text(body), body, [(b[0], h - b[3], b[2], h - b[1]) for b in boxes], (page.get_width(), h)))
                            finally:
                                text.close()
                        finally:
                            page.close()
                finally:
                    pdf.close()
        except Exception as exc:  # noqa: BLE001 -- logged; the row then has no page or crop
            log.warning("review bundle: could not search %s in %s: %s", file, self.client_dir.name, exc)
            return None, None, "the page could not be searched"
        if not pages:
            return (lo if lo == hi else None), None, "the page has no text to search (a scan)"
        found: list[tuple[str, list[tuple[int, list[float], tuple[float, float]]]]] = []  # (wording, its places)
        for text, mode in needles:
            pattern = _pattern(text, mode)
            places = []
            for n, folded, body, boxes, size in pages:
                for hit in pattern.finditer(folded):
                    chars = [boxes[i] for i in range(hit.start(), hit.end()) if not body[i].isspace()]
                    if chars and len(places) < MAX_PLACES:
                        places.append((n, [min(c[0] for c in chars), min(c[1] for c in chars), max(c[2] for c in chars), max(c[3] for c in chars)], size))
            if places:
                found.append((text, places))
        if not found:
            return (lo if lo == hi else None), None, "the value is not written on the page in a form that can be searched"
        unique = next((places for text, places in found if len(places) == 1), None)
        if unique:
            n, box, (w, h) = unique[0]
            return n, {"doc": file, "page": n, "pdf_box": box, "marks": [box], "page_size": [w, h], "places": 1, "lines": _bands(pages, n)}, ""
        text, places = found[0]
        if len({n for n, _b, _s in places}) > 1:
            return (lo if lo == hi else None), None, "the value stands on several pages"
        n, anchor, (w, h) = places[0]
        near = [b for _n, b, _s in places if abs(b[1] - anchor[1]) <= 120]  # the places within a few lines of the first one
        if len(re.sub(r"\W", "", text)) < 4 and len(places) > 1:
            return n, None, f"the value stands in {len(places)} places on the page"  # a short code is everywhere
        box = [min(b[0] for b in near), min(b[1] for b in near), max(b[2] for b in near), max(b[3] for b in near)]
        return n, {"doc": file, "page": n, "pdf_box": box, "marks": near, "page_size": [w, h], "places": len(places), "lines": _bands(pages, n)}, ""


def _positions(reader: PdfReader) -> dict[str, tuple[int, int, int, float]]:
    """Each box's place on the form, by its full name: (page, column, row from the top, left edge): the order a person checks a form
    in, page by page. (The field list's own order is the form's data order and jumps: a G-28 lists Item 4, then 2B, 2A.)
    A page set in two columns (the G-28's first and second: Part 1 on the left, Part 2 on the right) reads the left column down,
    then the right: found where no box of the page crosses a vertical line between the two."""
    places: dict[str, tuple[int, float, float, float]] = {}
    spans: dict[int, list[tuple[float, float]]] = {}
    for n, page in enumerate(reader.pages):
        for ref in page.get("/Annots") or []:
            widget = ref.get_object()
            if widget.get("/Subtype") != "/Widget" or "/Rect" not in widget:
                continue
            names, node = [], widget
            while node is not None:
                if "/T" in node:
                    names.append(str(node["/T"]))
                node = node["/Parent"].get_object() if "/Parent" in node else None
            x0, y0, x1, y1 = (float(v) for v in widget["/Rect"])
            full = ".".join(reversed(names))
            places.setdefault(full, (n, max(y0, y1), min(x0, x1), max(x0, x1)))
            if "PDF417BarCode" not in full:  # the form's own barcode runs across the foot of every page: not a box
                spans.setdefault(n, []).append((min(x0, x1), max(x0, x1)))
    gutter: dict[int, float | None] = {}
    for n, boxes in spans.items():
        width = float(reader.pages[n].mediabox.width)
        gutter[n] = next((g for g in range(int(width * 0.45), int(width * 0.6), 2)
                          if not any(x0 < g < x1 for x0, x1 in boxes)
                          and sum(x1 <= g for _x0, x1 in boxes) >= 4 and sum(x0 >= g for x0, _x1 in boxes) >= 4), None)
    return {name: (n, int(gutter[n] is not None and x0 >= gutter[n]), -round(top / 10), x0) for name, (n, top, x0, _x1) in places.items()}


_ACTION_WORDS = {"confirm": "confirmed", "set": "entered or corrected", "blank": "left blank", "acknowledge": "acknowledged",
                 "absent": "recorded that the client has no such paper"}


def _sources(key: str, fact: dict[str, Any] | None, ctx: dict[str, Any], family_copy: bool, constants: set, private: bool = False) -> list[dict[str, Any]]:
    from review.state import SOURCE_NAMES, mask_number, rule_info

    def shown(value: Any) -> Any:  # a Social Security number, an ITIN or a bank number is never printed whole
        return mask_number(value) if private and value not in (None, "") else value

    if family_copy:
        return [{"kind": "filing", "title": "This person's own answers",
                 "text": "A family member's copy of the form, filled from that person's answers in this filing."}]
    if fact is None:
        said = ctx["decisions"].get(key)
        if said and said.get("action") == "absent":  # the box reads NOT APPLICABLE because the office recorded the client has no such paper (src/absence.py)
            return [{"kind": "reviewer", "title": "Reviewer", "name": said.get("reviewer"), "role": said.get("role"), "date": _us_date(said.get("at")),
                     "action": _ACTION_WORDS["absent"], "note": said.get("note") or ""}]
        if key in constants:
            return [{"kind": "policy", "title": "The firm's fixed answer for this form",
                     "text": "A firm choice for every case of this kind; it needs the attorney's approval, like the firm's policies."}]
        if key.startswith("firm."):
            return [{"kind": "firm", "title": "The firm's details", "text": "From the Settings page (the case's office)."}]
        if key.startswith("g28."):  # the G-28's address and Part 4: the case's card, confirmed by a person (the card's own page follows the rows)
            return [{"kind": "reviewer", "title": "The G-28 card for this case",
                     "text": "The address and the Part 4 boxes are the choices on this case's G-28 card, each with who confirmed it and any change and its reason (below)."}]
        return [{"kind": "filing", "title": "This filing's own answers",
                 "text": "Worked out for this filing from the reviewed case and the filing's questions."}]
    out: list[dict[str, Any]] = []
    decision = ctx["decisions"].get(key)
    if fact.get("review"):
        review = fact["review"]
        out.append({"kind": "reviewer", "title": "Reviewer",
                    "name": (decision or {}).get("reviewer") or review.get("resolved_by"), "role": (decision or {}).get("role"),
                    "date": _us_date((decision or {}).get("at")), "action": _ACTION_WORDS.get((decision or {}).get("action"), "decided"),
                    "note": (decision or {}).get("note") or ""})
    if fact.get("derived_by"):
        info = rule_info(fact["derived_by"])
        ctx["rules"].setdefault(info["id"], {**info, "rows": 0})["rows"] += 1
        out.append({"kind": info["kind"], "title": info["name"], "rule": info["id"], "text": info["plain_text"], "edited": info.get("edited_text")})
    crops = {}
    for doc, _qid, ev in ctx["evidence"].get(key, []):
        if ev.get("page") is not None and ev.get("box"):
            crops.setdefault(doc, {"doc": doc, "page": ev["page"], "box": ev["box"]})
    seen = set()
    for s in fact.get("sources") or []:
        doc, kind = str(s.get("doc_id") or ""), s.get("doc_type") or ""
        if doc == "paralegal_review" or kind in ("paralegal_review", "absence_mark"):  # the reviewer's line above says who recorded that the client has no such paper
            continue
        if kind == "firm_profile":
            entry = {"kind": "firm", "title": "The firm's details", "text": "From the Settings page (the case's office)."}
        elif kind == "derived" or doc == "fact_graph":
            continue  # worked out from the facts above, not a source of its own
        elif kind == "office_question" or doc == "office question":  # the client's typed answer to a question the office asked in the portal
            entry = {"kind": "portal", "title": f"The client's answer to the office's question ({_us_date(s.get('extracted_at'))})".replace(" ()", ""),
                     "question": "", "value": shown(s.get("normalized_value"))}
        elif doc in ("portal questionnaire", "portal") or (kind in ("portal", "intake_questionnaire") and not doc.lower().endswith(".pdf")):
            # typed by the client in the portal: no file, no page. The question and the day the case recorded the answer, as the review card says it.
            entry = {"kind": "portal", "title": f"The client's answer in the portal ({_us_date(s.get('extracted_at'))})".replace(" ()", ""),
                     "question": _portal_question(key), "value": shown(s.get("normalized_value"))}
        else:
            file, _, pages = doc.partition("#")
            span = re.match(r"p(\d+)", pages)
            name = SOURCE_NAMES.get(kind, ("the " + kind.replace("_", " ")) if kind else "a document")
            entry = {"kind": "document", "title": name[:1].upper() + name[1:], "doc": file, "value": shown(s.get("normalized_value"))}
            if file in crops:  # a scanned questionnaire: the reader recorded the page and the box
                entry["crop"] = crops.pop(file)
                entry["page"] = entry["crop"]["page"] + 1
            else:  # read from the text layer: the page, and the spot (boxed) where the value stands on it
                page0, found, why = ctx["scans"].find(doc, s.get("raw_value"), s.get("normalized_value"))
                entry["page"] = page0 + 1 if page0 is not None else int(span.group(1)) if span else None
                if private:  # a crop would print the whole number the row hides: the page only
                    found, why = None, "a private number: the row shows its last four digits only, so the scan is not drawn"
                if found:
                    entry["crop"] = found
                else:
                    entry["no_crop"] = why
        sig = (entry["kind"], entry.get("doc"), entry.get("title"), str(entry.get("value")))
        if sig not in seen:
            seen.add(sig)
            if entry["kind"] in ("document", "portal"):  # for _used_note: does it say what the box says, and how sure was the reader
                entry["same"], entry["confidence"] = s.get("normalized_value") == fact.get("value"), s.get("confidence") or 0
            out.append(entry)
    for doc, c in crops.items():  # read off a scan this fact has no source entry for (a derived box built from it)
        out.append({"kind": "document", "title": "The client's questionnaire", "doc": doc, "page": c["page"] + 1, "crop": c})
    note = _used_note(out, fact.get("status"))
    if note:
        out.insert(next(i for i, e in enumerate(out) if e["kind"] in ("document", "portal")), {"kind": "used", "text": note})
    return out or [{"kind": "none", "title": "Not traced", "text": "No record says where this value came from."}]


def _plain_name(entry: dict[str, Any]) -> str:
    """A source's name inside a sentence: "the I-360 approval notice", "the client's answer in the portal"."""
    title = re.sub(r"\s*\([^)]*\)\s*$", "", entry.get("title") or "a source")
    return "the " + title[4:] if title.startswith("The ") else title


def _used_note(sources: list[dict[str, Any]], status: str | None) -> str:
    """When two or more sources (documents, the client's portal answer) speak to a box, which one the value was taken from and which agree
    ("Used: the I-360 approval notice; the client's answer in the portal and the I-94 agree."). The graph takes the first source when they
    agree and the surest reader's when they differ (factgraph.FactGraph._recompute); a different value a reviewer or a rule chose is the
    Reviewer or Rule entry. Nothing is said for one source, or when none of them says what the box says."""
    said = [s for s in sources if s["kind"] in ("document", "portal") and "same" in s]
    agree = [s for s in said if s["same"]]
    if len(said) < 2 or not agree:
        return ""
    used = max(agree, key=lambda s: s["confidence"]) if status == "conflict" else agree[0]

    def names(items: list[dict[str, Any]]) -> list[str]:
        return list(dict.fromkeys(_plain_name(s) for s in items if s is not used and _plain_name(s) != _plain_name(used)))

    def joined(words: list[str]) -> str:
        return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]

    also, other = names(agree), names([s for s in said if not s["same"]])
    text = f"Used: {_plain_name(used)}" + (f"; {joined(also)} {'agrees' if len(also) == 1 else 'agree'}" if also else "") + "."
    return text + (f" {joined(other)[:1].upper() + joined(other)[1:]} {'says' if len(other) == 1 else 'say'} something else (shown below)." if other else "")


def rows(client_dir: Path, filing: str | None = None, scans: _Scans | None = None) -> dict[str, Any]:
    """The bundle's content without drawing it: {packet, forms, rows, rules, firm, summary}.
    scans: where the documents are (build() passes the review app's); without it, the case's own source folder."""
    from fill.companion import load_profile
    from factgraph import FactGraph
    from review.state import _evidence_index, case_summary, i485_ref, is_private_number, mask_number, STRAY_REFS

    client_dir = Path(client_dir)
    schema, manifest = _packet(client_dir, filing)
    if manifest is None:
        raise ValueError("Build the packet first: the review bundle explains a built packet.")
    reviewed = client_dir / "fact_graph_reviewed.json"
    if not reviewed.exists():
        raise ValueError("This case hasn't been reviewed and re-filled yet: build the packet first.")
    facts = _read(reviewed, {}).get("facts", {})
    by_fact, _ = _evidence_index(_read(client_dir / "evidence.json", {}))
    ctx = {"decisions": _decision_index(client_dir), "evidence": by_fact, "rules": {}, "scans": scans or _Scans(client_dir)}
    profile = load_profile()
    out_rows: list[dict[str, Any]] = []
    graph = FactGraph.load(reviewed)
    for form in manifest.get("forms") or []:
        pdf = client_dir / form["file"]
        if not pdf.exists():
            continue
        fmap, catalog, family_copy, constants = _form_map(form["id"], profile)
        if fmap is None:
            continue
        first_row = len(out_rows)
        owner: dict[str, str] = {}
        for key, spec in fmap.items():
            for name in _strings(spec):
                owner.setdefault(name, key)
        grouped: dict[Any, list] = {}
        for name, value, is_box, tip in _filled(pdf):
            grouped.setdefault(owner.get(name) or ("?", name), []).append((name, value, is_box, tip))
        for key, boxes in grouped.items():
            if isinstance(key, tuple):  # a box no fact fills: said so, never guessed
                name, value, is_box, tip = boxes[0]
                value = value.lstrip("/") if is_box else value
                out_rows.append({"form": form["short"], "ref": i485_ref(tip), "label": _item_label(_trim(tip)) or name.rsplit(".", 1)[-1],
                                 "value": mask_number(value) if not is_box and is_private_number(None, tip) else value, "boxes": 1,
                                 "sources": [{"kind": "none", "title": "Not traced", "text": "No fact fills this box: check it by hand."}]})
                continue
            fact = None if family_copy else facts.get(key)
            texts = {v for _n, v, box, _t in boxes if not box}
            if any(box for _n, _v, box, _t in boxes) or len(texts) != 1:  # a ticked box, or a value split over boxes (height, weight)
                value = (fact or {}).get("value") if fact else None
                value = value if value not in (None, "") else ", ".join(v.lstrip("/") for _n, v, _b, _t in boxes)
                value = f"{_shown(value)} (box ticked)" if all(box for _n, _v, box, _t in boxes) else _shown(value)
            else:
                value = texts.pop()
            private = is_private_number(key, catalog.label(key))
            if private and not value.endswith("(box ticked)"):
                value = mask_number(value)  # the last four digits only, here and in what the sources say
            ref = catalog.ref(key) or i485_ref(catalog.label(key))
            label = _item_label(catalog.label(key))
            if any(stray in boxes[0][0] for stray in STRAY_REFS):  # a stray number in the tooltip: the printed "26. A." is not this box's item
                label = _label_for(label)
            out_rows.append({"form": form["short"], "key": key, "ref": ref, "label": label,
                             "value": value, "boxes": len(boxes), "sources": _sources(key, fact, ctx, family_copy, constants, private)})
        if not family_copy:
            new_rows = out_rows[first_row:]
            _part14_landings(new_rows, graph, _template_of(form["id"], profile), form["short"],
                             lambda key: _sources(key, facts.get(key), ctx, False, constants))
            out_rows[first_row:] = new_rows
    firm = facts.get("firm.business_name", {}).get("value") if facts.get("firm.business_name", {}).get("status") == "resolved" else None
    if not firm:
        import offices

        office = offices.for_case(client_dir)
        firm = office["values"].get("firm.business_name") or office["name"]
    shorts = [f["short"] for f in manifest.get("forms") or []]
    main = next((s for s in shorts if not s.startswith("G-")), shorts[0] if shorts else "")  # the G-28 and G-1145 ride along
    import drafting

    declaration = drafting.bundle_rows(client_dir, schema.get("filing"))  # the client's declaration: each paragraph's source and edits
    import g28
    import part14_explain

    explanations = part14_explain.bundle_rows(client_dir) if "i485" in [f["id"] for f in manifest.get("forms") or []] else None  # Part 9's Yes answers explained
    return {"declaration": declaration, "explanations": explanations, "g28": g28.bundle_rows(client_dir) if g28.governed([f["id"] for f in manifest.get("forms") or []]) else None, "filing": schema.get("filing", "i485"), "title": f"{main} packet" if main else "Filing packet", "packet": {k: manifest.get(k) for k in ("built_at", "built_by", "draft", "pages", "sha256")},
            "forms": [f["short"] for f in manifest.get("forms") or []], "rows": out_rows, "rules": sorted(ctx["rules"].values(), key=lambda r: (r["kind"] != "rule", r["id"])),
            "firm": str(firm), "summary": case_summary(graph), "client_id": client_dir.name}


def _shown(value: Any) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(value))
    return f"{m.group(2)}/{m.group(3)}/{m.group(1)}" if m else str(value)


def _trim(tip: str) -> str:
    tip = re.sub(r"\s*Select [^.]*\.?\s*$", "", tip)
    return tip[-200:] if len(tip) > 200 else tip


def _label_for(label: str) -> str:
    """The label without its leading stray item number ("26. A. Enter Form I - 94 ..." -> "Enter Form I - 94 ..."); only for a box in state.STRAY_REFS."""
    lead = re.match(r"(\d{1,2})\.\s?(?:[A-Z]\.\s+)?", label)
    return label[lead.end():] if lead and len(label) > lead.end() + 8 else label


def _item_label(label: str) -> str:
    """The numbered question itself, without the Part's preamble the form repeats in every tooltip (the Part/Item is shown
    beside it): "Part 1. Information About Attorney ... 4. Enter Daytime Telephone Number" -> "4. Enter Daytime Telephone Number"."""
    starts = [m.start(1) for m in re.finditer(r"(?:^|\s)(\d{1,2}(?:\.?\s?[A-Z])?\.\s+[A-Z])", label)
              if not label[:m.start(1)].endswith("Part ") and not re.search(r"-\s*$", label[:m.start(1)])]  # "Part 1. Information..." is the Part; "Form I - 94. Enter" is a form
    return label[starts[-1]:] if starts and len(label) - starts[-1] > 12 else label


# -- the PDF --------------------------------------------------------------------------------

_COL = [(MARGIN, 168), (MARGIN + 174, 118), (MARGIN + 298, WIDTH - 2 * MARGIN - 298)]
_KIND_WORDS = {"document": "Document", "rule": "Rule", "policy": "Firm policy", "reviewer": "Reviewer", "firm": "Firm's details",
               "filing": "This filing", "none": "Not traced", "portal": "Client's portal answer"}


def _source_lines(s: dict[str, Any]) -> list[tuple[str, str]]:
    """(font, text) lines for one source, before wrapping."""
    kind = _KIND_WORDS.get(s["kind"], s["kind"])
    if s["kind"] == "reviewer":
        who = s.get("name") or "a reviewer"
        out = [("F2", f"Reviewer: {who}" + (f" ({s['role']})" if s.get("role") else "")),
               ("F3", f"{s.get('action', 'decided').capitalize()} on {s.get('date') or 'an unrecorded date'}")]
        if s.get("note"):
            out.append(("F3", f"Note: {s['note']}"))
        return out
    if s["kind"] == "used":  # which of several sources the value was taken from
        return [("F2", s["text"])]
    if s["kind"] == "portal":  # typed by the client: the question as they read it (in English) and what they answered
        out = [("F2", s.get("title") or "The client's answer in the portal")]
        if s.get("question"):
            out.append(("F3", f"Question: {s['question']}"))
        if s.get("value") not in (None, ""):
            out.append(("F3", f"Says: {_shown(s['value'])}"))
        return out
    if s["kind"] == "document":
        # the file and its page; with no crop, the row says why (the reader gave no box)
        page = f"page {s['page']}" if s.get("page") else "page not recorded"
        where = ", ".join(x for x in (s.get("doc"), page) if x) + ("" if s.get("crop") else f" (no crop: {s.get('no_crop') or 'the reader gave no box'})")
        out = [("F2", f"{kind}: {s.get('title', '')}"), ("F3", where)]
        if s.get("value") not in (None, ""):
            out.append(("F3", f"Says: {_shown(s['value'])}"))
        if (s.get("crop") or {}).get("pdf_box"):
            places = (s["crop"].get("places") or 1)
            shown = len(s["crop"].get("marks") or [1])
            out.append(("F3", "Where the value stands on the page (found by searching the page for it, boxed):" if places == 1
                        else f"Where the value stands on the page (found by searching the page for it: {places} places match, "
                             + ("each boxed):" if shown == places else f"the {shown} close to the first are boxed):")))
        return out
    if s["kind"] == "rule":
        return [("F2", f"Rule: {s.get('title', '')} ({s.get('rule', '')})"), ("F3", s.get("text") or "")]
    if s["kind"] == "policy":
        code = (s.get("rule") or "").replace("POLICY:", "")
        return ([("F2", f"Firm policy: {s.get('title') or code} ({code})" if code else s.get("title") or kind), ("F3", s.get("text") or "")]
                + ([("F3", s["edited"] + ".")] if s.get("edited") else []))  # the attorney's edit in Settings (src/rules/firm_policies.py)
    title = s.get("title") or kind
    return [("F2", title if title.lower().replace("the ", "") in kind.lower() else f"{kind}: {title}"), ("F3", s.get("text") or "")]


def _draw_rows(doc: _Doc, data: dict[str, Any], images) -> None:
    size, lead = 7.5, 9.5

    def headings():
        for (x, _w), title in zip(_COL, ("Form and item", "Value as filled", "Where it came from")):
            doc.p.text(x, doc.y, title, "F2", 8)
        doc.y -= 6
        doc.p.line(MARGIN, doc.y, WIDTH - MARGIN, doc.y, 0.6)
        doc.y -= 11

    doc.need(80)
    headings()
    doc.on_new_page = headings
    for row in data["rows"]:
        left = [("F2", f"{row['form']}" + (f", {row['ref']}" if row.get("ref") else ""))] + [("F3", t) for t in _wrap(row["label"], size, _COL[0][1])][:6]
        if row.get("boxes", 1) > 1:
            left.append(("F3", f"(in {row['boxes']} boxes)"))
        if row.get("landed"):  # a Part 14 entry: the box (and copy of the form's page) it is in, and the line it came from
            left += [("F3", t) for t in _wrap(f"Lands in: {row['landed']}", size, _COL[0][1])][:4]
            left += [("F3", t) for t in _wrap(f"From: {row['line']}", size, _COL[0][1])][:3]
        mid = [("F2", t) for t in _wrap(row["value"], size, _COL[1][1])][:24 if row.get("landed") else 8]  # a Part 14 entry shows whole
        right: list[tuple[str, Any]] = []
        for s in row["sources"]:
            for font, text in _source_lines(s):
                right += [(font, t) for t in _wrap(text, size, _COL[2][1])]
            if s.get("crop"):
                pic = images(s["crop"]) if images is not None else None
                if pic is not None:
                    right.append(("IMG", pic))
                else:  # the page could not be read or drawn (the reason is in the log): said, never left out
                    right.append(("F3", "crop unavailable"))
            right.append(("GAP", 3))
        height = max(len(left) * lead, len(mid) * lead, sum(lead if f not in ("IMG", "GAP") else (v[3] + 6 if f == "IMG" else v) for f, v in right)) + 6
        doc.need(min(height, HEIGHT - 140))
        top = doc.y
        for col, lines in ((0, left), (1, mid)):
            y = top
            for font, text in lines:
                doc.p.text(_COL[col][0], y, text, font, size)
                y -= lead
        y = top
        for font, v in right:
            if font == "GAP":
                y -= v
                continue
            if y < 60:  # a long source list continues on the next page
                doc.new_page()
                y = doc.y
            if font == "IMG":
                jpeg, px, w, h = v
                doc.p.image(jpeg, px, _COL[2][0], y - h + 3, w, h)  # its top just under the line above
                doc.p.box(_COL[2][0] - 1, y - h + 2, w + 2, h + 2)
                y -= h + 6
            else:
                doc.p.text(_COL[2][0], y, v, font, size)
                y -= lead
        doc.y = min(top - height, y - 4)
        doc.p.line(MARGIN, doc.y + 6, WIDTH - MARGIN, doc.y + 6, 0.3)
        doc.y -= 4
    doc.on_new_page = None


def _draw_rules(doc: _Doc, data: dict[str, Any]) -> None:
    doc.heading("Rules and firm policies used in this packet")
    doc.line_text("Each rule and firm policy below filled at least one box above. An attorney approves a rule once for every case "
                  "(Keeping current, in the review app); each case's answers are still confirmed on its Attorney sign-off tab.", 8.5)
    if not data["rules"]:
        doc.line_text("None: every box above came from a document, the client, a reviewer or the firm's details.", 9)
        return
    for r in data["rules"]:
        doc.need(60)
        title = ("Rule: " if r["kind"] == "rule" else "Firm policy: ") + r["name"] + f" ({r['code']})"
        doc.line_text(f"{title}: {r['rows']} row{'s' if r['rows'] != 1 else ''}", 9.5, "F2", gap=1)
        doc.line_text(r["plain_text"] or "No plain text recorded.", 8.5, gap=1)
        if r.get("edited_text"):  # the attorney changed the firm's wording or answer in Settings: who, and the date
            doc.line_text(r["edited_text"] + ".", 8.5, "F2", gap=1)
        if r.get("source"):
            doc.line_text(f"Source: {r['source']}.", 8.5, gap=1)
        state = r["approval"].get("state")
        doc.line_text({"approved": r["approval_text"] + " for volume use.", "not_approved": "Not yet approved for volume use."}.get(state, r["approval_text"] + "."),
                      8.5, "F2", gap=8)


def _draw_g28(doc: _Doc, data: dict[str, Any]) -> None:
    """The G-28's card (src/g28.py): who confirmed it and when, the address and Part 4 as the case has them with the form's own words, each change
    with its reason and person, the attorney's approval of the office's choices."""
    card = data.get("g28")
    if not card:
        return
    doc.heading("The G-28 for this case: the choices and who made them")
    done = card.get("confirmed")
    doc.line_text((f"Confirmed by {done['by']}" + (f" ({done['role']})" if done.get("role") else "") + f" on {_us_date(done['at'])}." if done
                   else ("The choices changed after they were confirmed: not confirmed now." if card["state"] == "changed" else "Not confirmed.")), 9, "F2", gap=2)
    said = card["approval"]["text"] if card["approval"]["state"] != "not_approved" else "no attorney has approved them yet, so the client's own address and a blank Part 4 are what the product fills"
    doc.line_text(f"The attorney's approval of the offices' G-28 choices: {said}.", 8.5, gap=2)
    doc.line_text(f"The form's own note on the client's mailing address: {card['mail_note']}", 8.5, gap=2)
    m = card["mail"]
    doc.line_text(f"Mailing address on the G-28: {m['where']}." + ("" if not m["changed"] else f" Changed for this case by {m['changed'].get('by')} on "
                  f"{_us_date(m['changed'].get('at'))}: {m['changed'].get('reason')}"), 9, gap=2)
    for p in card["part4"]:
        doc.line_text(f"{p['item']}: {'marked' if p['marked'] else 'left blank'}. The form's words: {p['words']}"
                      + ("" if not p["changed"] else f" Changed for this case by {p['changed'].get('by')} on {_us_date(p['changed'].get('at'))}: {p['changed'].get('reason')}"),
                      8.5, gap=2)
    o = card["others"]
    if o["chosen"]:
        mine = "the office's address in care of the firm" if o["chosen"] == "office" else "the client's own address"
        doc.line_text(f"The I-485's and the I-765's mailing address: {mine}"
                      + (f". Chosen by {o['changed'].get('by')} on {_us_date(o['changed'].get('at'))}: {o['changed'].get('reason')}" if o["changed"] else "") + ".", 9, gap=2)
    for h in card["history"]:
        doc.line_text(f"{_us_date(h['at'])}: {h['what']}, by {h['by']}" + (f" (taken back by {h['undone']['by']})" if h.get("undone") else "") + ".", 8, gap=1)
    doc.y -= 8


_ENGLISH_HOW = {"original": "The client's own words, in English", "machine": "Machine translation (offline translator), not yet checked by a person",
                "edited": "Typed or corrected by a person (below)", "none": "No English yet"}


def _draw_declaration(doc: _Doc, data: dict[str, Any]) -> None:
    """The client's declaration (src/drafting.py): for each paragraph, the question it answers, where the answer came from, its
    date and language, how its English was made, every person's edit (old and new) and every grammar smoothing (before and after)."""
    dec = data.get("declaration")
    if not dec:
        return
    doc.heading("The client's declaration: where each paragraph came from")
    practice = dec["practice"]
    doc.line_text("Every paragraph is one of the client's own answers, word for word, in the order the questions ask them. "
                  f"Drafting practice: {practice['text'].lower() if practice['state'] == 'not_approved' else practice['text']}.", 8.5)
    final, signed = dec.get("final"), dec.get("signed")
    doc.line_text((f"Marked as the client's final by {final['who']} on {final['date']}." if final else
                   "Changed after it was marked final: not in the packet until marked again." if dec.get("changed_since_final") else "Not marked final.")
                  + (f" Signed by the client on {signed['date']} (recorded by {signed['who']})." if signed else " Not signed yet." if final else ""), 8.5, "F2", gap=6)
    for p in dec["paragraphs"]:
        doc.need(70)
        doc.line_text(f"Paragraph {p['n']}: {p['label']}" if p["included"] else f"Left out of the declaration by a person: {p['label']}", 9, "F2", gap=1)
        src = p["source"]
        doc.line_text(f"Question id: {p['id']}. {src['words']}" + (f" on {src['date']}" if src.get("date") else "") + "."
                      + f" Language: {p['language_name']} ({p['language_how']}).", 8, gap=1)
        doc.line_text(f"The answer: {p['original']}", 8, gap=1)
        how = p["english_how"]
        doc.line_text(f"English: {_ENGLISH_HOW.get(how, how)}.", 8, gap=1)
        if p["english"] != p["original"]:
            doc.line_text(f"In the declaration: {p['english']}", 8, gap=1)
        for e in p["edits"]:
            undone = f" Taken back by {e['undone']['who']} on {e['undone']['date']}." if e.get("undone") else ""
            doc.line_text(("Grammar suggestion accepted by " if e.get("suggestion") else "Edited by ") + f"{e['who']}"
                          + (f" ({e['role']})" if e.get("role") else "") + f" on {e['date']}.{undone}", 8, "F2", gap=1)
            doc.line_text(f"Old: {e.get('old') or '(empty)'}", 8, gap=1)
            doc.line_text(f"New: {e.get('new') or '(empty)'}", 8, gap=1)
        s = p.get("smoothing")
        if s:
            declined = s.get("declined")
            doc.line_text(f"Grammar suggestion by {s.get('engine') or 'the local model'}, asked by {(s.get('by') or {}).get('who') or 'someone'}: "
                          + (f"refused by the check ({s.get('reason')})." if not s.get("accepted")
                             else f"declined by {declined['who']}." if declined else "offered to a person; used only if accepted (above)."), 8, "F2", gap=1)
            doc.line_text(f"Before: {s.get('before')}", 8, gap=1)
            doc.line_text(f"Suggested: {s.get('after') or '(nothing)'}", 8, gap=1)
        doc.y -= 6


def _draw_explanations(doc: _Doc, data: dict[str, Any]) -> None:
    """The Part 14 explanations (src/part14_explain.py): for each Yes the form says to explain, the lines it rests on, the wording it was
    suggested from, every slot's value and where it came from, the citation (or that the attorney adds it), every edit and the approval."""
    ex = data.get("explanations")
    if not ex:
        return
    doc.heading("Part 14 explanations: where each came from")
    doc.line_text("Each Yes in Part 9 the form says to explain, with its explanation on the form's own Part 14 page. A suggestion is only the firm's "
                  "DRAFT wording with each blank filled from a fact of the case; no model writes a word. " + ex["voice"]["note"], 8.5, gap=6)
    for e in ex["entries"]:
        doc.need(80)
        doc.line_text(f"Part 9, item {e['item']} (page {e['page']}): {e['question']}", 9, "F2", gap=1)
        for line in e["lines"]:
            doc.line_text(f"{line['where']}: {line['says']}", 7.5, gap=1)
        s = e["suggestion"]
        doc.line_text("Suggested from the firm's DRAFT wording (" + s["when"].rstrip(".").lower() + ")." if s else
                      "No wording ships for this item: a person wrote the explanation.", 8, gap=1)
        for slot in (s or {}).get("slots") or []:
            shown = slot["value"] or (f"{slot['proposed']} (the client's own answer: used only where a person put it in the text)" if slot["proposed"] else "blank")
            where_from = "; ".join(x["words"] + (f" ({x['doc']}" + (f", page {x['page'] + 1}" if x.get("page") is not None else "") + ")" if x.get("doc") else "")
                                   for x in slot["sources"]) or slot["why"]
            doc.line_text(f"{slot['words']}: {shown}. From: {where_from}", 8, gap=1)
        cite = (s or {}).get("cite")
        if cite:
            doc.line_text(f"Citation: {cite['cite']}, from the rule {cite['rule']} ({cite['source']}, read {_us_date(cite['read'])})." if cite["held"] else
                          f"Citation: none in the text ({cite['needed']}): {cite['note']}.", 8, gap=1)
        doc.line_text(f"The explanation: {e['text'] or '(none written)'}", 8, gap=1)
        for ed in e["edits"]:
            undone = f" Taken back by {ed['undone']['who']} on {ed['undone']['date']}." if ed.get("undone") else ""
            doc.line_text(("Grammar suggestion accepted by " if ed.get("suggestion") else "Edited by ") + f"{ed['who']}" + (f" ({ed['role']})" if ed.get("role") else "")
                          + f" on {ed['date']}.{undone}", 8, "F2", gap=1)
            doc.line_text(f"Old: {ed.get('old') or '(empty)'}", 8, gap=1)
        a = e["approval"]
        doc.line_text((f"Approved by {a['who']} on {a['date']}." + ("" if a["holds"] else f" No longer holds: {a['why_not']}")) if a else "Not approved.", 8.5, "F2", gap=8)


def build(client_dir: Path, filing: str | None, who: str, page_images=None, source_path=None) -> dict[str, Any]:
    """Writes the review bundle next to the packet; returns its record (without the rows).
    page_images(doc) -> the page images the review cards use (server.py page_image); source_path(doc) -> the file itself
    (server.py source_file). Without them, the case's own source folder is read here."""
    who = (who or "").strip()
    if not who:
        raise ValueError("Enter your name first: the review bundle records who built it.")
    client_dir = Path(client_dir)
    scans = _Scans(client_dir, page_images, source_path)
    data = rows(client_dir, filing, scans)

    def picture(c):
        images = scans.images(c["doc"])
        if not images or c["page"] >= len(images):
            log.warning("review bundle: %s has no page %s to crop", c["doc"], c["page"] + 1)
            return None
        try:
            if c.get("box"):  # the reader recorded the box: the scan as the review cards show it, cut between lines, the answer boxed
                return _jpeg(crop_whole_lines(images[c["page"]], c["box"]), _COL[2][1] - 4, 66)
            return _jpeg(found_crop(images[c["page"]], c), _COL[2][1] - 4, 66 if len(c.get("marks") or []) < 2 else 90)  # found in the text: boxed
        except Exception as exc:  # noqa: BLE001 -- logged; the row says "crop unavailable"
            log.warning("review bundle: could not crop %s page %s: %s", c["doc"], c["page"] + 1, exc)
            return None

    summary = data["summary"]
    a = re.sub(r"\D", "", summary.get("a_number") or "")
    now = clock.now()  # the bundle's date is the office's (src/clock.py), not the server's UTC
    header = f"Review bundle: {summary.get('name') or data['client_id']}" + (f", A-{a}" if a else "") + f" · {data['title']}"
    doc = _Doc(data["firm"], header, f"Built {_us_date(now.isoformat())} by {who} from the review record of this packet. Never mailed.")
    doc.heading(f"Review bundle: {data['title']}", 14)
    pk = data["packet"]
    for line in (f"Client: {summary.get('name') or data['client_id']}" + (f" (A-{a})" if a else ""),
                 f"Packet built {_us_date(pk.get('built_at'))} by {pk.get('built_by') or 'someone'}, {pk.get('pages') or '?'} pages"
                 + (", marked DRAFT" if pk.get("draft") else "") + f". Packet fingerprint: {str(pk.get('sha256') or '')[:16]}",
                 f"Forms: {', '.join(data['forms'])}",
                 f"This bundle: built {_us_date(now.isoformat())} by {who}."):
        doc.line_text(line, 9, gap=1)
    doc.y -= 6
    kinds: dict[str, int] = {}
    for r in data["rows"]:
        for s in r["sources"]:
            if s["kind"] != "used":  # a note on the sources, not a source
                kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    doc.line_text(f"{len(data['rows'])} answers filled on the forms, each with where it came from: a document (with the part of the scan "
                  "it stands on, when it can be found), the client's own answer in the portal, a rule, a reviewer's decision, the firm's policy or the "
                  "firm's details. Rows follow the form's own order. Social Security numbers and other private numbers show their last four digits only. " + ", ".join(f"{n} from {_KIND_WORDS.get(k, k).lower()}" for k, n in sorted(kinds.items())) + ".", 9)
    doc.line_text("Built only from what the packet and the review recorded; nothing was read or worked out again. "
                  "An internal record for the firm: never filed, never mailed.", 8.5, gap=8)
    _draw_rows(doc, data, picture)
    _draw_declaration(doc, data)
    _draw_explanations(doc, data)
    _draw_g28(doc, data)
    _draw_rules(doc, data)
    pdf = doc.finish()
    pdf_path, record_path = paths(client_dir, filing)
    pdf_path.write_bytes(pdf)
    record = {"built_at": now.isoformat(), "built_by": who, "filing": data["filing"], "pages": len(doc.pages), "sha256": hashlib.sha256(pdf).hexdigest(),
              "rows": len(data["rows"]), "packet_built_at": pk.get("built_at"), "packet_sha256": pk.get("sha256"),
              "rules": [{"id": r["id"], "approval": r["approval"]} for r in data["rules"]]}
    record_path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    events.record("packet", "built", f"Built the review bundle for {_packet_title(client_dir, filing)}", case_dir=client_dir, who=who)
    return record


def info(client_dir: Path, filing: str | None) -> dict[str, Any] | None:
    """The last bundle built for this filing's packet, and whether the packet was rebuilt since."""
    pdf, record_path = paths(Path(client_dir), filing)
    record = _read(record_path, None)
    if record is None or not pdf.exists():
        return None
    _schema, manifest = _packet(Path(client_dir), filing)
    return record | {"stale": bool(manifest and manifest.get("sha256") != record.get("packet_sha256"))}


# -- the accuracy record (review/learning.py accuracy) ---------------------------------------


def accuracy_pdf(report: dict[str, Any], firm: str, who: str = "") -> bytes:
    """The accuracy page as a PDF, with the method written on it (for a sales conversation or the firm's file)."""
    period = f"{_us_date(report['from']) or 'the first decision'} to {_us_date(report['to']) or 'today'}"
    doc = _Doc(firm, f"Accuracy record: {period}", f"Built {_us_date(clock.stamp())}" + (f" by {who}" if who else "")
               + " from the review decisions on file. No estimate: a number the records don't hold says no data.")
    doc.heading(f"Accuracy record: {period}", 14)
    t = report["totals"]
    doc.line_text(f"{report['clients']} clients, {t['reviewed']} reviewed values: {t['confirmed']} confirmed, {t['corrected']} corrected, "
                  f"{t['blanked']} left blank, {t['filled']} corrected where the box was empty." if t["reviewed"] else "No data: no reviewed values in this period.", 9.5)
    doc.heading("How these numbers are counted", 11)
    for line in report["method"]:
        doc.line_text("- " + line, 8.5, gap=1)
    for title, group, first in (("By where the value came from", report["by_source"], "Source"), ("By form item", report["by_item"], "Form item")):
        doc.heading(title, 11)
        cols = [(MARGIN, 250), (MARGIN + 260, 60), (MARGIN + 325, 60), (MARGIN + 390, 60), (MARGIN + 455, 50)]

        def heads(cols=cols, first=first):
            for (x, _w), h in zip(cols, (first, "Confirmed", "Corrected", "Left blank", "Corrected, empty")):
                doc.p.text(x, doc.y, h, "F2", 8)
            doc.y -= 12

        doc.need(30)
        heads()
        doc.on_new_page = heads
        if not group:
            doc.line_text("No data.", 9)
        for g in group:
            lines = _wrap(g["label"] + (f" ({g['ref']})" if g.get("ref") else ""), 8, cols[0][1])[:3]
            doc.need(10 * len(lines) + 4)
            for i, line in enumerate(lines):
                doc.p.text(cols[0][0], doc.y - 10 * i, line, "F3", 8)
            for (x, _w), k in zip(cols[1:], ("confirmed", "corrected", "blanked", "filled")):
                doc.p.text(x, doc.y, str(g[k]), "F3", 8)
            doc.y -= 10 * len(lines) + 3
        doc.on_new_page = None
    doc.heading("Comparisons with the firm's earlier filings", 11)
    doc.line_text(report["reference"], 8.5)
    return doc.finish()
