"""Actual retained-source browser flows; opt-in before-UI capture is separate."""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_review_evidence_routes import world, app, server, controls  # noqa: F401 -- pytest fixture registration and helper reexports
from assignment_route_fixtures import PASSWORD
from evidence_browser_fixtures import retain_case


def login(browser, server, width=1000):  # noqa: F811 -- pytest fixture injection
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(server)
    page.locator("input[name=email]").fill("jane@firm.example")
    page.locator("input[name=password]").fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.get_by_role("heading", name="My cases", exact=True)).to_be_visible(timeout=60000)
    return context, page, errors


def open_tab(page, server, tab):  # noqa: F811 -- pytest fixture injection
    page.goto("about:blank")
    page.goto(server + "?tab=" + tab + "#case-ana")
    expect(page.locator("#client")).to_have_value("case-ana", timeout=60000)
    show = page.get_by_role("button", name="Show all", exact=True)
    if show.is_visible():
        show.click()


def card_for(page, server, key):  # noqa: F811 -- pytest fixture injection
    items = page.request.get(server + "/api/items?client=case-ana").json()
    row = next(c for c in items["cards"] if any(f["key"] == key for f in c["facts"]))
    open_tab(page, server, row["tab"])
    card = page.locator("article[data-review-card=" + json.dumps(row["id"]) + "]")
    expect(card).to_be_visible(timeout=60000)
    return row, card


def shot(page, name, fixture):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Horizontal page overflow"
    folder = os.environ.get("E2E_SHOTS")
    if folder:
        output = Path(folder); output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / (name + ".png")), full_page=True)
        (output / (name + ".json")).write_text(json.dumps({"source_sha256": fixture["sha256"],
            "reader_text": fixture["reader_text"], "capture": name,
            "limitations": "Scripted fictional browser capture; no human accuracy/time estimate."}, indent=2), encoding="utf-8")


@pytest.mark.skipif(os.environ.get("EV5_CAPTURE_BEFORE") != "1", reason="Explicit before-UI capture only, not candidate acceptance")
@pytest.mark.parametrize("width", [1000, 1400], ids=["1000px", "1400px"])
def test_before_ui_capture(browser, server, world, app, tmp_path, width):  # noqa: F811 -- pytest fixture injection
    fixture = retain_case(world, app, tmp_path, translated=True)
    context, page, errors = login(browser, server, width)
    try:
        card_for(page, server, "applicant.i94_number")
        shot(page, "before-ui-new-backend-retained-i94-" + str(width), fixture)
        card_for(page, server, "nta.arrival_date")
        shot(page, "before-ui-new-backend-retained-nta-" + str(width), fixture)
        open_tab(page, server, "documents")
        expect(page.locator("#dropzone")).to_be_visible(timeout=60000)
        expect(page.locator("#papers")).not_to_contain_text("Reading the papers this filing asks about", timeout=60000)
        shot(page, "before-ui-new-backend-inventory-" + str(width), fixture)
        assert not errors
    finally:
        context.close()
