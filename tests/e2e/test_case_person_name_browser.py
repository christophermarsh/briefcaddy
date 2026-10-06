"""The case name supplies the placeholder's display without changing identity."""
# ruff: noqa: F811 -- canonical fixtures use pytest injection
import copy

import documents
import subject_attribution as subjects
from playwright.sync_api import expect
from test_assignment_routes import app, controls, server, world  # noqa: F401
from test_client_picker_refresh_browser import login
from evidence_browser_fixtures import retain_case


def test_existing_case_name_is_used_without_retyping_or_reopening_assignments(browser, server, world, app, tmp_path):
    fixture = retain_case(world, app, tmp_path)
    case = fixture["case"]
    before = copy.deepcopy(documents.read(case)["case_subjects"])
    assert before["people"][0]["label"] == "Client in case case-ana"
    assert all(row["current"] for row in subjects.views(case))
    context, page = login(browser, server)
    try:
        page.goto(server + "/?tab=documents#case-ana")
        expect(page.locator("#case h1")).not_to_have_text("case-ana", timeout=60000)
        expect(page.get_by_text("Confirmed documents (", exact=False)).to_be_visible(timeout=60000)
        name = page.locator("#case h1").inner_text()
        page.get_by_text("Confirmed documents (", exact=False).click()
        holder = page.get_by_label("Document holder in", exact=False).first
        expect(holder).to_be_visible()
        expect(holder.locator("option").filter(has_text=name)).to_have_count(1)
        expect(holder).not_to_contain_text("Client in case case-ana")
        page.get_by_text("Manage case people", exact=True).click()
        page.get_by_text("Applicant name", exact=True).click()
        expect(page.get_by_text(f"Applicant: {name}. Uses the name already entered on this case.")).to_be_visible()
        expect(page.get_by_label("Correct name for applicant", exact=True)).to_have_count(0)
        assert documents.read(case)["case_subjects"] == before
        assert all(row["current"] for row in subjects.views(case))
    finally:
        context.close()
