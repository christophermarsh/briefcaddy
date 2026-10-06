"""Fictional preview release must refresh authority and source after rendering."""
# ruff: noqa: F811 -- canonical isolated fixtures
import pytest
from test_review_evidence_routes import (  # noqa: F401
    app,
    call,
    controls,
    retain,
    server,
    sign_in,
    world,
)

import documents
import restricted
from portal.demo import document_pdf


@pytest.mark.parametrize("change", ["session", "case_access", "source"])
def test_crop_rechecks_authority_and_source_after_rendering(server, world, app, monkeypatch, change):
    source = retain(world, "case-ana")
    cookie = sign_in(server, "jane@firm.example")
    expected = documents.read(world / "case-ana")["boundary_plans"]["i94.pdf"]["source_sha256"]
    original = app.page_image
    changed = []

    def render_then_change(*args, **kwargs):
        image = original(*args, **kwargs)
        changed.append(True)
        if change == "session":
            app.accounts.sign_out(cookie.split("=", 1)[1])
        elif change == "case_access":
            restricted.mark(world / "case-ana", True, "Fictional access revoked during preview", "Fictional Attorney", "attorney")
        else:
            (source / "i94.pdf").write_bytes(document_pdf(["CHANGED FICTIONAL SOURCE AFTER RENDER"]))
        return image

    monkeypatch.setattr(app, "page_image", render_then_change)
    status, body = call(server + "/api/crop?client=case-ana&doc=i94.pdf&page=0&expected_sha256=" + expected, cookie)
    assert changed == [True]
    assert status == (401 if change == "session" else 404)
    assert not body.startswith("\x89PNG")
