"""List navigation clears case context and paints cached rows before refresh."""
# ruff: noqa: F811 -- canonical fixtures use pytest injection
import json

from playwright.sync_api import expect
from test_assignment_routes import app, controls, server, world  # noqa: F401
from test_client_picker_refresh_browser import login, search


def test_all_clients_clears_sidebar_reuses_rows_and_ignores_late_navigation(browser, server):
    context, page = login(browser, server)
    held = []
    try:
        result = page.request.get(server + "/api/overview").json()
        assert result["clients"]

        def open_case():
            search(page, "case-ana")
            page.locator("#client-pick .pick-item").filter(has_text="case-ana").click()
            expect(page.locator("#side")).to_contain_text("Check answers", timeout=60000)

        open_case()
        page.route("**/api/overview*", lambda route: held.append(route))
        page.locator("#all").click()
        expect(page.locator("#case h1")).to_have_text("All clients", timeout=1000)
        expect(page.locator("#side")).to_have_text("", timeout=1000)
        expect(page.locator("#main")).to_contain_text("Loading the client list")
        assert held
        held.pop().fulfill(status=200, content_type="application/json", body=json.dumps(result))
        expect(page.locator("#client-rows tr[data-client]")).to_have_count(len(result["clients"]))

        open_case()
        page.locator("#all").click()
        expect(page.locator("#client-rows tr[data-client]")).to_have_count(len(result["clients"]), timeout=1000)
        expect(page.locator("#side").get_by_role("button", name="Check answers", exact=False)).to_have_count(0)
        expect(page.locator("#case")).to_contain_text("Updating the client list")
        assert held
        old = held.pop()
        open_case()
        stale = {**result, "clients": [{**row, "summary": {**row.get("summary", {}), "name": "LATE STALE READING"}} for row in result["clients"]]}
        old.fulfill(status=200, content_type="application/json", body=json.dumps(stale))
        page.wait_for_timeout(150)
        expect(page.locator("#side")).to_contain_text("Check answers")
        expect(page.locator("#case h1")).not_to_have_text("All clients")
        page.locator("#all").click()
        expect(page.locator("#client-rows")).not_to_contain_text("LATE STALE READING")
        expect(page.locator("#client-rows tr[data-client]")).to_have_count(len(result["clients"]), timeout=1000)
    finally:
        for route in held:
            route.abort()
        context.close()
