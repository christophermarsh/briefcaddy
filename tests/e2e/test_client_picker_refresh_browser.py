"""Protected picker retries do not revive stale choices or reveal pending IDs."""
# ruff: noqa: F811 -- imported canonical fixtures use pytest injection
import json
from urllib.parse import parse_qs, urlparse

from assignment_route_fixtures import PASSWORD
from playwright.sync_api import expect
from test_assignment_routes import (  # noqa: F401 -- fixtures
    app,
    controls,
    server,
    world,
)


def login(browser, server):
    context = browser.new_context()
    page = context.new_page()
    page.goto(server)
    page.locator("input[name=email]").fill("jane@firm.example")
    page.locator("input[name=password]").fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.get_by_role("heading", name="My cases", exact=True)).to_be_visible(timeout=60000)
    return context, page


def search(page, text):
    page.locator("#client").click()
    page.get_by_label("Find a client", exact=True).fill(text)


def test_empty_refresh_converges_without_cached_options_and_stops_for_hidden_or_absent(browser, server):
    context, page = login(browser, server)
    calls = {}
    try:
        real = page.request.get(server + "/api/case-list?scope=all&ended=all&q=case-ana").json()
        assert real["total"] == 1
        for query in ("case-rosa", "fictional-does-not-exist"):
            assert page.request.get(server + "/api/case-list?scope=all&ended=all&q=" + query).json()["total"] == 0
        empty = real | {"clients": [], "total": 0}
        def intercepted(route):
            query = parse_qs(urlparse(route.request.url).query).get("q", [""])[0]
            calls[query] = calls.get(query, 0) + 1
            result = real if query == "case-ana" and calls[query] == 4 else empty
            route.fulfill(status=200, content_type="application/json", body=json.dumps(result))
        page.route("**/api/case-list?**", intercepted)
        search(page, "case-ana")
        expect(page.locator("#client-pick .pick-item")).to_have_count(0)
        expect(page.locator("#client-pick")).to_contain_text("Searching accessible cases")
        expect(page.locator("#client-pick .pick-item")).to_have_count(1, timeout=10000)
        assert calls["case-ana"] == 4
        page.get_by_label("Find a client", exact=True).press("Escape")
        for query in ("case-rosa", "fictional-does-not-exist"):
            search(page, query)
            expect(page.locator("#client-pick")).to_contain_text("No client matches", timeout=10000)
            expect(page.locator("#client-pick .pick-item")).to_have_count(0)
            expect(page.locator("#client-pick")).to_contain_text("current accessible results. Try again shortly")
            assert calls[query] == 4
            if query == "fictional-does-not-exist":
                page.get_by_role("button", name="Search again", exact=True).click()
                expect(page.locator("#client-pick")).to_contain_text("Searching accessible cases")
                expect(page.locator("#client-pick")).to_contain_text("No client matches", timeout=10000)
                assert calls[query] == 8
            page.get_by_label("Find a client", exact=True).press("Escape")
        assert not page.locator("#client-pick").count()
    finally:
        context.close()


def test_stale_query_navigation_and_closed_picker_cancel_delayed_replies(browser, server):
    context, page = login(browser, server)
    held, calls = [], []
    try:
        real = page.request.get(server + "/api/case-list?scope=all&ended=all&q=case-ana").json()
        empty = real | {"clients": [], "total": 0}
        def intercepted(route):
            query = parse_qs(urlparse(route.request.url).query).get("q", [""])[0]
            calls.append(query)
            if query in ("old-query", "navigation-query", "closed-query", "actor-query"):
                held.append(route)
            else:
                route.fulfill(status=200, content_type="application/json", body=json.dumps(empty))
        page.route("**/api/case-list?**", intercepted)
        with page.expect_request(lambda request: "q=old-query" in request.url):
            search(page, "old-query")
        page.wait_for_timeout(50)
        assert held
        page.get_by_label("Find a client", exact=True).fill("fictional-absent")
        held.pop().fulfill(status=200, content_type="application/json", body=json.dumps(real))
        expect(page.locator("#client-pick")).to_contain_text("No client matches", timeout=10000)
        expect(page.locator("#client-pick .pick-item")).to_have_count(0)
        page.get_by_label("Find a client", exact=True).press("Escape")
        with page.expect_request(lambda request: "q=navigation-query" in request.url):
            search(page, "navigation-query")
        page.wait_for_timeout(50)
        assert held
        page.locator("#settings").focus()
        page.keyboard.press("Enter")
        held.pop().abort("failed")
        expect(page.locator("#client-pick")).to_have_count(0)
        with page.expect_request(lambda request: "q=closed-query" in request.url):
            search(page, "closed-query")
        page.wait_for_timeout(50)
        assert held
        page.get_by_label("Find a client", exact=True).press("Escape")
        held.pop().fulfill(status=200, content_type="application/json", body=json.dumps(real))
        expect(page.locator("#client-pick")).to_have_count(0)
        assert calls.count("closed-query") == calls.count("navigation-query") == 1
        search(page, "cancel-empty-retry")
        with page.expect_response(lambda response: "q=cancel-empty-retry" in response.url):
            page.wait_for_timeout(300)
        page.locator("#all").focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(700)
        assert calls.count("cancel-empty-retry") == 1
        expect(page.locator("#client-pick")).to_have_count(0)
        with page.expect_request(lambda request: "q=actor-query" in request.url):
            search(page, "actor-query")
        page.wait_for_timeout(50)
        page.get_by_role("button", name="Sign out", exact=True).focus()
        page.keyboard.press("Enter")
        expect(page.locator("input[name=email]")).to_be_visible(timeout=10000)
        held.pop().fulfill(status=200, content_type="application/json", body=json.dumps(real))
        expect(page.locator("#client-pick")).to_have_count(0)
    finally:
        context.close()
