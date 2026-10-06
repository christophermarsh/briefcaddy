"""Document-processing helper."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_COMMON_WINDOWS_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]

_WSL_MOUNT = re.compile(r"^/mnt/([a-z])/(.*)$")


def find_tesseract() -> str | None:
    """Document-processing helper."""
    env = os.environ.get("TESSERACT_CMD")
    if env:
        if Path(env).exists():
            return env
        wsl_env = _windows_to_wsl_path(env)  # Supporting implementation.
        if wsl_env and Path(wsl_env).exists():
            return wsl_env

    found = shutil.which("tesseract")
    if found:
        return found

    for candidate in _COMMON_WINDOWS_PATHS:
        if Path(candidate).exists():
            return candidate
        wsl_path = _windows_to_wsl_path(candidate)
        if wsl_path and Path(wsl_path).exists():
            return wsl_path

    return None


def _windows_to_wsl_path(win_path: str) -> str | None:
    match = re.match(r"^([A-Za-z]):\\(.*)$", win_path)
    if not match:
        return None
    drive, rest = match.groups()
    return f"/mnt/{drive.lower()}/" + rest.replace("\\", "/")


def _to_windows_path(path: Path) -> str:
    match = _WSL_MOUNT.match(path.as_posix())
    if not match:
        return str(path)  # Supporting implementation.
    drive, rest = match.groups()
    return f"{drive.upper()}:\\" + rest.replace("/", "\\")


def _content_row_bands(content_mask: Any, min_gap: int = 40) -> list[tuple[int, int]]:
    """Document-processing helper."""
    from PIL import Image

    height = content_mask.height
    strip = content_mask.resize((1, height), Image.BOX)
    row_values = list(strip.getdata())

    bands: list[tuple[int, int]] = []
    band_start: int | None = None
    last_content_row = -1
    for row, value in enumerate(row_values):
        if value > 5:
            if band_start is None:
                band_start = row
            last_content_row = row
        elif band_start is not None and row - last_content_row > min_gap:
            bands.append((band_start, last_content_row))
            band_start = None
    if band_start is not None:
        bands.append((band_start, last_content_row))
    return bands


def _upscale(gray: Any) -> Any:
    """Document-processing helper."""
    from PIL import Image

    target_width = 3000
    if gray.width < target_width:
        scale = target_width / gray.width
        gray = gray.resize((round(gray.width * scale), round(gray.height * scale)), Image.LANCZOS)
    return gray


def _enhance(gray: Any) -> Any:
    """Document-processing helper."""
    from PIL import ImageEnhance

    gray = _upscale(gray)
    gray = ImageEnhance.Contrast(gray).enhance(1.8)
    gray = ImageEnhance.Sharpness(gray).enhance(2.0)
    return gray.point(lambda p: 255 if p > 150 else 0)


def _pictures(gray: Any) -> list[tuple[str, Any]]:
    """Document-processing helper."""
    from PIL import ImageEnhance

    return [("black_and_white", _enhance(gray)), ("grayscale", ImageEnhance.Contrast(_upscale(gray)).enhance(1.8))]


CONFIDENT = 90.0  # Supporting implementation.


def _confidence(words: list[OcrWord]) -> float:
    """Document-processing helper."""
    confs = [w.conf for w in words if len(w.text) >= 2 and w.conf >= 0]
    return round(sum(confs) / len(confs), 1) if confs else 0.0


def _best(readings: list[tuple[str, str, list[OcrWord]]]) -> str:
    """Document-processing helper."""
    def score(reading: tuple[str, str, list[OcrWord]]) -> tuple[float, int]:
        return (_confidence(reading[2]), sum(1 for w in reading[2] if len(w.text) >= 2 and w.conf >= 0))

    return max(readings, key=score)[1] if readings else ""


def _preprocess(image: Any) -> list[Any]:
    """Document-processing helper."""
    from PIL import ImageOps

    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    content_mask = ImageOps.invert(gray).point(lambda p: 255 if p > 40 else 0)

    bands = _content_row_bands(content_mask)
    if not bands:
        return [gray]

    crops = []
    pad = 10
    for top, bottom in bands:
        row_band = content_mask.crop((0, top, content_mask.width, bottom + 1))
        col_bbox = row_band.getbbox()
        left, right = (col_bbox[0], col_bbox[2]) if col_bbox else (0, content_mask.width)
        crop = gray.crop(
            (
                max(0, left - pad),
                max(0, top - pad),
                min(gray.width, right + pad),
                min(gray.height, bottom + 1 + pad),
            )
        )
        crops.append(crop)
    return crops


def ocr_image(image: Any, tesseract_cmd: str | None = None, work_dir: str | Path = ".ocr_tmp") -> str:
    """Document-processing helper."""
    cmd = tesseract_cmd or find_tesseract()
    if cmd is None:
        raise RuntimeError(
            "Tesseract OCR not found. Install it locally and, if it's not on "
            "PATH or in a standard location, set the TESSERACT_CMD environment "
            "variable to its executable path."
        )

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    crossing_wsl_boundary = os.name == "posix" and cmd.lower().endswith(".exe")

    texts = []
    selected_words = []
    for band in _preprocess(image):
        readings = []
        for name, picture in _pictures(band):
            if readings and _confidence(readings[-1][2]) >= CONFIDENT:
                break  # Supporting implementation.
            with tempfile.TemporaryDirectory(dir=work_dir) as tmp_dir:
                tmp_path = Path(tmp_dir)
                input_path = tmp_path / "input.png"
                output_base = tmp_path / "output"
                picture.save(input_path)

                input_arg = _to_windows_path(input_path) if crossing_wsl_boundary else str(input_path)
                output_arg = _to_windows_path(output_base) if crossing_wsl_boundary else str(output_base)

                # Supporting implementation.
                subprocess.run([cmd, input_arg, output_arg, "--psm", "6", "txt", "tsv"], check=True, capture_output=True)
                text = output_base.with_suffix(".txt").read_text(encoding="utf-8")
                words = parse_tsv(output_base.with_suffix(".tsv").read_text(encoding="utf-8"))
            readings.append((name, text, words))
        best = _best(readings)
        texts.append(best)
        selected_words.extend(next((words for _, text, words in readings if text == best), []))

    combined = "\n".join(texts)
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    from .classifier import classify_text
    from extract.ssn_card import extract as ssn_fields
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    if _confidence(selected_words) < 70:
        try:
            with tempfile.TemporaryDirectory(dir=work_dir) as tmp_dir:
                input_path = Path(tmp_dir) / "layout.png"
                output_base = Path(tmp_dir) / "layout"
                image.save(input_path)
                input_arg = _to_windows_path(input_path) if crossing_wsl_boundary else str(input_path)
                output_arg = _to_windows_path(output_base) if crossing_wsl_boundary else str(output_base)
                subprocess.run([cmd, input_arg, output_arg, "--psm", "3", "txt", "tsv"], check=True, capture_output=True)
                native = output_base.with_suffix(".txt").read_text(encoding="utf-8")
                native_words = parse_tsv(output_base.with_suffix(".tsv").read_text(encoding="utf-8"))
            processed_kind = classify_text(combined).doc_type
            if processed_kind == "unclassified" or classify_text(native).doc_type == processed_kind:
                combined = _best([("processed", combined, selected_words), ("native_layout", native, native_words)])
        except (OSError, subprocess.CalledProcessError, UnicodeError):
            pass  # Supporting implementation.
    if classify_text(combined).doc_type == "ssn_card" and not ssn_fields(combined):
        try:
            with tempfile.TemporaryDirectory(dir=work_dir) as tmp_dir:
                input_path = Path(tmp_dir) / "native.png"
                output_base = Path(tmp_dir) / "native"
                image.save(input_path)
                input_arg = _to_windows_path(input_path) if crossing_wsl_boundary else str(input_path)
                output_arg = _to_windows_path(output_base) if crossing_wsl_boundary else str(output_base)
                subprocess.run([cmd, input_arg, output_arg, "--psm", "6", "txt"], check=True, capture_output=True)
                native = output_base.with_suffix(".txt").read_text(encoding="utf-8")
        except (OSError, subprocess.CalledProcessError, UnicodeError):
            # Supporting implementation.
            # Supporting implementation.
            return combined
        if classify_text(native).doc_type == "ssn_card" and ssn_fields(native):
            return native
    if classify_text(combined).doc_type == "birth_certificate":
        from extract.birth_certificate import extract as birth_fields
        if not any("birth_cert.parent_" in field.fact_key or field.fact_key in {
            "applicant.birth_cert.mother_name", "applicant.birth_cert.father_name"
        } for field in birth_fields(combined)):
            # Supporting implementation.
            # Supporting implementation.
            try:
                parent_block = _filiation_pixel_block(image, cmd, work_dir)
                if parent_block:
                    combined += "\n" + parent_block
            except (RuntimeError, OSError, ValueError, UnicodeError, subprocess.CalledProcessError):
                pass
    return combined


def _filiation_pixel_block(image, cmd, work_dir):
    """Document-processing helper."""
    import re
    import unicodedata
    from PIL import ImageOps
    words = ocr_words(image, tesseract_cmd=cmd, work_dir=work_dir)
    lines = {}
    for word in words:
        lines.setdefault(word.line_id, []).append(word)
    rows = []
    for line in lines.values():
        line.sort(key=lambda word: word.left)
        value = " ".join(word.text for word in line)
        if re.search(r"(?i)\bnatural de\b", value) and _confidence(line) >= 70:
            rows.append((min(word.top for word in line), max(word.bottom for word in line), min(word.left for word in line), value))
    rows.sort()
    if len(rows) != 2 or not 8 < (rows[1][0] + rows[1][1] - rows[0][0] - rows[0][1]) / 2 < 120 or abs(rows[0][2] - rows[1][2]) > image.width * .1:
        return None
    top = rows[0][0]
    if top < 5:
        return None
    heading = ImageOps.grayscale(image.crop((max(0, rows[0][2] - 20), max(0, top - 100), min(image.width, rows[0][2] + image.width * .35), top - 2)))
    heading = _upscale(heading)
    crossing = os.name == "posix" and cmd.lower().endswith(".exe")
    verified = False
    # Supporting implementation.
    # Supporting implementation.
    for _variant, picture in _pictures(heading):
        with tempfile.TemporaryDirectory(dir=work_dir) as scratch:
            input_path = Path(scratch) / "filiation-heading.png"
            output = Path(scratch) / "filiation-heading"
            picture.save(input_path)
            subprocess.run([cmd, _to_windows_path(input_path) if crossing else str(input_path),
                            _to_windows_path(output) if crossing else str(output), "--psm", "11", "txt"], check=True, capture_output=True)
            label = output.with_suffix(".txt").read_text(encoding="utf-8")
        label = "".join(c for c in unicodedata.normalize("NFKD", label.upper()) if not unicodedata.combining(c))
        match = re.search(r"\bFILIA[CG]+AO\b", label)
        if match is not None and not re.search(r"[A-Z]", label[match.end():]):
            verified = True
            break
    if not verified:
        return None
    # Supporting implementation.
    # Supporting implementation.
    from extract.birth_certificate import _filiation_entries
    block = "FILIACAO\n" + "\n".join(row[3] for row in rows) + "\nAVOS"
    return block if len(_filiation_entries(block)) == 2 else None


@dataclass(frozen=True)
class OcrWord:
    """Document-processing helper."""

    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float
    line_id: tuple[int, int, int]

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


def ocr_words(image: Any, tesseract_cmd: str | None = None, work_dir: str | Path = ".ocr_tmp", *, timeout: float = 30) -> list[OcrWord]:
    """Document-processing helper."""
    from PIL import ImageOps

    cmd = tesseract_cmd or find_tesseract()
    if cmd is None:
        raise RuntimeError("Tesseract OCR not found. Set TESSERACT_CMD to its executable path.")

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    crossing_wsl_boundary = os.name == "posix" and cmd.lower().endswith(".exe")

    with tempfile.TemporaryDirectory(dir=work_dir) as tmp_dir:
        tmp_path = Path(tmp_dir)
        input_path = tmp_path / "input.png"
        output_base = tmp_path / "output"
        ImageOps.grayscale(image).save(input_path)

        input_arg = _to_windows_path(input_path) if crossing_wsl_boundary else str(input_path)
        output_arg = _to_windows_path(output_base) if crossing_wsl_boundary else str(output_base)

        subprocess.run([cmd, input_arg, output_arg, "tsv"], check=True, capture_output=True, timeout=timeout)
        tsv = output_base.with_suffix(".tsv").read_text(encoding="utf-8")

    return parse_tsv(tsv)


def parse_tsv(tsv: str) -> list[OcrWord]:
    """Document-processing helper."""
    words: list[OcrWord] = []
    lines = tsv.splitlines()
    for row in lines[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = cols[11].strip()
        if not text:
            continue
        words.append(
            OcrWord(
                text=text,
                left=int(cols[6]),
                top=int(cols[7]),
                width=int(cols[8]),
                height=int(cols[9]),
                conf=float(cols[10]),
                line_id=(int(cols[2]), int(cols[3]), int(cols[4])),
            )
        )
    return words
