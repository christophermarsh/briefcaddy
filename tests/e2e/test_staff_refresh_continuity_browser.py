"""Staff saves and worker updates preserve the work in progress on a case.

Only fictional, authenticated temporary cases are used. Job/upload responses
are controlled so receipt and completion updates can be tested independently
from OCR, without writing to any installed client's folder.
"""
# ruff: noqa: F811 -- imported canonical fixtures use pytest injection
import copy
import json

from playwright.sync_api import expect
from test_assignment_routes import (  # noqa: F401 -- fixtures
    app,
    call,
    controls,
    server,
    sign_in,
    world,
)
from test_client_picker_refresh_browser import login, search


def open_case(page, case="case-ana"):
    search(page, case)
    page.locator("#client-pick .pick-item").filter(has_text=case).click()
    expect(page.locator("#case h1")).not_to_have_text(case, timeout=60000)
    expect(page.locator("[data-review-card]").first).to_be_visible(timeout=60000)


def fact_card(page, key):
    return page.locator("[data-review-card]").filter(
        has=page.locator(f'[data-fact-key="{key}"]')
    )


def field(card, key):
    return card.locator(f'[data-fact-key="{key}"] input:not([type=radio])')


def remember_position(page, control):
    control.focus()
    control.evaluate("node => node.setSelectionRange(2, 5)")
    page.evaluate("window.scrollTo(0, Math.min(450, document.documentElement.scrollHeight - innerHeight))")
    return page.evaluate("window.scrollY")


def expect_position(page, control, scroll):
    expect(control).to_be_focused()
    assert control.evaluate("node => [node.selectionStart, node.selectionEnd]") == [2, 5]
    page.wait_for_function("before => Math.abs(window.scrollY - before) < 3", arg=scroll)


def test_saving_one_answer_preserves_another_cards_draft_note_source_and_position(browser, server, world):
    context, page = login(browser, server)
    held = []
    try:
        open_case(page)
        saved = fact_card(page, "applicant.physical_city")
        draft = fact_card(page, "applicant.birth_city")
        field(saved, "applicant.physical_city").fill("Fictional City")
        field(draft, "applicant.birth_city").fill("Unsaved birthplace")
        draft.locator(".note").fill("Check the original with the client")
        detail = draft.locator("details.official")
        detail.locator("summary").click()
        expect(detail).to_have_attribute("open", "")
        original = draft.element_handle()

        # Hold the actual decision response while the paralegal continues
        # editing a different card. The server still performs a real save.
        page.route("**/api/decide", lambda route: held.append(route))
        with page.expect_request("**/api/decide"):
            saved.get_by_role("button", name="Save", exact=True).click()
        assert len(held) == 1
        control = field(draft, "applicant.birth_city")
        scroll = remember_position(page, control)
        with page.expect_response("**/api/items?**"):
            response = held[0].fetch()
            assert response.status == 200
            held[0].fulfill(response=response)
        expect(saved).to_have_count(0)
        expect(control).to_have_value("Unsaved birthplace")
        expect(draft.locator(".note")).to_have_value("Check the original with the client")
        expect(detail).to_have_attribute("open", "")
        assert draft.evaluate("(node, old) => node === old", original)
        expect_position(page, control, scroll)
        decisions = json.loads((world / "case-ana" / "decisions.json").read_text())
        assert decisions["missing:applicant.physical_city"]["values"]["applicant.physical_city"] == "FICTIONAL CITY"
        assert "missing:applicant.birth_city" not in decisions

        # Background DOM updates must respect an intentional focus change.
        page.locator("#settings").focus()
        page.evaluate("loadClient('case-ana')")
        expect(page.locator("#settings")).to_be_focused()
        expect(control).to_have_value("Unsaved birthplace")
    finally:
        context.close()


def test_successful_case_reply_held_across_manual_signout_cannot_restore_case_or_drafts(browser, server):
    context, page = login(browser, server)
    held = []
    try:
        open_case(page)
        draft = fact_card(page, "applicant.birth_city")
        field(draft, "applicant.birth_city").fill("Abandoned private case draft")
        draft.locator(".note").fill("Unsaved private office note")
        draft.locator("details.official summary").click()
        page.route("**/api/items?**", lambda route: held.append(route))
        with page.expect_request("**/api/items?**"):
            page.evaluate("void loadClient('case-ana')")
        # A browser interaction dispatches the intercepted route callback;
        # fetch the real successful response while the actor is still signed in.
        draft.locator(".note").focus()
        assert len(held) == 1
        route = held.pop()
        successful = route.fetch()
        assert successful.status == 200

        page.get_by_role("button", name="Sign out", exact=True).focus()
        page.keyboard.press("Enter")
        email = page.locator("input[name=email]")
        password = page.locator("input[name=password]")
        expect(email).to_be_visible()
        email.fill("new-session@firm.example")
        password.fill("Synthetic unsent login text")
        with page.expect_response("**/api/items?**"):
            route.fulfill(response=successful)

        expect(page.get_by_role("heading", name="Sign in", exact=True)).to_be_visible()
        expect(page.locator("#case h1")).to_have_text("Case Review")
        expect(page.locator("#side")).to_have_text("")
        expect(page.locator("[data-review-card]")).to_have_count(0)
        expect(email).to_have_value("new-session@firm.example")
        expect(password).to_have_value("Synthetic unsent login text")
        assert page.evaluate("({client: S.client, data: S.data, actor: workActor(), context: WORK.context, drafts: WORK.drafts.size})") == {
            "client": None, "data": None, "actor": None, "context": None, "drafts": 0,
        }
    finally:
        for route in held:
            route.abort()
        context.close()


def test_changed_evidence_requires_explicit_review_and_keeps_drafts_by_fact_key(browser, server):
    context, page = login(browser, server)
    posts = []
    try:
        model = page.request.get(server + "/api/items?client=case-ana").json()
        target = next(c for c in model["cards"] if c["id"] == "missing:applicant.birth_city")
        target["evidence_fingerprints"] = {"fictional-source.pdf": "first-reading"}
        target["facts"][0]["value"] = "First source city"
        state = {"model": model}
        page.route("**/api/items?**", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(state["model"])
        ))
        page.route("**/api/decide", lambda route: (
            posts.append(route.request.post_data_json),
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"saved": 1}))
        ))
        open_case(page)
        card = fact_card(page, "applicant.birth_city")
        control = field(card, "applicant.birth_city")
        control.fill("Draft city correction")
        card.locator(".note").fill("Still checking this source")
        card.locator("details.official summary").click()
        scroll = remember_position(page, control)

        updated = copy.deepcopy(model)
        next_card = next(c for c in updated["cards"] if c["id"] == target["id"])
        next_card["evidence_fingerprints"] = {"fictional-source.pdf": "second-reading"}
        next_card["facts"][0]["value"] = "Revised source city"
        # A new fact preceding the edited fact must not inherit its draft.
        extra = copy.deepcopy(next_card["facts"][0])
        extra.update(key="applicant.birth_country", label="Country of birth", short="Country of birth", value="BRAZIL")
        next_card["facts"].insert(0, extra)
        state["model"] = updated
        page.evaluate("loadClient('case-ana')")
        expect(card.get_by_role("button", name="Review updated information", exact=True)).to_be_visible()
        expect(card.get_by_role("button", name="Save", exact=True)).to_be_disabled()
        expect(control).to_have_value("Draft city correction")
        expect_position(page, control, scroll)
        assert not posts

        card.get_by_role("button", name="Review updated information", exact=True).click()
        expect(field(card, "applicant.birth_city")).to_have_value("Draft city correction")
        expect(field(card, "applicant.birth_country")).to_have_value("BRAZIL")
        expect(card.locator(".note")).to_have_value("Still checking this source")
        expect(card.locator("details.official")).to_have_attribute("open", "")
        with page.expect_request("**/api/decide"):
            card.get_by_role("button", name="Save", exact=True).click()
        assert len(posts) == 1
        decision = posts[0]["decisions"][0]
        assert decision["evidence_fingerprints"] == {"fictional-source.pdf": "second-reading"}
        assert decision["values"]["applicant.birth_city"] == "Draft city correction"
        assert decision["values"]["applicant.birth_country"] == "BRAZIL"
    finally:
        context.close()


def test_saved_note_and_task_clear_only_the_submitted_draft(browser, server):
    context, page = login(browser, server)
    held = []
    try:
        open_case(page)
        page.locator("#side").get_by_role("button", name="Notes and tasks", exact=False).click()
        note = page.get_by_label("Write a note", exact=True)
        expect(note).to_be_visible()
        note.fill("Fictional saved office note")
        task = page.get_by_label("Task", exact=True)
        task.fill("Unsaved follow-up task")
        page.get_by_label("Due date", exact=True).fill("2026-11-15")
        page.get_by_label("Note on the task", exact=True).fill("Unrelated task draft")
        page.route("**/api/case-notes", lambda route: held.append(route))
        with page.expect_request("**/api/case-notes"):
            page.get_by_role("button", name="Add the note", exact=True).click()
        scroll = remember_position(page, task)
        route = held.pop()
        response = route.fetch()
        assert response.status == 200
        route.fulfill(response=response)
        expect(page.locator("#note-list")).to_contain_text("Fictional saved office note")
        expect(note).to_have_value("")
        expect(task).to_have_value("Unsaved follow-up task")
        expect(page.get_by_label("Due date", exact=True)).to_have_value("2026-11-15")
        expect(page.get_by_label("Note on the task", exact=True)).to_have_value("Unrelated task draft")
        expect_position(page, task, scroll)

        note.fill("New unsaved note")
        with page.expect_request("**/api/case-notes"):
            page.get_by_role("button", name="Add the task", exact=True).click()
        scroll = remember_position(page, note)
        route = held.pop()
        response = route.fetch()
        assert response.status == 200
        route.fulfill(response=response)
        expect(page.locator("#task-list")).to_contain_text("Unsaved follow-up task")
        expect(task).to_have_value("")
        expect(page.get_by_label("Due date", exact=True)).to_have_value("")
        expect(page.get_by_label("Note on the task", exact=True)).to_have_value("")
        expect(note).to_have_value("New unsaved note")
        expect_position(page, note, scroll)
    finally:
        context.close()


def test_updated_source_keeps_the_latest_radio_choice_and_clears_replaced_custom_text(browser, server):
    context, page = login(browser, server)
    posts = []
    try:
        model = page.request.get(server + "/api/items?client=case-ana").json()
        target = next(c for c in model["cards"] if c["id"] == "missing:applicant.birth_city")
        target["facts"][0]["input"]["alternatives"] = ["First City", "Second City"]
        target["evidence_fingerprints"] = {"fictional-source.pdf": "reading-1"}
        page.route("**/api/items?**", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(model)
        ))
        page.route("**/api/decide", lambda route: (
            posts.append(route.request.post_data_json),
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"saved": 1}))
        ))
        open_case(page)
        card = fact_card(page, "applicant.birth_city")
        first = card.get_by_role("radio", name="First City", exact=True)
        second = card.get_by_role("radio", name="Second City", exact=True)
        custom = card.get_by_label("Corrected city of birth", exact=True)

        for revision in (2, 3):
            if revision == 3:
                custom.fill("Earlier custom correction")
                expect(first).not_to_be_checked()
            second.check()
            first.check()
            expect(custom).to_have_value("")
            target["evidence_fingerprints"] = {"fictional-source.pdf": f"reading-{revision}"}
            page.evaluate("loadClient('case-ana')")
            review = card.get_by_role("button", name="Review updated information", exact=True)
            expect(review).to_be_visible()
            review.click()
            expect(first).to_be_checked()
            expect(second).not_to_be_checked()
            expect(custom).to_have_value("")

        with page.expect_request("**/api/decide"):
            card.get_by_role("button", name="Save", exact=True).click()
        assert len(posts) == 1
        assert posts[0]["decisions"][0]["values"]["applicant.birth_city"] == "First City"
        assert posts[0]["decisions"][0]["evidence_fingerprints"] == {"fictional-source.pdf": "reading-3"}
    finally:
        context.close()


def test_private_note_correction_survives_an_unrelated_task_save(browser, server):
    context = browser.new_context()
    cookie = sign_in(server, "sam@firm.example")
    name, value = cookie.split("=", 1)
    context.add_cookies([{"name": name, "value": value, "url": server}])
    assert call(server + "/api/getting-started", cookie, {})[0] == 200
    assert call(server + "/api/case-notes", cookie, {
        "client": "case-ana", "action": "note", "text": "Fictional original attorney note", "reviewer": "Sam Attorney",
    })[0] == 200
    page = context.new_page()
    try:
        page.goto(server)
        expect(page.get_by_role("heading", name="My cases", exact=True)).to_be_visible(timeout=60000)
        open_case(page)
        page.locator("#side").get_by_role("button", name="Notes and tasks", exact=False).click()
        original = page.locator(".note-row").filter(has_text="Fictional original attorney note")
        expect(original).to_be_visible()
        correction_id = original.get_attribute("data-note")
        original.get_by_role("button", name="Correct this note", exact=True).click()
        note = page.get_by_label("Write a note", exact=True)
        note.fill("Unsaved private correction")
        private = page.locator("#note-attorney-only")
        private.check()
        page.get_by_label("Task", exact=True).fill("Fictional unrelated task")
        page.get_by_label("Due date", exact=True).fill("2026-11-15")
        with page.expect_response("**/api/case-notes") as saved:
            page.get_by_role("button", name="Add the task", exact=True).click()
        assert saved.value.status == 200
        expect(page.locator("#task-list")).to_contain_text("Fictional unrelated task")
        expect(note).to_have_value("Unsaved private correction")
        expect(private).to_be_checked()
        expect(page.locator("#note-fixing")).to_be_visible()

        with page.expect_request("**/api/case-notes") as submitted:
            page.get_by_role("button", name="Add the note", exact=True).click()
        body = submitted.value.post_data_json
        assert body["corrects"] == correction_id
        assert body["attorney_only"] is True
        expect(page.locator("#note-list")).to_contain_text("Unsaved private correction")
        stored = page.request.get(server + "/api/case-notes?client=case-ana").json()
        correction = next(row for row in stored["notes"] if row["text"] == "Unsaved private correction")
        assert correction["corrects"] == correction_id
        assert correction["attorney_only"] is True
    finally:
        context.close()


def test_upload_receipt_and_finished_reading_preserve_documents_without_duplicates(browser, server):
    context, page = login(browser, server)
    held = []
    state = {"job": "running"}
    try:
        docs = page.request.get(server + "/api/documents?client=case-ana").json()
        page.route("**/api/documents?**", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(docs)
        ))
        page.route("**/api/document-text?**", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({
                "text": "Synthetic passport text", "reading_note": "Fictional source reading",
                "source_locations": [], "record_built": "2026-10-05T12:00:00+00:00",
            })
        ))
        page.route("**/api/jobs?**", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"jobs": [{
                "id": "fictional-reading", "what": "Fictional scan",
                "state": state["job"], "label": "Read" if state["job"] == "done" else "Reading",
            }]})
        ))
        page.route("**/api/client-upload", lambda route: held.append(route))
        open_case(page)
        page.locator("#side").get_by_role("button", name="Documents", exact=False).click()
        expect(page.locator("#doc-a1")).to_be_visible()
        expect(page.locator("#readings")).to_contain_text("Reading")
        row = page.locator("#doc-a1")
        row.get_by_text("Read extracted text", exact=True).click()
        expect(row.locator("[data-original-text]")).to_contain_text("Synthetic passport text")
        row.get_by_text("Change the language", exact=True).click()
        language = row.get_by_label("Language of the passport", exact=True)
        language.select_option("pt")
        original = row.element_handle()

        with page.expect_request("**/api/client-upload"):
            page.locator("#dropzone input[type=file]").set_input_files({
                "name": "fictional-new.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 fictional",
            })
        # Another editable field stays focused while upload receipt arrives.
        page.locator("#papers input[type=text]").first.wait_for(state="attached")
        note = page.get_by_label("A note about the Birth certificate", exact=False)
        if not note.count():
            note = page.locator("#papers input[type=text]").first
        # Open the paper's optional note before editing it.
        note.evaluate("node => { for (let p=node.parentElement; p; p=p.parentElement) if (p.tagName === 'DETAILS') p.open=true; }")
        note.fill("Unsaved document follow-up")
        scroll = remember_position(page, note)
        assert len(held) == 1
        incoming = copy.deepcopy(docs["documents"][0])
        incoming.update(id="fictional-new", files=["fictional-new.pdf"], doc_ids=["fictional-new.pdf"], source_locations=[])
        docs["documents"].append(incoming)
        receipt = {"received": True, "processed": False, "status": "queued", "reading": True, "worker_available": True}
        with page.expect_response("**/api/items?**"):
            held[0].fulfill(status=200, content_type="application/json", body=json.dumps(receipt))
        expect(page.locator("[data-upload-status]")).to_contain_text("Received; reading is queued")
        expect(page.locator("#doc-fictional-new")).to_have_count(1)
        expect(language).to_have_value("pt")
        expect(note).to_have_value("Unsaved document follow-up")
        assert row.evaluate("(node, old) => node === old", original)
        expect_position(page, note, scroll)

        state["job"] = "done"
        with page.expect_response("**/api/items?**", timeout=10000):
            expect(page.locator("#readings")).to_contain_text("Read", timeout=10000)
        expect(language).to_have_value("pt")
        expect(note).to_have_value("Unsaved document follow-up")
        expect_position(page, note, scroll)
        expect(row.locator("[data-original-text]")).to_contain_text("Synthetic passport text")
        for _ in range(3):
            with page.expect_response("**/api/documents?**"):
                page.evaluate("loadClient('case-ana')")
            expect(page.locator("#dropzone")).to_have_count(1)
            expect(page.locator("#readings")).to_have_count(1)
            expect(page.locator("#papers")).to_have_count(1)
            expect(page.locator("#doc-a1")).to_have_count(1)
            expect(page.locator("#doc-fictional-new")).to_have_count(1)
            expect(page.locator("[data-staff-upload-recovery]")).to_have_count(1)
        expect(note).to_have_value("Unsaved document follow-up")
    finally:
        context.close()
