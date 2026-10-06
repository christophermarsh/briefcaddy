"""Actual staff Chromium recovery over fictional prepared roots/PDFs only."""
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

import jobs
from portal.demo import document_pdf
from upload_workflow_fixtures import source_firm, world, app, server, PASSWORD  # noqa: F401 -- pytest fixture registration and helper reexports


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


def prepared_case(page, server, world):  # noqa: F811 -- pytest fixture injection
    response = page.request.post(server + "/api/source-setup", data={"client": world["client"]}, headers={"X-Review-App": "1"})
    assert response.status == 200, response.text()
    open_documents(page, server, world)


def open_documents(page, server, world):  # noqa: F811 -- pytest fixture injection
    page.goto(server + "?tab=documents#" + world["client"])
    expect(page.locator("#client")).to_have_value(world["client"], timeout=60000)
    expect(page.locator("#dropzone")).to_be_visible(timeout=60000)


def selected(page, world, name="Fictional I94.pdf", data=None):  # noqa: F811 -- pytest fixture injection
    page.locator('#dropzone input[type="file"]').set_input_files({"name": name, "mimeType": "application/pdf", "buffer":
        document_pdf(world["pages"][0].splitlines()) if data is None else data})


def one_attempt(page):
    panel = page.locator("[data-staff-upload-attempt]")
    expect(panel).to_have_count(1, timeout=60000)
    return panel


def capture(page, name):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if os.environ.get("E2E_SHOTS"):
        root = Path(os.environ["E2E_SHOTS"]); root.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(root / (name + ".png")), full_page=True)


@pytest.mark.parametrize("width", [1000, 1400], ids=["1000px", "1400px"])
def test_lost_response_same_file_retry_actual_worker_and_review_holds(browser, server, world, width, monkeypatch):  # noqa: F811 -- pytest fixture injection
    context, page, errors = login(browser, server, width)
    try:
        prepared_case(page, server, world)
        committed = []
        def lose_first_response(route):
            if committed:
                route.continue_()
            else:
                actual = route.fetch()
                assert actual.status == 200, actual.text()
                committed.append(actual.json())
                route.abort("failed")  # server already committed; browser gets no response
        page.route("**/api/client-upload", lose_first_response)
        selected(page, world)
        panel = one_attempt(page)
        expect(panel.locator("[data-upload-status]")).to_contain_text("Outcome unknown", timeout=60000)
        assert committed[0]["received"] and not committed[0]["processed"]
        attempt = panel.get_attribute("data-staff-upload-attempt")
        expect(page.locator("[data-staff-upload-recovery]")).to_contain_text("refreshing or signing out loses")
        panel.get_by_role("button", name="Check outcome", exact=True).click()
        expect(panel.locator("[data-upload-status]")).to_contain_text("reading is queued", timeout=60000)
        with page.expect_response(lambda r: r.url.endswith("/api/client-upload") and r.request.method == "POST", timeout=60000) as retried:
            panel.get_by_role("button", name="Retry this same file", exact=True).click()
        assert retried.value.status == 200
        assert retried.value.request.post_data_json["attempt"] == attempt
        assert retried.value.json()["job"]["id"] == committed[0]["job"]["id"]
        panel = one_attempt(page)
        expect(page.locator("[data-staff-upload-recovery]")).to_have_count(1)
        expect(panel.locator("[data-upload-status]")).to_contain_text("could not be started here", timeout=60000)
        capture(page, "staff-upload-received-worker-unavailable-" + str(width))
        scope, client = world["scope"], world["client"]
        assert len(list((scope.documents / client / "source").glob("*.pdf"))) == 1
        assert len(list(scope.queue.glob("*-staff_upload-*.json"))) == 1
        from review import front_desk
        original = front_desk.read_staff_upload
        calls = []
        def read(*args, **kwargs):
            calls.append(args[2]); return original(*args, **kwargs)
        monkeypatch.setattr(front_desk, "read_staff_upload", read)
        done = jobs.run_job(jobs.Context(scope.cases, scope.portal, jobs_root=scope.queue), jobs.get(scope.queue, committed[0]["job"]["id"]))
        assert done["state"] == "done" and done["result"]["processed"], done
        panel.get_by_role("button", name="Check outcome", exact=True).click()
        expect(panel.locator("[data-upload-status]")).to_contain_text("Read. Document boundaries", timeout=60000)
        expect(page.locator("[data-staff-upload-recovery]")).to_have_count(1)
        expect(page.locator("#documents")).to_be_visible(timeout=60000)
        expect(page.locator("#subject-reviews")).to_be_visible(timeout=60000)
        expect(page.get_by_text("No documents yet", exact=True)).to_have_count(0)
        assert page.locator("#documents").get_by_role("row").count() >= 2
        expect(panel.get_by_role("button", name="Retry this same file", exact=True)).to_be_disabled()
        assert calls == [client]
        import subject_attribution as subjects, critical_review
        from factgraph import FactGraph
        assert subjects.affected_keys(scope.cases / client)
        assert critical_review.flags(FactGraph.load(scope.cases / client / "fact_graph_raw.json"))
        assert not (scope.cases / client / "decisions.json").exists()
        capture(page, "staff-upload-read-review-required-" + str(width))
        assert not errors
    finally:
        context.close()


def hold_successful_upload_response(page):
    # Real native fetch commits at the server. Only response delivery to the UI
    # is delayed, so Cancel exercises a real committed-but-unknown boundary.
    page.evaluate("""() => {
      const native = window.fetch;
      window.staffUploadCommitted = false;
      window.fetch = async function(url, options) {
        const response = await native.call(this, url, options);
        if (String(url).includes('/api/client-upload') && !window.staffUploadCommitted) {
          window.staffUploadCommitted = true;
          return await new Promise((resolve, reject) => {
            window.releaseStaffUploadResponse = () => resolve(response);
            options.signal.addEventListener('abort', () => reject(new DOMException('Stopped waiting', 'AbortError')), {once:true});
          });
        }
        return response;
      };
    }""")


def test_stop_waiting_then_observe_and_local_discard_keeps_server_file(browser, server, world):  # noqa: F811 -- pytest fixture injection
    context, page, errors = login(browser, server)
    try:
        prepared_case(page, server, world)
        hold_successful_upload_response(page)
        selected(page, world)
        page.wait_for_function("window.staffUploadCommitted === true")
        panel = one_attempt(page)
        expect(panel.get_by_role("button", name="Stop waiting", exact=True)).to_be_enabled()
        panel.get_by_role("button", name="Stop waiting", exact=True).click()
        expect(panel).to_contain_text("Stopped waiting. The server may already have received")
        expect(panel.locator("[data-upload-status]")).to_contain_text("Outcome unknown")
        panel.get_by_role("button", name="Check outcome", exact=True).click()
        expect(panel.locator("[data-upload-status]")).to_contain_text("Received; reading is queued", timeout=60000)
        scope, client = world["scope"], world["client"]
        originals = list((scope.documents / client / "source").glob("*.pdf"))
        assert len(originals) == 1
        dialogs = []
        page.once("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
        panel.get_by_role("button", name="Remove from this page", exact=True).click()
        expect(page.locator("[data-staff-upload-attempt]")).to_have_count(0)
        assert dialogs == ["Remove this file from this page? The server may already have received it; this does not delete it there."]
        assert originals[0].is_file() and len(list(scope.queue.glob("*-staff_upload-*.json"))) == 1
        assert not errors
    finally:
        context.close()


def test_source_setup_has_no_fake_file_and_real_original_is_readonly_until_recovered(browser, server, world, app):  # noqa: F811 -- pytest fixture injection
    store, scope, client = world["store"], world["scope"], world["client"]
    row = store.add_upload(client, "i94", "Retained fictional I94.pdf", document_pdf(world["pages"][0].splitlines()), "application/pdf")
    import source_association
    # Establish a real pending association after the durable initial record,
    # not an unassociated legacy pipeline case or a fabricated hold DTO.
    with pytest.MonkeyPatch.context() as fault:
        def interrupted(*args, **kwargs):
            raise OSError("synthetic interruption after association record before copy")
        fault.setattr(source_association, "_publish", interrupted)
        with pytest.raises(OSError, match="after association record"):
            source_association.associate(scope.root, store, client, actor_email="jane@firm.example",
                                         actor_reader=app._upload_actor_reader, use_policies=False)
    assert source_association.hold(scope.cases / client)
    context, page, errors = login(browser, server, 1400)
    try:
        open_documents(page, server, world)
        setup = page.locator("[data-source-setup]")
        expect(setup).to_have_count(1)
        expect(setup).to_contain_text("case-wide setup step")
        assert setup.locator("a").count() == 0
        assert setup.get_by_role("button", name="Confirm these boundaries", exact=True).count() == 0
        physical = page.locator("section.group").filter(has=page.get_by_role("heading", name="Retained original awaiting source setup", exact=True))
        expect(physical).to_be_visible()
        assert physical.locator('a[href*="/api/file"]').count() >= 1
        assert "doc=" + row["stored"] in physical.locator('a[href*="/api/file"]').first.get_attribute("href")
        assert physical.get_by_role("button", name="Confirm these boundaries", exact=True).count() == 0
        expect(physical).to_contain_text("boundary confirmation is unavailable")
        capture(page, "source-setup-pending-original-readonly-1400")
        with page.expect_response(lambda r: r.url.endswith("/api/source-setup") and r.request.method == "POST", timeout=120000) as response:
            setup.get_by_role("button", name="Set up or recover sources", exact=True).click()
        assert response.value.status == 200, response.value.text()
        expect(page.locator("[data-source-setup]")).to_have_count(0, timeout=120000)
        import source_association, subject_attribution as subjects
        assert not source_association.hold(scope.cases / client)
        assert subjects.affected_keys(scope.cases / client)
        assert (scope.documents / client / "source" / row["stored"]).is_file()
        assert not (scope.cases / client / "decisions.json").exists()
        assert not errors
    finally:
        context.close()


def test_portal_only_row_chooser_uses_same_recovery_and_does_not_claim_read(browser, server, world):  # noqa: F811 -- pytest fixture injection
    context, page, errors = login(browser, server)
    try:
        page.locator("#all").click()  # actual All clients action, not same-document hash navigation
        row = page.locator('tr[data-client="' + world["client"] + '"]')
        expect(row).to_be_visible(timeout=60000)
        row.locator("details.rowmenu summary").click()
        with page.expect_file_chooser() as chooser:
            row.get_by_role("button", name="Add documents", exact=True).click()
        chooser.value.set_files({"name": "Portal-only fictional.pdf", "mimeType": "application/pdf", "buffer": document_pdf(world["pages"][0].splitlines())})
        panel = one_attempt(page)
        expect(panel.locator("[data-upload-status]")).to_contain_text("could not be started here", timeout=60000)
        assert "being read now" not in panel.inner_text()
        assert len(world["store"].uploads(world["client"])) == 1
        assert not (world["scope"].cases / world["client"] / "fact_graph.json").exists()
        capture(page, "portal-only-staff-upload-worker-unavailable-1000")
        assert not errors
    finally:
        context.close()


def test_invalid_selection_can_be_removed_then_valid_selection_succeeds(browser, server, world):  # noqa: F811 -- pytest fixture injection
    context, page, errors = login(browser, server)
    try:
        prepared_case(page, server, world)
        selected(page, world, "Malformed fictional.pdf", b"%PDF invalid fictional file")
        panel = one_attempt(page)
        expect(panel.locator("[data-upload-status]")).to_contain_text("request was refused", timeout=60000)
        expect(panel.get_by_role("button", name="Remove from this page", exact=True)).to_be_enabled()
        page.once("dialog", lambda dialog: dialog.accept())
        panel.get_by_role("button", name="Remove from this page", exact=True).click()
        selected(page, world)
        panel = one_attempt(page)
        expect(panel.locator("[data-upload-status]")).to_contain_text("Received; reading is queued", timeout=60000)
        assert len(list(world["scope"].queue.glob("*-staff_upload-*.json"))) == 1
        assert not errors
    finally:
        context.close()


def test_busy_reader_keeps_same_file_attempt_for_explicit_retry(browser, server, world):  # noqa: F811 -- pytest fixture injection
    from staff_upload_lock_fixtures import held_reader
    context, page, errors = login(browser, server)
    try:
        prepared_case(page, server, world)
        with held_reader(world):
            with page.expect_response(lambda r: r.url.endswith("/api/client-upload") and r.request.method == "POST", timeout=5000) as busy:
                selected(page, world)
            assert busy.value.status == 409 and "being read right now" in busy.value.json()["error"]
            panel = one_attempt(page)
            attempt = panel.get_attribute("data-staff-upload-attempt")
            expect(panel.locator("[data-upload-status]")).to_contain_text("request was refused")
            expect(panel).to_contain_text("being read right now")
            with page.expect_response(lambda r: "/api/staff-upload-outcome?" in r.url, timeout=5000) as check:
                panel.get_by_role("button", name="Check outcome", exact=True).click()
            assert check.value.status == 409
            expect(panel.locator("[data-upload-status]")).to_contain_text("Outcome unknown")
            expect(panel.get_by_role("button", name="Retry this same file", exact=True)).to_be_enabled()
            assert not (world["scope"].cases / world["client"] / "staff-upload-receipts").exists()
            assert not list(world["scope"].queue.glob("*-staff_upload-*.json"))
        with page.expect_response(lambda r: r.url.endswith("/api/client-upload") and r.request.method == "POST", timeout=60000) as retry:
            panel.get_by_role("button", name="Retry this same file", exact=True).click()
        assert retry.value.status == 200 and retry.value.request.post_data_json["attempt"] == attempt
        assert retry.value.json()["received"] and not retry.value.json()["processed"]
        panel = one_attempt(page)
        expect(panel.locator("[data-upload-status]")).to_contain_text("could not be started here", timeout=60000)
        assert len(list(world["scope"].queue.glob("*-staff_upload-*.json"))) == 1
        assert len(list((world["scope"].documents / world["client"] / "source").glob("*.pdf"))) == 1
        assert not errors
    finally:
        context.close()
