"""Pending UX03 browser acceptance; no production bypass or legal approval."""
import json
import pytest
from playwright.sync_api import expect

from test_review_evidence_routes import world, app, server, controls  # noqa: F401 -- pytest fixture registration and helper reexports
from test_review_evidence_browser import login, card_for, open_tab
from test_review_evidence_ui import focused_shot
from place_compare_fixtures import retain_place_case
from test_place_comparison import CITY, REFERENCE, filed_city
import critical_review as critical


@pytest.mark.parametrize("width", [1000, 1400], ids=["1000px", "1400px"])
def test_state_level_reference_ack_does_not_choose_city(browser, server, world, app, tmp_path, width):  # noqa: F811 -- pytest fixture injection
    case = retain_place_case(world, app, tmp_path)
    before = filed_city(app, case, tmp_path / "before.pdf")
    holds = critical.problems(case)
    context, page, errors = login(browser, server, width)
    try:
        row, card = card_for(page, server, REFERENCE)
        assert row["comparison"]["kind"] == "specificity"
        reference_row = card.locator('[data-fact-key="applicant.marriage_cert_birthplace"]')
        expect(reference_row.locator(".lbl")).to_contain_text("Marriage certificate: printed birthplace")
        expect(reference_row).not_to_contain_text("Answer on the form")
        expect(reference_row.locator("input,textarea,select")).to_have_count(0)
        city_row = card.locator('[data-fact-key="applicant.birth_city"]')
        expect(city_row.locator(".lbl")).to_contain_text("Current I-485 city")
        expect(city_row.locator("input,textarea,select")).to_have_count(0)
        expect(card.locator('figure[data-source-preview="birth.pdf"]')).to_have_count(1)
        expect(card.locator('figure[data-source-preview="marriage.pdf"]')).to_have_count(1)
        expect(card.locator(".place-comparison")).to_contain_text("Current I-485")
        expect(card.locator(".place-comparison")).to_contain_text("CAMPINAS")
        expect(card).to_contain_text("Acknowledge")
        expect(card).to_contain_text("does not change")
        expect(card.get_by_role("button", name="Save", exact=True)).to_have_count(0)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        for image in card.locator("img.scan").all():
            image.scroll_into_view_if_needed()
            expect(image).to_have_js_property("complete", True)
            assert image.evaluate("node => node.naturalWidth > 0")
        focused_shot(page, card, "birthplace-" + row["comparison"]["kind"] + "-" + str(width))
        with page.expect_response(lambda response: "/api/decide" in response.url) as recorded:
            card.get_by_role("button", name="Acknowledge", exact=True).click()
        assert recorded.value.status == 200, recorded.value.text()
        expect(card).to_have_count(0, timeout=60000)
        assert filed_city(app, case, tmp_path / "acknowledged.pdf") == before == "CAMPINAS"
        assert critical.problems(case) == holds
        decision = json.loads((case / "decisions.json").read_text(encoding="utf-8"))["crosscheck:" + REFERENCE]
        assert decision["action"] == "acknowledge" and decision["reviewer"] == "Jane Doe"
        assert not decision.get("evidence_confirmation")
        assert not errors
    finally:
        context.close()


@pytest.mark.parametrize("width", [1000, 1400], ids=["1000px", "1400px"])
def test_disagreement_explicit_city_choice_changes_filled_output_and_undo(browser, server, world, app, tmp_path, width):  # noqa: F811 -- pytest fixture injection
    case = retain_place_case(world, app, tmp_path, disagreement=True)
    context, page, errors = login(browser, server, width)
    try:
        row, card = card_for(page, server, REFERENCE)
        assert row["comparison"]["kind"] == "disagreement"
        reference_row = card.locator('[data-fact-key="applicant.marriage_cert_birthplace"]')
        expect(reference_row.locator(".lbl")).to_contain_text("Marriage certificate: printed birthplace")
        expect(reference_row).not_to_contain_text("Answer on the form")
        expect(reference_row.locator("input,textarea,select")).to_have_count(0)
        expect(card).to_contain_text("The readings differ. Check both originals")
        expect(card).not_to_contain_text("One of them is wrong")
        expect(card.locator(".place-comparison")).to_contain_text("Current I-485")
        expect(card.get_by_role("button", name="Acknowledge", exact=True)).to_have_count(0)
        choices = card.get_by_role("radio")
        expect(choices).to_have_count(2)
        assert all(not choice.is_checked() for choice in choices.all())
        assert filed_city(app, case, tmp_path / "before.pdf") == "CAMPINAS"
        card.get_by_role("radio", name="Marriage certificate: SOROCABA", exact=True).check()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        for image in card.locator("img.scan").all():
            image.scroll_into_view_if_needed()
            expect(image).to_have_js_property("complete", True)
            assert image.evaluate("node => node.naturalWidth > 0")
        focused_shot(page, card, "birthplace-" + row["comparison"]["kind"] + "-" + str(width))
        with page.expect_response(lambda response: "/api/decide" in response.url) as recorded:
            card.get_by_role("button", name="Save", exact=True).click()
        assert recorded.value.status == 200, recorded.value.text()
        expect(card).to_have_count(0, timeout=60000)
        assert filed_city(app, case, tmp_path / "selected.pdf") == "SOROCABA"
        decision = json.loads((case / "decisions.json").read_text(encoding="utf-8"))["crosscheck:" + REFERENCE]
        assert decision["values"] == {CITY: "SOROCABA"}
        assert decision["reviewer"] == "Jane Doe" and decision["role"] == "paralegal"
        assert decision["evidence_confirmation"]["basis"] == "manual_retained_source_review"
        assert decision["evidence_confirmation"]["model_release_approval"] is False
        done = next(item for item in page.request.get(server + "/api/items?client=case-ana").json()["done"] if item["id"] == "crosscheck:" + REFERENCE)
        open_tab(page, server, "done")
        record = page.locator("tr").filter(has_text=done.get("headline") or done["title"]).filter(has=page.get_by_role("button", name="Undo", exact=False))
        expect(record).to_have_count(1)
        with page.expect_response(lambda response: "/api/undo" in response.url) as reopened:
            record.get_by_role("button", name="Undo", exact=False).click()
        assert reopened.value.status == 200
        assert filed_city(app, case, tmp_path / "undone.pdf") == "CAMPINAS"
        _, card = card_for(page, server, REFERENCE)
        expect(card.get_by_role("radio", name="Marriage certificate: SOROCABA", exact=True)).not_to_be_checked()
        assert not errors
    finally:
        context.close()
