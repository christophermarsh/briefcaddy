"""Document-processing helper."""

from __future__ import annotations

import re
import hashlib
import io
from contextlib import contextmanager
from contextvars import ContextVar

from dataclasses import dataclass, field
from pathlib import Path

from .patterns import CONFLICT_MARGIN, MIN_CONFIDENCE, NOTICE_CASE_TYPE_MAP, PATTERNS


@dataclass
class Classification:
    doc_type: str
    confidence: float
    matched_signals: list[str] = field(default_factory=list)
    text_extracted: bool = True
    ambiguous_with: list[str] = field(default_factory=list)


# Supporting implementation.
# Supporting implementation.
_FORM_FOOTER = re.compile(r"Form\s+([IG1])\s*-?\s*(\d{2,3}[A-Z]?)(?:\s+Supp(?:lement)?\.?\s+([A-Z]))?\s+Edition\s+\d", re.I)
_FORM_TYPES = {
    "I-485": "i485", "I-765": "i765", "G-28": "g28", "I-360": "i360_petition", "I-130": "i130", "I-589": "i589",
    "I-131": "i131", "I-912": "i912", "I-864": "i864", "I-693": "i693", "I-601": "i601", "I-212": "i212",
    "I-290B": "i290b", "I-730": "i730", "I-914": "i914", "I-918": "i918", "I-539": "i539",
    # Supporting implementation.
    "I-914A": "i914a", "I-914B": "i914b", "I-918A": "i918a", "I-918B": "i918b", "I-929": "i929", "I-192": "i192",
}
_BROWSER_PRINTOUT = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{2},\s*\d{1,2}:\d{2}\s*[AP]M", re.M)


def classify_text(text: str) -> Classification:
    """Document-processing helper."""
    if not text or not text.strip():
        return Classification(doc_type="unclassified", confidence=0.0, text_extracted=False)

    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    footer = _FORM_FOOTER.search(text)
    if footer:
        form = (footer.group(1).upper().replace("1", "I") + "-" + footer.group(2).upper() + (footer.group(3) or "").upper()).replace("II", "I")
        doc_type = _FORM_TYPES.get(form)
        if doc_type:
            return Classification(doc_type=doc_type, confidence=0.95, matched_signals=[footer.group(0)])
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    history_table = (all(re.search(r"\b" + column + r"\b", text, re.I)
                         for column in ("Date", "Type", "Location"))
                     or (re.search(r"\bArrivals?\b", text, re.I)
                         and re.search(r"\bDepartures?\b", text, re.I)))
    if re.search(r"\bTravel\s+History\s+Results\b", text, re.I) and history_table:
        return Classification(doc_type="travel_history", confidence=0.95,
                              matched_signals=["Travel History Results", "travel history table columns"])
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    if _BROWSER_PRINTOUT.search(text[:200]) and not re.search(r"I-?94/I-?95 Official Website|Most Recent I-94", text[:400]):
        return Classification(doc_type="web_printout", confidence=0.9, matched_signals=["browser print header"])

    scores: dict[str, tuple[float, list[str]]] = {}
    for doc_type, signals in PATTERNS.items():
        total = 0.0
        matched = []
        for pattern, weight in signals:
            if pattern.search(text):
                total += weight
                matched.append(pattern.pattern)
        if total > 0:
            scores[doc_type] = (total, matched)

    if not scores:
        return Classification(doc_type="unclassified", confidence=0.0)

    ranked = sorted(scores.items(), key=lambda kv: kv[1][0], reverse=True)
    top_type, (top_score, top_matched) = ranked[0]

    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    confidence = min(top_score, 1.0)

    ambiguous_with = [dt for dt, (score, _) in ranked[1:] if top_score - score <= CONFLICT_MARGIN]

    if confidence < MIN_CONFIDENCE:
        return Classification(
            doc_type="unclassified", confidence=confidence, matched_signals=top_matched,
            ambiguous_with=[top_type] + ambiguous_with,
        )

    if top_type == "uscis_notice":
        top_type = _subclassify_notice(text)

    return Classification(
        doc_type=top_type, confidence=confidence, matched_signals=top_matched, ambiguous_with=ambiguous_with,
    )


def _subclassify_notice(text: str) -> str:
    """Document-processing helper."""
    from extract.uscis_notice import parse

    form = parse(text).form  # Supporting implementation.
    if form:
        own = {"I-360": "i360_approval", "I-765": "i765_approval", "I-130": "i130_approval", "I-485": "i485_receipt",
               "I-526": "i526_approval", "I-590": "i590_approval", "I-730": "i730_approval"}.get(form)
        return own or "uscis_notice"
    for pattern, specific_type in NOTICE_CASE_TYPE_MAP.items():
        if pattern.search(text):
            return specific_type
    return "uscis_notice"


# Supporting implementation.
# Supporting implementation.
# Confirmed necessary against the real driver's license in the example
# Supporting implementation.
# Supporting implementation.
_OCR_FALLBACK_THRESHOLD = 400

_PAGE_CACHE = ContextVar("document_page_cache", default=None)


@contextmanager
def page_cache():
    """Document-processing helper."""
    existing = _PAGE_CACHE.get()
    if existing is not None:
        yield existing  # Supporting implementation.
        return
    cache = {}
    token = _PAGE_CACHE.set(cache)
    try:
        yield cache
    finally:
        _PAGE_CACHE.reset(token)


def cached_pdf_pages(data: bytes) -> list[str]:
    cache = _PAGE_CACHE.get()
    key = hashlib.sha256(data).hexdigest()
    if cache is not None and key in cache:
        value = cache[key]
        if isinstance(value, Exception):
            raise value
        return list(value)
    try:
        pages = _extract_pdf_pages(data)
    except Exception as exc:
        if cache is not None:
            cache[key] = exc
        raise
    if cache is not None:
        cache[key] = tuple(pages)
    return pages


def extract_pages(path: str | Path) -> list[str]:
    """Document-processing helper."""
    return cached_pdf_pages(Path(path).read_bytes())


def _extract_pdf_pages(data: bytes) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        # Supporting implementation.
        # Supporting implementation.
        unreadable_layer = len(page_text.strip()) >= _OCR_FALLBACK_THRESHOLD and readability(page_text) < 3 and _page_images(page)
        parts = [page_text]
        if len(page_text.strip()) < _OCR_FALLBACK_THRESHOLD or unreadable_layer:
            parts.append(_ocr_page_images(page))
            parts.append(_barcode_page_images(page))
        pages.append("\n".join(parts))
    return pages


def split_documents(pages: list[str]) -> list[tuple[int, int, str]]:
    """Document-processing helper."""
    from document_instances import segments

    return segments(pages)


def extract_text(path: str | Path) -> str:
    """Document-processing helper."""
    parts = extract_pages(path)
    combined = "\n".join(parts)
    if combined.strip() and classify_text(combined).doc_type == "unclassified":
        translated = _try_translate(combined)
        if translated:
            parts.append(translated)

    return "\n".join(parts)


# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
_TRANSLATION_CANDIDATE_LANGUAGES = ("pt", "es", "fr")


def _try_translate(text: str) -> str | None:
    """Document-processing helper."""
    from .translate import translate_text

    for language in _TRANSLATION_CANDIDATE_LANGUAGES:
        translated = translate_text(text, language, "en")
        if translated and classify_text(translated).doc_type != "unclassified":
            return translated
    return None


def _page_images(page) -> list:
    try:
        return list(page.images)
    except Exception:  # noqa: BLE001 -- e.g. Pillow not installed; image extraction is optional
        return []


# Supporting implementation.
# Supporting implementation.
_READABLE_WORDS = {
    "THE", "OF", "AND", "NAME", "DATE", "BIRTH", "PLACE", "PASSPORT", "NATIONALITY", "SEX", "UNITED", "STATES", "OF",
    "DE", "DA", "DO", "DOS", "NOME", "DATA", "NASCIMENTO", "PASSAPORTE", "NACIONALIDADE", "SEXO", "REPUBLICA",
    "FEDERATIVA", "BRASIL", "BRAZIL", "REGISTRO", "CIVIL", "CERTIDAO", "FILIACAO", "ESTADO", "EXPEDICAO", "VALIDADE",
    "NUMERO", "TIPO", "PAIS", "APELLIDOS", "NOMBRES", "FECHA", "NACIMIENTO", "LUGAR", "SEXE", "NOM", "DEPARTMENT",
    "HOMELAND", "SECURITY", "NOTICE", "RECEIPT", "COURT", "ORDER", "CERTIFICATE", "ISSUED", "EXPIRES", "ADDRESS",
}


def readability(text: str) -> float:
    """Document-processing helper."""
    import unicodedata

    folded = "".join(c for c in unicodedata.normalize("NFKD", text.upper()) if not unicodedata.combining(c))
    words = re.findall(r"[A-Z]{2,}", folded)
    if not words:
        return 0.0
    hits = sum(1 for w in words if w in _READABLE_WORDS)
    mrz = 5 if re.search(r"P<[A-Z]{3}[A-Z<]{5,}", folded) else 0
    return (hits + mrz) / max(20, len(words)) * 100


def _ocr_page_images(page) -> str:
    """Document-processing helper."""
    from .ocr import ocr_image

    texts = []
    for image_file in _page_images(page):
        try:
            image = image_file.image
            best = ocr_image(image)
            score = readability(best)
            recognized = classify_text(best).doc_type != "unclassified"
            if not recognized:
                from PIL import ImageOps

                # Supporting implementation.
                variants = [image.rotate(a, expand=True) for a in (180, 90, 270)]
                variants += [ImageOps.mirror(image).rotate(a, expand=True) if a else ImageOps.mirror(image) for a in (0, 90, 180, 270)]
                for variant in variants:
                    candidate = ocr_image(variant)
                    candidate_recognized = classify_text(candidate).doc_type != "unclassified"
                    candidate_score = readability(candidate)
                    if (candidate_recognized, candidate_score) > (recognized, score):
                        best, score, recognized = candidate, candidate_score, candidate_recognized
                    if recognized:
                        break
            texts.append(best)
        except Exception:  # noqa: BLE001 -- e.g. Tesseract not installed; skip, don't fail extraction
            continue
    return "\n".join(texts)


def _barcode_page_images(page) -> str:
    """Document-processing helper."""
    from .barcode import decode_pdf417

    texts = []
    for image_file in _page_images(page):
        try:
            decoded = decode_pdf417(image_file.image)
        except Exception:  # noqa: BLE001 -- e.g. zxing-cpp not installed; skip, don't fail extraction
            continue
        if decoded:
            texts.append(decoded)
    return "\n".join(texts)


def classify_document(path: str | Path) -> Classification:
    text = extract_text(path)
    result = classify_text(text)
    if not text.strip():
        result.text_extracted = False
    return result


def acroform_field_values(path: str | Path) -> dict:
    """Document-processing helper."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    fields = reader.get_fields() or {}
    return {name: f.value for name, f in fields.items() if f.value not in (None, "", "/Off")}
