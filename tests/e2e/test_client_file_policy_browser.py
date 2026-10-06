"""Current protected policy decisions on fresh fictional cases; no live delivery."""
import json
import os
import threading
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import expect
import jobs

from cloud_daily_work_fixtures import cloud_world, cloud_app, cloud_server, ATTORNEY, STAFF, login, ok

# Fixture imports are intentional pytest injection.
# ruff: noqa: F401, F811

RUN = os.environ.get("FILE_POLICY_UI_RUN", "development")
SHOTS = Path(__file__).resolve().parents[2] / "docs/research/cloud_file_policy_ui_evidence/shots" / RUN


def create_case(base, cookie, name="Fictional File Policy Client"):
    search = ok(base, "/api/conflict-search", cookie, {"purpose": "add", "name": name})
    return ok(base, "/api/client-add", cookie, {"name": name, "language": "en", "filing": "i485",
        "invite": False, "conflict": {"search": search["id"], "decision": "none"}})["id"]


def screen(browser, base, cookie, client, width):
    context = browser.new_context(viewport={"width": width, "height": 900})
    key, value = cookie.split("=", 1)
    context.add_cookies([{"name": key, "value": value, "url": base}])
    page = context.new_page()
    page.set_default_timeout(60000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base)
    # Finish the fresh attorney's actual first-sign-in navigation before opening a case.
    expect(page.locator("#main").get_by_role("heading", name="Getting started", exact=True)).to_be_visible()
    page.get_by_role("button", name="All clients", exact=True).click()
    row = page.locator('tr.row[data-client="' + client + '"]')
    expect(row).to_be_visible()
    row.locator("details.rowmenu summary").click()
    row.get_by_role("button", name="Agreement and closing", exact=True).click()
    panel = page.locator("#file-policy")
    expect(panel).to_be_visible()
    expect(panel).to_contain_text("Case: " + client)
    return context, page, panel, errors


def capture(page, card, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    expect(page.locator("#toast")).to_be_hidden()
    card.evaluate("node => scrollTo(0, Math.max(0, scrollY + node.getBoundingClientRect().top - document.querySelector('.topbar').getBoundingClientRect().height - 16))")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(SHOTS / (name + "-viewport.png")))
    card.screenshot(path=str(SHOTS / (name + "-element.png")))


def submit(page, button, action, client):
    with page.expect_response(lambda response: "/api/engagement?" in response.url
                              and "client=" + client in response.url and response.request.method == "GET") as current:
        with page.expect_response(lambda response: response.url.endswith("/api/engagement")
                                  and response.request.method == "POST"
                                  and response.request.post_data_json.get("action") == action) as saved:
            button.click()
        assert saved.value.status == 200, saved.value.text()
    assert current.value.status == 200, current.value.text()
    data = current.value.json()
    expect(page.locator("#file-policy")).to_have_attribute("data-policy-revision", str(data["file_policy"]["revision"]))
    if data.get("file"):
        expect(page.locator("#client-file")).to_have_attribute("data-file-sha256", data["file"]["sha256"])
        for field, attribute in (("approved", "data-file-approved-at"), ("returned", "data-file-returned-at")):
            if data["file"].get(field):
                expect(page.locator("#client-file")).to_have_attribute(attribute, data["file"][field]["at"])
    return data["file_policy"]


def enter_jurisdiction(panel, reason="Fictional attorney reviewed the available source profile and this matter's jurisdiction."):
    panel.get_by_label("Jurisdiction profile", exact=True).select_option("MA-CLIENT-FILE-DRAFT")
    panel.get_by_label("Basis for applicable law", exact=True).select_option("principal_office")
    panel.get_by_label("Attorney admissions (one per line)", exact=True).fill("MA")
    panel.get_by_label("Attorney's principal office", exact=True).fill("Fictional Main Office")
    panel.get_by_label("Principal office jurisdiction", exact=True).fill("MA")
    panel.get_by_label("Attorney's applicability determination", exact=True).fill(reason)


def approve_jurisdiction(page, panel, client):
    panel.get_by_label("I reviewed the current official sources and their application to this matter.", exact=True).check()
    return submit(page, panel.get_by_role("button", name="Approve jurisdiction applicability", exact=True), "file_policy_approve", client)


@pytest.mark.parametrize("width", [1000, 1400])
def test_explicit_jurisdiction_recipient_and_minor_retention_are_separate(browser, cloud_world, cloud_app, cloud_server, width):
    base, world = cloud_server, cloud_world
    attorney = login(base, ATTORNEY)
    client = create_case(base, attorney)
    assert not world["sent"]
    context, page, panel, errors = screen(browser, base, attorney, client, width)
    try:
        expect(panel.get_by_label("Jurisdiction profile", exact=True)).to_have_value("")
        expect(panel.get_by_label("Basis for applicable law", exact=True)).to_have_value("unknown")
        expect(page.locator("#make-file")).to_be_disabled()
        assert "Florida Opinion 88-11" not in page.locator("body").inner_text()
        sources = panel.locator("#file-policy-sources")
        sources.locator("summary").click()
        expect(sources).to_contain_text("Draft reference")
        assert sources.get_by_role("link", name="Official rule 1.15A", exact=True).get_attribute("href").startswith("https://www.mass.gov/")
        assert sources.get_by_role("link", name="Official rule 8.5", exact=True).get_attribute("href").startswith("https://www.mass.gov/")
        capture(page, panel.locator("#file-policy-facts"), "explicit-empty-profile-" + str(width))
        page.get_by_label("Write a note", exact=True).fill("Fictional attorney work-product note selected for client-file review.")
        page.locator("#note-add").click()
        expect(page.locator("#note-list")).to_contain_text("Fictional attorney work-product note selected for client-file review.")
        panel.get_by_role("button", name="Reload current policy", exact=True).click()
        expect(panel.get_by_label("Inventory choice for clients/" + client + "/notes.json", exact=True)).to_be_visible()
        enter_jurisdiction(panel)
        panel.locator("#file-retention-facts summary").click()
        panel.get_by_role("button", name="Add preservation hold", exact=True).click()
        panel.get_by_label("Preservation hold description", exact=True).fill("Fictional active preservation order; protects destruction but permits protective file handover.")
        panel.get_by_label("This preservation hold is active", exact=True).check()
        facts = submit(page, panel.get_by_role("button", name="Save file-policy facts", exact=True), "file_policy_facts", client)
        assert facts["state"] == "review_required" and facts["facts"]["profile_id"] == "MA-CLIENT-FILE-DRAFT"
        policy = approve_jurisdiction(page, panel, client)
        assert policy["state"] == "approved" and policy["handover"]["policy_ready"]
        assert policy["applicability_approval"]["role"] == "attorney" and policy["applicability_approval"]["current"]
        assert policy["retention"]["state"] == policy["destruction"]["state"] == "held"
        assert policy["facts"]["holds"][0]["active"]
        expect(panel.locator("#file-policy-applicability")).to_contain_text(policy["applicability_approval"]["by"])
        capture(page, panel.locator("#file-policy-applicability"), "named-applicability-unknown-retention-" + str(width))
        inventory = panel.locator("#file-policy-inventory")
        for entry in policy["inventory"]["entries"]:
            include = entry["reviewable"]
            inventory.get_by_label("Inventory choice for " + entry["path"], exact=True).select_option("include" if include else "exclude")
            inventory.get_by_label("Inventory reason for " + entry["path"], exact=True).fill(
                "Fictional attorney reviewed this exact candidate and includes it in the client file."
                if include else "Fictional attorney excludes unsupported material pending a reviewed safe alternative.")
        policy = submit(page, inventory.get_by_role("button", name="Record current inventory review", exact=True), "inventory_review", client)
        assert policy["inventory"]["state"] == "approved" and policy["inventory"]["approval"]["current"]
        assert any(row["category"] == "work_product_notes" and row["inclusion"] == "include" for row in policy["inventory"]["entries"])
        capture(page, panel.locator("#file-policy-inventory"), "explicit-work-product-inventory-" + str(width))
        recipient = panel.locator("#file-policy-recipient")
        expect(recipient.get_by_label("Recipient authority", exact=True)).to_have_value("")
        expect(recipient.get_by_label("File delivery method", exact=True)).to_have_value("")
        recipient.get_by_label("File recipient name", exact=True).fill("Fictional Authorized Representative")
        recipient.get_by_label("Recipient authority", exact=True).select_option("authorized_representative")
        recipient.get_by_label("Evidence of recipient authority", exact=True).fill("Fictional attorney-reviewed recipient authorization; no external document or delivery.")
        recipient.get_by_label("File delivery method", exact=True).select_option("secure_transfer")
        recipient.get_by_label("Delivery destination", exact=True).fill("Fictional local handover destination")
        recipient.get_by_label("I reviewed this recipient's authority and delivery method.", exact=True).check()
        policy = submit(page, recipient.get_by_role("button", name="Save reviewed recipient", exact=True), "recipient_save", client)
        assert policy["recipient"]["current"] and policy["recipient"]["authority"] == "authorized_representative"
        assert policy["recipient"]["method"] == "secure_transfer" and not world["sent"]
        assert policy["handover"]["binding_sha256"] and policy["handover"]["recipient_sha256"]
        capture(page, panel.locator("#file-policy-recipient"), "explicit-recipient-no-delivery-" + str(width))
        submit(page, page.locator("#make-file"), "file", client)
        archive = page.locator("#client-file")
        expect(archive).to_contain_text("Case: " + client)
        with page.expect_download() as downloaded:
            archive.get_by_role("link", name="Download for review", exact=True).click()
        with zipfile.ZipFile(downloaded.value.path()) as bundle:
            note_path = "clients/" + client + "/CASE-NOTES.txt"
            note = bundle.read(note_path).decode()
            assert "Fictional attorney work-product note selected for client-file review." in note
            assert "attorney_only" not in note and "carried_from" not in note
        assert not ok(base, "/api/engagement?client=" + client, attorney)["file"].get("returned")
        submit(page, archive.get_by_role("button", name="Approve reviewed archive and draft cover", exact=True), "file_approve", client)
        archive.get_by_label("Actual file handover date", exact=True).fill(archive.get_by_label("Actual file handover date", exact=True).get_attribute("max"))
        archive.get_by_label("Actual delivery receipt reference", exact=True).fill("Fictional simulated recipient acknowledgement receipt; no external delivery occurred.")
        submit(page, archive.get_by_role("button", name="Record actual manual handover", exact=True), "returned", client)
        actual = ok(base, "/api/engagement?client=" + client, attorney)
        assert actual["file"]["returned"]["recipient"]["authority"] == "authorized_representative"
        assert actual["file"]["returned"]["receipt_reference"].startswith("Fictional simulated")
        assert actual["file_policy"]["destruction"]["state"] == "held" and actual["file_policy"]["facts"]["holds"][0]["active"]
        capture(page, page.locator("#client-file"), "manual-receipt-with-destruction-hold-" + str(width))
        panel.locator("#file-retention-facts summary").click()
        panel.get_by_role("button", name="Remove this hold", exact=True).click()
        panel.get_by_label("Matter type for retention review", exact=True).select_option("civil")
        panel.get_by_label("Client age at representation completion", exact=True).select_option("minor")
        panel.get_by_label("Attorney-determined representation completion", exact=True).fill("2026-09-01")
        panel.get_by_label("Attorney-determined date of majority", exact=True).fill("2030-01-01")
        panel.get_by_label("Originals held for the client", exact=True).select_option("none_held")
        panel.get_by_label("Other retention requirements", exact=True).select_option("reviewed_none")
        expect(panel.get_by_role("button", name="Approve jurisdiction applicability", exact=True)).to_be_disabled()
        policy = submit(page, panel.get_by_role("button", name="Save file-policy facts", exact=True), "file_policy_facts", client)
        assert policy["facts"]["date_of_majority"] == "2030-01-01" and policy["state"] == "review_required"
        assert not policy["applicability_approval"]["current"]
        policy = approve_jurisdiction(page, panel, client)
        panel.locator("#file-policy-retention summary").click()
        panel.get_by_label("Attorney-determined keep-through date", exact=True).fill("2036-01-01")
        panel.get_by_label("Retention determination", exact=True).fill("Fictional reviewed minor protection using the future majority date; no legal conclusion for a real client.")
        policy = submit(page, panel.get_by_role("button", name="Record retention approval", exact=True), "retention_review", client)
        assert policy["retention"]["state"] == "approved" and policy["retention"]["approval"]["keep_until"] == "2036-01-01"
        assert policy["destruction"]["state"] == "held" and policy["handover"]["policy_ready"]
        stale_file = ok(base, "/api/engagement?client=" + client, attorney)["file"]
        assert not stale_file["binding_current"] and stale_file["returned"]["receipt_reference"].startswith("Fictional simulated")
        panel.locator("#file-policy-retention summary").click()
        expect(panel.get_by_role("button", name="Record separate destruction approval", exact=True)).to_be_disabled()
        capture(page, panel.locator("#file-policy-retention"), "future-majority-inclusive-keep-through-" + str(width))
        SHOTS.mkdir(parents=True, exist_ok=True)
        (SHOTS / ("actual-policy-" + str(width) + ".json")).write_text(json.dumps(policy, indent=2) + "\n")
        assert not errors and not world["sent"]
    except BaseException:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / ("failure-core-" + str(width) + ".png")), full_page=True)
        raise
    finally:
        context.close()


def test_stale_policy_save_preserves_edits_and_requires_current_reread(browser, cloud_world, cloud_app, cloud_server):
    base = cloud_server
    attorney = login(base, ATTORNEY)
    client = create_case(base, attorney, "Fictional Concurrent Policy Client")
    context, page, panel, errors = screen(browser, base, attorney, client, 1000)
    try:
        enter_jurisdiction(panel)
        submit(page, panel.get_by_role("button", name="Save file-policy facts", exact=True), "file_policy_facts", client)
        current = approve_jurisdiction(page, panel, client)
        panel.get_by_label("Attorney's applicability determination", exact=True).fill("Unsaved fictional reviewer edit retained after stale write.")
        changed = dict(current["facts"], applicability_reason="Separate current fictional attorney determination.")
        latest = ok(base, "/api/engagement", attorney, {"client": client, "action": "file_policy_facts",
            "expected_revision": current["revision"], "facts": changed})["file_policy"]
        with page.expect_response(lambda response: response.url.endswith("/api/engagement") and response.request.method == "POST") as rejected:
            panel.get_by_role("button", name="Save file-policy facts", exact=True).click()
        assert rejected.value.status in {400, 409}, rejected.value.text()
        expect(panel.get_by_role("alert")).to_contain_text("Not confirmed")
        expect(panel.get_by_label("Attorney's applicability determination", exact=True)).to_have_value("Unsaved fictional reviewer edit retained after stale write.")
        expect(panel.get_by_role("button", name="Save file-policy facts", exact=True)).to_be_disabled()
        expect(panel.get_by_role("button", name="Approve jurisdiction applicability", exact=True)).to_be_disabled()
        expect(panel.locator("#file-policy-applicability")).to_contain_text("Current status needs rereading")
        capture(page, panel.locator("#file-policy-facts"), "stale-save-retained-edits-1000")
        panel.get_by_role("button", name="Reload current policy", exact=True).click()
        expect(panel.get_by_label("Attorney's applicability determination", exact=True)).to_have_value(changed["applicability_reason"])
        assert ok(base, "/api/engagement?client=" + client, attorney)["file_policy"]["revision"] == latest["revision"]
        assert not errors and not cloud_world["sent"]
    finally:
        context.close()


def test_late_other_case_policy_response_and_detached_controls_do_not_mutate_current_case(browser, cloud_world, cloud_app, cloud_server):
    base = cloud_server
    attorney = login(base, ATTORNEY)
    first = create_case(base, attorney, "Fictional First Policy Client")
    second = create_case(base, attorney, "Fictional Second Policy Client")
    context, page, panel, errors = screen(browser, base, attorney, second, 1000)
    held, posted = [], []
    try:
        old_button = panel.get_by_role("button", name="Save file-policy facts", exact=True).element_handle()

        def intercept(route):
            response = route.fetch()
            assert response.status == 200
            held.append((route, response))

        page.route("**/api/engagement?client=" + first, intercept)
        page.on("request", lambda request: posted.append(request.post_data_json)
                if request.url.endswith("/api/engagement") and request.method == "POST" else None)
        page.get_by_role("button", name="All clients", exact=True).click()
        row = page.locator('tr.row[data-client="' + first + '"]')
        expect(row).to_be_visible()
        row.locator("details.rowmenu summary").click()
        with page.expect_request(lambda request: request.url.endswith("/api/engagement?client=" + first)):
            row.get_by_role("button", name="Agreement and closing", exact=True).click()
        page.get_by_role("button", name="All clients", exact=True).click()
        row = page.locator('tr.row[data-client="' + second + '"]')
        expect(row).to_be_visible()
        row.locator("details.rowmenu summary").click()
        row.get_by_role("button", name="Agreement and closing", exact=True).click()
        expect(panel).to_contain_text("Case: " + second)
        assert held
        route, response = held.pop()
        route.fulfill(response=response)
        expect(panel).to_have_count(1)
        expect(panel).to_contain_text("Case: " + second)
        assert first not in panel.inner_text()
        old_button.evaluate("node => node.click()")
        assert not posted
        assert ok(base, "/api/engagement?client=" + first, attorney)["file_policy"]["revision"] == 0
        assert ok(base, "/api/engagement?client=" + second, attorney)["file_policy"]["revision"] == 0
        assert not errors and not cloud_world["sent"]
    finally:
        for route, response in held:
            route.fulfill(response=response)
        context.close()


def test_actual_worker_busy_save_preserves_edits_and_read_only_job_poll(browser, cloud_world, cloud_app, cloud_server, monkeypatch):
    """Diagnostic handler holds real worker locks; no model or reader executes."""
    base = cloud_server
    attorney = login(base, ATTORNEY)
    client = create_case(base, attorney, "Fictional Busy Policy Client")
    context, page, panel, errors = screen(browser, base, attorney, client, 1000)
    scope = cloud_world["scope"]
    running, release = threading.Event(), threading.Event()
    failures = []

    def held_worker(_, __, progress):
        progress(1, 1, "Fictional diagnostic waiting under actual worker locks")
        running.set()
        assert release.wait(30)
        return {"diagnostic_only": True, "reader_executed": False}

    monkeypatch.setitem(jobs.HANDLERS, "staff_upload", held_worker)
    queued = jobs.submit(scope.queue, "staff_upload", client=client, by="Fictional Attorney", args={})

    def work():
        try:
            assert jobs.work(scope.cases, scope.portal, once=True) == 1
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=work, daemon=True)
    try:
        enter_jurisdiction(panel, "Unsaved fictional applicability text preserved after a busy refusal.")
        thread.start()
        assert running.wait(5)
        polled = ok(base, "/api/jobs?client=" + client, attorney)
        assert any(row["id"] == queued["id"] and row["state"] == "running" for row in polled["jobs"])
        with page.expect_response(lambda response: response.url.endswith("/api/engagement")
                                  and response.request.method == "POST", timeout=20000) as refused:
            panel.get_by_role("button", name="Save file-policy facts", exact=True).click()
        assert refused.value.status in {409, 503}, refused.value.text()
        expect(panel.get_by_role("alert")).to_contain_text("Not confirmed")
        expect(panel.get_by_label("Attorney's applicability determination", exact=True)).to_have_value(
            "Unsaved fictional applicability text preserved after a busy refusal.")
        expect(panel.get_by_role("button", name="Save file-policy facts", exact=True)).to_be_disabled()
        capture(page, panel.locator("#file-policy-facts"), "real-worker-busy-retained-edits-1000")
        release.set()
        thread.join(5)
        assert not thread.is_alive() and not failures
        assert ok(base, "/api/engagement?client=" + client, attorney)["file_policy"]["revision"] == 0
        assert not errors and not cloud_world["sent"]
    finally:
        release.set()
        if thread.ident is not None:
            thread.join(5)
        context.close()
