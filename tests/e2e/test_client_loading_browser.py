"""A slow communication read must not block the case or erase unsaved edits."""
# ruff: noqa: F811 -- canonical fixtures use pytest injection
import json

from playwright.sync_api import expect
from test_assignment_routes import app, controls, server, world  # noqa: F401
from test_client_picker_refresh_browser import login, search


def choose(page, client):
    search(page, client)
    page.locator("#client-pick .pick-item").filter(has_text=client).click()
    expect(page.locator("#side")).to_contain_text("Check answers", timeout=60000)


def test_case_renders_before_communication_and_refresh_preserves_edits(browser, server):
    context, page = login(browser, server)
    held = []
    try:
        page.route("**/api/communication?client=case-ana", lambda route: held.append(route))
        choose(page, "case-ana")
        expect(page.locator("#main")).not_to_contain_text("Loading case-ana")
        assert held
        note = page.locator("article.card input.note").first
        expect(note).to_be_visible()
        note.fill("Unsaved fictional staff note")
        held.pop().fulfill(status=200, content_type="application/json", body=json.dumps({"can_send": True}))
        expect(page.get_by_role("button", name="Ask the client", exact=True).first).to_be_visible()
        expect(note).to_have_value("Unsaved fictional staff note")
    finally:
        for route in held:
            route.abort()
        context.close()


def test_previous_clients_late_communication_does_not_enable_current_clients_actions(browser, server):
    context, page = login(browser, server)
    held = []
    try:
        page.route("**/api/communication?client=case-ana", lambda route: held.append(route))
        choose(page, "case-ana")
        assert held
        choose(page, "case-bia")
        held.pop().fulfill(status=200, content_type="application/json", body=json.dumps({"can_send": True}))
        page.wait_for_timeout(150)
        assert page.url.endswith("#case-bia")
        expect(page.get_by_role("button", name="Ask the client", exact=True)).to_have_count(0)
    finally:
        for route in held:
            route.abort()
        context.close()
