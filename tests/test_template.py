"""Fictional fixture helper."""

from pypdf import PdfWriter

from questionnaire.template import is_blank_template



def test_scanned_page_is_never_a_blank_template(tmp_path):
    from PIL import Image

    img = Image.new("RGB", (1700, 2200), "white")
    path = tmp_path / "scan.pdf"
    img.save(path)
    assert is_blank_template(path) is False


def test_page_with_no_text_and_no_scan_is_not_flagged(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    path = tmp_path / "empty.pdf"
    with open(path, "wb") as fh:
        writer.write(fh)
    assert is_blank_template(path) is False
