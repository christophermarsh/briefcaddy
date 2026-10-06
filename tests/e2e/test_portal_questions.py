"""The office's questions in the client's language (docs/design_plan.md Part 7, item 1), in the browser: the paralegal
asks the Portuguese-reading demo client a date question, sees the English and the Portuguese side by side and writes
the Portuguese (this machine may have no English-to-Portuguese model, so the test types it, as a bilingual paralegal
would correct a draft); the client, on a phone, reads the question in Portuguese with a date picker and answers; the
paralegal reads the date as MM/DD/YYYY. E2E_SHOTS keeps a screenshot of each screen."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import LEAK, REPO

ENGLISH = "What is your father's date of birth?"
PORTUGUESE = "Qual é a data de nascimento do seu pai?"


def _shot(page, name: str) -> None:
    if os.environ.get("E2E_SHOTS"):
        Path(os.environ["E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(os.environ["E2E_SHOTS"]) / f"questions-{name}.png"), full_page=True)


def test_a_portuguese_client_answers_a_date_question_in_portuguese(world, browser, paralegal):
    from portal.store import PortalStore

    # the client reads the portal in Portuguese: another test in the same world (test_pathways) picks English on the portal's language menu,
    # which the portal keeps, and an English reader gets no translation beside the question
    PortalStore(world["portal"]).update_profile("demo-ana", language="pt")
    paralegal.open("demo-ana", "fix")
    card = paralegal.page.locator("article, .card", has_text="Father: Date of birth").filter(has=paralegal.page.get_by_role("button", name="Ask the client")).last
    if card.count() == 0:
        pytest.skip("no father's date of birth card in this world")
    card.get_by_role("button", name="Ask the client").click()
    form = paralegal.page.locator(".ask")
    assert form.get_by_label("Kind of answer").input_value() == "date"  # a date card asks for a date by itself
    # the translator's draft (or "needs a translator") arrives beside it; loading the models can take a while the first time
    with paralegal.page.expect_response(lambda r: "ask-preview" in r.url and ENGLISH in (r.request.post_data or ""), timeout=60000):
        form.get_by_label("Your question, in English").fill(ENGLISH)
    # the note is written when the page has read the answer, a moment after the response (longer on a loaded machine): wait for it, then check what it says
    paralegal.page.wait_for_function("() => /Machine translation|Needs a translator/.test(([...document.querySelectorAll('.ask .ask-two .hint')].pop() || {}).innerText || '')",
                                     timeout=30000)
    note = form.locator(".ask-two .hint").last.inner_text()
    assert "Machine translation" in note or "Needs a translator" in note, note
    form.get_by_label("The question in Portuguese").fill(PORTUGUESE)  # corrected (or written) by the paralegal
    body = paralegal.check("ask-side-by-side")
    assert "In English (for the file)" in body and "In Portuguese (what the client reads)" in body
    form.get_by_role("button", name="Add to the client's list").click()
    paralegal.toast()
    paralegal.settle()
    # the questions tray only: a client message left open by another test in the same world adds a messages tray
    tray = paralegal.page.locator("section.tray", has_text="Questions for the client")
    assert f"Asked in Portuguese: {PORTUGUESE}" in tray.inner_text() and "Answer: a date" in tray.inner_text()
    tray.get_by_role("button", name=re.compile("Send all")).click()
    paralegal.toast()

    from portal.store import PortalStore

    words = "Where does your father live now?"  # a question for words, asked earlier: the box counts against the limit
    PortalStore(world["portal"]).add_request("demo-ana", words, None, "Paulo Paralegal",
                                             typed={"type": "text", "text_client": "Onde o seu pai mora hoje?", "language": "pt",
                                                    "machine_translated": False, "needs_translator": False})
    link = subprocess.run([sys.executable, "src/portal/admin.py", "link", "demo-ana"], cwd=REPO, env=world["env"],
                          capture_output=True, text=True).stdout.split()[0]
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="en-US")  # a phone set to English: the portal still speaks Portuguese
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(link)
    page.wait_for_load_state("networkidle")
    if page.locator(".btn.big").count():  # the welcome: "Continuar de onde parei"
        page.locator(".btn.big").first.click()
        page.locator(".alert .item", has_text=PORTUGUESE).wait_for()  # the questions are drawn (not a fixed pause)
    item = page.locator(".alert .item", has_text=PORTUGUESE)
    assert item.count() == 1, page.locator("body").inner_text()[:800]
    assert "O escritório pediu" in item.inner_text() and ENGLISH not in item.inner_text()
    assert "inglês" not in item.inner_text()  # translated: no "this question is in English" line
    picker = item.locator("input[type=date]")
    assert picker.count() == 1  # a date picker, not a free-text box
    picker.fill("1970-05-04")
    picker.dispatch_event("change")
    assert "4 de maio de 1970" in item.inner_text()  # written back in Portuguese words
    _shot(page, "client-phone")
    body = page.locator("body").inner_text()
    assert not [line for line in body.splitlines() if LEAK.search(line)]
    assert page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 1
    item.get_by_role("button", name="Enviar resposta").click()
    page.wait_for_function("(t) => ![...document.querySelectorAll('.alert .item')].some((e) => e.innerText.includes(t))", arg=PORTUGUESE, timeout=15000)  # not a fixed wait: a busy machine answers slower
    assert page.locator(".alert .item", has_text=PORTUGUESE).count() == 0  # answered: off the client's list
    words_item = page.locator(".alert .item", has_text="Onde o seu pai mora hoje?")
    assert "0 / 2.000" in words_item.inner_text()  # the limit, before a word is typed
    words_item.locator("textarea").fill("x" * 2001)
    assert "2.001 / 2.000" in words_item.inner_text() and "longa demais" in words_item.inner_text()  # over: said in Portuguese
    words_item.locator("textarea").fill("Em Boston.")
    assert "10 / 2.000" in words_item.inner_text()
    _shot(page, "client-words")
    assert not errors, errors
    ctx.close()

    paralegal.open("demo-ana", "fix")
    if paralegal.page.locator("#requests details").get_attribute("open") is None:  # opens by itself once something is answered
        paralegal.page.locator("#requests summary").click()
    log = paralegal.page.locator("#requests").inner_text()
    after = log.split(ENGLISH)[-1]  # the newest request is listed first
    assert "05/04/1970" in after and "Answered" in after and f"Asked in Portuguese: {PORTUGUESE}" in after, log
    # the answer fills the box at once (the card leaves "Needs an answer" for Check), and the card says where it came from, the same
    # Implementation note.
    paralegal.open("demo-ana", "check")
    card = paralegal.page.locator("article.card", has_text="Father").filter(has_text="Filled from the client's answer").first
    prov = card.inner_text()
    assert f"05/04/1970 from the client, answering the office's question “{ENGLISH}”" in prov and f"Asked in Portuguese: {PORTUGUESE}" in prov, prov
    paralegal.check("after-answer")
    _shot(paralegal.page, "staff-answer")
