"""Staff can see both printed parents and assign them without retyping names."""
# ruff: noqa: F811 -- canonical fixtures use pytest injection
import documents
from playwright.sync_api import expect
from test_assignment_routes import app, controls, server, world  # noqa: F401
from test_client_picker_refresh_browser import login
from test_people import SECOND_TRANSLATOR
from synthetic_documents import process_retained_documents
from review.state import save_bundle
from factgraph import FactGraph
import os
import json
from pathlib import Path
import pytest


@pytest.fixture
def browser():
    """This scoped acceptance uses the installed browser with its sandbox on."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        running = playwright.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None,
            chromium_sandbox=True,
            args=["--enable-automation"],
        )
        if os.environ.get("E2E_SHOTS"):
            import json
            destination = Path(os.environ["E2E_SHOTS"])
            destination.mkdir(parents=True, exist_ok=True)
            session = running.new_browser_cdp_session()
            (destination / "parent-chromium-launch.json").write_text(json.dumps(session.send("Browser.getBrowserCommandLine"), indent=2))
            session.detach()
        yield running
        running.close()


def test_birth_parent_names_are_visible_and_one_review_adds_named_parents(browser, server, world, app, tmp_path):
    case = world / "case-Ana"
    source = tmp_path / "birth-source"
    # This UI acceptance supplies two agreeing source versions. The shared
    # layout fixture's extra leading OCR letter (lrosa) is unresolved by the
    # conservative parent-version reader and is tested separately below.
    agreed_parents = SECOND_TRANSLATOR.replace("lrosa Ficcaog", "Ficcaol Ficcaog")
    save_bundle(process_retained_documents(case.name, source, [("birth.pdf", agreed_parents)]), case, source)
    for filename in ("fact_graph_raw.json", "fact_graph.json"):
        graph = FactGraph.load(case / filename)
        for role, name in (("mother", "FICCAOL FICCAOG DA FICCAOB"), ("father", "ANA FICCAOO DOS FICCAOK")):
            graph.add_source(f"questionnaire.{role}_birth_name", "portal questionnaire", "intake_questionnaire", name, name, .95, tier=3)
        graph.save(case / filename)
    app.roster.touch(case.name)
    context, page = login(browser, server)
    diagnostics = {"requests": [], "responses": [], "console": [], "page_errors": []}
    page.on("request", lambda request: diagnostics["requests"].append({"method": request.method, "url": request.url}) if "/api/document" in request.url else None)
    def document_response(response):
        if "/api/document" in response.url:
            entry = {"method": response.request.method, "url": response.url, "status": response.status}
            if "/api/documents?" in response.url and response.status == 200:
                entry["model"] = response.json()
            diagnostics["responses"].append(entry)
    page.on("response", document_response)
    page.on("console", lambda message: diagnostics["console"].append({"type": message.type, "text": message.text}))
    page.on("pageerror", lambda error: diagnostics["page_errors"].append(str(error)))
    try:
        page.goto(server + "/?tab=documents#case-Ana")
        card = page.locator("section[data-subject-instance]")
        expect(card).to_have_count(1, timeout=60000)
        expect(card.get_by_text("Ficcaol Ficcaog Da Ficcaob", exact=True)).to_be_visible()
        expect(card.get_by_text("Ana Ficcaoo Dos Ficcaok", exact=True)).to_be_visible()
        people = page.request.get(server + "/api/documents?client=case-Ana").json()["case_subjects"]
        identity = next(p["id"] for p in people if p["case_role"] == "applicant")
        card.get_by_label("Person whose birth is recorded in", exact=False).select_option(identity)
        expect(card.get_by_label("parent a in", exact=False)).to_have_value("new:mother")
        expect(card.get_by_label("parent b in", exact=False)).to_have_value("new:father")
        mode = os.environ.get("PARENT_REVIEW_DELAYED_MODEL")
        if mode:
            assert mode in {"identical", "changed"}
            delayed = []
            def hold_model(route):
                response = route.fetch()
                model = response.json()
                if mode == "changed":
                    # A fictional suggestion caution changes the displayed model,
                    # without changing retained source truth or assignment proof.
                    model["subject_reviews"][0]["shadow"]["roles"]["birth_subject"]["reason"] += " Fictional diagnostic quality caution: review the original."
                delayed.append((route, response, model))
                page.evaluate("() => { window.__parentModelHeld = true; }")
            page.route("**/api/documents?*", hold_model, times=1)
            page.evaluate("() => { void renderDocumentsTab(document.querySelector('main')); }")
            page.wait_for_function("() => window.__parentModelHeld === true")
            route, response, model = delayed.pop()
            with page.expect_response(lambda r: "/api/documents?" in r.url and r.status == 200):
                route.fulfill(response=response, json=model)
            if mode == "changed":
                expect(card.get_by_role("button", name="Review updated information", exact=True)).to_be_visible()
                expect(card.get_by_role("button", name="Confirm document person", exact=True)).to_be_disabled()
                diagnostics["delayed_model_guard"] = card.evaluate("node => ({needsReview: node.dataset.needsReview, visibleUpdate: !!node.querySelector('[data-review-update]')})")
                assert not any(r["method"] == "POST" for r in diagnostics["requests"])
                card.get_by_role("button", name="Review updated information", exact=True).click()
                # Restore the genuine current backend model before confirming;
                # the real update affordance preserves and rechecks the draft.
                with page.expect_response(lambda r: "/api/documents?" in r.url and r.status == 200):
                    page.evaluate("() => { void renderDocumentsTab(document.querySelector('main')); }")
                expect(card.get_by_role("button", name="Review updated information", exact=True)).to_be_visible()
                card.get_by_role("button", name="Review updated information", exact=True).click()
            expect(card.get_by_role("button", name="Confirm document person", exact=True)).to_be_enabled()
            assert not card.get_attribute("data-needs-review")
            expect(card.get_by_label("Person whose birth is recorded in", exact=False)).to_have_value(identity)
            expect(card.get_by_label("parent a in", exact=False)).to_have_value("new:mother")
            expect(card.get_by_label("parent b in", exact=False)).to_have_value("new:father")
            diagnostics["delayed_model_mode"] = mode
        pause = int(os.environ.get("PARENT_REVIEW_PAUSE_MS", "0"))
        if pause:
            page.wait_for_timeout(pause)
        diagnostics["before_confirmation"] = card.evaluate("node => ({needsReview: node.dataset.needsReview || null, values: [...node.querySelectorAll('select')].map(e => ({label: e.getAttribute('aria-label'), value: e.value}))})")
        diagnostics["before_confirmation"]["application"] = page.evaluate("({reviewer: reviewer(), accounts: ME.accounts, user: {name: ME.user?.name, role: ME.user?.role}, subjectParts: [...WORK.parts.entries()].filter(([key]) => key.startsWith('subject:')).map(([key, node]) => ({key, needsReview: node.dataset.needsReview || null, signature: node._workSignature || null, pending: !!node._pendingWork}))})")
        with page.expect_response(lambda r: "/api/document" in r.url and r.request.method == "POST") as saved:
            card.get_by_role("button", name="Confirm document person", exact=True).click()
        assert saved.value.status == 200
        current = {p["case_role"]: p for p in documents.read(case)["case_subjects"]["people"]}
        assert current["mother"]["label"] == "FICCAOL FICCAOG DA FICCAOB"
        assert current["father"]["label"] == "ANA FICCAOO DOS FICCAOK"
        assert current["mother"]["who"] == "Jane Doe"
        assert not page.request.get(server + "/api/documents?client=case-Ana").json()["subject_reviews"][0]["held"]
        assert not diagnostics["page_errors"]
    finally:
        if os.environ.get("E2E_SHOTS"):
            destination = Path(os.environ["E2E_SHOTS"])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "parent-confirmation-diagnostic.json").write_text(json.dumps(diagnostics, indent=2))
        context.close()


@pytest.mark.parametrize("width", [1400, 1000])
def test_travel_history_and_latest_i94_have_distinct_review_labels_without_assumed_holder(browser, server, world, app, tmp_path, width):
    case = world / "case-Ana"
    source = tmp_path / "travel-source"
    history = "Travel History Results\nRow Date Type Location\n1 2026-01-01 Arrival BOS\nName: FICTIONAL PERSON\nClass of Admission: B2"
    latest = "Most Recent I-94\nAdmission (I-94) Record Number: 11111111111\nLast/Surname: SAMPLE\nFirst (Given) Name: ALPHA\nBirth Date: 01/02/2000"
    result = process_retained_documents(case.name, source, [("history.pdf", history), ("latest.pdf", latest)],
                                        pages={"history.pdf": [history], "latest.pdf": [latest]})
    save_bundle(result, case, source)
    app.roster.touch(case.name)
    context, page = login(browser, server)
    try:
        page.set_viewport_size({"width": width, "height": 1000})
        page.goto(server + "/?tab=documents#case-Ana")
        expect(page.get_by_text("Travel history", exact=True)).to_be_visible(timeout=60000)
        expect(page.get_by_text("I-94 arrival record", exact=True)).to_be_visible()
        response = page.request.get(server + "/api/documents?client=case-Ana")
        assert response.status == 200
        rows = response.json()["documents"]
        retained_history = next(row for row in rows if row["type"] == "travel_history")
        assert retained_history["name"] == "Travel history"
        assert retained_history["person"] == "unknown" and retained_history["person_basis"] == "unknown"
        assert "status" not in retained_history["roles"] and "entry" not in retained_history["roles"]
        assert not any(any(source.doc_id == "history.pdf" for source in fact.sources) for fact in result.graph.all_facts().values())
        shots = os.environ.get("E2E_SHOTS")
        if shots:
            destination = Path(shots)
            destination.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(destination / f"travel-history-{width}.png"), full_page=True)
    finally:
        context.close()


def test_unmatched_parent_versions_have_no_invented_parent_review_assignments(browser, server, world, app, tmp_path):
    case = world / "case-Ana"
    source = tmp_path / "ambiguous-birth-source"
    save_bundle(process_retained_documents(case.name, source, [("birth.pdf", SECOND_TRANSLATOR)]), case, source)
    app.roster.touch(case.name)
    context, page = login(browser, server)
    try:
        page.goto(server + "/?tab=documents#case-Ana")
        card = page.locator("section[data-subject-instance]")
        expect(card).to_have_count(1, timeout=60000)
        expect(card.get_by_label("Person whose birth is recorded in", exact=False)).to_be_visible()
        expect(card.get_by_label("parent a in", exact=False)).to_have_count(0)
        expect(card.get_by_label("parent b in", exact=False)).to_have_count(0)
        expect(card.get_by_text("Ficcaol Ficcaog Da Ficcaob", exact=True)).to_have_count(0)
        expect(card.get_by_text("Ana Ficcaoo Dos Ficcaok", exact=True)).to_have_count(0)
        rows = page.request.get(server + "/api/documents?client=case-Ana").json()["subject_reviews"]
        assert rows[0]["held"]
        assert not any("birth_cert.parent_" in fact["key"] for fact in rows[0]["facts"])
    finally:
        context.close()
