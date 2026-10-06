"""Real staff Chromium over fictional configuration/provider/originals."""
import hashlib
import math
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect
import events
import firmsecrets
import jobs
import second_factor
from connectors import drive_settings
from drive_workflow_fixtures import source_firm, world, drive_world, configured_world, app, server, PASSWORD, ATTORNEY  # noqa: F401 -- pytest fixture registration and helper reexports
from test_drive_selection_routes import provider, configure, REMOTE  # noqa: F401 -- pytest fixture registration and helper reexports


@pytest.fixture
def browser_provider(provider, configured_world, monkeypatch):  # noqa: F811 -- pytest fixture injection
    # Settings also inspects unrelated connector credential availability. None
    # are configured in this fixture; only the installed Drive adapter receives
    # the fictional key under its explicit own-root/env={} request.
    original = firmsecrets.get
    def isolated(name, **kw):
        if name == "gdrive.service_account" and kw.get("env") == {} and kw.get("data_root") == configured_world["scope"].data:
            return original(name, **kw)
        return None
    monkeypatch.setattr(firmsecrets, "get", isolated)
    return provider


def login(browser, server, width=1000, email="jane@firm.example"):  # noqa: F811 -- pytest fixture injection
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    page.set_default_timeout(15000)  # Routine control mistakes fail boundedly; startup/reading waits stay explicit.
    errors, requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    page.goto(server)
    page.locator("input[name=email]").fill(email)
    page.locator("input[name=password]").fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    if email == ATTORNEY:
        expect(page.locator("input[name=code]")).to_be_visible(timeout=60000)
        page.locator("input[name=code]").fill(second_factor._RECOVERY[email].pop())
        # First sign-in legitimately finishes its real Getting started flow
        # after loadClients. Wait for that page's own seen POST before navigating.
        with page.expect_response(lambda r: r.url.endswith("/api/getting-started") and r.request.method == "POST", timeout=60000) as seen:
            page.locator('form button[type="submit"]').click()
        assert seen.value.status == 200
        expect(page.locator("#main").get_by_role("heading", name="Getting started", exact=True)).to_be_visible(timeout=60000)
    expect(page.locator("#my-cases")).to_be_visible(timeout=60000)
    page.locator("#my-cases").click()
    expect(page.get_by_role("heading", name="My cases", exact=True)).to_be_visible(timeout=60000)
    return context, page, errors, requests


def documents_page(page, server, w):  # noqa: F811 -- pytest fixture injection
    page.goto(server + "?tab=documents#" + w["client"])
    panel = page.locator('[data-selected-drive="' + w["client"] + '"]')
    expect(panel).to_have_count(1, timeout=60000)
    expect(panel.get_by_role("button", name="Preview configured Drive selection", exact=True)).to_be_enabled(timeout=60000)
    return panel


def capture(page, panel, name):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if os.environ.get("E2E_SHOTS"):
        folder = Path(os.environ["E2E_SHOTS"]); folder.mkdir(parents=True, exist_ok=True)
        original = page.viewport_size
        header_height = page.locator(".topbar").bounding_box()["height"]
        height = max(original["height"], math.ceil(panel.bounding_box()["height"] + header_height + 48))
        assert height <= 2200  # Bounded fixture capture, not a massive full Settings page.
        try:
            page.set_viewport_size({"width": original["width"], "height": height})
            panel.evaluate("element => window.scrollTo(0, Math.max(0, scrollY + element.getBoundingClientRect().top - document.querySelector('.topbar').getBoundingClientRect().height - 16))")
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            assert panel.evaluate("element => element.getBoundingClientRect().top >= document.querySelector('.topbar').getBoundingClientRect().bottom")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(folder / (name + ".png")))  # Live header remains visible; no screenshot CSS masking.
        finally:
            page.set_viewport_size(original)
    assert not any(value in panel.inner_text() for value in ("undefined", "NaN", "[object Object]"))


@pytest.mark.parametrize("width", [1000, 1400], ids=["1000px", "1400px"])
def test_attorney_settings_bounded_case_choice_pending_and_explicit_audit_retry(browser, app, server, configured_world, browser_provider, monkeypatch, width):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    context, page, errors, requests = login(browser, server, width, ATTORNEY)
    try:
        page.locator("#settings").click()
        expect(page.locator("#case").get_by_role("heading", name="Settings", exact=True)).to_be_visible(timeout=60000)
        panel = page.locator("#drive-settings")
        expect(panel.locator("[data-drive-settings-status]")).to_contain_text("Saved revision 0", timeout=60000)
        expect(panel.get_by_role("button", name="Retry audit", exact=True)).to_be_hidden()
        panel.get_by_role("textbox", name="Selected Drive root folder ID", exact=True).fill("mocked-configured-root")
        panel.get_by_role("textbox", name="Remote folder ID to add", exact=True).fill(REMOTE)
        panel.get_by_role("combobox", name="Existing case for Drive folder", exact=True).click()
        page.get_by_role("searchbox", name="Find a client", exact=True).fill(w["client"])
        result = page.get_by_role("listbox", name="Clients", exact=True).get_by_role("option").filter(has_text=w["client"])
        expect(result).to_have_count(1, timeout=60000)
        result.click()
        panel.get_by_role("button", name="Add folder and case pair", exact=True).click()
        expect(panel.get_by_role("textbox", name="Selected Drive folder and case pairs", exact=True)).to_have_value(REMOTE + " -> " + w["client"])
        assert any("/api/case-list?" in url and "scope=all" in url and "size=50" in url for url in requests)
        assert not any(url.split("?", 1)[0].endswith("/api/clients") for url in requests)
        assert browser_provider["calls"] == [] and not drive_settings.paths(w["scope"])[0].exists()
        original = events.record
        monkeypatch.setattr(events, "record", lambda *_a, **_k: None)
        with page.expect_response(lambda r: r.url.endswith("/api/drive-settings") and r.request.method == "POST") as response:
            panel.get_by_role("button", name="Save Drive settings", exact=True).click()
        assert response.value.status == 200, response.value.text()
        expect(panel.locator("[data-drive-settings-status]")).to_contain_text("audit is pending")
        expect(panel.get_by_role("button", name="Retry audit", exact=True)).to_be_visible()
        expect(panel.get_by_role("textbox", name="Selected Drive root folder ID", exact=True)).to_be_disabled()
        saved = drive_settings.read_record(w["scope"])
        capture(page, panel, "drive-settings-audit-pending-" + str(width))
        monkeypatch.setattr(events, "record", original)
        with page.expect_response(lambda r: r.url.endswith("/api/drive-settings-audit")) as response:
            panel.get_by_role("button", name="Retry audit", exact=True).click()
        assert response.value.status == 200
        expect(panel.locator("[data-drive-settings-status]")).to_contain_text("Audit is current")
        expect(panel.get_by_role("button", name="Retry audit", exact=True)).to_be_hidden()
        repaired = drive_settings.read_record(w["scope"])
        repaired["operation"]["audit"] = saved["operation"]["audit"]
        assert repaired == saved and browser_provider["calls"] == [] and not list(w["scope"].queue.glob("*.json"))
        capture(page, panel, "drive-settings-audit-current-" + str(width))
        assert not errors
    finally:
        context.close()


@pytest.mark.parametrize("width", [1000, 1400], ids=["1000px", "1400px"])
def test_case_preview_lost_response_same_attempt_and_actual_read_outcome(browser, app, server, configured_world, browser_provider, width):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    configure(w)
    context, page, errors, requests = login(browser, server, width)
    try:
        panel = documents_page(page, server, w)
        expect(panel.get_by_role("button", name="Process the shown selection", exact=True)).to_be_disabled()
        expect(panel.get_by_role("button", name="Stop waiting", exact=True)).to_be_hidden()
        assert browser_provider["calls"] == []
        with page.expect_response(lambda r: r.url.endswith("/api/drive-preview")) as response:
            panel.get_by_role("button", name="Preview configured Drive selection", exact=True).click()
        assert response.value.status == 200
        expect(panel.locator("[data-drive-preview]")).to_contain_text("Drive evidence.pdf")
        expect(panel.locator("[data-drive-preview]")).not_to_contain_text("Other fictional private")
        assert browser_provider["downloads"] == 0 and not list(w["scope"].queue.glob("*.json"))
        capture(page, panel, "drive-current-case-readonly-preview-" + str(width))
        committed, bodies = [], []
        def lost(route):
            bodies.append(route.request.post_data_json)
            if committed:
                route.continue_()
            else:
                actual = route.fetch()
                assert actual.status == 200, actual.text()
                committed.append(actual.json()); route.abort("failed")
        page.route("**/api/drive-enqueue", lost)
        panel.get_by_role("button", name="Process the shown selection", exact=True).click()
        expect(panel.locator("[data-drive-status]")).to_contain_text("may already have been received", timeout=60000)
        jid = committed[0]["jobs"][REMOTE]["id"]
        expect(panel.get_by_role("button", name="Retry the same processing attempt", exact=True)).to_be_enabled()
        panel.get_by_role("button", name="Check Drive outcome", exact=True).click()
        expect(panel.locator("[data-drive-jobs]")).to_contain_text("cannot prove which belongs to the unknown attempt")
        expect(panel.locator("[data-drive-status]")).not_to_contain_text("were read")
        with page.expect_response(lambda r: r.url.endswith("/api/drive-enqueue")) as response:
            panel.get_by_role("button", name="Retry the same processing attempt", exact=True).click()
        assert response.value.status == 200 and response.value.json()["jobs"][REMOTE]["id"] == jid
        assert bodies[0] == bodies[1]
        assert len([j for j in jobs.jobs(w["scope"].queue, client=w["client"]) if j["kind"] == "drive_intake"]) == 1
        expect(panel.locator("[data-drive-status]")).to_contain_text("worker availability was not confirmed")
        assert jobs.work(w["scope"].cases, w["scope"].portal, once=True, jobs_root=w["scope"].queue, use_policies=False) >= 1
        assert jobs.get(w["scope"].queue, jid)["state"] == "done"
        panel.get_by_role("button", name="Check Drive outcome", exact=True).click()
        expect(page.locator('[data-selected-drive="' + w["client"] + '"] [data-drive-status]')).to_contain_text("were read", timeout=60000)
        panel = page.locator('[data-selected-drive="' + w["client"] + '"]')
        expect(panel).to_have_count(1)
        # docName intentionally hides the extension in the visible source label.
        # Prove the exact retained source through its current hash-bound link.
        expect(page.locator("#main")).to_contain_text("Drive evidence [fictionalpdf]", timeout=60000)
        links = page.locator("#documents a[href^='/api/file?']").evaluate_all("nodes => nodes.map(node => node.getAttribute('href'))")
        retained = [(href, parse_qs(urlparse(href).query)) for href in links
                    if parse_qs(urlparse(href).query).get("doc") == ["Drive evidence [fictionalpdf].pdf"]]
        assert len(retained) == 1, links
        href, binding = retained[0]
        expected_sha = hashlib.sha256(browser_provider["pdf"]).hexdigest()
        assert binding["client"] == [w["client"]] and binding["expected_sha256"] == [expected_sha]
        original = page.request.get(server + href)
        assert original.status == 200 and hashlib.sha256(original.body()).hexdigest() == expected_sha
        expect(panel).to_contain_text("not an approval or a sent packet")
        assert browser_provider["downloads"] == 1 and not errors
        capture(page, panel, "drive-current-case-read-held-" + str(width))
        configure(w, {}, revision=1, attempt="f" * 32)
        panel.get_by_role("button", name="Reload configured selection", exact=True).click()
        expect(panel.locator("[data-drive-status]")).to_contain_text("earlier configured selection")
        expect(panel.locator("[data-drive-status]")).to_contain_text("does not establish a read outcome for its new selection")
        expect(panel.get_by_role("button", name="Preview configured Drive selection", exact=True)).to_be_disabled()
        assert browser_provider["downloads"] == 1
    finally:
        context.close()


@pytest.mark.parametrize("action", ["navigate", "configuration"], ids=["late-preview-navigation", "stale-configuration"])
def test_preview_navigation_and_changed_configuration_cannot_process_old_selection(browser, app, server, configured_world, browser_provider, action):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    configure(w)
    context, page, errors, _ = login(browser, server)
    try:
        panel = documents_page(page, server, w)
        if action == "navigate":
            def delay(route):
                result = None
                try:
                    result = route.fetch()
                    assert result.status == 200
                    # renderSide adds its actual arrow span to the accessible name.
                    page.get_by_role("navigation", name="Review queues").get_by_role("button", name="Notes and tasks", exact=False).click()
                finally:
                    if result is not None:
                        route.fulfill(response=result)
                    else:
                        route.abort("failed")
            page.route("**/api/drive-preview", delay)
            try:
                panel.get_by_role("button", name="Preview configured Drive selection", exact=True).click()
                expect(page.get_by_role("heading", name="Notes and tasks", exact=True)).to_be_visible(timeout=60000)
                expect(page.locator("[data-selected-drive]")).to_have_count(0)
            finally:
                page.unroute("**/api/drive-preview", delay)
        else:
            panel.get_by_role("button", name="Preview configured Drive selection", exact=True).click()
            expect(panel.get_by_role("button", name="Process the shown selection", exact=True)).to_be_enabled(timeout=60000)
            # Commit a legitimate job, then revoke its mapping before the
            # installed worker executes. Check must report incomplete, not queued/read.
            with page.expect_response(lambda r: r.url.endswith("/api/drive-enqueue")) as received:
                panel.get_by_role("button", name="Process the shown selection", exact=True).click()
            assert received.value.status == 200
            jid = received.value.json()["jobs"][REMOTE]["id"]
            configure(w, {}, revision=1, attempt="e" * 32)
            jobs.work(w["scope"].cases, w["scope"].portal, once=True, jobs_root=w["scope"].queue, use_policies=False)
            assert jobs.get(w["scope"].queue, jid)["state"] == "failed"
            panel.get_by_role("button", name="Check Drive outcome", exact=True).click()
            expect(panel.locator("[data-drive-status]")).to_contain_text("incomplete or stopped")
            expect(panel.locator("[data-drive-status]")).not_to_contain_text("was queued")
            expect(panel.locator("[data-drive-status]")).not_to_contain_text("were read")
            assert browser_provider["downloads"] == 0
            capture(page, panel, "drive-current-case-processing-incomplete-1000")
            page.once("dialog", lambda dialog: dialog.accept())
            panel.get_by_role("button", name="Remove local Drive handle", exact=True).click()
            configure(w, revision=2, attempt="f" * 32)
            panel.get_by_role("button", name="Reload configured selection", exact=True).click()
            expect(panel.get_by_role("button", name="Preview configured Drive selection", exact=True)).to_be_enabled()
            panel.get_by_role("button", name="Preview configured Drive selection", exact=True).click()
            expect(panel.get_by_role("button", name="Process the shown selection", exact=True)).to_be_enabled()
            configure(w, {}, revision=3, attempt="d" * 32)
            with page.expect_response(lambda r: r.url.endswith("/api/drive-enqueue")) as response:
                panel.get_by_role("button", name="Process the shown selection", exact=True).click()
            assert response.value.status == 400
            expect(panel.locator("[data-drive-status]")).to_contain_text("request was refused")
            assert len([j for j in jobs.jobs(w["scope"].queue, client=w["client"]) if j["kind"] == "drive_intake"]) == 1 and browser_provider["downloads"] == 0
            page.once("dialog", lambda dialog: dialog.accept())
            panel.get_by_role("button", name="Remove local Drive handle", exact=True).click()
            panel.get_by_role("button", name="Reload configured selection", exact=True).click()
            expect(panel.get_by_role("button", name="Preview configured Drive selection", exact=True)).to_be_disabled()
            expect(panel.locator("[data-drive-status]")).to_contain_text("An attorney must configure")
        assert not errors
    finally:
        context.close()
