"""EV5 safe retained preview contract; every input is fictional/local."""
import hashlib
import io
import json
import threading
from collections import OrderedDict

import pytest
from PIL import Image, ImageDraw

import documents
import subject_attribution as subjects
from document_instances import digest
from factgraph import FactGraph
from portal.demo import document_pdf
from questionnaire.pages import page_images
from review.evidence import Unavailable, annotate, snapshot, validate_region
from review.server import ReviewApp
from review.state import save_bundle, _fact_view, _source_reader
from synthetic_documents import process_retained_documents

TEXT = "Most Recent I-94\nAdmission (I-94) Record Number: 11111111111\nLast/Surname: SAMPLE\nFirst (Given) Name: ALPHA\nBirth Date: 01/02/2000"


@pytest.fixture
def retained(tmp_path):
    case, folder = tmp_path / "cases" / "fictional", tmp_path / "originals"
    result = process_retained_documents(case.name, folder, [("i94.pdf", TEXT)], pages={"i94.pdf": [TEXT]})
    save_bundle(result, case, folder)
    row = subjects.views(case)[0]
    person = documents.read(case)["case_subjects"]["people"][0]["id"]
    subjects.assign(case, row["instance_id"], row["fingerprint"], {"holder": person}, "Fictional Reviewer", "paralegal")
    return case, folder, row


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_retained_location_is_original_zero_based_and_has_no_invented_region(retained):
    case, folder, row = retained
    source = snapshot(case, "i94.pdf")
    assert source.data == (folder / "i94.pdf").read_bytes()
    assert source.sha256 == hashlib.sha256(source.data).hexdigest()
    location = source.location(0, row["instance_id"])
    assert location["state"] == "current" and location["page"] == 0
    assert location["instance_id"] == row["instance_id"] and location["original_range"] == [0, 0]
    assert location["region"] is location["coordinate_space"] is None
    assert "No reliable region" in location["reason"]
    assert str(folder) not in json.dumps(location)


@pytest.mark.parametrize("page", [-1, True, 1, "0"], ids=["negative", "boolean", "outside", "text"])
def test_unknown_or_outside_page_is_document_only(retained, page):
    case, _, _ = retained
    location = snapshot(case, "i94.pdf").location(page)
    assert location["page"] is None and location["page_basis"] == "unavailable"
    assert "open the document" in location["reason"]


def test_missing_fact_page_uses_only_the_bound_single_page_instance(retained):
    case, _, row = retained
    source = snapshot(case, "i94.pdf")
    loc = source.location(None, row["instance_id"])
    assert loc["page"] == 0 and loc["original_range"] == [0, 0]
    assert loc["page_basis"] == "single_page_instance_original_zero_based"
    assert "one original page" in loc["reason"] and loc["region"] is None
    payload = {"doc": "i94.pdf", "page": None, "instance_id": row["instance_id"], "evidence_fingerprints": {"key": "unchanged"}}
    annotate(case, payload)
    assert payload["page"] is None  # read-time provenance is never backfilled
    assert payload["source_location"]["page"] == 0
    assert payload["evidence_fingerprints"] == {"key": "unchanged"}
    assert source.location(None, "unrelated-instance")["page"] is None


def test_wrong_instance_does_not_authenticate_location(retained):
    case, _, _ = retained
    location = snapshot(case, "i94.pdf").location(0, "unrelated-instance")
    assert location["state"] == "unverified" and location["page"] is None
    assert location["instance_id"] is None


def test_split_alias_preserves_original_range_and_rejects_other_segment_page(retained):
    case, folder, _ = retained
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for _ in range(3):
        writer.add_page(PdfReader(io.BytesIO(document_pdf(TEXT.splitlines()))).pages[0])
    with (folder / "combined.pdf").open("wb") as stream:
        writer.write(stream)
    sha = hashlib.sha256((folder / "combined.pdf").read_bytes()).hexdigest()
    data = documents.read(case)
    data["boundary_plans"]["combined.pdf"] = {"source_sha256": sha, "page_count": 3, "instances": [
        {"first": 0, "last": 0, "doc_ids": ["combined.pdf#p1"], "instance_id": "first", "state": "reviewed"},
        {"first": 1, "last": 2, "doc_ids": ["combined.pdf#p2-3"], "instance_id": "second", "state": "reviewed"}]}
    write(case / "documents.json", data)
    source = snapshot(case, "combined.pdf#p2-3")
    assert source.location(1, "second")["original_range"] == [1, 2]
    assert source.location(2, "second")["page"] == 2
    assert source.location(0, "second")["page"] is None
    assert source.location(None, "second")["page"] is None  # two pages, no guessed first
    one = snapshot(case, "combined.pdf#p1").location(None, "first")
    assert one["page"] == 0 and one["page_basis"] == "single_page_instance_original_zero_based"
    assert snapshot(case, "combined.pdf").location(None)["page"] is None  # ambiguous bare original
    with pytest.raises(Unavailable, match="Unknown document"):
        snapshot(case, "combined.pdf#p1-3")


def test_registered_legacy_original_stays_readable_but_unverified(retained):
    case, _, _ = retained
    data = documents.read(case); data["boundary_plans"] = {}
    write(case / "documents.json", data)
    source = snapshot(case, "i94.pdf")
    assert source.data.startswith(b"%PDF")
    loc = source.location(0)
    assert loc["state"] == "unverified" and loc["source_sha256"] is loc["page"] is None


@pytest.mark.parametrize("doc", ["../i94.pdf", "nested/i94.pdf", "nested\\i94.pdf", "C:i94.pdf", "i94.pdf\x00", "missing.pdf"],
                         ids=["traversal", "slash", "backslash", "drive", "control", "unregistered"])
def test_source_names_refuse_traversal_and_unregistered_files(retained, doc):
    case, folder, _ = retained
    (folder / "missing.pdf").write_bytes((folder / "i94.pdf").read_bytes())
    with pytest.raises(Unavailable):
        snapshot(case, doc)


def test_reparse_ancestor_refused_without_reading_source(retained, monkeypatch):
    case, folder, _ = retained
    import review.evidence as evidence
    original = evidence.Path.lstat
    def attrs(path):
        result = original(path)
        if path == folder:
            class Reparse:
                st_file_attributes = 1024
                st_mode = result.st_mode
            return Reparse()
        return result
    monkeypatch.setattr(evidence.Path, "lstat", attrs)
    with pytest.raises(Unavailable, match="original is unavailable"):
        snapshot(case, "i94.pdf")


@pytest.mark.parametrize("field,value", [("source_sha256", "damaged"), ("page_count", True), ("instances", []),
                                       ("instances", [{"first": 0, "last": 3, "doc_ids": []}])],
                         ids=["bad-hash", "bad-count", "empty-layout", "outside-range"])
def test_corrupt_binding_refuses_or_has_no_known_instance(retained, field, value):
    case, _, _ = retained
    data = documents.read(case); data["boundary_plans"]["i94.pdf"][field] = value
    write(case / "documents.json", data)
    with pytest.raises(Unavailable): snapshot(case, "i94.pdf")


def test_annotation_retains_proofs_and_unknown_region_fallback(retained):
    case, _, row = retained
    payload = {"facts": [{"sources": [{"doc": "i94.pdf", "page": 0, "instance_id": row["instance_id"]}]}],
               "evidence_fingerprints": {"key": "unchanged"}, "source_review": {"basis": "manual_retained_source_review"}}
    proof = digest({k: payload[k] for k in ("evidence_fingerprints", "source_review")})
    annotate(case, payload)
    assert payload["facts"][0]["sources"][0]["source_location"]["page"] == 0
    assert payload["facts"][0]["sources"][0]["source_location"]["region"] is None
    assert proof == digest({k: payload[k] for k in ("evidence_fingerprints", "source_review")})


def test_recorded_questionnaire_box_names_real_pixel_space(retained):
    case, _, _ = retained
    payload = {"evidence": [{"doc": "i94.pdf", "page": 0, "question": "fictional", "box": [1, 2, 30, 40]},
                            {"doc": "i94.pdf", "page": None, "question": "unknown", "box": [1, 2, 30, 40]}]}
    annotate(case, payload)
    assert payload["evidence"][0]["source_location"]["coordinate_space"] == "page_image_pixels"
    assert payload["evidence"][1]["source_location"]["region"] is None


def test_read_time_reader_metadata_never_uses_current_config():
    assert _source_reader(None)["state"] == "unrecorded"
    manifest = {"declared_reader_release_revision": "fictional-read-revision", "normalization_assets": {"asset": "old"}, "upstream": {"execution_identity": "unavailable"}}
    old = _source_reader(manifest)
    assert old["release_revision"] == "fictional-read-revision"
    assert old["configuration_digest"] == digest(manifest)
    assert old["normalization_digest"] == digest(manifest["normalization_assets"])


@pytest.mark.parametrize("upstream", ["damaged", ["damaged"]], ids=["text", "list"])
def test_malformed_read_time_upstream_is_explicitly_unavailable(upstream):
    reader = _source_reader({"upstream": upstream})
    assert reader["state"] == "unavailable" and reader["upstream_identity"] == "unavailable"
    assert reader["normalization_digest"] is None and "malformed" in reader["reason"]


@pytest.mark.parametrize("box", ["0,0,51,60", "-1,0,20,20", "1,1,0,0", "0,0,nan,20", "0,0,inf,20", "1,2,3", "bad"],
                         ids=["outside", "negative", "reversed", "nan", "infinity", "missing-coordinate", "text"])
def test_region_must_fit_actual_render_dimensions(box):
    with pytest.raises(Unavailable, match="whole original page"):
        validate_region(box, 50, 60)


def test_valid_pixel_region_accepts_actual_bounds():
    assert validate_region("0,0,50,60", 50, 60) is None


@pytest.mark.parametrize("expected", ["old", "", "a" * 64], ids=["malformed", "blank", "different"])
def test_expected_source_identity_refuses_malformed_or_different_snapshot(retained, expected):
    case, _, _ = retained
    with pytest.raises(Unavailable, match="source"):
        snapshot(case, "i94.pdf", expected_sha256=expected)


def test_reprocessed_current_source_cannot_open_beside_old_fact(retained):
    case, folder, _ = retained
    prior = snapshot(case, "i94.pdf").sha256
    result = process_retained_documents(case.name, folder, [("i94.pdf", TEXT.replace("11111111111", "22222222222"))],
                                        pages={"i94.pdf": [TEXT.replace("11111111111", "22222222222")]})
    save_bundle(result, case, folder)
    assert snapshot(case, "i94.pdf").sha256 != prior
    with pytest.raises(Unavailable, match="fact was displayed"):
        snapshot(case, "i94.pdf", expected_sha256=prior)


def test_request_local_snapshot_reuses_registered_combined_alias_bytes(retained, monkeypatch):
    case, folder, _ = retained
    data = documents.read(case)
    data["boundary_plans"]["i94.pdf"]["instances"][0]["doc_ids"] += ["i94.pdf#p1"]
    write(case / "documents.json", data)
    from pathlib import Path
    original, reads = Path.read_bytes, []
    def counted(path):
        if path == folder / "i94.pdf": reads.append(path)
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", counted)
    payload = {"sources": [{"doc": "i94.pdf", "page": 0}, {"doc": "i94.pdf#p1", "page": 0}]}
    annotate(case, payload)
    assert len(reads) == 1
    assert all(row["source_location"]["page"] == 0 for row in payload["sources"])


def test_additive_fact_source_metadata_preserves_page_and_instance(retained):
    case, _, row = retained
    class Catalog:
        policy_why = {}
        def label(self, key): return "Fictional source field"
        def ref(self, key): return "Fictional reference"
        def input(self, key): return {"type": "text"}
    graph = FactGraph.load(case / "fact_graph.json")
    source = graph.get("applicant.i94_number").sources[0]
    view = _fact_view(graph, "applicant.i94_number", Catalog())
    assert view["sources"][0]["page"] == source.page
    assert view["sources"][0]["instance_id"] == row["instance_id"]
    assert view["sources"][0]["reader"]["configuration_digest"] == digest(source.read_manifest)


@pytest.mark.parametrize("scan", [False, True], ids=["digital", "rotated-scan"])
def test_page_images_bytes_and_path_parity(tmp_path, scan):
    if scan:
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
        image = Image.new("RGB", (1200, 1600), "white")
        draw = ImageDraw.Draw(image); draw.rectangle((50, 200, 750, 400), fill="black")
        image = image.rotate(90, expand=True)
        stream = io.BytesIO(); canvas_ = canvas.Canvas(stream, pagesize=(800, 600), invariant=1)
        canvas_.drawImage(ImageReader(image), 0, 0, width=800, height=600); canvas_.save(); data = stream.getvalue()
    else:
        data = document_pdf(["FICTIONAL SOURCE", "Original digital page"])
    path = tmp_path / "source.pdf"; path.write_bytes(data)
    direct, immutable = page_images(path), page_images(data)
    assert len(direct) == len(immutable) == 1
    assert direct[0].mode == immutable[0].mode == "L"
    assert direct[0].size == immutable[0].size and direct[0].tobytes() == immutable[0].tobytes()


def test_cached_preview_rechecks_actual_bytes_before_return(retained, monkeypatch):
    case, folder, _ = retained
    app = ReviewApp.__new__(ReviewApp)
    app.client_dir = lambda client: case
    app._lock = threading.RLock(); app._pages = OrderedDict(); app._rendering = {}
    calls = []
    def render(data, page):
        assert isinstance(data, bytes); assert page == 0; calls.append(data); return Image.new("L", (50, 60))
    monkeypatch.setattr("review.evidence.render_source_page", render)
    first = app.page_image("fictional", "i94.pdf", 0)
    assert app.page_image("fictional", "i94.pdf", 0) is first and len(calls) == 1
    (folder / "i94.pdf").write_bytes(document_pdf(["CHANGED FICTIONAL SOURCE"]))
    with pytest.raises(Unavailable, match="original changed"):
        app.page_image("fictional", "i94.pdf", 0)
    assert len(calls) == 1
    payload = {"facts": [{"sources": [{"doc": "i94.pdf", "page": 0}]}]}
    annotate(case, payload)
    loc = payload["facts"][0]["sources"][0]["source_location"]
    assert loc["state"] == "unavailable" and loc["page"] is None and "changed" in loc["reason"]
