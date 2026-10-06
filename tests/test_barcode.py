"""classify/barcode.py -- parse_aamva is tested as pure logic against
constructed-but-spec-accurate AAMVA field-code text (the published AAMVA
DL/ID Card Design Standard, a real federal specification -- not a guessed
document layout). decode_pdf417 needs a real barcode image; the one real
license scan available in the example case doesn't have enough
resolution for its PDF417 to decode (see docs/decisions.md), so that path
is only smoke-tested for graceful failure, not a successful decode."""

from classify.barcode import decode_pdf417, parse_aamva


def test_parse_aamva_extracts_known_field_codes():
    raw = "DCSSMITH\nDACJOHN\nDAG123 MAIN ST\nDAICAMBRIDGE\nDAJMA\nDAK02139\n"
    parsed = parse_aamva(raw)
    assert parsed == {
        "DCS": "SMITH",
        "DAC": "JOHN",
        "DAG": "123 MAIN ST",
        "DAI": "CAMBRIDGE",
        "DAJ": "MA",
        "DAK": "02139",
    }


def test_parse_aamva_ignores_lines_that_are_not_field_codes():
    raw = "not a field code\nDCSSMITH\nrandom OCR noise\n"
    assert parse_aamva(raw) == {"DCS": "SMITH"}


def test_parse_aamva_returns_empty_dict_for_non_aamva_text():
    assert parse_aamva("This is a grocery list.\nMilk, eggs, bread.\n") == {}


def test_parse_aamva_skips_field_codes_with_no_value():
    assert parse_aamva("DCS\n") == {}


def test_decode_pdf417_returns_none_for_a_blank_image():
    from PIL import Image

    blank = Image.new("RGB", (200, 200), color="white")
    assert decode_pdf417(blank) is None
