"""An old capture cannot be presented as the current replacement document."""
# ruff: noqa: F811 -- canonical isolated fixture
import hashlib
import json
from types import SimpleNamespace

from test_capture_derivatives import store  # noqa: F401

from review.server import ReviewApp


def test_changed_same_filename_cannot_attach_prior_capture_as_current(store):
    case = store.root.parent / "clients" / "fictional-a"
    uploads = store.client_dir("fictional-a") / "uploads"
    uploads.mkdir(exist_ok=True)
    old_digest = hashlib.sha256(b"fictional old PDF").hexdigest()
    (uploads / "same.pdf").write_bytes(b"fictional replacement PDF")
    (case / "meta.json").write_text(json.dumps({"source_folder": str(uploads)}))
    store._write(store.client_dir("fictional-a") / "uploads.json", [
        {"id": "retained-capture", "stored": "same.pdf", "sha256": old_digest,
         "capture": {"quality_review_required": True, "original_sha256": "a" * 64}}])
    row = {"files": ["same.pdf"], "doc_ids": ["same.pdf"], "source_locations": [
        {"doc": "same.pdf", "source_location": {"state": "unavailable", "source_sha256": None}}]}
    app = SimpleNamespace(portal_root=store.root, client_dir=lambda client: case)
    ReviewApp._capture_notes(app, "fictional-a", [row])
    assert not row.get("capture_artifacts")
