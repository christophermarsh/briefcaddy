"""One fresh UI intake composed with all eight daily-work improvements.

Only fictional retained sources, approval evidence and local provider acceptance.
The fresh fixture supplies accounts/installation, not completed cases or approvals.
"""
# ruff: noqa: F811, F401 -- imported current canonical fixtures use pytest injection
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import second_factor
from cloud_daily_work_fixtures import (
    ATTORNEY,
    EMAIL,
    I94,
    MARRIAGE,
    NAME,
    NTA,
    OTHER_STAFF,
    PASSWORD,
    PHONE,
    PORTAL_ANSWERS,
    REMOTE,
    STAFF,
    cloud_app,
    cloud_server,
    cloud_world,
    field_card,
    install_selected_drive,
    last_token,
    pdf,
    review_source_subjects,
    work,
)
from playwright.sync_api import Error as PlaywrightError, expect
from test_client_consent_browser import local_server

import firmsecrets
import jobs
from review.state import reviewed_graph

pytestmark = pytest.mark.skipif(not os.environ.get("E2E"), reason="E2E=1 requests composed real Chromium daily work")
SHOTS = Path(__file__).resolve().parents[2] / "docs/research/cloud_session3_evidence/shots/composed14"
TABS = {"fix": "Needs attention", "check": "Check answers", "attorney": "Attorney sign-off", "done": "Decision log"}


def capture(page, card, name):
    """Keep the actual fixed header and the relevant card readable."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        # The original strict locator resolves the current DOM on every
        # operation; no stale element handle is reused after a refresh.
        current = card
        expect(current).to_be_visible()
        expect(page.locator("#toast")).to_be_hidden(timeout=15000)
        current.evaluate("node => window.scrollTo(0, Math.max(0, scrollY + node.getBoundingClientRect().top - document.querySelector('.topbar').getBoundingClientRect().height - 16))")
        assert current.evaluate("node => node.getBoundingClientRect().top >= document.querySelector('.topbar').getBoundingClientRect().bottom")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(SHOTS / (name + "-viewport.png")))
        try:
            current.screenshot(path=str(SHOTS / (name + "-card.png")))
        except PlaywrightError as error:
            if attempt == 2 or "Element is not attached to the DOM" not in str(error):
                raise
        else:
            return


def login(browser, base, email, width, password=PASSWORD, *, change_password=False):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page(); page.set_default_timeout(15000)
    errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base)
    page.locator("input[name=email]").fill(email)
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name="Sign in", exact=True).click()
    if change_password:
        expect(page.get_by_role("heading", name="Choose your password", exact=True)).to_be_visible(timeout=60000)
        page.locator("input[name=new_password]").fill(PASSWORD)
        page.locator("input[name=again]").fill(PASSWORD)
        page.locator('form button[type="submit"]').click()
    if email == ATTORNEY:
        expect(page.locator("input[name=code]")).to_be_visible(timeout=60000)
        page.locator("input[name=code]").fill(second_factor._RECOVERY[email].pop())
        with page.expect_response(lambda r: r.url.endswith("/api/getting-started") and r.request.method == "POST", timeout=60000) as seen:
            page.locator('form button[type="submit"]').click()
        assert seen.value.status == 200
        expect(page.locator("#main").get_by_role("heading", name="Getting started", exact=True)).to_be_visible(timeout=60000)
    expect(page.locator("#my-cases")).to_be_visible(timeout=60000)
    return context, page, errors


def api(page, base, route, body=None):
    response = page.request.get(base + route) if body is None else page.request.post(base + route, data=body, headers={"X-Review-App": "1"})
    assert response.status == 200, (route, response.status, response.text())
    return response.json()


def open_tab(page, label):
    page.get_by_role("navigation", name="Review queues").get_by_role("button", name=label, exact=False).click()


def choose_client(page, cid):
    page.locator("#client").click()
    page.get_by_label("Find a client", exact=True).fill(cid)
    expect(page.locator("#client-pick .pick-item").filter(has_text=cid)).to_be_visible(timeout=60000)
    page.get_by_label("Find a client", exact=True).press("Enter")


def panel_for(page, cid):
    row = page.locator(f'#client-rows tr.row[data-client="{cid}"]')
    row.locator("summary").click()
    row.get_by_role("button", name="Communication choices", exact=True).click()
    panel = page.locator(f'[data-communication="{cid}"]').first
    expect(panel.get_by_role("button", name="Check current permission", exact=True)).to_be_visible()
    return panel


def current_card(page, base, client, key):
    items = api(page, base, "/api/items?client=" + client)
    row = next(card for card in items["cards"] if any(fact["key"] == key for fact in card["facts"]))
    open_tab(page, TABS[row["tab"]])
    show = page.get_by_role("button", name="Show all", exact=True)
    if show.is_visible():
        show.click()
    card = page.locator('article[data-review-card]').filter(has=page.locator('[data-fact-key="' + key + '"]'))
    if card.count() == 0:
        card = page.locator('article[data-review-card="' + row["id"] + '"]')
    expect(card).to_be_visible(timeout=60000)
    return row, card


def add_client(page, name, email, phone=PHONE):
    page.locator("#all").click()
    page.locator("#add-client-btn").click()
    page.get_by_label("Client's full name", exact=True).fill(name)
    page.get_by_label("Client's phone number", exact=True).fill(phone)
    page.get_by_label("Client's email address", exact=True).fill(email)
    page.get_by_label("Client's language", exact=True).select_option("en")
    page.get_by_label("Questionnaire the client answers", exact=True).select_option("i485")
    expect(page.locator("#add-invite")).to_be_disabled()
    with page.expect_response(lambda r: r.url.endswith("/api/client-add") and r.request.method == "POST", timeout=60000) as made:
        with page.expect_response(lambda r: r.url.endswith("/api/conflict-search") and r.request.method == "POST", timeout=60000) as found:
            page.locator("#add-client-save").click()
        assert found.value.status == 200
        if found.value.json()["hits"]:
            # This fictional spouse's retained source creates a real weak hit.
            # Review and record the actual intake decision instead of skipping it.
            expect(page.get_by_role("radiogroup", name="What you decided", exact=True)).to_contain_text("No conflict")
            page.get_by_role("radio", name="No conflict.", exact=True).check()
            page.locator("#add-client-save").click()
    assert made.value.status == 200, made.value.text()
    cid = made.value.json()["id"]
    expect(page.locator(f'#client-rows tr[data-client="{cid}"]')).to_be_visible(timeout=60000)
    return cid


@pytest.mark.parametrize("width", [1000, 1400])
def test_fresh_intake_worker_drive_all_eight_ux_and_assignment(browser, cloud_world, cloud_app, cloud_server, monkeypatch, width):
    world, base = cloud_world, cloud_server
    scope, store = world["scope"], world["store"]
    assert not list(scope.cases.iterdir())
    attorney_context, attorney, attorney_errors = login(browser, base, ATTORNEY, width)
    staff_context = client_context = None
    search_trace, source_trace = [], []
    def record_search(response):
        if "/api/case-list?" in response.url:
            search_trace.append({"url": response.url, "status": response.status, "result": response.json()})
        if "/api/crop?" in response.url and response.request.resource_type == "image":
            source_trace.append({"url": response.url, "status": response.status, "content_type": response.headers.get("content-type")})
    def loaded_source(figure):
        figure.scroll_into_view_if_needed()
        image = figure.locator("img.scan")
        expect(image).to_have_js_property("complete", True, timeout=60000)
        assert image.evaluate("node => node.naturalWidth > 0")
        current_url = base + image.get_attribute("src")
        parsed = parse_qs(urlparse(current_url).query)
        assert parsed["expected_sha256"] and parsed["page"] == ["0"]
        assert any(row["url"] == current_url and row["status"] == 200 and row["content_type"].startswith("image/") for row in source_trace)
    try:
        # Actual Settings Add staff and one-time password, no seeded third account.
        attorney.locator("#settings").click()
        staff_box = attorney.locator("#set-staff")
        staff_box.get_by_label("New person's full name", exact=True).fill("Fictional Transfer Staff")
        staff_box.get_by_label("New person's work email", exact=True).fill(OTHER_STAFF)
        staff_box.get_by_label("New person's role", exact=True).select_option("paralegal")
        with attorney.expect_response(lambda r: r.url.endswith("/api/staff") and r.request.method == "POST") as added:
            staff_box.get_by_role("button", name="Add the person", exact=True).click()
        assert added.value.status == 200
        expect(staff_box.locator("#staff-rows")).to_contain_text(OTHER_STAFF)
        expect(staff_box.locator("#one-time code")).to_have_text(added.value.json()["one_time_password"])
        staff_context, staff, staff_errors = login(browser, base, OTHER_STAFF, width,
            password=added.value.json()["one_time_password"], change_password=True)
        staff.on("response", record_search)

        # Actual current English wording evidence on the visible Settings card.
        wording = attorney.locator("section.group").filter(has=attorney.get_by_role("heading", name="Current client wording review", exact=True))
        wording.get_by_label("Client wording language", exact=True).select_option("en")
        wording.get_by_label("Actual evidence file (JSON or PDF)", exact=True).set_input_files({"name": "fictional-current-review.json", "mimeType": "application/json",
            "buffer": b'{"fictional":true,"review":"Actual current complete fictional notice review"}'})
        with attorney.expect_response(lambda r: r.url.endswith("/api/client-wording") and r.request.method == "POST" and r.request.post_data_json.get("action") == "attorney_review") as reviewed:
            wording.get_by_role("button", name="Record actual attorney wording review", exact=True).click()
        assert reviewed.value.status == 200
        expect(wording).to_contain_text("Current wording reviewed", timeout=60000)
        capture(attorney, wording, "actual-settings-wording-card-" + str(width))

        cid = add_client(staff, NAME, EMAIL)
        world["client"] = cid; case = scope.cases / cid
        profile = store.profile(cid)
        assert profile["phone"] == PHONE and profile["language"] == "en" and profile["filing"] == "i485"
        assert not profile.get("invited_at") and not world["sent"]
        comm = panel_for(staff, cid)
        expect(comm.get_by_role("button", name="Send separate access invitation", exact=True)).to_have_count(0)
        expect(comm).to_contain_text("Office/manual follow-up")
        capture(staff, comm, "actual-add-client-office-only-" + str(width))

        client_context = browser.new_context(viewport={"width": 390, "height": 844})
        own = client_context.new_page()
        with local_server(scope.portal) as origin:
            monkeypatch.setenv("PORTAL_BASE_URL", origin)
            attorney.locator("#all").click()
            counsel_comm = panel_for(attorney, cid)
            counsel_comm.get_by_role("button", name="Assist client signoff", exact=True).click()
            assisted = attorney.get_by_label("The client's consent-only signoff link", exact=True).input_value()
            own.goto(assisted)
            expect(own.locator("#content")).to_be_visible(timeout=60000)
            assert own.request.get(origin + "/api/me").status == 401
            own.locator("#typedName").fill(NAME)
            own.locator('input[name=channel][value=email]').check()
            own.locator("#agree").check()
            own.locator("#save").click()
            expect(own.locator("#status")).to_contain_text("Your choice was saved", timeout=60000)
            assert not world["sent"] and own.request.get(origin + "/api/me").status == 401
            comm.get_by_role("button", name="Check current permission", exact=True).click()
            expect(comm.get_by_role("button", name="Send separate access invitation", exact=True)).to_be_visible(timeout=60000)
            comm.get_by_role("button", name="Send separate access invitation", exact=True).click()
            expect(staff.locator("#toast")).to_contain_text("Invitation", timeout=60000)
            assert len(world["sent"]) == 1
            own.goto(origin + "/l/" + last_token(world))
            expect(own.locator("#main h1")).to_be_visible(timeout=60000)
            assert own.request.get(origin + "/api/me").status == 200
            # Explicit own-client typed questionnaire answers, then actual retained PDF upload.
            answers = own.request.put(origin + "/api/answers", data=PORTAL_ANSWERS, headers={"X-Portal": "1"})
            assert answers.status == 200 and not answers.json()["errors"]
            upload = own.request.post(origin + "/api/upload", multipart={"doc_id": "i94", "attempt": "1" * 32,
                "file": {"name": "Fictional I94.pdf", "mimeType": "application/pdf", "buffer": pdf(I94)}}, headers={"X-Portal": "1"})
            assert upload.status == 200 and upload.json()["upload_outcome"]["received"]
            staff.locator("#all").click()
            row = staff.locator(f'#client-rows tr[data-client="{cid}"]')
            row.locator("summary").click()
            row.get_by_role("button", name="Source setup / recovery", exact=True).click()
            setup = staff.locator(f'[data-source-setup="{cid}"]')
            with staff.expect_response(lambda r: r.url.endswith("/api/source-setup") and r.request.method == "POST") as source:
                setup.get_by_role("button", name="Set up or recover sources", exact=True).click()
            assert source.value.status == 200
            assert work(world) >= 1 and (case / "fact_graph_raw.json").is_file()
            choose_client(staff, cid)
            open_tab(staff, "Documents")
            with staff.expect_response(lambda r: r.url.endswith("/api/client-upload") and r.request.method == "POST") as office:
                staff.locator('#dropzone input[type="file"]').set_input_files({"name": "Fictional NTA.pdf", "mimeType": "application/pdf", "buffer": pdf(NTA)})
            assert office.value.status == 200 and office.value.json()["received"]
            upload_attempt = staff.locator('[data-staff-upload-attempt]')
            expect(upload_attempt.get_by_role("button", name="Check outcome", exact=True)).to_be_enabled(timeout=60000)
            expect(upload_attempt.locator("[data-upload-status]")).to_contain_text("Received", timeout=60000)
            assert work(world) >= 1
            assert jobs.get(scope.queue, office.value.json()["job"]["id"])["state"] == "done"
            open_tab(staff, "Documents")
            nta_row = staff.locator("#documents tr").filter(has_text="Notice to Appear (Form I-862)")
            expect(nta_row).to_be_visible(timeout=60000)
            nta_original = parse_qs(urlparse(nta_row.get_by_role("link", name="Open original page 1", exact=True).get_attribute("href")).query)
            assert "Fictional_NTA" in nta_original["doc"][0] and len(nta_original["expected_sha256"][0]) == 64
            with staff.expect_response(lambda r: "/api/staff-upload-outcome?" in r.url) as outcome:
                staff.locator('[data-staff-upload-attempt]').get_by_role("button", name="Check outcome", exact=True).click()
            assert outcome.value.status == 200 and outcome.value.json()["processed"]
            expect(staff.locator('[data-staff-upload-attempt] [data-upload-status]')).to_contain_text("Read.", timeout=60000)
            expect(staff.locator('[data-staff-upload-attempt]').get_by_role("button", name="Check outcome", exact=True)).to_be_enabled(timeout=60000)
            expect(staff.locator("#documents")).to_be_visible(timeout=60000)
            capture(staff, staff.locator("#documents"), "actual-staff-worker-retained-sources-" + str(width))
            # Installed selected Drive adapter, actual settings/preview/enqueue.
            provider = install_selected_drive(world, monkeypatch)
            vault = firmsecrets.get
            monkeypatch.setattr(firmsecrets, "get", lambda name, **kw: vault(name, **kw)
                if name == "gdrive.service_account" and kw.get("env") == {} and kw.get("data_root") == scope.data else None)
            attorney.locator("#settings").click()
            drive_settings = attorney.locator("#drive-settings")
            drive_settings.get_by_label("Selected Drive root folder ID", exact=True).fill("fictional-daily-root")
            drive_settings.get_by_label("Remote folder ID to add", exact=True).fill(REMOTE)
            drive_settings.get_by_label("Existing case for Drive folder", exact=True).click()
            attorney.get_by_label("Find a client", exact=True).fill(cid)
            attorney.get_by_role("listbox", name="Clients", exact=True).get_by_role("option").filter(has_text=cid).click()
            drive_settings.get_by_role("button", name="Add folder and case pair", exact=True).click()
            with attorney.expect_response(lambda r: r.url.endswith("/api/drive-settings") and r.request.method == "POST") as configured:
                drive_settings.get_by_role("button", name="Save Drive settings", exact=True).click()
            assert configured.value.status == 200 and not provider["calls"]
            open_tab(staff, "Documents")
            selected = staff.locator(f'[data-selected-drive="{cid}"]')
            with staff.expect_response(lambda r: r.url.endswith("/api/drive-preview")) as preview:
                selected.get_by_role("button", name="Preview configured Drive selection", exact=True).click()
            assert preview.value.status == 200 and provider["downloads"] == 0
            assert "fictional-unselected-private" not in json.dumps(preview.value.json())
            with staff.expect_response(lambda r: r.url.endswith("/api/drive-enqueue")) as queued:
                selected.get_by_role("button", name="Process the shown selection", exact=True).click()
            assert queued.value.status == 200 and not queued.value.json()["completed"]
            assert work(world) >= 1 and provider["downloads"] == 1
            selected.get_by_role("button", name="Check Drive outcome", exact=True).click()
            expect(selected.locator("[data-drive-status]")).to_contain_text("read", timeout=60000)
            capture(staff, selected, "actual-selected-drive-current-worker-" + str(width))

            # Fourth genuine original creates the city/state reference comparison.
            with staff.expect_response(lambda r: r.url.endswith("/api/client-upload") and r.request.method == "POST") as marriage:
                staff.locator('#dropzone input[type="file"]').set_input_files({"name": "Fictional Marriage.pdf", "mimeType": "application/pdf", "buffer": pdf(MARRIAGE)})
            assert marriage.value.status == 200 and work(world) >= 1
            staff_cookie = next(row["name"] + "=" + row["value"] for row in staff_context.cookies() if row["name"] == "review_session")
            subjects = review_source_subjects(base, staff_cookie, cid)
            assert all(row["current"] for row in subjects["subject_reviews"])
            choose_client(staff, cid)

            # UX02 actual retained I94/NTA and own questionnaire are inspectable.
            items = api(staff, base, "/api/items?client=" + cid)
            arrival = next(row for row in items["cards"] if row["kind"] == "crosscheck" and any(
                fact["key"] == "applicant.last_arrival_city" for fact in row["facts"]))
            open_tab(staff, TABS[arrival["tab"]])
            show = staff.get_by_role("button", name="Show all", exact=True)
            if show.is_visible():
                show.click()
            arrival_card = staff.locator('article[data-review-card="' + arrival["id"] + '"]')
            expect(arrival_card).to_be_visible(timeout=60000)
            nta_preview = arrival_card.locator("figure[data-source-preview]").filter(has_text="Fictional NTA").first
            expect(nta_preview).to_be_visible()
            loaded_source(nta_preview)
            nta_preview.get_by_role("button", name="Enlarge original page 1", exact=True).click()
            expect(staff.locator("#lightbox")).to_be_visible()
            expect(staff.locator("#lightbox-img")).to_have_js_property("complete", True)
            assert staff.locator("#lightbox-img").evaluate("node => node.naturalWidth > 0")
            staff.keyboard.press("Escape")
            expect(staff.locator("#lightbox")).to_be_hidden()
            expect(nta_preview).to_contain_text("whole page")
            originals = arrival_card.get_by_role("link", name="Open original page", exact=False)
            assert originals.count() >= 2
            seen_sources = set()
            for link in originals.all():
                href = link.get_attribute("href")
                parsed = parse_qs(urlparse(href).query)
                assert parsed["expected_sha256"] and href.endswith("#page=1")
                if parsed["doc"][0] not in seen_sources:
                    with staff.expect_popup() as opened:
                        link.click()
                    response = staff.request.get(base + href.split("#", 1)[0])
                    assert response.status == 200 and response.body().startswith(b"%PDF")
                    opened.value.close()
                    seen_sources.add(parsed["doc"][0])
            assert len(seen_sources) >= 2
            capture(staff, arrival_card, "ux02-actual-last-arrival-nta-source-" + str(width))

            # UX03 explicit current field/reference distinction; no inferred choice.
            _, birth_card = current_card(staff, base, cid, "applicant.marriage_cert_birthplace")
            comparison = birth_card.locator(".place-comparison")
            expect(comparison).to_contain_text("Current I-485")
            expect(comparison).to_contain_text("CAMPINAS")
            expect(comparison).to_contain_text("reference context")
            expect(birth_card.get_by_role("button", name="Acknowledge", exact=True)).to_be_visible()
            assert not birth_card.get_by_role("button", name="Save", exact=True).count()
            capture(staff, birth_card, "ux03-city-state-current-field-reference-" + str(width))
            capture(staff, comparison, "ux03-focused-current-field-reference-" + str(width))

            # Actual critical source correction, bound to current source fingerprint.
            row, i94_card = current_card(staff, base, cid, "applicant.i94_number")
            original = i94_card.locator("figure[data-source-preview]").first
            loaded_source(original)
            parsed = parse_qs(urlparse(original.locator("img.scan").get_attribute("src")).query)
            assert parsed["expected_sha256"] and parsed["page"] == ["0"]
            i94_card.locator('[data-fact-key="applicant.i94_number"] input:not([type=radio])').fill("11111111111")
            with staff.expect_response(lambda r: r.url.endswith("/api/decide") and r.request.method == "POST") as checked:
                i94_card.get_by_role("button", name="Confirm", exact=True).click()
            assert checked.value.status == 200
            assert reviewed_graph(case).get("applicant.i94_number").value == "11111111111"

            # UX01 blank/populated multiline continuation, explicit Save only.
            # Confirm also reloads current case state; let its actual feedback settle
            # before making unsaved editor changes, then verify every capture's state.
            expect(staff.locator("#toast")).to_be_hidden(timeout=15000)
            _, part14 = current_card(staff, base, cid, "applicant.p14_block1_text")
            populated = part14.get_by_role("textbox", name="Part 14 entry 1 continuation text", exact=True)
            expect(populated).not_to_have_value("")
            assert populated.evaluate("node => node.tagName") == "TEXTAREA"
            assert populated.evaluate("node => parseFloat(getComputedStyle(node).minHeight)") >= 100
            assert populated.evaluate("node => getComputedStyle(node).resize") == "vertical"
            populated.fill("")
            expect(populated).to_have_value("")
            populated.focus(); expect(populated).to_be_focused()
            assert populated.evaluate("node => node.rows") >= 4
            capture(staff, part14, "ux01-blank-part14-keyboard-editor-" + str(width))
            expect(populated).to_have_value("")
            capture(staff, populated.locator("xpath=.."), "ux01-focused-blank-editor-action-area-" + str(width))
            expect(populated).to_have_value("")
            text = "Fictional work history checked by the office.\nSecond retained line of this continuation."
            populated.fill(text)
            capture(staff, part14, "ux01-populated-part14-multiline-save-" + str(width))
            capture(staff, populated.locator("xpath=.."), "ux01-focused-multiline-editor-action-area-" + str(width))
            expect(populated).to_have_value(text)
            with staff.expect_response(lambda r: r.url.endswith("/api/decide") and r.request.method == "POST") as saved:
                part14.get_by_role("button", name=re.compile(r"^Save(?: changes)?$")).click()
            assert saved.value.status == 200 and "\n" in reviewed_graph(case).get("applicant.p14_block1_text").value
            assert not (case / "part14_explanations.json").exists()
            saved_decisions = json.loads((case / "decisions.json").read_text())
            assert any(row.get("role") == "paralegal" and "applicant.p14_block1_text" in row.get("values", {}) for row in saved_decisions.values())
            # UX05 reading order remains intact while packet is still held.
            open_tab(staff, "Filing packet")
            excluded = staff.locator("#packet-excluded")
            expect(excluded).to_be_visible(timeout=60000)
            assert staff.locator("#packet-included").evaluate("node => !!(node.compareDocumentPosition(document.getElementById('packet-excluded')) & Node.DOCUMENT_POSITION_FOLLOWING)")
            assert excluded.evaluate("node => !!(node.compareDocumentPosition(document.getElementById('packet-before-filing')) & Node.DOCUMENT_POSITION_FOLLOWING)")
            assert not api(staff, base, "/api/packet?client=" + cid + "&filing=i485")["ready"]
            capture(staff, excluded, "ux05-packet-exclusions-before-mailing-hold-" + str(width))

            # UX06 actual unsaved preview with required/optional distinction.
            open_tab(staff, "Agreement and closing")
            expect(staff.locator("#agreement-purpose")).to_contain_text("Creating a draft does not send it or sign it", timeout=60000)
            fee = staff.get_by_role("textbox", name="Professional service fee and payment terms (required)", exact=True)
            expect(fee).to_have_value("")
            expect(staff.get_by_role("textbox", name="Government fees (optional)", exact=True)).to_have_value("")
            assert staff.locator(".filing-picks input:checked").count() == 0
            staff.locator('.filing-picks input[value="i485"]').check()
            fee.fill("Fictional professional services: 100 test units, payable after review.")
            staff.get_by_role("textbox", name="Additions (optional)", exact=True).fill("Fictional unsaved preview only.")
            before = len(world["sent"])
            with staff.expect_response(lambda r: r.url.endswith("/api/engagement-preview")) as agreement:
                staff.locator("#preview-agreement").click()
            assert agreement.value.status == 200
            preview_panel = staff.locator("#agreement-preview-panel")
            expect(preview_panel).to_contain_text("Nothing has been saved, sent or signed")
            assert not (case / "engagement.json").exists() and len(world["sent"]) == before
            capture(staff, preview_panel, "ux06-actual-unsaved-agreement-preview-" + str(width))

            # UX07 keyboard order and a named note saved on this same case.
            nav = staff.get_by_role("navigation", name="Review queues")
            assert nav.locator("button .nm").all_text_contents()[:2] == ["Where the case stands", "Notes and tasks"]
            nav.get_by_role("button", name="Where the case stands").focus()
            staff.keyboard.press("Tab")
            expect(nav.get_by_role("button", name="Notes and tasks")).to_be_focused()
            staff.keyboard.press("Enter")
            expect(staff.locator("#notes-panel")).to_be_visible(timeout=60000)
            staff.locator("#note-text").fill("Fictional composed daily-work handoff; source and legal review remain distinct.")
            staff.locator("#note-add").click()
            expect(staff.locator("#notes-panel")).to_contain_text("Fictional composed daily-work handoff")
            capture(staff, staff.locator("#notes-panel"), "ux07-notes-keyboard-and-named-handoff-" + str(width))

            # Current attorney alone acknowledges a real legal alert; Save did not.
            attorney.locator("#all").click(); choose_client(attorney, cid)
            open_tab(attorney, "Attorney sign-off")
            alerts = api(attorney, base, "/api/items?client=" + cid)["cards"]
            alert = next(row for row in alerts if row["tab"] == "attorney" and "acknowledge" in row["actions"])
            legal_card = attorney.locator('article[data-review-card="' + alert["id"] + '"]')
            legal_card.locator("input.note").fill("FICTIONAL attorney reviewed this alert and retained the remaining filing holds.")
            with attorney.expect_response(lambda r: r.url.endswith("/api/decide") and r.request.method == "POST") as legal:
                legal_card.get_by_role("button", name="Acknowledge", exact=True).click()
            assert legal.value.status == 200
            decisions = json.loads((case / "decisions.json").read_text())
            assert any(row.get("role") == "attorney" and row.get("action") == "acknowledge" for row in decisions.values())
            assert not api(attorney, base, "/api/packet?client=" + cid + "&filing=i485")["ready"]

            # UX04 current revocation hides Ask and keeps the nearby office route.
            choose_client(staff, cid)
            staff.locator("details.group > summary").filter(has_text=re.compile(r"^Communication choices and language$")).click()
            current_comm = staff.locator(f'[data-communication="{cid}"]')
            with staff.expect_response(lambda r: r.url.endswith("/api/communication") and r.request.method == "POST") as revoked:
                current_comm.get_by_role("button", name="Revoke communication permission", exact=True).click()
            assert revoked.value.status == 200
            expect(current_comm).to_contain_text("Office/manual follow-up", timeout=60000)
            assert own.request.get(origin + "/api/me").status == 401
            after_revoke = api(staff, base, "/api/case-list?scope=all&ended=all&q=" + cid)
            search_trace.append({"phase": "after_actual_revocation", "result": after_revoke,
                                 "pending": {key: sorted(getattr(cloud_app.roster, key)) for key in ("dirty", "later", "inflight")}})
            choose_client(staff, cid); open_tab(staff, "Needs attention")
            expect(staff.get_by_role("button", name="Ask the client", exact=True)).to_have_count(0)
            office_card = staff.locator("article[data-review-card]").filter(has=staff.locator(".office-contact")).first
            expect(office_card.locator(".office-contact")).to_be_visible(timeout=60000)
            capture(staff, office_card, "ux04-current-revocation-office-alternative-" + str(width))
            capture(staff, office_card.locator(".actions"), "ux04-focused-office-action-alternative-" + str(width))

            # UX08 actual second intake, explicit reciprocal relationship, no grants.
            peer = add_client(staff, "Delta Example Fictional", "delta.daily@example.test", "+15550100243")
            staff.locator(f'#client-rows tr[data-client="{peer}"] summary').click()
            staff.locator(f'#client-rows tr[data-client="{peer}"]').get_by_role("button", name="Source setup / recovery", exact=True).click()
            setup = staff.locator(f'[data-source-setup="{peer}"]')
            setup.get_by_role("button", name="Set up or recover sources", exact=True).click()
            staff.locator("#all").click(); choose_client(staff, cid); open_tab(staff, "Where the case stands")
            family = staff.locator(f'[data-family-case="{cid}"]')
            family.get_by_label("Find a family member's case", exact=True).fill(peer)
            candidate = family.get_by_role("listbox", name="Accessible family cases").get_by_role("option")
            expect(candidate).to_have_count(1, timeout=60000)
            candidate.get_by_role("button").click()
            expect(family.get_by_label("Family relationship", exact=True)).to_have_value("")
            expect(family.get_by_role("button", name="Confirm and link these cases", exact=True)).to_be_disabled()
            family.get_by_label("Family relationship", exact=True).select_option("Spouse")
            family.get_by_label("Confirm these two cases and the family relationship", exact=True).check()
            with staff.expect_response(lambda r: r.url.endswith("/api/journey") and r.request.method == "POST") as linked:
                family.get_by_role("button", name="Confirm and link these cases", exact=True).click()
            assert linked.value.status == 200
            expect(staff.locator("#family-members")).to_contain_text(peer)
            assert not store.profile(peer)["consent"]["email"]
            capture(staff, staff.locator("#family-members"), "ux08-explicit-family-pair-same-case-" + str(width))

            # Assignment claim/transfer/history composes with the same case.
            staff.locator("#my-cases").click()
            staff.locator('[data-case-scope="unassigned"]').click()
            assignment_row = staff.locator(f'tr[data-assignment-row="{cid}"]')
            assignment_row.get_by_role("button", name="Manage assignment", exact=True).click()
            responsibility = staff.locator(f'[data-assignment-case="{cid}"]')
            responsibility.get_by_role("button", name="Claim this case", exact=True).click()
            expect(staff.locator("#assignment-status")).to_contain_text("revision 1", timeout=60000)
            responsibility.get_by_label("New responsible staff member", exact=True).select_option(label="Fictional Staff · paralegal")
            responsibility.get_by_label("Assignment reason (optional)", exact=True).fill("Fictional reviewed daily-work transfer")
            responsibility.get_by_role("button", name="Transfer case", exact=True).click()
            expect(staff.locator("#assignment-status")).to_contain_text("revision 2", timeout=60000)
            expect(responsibility).to_contain_text("Fictional Transfer Staff")
            expect(responsibility).to_contain_text("Fictional Staff")
            expect(responsibility).to_contain_text("Recorded")
            expect(responsibility.locator("[data-assignment-identity]")).to_have_text("Case ID: " + cid)
            capture(staff, responsibility, "assignment-claim-transfer-named-history-" + str(width))
            retained = [{"file": str(path.relative_to(scope.documents)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                        for path in sorted((scope.documents / cid / "source").glob("*.pdf"))]
            assert len(retained) == 4
            (SHOTS / ("current-originals-" + str(width) + ".json")).write_text(json.dumps(retained, indent=2))
            assert not attorney_errors and not staff_errors
    except BaseException:
        SHOTS.mkdir(parents=True, exist_ok=True)
        (SHOTS / ("page-errors-" + str(width) + ".json")).write_text(json.dumps({"attorney": attorney_errors, "staff": staff_errors if staff_context else []}, indent=2))
        for label, page in (("attorney", attorney), ("staff", staff if staff_context else None)):
            if page is not None and not page.is_closed():
                page.screenshot(path=str(SHOTS / ("failure-" + label + "-" + str(width) + ".png")))
        raise
    finally:
        SHOTS.mkdir(parents=True, exist_ok=True)
        (SHOTS / ("protected-search-trace-" + str(width) + ".json")).write_text(json.dumps(search_trace, indent=2))
        (SHOTS / ("actual-source-image-responses-" + str(width) + ".json")).write_text(json.dumps(source_trace, indent=2))
        if client_context:
            client_context.close()
        if staff_context:
            staff_context.close()
        attorney_context.close()
