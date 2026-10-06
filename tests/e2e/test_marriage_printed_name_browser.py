"""Fictional Massachusetts-style reconstruction, never OCR of the user images.

Actual retained PDF uploads and registered worker reads supply the crucial full
post-marriage name. The browser reviews the current source, saves and undoes the
name choice. No marriage name fact or source approval is injected into the graph.
"""
# ruff: noqa: F401, F811 -- canonical fixtures are imported for pytest injection
import base64
import hashlib
import json
import os
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import pytest
from cloud_daily_work_fixtures import (
    BIRTH,
    STAFF,
    cloud_app,
    cloud_server,
    cloud_world,
    login,
    ok,
    pdf,
    request,
    work,
)
from playwright.sync_api import expect
from pypdf import PdfReader, PdfWriter

import critical_review
from review.state import load_decision_log, reviewed_graph

pytestmark = pytest.mark.skipif(not os.environ.get("E2E"), reason="E2E=1 requests real Chromium name review")
EARLIER = "ALPHA EXAMPLE"
AFTER = "ALPHA NOVEL"
SPOUSE = "DELTA DISTINCT EXAMPLE"
IDENTITY = """The Commonwealth of Massachusetts
Certificate of Marriage
Date of Marriage: JULY 1, 2026
Place of Marriage: BOSTON, MA
Party A Party B
Name: DELTA DISTINCT EXAMPLE Name: ALPHA EXAMPLE
Date of Birth: MARCH 3, 2001 Date of Birth: JANUARY 2, 2000
Place of Birth: BOGOTA, COLOMBIA Place of Birth: CAMPINAS, BRAZIL
"""
SHOTS = Path(__file__).resolve().parents[2] / "docs/research/cloud_marriage_name_ui_evidence/shots" / os.environ.get("MARRIAGE_UI_RUN", "development")


def certificate(tail):
    writer = PdfWriter()
    for text in (IDENTITY, "The Commonwealth of Massachusetts\nCertificate of Marriage\n" + tail):
        writer.add_page(PdfReader(BytesIO(pdf(text))).pages[0])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def make_case(world, base, cookie, tail):
    searched = ok(base, "/api/conflict-search", cookie, {"purpose": "add", "name": "Alpha Example Fictional"})
    cid = ok(base, "/api/client-add", cookie, {"name": "Alpha Example Fictional", "language": "en",
        "filing": "i485", "invite": False, "conflict": {"search": searched["id"], "decision": "none"}})["id"]
    ok(base, "/api/source-setup", cookie, {"client": cid})
    content = certificate(tail)
    for number, (name, data) in enumerate((("Fictional Birth.pdf", pdf(BIRTH)), ("Fictional Party B Marriage.pdf", content)), 1):
        uploaded = ok(base, "/api/client-upload", cookie, {"client": cid, "name": name,
            "data": base64.b64encode(data).decode(), "attempt": str(number) * 32})
        assert uploaded["received"] and work(world) >= 1
    # The deliberately separate continuation page is not automatically trusted
    # as part of the certificate. Review its actual retained two-page range.
    data = ok(base, "/api/documents?client=" + cid, cookie)
    for plan in data["boundaries"]:
        if any(part["state"] == "unresolved" for part in plan["instances"]):
            assert plan["page_count"] == 2
            status, _, original = request(base, "/api/file?client=" + cid + "&doc=" + quote(plan["file"]), cookie)
            assert status == 200 and original == content
            ok(base, "/api/document", cookie, {"client": cid, "field": "boundaries",
                "id": plan["file"], "value": "1", "fingerprint": plan["fingerprint"],
                "note": "Fictional reviewer read both retained pages as one certificate, not two people/documents."})
    # Named subject review is separate from approving a field or legal name.
    data = ok(base, "/api/documents?client=" + cid, cookie)
    for row in data["subject_reviews"]:
        assert row["type"] in {"birth_certificate", "marriage_certificate"} and row["bound"], row
        people = ok(base, "/api/documents?client=" + cid, cookie)["case_subjects"]
        applicant = next(person for person in people if person["case_role"] == "applicant")
        roles = {"birth_subject": "applicant", "father": "father", "mother": "mother",
                 "party_b": "applicant", "party_a": "spouse"}
        mapping = {}
        for slot in row["slots"]:
            role = roles[slot]
            person = applicant if role == "applicant" else next((person for person in people if person["case_role"] == role), None)
            if person is None:
                people = ok(base, "/api/document", cookie, {"client": cid, "field": "subject_person",
                    "value": SPOUSE if role == "spouse" else "Fictional " + role, "case_role": role})["case_subjects"]
                person = next(person for person in people if person["case_role"] == role)
            mapping[slot] = person["id"]
        status, _, original = request(base, "/api/file?client=" + cid + "&doc=" + quote(row["file"]), cookie)
        assert status == 200 and original.startswith(b"%PDF")
        ok(base, "/api/document", cookie, {"client": cid, "field": "subject_assignment",
            "id": row["instance_id"], "fingerprint": row["fingerprint"], "mappings": mapping,
            "note": "Fictional reconstruction: read both named parties and matched Party B to applicant, never by column alone."})
    return cid, content


def screen(browser, base, cookie, cid, width):
    context = browser.new_context(viewport={"width": width, "height": 900})
    key, value = cookie.split("=", 1)
    context.add_cookies([{"name": key, "value": value, "url": base}])
    page = context.new_page()
    page.set_default_timeout(60000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    image_responses = []
    page.on("response", lambda response: image_responses.append({"url": response.url, "status": response.status,
        "content_type": response.headers.get("content-type")}) if "/api/crop?" in response.url and response.request.resource_type == "image" else None)
    page.goto(base)
    expect(page.get_by_role("group", name="Case responsibility scope", exact=True)).to_be_visible(timeout=60000)
    page.locator("#client").click()
    page.get_by_label("Find a client", exact=True).fill(cid)
    expect(page.locator("#client-pick .pick-item").filter(has_text=cid)).to_be_visible(timeout=60000)
    page.get_by_label("Find a client", exact=True).press("Enter")
    page.get_by_role("navigation", name="Review queues").get_by_role("button", name="Needs attention", exact=False).click()
    show = page.get_by_role("button", name="Show all", exact=True)
    if show.is_visible():
        show.click()
    card = page.locator("article.card", has=page.get_by_role("heading", name="Which name is current?", exact=True))
    expect(card).to_be_visible()
    return context, page, card, errors, image_responses


def capture(page, card, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    expect(page.locator("#toast")).to_be_hidden()
    card.evaluate("node => scrollTo(0, Math.max(0, scrollY + node.getBoundingClientRect().top - document.querySelector('.topbar').getBoundingClientRect().height - 16))")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(SHOTS / (name + "-viewport.png")))
    card.screenshot(path=str(SHOTS / (name + "-card.png")))


def confirm_name_sources(page, base, cid, case, *, must_review_current=True):
    """A saved legal-name choice does not replace independent source review."""
    holds = critical_review.problems(case)
    keys = ("applicant.given_name", "applicant.family_name", "applicant.other_name1_given", "applicant.other_name1_family")
    assert not must_review_current or all(any(key in problem for problem in holds) for key in keys[:2])
    for key in keys:
        # A real grouped card can confirm a sibling in the same request. Ask
        # for the current hold before seeking its current source-review card.
        if not any(key in problem for problem in critical_review.problems(case)):
            if any(key in problem for problem in holds):
                decisions = load_decision_log(case)
                current = critical_review.context(case)
                assert any(not decision.get("undone") and key in decision.get("evidence_confirmation", {}).get("keys", {})
                           and critical_review.valid(decision, current) for decision in decisions.values())
            continue
        data = page.request.get(base + "/api/items?client=" + cid).json()
        row = next(row for row in data["cards"] if row.get("source_review") and any(fact["key"] == key for fact in row["facts"]))
        label = {"fix": "Needs attention", "check": "Check answers"}[row["tab"]]
        page.get_by_role("navigation", name="Review queues").get_by_role("button", name=label, exact=False).click()
        show = page.get_by_role("button", name="Show all", exact=True)
        if show.is_visible():
            show.click()
        current = page.locator('article[data-review-card="' + row["id"] + '"]')
        expect(current).to_be_visible()
        assert row["evidence_fingerprints"].get(key)
        with page.expect_response(lambda response: "/api/decide" in response.url) as checked:
            current.get_by_role("button", name="Confirm", exact=True).click()
        assert checked.value.status == 200, checked.value.text()
        expect(current).to_have_count(0)
    assert all(not any(key in problem for problem in critical_review.problems(case)) for key in keys)


@pytest.mark.parametrize("width", [1000, 1400])
def test_actual_party_b_printed_full_name_source_save_form_and_undo(browser, cloud_world, cloud_app, cloud_server, width):
    world, base = cloud_world, cloud_server
    cookie = login(base, STAFF)
    cid, content = make_case(world, base, cookie,
        "Name after Marriage: " + SPOUSE + " Name after Marriage: " + AFTER)
    case = world["scope"].cases / cid
    data = ok(base, "/api/items?client=" + cid, cookie)
    item = next(row for row in data["open"] if row["kind"] == "names")
    event = next(event for event in item["timeline"]["events"] if event.get("parent_key") == "marriage.party_b.name_after")
    assert event["name"] == AFTER and event["party"] == "party_b" and event["page"] == 1
    assert event["source_location"]["source_sha256"] == hashlib.sha256(content).hexdigest()
    assert not load_decision_log(case).get(item["id"])
    context, page, card, errors, image_responses = screen(browser, base, cookie, cid, width)
    try:
        expect(card).to_contain_text("Name after marriage: " + AFTER)
        expect(card).to_contain_text("Party B")
        expect(card).to_contain_text("Shown for now: " + AFTER)
        expect(card.locator(".badge.done").filter(has_text="Current")).to_have_count(0)
        assert "it prints no name after marriage" not in card.inner_text()
        assert card.locator(f'input[type=radio][value="{AFTER}"]').count() == 1
        assert card.locator(f'input[type=radio][value="{SPOUSE}"]').count() == 0
        figure = card.locator(".namepage").filter(has_text=AFTER).first
        image = figure.locator("img.scan")
        figure.scroll_into_view_if_needed()
        expect(image).to_have_js_property("complete", True)
        assert image.evaluate("node => node.naturalWidth > 0")
        assert "page=1" in image.get_attribute("src")
        actual_image = base + image.get_attribute("src")
        assert any(row["url"] == actual_image and row["status"] == 200 and row["content_type"].startswith("image/") for row in image_responses)
        link = figure.locator('a[data-open-source]').first
        assert link.get_attribute("href").endswith("#page=2")
        with page.expect_popup() as opened:
            link.click()
        opened.value.wait_for_url("**#page=2")
        assert "#page=2" in opened.value.url
        opened.value.close()
        capture(page, card, "printed-party-b-" + str(width))
        capture(page, card.locator('[data-fact-key="applicant.name_current"]'), "printed-party-b-choices-" + str(width))
        card.locator(f'input[type=radio][value="{AFTER}"]').check()
        card.locator("input.note").fill("Reviewed the fictional Party B full printed field on original page 2.")
        with page.expect_response(lambda response: "/api/decide" in response.url) as saved:
            card.get_by_role("button", name="Save", exact=True).click()
        assert saved.value.status == 200, saved.value.text()
        expect(card).to_have_count(0)
        record = load_decision_log(case)[item["id"]]
        assert record["reviewer"] == "Fictional Staff" and record["values"]["applicant.name_current"] == AFTER
        assert record["name_evidence"] == item["timeline"]["marriage_evidence"]
        assert len(record["name_evidence"]) == 64
        graph = reviewed_graph(case)
        assert graph.get("applicant.given_name").value == "ALPHA"
        assert graph.get("applicant.family_name").value == "NOVEL"
        assert graph.get("applicant.other_name1_family").value == "EXAMPLE"
        assert not ok(base, "/api/packet?client=" + cid + "&filing=i485", cookie)["ready"]
        confirm_name_sources(page, base, cid, case)
        ok(base, "/api/apply", cookie, {"client": cid})
        fields = {key.rsplit(".", 1)[-1]: str(value.get("/V") or "") for key, value in PdfReader(case / "i485_filled.pdf").get_fields().items()}
        assert (fields["Pt1Line1_GivenName[0]"], fields["Pt1Line1_FamilyName[0]"]) == ("ALPHA", "NOVEL")
        assert (fields["Pt1Line2_GivenName[0]"], fields["Pt1Line2_FamilyName[0]"]) == ("ALPHA", "EXAMPLE")
        page.get_by_role("navigation", name="Review queues").get_by_role("button", name="Decision log", exact=False).click()
        row = page.locator("tr").filter(has_text="Which name is current?").filter(has=page.get_by_role("button", name="Undo", exact=False))
        expect(row).to_contain_text("Fictional Staff")
        with page.expect_response(lambda response: "/api/undo" in response.url) as undone:
            row.get_by_role("button", name="Undo", exact=False).click()
        assert undone.value.status == 200
        page.get_by_role("navigation", name="Review queues").get_by_role("button", name="Needs attention", exact=False).click()
        expect(card).to_be_visible()
        assert load_decision_log(case)[item["id"]]["undone"]["by"] == "Fictional Staff"
        capture(page, card, "printed-party-b-after-undo-" + str(width))
        (SHOTS / ("source-and-decision-" + str(width) + ".json")).write_text(json.dumps({"scope": "Fictional reconstruction, actual PDF reader and protected browser review; no real user OCR replay",
            "certificate_sha256": hashlib.sha256(content).hexdigest(), "event": event,
            "saved_decision": record, "undone_decision": load_decision_log(case)[item["id"]],
            "actual_image_responses": image_responses, "i485_name_fields": {key: value for key, value in fields.items() if key.startswith(("Pt1Line1_", "Pt1Line2_"))}}, indent=2) + "\n")
        assert not errors
    except Exception:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / ("failure-explicit-" + str(width) + ".png")))
        raise
    finally:
        context.close()


@pytest.mark.parametrize("tail,state", [
    ("No post-marriage field extracted", "not_extracted"),
    ("Party B Name after Marriage: -----", "not_stated"),
    ("Party B Name after Marriage: ??? unreadable", "unreadable"),
    ("Name after Marriage: " + AFTER, "ambiguous"),
])
def test_uncertain_actual_read_never_claims_physical_absence_or_offers_guessed_name(browser, cloud_world, cloud_app, cloud_server, tail, state):
    world, base = cloud_world, cloud_server
    cookie = login(base, STAFF)
    cid, _ = make_case(world, base, cookie, tail)
    data = ok(base, "/api/items?client=" + cid, cookie)
    item = next(row for row in data["open"] if row["kind"] == "names")
    assert item["timeline"]["question"]["read_state"] == state
    assert AFTER not in item["facts"][0]["input"]["options"]
    context, page, card, errors, _ = screen(browser, base, cookie, cid, 1000)
    try:
        words = card.inner_text().lower()
        assert "it prints no name after marriage" not in words and "no name after marriage printed" not in words
        assert "nothing is picked for you" in words and "verify the original" in words
        assert card.locator("input[type=radio]:checked").count() == 0
        assert card.locator(f'input[type=radio][value="{AFTER}"]').count() == 0
        assert card.locator(f'input[type=radio][value="{SPOUSE}"]').count() == 0
        question = item["timeline"]["question"]
        if question["page"] is None:
            uncertain = card.locator(".namepage").filter(has_text="verify the original")
            expect(uncertain.locator("img.scan")).to_have_count(0)
            links = uncertain.locator("a[data-open-source]")
            assert links.count() and all("#page=" not in link.get_attribute("href") for link in links.all())
        assert not ok(base, "/api/packet?client=" + cid + "&filing=i485", cookie)["ready"]
        capture(page, card, "uncertain-" + state)
        capture(page, card.locator('[data-fact-key="applicant.name_current"]'), "uncertain-choices-" + state)
        (SHOTS / ("uncertain-" + state + ".json")).write_text(json.dumps(question, indent=2) + "\n")
        assert not errors
    except Exception:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / ("failure-uncertain-" + state + ".png")))
        raise
    finally:
        context.close()
