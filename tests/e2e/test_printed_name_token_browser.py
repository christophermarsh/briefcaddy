"""Actual retained PDFs preserve printed initials through review, fill and Undo."""
# ruff: noqa: F401, F811 -- imported canonical pytest fixtures
import hashlib
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect
from pypdf import PdfReader

from cloud_daily_work_fixtures import STAFF, cloud_world, cloud_app, cloud_server, login, ok
from factgraph import FactGraph
from test_marriage_printed_name_browser import make_case, screen, confirm_name_sources, SPOUSE
from review.state import load_decision_log, reviewed_graph

pytestmark = pytest.mark.skipif(not os.environ.get("E2E"), reason="E2E=1 requests actual Chromium")
SHOTS = Path(__file__).resolve().parents[2] / "docs/research/cloud_printed_name_s4_repair_evidence/shots" / os.environ.get("PRINTED_TOKEN_RUN", "development")


def capture(page, card, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    expect(page.locator("#toast")).to_be_hidden()
    card.evaluate("node => scrollTo(0, Math.max(0, scrollY + node.getBoundingClientRect().top - document.querySelector('.topbar').getBoundingClientRect().height - 16))")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(SHOTS / (name + "-viewport.png")))
    card.screenshot(path=str(SHOTS / (name + "-element.png")))


@pytest.mark.parametrize("width,printed", [(1000, "ALPHA NOVEL X"), (1400, "ALPHA NOVEL D E")])
def test_printed_initials_survive_actual_page_review_save_fill_and_undo(browser, cloud_world, cloud_app, cloud_server, width, printed):
    world, base = cloud_world, cloud_server
    cookie = login(base, STAFF)
    client, content = make_case(world, base, cookie, "Name after Marriage: " + SPOUSE + " Name after Marriage: " + printed)
    case = world["scope"].cases / client
    items = ok(base, "/api/items?client=" + client, cookie)
    item = next(row for row in items["open"] if row["kind"] == "names")
    event = next(row for row in item["timeline"]["events"] if row.get("parent_key") == "marriage.party_b.name_after")
    assert event["name"] == printed and event["party"] == "party_b" and event["page"] == 1
    raw = FactGraph.load(case / "fact_graph_raw.json").get("marriage.party_b.name_after")
    assert raw.value == printed and any(printed in source.raw_value and source.page == 1 for source in raw.sources)
    assert event["source_location"]["source_sha256"] == hashlib.sha256(content).hexdigest()
    assert not load_decision_log(case).get(item["id"])
    context, page, card, errors, images = screen(browser, base, cookie, client, width)
    try:
        expect(card).to_contain_text("Shown for now: " + printed)
        expect(card).to_contain_text("Party B")
        assert card.locator('input[type=radio][value="' + printed + '"]').count() == 1
        assert not card.locator('input[type=radio][value="' + SPOUSE + '"]').count()
        figure = card.locator(".namepage").filter(has_text=printed).first
        figure.scroll_into_view_if_needed()
        image = figure.locator("img.scan")
        expect(image).to_have_js_property("complete", True)
        assert image.evaluate("node => node.naturalWidth > 0")
        assert "page=1" in image.get_attribute("src")
        assert any(row["url"] == base + image.get_attribute("src") and row["status"] == 200 for row in images)
        with page.expect_popup() as original:
            figure.locator("a[data-open-source]").first.click()
        original.value.wait_for_url("**#page=2")
        original.value.close()
        capture(page, card, "printed-initials-source-" + str(width))
        capture(page, card.locator('[data-fact-key="applicant.name_current"]'), "printed-initials-choice-" + str(width))
        card.locator('input[type=radio][value="' + printed + '"]').check()
        card.locator("input.note").fill("Read the fictional full Party B name on retained page 2, including every printed initial.")
        with page.expect_response(lambda response: "/api/decide" in response.url) as saved:
            card.get_by_role("button", name="Save", exact=True).click()
        assert saved.value.status == 200, saved.value.text()
        expect(card).to_have_count(0)
        decision = load_decision_log(case)[item["id"]]
        assert decision["values"]["applicant.name_current"] == printed and decision["reviewer"] == "Fictional Staff"
        assert decision["name_evidence"] == item["timeline"]["marriage_evidence"]
        graph = reviewed_graph(case)
        assert graph.get("applicant.family_name").value == printed.removeprefix("ALPHA ")
        assert graph.get("applicant.other_name1_family").value == "EXAMPLE"
        assert not ok(base, "/api/packet?client=" + client + "&filing=i485", cookie)["ready"]
        confirm_name_sources(page, base, client, case)
        ok(base, "/api/apply", cookie, {"client": client})
        fields = {key.rsplit(".", 1)[-1]: str(value.get("/V") or "") for key, value in PdfReader(case / "i485_filled.pdf").get_fields().items()}
        assert (fields["Pt1Line1_GivenName[0]"], fields["Pt1Line1_FamilyName[0]"]) == ("ALPHA", printed.removeprefix("ALPHA "))
        assert (fields["Pt1Line2_GivenName[0]"], fields["Pt1Line2_FamilyName[0]"]) == ("ALPHA", "EXAMPLE")
        page.get_by_role("navigation", name="Review queues").get_by_role("button", name="Decision log", exact=False).click()
        row = page.locator("tr").filter(has_text="Which name is current?").filter(has=page.get_by_role("button", name="Undo", exact=False))
        expect(row).to_contain_text("Fictional Staff")
        with page.expect_response(lambda response: "/api/undo" in response.url) as undone:
            row.get_by_role("button", name="Undo", exact=False).click()
        assert undone.value.status == 200
        page.get_by_role("navigation", name="Review queues").get_by_role("button", name="Needs attention", exact=False).click()
        expect(card).to_be_visible()
        assert load_decision_log(case)[item["id"]]["undone"]["by"] == "Fictional Staff"
        capture(page, card.locator('[data-fact-key="applicant.name_current"]'), "printed-initials-undo-" + str(width))
        (SHOTS / ("actual-initials-" + str(width) + ".json")).write_text(json.dumps({"fictional": True,
            "printed": printed, "certificate_sha256": hashlib.sha256(content).hexdigest(), "event": event,
            "decision": decision, "undone": load_decision_log(case)[item["id"]], "crop_responses": images,
            "current_family": fields["Pt1Line1_FamilyName[0]"], "prior_family": fields["Pt1Line2_FamilyName[0]"]}, indent=2) + "\n")
        assert not errors and not world["sent"]
    except BaseException:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / ("failure-initials-" + str(width) + ".png")), full_page=True)
        raise
    finally:
        context.close()


@pytest.mark.parametrize("printed", ["ALPHA NOVEL iped", "UNKNOWN", "UNREADABLE", "ILLEGIBLE"])
def test_uncertain_read_stays_uncertain_in_actual_retained_case(browser, cloud_world, cloud_app, cloud_server, printed):
    world, base = cloud_world, cloud_server
    cookie = login(base, STAFF)
    client, content = make_case(world, base, cookie, "Party B Name after Marriage: " + printed)
    item = next(row for row in ok(base, "/api/items?client=" + client, cookie)["open"] if row["kind"] == "names")
    question = item["timeline"]["question"]
    assert question["read_state"] == "unreadable" and question["party"] == "party_b"
    assert not any(row.get("parent_key") == "marriage.party_b.name_after" for row in item["timeline"]["events"])
    context, page, card, errors, _ = screen(browser, base, cookie, client, 1000)
    try:
        expect(card).to_contain_text("verify the original")
        assert not card.locator('input[type=radio][value="ALPHA NOVEL"]').count()
        assert not card.locator("input[type=radio]:checked").count()
        assert "no name after marriage printed" not in card.inner_text().lower()
        assert not ok(base, "/api/packet?client=" + client + "&filing=i485", cookie)["ready"]
        capture(page, card, "uncertain-read-" + printed.replace(" ", "-") + "-source-1000")
        capture(page, card.locator('[data-fact-key="applicant.name_current"]'), "uncertain-read-" + printed.replace(" ", "-") + "-choices-1000")
        (SHOTS / ("actual-uncertain-" + printed.replace(" ", "-") + ".json")).write_text(json.dumps({"fictional": True,
            "printed": printed, "certificate_sha256": hashlib.sha256(content).hexdigest(), "question": question}, indent=2) + "\n")
        assert not errors and not world["sent"]
    except BaseException:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / ("failure-uncertain-" + printed.replace(" ", "-") + ".png")), full_page=True)
        raise
    finally:
        context.close()
