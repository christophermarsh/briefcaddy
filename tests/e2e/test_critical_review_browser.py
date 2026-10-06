"""Actual staff source-check flow; automated latency is not human labor time."""
import json
import time


def test_named_source_check_and_undo_with_visible_original(world, paralegal, tmp_path):
    from playwright.sync_api import expect
    from portal.store import PortalStore
    from review.state import save_bundle
    from synthetic_documents import process_retained_documents
    text = ["Most Recent I-94", "Admission (I-94) Record Number: 11111111111", "Last/Surname: EXAMPLE", "First (Given) Name: ALPHA"]
    screen = paralegal
    def upload_and_map(client, sibling=False):
        source = tmp_path / (client + "-source")
        originals = [("critical_exercise.pdf", "\n".join(text))]
        if sibling:
            originals.append(("old.pdf", "\n".join(text).replace("11111111111", "22222222222")))
        save_bundle(process_retained_documents(client, source, originals), world["clients"] / client, source)
        PortalStore(world["portal"]).add_client(client, "Alpha Example", email=client + "@example.invalid")
        screen.open(client, "documents")
        doc = screen.page.locator("section[data-subject-instance]").filter(has=screen.page.locator("a[href*='critical_exercise']"))
        expect(doc.get_by_label("Document holder in", exact=False)).to_be_visible(timeout=120000)
        payload = screen.page.request.get(world["review"] + "api/documents?client=" + client).json()
        identity = next(p["id"] for p in payload["case_subjects"] if p["case_role"] == "applicant")
        doc.get_by_label("Document holder in", exact=False).select_option(identity)
        with screen.page.expect_response(lambda response: "/api/document" in response.url and response.request.method == "POST") as confirmed:
            doc.get_by_role("button", name="Confirm document person", exact=True).click()
        assert confirmed.value.status == 200
        screen.open(client, "documents")
        screen.page.get_by_text("Confirmed documents (", exact=False).click()
        expect(doc.get_by_role("heading", name="Document person confirmed", exact=True)).to_be_visible(timeout=120000)
        items = screen.page.request.get(world["review"] + "api/items?client=" + client).json()
        row = next(c for c in items["cards"] if any(f["key"] == "applicant.i94_number" for f in c["facts"]))
        screen.open(client, row["tab"])
        show_all = screen.page.get_by_role("button", name="Show all", exact=True)
        if show_all.is_visible():
            show_all.click()
        return row

    # An unreviewed sibling source for this key must remain visible.
    # Its prerequisite must be visible instead of offering a doomed Confirm.
    blocked_row = upload_and_map("critical-pending-fictional", sibling=True)
    blocked = screen.page.locator("article[data-review-card=" + json.dumps(blocked_row["id"]) + "]")
    expect(blocked).to_have_attribute("data-source-prerequisite", "true")
    expect(blocked.locator(".source-prerequisite")).to_contain_text("Review the related documents first")
    expect(blocked.get_by_role("button", name="Confirm", exact=True)).to_be_disabled()
    expect(blocked.get_by_role("button", name="Confirm document person here")).to_be_visible()
    screen.check("critical-related-source-prerequisite")
    blocked.get_by_role("button", name="Confirm document person here").click()
    expect(blocked.locator("section[data-subject-instance]").first).to_be_visible()
    expect(blocked.get_by_text("Reference options (optional)").first).to_be_visible()
    expect(blocked.get_by_text("Do not use this reading in forms").first).not_to_be_visible()
    screen.check("critical-inline-document-person")

    # A separate empty fictional case exercises the successful action without
    # deleting the rich demo's unresolved evidence or manufacturing approvals.
    client = "critical-fictional"
    case = world["clients"] / client
    row = upload_and_map(client)
    show_all = screen.page.get_by_role("button", name="Show all", exact=True)
    if show_all.is_visible():
        show_all.click()
    card = screen.page.locator("article[data-review-card=" + json.dumps(row["id"]) + "]")
    expect(card).to_have_count(1)
    expect(card.locator(".source-review")).to_contain_text("Check the original source")
    screen.check("critical-before-source-check")
    before = time.monotonic()
    link = card.locator("a[href*='critical_exercise']").first
    expect(link).to_be_visible()
    # Exercise the actual protected original link before the named confirmation.
    with screen.page.expect_popup() as opened:
        link.click()
    opened.value.close()
    log_path = case / "decisions.json"
    before_decisions = json.loads(log_path.read_text()) if log_path.exists() else {}
    assert "fact:applicant.i94_number" not in before_decisions
    button = card.get_by_role("button", name="Confirm", exact=True)
    expect(button).to_be_visible()
    with screen.page.expect_response(lambda response: "/api/decide" in response.url) as saved:
        button.click()
    assert saved.value.status == 200, saved.value.text()
    expect(card).to_have_count(0, timeout=120000)
    elapsed = time.monotonic() - before
    decisions = json.loads(log_path.read_text())
    decision = decisions["fact:applicant.i94_number"]
    assert decision["reviewer"] == "Paulo Paralegal" and decision["role"] == "paralegal"
    assert decision["evidence_confirmation"]["basis"] == "manual_retained_source_review"
    current = screen.page.request.get(world["review"] + "api/items?client=" + client).json()
    logged = next(d for d in current["done"] if d["id"] == "fact:applicant.i94_number")
    assert not any("fact:applicant.i94_number" in c["item_ids"] for c in current["cards"])
    screen.open(client, "done")
    # The Decision log renders its concise headline, not the field's full
    # printed form tooltip. Bind the actual current record identified above.
    label = logged.get("headline") or logged["title"]
    record = screen.page.locator("tr").filter(has_text=label).filter(has=screen.page.get_by_role("button", name="Undo", exact=False))
    expect(record).to_have_count(1)
    with screen.page.expect_response(lambda response: "/api/undo" in response.url) as undone:
        record.get_by_role("button", name="Undo", exact=False).click()
    assert undone.value.status == 200
    screen.open(client, row["tab"])
    show_all = screen.page.get_by_role("button", name="Show all", exact=True)
    if show_all.is_visible():
        show_all.click()
    expect(screen.page.locator("article[data-review-card=" + json.dumps(row["id"]) + "]")).to_have_attribute("data-source-review", "true")
    screen.check("critical-after-undo")
    burden = {"scope": "One synthetic critical I-94 field, after document boundary/subject setup", "source_review_clicks": 2,
              "undo_clicks": 1, "automated_source_review_elapsed_seconds": round(elapsed, 3),
              "human_review_time_seconds": None, "accuracy_estimate": None,
              "limitations": "Scripted browser latency includes server/UI work; it does not measure a human reading or understanding the original."}
    (tmp_path / "critical-review-burden.json").write_text(json.dumps(burden, indent=2), encoding="utf-8")
