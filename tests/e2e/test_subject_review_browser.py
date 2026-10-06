"""A signed-in paralegal assigns a fictional retained source, then undoes it."""
import json
from pathlib import Path
import os
import pytest
from upload_workflow_fixtures import source_firm  # noqa: F401 -- canonical isolated installation


@pytest.fixture
def notice_environment(source_firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    """Reuse the actual prepared upload fixtures without replacing the e2e world."""
    import upload_workflow_fixtures as fixtures
    prepared = fixtures.world.__wrapped__(source_firm, monkeypatch)
    application = fixtures.app.__wrapped__(prepared)
    assert application.accounts.path == prepared["scope"].data / "review_users.json"
    endpoint = fixtures.server.__wrapped__(application)
    try:
        yield prepared | {"server": next(endpoint)}
    finally:
        next(endpoint, None)  # run the shared fixture's post-yield shutdown


def test_staff_subject_review_and_undo(world, paralegal, tmp_path):
    from playwright.sync_api import expect
    from portal.store import PortalStore
    from review.state import save_bundle
    from synthetic_documents import process_retained_documents
    fixture = json.loads((Path(__file__).parents[1] / "fixtures" / "document_instances.json").read_text())
    client = "subject-fictional"
    source = tmp_path / "subject-source"
    save_bundle(process_retained_documents(client, source, [("subject_exercise.pdf", fixture["same_type_i94"][0])]), world["clients"] / client, source)
    PortalStore(world["portal"]).add_client(client, "Alpha Example", email="subject@example.invalid")
    screen = paralegal
    screen.open(client, "documents")
    card = screen.page.locator("section[data-subject-instance]").filter(has=screen.page.locator("a[href*='subject_exercise']"))
    expect(card).to_have_count(1, timeout=120000)
    holder = card.get_by_label("Document holder in", exact=False)
    expect(holder).to_be_visible(timeout=120000)
    expect(holder).to_have_value("")
    payload = screen.page.request.get(world["review"] + "api/documents?client=" + client).json()
    identity = next(p["id"] for p in payload["case_subjects"] if p["case_role"] == "applicant")
    plan = screen.page.request.get(world["review"] + "api/packet?client=" + client).json()
    assert not plan["ready"] and any("whose facts" in message for message in plan["problems"])
    screen.check("subjects-held")
    holder.select_option(identity)
    with screen.page.expect_response(lambda response: "/api/document" in response.url and response.request.method == "POST") as confirmed:
        card.get_by_role("button", name="Confirm document person", exact=True).click()
    assert confirmed.value.status == 200
    screen.open(client, "documents")
    screen.page.get_by_text("Confirmed documents (", exact=False).click()
    expect(card.get_by_role("heading", name="Document person confirmed", exact=True)).to_be_visible(timeout=120000)
    expect(card).to_contain_text("Reviewed by Paulo Paralegal")
    expect(card.locator("[data-subject-summary]")).to_contain_text("Document holder:")
    expect(holder).not_to_be_visible()
    expect(card.locator("[data-user-edited]")).to_have_count(0)
    card.get_by_role("button", name="Edit document person", exact=True).click()
    expect(holder).to_be_visible()
    expect(holder).to_have_value(identity)
    expect(card.locator("[data-subject-summary]")).not_to_be_visible()
    screen.check("subjects-confirmed")
    if not card.get_by_role("button", name="Undo fact-subject review").is_visible():
        screen.page.get_by_text("Confirmed documents (", exact=False).click()
    card.get_by_role("button", name="Undo fact-subject review").click()
    expect(card.get_by_role("heading", name="Whose document is this?", exact=True)).to_be_visible(timeout=120000)
    screen.check("subjects-undone")
    data = json.loads((world["clients"] / client / "documents.json").read_text())
    decision = next(value for value in data["subject_assignments"].values() if value["who"] == "Paulo Paralegal")
    assert decision["undone"] and decision["role"] == "paralegal" and len(decision["history"]) == 2


def test_boundary_summary_edit_undo_and_changed_model_preserve_scoped_draft(world, paralegal, tmp_path):
    from playwright.sync_api import expect
    from portal.store import PortalStore
    from review.state import save_bundle
    from synthetic_documents import process_retained_documents

    fixture = json.loads((Path(__file__).parents[1] / "fixtures" / "document_instances.json").read_text())
    client, filename = "boundary-fictional", "boundary_exercise.pdf"
    pages = fixture["same_type_i94"]
    source = tmp_path / "boundary-source"
    save_bundle(process_retained_documents(client, source, [(filename, "\n".join(pages))],
                                          pages={filename: pages}), world["clients"] / client, source)
    PortalStore(world["portal"]).add_client(client, "Alpha Example", email="boundary@example.invalid")
    screen = paralegal
    screen.open(client, "documents")
    row = screen.page.locator("[data-boundary-file]").filter(has=screen.page.locator("a[href*='boundary_exercise']"))
    starts = row.get_by_label("Document start pages in", exact=False)
    expect(starts).to_be_visible(timeout=120000)
    original = screen.page.request.get(world["review"] + "api/file?client=" + client + "&doc=" + filename)
    assert original.status == 200 and original.body() == (source / filename).read_bytes()

    model = screen.page.request.get(world["review"] + "api/documents?client=" + client).json()
    screen.page.route("**/api/documents?**", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(model)))
    posts = []
    screen.page.route("**/api/document", lambda route: (posts.append(route.request.post_data_json), route.continue_()))
    starts.fill("1, 2")
    next(p for p in model["boundaries"] if p["file"] == filename)["reason"] = "Updated fictional boundary explanation"
    with screen.page.expect_response("**/api/documents?**"):
        screen.page.evaluate("client => void loadClient(client)", client)
    expect(row).to_have_attribute("data-needs-review", "true")
    expect(starts).to_have_value("1, 2")
    expect(row.get_by_role("button", name="Confirm these boundaries", exact=True)).to_be_disabled()
    assert posts == []
    row.get_by_role("button", name="Review updated information", exact=True).click()
    expect(row).not_to_have_attribute("data-needs-review", "true")
    expect(starts).to_have_value("1, 2")
    screen.page.unroute("**/api/documents?**")

    with screen.page.expect_response(lambda response: "/api/document" in response.url and response.request.method == "POST") as saved:
        row.get_by_role("button", name="Confirm these boundaries", exact=True).click()
    assert saved.value.status == 200, saved.value.text()
    expect(row.locator("[data-boundary-summary]")).to_be_visible(timeout=120000)
    expect(starts).not_to_be_visible()
    expect(row).to_contain_text("Reviewed by Paulo Paralegal")
    expect(row.locator("[data-user-edited]")).to_have_count(0)
    row.get_by_role("button", name="Edit document boundaries", exact=True).click()
    expect(starts).to_be_visible()
    starts.fill("1, 99")
    with screen.page.expect_response("**/api/documents?**"):
        screen.page.evaluate("client => void loadClient(client)", client)
    expect(starts).to_have_value("1, 99")
    with screen.page.expect_response(lambda response: "/api/document" in response.url and response.request.method == "POST") as undone:
        row.get_by_role("button", name="Undo boundary review", exact=True).click()
    assert undone.value.status == 200, undone.value.text()
    expect(row.locator("[data-boundary-summary]")).to_have_count(0, timeout=120000)
    expect(starts).to_be_visible()
    expect(starts).not_to_have_value("1, 99")
    expect(row.locator("[data-user-edited]")).to_have_count(0)
    decision = json.loads((world["clients"] / client / "documents.json").read_text())["boundary_decisions"][filename]
    assert decision["undone"] and decision["role"] == "paralegal" and len(decision["history"]) == 2


def test_unconfirmed_notice_date_is_visible_to_staff_without_client_event(notice_environment, browser, tmp_path):
    from playwright.sync_api import expect
    from portal.demo import document_pdf
    from review.server import COOKIE
    import jobs
    # Kept local: pytest also has tests/e2e/test_inbox.py, so importing that
    # module name would select a different file under the browser suite.
    notice = ["Department of Homeland Security", "U.S. Citizenship and Immigration Services", "I-797C, Notice of Action",
              "EXEMPLO: DEMONSTRATION DOCUMENT", "Receipt Number Case Type",
              "IOE0999100001 I485 - APPLICATION TO REGISTER PERMANENT RESIDENCE OR ADJUST STATUS",
              "Received Date Priority Date Applicant", "09/30/2026 EXEMPLO SOUZA, ANA CLARA",
              "Notice Date Page", "09/30/2026 1 of 1", "Notice Type: Request for Evidence", "REQUEST FOR EVIDENCE",
              "Please submit the evidence listed below by December 28, 2026."]
    path = tmp_path / "pending notice exercise.pdf"
    path.write_bytes(document_pdf(notice))
    environment = notice_environment
    scope, client, base = environment["scope"], environment["client"], environment["server"]
    context = browser.new_context(viewport={"width": 1280, "height": 1000})
    token = environment["accounts"].session_for("jane@firm.example", how="test")[0]
    context.add_cookies([{"name": COOKIE, "value": token, "url": base}])
    page, errors = context.new_page(), []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        response = page.request.post(base + "/api/source-setup", data={"client": client}, headers={"X-Review-App": "1"})
        assert response.status == 200, response.text()
        page.goto(base + "?tab=documents#" + client)
        with page.expect_response(lambda r: r.url.endswith("/api/client-upload") and r.request.method == "POST", timeout=60000) as uploaded:
            page.locator("#dropzone input[type=file]").set_input_files(str(path))
        assert uploaded.value.status == 200, uploaded.value.text()
        receipt = uploaded.value.json()
        assert receipt["received"] and not receipt["processed"]
        # Automatic worker launch remains disabled. Run exactly the accepted
        # fictional job through its current actor/source/policy checks locally.
        result = jobs.run_job(jobs.Context(scope.cases, scope.portal, jobs_root=scope.queue), jobs.get(scope.queue, receipt["job"]["id"]))
        assert result["state"] == "done" and result["result"]["processed"], result
        panel = page.locator("[data-staff-upload-attempt]")
        panel.get_by_role("button", name="Check outcome", exact=True).click()
        expect(panel.locator("[data-upload-status]")).to_contain_text("Read. Document boundaries", timeout=120000)
        stored = json.loads((scope.cases / client / "documents.json").read_text())
        assert any("pending_notice_exercise" in name for row in stored["documents"] for name in row.get("files", []))
        card = page.locator("section[data-subject-instance]").filter(has=page.locator("a[href*='pending_notice_exercise']"))
        expect(card.get_by_label("Person whose case this notice concerns", exact=False)).to_be_visible(timeout=120000)
        with page.expect_response(lambda r: "/api/journey?" in r.url and r.status == 200, timeout=60000) as journey:
            page.goto(base + "?tab=journey#" + client)
        row = journey.value.json()
        assert any(date["value"] == "2026-12-28" for entry in row["pending_evidence"] for date in entry["dates"])
        pending = page.locator("#pending-notice-evidence")
        expect(pending).to_contain_text("2026-12-28")
        expect(pending).to_contain_text("not been accepted")
        expect(pending.locator("a[href*='pending_notice_exercise']")).to_be_visible()
        if os.environ.get("E2E_SHOTS"):
            page.screenshot(path=str(Path(os.environ["E2E_SHOTS"]) / "pending-notice-date.png"), full_page=True)
        assert not any(e["date"] == "2026-09-30" for e in row["client_view"]["happened"])
        assert not any(d["date"] == "2026-12-28" for d in row["deadlines"])
        assert not errors
    finally:
        context.close()
