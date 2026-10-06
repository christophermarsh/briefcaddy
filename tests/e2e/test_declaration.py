"""The client's declaration through the screens (src/drafting.py), on a made-up asylum client cloned from the demo client
(tests/e2e/world.py): the paralegal types the client's account in the I-589's questions; the Declaration card on the packet
page shows it as paragraph 1 with the question beside it; the attorney edits the paragraph (recorded like any decision); the
card refuses "Mark as the client's final" until the drafting practice is approved, then marks it; the signature date is
recorded; the packet holds the declaration in the claim exhibit; the review bundle shows the edit, old and new. Everything
here is invented.
"""

from __future__ import annotations

import json
import re

from pypdf import PdfReader

from test_pathways import answer

ACCOUNT = "In March 2025 men from the gang came to our house and threatened my family because my father did not pay them"
EDITED = "In March 2025, men from the gang came to our house and threatened my family, because my father did not pay them."


def _card(screen):
    return screen.page.locator("#declaration")


def test_the_declaration_card_edit_final_signature_and_bundle(world, attorney, paralegal):
    w = world["world"]
    d = w.clone(world["root"], "demo-ana", "case-declaration")
    w._not_sij(d)
    w.name_paralegal(d)  # an asylum case is restricted: the paralegal is named on it
    w.add_fact(d, "asylum.basis_political", "Yes")

    # the paralegal types the client's account in the I-589's own questions
    paralegal.open("case-declaration", "packet", "i589")
    answer(paralegal, "Part B, 1.A · Have you, your family", "Yes")
    answer(paralegal, "Part B, 1.A · If yes: what happened", ACCOUNT)
    body = paralegal.check("declaration-card-paralegal")
    card = _card(paralegal).inner_text()
    assert "Declaration" in card and "1. Part B, 1.A · If yes: what happened" in card, card
    assert "Typed in the filing's questions by Paulo Paralegal" in card and "English (assumed English" not in card
    assert "The drafting practice is not approved yet" in card and "An attorney approves it." in card
    assert paralegal.page.get_by_role("button", name="Mark as the client's final").count() == 0
    assert "asylum.b1a_explain" not in body  # no fact key on the screen

    # the attorney edits paragraph 1: recorded like any decision, the client's words kept beside it
    attorney.open("case-declaration", "packet", "i589")
    attorney.page.get_by_role("textbox", name="English of paragraph 1").fill(EDITED)
    _card(attorney).get_by_role("button", name="Save the paragraph").click()
    assert attorney.toast() == "Paragraph saved."
    attorney.settle()
    card = _card(attorney).inner_text()
    assert re.search(r"Edited by Ana Attorney \(attorney\) on \d\d/\d\d/\d{4}\. Before: “" + re.escape(ACCOUNT), card), card
    decisions = json.loads((d / "decisions.json").read_text(encoding="utf-8"))
    entry = decisions["declaration:i589:asylum.b1a_explain"]
    assert entry["reviewer"] == "Ana Attorney" and entry["old"] == ACCOUNT and list(entry["values"].values()) == [EDITED]

    # the practice first, then the final mark, then the signature
    _card(attorney).get_by_role("button", name="Approve the drafting practice").click()
    assert attorney.toast() == "Approved."
    attorney.settle()
    _card(attorney).get_by_role("button", name="Mark as the client's final").click()
    assert attorney.toast() == "Marked as the client's final."
    attorney.settle()
    card = _card(attorney).inner_text()
    assert "Marked as the client's final by Ana Attorney" in card and "DRAFT until the client signs" in card, card
    order = attorney.page.locator("section", has_text="In the packet, in order").first.inner_text()
    assert "The client's declaration (DRAFT until the client signs)" in order, order
    pdf = attorney.page.request.get(world["review"].rstrip("/") + "/api/declaration.pdf?client=case-declaration&filing=i589")
    assert pdf.status == 200 and pdf.body()[:5] == b"%PDF-"
    attorney.page.get_by_label("The date the client signed").fill("2026-09-30")
    _card(attorney).get_by_role("button", name="Record the client's signature").click()
    assert attorney.toast() == "Signature recorded."
    attorney.settle()
    body = attorney.check("declaration-card-signed")
    assert "Signed by the client on 09/30/2026 (recorded by Ana Attorney)" in _card(attorney).inner_text()
    assert "The client's declaration, signed 09/30/2026" in attorney.page.locator("section", has_text="In the packet, in order").first.inner_text()

    # the packet and its review bundle: the edit, old and new
    attorney.page.get_by_role("button", name=re.compile("Build packet|Rebuild packet")).click()
    assert "Packet built" in attorney.toast()
    attorney.settle()
    attorney.page.get_by_role("button", name=re.compile("^Review bundle$")).click()
    assert "Review bundle built" in attorney.toast()
    attorney.settle()
    from review import bundle

    text = re.sub(r"\s+", " ", "\n".join(p.extract_text() or "" for p in PdfReader(str(bundle.paths(d, "i589")[0])).pages))
    assert "The client's declaration: where each paragraph came from" in text
    assert "Edited by Ana Attorney (attorney)" in text and "Old: " + ACCOUNT in text and "New: " + EDITED in text
    assert "Signed by the client on 09/30/2026" in text
    assert body

    # leaving the paragraph out changes the text: the mark no longer holds, and the old PDF is never handed out
    attorney.open("case-declaration", "packet", "i589")
    _card(attorney).get_by_role("button", name="Leave this paragraph out").click()
    assert attorney.toast() == "Paragraph left out."
    attorney.settle()
    card = _card(attorney).inner_text()
    assert "Left out" in card and "Changed after it was marked final" in card, card
    stale = attorney.page.request.get(world["review"].rstrip("/") + "/api/declaration.pdf?client=case-declaration&filing=i589")
    assert stale.status == 404 and "changed after it was marked final" in stale.json()["error"]
    attorney.check("declaration-card-left-out")
