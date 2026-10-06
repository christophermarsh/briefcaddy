"""Independent fictional revocation-at-decode upload regression."""
# ruff: noqa: F811 -- isolated capture fixture imported intentionally
import json

from communication_fixture import accepted_link
from fastapi.testclient import TestClient
from test_capture_derivatives import DERIVATIVE, META, ORIGINAL, store  # noqa: F401

from portal import capture_derivatives
from portal.app import create_app


def test_revocation_during_capture_validation_prevents_durable_upload(store, monkeypatch):
    real_prepare = capture_derivatives.prepare

    def revoke_after_decode(*args, **kwargs):
        result = real_prepare(*args, **kwargs)
        store.end_sessions("fictional-a")
        return result

    monkeypatch.setattr(capture_derivatives, "prepare", revoke_after_decode)
    with TestClient(create_app(store.root, secure_cookies=False)) as browser:
        browser.get("/l/" + accepted_link(store, "fictional-a"))
        response = browser.post("/api/upload", headers={"X-Portal": "1"},
            data={"doc_id": "passport", "attempt": "e" * 32, "capture_metadata": json.dumps(META)},
            files={"file": ("fictional.jpg", ORIGINAL, "image/jpeg"),
                   "derivative": ("fictional-crop.jpg", DERIVATIVE, "image/jpeg")})
        assert response.status_code == 401
    assert store.uploads("fictional-a") == []
    assert not (store.client_dir("fictional-a") / "capture-evidence").exists()
