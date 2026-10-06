"""Read-only retained evidence snapshots; this module grants no approval.

Authorization belongs to the case route. Original pages are zero based here;
the UI alone converts a validated location for display. No preview tempfiles.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import math
import re
import stat
from dataclasses import dataclass
from pathlib import Path


class Unavailable(LookupError):
    """A safe public explanation, without a filesystem path."""


def safe(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
        for part in (path, *path.parents):
            info = part.lstat()
            if part.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
                return False
        return path.is_file()
    except (OSError, ValueError):
        return False


def _json(path: Path, *, optional=False) -> dict:
    if optional and not path.exists():
        return {}
    if not safe(path, path.parent):
        raise Unavailable("The source record is unavailable. Locate and read the original again.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise Unavailable("The source record is unavailable. Locate and read the original again.") from None


def _basename(name) -> bool:
    return isinstance(name, str) and bool(name) and name not in {".", ".."} and not any(
        ch in name for ch in "/\\:"
    ) and not any(ord(ch) < 32 or ord(ch) == 127 for ch in name)


def _number(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True)
class Snapshot:
    path: Path
    data: bytes
    sha256: str
    plan: dict | None
    part: dict | None
    alias: str

    @property
    def content_type(self):
        return mimetypes.guess_type(self.path.name)[0] or "application/octet-stream"

    def location(self, page=None, instance_id=None) -> dict:
        plan, part = self.plan or {}, self.part or {}
        bound = bool(plan.get("source_sha256"))
        first, last = part.get("first"), part.get("last")
        expected_instance = part.get("instance_id")
        instance_matches = not instance_id or instance_id == expected_instance
        # A bare original name may cover several instances. A requested instance
        # must be one recorded in this exact original, never another source's.
        if instance_id and not part:
            matches = [p for p in plan.get("instances", []) if p.get("instance_id") == instance_id]
            if len(matches) == 1:
                part = matches[0]; first, last = part.get("first"), part.get("last")
                expected_instance = instance_id; instance_matches = True
            else:
                instance_matches = False
        elif not part and len(plan.get("instances", [])) == 1:
            # A single recorded instance of this exact retained original can
            # identify its sole physical page, without inventing a fact page.
            part = plan["instances"][0]
            first, last = part.get("first"), part.get("last")
            expected_instance = part.get("instance_id")
        sole_page = page is None and bound and instance_matches and _number(first) and first == last
        if sole_page:
            page = first
        count = plan.get("page_count")
        known = bound and instance_matches and _number(page) and _number(count) and page < count
        if part:
            known = known and _number(first) and _number(last) and first <= page <= last
        return {"state": "current" if bound and instance_matches else "unverified",
                "source_sha256": self.sha256 if bound else None,
                "instance_id": expected_instance if instance_matches else None,
                "original_range": [first, last] if bound and _number(first) and _number(last) else None,
                "page": page if known else None,
                "page_basis": ("single_page_instance_original_zero_based" if sole_page else "original_zero_based") if known else "unavailable",
                "region": None, "coordinate_space": None,
                "boundary_state": part.get("state"),
                "reason": ("The retained document instance contains one original page; no fact-specific region was recorded." if known and sole_page else
                           "No reliable region was recorded; open the original page." if known else
                           "Original page location unavailable; open the document." if bound and instance_matches else
                           "Unverified source identity/location. Read the original again before confirming a fact.")}


def snapshot(case_dir: Path, doc: str, expected_sha256: str | None = None) -> Snapshot:
    """Bind exact registered alias to retained bytes, refusing stale bound reads."""
    if not _basename(doc):
        raise Unavailable("Unknown document.")
    if expected_sha256 is not None and (not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)):
        raise Unavailable("The requested source identity is unavailable. Refresh the fact before opening its evidence.")
    meta = _json(case_dir / "meta.json")
    records = _json(case_dir / "documents.json", optional=True)
    plans = records.get("boundary_plans", {})
    rows = records.get("documents", [])
    if not isinstance(plans, dict) or not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise Unavailable("The source record is unavailable. Read the original again.")
    originals = {}
    for name, plan in plans.items():
        if not _basename(name) or not isinstance(plan, dict) or not isinstance(plan.get("instances"), list):
            raise Unavailable("The source record is unavailable. Read the original again.")
        count = plan.get("page_count")
        if not _number(count) or (count and not plan["instances"]) or not all(isinstance(p, dict) and _number(p.get("first")) and _number(p.get("last"))
                                         and p["first"] <= p["last"] < count and isinstance(p.get("doc_ids"), list)
                                         for p in plan["instances"]):
            raise Unavailable("The source page record is unavailable. Read the original again.")
        if name in originals:
            raise Unavailable("The source association is ambiguous. Read the original again.")
        originals[name] = (name, plan, None)
        for part in plan["instances"]:
            for alias in part["doc_ids"]:
                if not _basename(alias):
                    raise Unavailable("The source record is unavailable. Read the original again.")
                if alias != name:
                    if alias in originals:
                        raise Unavailable("The source association is ambiguous. Read the original again.")
                    originals[alias] = (name, plan, part)
    # Registered legacy originals remain readable, without retroactive proof.
    for row in rows:
        for field in ("files", "doc_ids"):
            values = row.get(field) or []
            if not isinstance(values, list):
                raise Unavailable("The source record is unavailable. Read the original again.")
            for alias in values:
                if not _basename(alias):
                    raise Unavailable("The source record is unavailable. Read the original again.")
                name = alias.split("#p", 1)[0]
                originals.setdefault(name, (name, None, None))
                originals.setdefault(alias, (name, None, None))
    graph = _json(case_dir / "fact_graph.json", optional=True)
    facts = graph.get("facts", {})
    if not isinstance(facts, dict):
        raise Unavailable("The source record is unavailable. Read the original again.")
    for fact in facts.values():
        if not isinstance(fact, dict) or not isinstance(fact.get("sources", []), list):
            raise Unavailable("The source record is unavailable. Read the original again.")
        for source in fact.get("sources", []):
            if not isinstance(source, dict):
                raise Unavailable("The source record is unavailable. Read the original again.")
            alias = source.get("doc_id")
            if _basename(alias):
                name = alias.split("#p", 1)[0]
                originals.setdefault(alias, (name, None, None))
                originals.setdefault(name, (name, None, None))
    if doc not in originals:
        raise Unavailable("Unknown document.")
    name, plan, part = originals[doc]
    folder = meta.get("source_folder")
    if not isinstance(folder, str) or not folder:
        raise Unavailable("The original is unavailable. Locate and read it again.")
    path, root = Path(folder) / name, Path(folder)
    if path.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"} or not safe(path, root):
        raise Unavailable("The original is unavailable. Locate and read it again.")
    try:
        data = path.read_bytes()
    except OSError:
        raise Unavailable("The original is unavailable. Locate and read it again.") from None
    if not safe(path, root):
        raise Unavailable("The original is unavailable. Locate and read it again.")
    sha = hashlib.sha256(data).hexdigest()
    expected = (plan or {}).get("source_sha256")
    if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)):
        raise Unavailable("The retained source identity is unavailable. Read the original again.")
    if expected and expected != sha:
        raise Unavailable("The original changed since it was read. Read it again before using its fact preview.")
    if expected_sha256 is not None and (not expected or expected_sha256 != sha):
        raise Unavailable("The source changed since this fact was displayed. Refresh the fact before opening its evidence.")
    return Snapshot(path, data, sha, plan, part, doc)


def validate_region(box: str, width: int, height: int) -> None:
    """Known page-image pixel coordinates must fit that actual rendered page."""
    try:
        coords = [float(n) for n in box.split(",")]
    except (ValueError, AttributeError):
        raise Unavailable("The recorded region is unavailable. Open the whole original page.") from None
    if len(coords) != 4 or not all(math.isfinite(n) for n in coords) or not (
        0 <= coords[0] < coords[2] <= width and 0 <= coords[1] < coords[3] <= height
    ):
        raise Unavailable("The recorded region is unavailable. Open the whole original page.")


def _request_reader(case_dir):
    originals = {}
    def get_source(doc):
        # Reuse only aliases recorded in the same validated original plan.
        # A legacy/unknown alias still goes through a fresh registration lookup.
        for name, old in originals.items():
            parts = [p for p in (old.plan or {}).get("instances", []) if doc in p.get("doc_ids", [])]
            if doc == name or len(parts) == 1:
                return Snapshot(old.path, old.data, old.sha256, old.plan,
                                None if doc == name else parts[0], doc)
        source = snapshot(case_dir, doc)
        originals[source.path.name] = source
        return source
    return get_source


def _missing(reason):
    return {"state": "unavailable", "source_sha256": None,
            "instance_id": None, "original_range": None, "page": None,
            "page_basis": "unavailable", "region": None, "coordinate_space": None, "reason": str(reason)}


def document_sources(case_dir: Path, rows: list[dict]) -> None:
    """Compact catalog previews carry actual original ranges, never guessed pages."""
    get_source = _request_reader(case_dir)
    for row in rows:
        sources = []
        aliases = row.get("doc_ids") or row.get("files") or []
        if not isinstance(aliases, list) or not all(_basename(alias) for alias in aliases):
            raise Unavailable("The document source record is unavailable. Read the original again.")
        for alias in dict.fromkeys(aliases):
            try:
                source = get_source(alias)
                part = source.part
                if part is None and len((source.plan or {}).get("instances", [])) == 1:
                    part = source.plan["instances"][0]
                page = part.get("first") if part else None
                location = source.location(page, part.get("instance_id") if part else None)
            except Unavailable as error:
                location = _missing(error)
            sources.append({"doc": alias, "source_location": location})
        row["source_locations"] = sources


def annotate(case_dir: Path, payload: dict, region_reader=None, *, preserve_identity=False) -> None:
    """Add current read-only location metadata without changing proof fields.

    One original snapshot per requested alias per response. Cached server page
    requests still recheck bytes independently when the reviewer opens a source.
    """
    snapshots, get_source = {}, _request_reader(case_dir)

    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            # Source views, questionnaire evidence and name timelines already
            # carry doc/page. Never infer a page from the filename or raw text.
            doc = value.get("doc")
            if isinstance(doc, str) and ("page" in value or "instance_id" in value):
                if doc not in snapshots:
                    try:
                        snapshots[doc] = get_source(doc)
                    except Unavailable as error:
                        snapshots[doc] = str(error)
                source = snapshots[doc]
                if isinstance(source, Snapshot):
                    prior = value.get("source_location") if preserve_identity else None
                    location = source.location(value.get("page"), value.get("instance_id"))
                    if isinstance(prior, dict):
                        if prior.get("state") != "current":
                            # Optional enrichment cannot upgrade an unavailable
                            # or unverified association from the built case view.
                            location = prior
                        elif prior.get("source_sha256") != source.sha256:
                            location = _missing("The source changed since this fact was displayed. Refresh the fact before opening its evidence.")
                        else:
                            location = source.location(prior.get("page"), prior.get("instance_id"))
                            if location["state"] != "current" or location["instance_id"] != prior.get("instance_id"):
                                location = _missing("The source page association changed. Refresh the fact before opening its evidence.")
                    box = value.get("box")
                    if _number(value.get("page")) and value["page"] == location["page"] and isinstance(box, (list, tuple)) and len(box) == 4 and all(
                        isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in box
                    ) and 0 <= box[0] < box[2] and 0 <= box[1] < box[3] and "question" in value:
                        # Questionnaire evidence boxes are explicitly recorded
                        # in the reader's page-image pixel coordinate space.
                        location.update(region=list(box), coordinate_space="page_image_pixels",
                                        reason="Recorded questionnaire region on the original page.")
                    if location["state"] == "current" and location["region"] is None and region_reader is not None and isinstance(value.get("raw"), str):
                        # An independent local pixel lookup is a display aid, never
                        # evidence approval or a replacement for the retained quote.
                        try:
                            region = region_reader(source, location, value["raw"])
                        except Unavailable as error:
                            # Source replacement between snapshot and optional
                            # lookup must refuse the preview, not the case view.
                            location = _missing(error)
                            region = None
                        if region is not None:
                            location.update(region=region, coordinate_space="page_image_pixels",
                                            region_basis="unique_quote_on_original_pixels",
                                            reason="Quoted text uniquely located on the verified original page; field approval is unchanged.")
                    value["source_location"] = location
                else:
                    value["source_location"] = _missing(source)
            # Snapshot metadata is terminal and proof/fingerprint data stays
            # byte-for-byte unchanged; it is not a source association input.
            for key, child in list(value.items()):
                if key not in {"source_location", "source_review", "evidence_confirmation", "evidence_fingerprints"}:
                    visit(child)

    visit(payload)


def revalidate_locations(case_dir: Path, payload: dict) -> None:
    """Recheck display sources immediately before release, without OCR.

    Keep an exact region only while its bytes and original instance/page remain
    current. This never changes fact fingerprints or confirmation state.
    """
    checked = {}

    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            location, doc = value.get("source_location"), value.get("doc")
            if isinstance(location, dict) and location.get("state") == "current" and isinstance(doc, str):
                key = (doc, location.get("source_sha256"))
                if key not in checked:
                    try:
                        checked[key] = snapshot(case_dir, doc, location.get("source_sha256"))
                    except Unavailable as error:
                        checked[key] = str(error)
                source = checked[key]
                if isinstance(source, Snapshot):
                    fresh = source.location(location.get("page"), location.get("instance_id"))
                    if fresh["state"] != "current" or fresh["page"] != location.get("page"):
                        value["source_location"] = _missing("The source page association changed. Refresh the fact before opening its evidence.")
                else:
                    value["source_location"] = _missing(source)
            for key, child in list(value.items()):
                if key not in {"source_location", "source_review", "evidence_confirmation", "evidence_fingerprints"}:
                    visit(child)

    visit(payload)


def render_source_page_bounded(data: bytes, page: int, timeout: float, *, image_only=False):
    """Isolate optional PDF decoding/rendering so the request can enforce its deadline."""
    import os
    import subprocess
    import sys
    from io import BytesIO
    from PIL import Image
    script = ("import sys; from review.evidence import render_source_page, render_source_image; "
              "image=(render_source_image(sys.stdin.buffer.read()) if sys.argv[2]=='image' else render_source_page(sys.stdin.buffer.read(),int(sys.argv[1]))); "
              "image.save(sys.stdout.buffer,format='PNG')")
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, "-c", script, str(page), "image" if image_only else "pdf"], input=data,
                            capture_output=True, check=True, timeout=max(.01, timeout), env=environment)
    with Image.open(BytesIO(result.stdout)) as image:
        if image.width * image.height > 24_000_000:
            raise ValueError("The optional page preview exceeds its pixel budget.")
        return image.copy()


def render_source_image(data):
    from io import BytesIO
    from PIL import Image, ImageOps
    with Image.open(BytesIO(data)) as image:
        if image.width * image.height > 24_000_000:
            raise ValueError("The optional page preview exceeds its pixel budget.")
        return ImageOps.grayscale(image.copy())


def render_source_page(data: bytes, page: int):
    """Render one original page, in the same coordinates as questionnaire pages.

    The caller has already authenticated these immutable source bytes and page.
    Scan extraction and PDFium scale deliberately match the existing renderer.
    """
    from io import BytesIO
    from PIL import ImageOps
    from pypdf import PdfReader
    from questionnaire.pages import _scan_image, PDFIUM_LOCK, RENDER_WIDTH
    reader = PdfReader(BytesIO(data))
    if not _number(page) or page >= len(reader.pages):
        raise Unavailable("Original page location unavailable. Read the original again.")
    scan = _scan_image(reader.pages[page])
    if scan is not None:
        if scan.width * scan.height > 24_000_000:
            raise ValueError("The optional page preview exceeds its pixel budget.")
        return ImageOps.grayscale(scan)
    import pypdfium2 as pdfium
    with PDFIUM_LOCK:
        document = pdfium.PdfDocument(data)
        try:
            pdf_page = document[page]
            try:
                if pdf_page.get_width() <= 0 or pdf_page.get_height() / pdf_page.get_width() * RENDER_WIDTH ** 2 > 24_000_000:
                    raise ValueError("The optional page preview exceeds its pixel budget.")
                bitmap = pdf_page.render(scale=RENDER_WIDTH / pdf_page.get_width(), may_draw_forms=True, draw_annots=True)
                try:
                    return bitmap.to_pil().convert("L")
                finally:
                    bitmap.close()
            finally:
                pdf_page.close()
        finally:
            document.close()


def quote_line_region(words, quote: str, width: int, height: int):
    """Locate one literal quote on original-pixel OCR lines; ambiguity has no box.

    No fuzzy spelling, translation, normalized-value or guessed-page matching.
    Every word in the matched quote must be confidently read and in bounds.
    """
    if not isinstance(quote, str) or not 3 <= len(quote.strip()) <= 256:
        return None
    tokens = lambda value: re.findall(r"\w+", value.casefold())
    wanted = tokens(quote)
    if not wanted:
        return None
    lines = {}
    for word in words:
        if not (0 <= word.left < word.right <= width and 0 <= word.top < word.bottom <= height):
            return None
        lines.setdefault(word.line_id, []).append(word)
    text, owners = [], []
    for line_id in sorted(lines):
        line = lines[line_id]
        line.sort(key=lambda word: word.left)
        for word in line:
            parts = tokens(word.text)
            text.extend(parts); owners.extend([word] * len(parts))
    # Check the full reading order, including adjacent-line wraps. A second
    # wrapped occurrence makes a single-line occurrence ambiguous too.
    found = []
    for start in range(len(text) - len(wanted) + 1):
        if text[start:start + len(wanted)] == wanted:
            matched = owners[start:start + len(wanted)]
            if any(not math.isfinite(word.conf) or word.conf < 70 for word in matched):
                return None
            # Cross-line occurrences count against uniqueness, but without
            # geometric evidence they cannot supply an exact crop themselves.
            if len({word.line_id for word in matched}) != 1:
                found.append(None)
                continue
            region_words = lines[matched[0].line_id]
            found.append([min(word.left for word in region_words), min(word.top for word in region_words),
                          max(word.right for word in region_words), max(word.bottom for word in region_words)])
    return found[0] if len(found) == 1 else None
