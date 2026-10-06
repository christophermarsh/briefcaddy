"""Actual Chromium assignment flow over small fictional HTTP worlds/cache."""
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_assignment_routes import app, world, server, controls, route_cohort  # noqa: F401 -- pytest fixture registration and helper reexports
from assignment_route_fixtures import PASSWORD
from test_case_assignment_lists import A


def login(browser, server, email="jane@firm.example", width=1400):  # noqa: F811 -- pytest fixture injection
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(server)
    expect(page.locator("#my-cases")).to_be_hidden()
    page.locator("input[name=email]").fill(email)
    page.locator("input[name=password]").fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.get_by_role("heading", name="My cases", exact=True)).to_be_visible(timeout=60000)
    return context, page, errors


def manage(page, case="case-ana", scope="unassigned"):
    page.locator('[data-case-scope="' + scope + '"]').click()
    row = page.locator('tr[data-assignment-row="' + case + '"]')
    expect(row).to_be_visible(timeout=60000)
    row.get_by_role("button", name="Manage assignment", exact=True).click()
    panel = page.locator('[data-assignment-case="' + case + '"]')
    expect(panel).to_be_visible()
    expect(panel.get_by_role("button", name="Retry the same assignment action", exact=True)).to_be_hidden()
    return panel


def shot(page, name):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Page exceeds viewport width"
    if os.environ.get("E2E_SHOTS"):
        folder = Path(os.environ["E2E_SHOTS"]); folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(folder / (name + ".png")), full_page=True)
    assert not any(word in page.locator("body").inner_text() for word in ["undefined", "NaN", "[object Object]"])


@pytest.mark.parametrize("width", [1000, 1400], ids=["1000px", "1400px"])
def test_default_claim_peer_transfer_clear_and_named_history(browser, server, width):  # noqa: F811 -- pytest fixture injection
    first, page, errors = login(browser, server, width=width)
    second = None
    try:
        expect(page.locator("#assignment-empty")).to_contain_text("No cases currently assigned to you")
        panel = manage(page)
        panel.get_by_role("button", name="Claim this case", exact=True).click()
        expect(page.locator("#assignment-status")).to_contain_text("Assignment recorded at revision 1", timeout=60000)
        page.locator("#my-cases").click()
        expect(page.locator('tr[data-assignment-row="case-ana"]')).to_contain_text("Jane Doe")
        shot(page, "assignment-my-" + str(width))
        second, peer, peer_errors = login(browser, server, "kim@firm.example", width)
        panel = manage(peer, scope="all")
        target = panel.get_by_label("New responsible staff member")
        assert target.input_value() == ""  # no implicit assignee
        expect(panel.get_by_role("button", name="Transfer case", exact=True)).to_be_disabled()
        target.select_option(label="Kim Exemplo · paralegal")
        panel.get_by_label("Assignment reason (optional)").fill("Fictional peer transfer")
        panel.get_by_role("button", name="Transfer case", exact=True).click()
        expect(peer.locator("#assignment-status")).to_contain_text("Assignment recorded at revision 2", timeout=60000)
        history = peer.locator('[data-assignment-case="case-ana"] table tr').filter(has_text="Transfer")
        expect(history.locator("td").nth(2)).to_have_text("Kim Exemplo")
        expect(history.locator("td").nth(3)).to_have_text("Jane Doe")
        expect(history.locator("td").nth(4)).to_have_text("Kim Exemplo")
        peer.on("dialog", lambda dialog: dialog.accept())
        peer.get_by_role("button", name="Return to Unassigned", exact=True).click()
        expect(peer.locator("#assignment-status")).to_contain_text("Assignment recorded at revision 3", timeout=60000)
        expect(peer.locator('[data-assignment-case="case-ana"]')).to_contain_text("Current responsible staff: Unassigned")
        expect(peer.locator('[data-assignment-case="case-ana"] table tr')).to_have_count(4)
        expect(peer.get_by_role("button", name="Retry the same assignment action", exact=True)).to_be_hidden()
        shot(peer, "assignment-transfer-clear-history-" + str(width))
        assert not errors and not peer_errors
    finally:
        if second: second.close()
        first.close()


def test_stale_action_refreshes_details_without_automatic_overwrite(browser, server, app):  # noqa: F811 -- pytest fixture injection
    first, page, errors = login(browser, server)
    second = None
    try:
        manage(page).get_by_role("button", name="Claim this case", exact=True).click()
        expect(page.locator("#assignment-status")).to_contain_text("revision 1", timeout=60000)
        second, peer, peer_errors = login(browser, server, "kim@firm.example")
        moved = peer.request.post(server + "/api/assignment", headers={"X-Review-App": "1"}, data={
            "client": "case-ana", "action": "reassign", "revision": 1, "operation_id": "b" * 32,
            "assignee": app.person_id("kim@firm.example")})
        assert moved.status == 200
        page.on("dialog", lambda dialog: dialog.accept())
        with page.expect_response(lambda response: response.url.endswith("/api/assignment") and response.request.method == "POST") as refused:
            page.get_by_role("button", name="Return to Unassigned", exact=True).click()
        assert refused.value.status == 409
        expect(page.locator("#assignment-status")).to_contain_text("Review the fresh details", timeout=60000)
        expect(page.locator('[data-assignment-case="case-ana"] p').filter(has_text="Current responsible staff:")).to_contain_text("Kim Exemplo · revision 2")
        assert page.request.get(server + "/api/assignment?client=case-ana").json()["revision"] == 2
        shot(page, "assignment-stale-requires-new-action")
        moved_again = peer.request.post(server + "/api/assignment", headers={"X-Review-App": "1"}, data={
            "client": "case-ana", "action": "reassign", "revision": 2, "operation_id": "c" * 32,
            "assignee": app.person_id("jane@firm.example")})
        assert moved_again.status == 200
        def conflict_after_navigation(route):
            response = route.fetch()
            assert response.status == 409
            page.locator("#all").click()
            expect(page.get_by_role("heading", name="All clients", exact=True)).to_be_visible(timeout=60000)
            route.fulfill(response=response)
        page.route("**/api/assignment", conflict_after_navigation)
        with page.expect_response(lambda response: response.url.endswith("/api/assignment") and response.request.method == "POST") as delayed:
            page.get_by_role("button", name="Return to Unassigned", exact=True).click()
        assert delayed.value.status == 409
        expect(page.get_by_role("heading", name="All clients", exact=True)).to_be_visible()
        expect(page.locator("#assignment-panel")).to_have_count(0)
        assert page.request.get(server + "/api/assignment?client=case-ana").json()["revision"] == 3
        shot(page, "assignment-conflict-preserves-later-navigation")
        assert not errors and not peer_errors
    finally:
        if second: second.close()
        first.close()


def test_lost_commit_response_explicit_same_operation_retry(browser, server):  # noqa: F811 -- pytest fixture injection
    context, page, errors = login(browser, server)
    posts = []
    try:
        panel = manage(page)
        def lose_first_response(route):
            if route.request.method != "POST":
                route.continue_(); return
            posts.append(route.request.post_data_json)
            if len(posts) == 1:
                committed = route.fetch()
                assert committed.status == 200
                route.abort("failed")
            else:
                route.continue_()
        page.route("**/api/assignment", lose_first_response)
        panel.get_by_role("button", name="Claim this case", exact=True).click()
        expect(page.locator("#assignment-status")).to_contain_text("outcome may be unknown", timeout=60000)
        assert len(posts) == 1
        expect(page.get_by_role("button", name="Retry the same assignment action", exact=True)).to_be_visible()
        page.get_by_role("button", name="Retry the same assignment action", exact=True).click()
        expect(page.locator("#assignment-status")).to_contain_text("Assignment recorded at revision 1", timeout=60000)
        expect(page.get_by_role("button", name="Retry the same assignment action", exact=True)).to_be_hidden()
        assert len(posts) == 2 and posts[0] == posts[1]
        state = page.request.get(server + "/api/assignment?client=case-ana").json()
        assert state["revision"] == len(state["history"]) == 1
        shot(page, "assignment-explicit-retry-after-response-loss")
        assert not errors
    finally: context.close()


def test_keyboard_remote_picker_does_not_fetch_unbounded_clients(browser, server):  # noqa: F811 -- pytest fixture injection
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    page = context.new_page(); requests = []; errors = []
    page.on("request", lambda request: requests.append(request.url))
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(server)
        page.locator("input[name=email]").fill("jane@firm.example")
        page.locator("input[name=password]").fill(PASSWORD)
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.get_by_role("heading", name="My cases", exact=True)).to_be_visible(timeout=60000)
        page.locator("#client").focus(); page.locator("#client").press("Enter")
        search = page.get_by_label("Find a client", exact=True)
        search.fill("case-ana")
        expect(page.locator("#client-pick li")).to_have_count(1)
        search.press("Enter")
        expect(page.locator("#client")).to_have_value("case-ana", timeout=60000)
        expect(page.locator("#client-pick")).to_have_count(0)
        assert not any("/api/clients" in url for url in requests)
        assert any("/api/case-list?" in url and "case-ana" in url for url in requests)
        shot(page, "assignment-keyboard-bounded-picker")
        assert not errors
    finally: context.close()


def test_2000_folders_actual_browser_bounded_paging_and_hidden_search(browser, server, app, route_cohort, monkeypatch):  # noqa: F811 -- pytest fixture injection
    roster, root, calls, loader, seeded, *_ = route_cohort
    app.roster = roster; app.data_root = root
    app.accounts.change_password(A, app.accounts.add(A, "Fictional Alpha", "paralegal"), PASSWORD)
    context, page, errors = login(browser, server, A)
    try:
        assert page.locator("#client option").count() <= 51
        with page.expect_response(lambda response: "/api/case-list?" in response.url and "scope=all" in response.url) as listed:
            page.locator('[data-case-scope="all"]').click()
        data = listed.value.json()
        assert data["total"] == 1900 and len(data["clients"]) == data["size"] == 50
        expect(page.locator("#assigned-case-rows tr[data-assignment-row]")).to_have_count(50)
        expect(page.get_by_text("1 to 50 of 1900", exact=True)).to_be_visible()
        page.get_by_role("button", name="Next", exact=True).click()
        expect(page.get_by_text("51 to 100 of 1900", exact=True)).to_be_visible()
        search = page.get_by_label("Search cases by name or ID", exact=True)
        search.fill("fictional-0010"); search.press("Enter")
        expect(page.locator("#assignment-empty")).to_have_text("No accessible cases match these filters.")
        hidden_text = page.locator("#case").inner_text()
        page.get_by_label("Search cases by name or ID", exact=True).fill("fictional-missing")
        page.get_by_role("button", name="Search cases", exact=True).click()
        expect(page.locator("#assignment-empty")).to_have_text("No accessible cases match these filters.")
        assert page.locator("#case").inner_text() == hidden_text
        shot(page, "assignment-2000-bounded-hidden-search")
        assert not errors
    finally: context.close()
