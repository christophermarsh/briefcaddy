"""Application parser or rule helper."""
from __future__ import annotations
import threading
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
PDFIUM_LOCK = threading.Lock()
SCAN_MIN_WIDTH = 1000
RENDER_WIDTH = 1700

@dataclass(frozen=True)
class TypedAnnotation:
    page: int
    box: tuple[int, int, int, int]
    text: str

def typed_annotations(pdf_path: str | Path) -> list[TypedAnnotation]:
    """Application parser or rule helper."""
    from pypdf import PdfReader
    out = []
    for index, page in enumerate(PdfReader(str(pdf_path)).pages):
        width, height = (float(page.mediabox.width), float(page.mediabox.height))
        scale = RENDER_WIDTH / width
        for ref in page.get('/Annots') or []:
            annot = ref.get_object()
            text = str(annot.get('/Contents') or '').strip()
            if annot.get('/Subtype') != '/FreeText' or not text:
                continue
            x0, y0, x1, y1 = (float(v) for v in annot['/Rect'])
            box = (round(x0 * scale), round((height - y1) * scale), round(x1 * scale), round((height - y0) * scale))
            out.append(TypedAnnotation(index, box, text))
    return out

def _scan_image(page) -> Any | None:
    for image in page.images:
        if image.image.width >= SCAN_MIN_WIDTH:
            return image.image
    return None

def page_images(pdf_path: str | Path | bytes, annotations: bool=True) -> list[Any]:
    """Application parser or rule helper."""
    from PIL import ImageOps
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(pdf_path) if isinstance(pdf_path, bytes) else str(pdf_path))
    scans = [_scan_image(page) for page in reader.pages]
    if all((scan is not None for scan in scans)):
        return [ImageOps.grayscale(scan) for scan in scans]
    import pypdfium2 as pdfium
    images: list[Any] = []
    with PDFIUM_LOCK:
        document = pdfium.PdfDocument(pdf_path if isinstance(pdf_path, bytes) else str(pdf_path))
        try:
            for index, scan in enumerate(scans):
                if scan is not None:
                    images.append(ImageOps.grayscale(scan))
                    continue
                pdf_page = document[index]
                scale = RENDER_WIDTH / pdf_page.get_width()
                bitmap = pdf_page.render(scale=scale, may_draw_forms=True, draw_annots=annotations)
                images.append(bitmap.to_pil().convert('L'))
                pdf_page.close()
        finally:
            document.close()
    return images
