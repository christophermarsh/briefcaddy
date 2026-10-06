"""Candidate EV5 UI; retained byte/page identity, not model accuracy."""
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from test_review_evidence_routes import world, app, server, controls  # noqa: F401 -- pytest fixture registration and helper reexports
from evidence_browser_fixtures import retain_case, PORTUGUESE, TRANSLATION
from test_review_evidence_browser import login, open_tab, card_for, shot


def packet_problems(page, server):  # noqa: F811 -- pytest fixture injection
    response = page.request.get(server + "/api/packet?client=case-ana")
    assert response.status == 200, response.text()
    return response.json()["problems"]


def focused_shot(page, card, name):
    if os.environ.get("E2E_SHOTS"):
        folder = Path(os.environ["E2E_SHOTS"]); folder.mkdir(parents=True, exist_ok=True)
        # Element screenshots can move the sticky global header into the
        # middle of a tall card. Hide it only during capture, then restore it.
        header = page.locator(".topbar")
        previous = header.evaluate("node => node.style.visibility")
        header.evaluate("node => { node.style.visibility='hidden'; }")
        try:
            card.screenshot(path=str(folder / (name + "-focused-card.png")))
        finally:
            header.evaluate("(node, previous) => { node.style.visibility=previous; }", previous)


@pytest.mark.parametrize("rotated,width", [(False, 1000), (False, 1400), (True, 1000)], ids=["ordinary-1000", "ordinary-1400", "rotated-scan-1000"])
def test_original_pages_split_preview_and_named_correction_undo(browser, server, world, app, tmp_path, rotated, width):  # noqa: F811 -- pytest fixture injection
    fixture = retain_case(world, app, tmp_path, rotated=rotated)
    context, page, errors = login(browser, server, width)
    try:
        row, card = card_for(page, server, "applicant.i94_number")
        preview = card.locator("figure[data-source-preview]").filter(has=page.locator("img[alt*='original page 1']")).first
        expect(preview).to_be_visible()
        image = preview.locator("img.scan")
        expect(image).to_be_visible()
        image.scroll_into_view_if_needed()
        expect(image).to_have_js_property("complete", True)
        assert image.evaluate("image => image.naturalWidth > 0")
        request = parse_qs(urlparse(image.get_attribute("src")).query)
        assert request["page"] == ["0"] and request["expected_sha256"] == [fixture["sha256"]]
        assert "box" not in request  # no invented crop for a reader without geometry
        expect(preview).to_contain_text("No reliable region recorded; whole page shown")
        expect(preview).to_contain_text("sole page of the retained document instance")
        expect(card).to_contain_text("These values were read from documents")
        expect(card).not_to_contain_text("These are the client's own answers")
        expect(card.locator(".source-review")).to_have_count(1)
        assert page.evaluate("""card => {
            const message = 'Critical document read. Check the original source and confirm, correct, or leave blank; the reader score is not a measured probability of correctness. Additional fictional warning must remain.';
            return renderCard({...card, messages: [message]}).textContent.includes('Additional fictional warning must remain.');
        }""", row)  # only exact duplicated boilerplate may be consolidated
        assert image.evaluate("node => node.getBoundingClientRect().height") <= 280
        preview.get_by_role("button", name="Enlarge original page 1", exact=True).click()
        expect(page.locator("#lightbox")).to_be_visible()
        expect(page.locator("#lightbox-img")).to_have_attribute("alt", image.get_attribute("alt"))
        page.keyboard.press("Escape")
        expect(page.locator("#lightbox")).to_be_hidden()
        link = preview.get_by_role("link", name="Open original page 1", exact=False)
        assert link.get_attribute("href").endswith("#page=1")
        with page.expect_popup() as popup:
            link.click()
        popup.value.close()
        card.get_by_text("Source identity and reading", exact=True).first.click()
        expect(card).to_contain_text(fixture["sha256"])
        name = "after-ui-retained-i94-" + ("rotated" if rotated else "ordinary") + "-" + str(width)
        shot(page, name, fixture)
        focused_shot(page, card, name)

        nta_row, nta = card_for(page, server, "nta.arrival_date")
        nta_preview = nta.locator("figure[data-source-preview]").filter(has=page.locator("img[alt*='original page 2']")).first
        expect(nta_preview).to_be_visible()
        nta_image = nta_preview.locator("img.scan")
        nta_image.scroll_into_view_if_needed()
        expect(nta_image).to_have_js_property("complete", True)
        assert nta_image.evaluate("image => image.naturalWidth > 0")
        request = parse_qs(urlparse(nta_image.get_attribute("src")).query)
        assert request["page"] == ["1"] and request["expected_sha256"] == [fixture["sha256"]]
        assert nta_preview.get_by_role("link").get_attribute("href").endswith("#page=2")
        name = "after-ui-retained-nta-" + ("rotated" if rotated else "ordinary") + "-" + str(width)
        shot(page, name, fixture)
        focused_shot(page, nta, name)

        row, card = card_for(page, server, "applicant.i94_number")
        assert any("applicant.i94_number" in text for text in packet_problems(page, server))
        index = next(i for i, fact in enumerate(row["facts"]) if fact["key"] == "applicant.i94_number")
        card.locator("input[name=" + json.dumps(row["id"] + "::" + str(index)) + "]").fill("22222222222")
        with page.expect_response(lambda r: "/api/decide" in r.url) as saved:
            card.get_by_role("button", name="Save changes", exact=True).click()
        assert saved.value.status == 200, saved.value.text()
        expect(card).to_have_count(0, timeout=60000)
        decisions = json.loads((fixture["case"] / "decisions.json").read_text(encoding="utf-8"))
        decision = decisions["fact:applicant.i94_number"]
        assert decision["reviewer"] == "Jane Doe" and decision["role"] == "paralegal"
        assert decision["action"] == "set" and decision["values"]["applicant.i94_number"] == "22222222222"
        assert decision["evidence_confirmation"]["basis"] == "manual_retained_source_review"
        assert decision["evidence_confirmation"]["model_release_approval"] is False
        assert not any("applicant.i94_number" in text for text in packet_problems(page, server))
        items = page.request.get(server + "/api/items?client=case-ana").json()
        done = next(item for item in items["done"] if item["id"] == "fact:applicant.i94_number")
        open_tab(page, server, "done")
        record = page.locator("tr").filter(has_text=done.get("headline") or done["title"]).filter(has=page.get_by_role("button", name="Undo", exact=False))
        expect(record).to_have_count(1)
        with page.expect_response(lambda r: "/api/undo" in r.url) as undone:
            record.get_by_role("button", name="Undo", exact=False).click()
        assert undone.value.status == 200
        _, card = card_for(page, server, "applicant.i94_number")
        expect(card).to_have_attribute("data-source-review", "true")
        assert any("applicant.i94_number" in text for text in packet_problems(page, server))
        shot(page, "after-ui-named-correction-undone-" + ("rotated" if rotated else "ordinary") + "-" + str(width), fixture)
        assert not errors
    finally:
        context.close()


@pytest.mark.parametrize("width", [1000, 1400], ids=["1000px", "1400px"])
def test_compact_inventory_explicit_translation_and_inert_raw_text(browser, server, world, app, tmp_path, width):  # noqa: F811 -- pytest fixture injection
    import documents
    fixture = retain_case(world, app, tmp_path, translated=True)
    data = documents.read(fixture["case"])
    translated = next(row for row in data["documents"] if "portuguese.pdf" in row["files"])
    # Deliberately adversarial stored reading remains text, never executable HTML.
    translated["text"] += "\n<script>window.syntheticTextExecuted=true</script>"
    (fixture["case"] / "documents.json").write_text(json.dumps(data), encoding="utf-8")
    context, page, errors = login(browser, server, width)
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    try:
        open_tab(page, server, "documents")
        details = page.locator("details[data-document-reading=" + json.dumps(translated["id"]) + "]")
        expect(details).to_be_visible(timeout=60000)
        expect(page.locator("#papers")).not_to_contain_text("Reading the papers this filing asks about", timeout=60000)
        assert not details.evaluate("node => node.open")
        assert not any("/api/document-text" in request for request in requests)
        shot(page, "after-ui-compact-inventory-" + str(width), fixture)
        summary = details.locator("summary")
        summary.focus()
        page.keyboard.press("Enter")
        expect(details.locator("pre[data-original-text]")).to_contain_text(PORTUGUESE, timeout=60000)
        expect(details.locator("pre[data-translated-text]")).to_have_text(TRANSLATION)
        expect(details).to_contain_text("field approval")
        expect(details).to_contain_text("certified")
        assert page.evaluate("window.syntheticTextExecuted") is None
        requested = [request for request in requests if "/api/document-text" in request]
        assert len(requested) == 1 and parse_qs(urlparse(requested[0]).query)["id"] == [translated["id"]]
        summary.click(); summary.click()
        assert len([request for request in requests if "/api/document-text" in request]) == 1
        row = page.locator("tr#doc-" + translated["id"])
        expect(row.get_by_role("link", name="Open original page 1", exact=False)).to_be_visible()
        shot(page, "after-ui-original-and-stored-translation-" + str(width), fixture)
        assert not errors
    finally:
        context.close()


def test_unknown_page_document_only_and_changed_source_preview_refuses(browser, server, world, app, tmp_path):  # noqa: F811 -- pytest fixture injection
    fixture = retain_case(world, app, tmp_path, legacy=True)
    context, page, errors = login(browser, server)
    try:
        open_tab(page, server, "documents")
        expect(page.locator("#dropzone")).to_be_visible(timeout=60000)
        expect(page.locator("#papers")).not_to_contain_text("Reading the papers this filing asks about", timeout=60000)
        links = page.get_by_role("link", name="Open unverified original", exact=False)
        expect(links.first).to_be_visible()
        assert all("#page=" not in href for href in links.evaluate_all("nodes => nodes.map(node => node.href)"))
        assert page.locator("img[src*='/api/crop']").count() == 0
        shot(page, "after-ui-unverified-document-only-1000", fixture)
        assert not errors
    finally:
        context.close()


def test_displayed_fact_hash_rejects_changed_original(browser, server, world, app, tmp_path):  # noqa: F811 -- pytest fixture injection
    from portal.demo import document_pdf
    fixture = retain_case(world, app, tmp_path)
    context, page, errors = login(browser, server)
    try:
        _, card = card_for(page, server, "applicant.i94_number")
        image = card.locator("img.scan").first
        image.scroll_into_view_if_needed()
        expect(image).to_have_js_property("complete", True)
        assert image.evaluate("image => image.naturalWidth > 0")
        url = image.get_attribute("src")
        target = parse_qs(urlparse(url).query)
        decisions_path = fixture["case"] / "decisions.json"
        before_decisions = decisions_path.read_bytes() if decisions_path.exists() else None
        (fixture["source"] / "combined.pdf").write_bytes(document_pdf(["CHANGED FICTIONAL ORIGINAL"]))
        response = page.request.get(server + url)
        assert response.status == 404 and "original changed" in response.json()["error"]
        # The actual image request also revalidates, even after a prior cached preview.
        def same_retry(response):
            parsed = urlparse(response.url)
            query = parse_qs(parsed.query)
            return (parsed.path == "/api/crop" and query.get("browser_retry") == ["1"]
                    and all(query.get(key) == target[key] for key in ("doc", "page", "expected_sha256")))
        with page.expect_response(same_retry) as refused:
            image.evaluate("node => { const source=node.src; node.removeAttribute('src'); node.src=source+'&browser_retry=1'; }")
        assert refused.value.status == 404
        expect(card.locator("img.scan").first).to_have_count(0)
        expect(card.get_by_role("button", name="Enlarge original page 1", exact=True)).to_have_count(0)
        expect(card).to_contain_text("Refresh the case or read the original again before confirming")
        # This is the still-displayed card/proof, not a fresh API card. The
        # authenticated server refuses its stale Confirm without a decision.
        with page.expect_response(lambda r: urlparse(r.url).path == "/api/decide") as confirm:
            card.get_by_role("button", name="Confirm", exact=True).click()
        assert confirm.value.status == 400, confirm.value.text()
        refusal = confirm.value.json()["error"]
        assert (refusal == "item fact:applicant.i94_number is no longer open: reload"
                or any(word in refusal.lower() for word in ("evidence", "source", "boundaries")))
        expect(card).to_contain_text(refusal)
        assert (decisions_path.read_bytes() if decisions_path.exists() else None) == before_decisions
        assert not errors
    finally:
        context.close()
