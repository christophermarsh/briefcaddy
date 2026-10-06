"""classify/ocr.py -- path translation is tested as pure logic (no
Tesseract needed); the actual OCR call is skipped when no local Tesseract
install is found, since this pipeline never falls back to a cloud OCR
service (docs/decisions.md's local-only data policy) and CI/dev machines
may not have Tesseract installed."""

import pytest

from classify.ocr import _content_row_bands, _to_windows_path, _windows_to_wsl_path, find_tesseract, ocr_image

pytestmark_tesseract = pytest.mark.skipif(find_tesseract() is None, reason="Tesseract OCR not installed locally")


def test_windows_to_wsl_path_converts_drive_letter():
    assert _windows_to_wsl_path(r"C:\Program Files\Tesseract-OCR\tesseract.exe") == (
        "/mnt/c/Program Files/Tesseract-OCR/tesseract.exe"
    )


def test_windows_to_wsl_path_returns_none_for_a_non_windows_path():
    assert _windows_to_wsl_path("/usr/bin/tesseract") is None


def test_to_windows_path_converts_a_wsl_mount_path():
    from pathlib import Path

    assert _to_windows_path(Path("/mnt/c/Users/chris/i485-pipeline/.ocr_tmp/x/input.png")) == (
        r"C:\Users\chris\i485-pipeline\.ocr_tmp\x\input.png"
    )


def test_to_windows_path_is_a_no_op_for_a_native_path():
    from pathlib import Path

    # Already-native paths (no /mnt/<drive> prefix) need no translation --
    # this is the case on a plain native install (Windows or Linux), where
    # OCR never needs to cross a WSL/Windows filesystem boundary at all.
    native = Path("/home/user/project/.ocr_tmp/x/input.png")
    assert _to_windows_path(native) == str(native)


def test_content_row_bands_splits_two_disconnected_blocks():
    # Confirmed necessary against the real driver's license: front and
    # back scanned onto one page as two disconnected blocks with a huge
    # blank gap between them -- a single overall bounding box still
    # includes that whole gap, diluting the resolution OCR actually gets
    # on the two small regions that matter.
    from PIL import Image, ImageDraw

    mask = Image.new("L", (100, 300), color=0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle((10, 10, 90, 40), fill=255)  # band 1: rows 10-40
    draw.rectangle((10, 200, 90, 230), fill=255)  # band 2: rows 200-230

    bands = _content_row_bands(mask)
    assert len(bands) == 2
    assert bands[0][0] <= 10 and bands[0][1] >= 40
    assert bands[1][0] >= 100 and bands[1][1] <= 300


def test_content_row_bands_merges_content_within_the_gap_tolerance():
    from PIL import Image, ImageDraw

    mask = Image.new("L", (100, 100), color=0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle((10, 10, 90, 20), fill=255)
    draw.rectangle((10, 25, 90, 35), fill=255)  # only 5px gap -- same band

    bands = _content_row_bands(mask, min_gap=40)
    assert len(bands) == 1


def test_content_row_bands_returns_empty_for_a_blank_image():
    from PIL import Image

    mask = Image.new("L", (100, 100), color=0)
    assert _content_row_bands(mask) == []


def _words(confs):
    from classify.ocr import OcrWord

    return [OcrWord(text="WORD", left=0, top=0, width=1, height=1, conf=c, line_id=(1, 1, 1)) for c in confs]


def test_each_band_is_read_in_black_and_white_and_in_grayscale_and_the_more_confident_reading_is_kept():
    # Measured on the real scans (10/04/2026): the black-and-white threshold erased the marriage certificate's "Name after
    # Marriage" row and garbled the Notice to Appear's typewritten address; grayscale read both. The birth certificate reads
    # better in black and white. So both are read and Tesseract's own confidence decides, per band.
    from PIL import Image

    from classify.ocr import _best, _pictures

    pictures = _pictures(Image.new("L", (400, 100), color=255))
    assert [name for name, _ in pictures] == ["black_and_white", "grayscale"] and all(p.width == 3000 for _, p in pictures)
    assert _best([("black_and_white", "FRAMINGHAN", _words([74.6, 70.0])), ("grayscale", "FRAMINGHAM", _words([79.7, 80.0]))]) == "FRAMINGHAM"
    assert _best([("black_and_white", "clean", _words([69.9] * 3)), ("grayscale", "noisy", _words([58.1] * 5))]) == "clean"
    # a tie: more words read wins; the same again: the first (black and white, what every scan got before)
    assert _best([("black_and_white", "two", _words([90, 90])), ("grayscale", "three", _words([90, 90, 90]))]) == "three"
    assert _best([("black_and_white", "first", _words([90])), ("grayscale", "second", _words([90]))]) == "first"
    assert _best([("black_and_white", "", []), ("grayscale", "", [])]) == "" and _best([]) == ""
    # what Tesseract did not read (confidence -1) and one-character debris do not count
    assert _best([("black_and_white", "a", _words([-1, -1, 95])), ("grayscale", "b", _words([80, 80]))]) == "a"


@pytestmark_tesseract
def test_ocr_image_reads_real_text_from_a_generated_image():
    import shutil
    from pathlib import Path

    from PIL import Image, ImageDraw

    image = Image.new("RGB", (400, 100), color="white")
    draw = ImageDraw.Draw(image)
    draw.text((10, 30), "HELLO WORLD", fill="black")

    # Deliberately NOT pytest's tmp_path fixture: that lives under the
    # OS's own /tmp, which is exactly the WSL-internal path a Windows
    # tesseract.exe can't resolve (see ocr_image's docstring). A relative
    # work_dir under the repo mirrors classifier.py's real default.
    work_dir = Path("tests/.ocr_test_tmp")
    try:
        text = ocr_image(image, work_dir=work_dir)
        assert "HELLO" in text.upper()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@pytest.mark.parametrize("native_result", ["number", "unreadable", "wrong_document", "process_error", "missing_output", "invalid_encoding"])
def test_ssn_security_background_falls_back_to_native_pixels(tmp_path, monkeypatch, native_result):
    from pathlib import Path
    from PIL import Image
    from classify import ocr
    processed = "YOUR SOCIAL SECURITY CARD\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION"
    native = processed + ("\n123-45-6789" if native_result == "number" else "\nUnreadable number")
    if native_result == "wrong_document":
        native = "Unrelated document\n123-45-6789"
    image = Image.new("RGB", (64, 48), "white")
    monkeypatch.setattr(ocr, "_preprocess", lambda _: [image])
    monkeypatch.setattr(ocr, "_pictures", lambda _: [("fixture", image)])
    monkeypatch.setattr(ocr, "parse_tsv", lambda _: _words([99]))
    calls = []
    def tesseract(command, **kwargs):
        output = Path(command[2])
        calls.append(command)
        if output.name == "native":
            if native_result == "process_error":
                raise ocr.subprocess.CalledProcessError(1, command)
            if native_result == "missing_output":
                return
            if native_result == "invalid_encoding":
                output.with_suffix(".txt").write_bytes(b"\xff")
                return
        output.with_suffix(".txt").write_text(native if output.name == "native" else processed)
        output.with_suffix(".tsv").write_text("")
    monkeypatch.setattr(ocr.subprocess, "run", tesseract)
    assert ocr.ocr_image(image, tesseract_cmd="tesseract", work_dir=tmp_path) == (native if native_result == "number" else processed)
    assert len(calls) == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("result", ["better", "worse", "wrong_document", "failed"])
def test_weak_phone_photo_uses_native_layout_only_when_it_improves_reading(tmp_path, monkeypatch, result):
    from pathlib import Path
    from PIL import Image
    from classify import ocr

    processed = "PASSPORT\nP<BRATEST<<PERSON\ngarbled background"
    native = "PASSPORT\nP<BRATEST<<PERSON\nreadable fields"
    if result == "wrong_document":
        native = "YOUR SOCIAL SECURITY CARD"
    image = Image.new("RGB", (64, 48), "white")
    monkeypatch.setattr(ocr, "_preprocess", lambda _: [image])
    monkeypatch.setattr(ocr, "_pictures", lambda _: [("fixture", image)])
    monkeypatch.setattr(ocr, "parse_tsv", lambda text: _words([int(text)]))
    calls = []

    def tesseract(command, **kwargs):
        calls.append(command)
        output = Path(command[2])
        layout = output.name == "layout"
        if layout and result == "failed":
            raise ocr.subprocess.CalledProcessError(1, command)
        output.with_suffix(".txt").write_text(native if layout else processed)
        confidence = 80 if layout and result != "worse" else 40 if layout else 50
        output.with_suffix(".tsv").write_text(str(confidence))

    monkeypatch.setattr(ocr.subprocess, "run", tesseract)
    assert ocr.ocr_image(image, tesseract_cmd="tesseract", work_dir=tmp_path) == (native if result == "better" else processed)
    assert calls[-1][3:] == ["--psm", "3", "txt", "tsv"]
    assert list(tmp_path.iterdir()) == []
def test_word_ocr_subprocess_has_explicit_execution_bound(tmp_path, monkeypatch):
    import subprocess
    from PIL import Image
    from classify import ocr
    calls = []
    def timeout(command, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(ocr.subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        ocr.ocr_words(Image.new("L", (100, 100)), tesseract_cmd="fictional-tesseract", work_dir=tmp_path, timeout=2)
    assert calls[0]["timeout"] == 2
