"""Supplemental independent source-race and default deadline checks."""
# ruff: noqa: F811 -- canonical isolated fixture is injected by pytest
import inspect

import pytest
from PIL import Image
from test_review_evidence import retained  # noqa: F401

from classify import ocr
from portal.demo import document_pdf
from review.evidence import Unavailable, annotate, snapshot


def test_independent_general_word_ocr_keeps_thirty_second_default(tmp_path, monkeypatch):
    import subprocess
    observed = []
    def fail(command, **kwargs):
        observed.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(ocr.subprocess, "run", fail)
    assert inspect.signature(ocr.ocr_words).parameters["timeout"].default == 30
    with pytest.raises(subprocess.TimeoutExpired):
        ocr.ocr_words(Image.new("L", (40, 60)), tesseract_cmd="independent-fictional", work_dir=tmp_path)
    assert observed == [30]


def test_independent_replacement_during_optional_region_lookup_preserves_proof(retained):
    case, folder, _ = retained
    payload = {"sources": [{"doc": "i94.pdf", "page": 0, "raw": "SAMPLE ALPHA"}],
               "evidence_fingerprints": {"independent": "unchanged"}}
    def lookup(source, location, quote):
        (folder / "i94.pdf").write_bytes(document_pdf(["INDEPENDENT FICTIONAL REPLACEMENT"]))
        snapshot(case, "i94.pdf", source.sha256)
    annotate(case, payload, region_reader=lookup)
    assert payload["sources"][0]["source_location"]["state"] == "unavailable"
    assert payload["sources"][0]["source_location"]["region"] is None
    assert payload["evidence_fingerprints"] == {"independent": "unchanged"}
    with pytest.raises(Unavailable, match="original changed"):
        snapshot(case, "i94.pdf")
