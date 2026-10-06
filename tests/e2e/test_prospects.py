"""The first call, in the browser (src/prospects.py, src/case_notes.py, src/apply_for.py): the attorney records a call and shows the person's link; the person answers the first
questions on a phone in Spanish; the office sees who answered; a note and a task are added (the task is in My work); the questions of what could this person apply for are answered
and the screen never says what a person can apply for; the prospect becomes a client through Add a client, filled in from the call, with the answers and notes carried over.
Everyone here is made up. E2E_SHOTS keeps a screenshot of each step."""

from __future__ import annotations

import os
import re
from datetime import date, timedelta
from pathlib import Path

NAME = "Lia Exemplo Telefone"
ID = "prospect-lia-exemplo-telefone"
CONCLUSION = re.compile(r"eligib|qualif|likely|score", re.I)


def _shot(page, name: str) -> None:
    shots = os.environ.get("E2E_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(shots) / f"prospects-{name}.png"), full_page=True)


def saved(phone, selector: str) -> None:
    """The answer typed into the box is sent (the page saves a box when it changes) and the page has taken the server's answer: a person types slower than this test, and two
    answers in flight at once can leave the page showing the older one."""
    with phone.expect_response(lambda r: "/api/answers" in r.url and r.request.method == "PUT"):
        phone.locator(selector).dispatch_event("change")
    phone.wait_for_load_state("networkidle")


def test_a_prospect_answers_on_the_phone_in_spanish_then_becomes_a_client_with_a_note_and_a_task(world, browser, attorney):
    page = attorney.page
    page.goto(world["review"])
    page.wait_for_selector("#client-rows")
    attorney.settle()
    assert NAME not in page.locator("#client-rows").inner_text()

    # Prospects, New prospect: the call is recorded
    page.locator("#prospects-btn").click()
    page.locator("#new-prospect-btn").click()
    page.locator("#prospect-name").fill(NAME)
    page.locator("#prospect-phone").fill("(555) 010-7778")
    page.locator("#prospect-email").fill("lia.telefone@example.com")
    page.locator("#prospect-language").select_option("es")
    page.locator("#prospect-how_heard").fill("A friend from church")
    page.locator("#prospect-note").fill("Called about her nephew, who is 17. He has a letter from a court.")
    page.locator("#new-prospect-form .checks label", has_text="Email").locator("input").check()
    attorney.check("prospects-new-form")
    page.locator("#prospect-save").click()
    attorney.toast()
    page.locator("#prospect-stage").wait_for()
    attorney.settle()
    text = attorney.check("prospects-recorded")
    assert "New" in page.locator("#prospect-stage").inner_text() and "The first questions have not been sent." in text
    assert "Called about her nephew, who is 17." in text and "Spanish" in text and "A friend from church" in text

    # the attorney shows the link on the screen (a working credential: the attorney's)
    page.locator("#prospect-link").click()
    attorney.toast()
    box = page.locator("#prospect-link-box")
    box.wait_for()
    url = box.locator("code").inner_text().strip()
    assert "/l/" in url and "72 hours" in box.inner_text()

    # the person, on a phone, in Spanish
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="es-ES", has_touch=True)
    phone = ctx.new_page()
    errors = []
    phone.on("pageerror", lambda e: errors.append(str(e)))
    phone.goto(url)
    phone.wait_for_load_state("networkidle")
    welcome = phone.locator("#main").inner_text()
    assert "Estas preguntas ayudan a la oficina a conocer su situación" in welcome and "no lo convierte en cliente" in welcome and "Unos 5 a 10 minutos" in welcome
    _shot(phone, "1-welcome")
    phone.locator(".btn.big").first.click()
    phone.locator("#q_given_name").fill("Lia")
    saved(phone, "#q_given_name")
    phone.locator("#q_family_name").fill("Exemplo Telefone")
    saved(phone, "#q_family_name")
    assert "Información sobre usted" in phone.locator("#main").inner_text()
    phone.get_by_role("button", name="Continuar").click()
    phone.locator("#q_birth_country").fill("Brasil")
    saved(phone, "#q_birth_country")
    for _ in range(4):
        phone.get_by_role("button", name="Continuar").click()
    phone.locator("#q_fc_asking").wait_for()
    assert "Lo que quiere preguntar" in phone.locator("#main").inner_text()
    phone.locator("#q_fc_asking").fill("Quiero preguntar por mi sobrino. Tiene una carta de un tribunal.")
    saved(phone, "#q_fc_asking")
    _shot(phone, "2-asking")
    phone.get_by_role("button", name="Continuar").click()
    review = phone.locator("#main").inner_text()
    assert "Revisar y enviar" in review and "no me convierte en cliente de la oficina" in review
    assert "Documentos" not in phone.locator("#nav").inner_text()  # a prospect is asked no documents
    phone.locator("#agree").check()
    phone.get_by_placeholder("Escriba su nombre completo").fill("Lia Exemplo Telefone")
    phone.get_by_role("button", name="Enviar a la oficina").click()
    phone.locator("h1", has_text="¡Listo! Recibimos sus respuestas.").wait_for()
    done = phone.locator("#main").inner_text()
    assert "La oficina se comunicará con usted." in done and "Alguien le llama o le escribe para hablar." in done
    assert phone.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 1
    _shot(phone, "3-sent")
    assert not errors, errors
    ctx.close()

    # the office: waiting for the attorney, each answer says who gave it
    page.goto("about:blank")
    page.goto(world["review"] + "#prospect:" + ID)
    page.locator("#prospect-stage").wait_for()
    attorney.settle()
    text = attorney.check("prospects-answered")
    assert "Waiting for the attorney" in page.locator("#prospect-stage").inner_text()
    assert "Answered by the prospect" in text and page.locator("#prospect-answers [data-q='fc_asking']").input_value().startswith("Quiero preguntar por mi sobrino.")
    assert page.locator("#prospect-answers [data-q='given_name']").input_value() == "Lia"

    # a note and a task, the task for the attorney (it is in My work)
    page.locator("#note-text").fill("Spoke with the aunt: the hearing letter says the court date is next month.")
    page.locator("#note-add").click()
    attorney.toast()
    page.locator("#note-list .note-row", has_text="the hearing letter says").wait_for()
    assert "Ana Attorney" in page.locator("#note-list").inner_text()
    due = (date.today() + timedelta(days=3)).isoformat()
    page.locator("#task-title").fill("Ask the aunt to bring the court letter")
    page.locator("#task-date").fill(due)
    page.locator("#task-who").select_option(label="Ana Attorney (attorney)")
    page.locator("#task-add").click()
    attorney.toast()
    page.locator("#task-list tr", has_text="Ask the aunt to bring the court letter").wait_for()
    attorney.settle()
    assert "Ana Attorney" in page.locator("#task-list").inner_text()
    attorney.check("prospects-note-and-task")

    # what could this person apply for: questions with their sources, answered by a person, never concluded
    page.locator("#relief-sij summary").click()
    row = page.locator("#relief-sij tr[data-question='sij.under21']")
    assert "Source:" in row.inner_text() and "Read:" in row.inner_text()
    row.get_by_role("button", name="Yes").click()
    page.locator("#relief-sij tr[data-question='sij.under21']", has_text="Yes, by Ana Attorney").wait_for()
    panel = page.locator("#apply-panel").inner_text()
    assert "The attorney decides." in panel and "1 of " in page.locator("#apply-count").inner_text()
    assert not CONCLUSION.search(panel), CONCLUSION.findall(panel)
    assert not re.search(r"schemas/|docs/|src/|\.json|\.md|\.py\b", panel), re.findall(r"schemas/\S*|docs/\S*|src/\S*|\S*\.json|\S*\.md|\S*\.py\b", panel)  # no file name on the screen
    assert "held in the product's" in row.inner_text()
    assert not CONCLUSION.search(page.locator("#main").inner_text()), CONCLUSION.findall(page.locator("#main").inner_text())
    attorney.check("prospects-apply-for")

    # My work (the attorney's): the prospect waiting for the attorney, and the task
    page.goto("about:blank")
    page.goto(world["review"])
    page.wait_for_selector("#client-rows")
    page.get_by_role("button", name="My work").click()
    page.locator("section", has_text="Prospects waiting for the attorney").wait_for()
    work = attorney.text()
    assert "prospects waiting for the attorney" in work.lower() and "Lia Exemplo Telefone" in work  # a table heading is drawn in capitals
    assert "Ask the aunt to bring the court letter" in work, work[:1500]
    attorney.check("prospects-my-work")

    # Prospects: one waiting; not in All clients
    page.goto("about:blank")
    page.goto(world["review"] + "#prospects")
    page.locator("#prospect-rows").wait_for()
    assert "Lia Exemplo Telefone" in page.locator("#prospect-rows").inner_text() and page.locator("[data-stage='waiting'] .n").inner_text() == "1"
    assert "Page 1 of 1" in page.locator("#prospects-page").inner_text()
    page.goto("about:blank")
    page.goto(world["review"])
    page.wait_for_selector("#client-rows")
    attorney.settle()
    assert NAME not in page.locator("#client-rows").inner_text()

    # Become a client: Add a client, filled in from the call; the conflict search runs first; the answers, the note and the task go onto the case
    page.goto("about:blank")
    page.goto(world["review"] + "#prospect:" + ID)
    page.locator("#prospect-become").wait_for()
    page.locator("#prospect-become").click()
    page.locator("#add-name").wait_for()
    assert page.locator("#add-name").input_value() == NAME and page.locator("#add-phone").input_value() == "(555) 010-7778" and page.locator("#add-language").input_value() == "es"
    assert "Add a client from a first call" in page.locator("#add-client-form").inner_text()
    attorney.check("prospects-add-a-client")
    page.locator("#add-client-save").click()
    toast = attorney.toast()
    assert f"Added {NAME}." in toast and "first call's answers, notes and tasks are on the case" in toast and "3 answers" in toast and "2 notes" in toast and "1 task" in toast
    page.locator("tr.row", has_text=NAME).wait_for()
    attorney.settle()
    assert NAME in page.locator("#client-rows").inner_text()
    attorney.check("prospects-became-a-client")

    # the client's page for a person with no case file yet: the first call, the carried note, the task, the answers
    page.locator("tr.row", has_text=NAME).locator("details.rowmenu summary").click()
    page.locator("tr.row", has_text=NAME).get_by_role("button", name="Agreement and closing").click()
    page.locator("#notes-panel").wait_for()
    attorney.settle()
    text = attorney.check("prospects-the-case")
    assert "The first call" in text and "A friend from church" in text and "Quiero preguntar por mi sobrino." in text
    assert "from the first call" in page.locator("#note-list").inner_text() and "Ask the aunt to bring the court letter" in page.locator("#task-list").inner_text()
    assert "1 of " in page.locator("#apply-count").inner_text()

    # Prospects now shows the person as a client
    page.goto("about:blank")
    page.goto(world["review"] + "#prospects")
    page.locator("#prospect-rows").wait_for()
    assert "Became a client" in page.locator("#prospect-rows").inner_text() and page.locator("[data-stage='client'] .n").inner_text() == "1"
