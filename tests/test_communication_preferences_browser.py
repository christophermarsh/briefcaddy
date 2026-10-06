"""Fictional draft recovery, refresh and independently reopened staff sessions."""
# ruff: noqa: F811
import os
import pytest
from playwright.sync_api import expect
from test_prospects import firm, server  # noqa: F401
from test_staff_consent_browser import staff_chromium  # noqa: F401
from test_staff_consent_http import client

pytestmark = pytest.mark.skipif(not os.environ.get("E2E"), reason="E2E=1 enables sandboxed Chromium")


def open_panel(page, server, cid):
    page.goto("about:blank")
    page.goto(server["base"] + "/#all")
    row = page.locator(f'#client-rows tr.row[data-client="{cid}"]')
    row.locator("summary").click()
    row.get_by_role("button", name="Communication choices", exact=True).click()
    panel = page.locator(f'[data-communication="{cid}"]').first
    panel.get_by_text("Permission for office messages", exact=True).click()
    return panel


def context(browser, server, *, cookie=None):
    result = browser.new_context(viewport={"width": 1280, "height": 900})
    name, value = (cookie or server["jane"]).split("=", 1)
    result.add_cookies([{"name": name, "value": value, "url": server["base"]}])
    return result


@pytest.mark.parametrize("label", ["email", "text messages", "whatsapp"])
def test_failed_save_retains_draft_on_reopen_refresh_and_saved_choices_in_new_session(staff_chromium, server, firm, label):
    cid = client(server, name="Fictional Draft Client")
    firm["store"].update_profile(cid, phone="+15550108976")
    channel_label = "Permission for " + label
    first = context(staff_chromium, server)
    second = None
    try:
        page = first.new_page()
        panel = open_panel(page, server, cid)
        panel.get_by_label(channel_label, exact=True).check()
        panel.get_by_label("Communication permission note (optional)", exact=True).fill("Unsaved fictional instruction")
        panel.get_by_label("Client agreed to messages through selected channels", exact=True).check()
        page.route("**/api/communication", lambda route: route.fulfill(status=400, content_type="application/json", body='{"error":"Fictional interrupted save; retry."}'), times=1)
        panel.get_by_role("button", name="Save client permission", exact=True).click()
        expect(panel.get_by_role("alert").filter(has_text="Fictional interrupted save")).to_be_visible()
        panel = open_panel(page, server, cid)
        expect(panel.get_by_label(channel_label, exact=True)).to_be_checked()
        expect(panel.get_by_label("Communication permission note (optional)", exact=True)).to_have_value("Unsaved fictional instruction")
        expect(panel.get_by_label("Client agreed to messages through selected channels", exact=True)).not_to_be_checked()
        panel.get_by_label("Client agreed to messages through selected channels", exact=True).check()
        panel.get_by_role("button", name="Save client permission", exact=True).click()
        expect(panel.get_by_text("Client permission saved. Nothing was sent.", exact=True)).to_be_visible()
        expect(panel.locator("[data-saved-communication]")).to_contain_text("Unsaved fictional instruction")
        expect(panel.locator("[data-saved-communication]")).to_contain_text("sms" if label == "text messages" else label)
        page.reload()
        panel = open_panel(page, server, cid)
        expect(panel.get_by_label(channel_label, exact=True)).to_be_checked()
        session = server["accounts"].session_for("jane@firm.example", how="test")[0]
        cookie_name, original = server["jane"].split("=", 1)
        assert session != original
        second = context(staff_chromium, server, cookie=cookie_name + "=" + session)
        other = second.new_page()
        remote = open_panel(other, server, cid)
        expect(remote.get_by_label(channel_label, exact=True)).to_be_checked()
        expect(remote.get_by_label("Communication permission note (optional)", exact=True)).to_have_value("Unsaved fictional instruction")
        # An independent concurrent edit must reject the old displayed revision.
        remote.get_by_label("Communication permission note (optional)", exact=True).fill("Second staff session")
        remote.get_by_label("Client agreed to messages through selected channels", exact=True).check()
        remote.get_by_role("button", name="Save client permission", exact=True).click()
        expect(remote.get_by_text("Client permission saved. Nothing was sent.", exact=True)).to_be_visible()
        panel.get_by_label("Communication permission note (optional)", exact=True).fill("Older local draft")
        panel.get_by_label("Client agreed to messages through selected channels", exact=True).check()
        panel.get_by_role("button", name="Save client permission", exact=True).click()
        expect(panel.get_by_role("alert").filter(has_text="choices changed")).to_be_visible()
        expect(panel.get_by_label("Communication permission note (optional)", exact=True)).to_have_value("Older local draft")
        panel = open_panel(page, server, cid)
        expect(panel.locator("[data-saved-communication]")).to_contain_text("Second staff session")
        expect(panel.get_by_label("Communication permission note (optional)", exact=True)).to_have_value("Older local draft")
        assert not (firm["portal"] / "outbox.jsonl").exists()
    finally:
        first.close()
        if second:
            second.close()


def test_older_failed_read_and_delayed_save_cannot_replace_newer_communication_draft(staff_chromium, server, firm):
    cid = client(server, name="Fictional Delayed Choices")
    owner = context(staff_chromium, server)
    pending = []
    try:
        page = owner.new_page()
        panel = open_panel(page, server, cid)
        panel.get_by_label("Communication permission note (optional)", exact=True).evaluate("node => node.dataset.oldForm = 'true'")
        def hold_read(route):
            pending.append(route)
            page.evaluate("window.__communicationReadHeld = true")
        page.route("**/api/communication?*", hold_read, times=1)
        panel.get_by_role("button", name="Reload saved communication choices", exact=True).click()
        page.wait_for_function("() => window.__communicationReadHeld === true")
        with page.expect_response(lambda r: "/api/communication?" in r.url and r.status == 200):
            panel.get_by_role("button", name="Reload saved communication choices", exact=True).click()
        panel = page.locator(f'[data-communication="{cid}"]').first
        expect(panel.locator("[data-old-form]")).to_have_count(0)
        panel.get_by_text("Permission for office messages", exact=True).click()
        panel.get_by_label("Communication permission note (optional)", exact=True).fill("New draft after newer successful read")
        with page.expect_response(lambda r: "/api/communication?" in r.url and r.status == 400):
            pending.pop().fulfill(status=400, content_type="application/json", body='{"error":"Older fictional read failed"}')
        page.wait_for_timeout(300)  # let the rejected fetch reach the draw catch
        expect(panel.get_by_label("Communication permission note (optional)", exact=True)).to_have_value("New draft after newer successful read")
        expect(panel).not_to_contain_text("Older fictional read failed")

        def hold_save(route):
            response = route.fetch()
            pending.append((route, response))
            page.evaluate("window.__communicationSaveHeld = true")
        page.route("**/api/communication", hold_save, times=1)
        panel.get_by_label("Permission for email", exact=True).check()
        panel.get_by_label("Client agreed to messages through selected channels", exact=True).check()
        panel.get_by_role("button", name="Save client permission", exact=True).click()
        page.wait_for_function("() => window.__communicationSaveHeld === true")
        panel.get_by_label("Communication permission note (optional)", exact=True).fill("Newer note while successful save response waits")
        route, response = pending.pop()
        route.fulfill(response=response)
        expect(panel.get_by_text("Previous choices saved. Newer edits are still unsaved.", exact=True)).to_be_visible()
        expect(panel.locator("[data-saved-communication]")).to_contain_text("New draft after newer successful read")
        expect(panel.get_by_label("Client agreed to messages through selected channels", exact=True)).not_to_be_checked()
        panel = open_panel(page, server, cid)
        expect(panel.get_by_label("Communication permission note (optional)", exact=True)).to_have_value("Newer note while successful save response waits")
        saved = page.request.get(server["base"] + "/api/communication?client=" + cid).json()["preferences"]
        assert saved["note"] == "New draft after newer successful read"
        assert not (firm["portal"] / "outbox.jsonl").exists()
    finally:
        owner.close()
