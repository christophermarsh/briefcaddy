"""Application helper with evidence-bound inputs."""

from __future__ import annotations

import re
from pathlib import Path

from .pages import SCAN_MIN_WIDTH, typed_annotations

MIN_BLANK_LINES = 20
_BLANK_LINE = re.compile(r"_{8,}")


def is_blank_template(pdf_path: str | Path) -> bool:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    if any(v.get("/V") not in (None, "", "/Off") for v in (reader.get_fields() or {}).values()):
        return False  # Generic implementation note.
    if typed_annotations(pdf_path):
        return False  # Generic implementation note.
    blank_lines = 0
    for page in reader.pages:
        if any(image.image.width >= SCAN_MIN_WIDTH for image in page.images):
            return False  # Generic implementation note.
        blank_lines += len(_BLANK_LINE.findall(page.extract_text() or ""))
    return blank_lines >= MIN_BLANK_LINES
