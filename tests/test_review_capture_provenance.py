"""Capture display binds exact current PDF plus immutable own-client artifacts."""
# ruff: noqa: F811
import hashlib
import io
import json
import pytest
from PIL import Image
from portal.store import PortalStore
from review.server import ReviewApp
from test_prospects import firm, server, call  # noqa: F401
from test_staff_consent_http import client


def test_same_filename_replacement_does_not_attach_earlier_capture(monkeypatch, tmp_path):
    case = tmp_path / "case"; case.mkdir()
    portal = tmp_path / "portal"
    source = portal / "clients" / "fictional" / "uploads"; source.mkdir(parents=True)
    (case / "meta.json").write_text(json.dumps({"source_folder": str(source)}))
    upload = {"id": "u1", "stored": "scan.pdf", "sha256": "a" * 64,
              "capture": {"quality_review_required": True, "original_sha256": "b" * 64}}
    monkeypatch.setattr(PortalStore, "uploads", lambda *args: [upload])
    app = ReviewApp.__new__(ReviewApp)
    app.portal_root = portal
    app.client_dir = lambda *args: case
    row = {"files": ["scan.pdf"], "source_locations": [{"doc": "scan.pdf", "source_location": {"state": "current", "source_sha256": "a" * 64}}]}
    app._capture_notes("fictional", [row])
    assert row["capture_artifacts"][0]["upload_id"] == "u1"
    row["source_locations"][0]["source_location"]["source_sha256"] = "c" * 64
    app._capture_notes("fictional", [row])
    assert row["capture_artifacts"] == []


def test_capture_image_hash_and_own_case_authority(server, firm):
    cid = client(server)
    folder = firm["store"].client_dir(cid)
    captures = folder / "capture-evidence"; captures.mkdir()
    output = io.BytesIO(); Image.new("RGB", (30, 40), "white").save(output, format="PNG")
    data = output.getvalue(); digest = hashlib.sha256(data).hexdigest()
    capture = {"original_stored": "capture-evidence/u1-original.image", "original_sha256": digest}
    (captures / "u1-original.image").write_bytes(data)
    firm["store"]._write(folder / "uploads.json", [{"id": "u1", "stored": "scan.pdf", "capture": capture}])
    route = "/api/capture-image?client=" + cid + "&upload=u1&kind=original"
    code, body = call(server, "jane", route)
    assert code == 200 and body == data
    assert call(server, "jane", route.replace(cid, "case-rosa"))[0] == 404
    (captures / "u1-original.image").write_bytes(b"tampered")
    assert call(server, "jane", route)[0] == 404


@pytest.mark.parametrize("change", ["session", "case_access"])
def test_capture_artifact_release_rechecks_current_authority(server, firm, monkeypatch, change):
    import restricted
    cid = client(server, name="Fictional Capture Release")
    calls = []
    def read_then_revoke(*_args):
        calls.append(True)
        if change == "session":
            server["accounts"].sign_out(server["jane"].split("=", 1)[1])
        else:
            restricted.mark(firm["clients"] / cid, True, "Fictional access revoked during capture read", "Fictional Attorney", "attorney")
        return b"fictional-sensitive-capture", "image/png"
    monkeypatch.setattr(server["app"], "capture_image", read_then_revoke)
    code, body = call(server, "jane", "/api/capture-image?client=" + cid + "&upload=u1&kind=original")
    assert calls == [True]
    assert code == (401 if change == "session" else 404)
    assert b"fictional-sensitive-capture" not in body
