"""The client's names in the browser (wave K, brief K1): a made-up client (demo-ana's documents, the Exemplo family) who married after
her I-360 was approved, with a Massachusetts certificate that prints her surname after marriage. The paralegal settles "Which name is
current?" with every document open; the attorney's card "USCIS knows the client by another name" is closed to the paralegal and saved
by the attorney; the packet stops naming them, and the I-485 carries the name after marriage in Part 1 item 1 and the earlier name in
item 2. The case page shows the client's names; nothing about them reaches the client's own page."""

from __future__ import annotations

import json
import shutil

from playwright.sync_api import expect
from pypdf import PdfReader
from test_marriage_printed_name_browser import confirm_name_sources
from test_name_cards import _retained_name_case
from test_name_events import MA_AFTER, MA_REAL_SHAPE

HEADERS = {"X-Review-App": "1", "Content-Type": "application/json"}
MARRIED, EARLIER = "ANA CLARA EXEMPLO SOUZA TESTE", "ANA CLARA EXEMPLO SOUZA"


def _retained(world, tmp_path, client, marriage, party="party_a"):
    source_case = _retained_name_case(tmp_path, marriage, applicant_party=party, client=client)
    destination = world["clients"] / client
    shutil.copytree(source_case, destination)
    return destination


def _married(world, tmp_path):
    return _retained(world, tmp_path, "case-names", MA_AFTER)


def _card(screen, title):
    return screen.page.locator("article.card", has=screen.page.get_by_role("heading", name=title, exact=True))


def test_the_name_cards_the_save_and_the_packet_after(world, attorney, paralegal, tmp_path):
    d = _married(world, tmp_path)

    paralegal.open("case-names", "fix")
    card = _card(paralegal, "Which name is current?")
    text = card.inner_text()
    for words in (MARRIED, EARLIER, "Birth certificate", "USCIS I-360 approval notice", "Marriage certificate", "07/01/2026",
                  "Surname after marriage: EXEMPLO SOUZA TESTE", f"Shown for now: {MARRIED}.", "Other names used (Part 1, Item 2): " + EARLIER):
        assert words in text, (words, text)
    assert card.locator(".namepage").count() == 3 and card.get_by_text("Open original page 1").count() >= 3  # each document open at its page
    assert "applicant." not in text and ".pdf" not in text.replace("casamento.pdf", "")
    paralegal.check("names-card")
    assert card.locator(f"input[type=radio][value='{MARRIED}']").is_checked()
    with paralegal.page.expect_response(lambda response: "/api/decide" in response.url) as saved:
        card.get_by_role("button", name="Save").click()
    assert saved.value.status == 200, saved.value.text()
    paralegal.toast()
    paralegal.settle()
    expect(_card(paralegal, "Which name is current?")).to_have_count(0)

    paralegal.open("case-names", "attorney")
    card = _card(paralegal, "USCIS knows the client by another name")
    assert f"USCIS knows the client as {EARLIER}" in card.inner_text() and "An attorney decides this one." in card.inner_text()
    assert card.get_by_role("button", name="Save").count() == 0  # the attorney's decision

    attorney.open("case-names", "attorney")
    card = _card(attorney, "USCIS knows the client by another name")
    assert f"this filing will say {MARRIED}" in card.inner_text() and "Nothing is filed as a name change request" in card.inner_text()
    attorney.check("names-uscis-card")
    card.locator("input.note").fill("The marriage certificate goes in the packet beside the I-360 approval.")
    card.get_by_role("button", name="Save").click()
    attorney.toast()
    attorney.settle()
    assert _card(attorney, "USCIS knows the client by another name").count() == 0

    attorney.open("case-names", "done")  # the Decision log: both, under each person's name, each with Undo
    log = attorney.text()
    assert "Which name is current?" in log and "USCIS knows the client by another name" in log and "Paulo Paralegal" in log
    attorney.check("names-decision-log")

    attorney.open("case-names", "packet")
    assert "is not saved yet" not in attorney.text()
    confirm_name_sources(paralegal.page, world["review"].rstrip("/"), "case-names", d)
    r = attorney.page.request.post(world["review"].rstrip("/") + "/api/apply", headers=HEADERS, data=json.dumps({"client": "case-names"}))
    assert r.status == 200, r.text()
    filled = {k.rsplit(".", 1)[-1]: str(v.get("/V") or "") for k, v in PdfReader(str(d / "i485_filled.pdf")).get_fields().items()}
    assert (filled["Pt1Line1_FamilyName[0]"], filled["Pt1Line1_GivenName[0]"]) == ("EXEMPLO SOUZA TESTE", "ANA CLARA")
    assert (filled["Pt1Line2_FamilyName[0]"], filled["Pt1Line2_GivenName[0]"]) == ("EXEMPLO SOUZA", "ANA CLARA")

    attorney.open("case-names", "journey")
    names = attorney.page.locator("#client-names").inner_text()
    assert f"On every form: {MARRIED}." in names and "Marriage certificate" in names
    attorney.check("names-case-page")


def test_a_certificate_with_no_name_after_marriage_asks_and_a_typed_name_settles_every_form(world, attorney, paralegal, tmp_path):
    """Brief K6: the certificate (the shape of a real Massachusetts city clerk's copy) prints no name after marriage. The card asks with
    nothing picked and two empty boxes; the paralegal types the name; the I-485 carries it in item 1 and the birth name in item 2."""
    d = _retained(world, tmp_path, "case-names-asked", MA_REAL_SHAPE, "party_b")

    paralegal.open("case-names-asked", "fix")
    card = _card(paralegal, "Which name is current?")
    text = card.inner_text()
    assert "No attributable post-marriage name was read for the client on the marriage certificate of 07/01/2026." in text
    assert "it prints no name after marriage" not in text and "Nothing is picked for you" in text and "Shown for now" in text
    assert card.locator("input[type=radio]:checked").count() == 0  # nothing chosen for the reviewer
    boxes = card.locator("input[type=text]:not(.note)")
    assert boxes.count() == 2 and boxes.nth(0).input_value() == "" and boxes.nth(1).input_value() == ""  # empty, prefilled with nothing
    assert "Shown for information only" in text
    for bad in ("applicant.", "—", " -- ", "2026-07-01"):
        assert bad not in text, bad
    paralegal.check("names-asked-card")
    boxes.nth(0).fill("Ana Clara")
    boxes.nth(1).fill("Exemplo Souza Teste")
    with paralegal.page.expect_response(lambda response: "/api/decide" in response.url) as saved:
        card.get_by_role("button", name="Save").click()
    assert saved.value.status == 200, saved.value.text()
    paralegal.toast()
    paralegal.settle()
    expect(_card(paralegal, "Which name is current?")).to_have_count(0)
    entry = json.loads((d / "decisions.json").read_text(encoding="utf-8"))["names:applicant.name_current"]
    assert entry["reviewer"] == "Paulo Paralegal" and entry["values"] == {"applicant.name_chosen_given": "ANA CLARA",
                                                                          "applicant.name_chosen_family": "EXEMPLO SOUZA TESTE"}

    confirm_name_sources(paralegal.page, world["review"].rstrip("/"), "case-names-asked", d, must_review_current=False)
    r = attorney.page.request.post(world["review"].rstrip("/") + "/api/apply", headers=HEADERS, data=json.dumps({"client": "case-names-asked"}))
    assert r.status == 200, r.text()
    filled = {k.rsplit(".", 1)[-1]: str(v.get("/V") or "") for k, v in PdfReader(str(d / "i485_filled.pdf")).get_fields().items()}
    assert (filled["Pt1Line1_FamilyName[0]"], filled["Pt1Line1_GivenName[0]"]) == ("EXEMPLO SOUZA TESTE", "ANA CLARA")
    assert (filled["Pt1Line2_FamilyName[0]"], filled["Pt1Line2_GivenName[0]"]) == ("EXEMPLO SOUZA", "ANA CLARA")


def test_an_undo_reopens_the_card(world, paralegal, tmp_path):
    _retained(world, tmp_path, "case-names-undo", MA_AFTER)
    paralegal.open("case-names-undo", "fix")
    card = _card(paralegal, "Which name is current?")
    card.locator(f"input[type=radio][value='{EARLIER}']").check()
    card.get_by_role("button", name="Save changes").or_(card.get_by_role("button", name="Save")).first.click()
    paralegal.toast()
    paralegal.open("case-names-undo", "done")
    row = paralegal.page.locator("tr", has_text="Which name is current?")
    assert EARLIER in row.inner_text()
    row.get_by_role("button", name="Undo").click()
    paralegal.settle()
    paralegal.open("case-names-undo", "fix")
    assert _card(paralegal, "Which name is current?").count() == 1
    paralegal.check("names-after-undo")
