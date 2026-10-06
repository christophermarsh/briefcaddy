"""Fictional lifecycle probes for the new receipt and operation proof stores."""
import json

import pytest
import jobs
import purge
from portal.store import PortalStore
from portal import upload_recovery


def test_reserved_job_without_file_is_explicitly_unavailable(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    real_write = jobs._write
    def write(path, value):
        if jobs.NAME.fullmatch(path.name):
            raise OSError("fictional interruption after reservation")
        real_write(path, value)
    monkeypatch.setattr(jobs, "_write", write)
    with pytest.raises(OSError):
        jobs.submit(root, "portal_upload", "fictional-a", operation_id="a" * 32)
    monkeypatch.setattr(jobs, "_write", real_write)
    result = jobs.submit(root, "portal_upload", "fictional-a", operation_id="a" * 32)
    assert result["state"] == "unavailable"
    assert jobs.jobs(root) == []
    assert len(list(root.glob("operation-*.json"))) == 1


def test_q1_purge_removes_own_receipts_and_proofs_keeps_other_client(tmp_path, monkeypatch):
    from communication_fixture import installation
    from pypdf import PdfWriter
    import io
    writer = PdfWriter(); writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO(); writer.write(buffer); pdf = buffer.getvalue()
    home = installation(tmp_path, monkeypatch); clients = home / "clients"
    store = PortalStore(home / "portal")
    from PIL import Image
    photo = io.BytesIO(); Image.new("RGB", (16, 16), "white").save(photo, "PNG")
    for cid in ("fictional-a", "fictional-b"):
        (clients / cid).mkdir(parents=True)
        store.add_client(cid, "Fictional " + cid, language="en")
        upload_recovery.accept(store, cid, "a" * 32, "passport", "fictional.pdf", pdf, "application/pdf", lambda *args: "tonight")
        upload_recovery.accept(store, cid, "b" * 32, "passport", "fictional.png", photo.getvalue(), "image/png", lambda *args: "tonight")
        jobs.submit(home / "jobs", "portal_upload", cid, operation_id="a" * 32)
    own_receipt = upload_recovery._path(store, "fictional-a", "a" * 32)
    other_receipt = upload_recovery._path(store, "fictional-b", "a" * 32)
    before = other_receipt.read_bytes()
    other_image = next((store.client_dir("fictional-b") / "capture-evidence").glob("*.image"))
    other_image_bytes = other_image.read_bytes()
    identity = purge.Identity("fictional-a", [], set(), set(), set())
    purge.empty_stores(clients, "fictional-a", store.root, who=identity)
    assert not own_receipt.exists()
    assert other_receipt.read_bytes() == before
    assert not (store.client_dir("fictional-a") / "capture-evidence").exists()
    assert other_image.read_bytes() == other_image_bytes
    proofs = [json.loads(path.read_text()) for path in (home / "jobs").glob("operation-*.json")]
    assert [r["client"] for r in proofs] == ["fictional-b"]
