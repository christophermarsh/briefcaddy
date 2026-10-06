"""Independent fictional staff download must recheck authority after rendering."""
# ruff: noqa: F811 -- canonical isolated fixtures
import pytest
from test_prospects import call, firm, server  # noqa: F401
from test_staff_consent_http import client

import restricted
from portal import questionnaire_pdf


@pytest.mark.parametrize("change", ["session", "case_access"])
def test_staff_pdf_rechecks_authority_after_rendering(server, firm, monkeypatch, change):
    cid = client(server, name="Fictional Download Authority")
    firm["store"].update_profile(cid, status="submitted", submitted_at="2026-10-05T12:00:00+00:00")
    calls = []

    def render_then_revoke(*_args):
        calls.append(True)
        if change == "session":
            server["accounts"].sign_out(server["jane"].split("=", 1)[1])
        else:
            restricted.mark(firm["clients"] / cid, True, "Fictional access revoked during rendering", "Fictional Attorney", "attorney")
        return b"%PDF-fictional-sensitive-download"

    monkeypatch.setattr(questionnaire_pdf, "render", render_then_revoke)
    status, body = call(server, "jane", "/api/questionnaire.pdf?client=" + cid)
    assert calls == [True]
    assert status == (401 if change == "session" else 404)
    assert b"fictional-sensitive-download" not in body
