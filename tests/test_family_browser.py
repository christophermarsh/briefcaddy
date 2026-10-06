"""Fictional UX08 actual controls against current protected loopback HTTP."""
# ruff: noqa: F811 -- imported canonical pytest fixtures are injected by name
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

import journey
import restricted
from test_prospects import firm, server, ok, new, change  # noqa: F401 -- fixtures
from test_staff_consent_browser import staff_chromium, choose_client  # noqa: F401 -- fixture
from test_case_assignment_lists import entry

pytestmark = pytest.mark.skipif(not os.environ.get("E2E"), reason="E2E=1 requests real Chromium family controls")
SHOTS = Path(__file__).resolve().parents[1] / "docs/research/cloud_family_evidence/shots/run24"


def capture(page, panel, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    expect(page.locator("#toast")).to_be_hidden(timeout=15000)
    panel.scroll_into_view_if_needed()
    page.evaluate("el => el.scrollIntoView({block:'start'})", panel.element_handle())
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(SHOTS / (name + "-viewport.png")))
    panel.screenshot(path=str(SHOTS / (name + "-panel.png")))


@pytest.fixture
def cohort(server, firm, monkeypatch):
    seeded = {}
    root = firm["clients"]
    for n in range(1997):
        cid = f"family-fictional-{n:04d}"
        folder = root / cid; folder.mkdir()
        (folder / "meta.json").write_text('{"classifications":{}}')
        (folder / "fact_graph.json").write_text(json.dumps({"client_id": cid, "facts": {}}))
        row = entry(cid, None, closed=n % 10 == 0, named=[])
        row["row"]["summary"]["name"] = "Fictional Duplicate Person" if n in (1, 2) else f"Fictional Person {n:04d}"
        seeded[cid] = row
        if row["closed"]:
            (folder / "access.json").write_text(json.dumps({"marked": {"on": True}, "people": []}))
    assert len(list(root.iterdir())) == 2000
    from review.roster import Roster
    def build(self, cid):
        self.reads += 1
        return seeded.get(cid) or entry(cid, None, closed=cid == "case-rosa", named=[])
    monkeypatch.setattr(Roster, "build", build)
    monkeypatch.setenv("I485_WALK_EVERY", "600")
    server["app"].roster.walk(parallel=False)
    return server["app"].roster


def open_family(browser, srv, width=1000, who="sam"):
    context = browser.new_context(viewport={"width": width, "height": 900})
    key, value = srv[who].split("=", 1)
    context.add_cookies([{"name": key, "value": value, "url": srv["base"]}])
    page = context.new_page(); errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    if who == "sam":
        page.goto(srv["base"] + "/#all")
        expect(page.get_by_role("heading", name="Getting started", exact=True).first).to_be_visible(timeout=30000)
        page.locator("#all").click()
        expect(page.locator("#add-client-btn")).to_be_visible(timeout=30000)
        choose_client(page, "case-ana")
        page.get_by_role("navigation", name="Review queues").get_by_role("button", name="Where the case stands").click()
    else:
        page.goto(srv["base"] + "/?tab=journey#case-ana")
    panel = page.locator('[data-family-case="case-ana"]')
    expect(panel).to_be_visible(timeout=60000)
    return context, page, panel, errors


@pytest.mark.parametrize("width", [1000, 1400])
def test_two_thousand_bounded_keyboard_duplicate_selection_and_explicit_confirmation(staff_chromium, server, firm, cohort, width):
    context, page, panel, errors = open_family(staff_chromium, server, width, "jane")
    requests = []; page.on("request", lambda req: requests.append(req.url))
    try:
        find = panel.get_by_label("Find a family member's case", exact=True)
        save = panel.get_by_role("button", name="Confirm and link these cases", exact=True)
        expect(save).to_be_disabled()
        expect(panel.get_by_label("Family relationship", exact=True)).to_have_value("")
        expect(panel.get_by_role("listbox", name="Accessible family cases").get_by_role("option")).to_have_count(0)
        find.fill("family-fictional")
        expect(panel.get_by_role("listbox", name="Accessible family cases").get_by_role("option")).to_have_count(20, timeout=60000)
        expect(panel.get_by_role("status").first).to_contain_text("1797 accessible cases")
        capture(page, panel, "family-bounded-2000-" + str(width))
        panel.get_by_role("button", name="Next family results", exact=True).click()
        expect(panel.get_by_role("status").first).to_contain_text("Showing 21–40", timeout=60000)
        find.fill("family-fictional-0000")
        expect(panel.get_by_role("status").first).to_have_text("No accessible cases match. Try another name or case ID.", timeout=60000)
        hidden = panel.get_by_role("status").first.inner_text()
        find.fill("unknown-fictional")
        expect(panel.get_by_role("status").first).to_have_text(hidden, timeout=60000)
        find.fill("Fictional Duplicate Person")
        expect(panel.get_by_role("listbox", name="Accessible family cases").get_by_role("option")).to_have_count(2, timeout=60000)
        expect(panel.get_by_role("listbox", name="Accessible family cases").get_by_role("option").nth(0)).to_contain_text("family-fictional-0001")
        expect(panel.get_by_role("listbox", name="Accessible family cases").get_by_role("option").nth(1)).to_contain_text("family-fictional-0002")
        expect(save).to_be_disabled()
        find.press("ArrowDown"); find.press("ArrowDown"); find.press("Enter")
        expect(panel.locator("[data-family-selection]")).to_contain_text("family-fictional-0002")
        expect(panel.get_by_label("Family relationship", exact=True)).to_be_focused()
        expect(save).to_be_disabled()
        panel.get_by_label("Family relationship", exact=True).select_option("Child")
        expect(save).to_be_disabled()
        panel.get_by_label("Confirm these two cases and the family relationship", exact=True).check()
        capture(page, panel, "family-explicit-two-cases-confirmation-" + str(width))
        with page.expect_response(lambda r: r.url.endswith("/api/journey") and r.request.method == "POST", timeout=60000) as linked:
            save.click()
        assert linked.value.status == 200, linked.value.text()
        expect(page.locator("#family-members table").first).to_contain_text("family-fictional-0002", timeout=60000)
        assert journey._status(firm["clients"] / "family-fictional-0002")["journey"]["linked"][0]["relationship"] == "Parent"
        capture(page, page.locator("#family-members"), "family-reciprocal-recorded-" + str(width))
        assert all("size=20" in url for url in requests if "/api/family-candidates?" in url)
        assert not errors
    finally:
        context.close()


def test_stale_target_access_is_refused_without_relationship_write(staff_chromium, server, firm):
    context, page, panel, errors = open_family(staff_chromium, server, who="jane")
    try:
        panel.get_by_label("Find a family member's case", exact=True).fill("case-bia")
        option = panel.get_by_role("listbox", name="Accessible family cases").get_by_role("option"); expect(option).to_have_count(1, timeout=60000)
        option.get_by_role("button").click()
        panel.get_by_label("Family relationship", exact=True).select_option("Sibling")
        panel.get_by_label("Confirm these two cases and the family relationship", exact=True).check()
        restricted.mark(firm["clients"] / "case-bia", True, "Fictional revocation before family save", "Sam Attorney", "attorney")
        with page.expect_response(lambda r: r.url.endswith("/api/journey") and r.request.method == "POST") as response:
            panel.get_by_role("button", name="Confirm and link these cases", exact=True).click()
        assert response.value.status == 404
        expect(panel.get_by_role("alert")).to_contain_text("Check current access")
        expect(panel.locator("[data-family-selection]")).to_be_hidden()
        assert not journey._status(firm["clients"] / "case-ana").get("journey", {}).get("family_instance")
        capture(page, panel, "family-current-access-refused")
        assert not errors
    finally:
        context.close()


def select_existing(panel, target="case-bia", relationship="Child"):
    panel.get_by_label("Find a family member's case", exact=True).fill(target)
    option = panel.get_by_role("listbox", name="Accessible family cases").get_by_role("option")
    expect(option).to_have_count(1, timeout=60000)
    option.get_by_role("button").click()
    panel.get_by_label("Family relationship", exact=True).select_option(relationship)
    panel.get_by_label("Confirm these two cases and the family relationship", exact=True).check()


def test_interrupted_actual_link_recovers_exact_operation_through_controls(staff_chromium, server, firm, monkeypatch):
    context, page, panel, errors = open_family(staff_chromium, server)
    try:
        select_existing(panel)
        save = journey._family_save
        with monkeypatch.context() as fault:
            def crash(folder, status):
                save(folder, status)
                if folder.name == "case-bia" and status.get("journey", {}).get("family_pending"):
                    raise OSError("Fictional actual family browser interruption")
            fault.setattr(journey, "_family_save", crash)
            with page.expect_response(lambda r: r.url.endswith("/api/journey") and r.request.method == "POST") as failed:
                panel.get_by_role("button", name="Confirm and link these cases", exact=True).click()
            assert failed.value.status == 500
        expect(panel.get_by_role("alert")).to_contain_text("recorded operation")
        panel.get_by_text("An earlier family change was interrupted?", exact=True).click()
        panel.get_by_role("button", name="Inspect interrupted family operation", exact=True).click()
        recover = panel.get_by_role("button", name="Recover this family operation", exact=True)
        expect(recover).to_be_visible(timeout=60000)
        capture(page, panel, "family-retained-operation-recovery")
        with page.expect_response(lambda r: r.url.endswith("/api/family-link-recovery") and r.request.method == "POST") as done:
            recover.click()
        assert done.value.status == 200 and done.value.json()["state"] == "none"
        expect(page.locator("#family-members table").first).to_contain_text("case-bia", timeout=60000)
        assert journey._status(firm["clients"] / "case-bia")["journey"]["linked"][0]["relationship"] == "Parent"
        assert not errors
    finally:
        context.close()


def test_late_family_search_and_journey_response_preserve_current_controls(staff_chromium, server):
    context, page, panel, errors = open_family(staff_chromium, server)
    try:
        def delayed_search(route):
            response = route.fetch()
            if "q=case-bia" in route.request.url:
                page.locator('[data-family-case="case-ana"]').get_by_label("Find a family member's case", exact=True).fill("unknown-fictional")
            route.fulfill(response=response)
        page.route("**/api/family-candidates?**", delayed_search)
        panel.get_by_label("Find a family member's case", exact=True).fill("case-bia")
        expect(panel.get_by_role("status").first).to_have_text("No accessible cases match. Try another name or case ID.", timeout=60000)
        expect(panel.get_by_role("listbox", name="Accessible family cases").get_by_role("option")).to_have_count(0)
        expect(panel.get_by_role("button", name="Confirm and link these cases", exact=True)).to_be_disabled()
        page.unroute("**/api/family-candidates?**", delayed_search)
        def delayed_journey(route):
            response = route.fetch()
            page.locator("#all").click()
            route.fulfill(response=response)
        page.route("**/api/journey?client=case-ana", delayed_journey)
        page.get_by_role("navigation", name="Review queues").get_by_role("button", name="Where the case stands").click()
        expect(page.get_by_role("heading", name="Every case", exact=True)).to_be_visible(timeout=60000)
        expect(page.locator("[data-family-case]")).to_have_count(0)
        assert not errors
    finally:
        context.close()


def test_actual_from_call_add_interruption_and_same_promotion_recovery(staff_chromium, server, firm, monkeypatch):
    from portal import promotion
    pid = new(server)
    change(server, "jane", pid, "answers", answers={"given_name": "Lia"})
    context = staff_chromium.new_context(viewport={"width": 1000, "height": 900})
    key, value = server["sam"].split("=", 1)
    context.add_cookies([{"name": key, "value": value, "url": server["base"]}])
    page = context.new_page(); errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(server["base"] + "/#all")
        expect(page.get_by_role("heading", name="Getting started", exact=True).first).to_be_visible(timeout=30000)
        page.locator("#all").click()
        expect(page.locator("#add-client-btn")).to_be_visible(timeout=30000)
        page.get_by_role("button", name="Prospects", exact=True).click()
        page.locator(f'[data-prospect="{pid}"]').get_by_role("button", name="Open", exact=True).click()
        expect(page.locator("#prospect-become")).to_be_visible(timeout=60000)
        page.locator("#prospect-become").click()
        expect(page.locator("#add-client-save")).to_be_visible(timeout=60000)
        save = promotion._save
        with monkeypatch.context() as fault:
            def crash(scope, store, client, record):
                save(scope, store, client, record)
                if record["role"] == "source" and record["state"] == "pending":
                    raise OSError("Fictional actual browser promotion interruption")
            fault.setattr(promotion, "_save", crash)
            with page.expect_response(lambda r: r.url.endswith("/api/client-add") and r.request.method == "POST", timeout=60000) as failed:
                page.locator("#add-client-save").click()
            assert failed.value.status == 409
        panel = page.locator("#add-client [data-promotion-recovery]")
        expect(panel).to_be_visible(timeout=60000)
        panel.get_by_role("button", name="Inspect this prospect promotion", exact=True).click()
        recover = panel.get_by_role("button", name="Recover this prospect promotion", exact=True)
        expect(recover).to_be_visible(timeout=60000)
        capture(page, panel, "promotion-same-reserved-client-recovery")
        with page.expect_response(lambda r: r.url.endswith("/api/promotion-recovery") and r.request.method == "POST") as done:
            recover.click()
        assert done.value.status == 200
        cid = done.value.json()["client"]
        expect(panel.get_by_role("status")).to_contain_text("nothing was sent")
        assert firm["store"].answers(cid)["given_name"] == "Lia"
        assert not firm["store"].profile(cid)["consent"]["email"]
        panel.get_by_role("button", name="Refresh client list", exact=True).click()
        expect(page.locator(f'#client-rows tr[data-client="{cid}"]')).to_be_visible(timeout=60000)
        assert not errors
    finally:
        context.close()
