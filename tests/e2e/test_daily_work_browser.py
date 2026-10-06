"""Fictional current staff workflows; no provider or real client actions."""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from assignment_route_fixtures import world, app, server, PASSWORD  # noqa: F401 -- pytest fixture registration and helper reexports
from test_review_evidence_routes import controls  # noqa: F401 -- pytest fixture registration and helper reexports
from daily_work_fixtures import typed_part14, SHORT
from evidence_browser_fixtures import retain_case
from review.state import reviewed_graph, _check_value


def login(browser, server, width):  # noqa: F811 -- pytest fixture injection
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
    page.goto(server + "?tab=" + tab + "#case-ana")
    expect(page.locator("#client")).to_have_value("case-ana", timeout=60000)
    show = page.get_by_role("button", name="Show all", exact=True)
    if show.is_visible():
        show.click()


def capture(page, name):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if os.environ.get("E2E_SHOTS"):
        folder = Path(os.environ["E2E_SHOTS"]); folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(folder / (name + ".png")), full_page=True)


@pytest.mark.parametrize("width", [1000, 1400], ids=["1000px", "1400px"])
def test_blank_short_and_populated_part14_multiline_save(browser, server, world, app, tmp_path, width):  # noqa: F811 -- pytest fixture injection
    case = typed_part14(world, app, tmp_path)
    context, page, errors = login(browser, server, width)
    try:
        data = page.request.get(server + "/api/items?client=case-ana").json()
        row = next(c for c in data["cards"] if any(f["key"] == "applicant.p14_block1_text" for f in c["facts"]))
        open_tab(page, server, row["tab"])
        short = page.get_by_role("textbox", name="Part 14 entry 1 continuation text", exact=True)
        blank = page.get_by_role("textbox", name="Part 14 entry 2 continuation text", exact=True)
        expect(short).to_have_value(SHORT, timeout=60000)
        expect(blank).to_have_value("")
        for field in [short, blank]:
            assert field.evaluate("n => n.tagName") == "TEXTAREA"
            assert field.evaluate("n => n.rows") >= 4
            assert field.evaluate("n => parseFloat(getComputedStyle(n).minHeight)") >= 100
            assert field.evaluate("n => getComputedStyle(n).resize") == "vertical"
            assert field.evaluate("n => !!document.querySelector('label[for=' + CSS.escape(n.id) + ']')")
        blank.focus()
        expect(blank).to_be_focused()
        text = "A fictional first line.\nA second line retained for Part 14."
        short.fill(text)
        blank.fill("A fictional previously blank continuation.")
        capture(page, "part14-multiline-" + str(width))
        cards = page.locator("article[data-review-card]").filter(has=short)
        with page.expect_response(lambda r: "/api/decide" in r.url and r.request.method == "POST", timeout=120000) as result:
            cards.get_by_role("button", name="Save", exact=True).click()
        assert result.value.status == 200, result.value.text()
        graph = reviewed_graph(case)
        key = "applicant.p14_block1_text"
        entered = result.value.request.post_data_json["decisions"]
        assert any(d.get("values", {}).get(key) == text for d in entered)
        source = next(f for f in row["facts"] if f["key"] == key)
        normalized = _check_value(text, source["input"], key)
        assert graph.get(key).value == normalized
        assert "\n" in graph.get(key).value
        # The blank entry may share or have its own review card. Save it explicitly if separate.
        if graph.get("applicant.p14_block2_text").value == "":
            expect(blank).to_be_visible(timeout=60000)
            separate = page.locator("article[data-review-card]").filter(has=blank)
            blank.fill("A fictional previously blank continuation.")
            with page.expect_response(lambda r: "/api/decide" in r.url and r.request.method == "POST", timeout=120000) as result2:
                separate.get_by_role("button", name="Save", exact=True).click()
            assert result2.value.status == 200, result2.value.text()
        final_cards = data["cards"]
        blank_fact = next(f for c in final_cards for f in c["facts"] if f["key"] == "applicant.p14_block2_text")
        assert reviewed_graph(case).get("applicant.p14_block2_text").value == _check_value(
            "A fictional previously blank continuation.", blank_fact["input"], blank_fact["key"])
        log = json.loads((case / "decisions.json").read_text())
        assert any(d.get("role") == "paralegal" and d.get("values", {}).get(key) == normalized for d in log.values())
        assert any("\n" in d.get("values", {}).get(key, "") for d in log.values())
        assert not (case / "part14_explanations.json").exists()  # text save grants no legal explanation approval
        assert not errors
    finally:
        context.close()


@pytest.mark.parametrize("width", [1000, 1400], ids=["1000px", "1400px"])
def test_notes_keyboard_and_packet_section_order(browser, server, world, app, tmp_path, width):  # noqa: F811 -- pytest fixture injection
    retain_case(world, app, tmp_path, translated=True)
    context, page, errors = login(browser, server, width)
    try:
        open_tab(page, server, "packet")
        included, excluded, before = [page.locator("#" + key) for key in ["packet-included", "packet-excluded", "packet-before-filing"]]
        expect(excluded).to_be_visible(timeout=60000)
        expect(included).to_contain_text("In the packet, in order")
        expect(excluded).to_contain_text("Not in the packet")
        expect(before).to_contain_text("Before you mail it")
        assert included.evaluate("n => !!(n.compareDocumentPosition(document.getElementById('packet-excluded')) & Node.DOCUMENT_POSITION_FOLLOWING)")
        assert excluded.evaluate("n => !!(n.compareDocumentPosition(document.getElementById('packet-before-filing')) & Node.DOCUMENT_POSITION_FOLLOWING)")
        nav = page.get_by_role("navigation", name="Review queues")
        labels = nav.locator("button .nm").all_text_contents()
        assert labels[:2] == ["Where the case stands", "Notes and tasks"]
        status = nav.get_by_role("button", name="Where the case stands")
        status.focus(); page.keyboard.press("Tab")
        notes = nav.get_by_role("button", name="Notes and tasks")
        expect(notes).to_be_focused()
        page.keyboard.press("Enter")
        expect(notes).to_have_attribute("aria-current", "page")
        expect(page.get_by_role("heading", name="Notes and tasks", exact=True)).to_be_visible()
        open_tab(page, server, "notes")
        expect(nav.get_by_role("button", name="Notes and tasks")).to_have_attribute("aria-current", "page")
        capture(page, "notes-navigation-" + str(width))
        open_tab(page, server, "packet")
        expect(page.locator("#packet-excluded")).to_be_visible(timeout=60000)
        capture(page, "packet-order-" + str(width))
        assert not errors
    finally:
        context.close()


def test_agreement_inputs_explain_required_services_and_optional_government_fees(browser, server, world, app):  # noqa: F811 -- pytest fixture injection
    context, page, errors = login(browser, server, 1000)
    try:
        open_tab(page, server, "engagement")
        expect(page.locator("#agreement-purpose")).to_contain_text("Creating a draft does not send it or sign it", timeout=60000)
        fee = page.get_by_role("textbox", name="Professional service fee and payment terms (required)", exact=True)
        expect(fee).to_have_value("")
        expect(page.get_by_role("textbox", name="Government fees (optional)", exact=True)).to_have_value("")
        expect(page.get_by_role("textbox", name="Additions (optional)", exact=True)).to_have_value("")
        assert page.locator(".filing-picks input:checked").count() == 0
        assert not page.locator("#send-agreement").count()
        capture(page, "agreement-input-clarity-1000")
        assert not errors
    finally:
        context.close()
