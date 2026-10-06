"""Optional exact unsaved agreement preview on fictional protected staff UI."""

import pytest
from playwright.sync_api import expect

from test_daily_work_browser import world, app, server, controls, login, open_tab, capture  # noqa: F401 -- pytest fixture registration and helper reexports

FEE = "Fictional professional service fee $100; payable as agreed."


def fill_terms(page):
    page.locator('.filing-picks input[value="i485"]').check()
    page.get_by_role("textbox", name="Professional service fee and payment terms (required)", exact=True).fill(FEE)
    page.get_by_role("textbox", name="Government fees (optional)", exact=True).fill("Government fees are separate.")
    page.get_by_role("textbox", name="Additions (optional)", exact=True).fill("Fictional optional terms for attorney review.")


def preview(page):
    with page.expect_response(lambda r: r.url.endswith("/api/engagement-preview") and r.request.method == "POST", timeout=60000) as response:
        page.locator("#preview-agreement").click()
    assert response.value.status == 200, response.value.text()
    return response.value.json()


@pytest.mark.parametrize("width", [1000, 1400], ids=["1000px", "1400px"])
def test_optional_preview_is_exact_unsaved_and_make_is_separate(browser, server, world, app, width):  # noqa: F811 -- pytest fixture injection
    import engagement
    context, page, errors = login(browser, server, width)
    try:
        open_tab(page, server, "engagement")
        expect(page.locator("#agreement-filing-suggestion")).to_be_visible(timeout=60000)
        assert page.locator(".filing-picks input:checked").count() == 0
        fill_terms(page)
        result = preview(page)
        panel = page.locator("#agreement-preview-panel")
        expect(panel.get_by_role("heading", name="Unsaved agreement preview", exact=True)).to_be_visible()
        expect(panel).to_contain_text("Nothing has been saved, sent or signed")
        paragraphs = panel.locator('[data-language="en"] p').all_text_contents()
        assert paragraphs == result["texts"]["en"]
        assert not engagement.read(world / "case-ana")["letters"]
        capture(page, "agreement-unsaved-preview-" + str(width))
        # Editing invalidates the shown binding without granting an approval.
        gov = page.get_by_role("textbox", name="Government fees (optional)", exact=True)
        gov.fill("Changed fictional government-fee wording.")
        expect(panel).to_contain_text("inputs changed")
        expect(panel.get_by_role("heading", name="Unsaved agreement preview", exact=True)).to_have_count(0)
        result = preview(page)
        with page.expect_response(lambda r: r.url.endswith("/api/engagement") and r.request.method == "POST", timeout=120000) as made:
            page.locator("#make-agreement").click()
        assert made.value.status == 200, made.value.text()
        sent_body = made.value.request.post_data_json
        assert sent_body["preview_sha256"] == result["preview_sha256"]
        saved = engagement.read(world / "case-ana")["letters"][-1]
        assert saved["texts"] == result["texts"]
        assert not saved.get("sent") and not saved.get("signature")
        assert not errors
    finally:
        context.close()


@pytest.mark.parametrize("action", ["edit", "navigate"], ids=["edited-input", "left-agreement"])
def test_late_preview_response_cannot_restore_old_inputs_or_page(browser, server, world, app, action):  # noqa: F811 -- pytest fixture injection
    import engagement
    context, page, errors = login(browser, server, 1000)
    try:
        open_tab(page, server, "engagement")
        expect(page.locator("#preview-agreement")).to_be_visible(timeout=60000)
        fill_terms(page)
        observed = []
        def delay_response(route):
            response = route.fetch()
            assert response.status == 200
            observed.append(response.json()["preview_sha256"])
            if action == "edit":
                page.get_by_role("textbox", name="Government fees (optional)", exact=True).fill("Edited while the preview was pending.")
            else:
                page.get_by_role("navigation", name="Review queues").get_by_role("button", name="Notes and tasks").click()
            route.fulfill(response=response)
        page.route("**/api/engagement-preview", delay_response)
        page.locator("#preview-agreement").click()
        if action == "edit":
            expect(page.locator("#agreement-preview-panel")).to_contain_text("inputs changed", timeout=60000)
            expect(page.locator("#agreement-preview-panel h3")).to_have_count(0)
            expect(page.locator("#preview-agreement")).to_be_enabled()
            expect(page.locator("#make-agreement")).to_be_enabled()
        else:
            expect(page.get_by_role("heading", name="Notes and tasks", exact=True)).to_be_visible(timeout=60000)
            expect(page.locator("#agreement-preview-panel")).to_have_count(0)
        assert observed and not engagement.read(world / "case-ana")["letters"]
        assert not errors
    finally:
        context.close()
