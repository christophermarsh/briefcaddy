"""Fictional original-pixel regions and single-page preview regressions."""
import hashlib
import io
import threading
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from classify.ocr import OcrWord
from review.evidence import quote_line_region, render_source_page, Unavailable
from review.server import ReviewApp
from questionnaire.pages import page_images
from test_review_evidence import retained  # noqa: F401 -- fictional fixture

def word(text, left=10, line=1, confidence=95):
    return OcrWord(text, left, line * 20, 50, 12, confidence, (1, 1, line))

def test_quote_region_is_original_pixel_line_and_requires_unique_literal_match():
    words = [word("Name:"), word("JOAO", 70), word("SILVA", 130)]
    assert quote_line_region(words, "JOAO SILVA", 400, 500) == [10, 20, 180, 32]
    assert quote_line_region(words, "JOSE SILVA", 400, 500) is None
    assert quote_line_region(words + [word("JOAO", 70, 2), word("SILVA", 130, 2)], "JOAO SILVA", 400, 500) is None


def test_wrapped_duplicate_quote_prevents_a_falsely_unique_crop():
    words = [word("JOAO", 70), word("SILVA", 130),
             word("JOAO", 70, 2), word("SILVA", 70, 3)]
    assert quote_line_region(words, "JOAO SILVA", 400, 500) is None


def test_wrapped_only_quote_keeps_honest_page_fallback():
    assert quote_line_region([word("JOAO", 70), word("SILVA", 70, 2)], "JOAO SILVA", 400, 500) is None


def test_distant_blocks_cannot_form_an_exact_crop():
    distant = OcrWord("SILVA", 700, 800, 60, 12, 95, (2, 1, 1))
    assert quote_line_region([word("JOAO", 70), distant], "JOAO SILVA", 1000, 1200) is None

@pytest.mark.parametrize("confidence", [0, 69, float("nan")])
def test_uncertain_words_have_no_invented_region(confidence):
    assert quote_line_region([word("SAMPLE", confidence=confidence)], "SAMPLE", 400, 500) is None

def test_invalid_coordinates_and_empty_quote_have_no_region():
    assert quote_line_region([word("SAMPLE", left=-1)], "SAMPLE", 400, 500) is None
    assert quote_line_region([word("SAMPLE")], "", 400, 500) is None

@pytest.mark.parametrize("scan", [False, True])
def test_single_page_matches_existing_original_coordinates(scan):
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    data = io.BytesIO()
    pdf = canvas.Canvas(data, pagesize=(400, 500))
    for index in range(3):
        if scan:
            image = Image.new("RGB", (1200, 1500), (index * 30, 255, 240))
            pdf.drawImage(ImageReader(image), 0, 0, 400, 500)
        else:
            pdf.drawString(30, 400, "FICTIONAL PAGE " + str(index))
        pdf.showPage()
    pdf.save()
    original = data.getvalue()
    all_pages = page_images(original)
    for page in (0, 2):
        isolated = render_source_page(original, page)
        assert isolated.size == all_pages[page].size
        assert isolated.tobytes() == all_pages[page].tobytes()
    with pytest.raises(Unavailable):
        render_source_page(original, 3)

def test_alias_cache_reuses_one_page_only_after_each_source_validation(monkeypatch):
    app = ReviewApp.__new__(ReviewApp)
    app._lock = threading.RLock(); app._pages = OrderedDict(); app._rendering = {}
    seen = []; renders = []
    digest = hashlib.sha256(b"fictional").hexdigest()
    class Source:
        path = Path("fictional.pdf"); sha256 = digest; data = b"fictional"; content_type = "application/pdf"
        def location(self, page):
            return {"page": page if page == 1 else None}
    def snapshot(client, doc, expected):
        seen.append((client, doc, expected)); return Source()
    app.source_snapshot = snapshot
    def render(data, page, timeout):
        renders.append(page); return Image.new("L", (100, 120))
    monkeypatch.setattr("review.evidence.render_source_page_bounded", render)
    first = app.page_image("fictional", "fictional.pdf#p2", 1, digest)
    assert app.page_image("fictional", "fictional.pdf", 1, digest) is first
    with pytest.raises(Unavailable):
        app.page_image("fictional", "fictional.pdf#p2", 2, digest)
    assert renders == [1] and len(seen) == 3

def test_failed_render_releases_per_page_lock_record(monkeypatch):
    app = ReviewApp.__new__(ReviewApp)
    app._lock = threading.RLock(); app._pages = OrderedDict(); app._rendering = {}
    app.source_snapshot = lambda *args: SimpleNamespace(path=Path("fictional.pdf"), sha256="f" * 64, data=b"fictional", content_type="application/pdf", location=lambda page: {"page": page})
    def fail(*args):
        raise ValueError("fictional render failure")
    monkeypatch.setattr("review.evidence.render_source_page_bounded", fail)
    with pytest.raises(ValueError):
        app.page_image("fictional", "fictional.pdf", 0)
    assert not app._rendering and not app._pages


def test_scan_region_annotation_is_additive_and_stale_sources_never_get_regions(retained):  # noqa: F811 -- pytest fixture injection
    import copy
    from review.evidence import annotate
    from portal.demo import document_pdf
    case, folder, _ = retained
    payload = {"facts": [{"sources": [{"doc": "i94.pdf", "page": 0, "raw": "SAMPLE ALPHA"}]}], "evidence_fingerprints": {"original": "unchanged"}}
    before = copy.deepcopy(payload)
    calls = []
    def locate(source, location, quote):
        calls.append((source.sha256, location["instance_id"], quote))
        return [10, 20, 180, 32]
    annotate(case, payload, region_reader=locate)
    location = payload["facts"][0]["sources"][0].pop("source_location")
    assert location["region"] == [10, 20, 180, 32]
    assert location["region_basis"] == "unique_quote_on_original_pixels"
    assert calls[0][0] == location["source_sha256"] and calls[0][1] == location["instance_id"]
    assert payload == before
    (folder / "i94.pdf").write_bytes(document_pdf(["CHANGED FICTIONAL SOURCE"]))
    calls.clear()
    annotate(case, payload, region_reader=locate)
    assert not calls
    assert payload["facts"][0]["sources"][0]["source_location"]["region"] is None


def test_source_change_during_optional_lookup_degrades_case_evidence(retained):  # noqa: F811 -- pytest fixture injection
    from review.evidence import annotate, snapshot
    from portal.demo import document_pdf
    case, folder, _ = retained
    payload = {"sources": [{"doc": "i94.pdf", "page": 0, "raw": "SAMPLE ALPHA"}], "evidence_fingerprints": {"original": "unchanged"}}
    def locate(source, location, quote):
        (folder / "i94.pdf").write_bytes(document_pdf(["NEW FICTIONAL SOURCE"]))
        snapshot(case, source.alias, source.sha256)
    annotate(case, payload, region_reader=locate)
    assert payload["sources"][0]["source_location"]["state"] == "unavailable"
    assert payload["sources"][0]["source_location"]["region"] is None
    assert payload["evidence_fingerprints"] == {"original": "unchanged"}


def test_final_source_revalidation_discards_stale_region(retained):  # noqa: F811 -- pytest fixture injection
    from review.evidence import annotate, revalidate_locations
    from portal.demo import document_pdf
    case, folder, _ = retained
    payload = {"sources": [{"doc": "i94.pdf", "page": 0, "raw": "SAMPLE ALPHA"}]}
    annotate(case, payload, region_reader=lambda *args: [10, 20, 180, 32])
    (folder / "i94.pdf").write_bytes(document_pdf(["CHANGED FICTIONAL SOURCE"]))
    revalidate_locations(case, payload)
    assert payload["sources"][0]["source_location"]["state"] == "unavailable"
    assert payload["sources"][0]["source_location"]["region"] is None


def test_enrichment_cannot_upgrade_source_identity_after_reprocessing(retained):  # noqa: F811 -- pytest fixture injection
    import json
    from review.evidence import annotate
    from portal.demo import document_pdf
    case, folder, _ = retained
    payload = {"sources": [{"doc": "i94.pdf", "page": 0, "raw": "SAMPLE ALPHA"}]}
    annotate(case, payload)
    new_data = document_pdf(["CHANGED FICTIONAL SOURCE"])
    (folder / "i94.pdf").write_bytes(new_data)
    documents = json.loads((case / "documents.json").read_text(encoding="utf-8"))
    documents["boundary_plans"]["i94.pdf"]["source_sha256"] = hashlib.sha256(new_data).hexdigest()
    (case / "documents.json").write_text(json.dumps(documents), encoding="utf-8")
    calls = []
    annotate(case, payload, region_reader=lambda *args: calls.append(args), preserve_identity=True)
    assert not calls and payload["sources"][0]["source_location"]["state"] == "unavailable"


def test_preview_ocr_timeout_is_optional_and_revalidates_original(monkeypatch, tmp_path):
    import subprocess
    app = ReviewApp.__new__(ReviewApp)
    app._lock = threading.RLock()
    image = Image.new("L", (400, 500))
    app.page_image = lambda *args, **kwargs: image
    app.data_root = tmp_path / "data" / "clients"
    source = SimpleNamespace(path=Path("fictional.pdf"), alias="fictional.pdf", sha256="f" * 64)
    checked = []
    app.source_snapshot = lambda *args: checked.append(args)
    def timed_out(*args, **kwargs):
        assert 0 < kwargs["timeout"] <= 10
        raise subprocess.TimeoutExpired("synthetic-tesseract", 10)
    monkeypatch.setattr("classify.ocr.ocr_words", timed_out)
    assert app.source_quote_region("fictional", source, {"page": 0, "instance_id": "fictional-instance"}, "JOAO SILVA") is None
    assert checked == [("fictional", "fictional.pdf", "f" * 64)]
