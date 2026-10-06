"""The agreement and the end of a case, in the browser (src/engagement.py): the attorney approves the letters' wording and makes the agreement; the
client signs it on a phone in Portuguese, reading it beside the English; the attorney counter-signs it; then closes the case, which leaves the
open cases' list and shows under the ended ones, and the client's page shows the closing letter and nothing else. Everyone here is made up.
E2E_SHOTS keeps a screenshot of each step."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

CASE = "case-agree"
NAME = "Clara Exemplo Teste"


@pytest.fixture(scope="module")
def agree_case(world):
    """A case cloned from the demo client, in the portal in Portuguese."""
    from portal.store import PortalStore

    world["world"].clone(world["root"], "demo-ana", CASE)
    store = PortalStore(world["portal"])
    store.add_client(CASE, NAME, email="clara@example.com", language="pt")
    return store


def _shot(page, name: str) -> None:
    shots = os.environ.get("E2E_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(shots) / f"engagement-{name}.png"), full_page=True)


def test_the_client_signs_in_portuguese_the_attorney_counter_signs_then_closes_the_case(world, browser, attorney, agree_case):
    page = attorney.page
    attorney.open(CASE, "engagement")
    text = attorney.check("engagement-start")
    assert "The letters' wording is not approved yet" in text and "No agreement yet" in text
    # the attorney approves the firm's wording (the confirm dialog is accepted), then makes the agreement
    page.get_by_role("button", name="Approve the letters' wording").click()
    attorney.toast()
    attorney.settle()
    page.locator("#agreement label", has_text="Form I-485").first.locator("input").check()
    page.get_by_label("The fee").fill("A flat fee of $2,500, payable in five monthly payments.")
    page.locator("#make-agreement").click()
    attorney.toast()
    attorney.settle()
    text = attorney.check("engagement-made")
    assert "A flat fee of $2,500" in text and "Not sent yet" in text
    page.locator("#send-agreement").click()
    assert "portal" in attorney.toast()
    attorney.settle()
    assert "Waiting for the client's signature" in attorney.text()

    # the client, on a phone, in Portuguese
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="pt-BR", has_touch=True)
    phone = ctx.new_page()
    errors = []
    phone.on("pageerror", lambda e: errors.append(str(e)))
    phone.goto(f"{world['portal_url']}/l/{agree_case.new_link_token(CASE)}")
    phone.wait_for_load_state("networkidle")
    assert "Contrato esperando a sua assinatura" in phone.locator("#agreement").inner_text()
    _shot(phone, "1-waiting")
    phone.get_by_role("button", name="Ler e assinar").click()
    layer = phone.locator("#layer .panel")
    layer.wait_for(state="visible")
    letter = layer.inner_text()
    assert "Carta de contratação e contrato de honorários" in letter and "A mesma carta em inglês" in letter and "Engagement letter and fee agreement" in letter
    assert f"Olá, {NAME}." in letter and f"Dear {NAME}," in letter and "A flat fee of $2,500" in letter
    _shot(phone, "2-letter")
    layer.get_by_role("button", name="Assinar o contrato").click()
    assert "Marque a caixa e digite o seu nome completo primeiro." in layer.inner_text()  # nothing ticked: said in Portuguese
    layer.locator("#agree-letter").check()
    layer.get_by_placeholder("Digite seu nome completo").fill(NAME)
    layer.get_by_role("button", name="Assinar o contrato").click()
    phone.locator("#agreement", has_text="Contrato assinado em").wait_for()  # the signed copy is drawn on the case first: it can take a moment
    card = phone.locator("#agreement").inner_text()
    assert "Contrato assinado em" in card and "Obrigado. O escritório recebeu o seu contrato assinado." in card
    _shot(phone, "3-signed")
    assert not errors

    # the attorney sees the signature and counter-signs
    attorney.open(CASE, "engagement")
    text = attorney.check("engagement-signed")
    assert f"typed name “{NAME}”" in text and "reading the letter in Portuguese" in text
    page.locator("#countersign").click()
    attorney.toast()
    attorney.settle()
    assert "Counter-signed for the firm by" in attorney.text()

    # the attorney closes the case: the closing letter, off the open cases, on the ended ones
    page.get_by_label("What happens to the case").select_option("closed")
    page.get_by_label("Why (kept for the firm)").fill("The green card was approved.")
    page.locator("#end-case-go").click()
    attorney.toast()
    attorney.settle()
    text = attorney.check("engagement-closed")
    assert "Closed since" in text and "No keeping date yet" in text and "Closing letter" in text
    page.locator("#case-ended").wait_for()
    page.goto(world["review"])
    page.wait_for_selector("#client-rows")
    attorney.settle()
    assert CASE not in page.locator("#client-rows").inner_text()
    page.locator("#ended-filter").select_option("ended")
    page.wait_for_function("(c) => document.getElementById('client-rows').innerText.includes(c)", arg=CASE)  # the ended cases are asked of the app, then drawn
    assert CASE in page.locator("#client-rows").inner_text() and "Closed since" in page.locator("#client-rows").inner_text()
    attorney.check("engagement-all-clients-ended")

    # the client's page: the case is closed, the closing letter, nothing else
    phone.reload()
    phone.wait_for_load_state("networkidle")
    body = phone.locator("#main").inner_text()
    assert "O seu caso com o escritório está encerrado desde" in body and "O seu assunto com o nosso escritório" in body and "Your matter is concluded" in body
    assert "Contrato" not in body and phone.locator("#nav li").count() == 0
    _shot(phone, "4-closed")
    assert not errors
    ctx.close()

    # Settings: each office's letters and its keeping period; Keeping current: the files past their keeping date
    page.goto("about:blank")
    page.goto(world["review"] + "#settings:firm-documents")
    page.locator("#set-firm-documents details").first.wait_for()
    letters = page.locator("#set-firm-documents").inner_text()
    assert "Engagement letter and fee agreement" in letters and "Wording approved" in letters and "Closing letter" in letters
    assert page.get_by_text("Keep a closed case's file for (years)").count() >= 1
    attorney.check("engagement-settings-firm-documents")
    page.goto("about:blank")
    page.goto(world["review"])
    page.wait_for_selector("#client-rows")
    page.get_by_role("button", name="Keeping current").click()
    page.locator("#retention h3").wait_for()
    assert "Files past their keeping date" in page.locator("#retention").inner_text() and "No file is past its keeping date." in page.locator("#retention").inner_text()
    attorney.check("engagement-keeping-current")

    # a prospect not processed yet: its agreement, or the letter when the firm does not take it, from the row's menu
    page.goto("about:blank")
    page.goto(world["review"])
    page.wait_for_selector("#client-rows")
    row = page.locator("tr.row", has_text="Nova Exemplo Teste")
    row.locator("details.rowmenu summary").click()
    row.get_by_role("button", name="Agreement and closing").click()
    page.locator("#agreement").wait_for()
    text = attorney.text()
    assert "No agreement yet" in text and "Ending the case" in text
    attorney.check("engagement-prospect")
