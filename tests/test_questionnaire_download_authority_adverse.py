"""Independent fictional revocation while a download waits for its gate."""
# ruff: noqa: F811 -- canonical isolated fixture
import inspect
from contextlib import contextmanager

from communication_fixture import accepted_link
from fastapi.testclient import TestClient
from test_capture_derivatives import store  # noqa: F401

from portal.app import create_app
from portal.store import PortalStore


def test_download_rechecks_session_after_acquiring_final_read_gate(store, monkeypatch):
    from portal import questionnaire_pdf
    store.update_profile("fictional-a", status="submitted", submitted_at="2026-10-05T12:00:00+00:00")
    rendered = []
    monkeypatch.setattr(questionnaire_pdf, "render", lambda *args: rendered.append(True) or b"fictional-private-copy")
    original_gate = PortalStore._communication_gate
    revoked = False

    def gate(instance):
        at_download_gate = inspect.currentframe().f_back.f_code.co_name == "questionnaire_copy"

        @contextmanager
        def held():
            nonlocal revoked
            with original_gate(instance):
                if at_download_gate and not revoked:
                    revoked = True
                    store.end_sessions("fictional-a")
                yield

        return held()

    with TestClient(create_app(store.root, secure_cookies=False)) as browser:
        browser.get("/l/" + accepted_link(store, "fictional-a"))
        monkeypatch.setattr(PortalStore, "_communication_gate", gate)
        response = browser.get("/api/questionnaire.pdf")
    assert revoked
    assert response.status_code == 401
    assert rendered == []
