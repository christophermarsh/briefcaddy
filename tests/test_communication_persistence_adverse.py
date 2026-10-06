"""Independent fictional regressions for the reported preference persistence bug."""
# ruff: noqa: F811 -- canonical isolated pytest fixtures
import os

import pytest
from playwright.sync_api import expect
from test_prospects import firm, server  # noqa: F401
from test_staff_consent_browser import staff_chromium  # noqa: F401
from test_staff_consent_http import client

pytestmark = pytest.mark.skipif(not os.environ.get("E2E"), reason="E2E=1 enables Chromium")


@pytest.mark.parametrize("channel,label", [("email", "email"), ("sms", "text messages"), ("whatsapp", "whatsapp")])
def test_saved_permission_and_note_survive_panel_reopen(staff_chromium, server, firm, channel, label):
    cid = client(server, language="en", name="Fictional Persistence Tester")
    firm["store"].update_profile(cid, phone="+15550100123")
    context = staff_chromium.new_context(viewport={"width": 1280, "height": 900})
    name, value = server["jane"].split("=", 1)
    context.add_cookies([{"name": name, "value": value, "url": server["base"]}])
    page = context.new_page()
    try:
        page.goto(server["base"] + "/#all")
        row = page.locator(f'#client-rows tr.row[data-client="{cid}"]')
        row.locator("summary").click()
        row.get_by_role("button", name="Communication choices", exact=True).click()
        panel = page.locator(f'[data-communication="{cid}"]').first
        panel.get_by_text("Permission for office messages", exact=True).click()
        panel.get_by_label("Permission for " + label, exact=True).check()
        note = "Fictional requested channel, authorized in person."
        panel.get_by_label("Communication permission note (optional)", exact=True).fill(note)
        panel.get_by_label("Client agreed to messages through selected channels", exact=True).check()
        panel.get_by_role("button", name="Save client permission", exact=True).click()
        expect(panel.get_by_text("Client permission saved. Nothing was sent.", exact=True)).to_be_visible()
        panel.get_by_role("button", name="Close", exact=True).click()
        row.get_by_role("button", name="Communication choices", exact=True).click()
        panel = page.locator(f'[data-communication="{cid}"]').first
        panel.get_by_text("Permission for office messages", exact=True).click()
        expect(panel.get_by_label("Permission for " + label, exact=True)).to_be_checked()
        expect(panel.get_by_label("Communication permission note (optional)", exact=True)).to_have_value(note)
        assert not (firm["portal"] / "outbox.jsonl").exists()
    finally:
        context.close()
