"""The client-row button opens a real chooser and leaves visible upload controls."""
# ruff: noqa: F811 -- shared pytest fixtures
import os

import pytest
from playwright.sync_api import expect

from upload_workflow_fixtures import source_firm, world, app, server, sign_in  # noqa: F401
from test_staff_consent_browser import staff_chromium  # noqa: F401
from test_staff_upload_recovery import heic, pdf

pytestmark = pytest.mark.skipif(not os.environ.get("E2E"), reason="E2E=1 enables Chromium")


def test_client_row_add_documents_accepts_heic_and_shows_outcome(staff_chromium, server, world, monkeypatch):
    import jobs
    monkeypatch.setattr(jobs, "ensure_worker", lambda *_: False)
    cid = world["client"]
    client_name = world["store"].profile(cid)["name"]
    context = staff_chromium.new_context(viewport={"width": 1280, "height": 900})
    name, value = sign_in(server, "jane@firm.example").split("=", 1)
    context.add_cookies([{"name": name, "value": value, "url": server}])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(server + "/#all")
        row = page.locator(f'#client-rows tr.row[data-client="{cid}"]')
        row.locator("summary").click()
        with page.expect_file_chooser() as chooser:
            row.get_by_role("button", name="Add documents", exact=True).click()
        picker = page.locator(f'[data-staff-document-picker="{cid}"]')
        expect(picker.get_by_role("heading", name="Add documents for " + client_name, exact=True)).to_be_visible()
        assert ".heic" in picker.locator('input[type="file"]').get_attribute("accept")
        with page.expect_response(lambda response: response.url.endswith("/api/client-upload") and response.request.post_data_json["name"] == "fictional-second.pdf") as received:
            chooser.value.set_files([{"name": "fictional-iphone.HEIC", "mimeType": "image/heic", "buffer": heic()},
                                     {"name": "fictional-second.pdf", "mimeType": "application/pdf", "buffer": pdf()}])
        assert received.value.status == 200, received.value.text()
        expect(page.locator("[data-staff-upload-attempt]")).to_have_count(2)
        expect(page.locator("[data-upload-status]").first).to_contain_text("Received")
        expect(page.locator("[data-upload-status]").last).to_contain_text("Received")
        assert len(world["store"].uploads(cid)) == 2
        assert not errors
    finally:
        context.close()
