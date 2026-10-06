"""Opt-in fictional staff+own-client Chromium over actual protected loopback HTTP."""
# ruff: noqa: F811 -- imported canonical pytest fixtures are injected by name
import os
from pathlib import Path
import re
import time

import pytest
from playwright.sync_api import expect

from portal.notify import Notifier
from test_prospects import firm, server, ok, CALL  # noqa: F401 -- fixtures
from test_staff_consent_http import client, wording, grant
from test_client_consent_browser import local_server

pytestmark = pytest.mark.skipif(not os.environ.get("E2E"), reason="E2E=1 requests real Chromium staff consent checks")
SHOTS = Path(os.environ.get("E2E_SHOTS", Path(__file__).resolve().parents[1] / "docs/research/cloud_session1_evidence/shots"))


@pytest.fixture
def staff_chromium():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None, chromium_sandbox=True, args=["--enable-automation"])
        if os.environ.get("E2E_SHOTS"):
            import json
            directory = Path(os.environ["E2E_SHOTS"])
            directory.mkdir(parents=True, exist_ok=True)
            session = browser.new_browser_cdp_session()
            (directory / "staff-chromium-launch.json").write_text(json.dumps(session.send("Browser.getBrowserCommandLine"), indent=2))
            session.detach()
        yield browser
        browser.close()


def staff_page(browser, srv, width=1000):
    context = browser.new_context(viewport={"width": width, "height": 900})
    cookie = srv["sam"].split("=", 1)
    context.add_cookies([{"name": cookie[0], "value": cookie[1], "url": srv["base"]}])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(srv["base"] + "/#all")
    expect(page.get_by_role("heading", name="Getting started", exact=True).first).to_be_visible(timeout=30000)
    page.locator("#all").click()
    expect(page.locator("#add-client-btn")).to_be_visible(timeout=30000)
    return context, page, errors


def panel_for(page, cid):
    row = page.locator(f'#client-rows tr.row[data-client="{cid}"]')
    row.locator("summary").click()
    row.get_by_role("button", name="Communication choices", exact=True).click()
    panel = page.locator(f'[data-communication="{cid}"]').first
    expect(panel.get_by_role("button", name="Check current permission")).to_be_visible()
    panel.get_by_text("Advanced communication records", exact=True).click()
    return panel


def choose_client(page, cid):
    page.locator("#client").click()
    page.get_by_label("Find a client", exact=True).fill(cid)
    expect(page.locator("#client-pick .pick-item").filter(has_text=cid)).to_be_visible()
    page.get_by_label("Find a client", exact=True).press("Enter")


def shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    expect(page.locator("#toast")).to_be_hidden(timeout=15000)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    target = SHOTS / name
    if target.exists() and not (SHOTS / (target.stem + "-initial.png")).exists():
        target.rename(SHOTS / (target.stem + "-initial.png"))
    page.evaluate("scrollTo(0, 0); document.querySelectorAll('section.group').forEach(e => {if(e.querySelector('#client-rows')) e.scrollLeft=0;})")
    page.screenshot(path=str(target), full_page=True)
    page.screenshot(path=str(SHOTS / (target.stem + "-viewport.png")))


@pytest.mark.parametrize("width", [1000, 1400])
def test_create_assisted_actual_signoff_and_separate_accepted_invite(staff_chromium, server, firm, monkeypatch, width):
    wording(server)
    accepted = []
    monkeypatch.setattr(Notifier, "_email", lambda self, to, subject, body, kind: accepted.append(body) or "sent")
    context, page, errors = staff_page(staff_chromium, server, width)
    try:
        with local_server(firm["portal"]) as origin:
            monkeypatch.setenv("PORTAL_BASE_URL", origin)
            page.locator("#add-client-btn").click()
            page.get_by_label("Client's full name", exact=True).fill("Fictional Browser Client")
            page.get_by_label("Client's phone number", exact=True).fill("(555) 010-9876")
            page.get_by_label("Client's email address", exact=True).fill("fictional-browser@example.test")
            page.get_by_label("Client's language", exact=True).select_option("en")
            expect(page.locator("#add-invite")).to_be_disabled()
            page.locator("#add-client-save").click()
            cid = "fictional-browser-client"
            expect(page.locator(f'#client-rows tr.row[data-client="{cid}"]')).to_be_visible()
            panel = panel_for(page, cid)
            expect(panel.get_by_role("button", name="Send separate access invitation")).to_have_count(0)
            expect(panel.get_by_text("Office/manual follow-up: no current approved channel.", exact=True)).to_be_visible()
            assert not accepted
            shot(page, f"staff-assisted-before-signoff-{width}.png")
            panel.get_by_role("button", name="Assist client signoff", exact=True).click()
            link = page.get_by_label("The client's consent-only signoff link").input_value()
            phone_context = staff_chromium.new_context(viewport={"width": 390, "height": 844})
            phone = phone_context.new_page()
            try:
                phone.goto(link)
                expect(phone.locator("#content")).to_be_visible()
                assert phone.url == origin + "/consent"
                assert phone.request.get(origin + "/api/me").status == 401
                phone.locator("#typedName").fill("Fictional Browser Client")
                phone.locator('input[name=channel][value=email]').check()
                phone.locator("#agree").check()
                phone.locator("#save").click()
                expect(phone.locator("#status")).to_contain_text("Your choice was saved")
                assert phone.request.get(origin + "/api/me").status == 401
                page.locator("#link-panel").get_by_role("button", name="Close", exact=True).click()
                panel.get_by_role("button", name="Check current permission").click()
                expect(panel.get_by_role("button", name="Send separate access invitation")).to_be_visible()
                assert not accepted
                panel.get_by_role("button", name="Send separate access invitation").click()
                expect(page.get_by_text("Invitation: Sent by email", exact=False)).to_be_visible()
                assert len(accepted) == 1
                delivered_link = re.search(r"https?://[^\s]+/l/[A-Za-z0-9_-]+", accepted[0]).group()
                phone.goto(delivered_link)
                assert phone.request.get(origin + "/api/me").status == 200
                assert firm["store"].profile(cid).get("last_invite_at")
                assert ok(server, "jane", "/api/communication?client=" + cid)["phone_access"] == "office_only"
                shot(page, f"staff-accepted-invitation-{width}.png")
            finally:
                phone_context.close()
        assert not errors
    finally:
        context.close()


@pytest.mark.parametrize("width", [1000, 1400])
def test_ux04_revocation_hides_ask_and_request_fallback_is_explicit(staff_chromium, server, firm, monkeypatch, width):
    cid = "case-ana"
    firm["store"].add_client(cid, "Ana Clara Exemplo Souza", email="ana-fictional@example.test", language="pt")
    grant(server, cid, lang="pt")
    calls = []
    monkeypatch.setattr(Notifier, "_email", lambda *args: calls.append(args) or "sent")
    context, page, errors = staff_page(staff_chromium, server, width)
    try:
        page.goto(server["base"] + "/?tab=fix#" + cid)
        expect(page.locator("#client")).to_have_value(cid)
        expect(page.get_by_role("button", name="Ask the client", exact=True).first).to_be_visible()
        request = ok(server, "jane", "/api/ask", {"client": cid, "text": "Fictional choice?", "type": "choice", "queue": True,
            "options": ["A", "B"], "text_client": "", "options_client": ["", ""]})["request"]
        page.reload()
        held = page.locator("#requests")
        expect(held.get_by_text("Held:", exact=False)).to_be_visible()
        held.get_by_text("Review request wording / English choice", exact=True).click()
        review = held.locator(".ask")
        review.get_by_label("Request wording review mode", exact=True).select_option("english_fallback")
        review.get_by_label("Actual evidence file (JSON or PDF)", exact=True).set_input_files({"name": "fictional-request-review.json", "mimeType": "application/json", "buffer": b'{"fictional":true,"actual_choice":"whole English fallback"}'})
        review.get_by_label("Deliberate English fallback reason", exact=True).fill("Actual fictional deliberate choice of complete English question and choices")
        review.get_by_label("Publish after actual review", exact=True).check()
        review.get_by_role("button", name="Save actual request review", exact=True).click()
        expect(page.get_by_text("Request published. Nothing sent.", exact=False)).to_be_visible()
        assert not calls
        saved = next(row for row in firm["store"].requests(cid) if row["id"] == request["id"])
        assert saved["language_review"]["mode"] == "english_fallback" and saved["status"] == "open"
        page.locator('details').filter(has=page.locator('[data-communication="case-ana"]')).first.locator("summary").first.click()
        panel = page.locator('[data-communication="case-ana"]')
        panel.get_by_text("Advanced communication records", exact=True).click()
        panel.get_by_role("button", name="Revoke communication permission", exact=True).click()
        expect(page.get_by_role("button", name="Ask the client", exact=True)).to_have_count(0)
        expect(page.locator(".office-contact:visible").first).to_be_visible()
        assert not calls
        shot(page, f"staff-ux04-revoked-{width}.png")
        page.locator("article").filter(has=page.locator(".office-contact:visible")).first.screenshot(path=str(SHOTS / f"staff-ux04-office-card-{width}.png"))
        assert not errors
    finally:
        context.close()


def test_actual_wording_review_separates_qualified_review_and_long_evidence_layout(staff_chromium, server, firm):
    context, page, errors = staff_page(staff_chromium, server, 1000)
    try:
        page.locator("#settings").click()
        page.get_by_label("Client wording language", exact=True).select_option("ht")
        panel = page.locator("#client-wording-panel")
        expect(panel.get_by_text("Attorney review of the current wording is required.", exact=True)).to_be_visible()
        panel.get_by_label("Actual evidence file (JSON or PDF)", exact=True).set_input_files({"name": "fictional-review.json", "mimeType": "application/json", "buffer": b'{"fictional":true,"actual_review":"reviewed all wording"}'})
        panel.get_by_role("button", name="Record actual attorney wording review", exact=True).click()
        expect(panel.get_by_text("Separate qualified translation review is required.", exact=True)).to_be_visible()
        panel.get_by_label("Actual qualified reviewer", exact=True).fill("Fictional Qualified Reviewer " * 5)
        panel.get_by_label("Actual reviewer qualification", exact=True).fill("Actual fictional qualified language review " * 15)
        panel.get_by_role("button", name="Record actual qualified translation review", exact=True).click()
        expect(panel.get_by_text("Current wording reviewed", exact=True)).to_be_visible()
        shot(page, "staff-wording-separate-qualified-review-1000.png")
        assert not errors
    finally:
        context.close()


def test_case_navigation_discards_delayed_old_communication(staff_chromium, server, firm):
    firm["store"].add_client("case-ana", "Ana Clara Exemplo Souza", email="ana@example.test", language="en")
    firm["store"].add_client("case-bia", "Beatriz Exemplo Lima", email="bia@example.test", language="en")
    grant(server, "case-ana")
    context, page, errors = staff_page(staff_chromium, server)
    delayed = []
    def delay(route):
        delayed.append((route, route.fetch()))
    page.route("**/api/communication?client=case-ana", delay)
    try:
        choose_client(page, "case-ana")
        page.wait_for_function("document.querySelector('#client').value === 'case-ana'")
        deadline = time.monotonic() + 10
        while not delayed and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        assert delayed
        choose_client(page, "case-bia")
        expect(page.get_by_role("heading", name="Beatriz Exemplo Lima", exact=True)).to_be_visible()
        for route, response in delayed:
            route.fulfill(response=response)
        page.wait_for_function("S.client === 'case-bia' && S.data && S.data.communication && !S.data.communication.can_send")
        expect(page.get_by_role("heading", name="Beatriz Exemplo Lima", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Ask the client", exact=True)).to_have_count(0)
        assert not errors
    finally:
        context.close()


def test_current_revocation_before_send_and_long_contact_language_controls(staff_chromium, server, firm, monkeypatch):
    from communication_fixture import accepted_link
    cid = client(server, name="Fictional Contact Browser")
    grant(server, cid)
    calls = []
    monkeypatch.setattr(Notifier, "_email", lambda *args: calls.append(args) or "sent")
    context, page, errors = staff_page(staff_chromium, server)
    try:
        panel = panel_for(page, cid)
        expect(panel.locator('[data-communication-identity]')).to_contain_text("Fictional Contact Browser")
        expect(panel.get_by_role("button", name="Send separate access invitation")).to_be_visible()
        ok(server, "jane", "/api/communication", {"client": cid, "action": "revoke", "channels": ["email"]})
        panel.get_by_role("button", name="Send separate access invitation").click()
        expect(page.get_by_text("Invitation held. Actual current signoff is required.", exact=True)).to_be_visible()
        assert not calls
        expect(page.locator('[data-communication-open]')).to_have_count(1)
        panel = page.locator('[data-communication-open]')
        panel.get_by_text("Advanced communication records", exact=True).click()
        panel.get_by_text("Contact and language controls", exact=True).click()
        panel.get_by_label("Current client email", exact=True).fill("fictional-" + "x" * 45 + "@" + ("d" * 55 + ".") * 3 + "example.test")
        panel.get_by_label("Current client phone", exact=True).fill("(555) 010-1234")
        shot(page, "staff-long-contact-controls-1000.png")
        panel.locator("details").filter(has=page.get_by_label("Current client email", exact=True)).last.screenshot(path=str(SHOTS / "staff-long-contact-fields-1000.png"))
        panel.get_by_role("button", name="Save contact change", exact=True).click()
        expect(panel.get_by_text("Contact change saved;", exact=False)).to_be_visible()
        assert not calls and not ok(server, "jane", "/api/communication?client=" + cid)["can_send"]
        grant(server, cid)
        token = accepted_link(firm["store"], cid)
        firm["store"].save_answers(cid, {"fictional_retained": "Original retained answer"})
        with local_server(firm["portal"]) as origin:
            phone_context = staff_chromium.new_context()
            phone = phone_context.new_page()
            try:
                phone.goto(origin + "/l/" + token)
                assert phone.request.get(origin + "/api/me").status == 200
                panel.get_by_text("Contact and language controls", exact=True).click()
                panel.get_by_label("Current client language", exact=True).select_option("pt")
                panel.get_by_role("button", name="Change client language", exact=True).click()
                expect(panel.get_by_text("Language changed. Retained answers/uploads stay saved.", exact=False)).to_be_visible()
                assert phone.request.get(origin + "/api/me").status == 401
                assert firm["store"].answers(cid)["fictional_retained"] == "Original retained answer"
                assert not calls
            finally:
                phone_context.close()
        assert not errors
    finally:
        context.close()


def test_actual_missing_profile_recovery_controls_keep_same_identity(staff_chromium, server, firm, monkeypatch):
    from portal import contact_transitions as transitions
    cid = "fictional-interrupted-browser"
    (firm["clients"] / cid).mkdir()
    with monkeypatch.context() as fault:
        save = transitions._save
        def crash(*args):
            save(*args)
            if (args[-1].get("transition") or {}).get("enrollment"):
                raise OSError("fictional interrupted enrollment")
        fault.setattr(transitions, "_save", crash)
        with pytest.raises(OSError):
            firm["store"].add_client(cid, "Fictional Interrupted Browser", email="interrupted-browser@example.test", language="en")
    assert not (firm["store"].client_dir(cid) / "profile.json").exists()
    context, page, errors = staff_page(staff_chromium, server)
    try:
        page.get_by_text("Recover interrupted client/contact creation", exact=True).click()
        page.get_by_label("Recorded client or prospect ID", exact=True).fill(cid)
        page.get_by_role("button", name="Inspect recorded recovery", exact=True).click()
        expect(page.get_by_text("Name: Fictional Interrupted Browser", exact=True)).to_be_visible()
        expect(page.get_by_text("Interrupted operation pending. Access remains held.", exact=True)).to_be_visible()
        shot(page, "staff-missing-profile-recovery-1000.png")
        page.get_by_role("button", name="Recover this recorded operation", exact=True).click()
        expect(page.get_by_text("Recovered " + cid, exact=False)).to_be_visible()
        assert firm["store"].profile(cid)["name"] == "Fictional Interrupted Browser"
        assert not (firm["store"].client_dir(cid + "-2")).exists()
        assert not ok(server, "sam", "/api/communication?client=" + cid)["can_send"]
        page.get_by_role("button", name="Inspect recorded recovery", exact=True).click()
        expect(page.get_by_role("button", name="Recover this recorded operation", exact=True)).to_have_count(0)
        assert not errors
    finally:
        context.close()


def test_prospect_prepare_assisted_own_signoff_separate_access(staff_chromium, server, firm, monkeypatch):
    pid = ok(server, "sam", "/api/prospect-new", CALL | {"name": "Fictional Browser Prospect", "language": "en", "email": "browser-prospect@example.test"})["id"]
    wording(server)
    accepted = []
    monkeypatch.setattr(Notifier, "_email", lambda self, to, subject, body, kind: accepted.append(body) or "sent")
    context, page, errors = staff_page(staff_chromium, server)
    try:
        with local_server(firm["portal"]) as origin:
            monkeypatch.setenv("PORTAL_BASE_URL", origin)
            page.locator("#prospects-btn").click()
            page.locator("tr").filter(has_text="Fictional Browser Prospect").get_by_role("button", name="Open", exact=True).click()
            panel = page.locator('[data-communication="' + pid + '"]')
            expect(panel.locator('[data-communication-identity]')).to_contain_text("Fictional Browser Prospect")
            panel.get_by_text("Advanced communication records", exact=True).click()
            panel.get_by_role("button", name="Prepare assisted signoff profile", exact=True).click()
            expect(panel.get_by_text("Prospect consent profile prepared. Nothing sent.", exact=False)).to_be_visible()
            panel.get_by_role("button", name="Assist client signoff", exact=True).click()
            link = panel.locator('[data-assisted-link] code').inner_text()
            phone_context = staff_chromium.new_context(viewport={"width": 390, "height": 844})
            phone = phone_context.new_page()
            try:
                phone.goto(link)
                expect(phone.locator("#typedName")).to_be_visible()
                assert phone.request.get(origin + "/api/me").status == 401
                phone.locator("#typedName").fill("Fictional Browser Prospect")
                phone.locator('input[name=channel][value=email]').check()
                phone.locator("#agree").check()
                phone.locator("#save").click()
                expect(phone.locator("#status")).to_contain_text("Your choice was saved")
                assert phone.request.get(origin + "/api/me").status == 401 and not accepted
                panel.get_by_role("button", name="Check current permission").click()
                expect(panel.get_by_role("button", name="Send separate access invitation")).to_be_visible()
                panel.get_by_role("button", name="Send separate access invitation").click()
                expect(panel.get_by_text("Invitation: Sent by email", exact=False)).to_be_visible()
                assert len(accepted) == 1
                delivered_link = re.search(r"https?://[^\s]+/l/[A-Za-z0-9_-]+", accepted[0]).group()
                phone.goto(delivered_link)
                assert phone.request.get(origin + "/api/me").status == 200
                shot(page, "staff-prospect-assisted-separate-invite-1000.png")
            finally:
                phone_context.close()
        assert not errors
    finally:
        context.close()


def test_wording_read_and_evidence_upload_bind_clicked_language(staff_chromium, server, firm):
    wording(server, "en")
    context, page, errors = staff_page(staff_chromium, server)
    delayed_read, delayed_upload = [], []
    page.route("**/api/client-wording?language=en", lambda route: delayed_read.append((route, route.fetch())))
    try:
        page.locator("#settings").click()
        language = page.get_by_label("Client wording language", exact=True)
        expect(language).to_be_visible()
        deadline = time.monotonic() + 10
        while not delayed_read and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        assert delayed_read
        language.select_option("ht")
        panel = page.locator("#client-wording-panel")
        expect(panel.get_by_text("Attorney review of the current wording is required.", exact=True)).to_be_visible()
        for route, response in delayed_read:
            route.fulfill(response=response)
        page.wait_for_timeout(100)
        expect(language).to_have_value("ht")
        expect(panel.get_by_text("Attorney review of the current wording is required.", exact=True)).to_be_visible()
        page.unroute("**/api/client-wording?language=en")
        def hold_evidence(route):
            if route.request.post_data_json.get("action") == "evidence":
                delayed_upload.append((route, route.fetch()))
            else:
                route.continue_()
        page.route("**/api/client-wording", hold_evidence)
        panel.get_by_label("Actual evidence file (JSON or PDF)", exact=True).set_input_files({"name": "fictional-held-review.json", "mimeType": "application/json", "buffer": b'{"fictional":true,"actual_review":"Haitian Creole current wording"}'})
        panel.get_by_role("button", name="Record actual attorney wording review", exact=True).click()
        expect(language).to_be_disabled()
        expect(panel.get_by_role("button", name="Record actual qualified translation review", exact=True)).to_be_disabled()
        deadline = time.monotonic() + 10
        while not delayed_upload and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        assert delayed_upload
        for route, response in delayed_upload:
            route.fulfill(response=response)
        expect(language).to_be_enabled()
        expect(language).to_have_value("ht")
        expect(panel.get_by_text("Separate qualified translation review is required.", exact=True)).to_be_visible()
        assert ok(server, "sam", "/api/client-wording?language=ht")["reason"] == "qualified_translation_review_required"
        assert ok(server, "sam", "/api/client-wording?language=en")["ready"]
        language.select_option("en")
        expect(panel.get_by_text("Current wording reviewed", exact=True)).to_be_visible()
        assert not errors
    finally:
        context.close()
