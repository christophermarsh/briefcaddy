"""The client's side of the portal and the office's side of it, end to end, on the made-up showcase client
(src/portal/demo.py showcase: Beatriz Exemplo Lima, Portuguese): the paralegal finds her message and her blurry photo in My work
and on the case, answers her; then she signs in on a phone, sees the answer, the appointment page (what to bring, where, when,
and printing), the retake task with the phone's camera (a new photo replaces it) and the "Ask the office" thread.
E2E_SHOTS keeps a screenshot of each step.
"""

from __future__ import annotations

import io
import json
import os
from datetime import date
from pathlib import Path

import pytest
from conftest import LEAK
from PIL import Image


@pytest.fixture(scope="module")
def showcase(world):
    """Beatriz, built into the world's own portal and review folders (read by the running servers), and removed afterwards."""
    from portal import demo
    from portal.store import PortalStore

    store = PortalStore(world["portal"])
    built = demo.showcase(store, world["clients"], process=True)
    yield store, built
    demo.reset(store, world["clients"], demo.SHOWCASE_ID)


def _shot(page, name: str) -> None:
    shots = os.environ.get("E2E_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(shots) / f"client-side-{name}.png"), full_page=True)


def test_the_paralegal_finds_the_message_and_the_retake_then_answers(world, paralegal, showcase):
    store, built = showcase
    paralegal.page.goto("about:blank")
    paralegal.page.goto(f"{world['review']}#work")
    paralegal.settle(1500)
    body = paralegal.check("client-side-my-work")
    assert "messages from clients" in body.lower() and "Message from the client: Obrigada! Mando amanhã." in body, body[:1500]  # (headings are shown in capitals)
    assert "photos the client was asked to retake" in body.lower() and "fora de foco" not in body  # the office reads English
    assert "passport: the photo is blurry. Waiting for a new photo." in body
    assert f"Requested {date.today().strftime('%m/%d/%Y')}" in body
    # the case: the retake with its day, the thread, the reply box
    paralegal.open("demo-bia", "fix")
    body = paralegal.check("client-side-case-thread")
    assert f"Retake requested {date.today().strftime('%m/%d/%Y')}: passport, the photo is blurry." in body
    assert "Messages from the client (1 waiting)" in body and "preciso levar alguém comigo" in body
    assert "Bom dia! Posso mandar a foto do passaporte amanhã?" in body and "Answer from Demo Paralegal" in body
    assert "Sent to the client in Portuguese as a machine draft" in body
    box = paralegal.page.locator("textarea[aria-label^='Your answer to the client']")
    box.fill("No one needs to come with you. Bring the appointment letter and a photo ID.")
    paralegal.page.get_by_role("button", name="Show it in Portuguese").click()
    paralegal.page.wait_for_selector("textarea[aria-label='The answer in Portuguese']", state="visible")
    draft = paralegal.page.locator("textarea[aria-label='The answer in Portuguese']")
    note = paralegal.page.locator("#messages .hint").last.inner_text()
    assert "machine draft" in note or "no machine translation" in note  # either way the paralegal reads what the client will see
    if not draft.input_value():  # no translation model installed here: the paralegal types the Portuguese version herself
        draft.fill("Ninguém precisa ir com você. Leve a carta do USCIS e um documento com foto.")
    paralegal.page.get_by_role("button", name="Send the answer").click()
    assert "Answer sent" in paralegal.toast()
    paralegal.settle()
    body = paralegal.check("client-side-case-answered")
    assert "Answer from Paulo Paralegal" in body and "Messages with the client" in body and "Messages from the client (" not in body  # nothing waiting now
    # nothing but "open your page" left the installation: the notification has a link and none of the words
    mail = [json.loads(line) for line in (world["portal"] / "outbox.jsonl").read_text(encoding="utf-8").splitlines()]
    sent = mail[-1]
    assert "/l/" in sent["body"] and "appointment" not in sent["body"] and "photo ID" not in sent["body"] and "USCIS" not in sent["body"]
    assert "respondeu" in sent["body"]  # in the client's language
    # and the message left My work
    paralegal.page.goto("about:blank")
    paralegal.page.goto(f"{world['review']}#work")
    paralegal.settle(1500)
    assert "Obrigada! Mando amanhã." not in paralegal.page.locator("#main").inner_text()
    # the timeline of the case has the message with its time
    paralegal.open("demo-bia", "journey")
    body = paralegal.check("client-side-timeline")
    assert "Message from the client at" in body and "Answer to the client from Paulo Paralegal at" in body


def test_the_client_reads_the_answer_sees_what_to_bring_and_retakes_the_photo_on_a_phone(world, browser, showcase, tmp_path):
    store, built = showcase
    from portal import demo, messages

    messages.reply(store, "demo-bia", "Bring the appointment letter and a photo ID.", "Paulo Paralegal", translate=demo._demo_draft)  # on its own, whatever ran before
    unseen = sum(1 for m in store.messages("demo-bia") if m["from"] == "office" and not m.get("seen_at"))
    when = date.fromisoformat(built["appointment"])
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="pt-BR", has_touch=True)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{world['portal_url']}/l/{store.new_link_token('demo-bia')}")
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text()
    assert "Olá, Beatriz!" in body and "Coleta de digitais (biometria)" in body
    assert "O que levar e onde" in body and f"{when.day} de" in body  # the appointment, in Portuguese words
    _shot(page, "1-welcome")

    # the page for the appointment: when, where, what to bring; and it prints alone
    page.get_by_role("button", name="O que levar e onde").click()
    sheet = page.locator("#layer .prep")
    sheet.wait_for(state="visible")
    text = page.locator("#layer").inner_text()
    for expected in ("QUANDO", "ONDE", "O QUE LEVAR", "· 09:30", "1 Example Street, Springfield, MA 01103", "Confira este endereço na sua carta.",
                     "A carta de compromisso do USCIS (Formulário I-797C)", "Um documento com foto", "Como vai ser o dia", "Se você não puder ir nesta data, avise o escritório"):
        assert expected.lower() in text.lower(), (expected, text)
    _shot(page, "2-appointment-page")
    page.evaluate("() => { window.__printed = 0; window.print = () => { window.__printed++; }; }")
    page.get_by_role("button", name="Imprimir esta página").click()
    assert page.evaluate("window.__printed") == 1
    page.emulate_media(media="print")
    assert page.locator("header.top").is_hidden() and page.locator("#wrap").is_hidden() and sheet.is_visible()  # only the page goes to paper
    assert page.locator("#layer .noprint").is_hidden()  # the print button doesn't print itself
    page.emulate_media(media="screen")
    page.keyboard.press("Escape")

    # into the questionnaire: the appointment stays in view, the photo to retake has the camera
    page.get_by_role("button", name="Continuar de onde parei").click()
    page.wait_for_timeout(600)
    assert page.locator(".nextappt").is_visible() and "Coleta de digitais" in page.locator(".nextappt").inner_text()
    alert = page.locator(".alert", has_text="Precisamos da sua ajuda")
    assert "fora de foco ou tremida" in alert.inner_text() and "passaporte" in alert.inner_text()
    camera = alert.locator("input[type=file][capture=environment]")
    assert camera.count() == 1 and camera.get_attribute("accept") == "image/*"
    _shot(page, "3-retake")
    shot = tmp_path / "passport-new.png"
    out = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(out, format="PNG")
    shot.write_bytes(out.getvalue())
    camera.set_input_files(str(shot))
    page.wait_for_timeout(1500)
    assert "fora de foco" not in page.locator("main").inner_text()  # the new photo replaced the request: "Thank you, we have it"
    assert "Obrigado, recebemos." in page.locator("main").inner_text() or not [t for t in store.tasks("demo-bia") if t["kind"] == "retake"]  # (or it was already read)
    assert all(t.get("received_at") for t in store.tasks("demo-bia") if t["kind"] == "retake")  # no request is still waiting for the photo
    assert len([u for u in store.uploads("demo-bia") if u["doc_id"] == "passport"]) == 2

    # the thread with the office: the answer is new, in Portuguese, with the office's own words next to it
    badge = page.locator("#msgbtn .badge-n")
    assert badge.inner_text() == str(unseen) and unseen >= 2 and page.locator("#msgbtn").get_attribute("aria-label") == "Falar com o escritório"  # answers to read
    page.locator("#msgbtn").click()
    thread = page.locator("#layer .thread")
    thread.wait_for(state="visible")
    text = thread.inner_text()
    assert "Posso mandar a foto do passaporte amanhã?" in text and "Você" in text and "O escritório" in text
    assert "Tradução automática: pode ter erros." in text or "O escritório entrará em contato com você." in text
    assert "Palavras do escritório (em inglês)" in text and "Bring the appointment letter and a photo ID." in text
    _shot(page, "4-thread")
    page.locator("#layer textarea").fill("Muito obrigada! Vou levar a carta.")
    page.get_by_role("button", name="Enviar mensagem").click()
    page.wait_for_selector("#layer .note:not([hidden])")
    assert "Mensagem enviada." in page.locator("#layer").inner_text() and "Muito obrigada! Vou levar a carta." in page.locator("#layer .thread").inner_text()
    assert page.locator("#msgbtn .badge-n").count() == 0  # opening the thread read the answers
    assert [m["from"] for m in store.messages("demo-bia")][-1] == "client" and store.messages("demo-bia")[-1]["status"] == "new"
    page.keyboard.press("Escape")
    # in Haitian Creole the answer is English with the Creole follow-up line
    page.locator("#lang").select_option("ht")
    page.wait_for_timeout(800)
    page.locator("#msgbtn").click()
    text = page.locator("#layer .thread").inner_text()
    assert "Biwo a ap kontakte ou." in text and "Bring the appointment letter and a photo ID." in text and "Ou menm" in text
    _shot(page, "5-thread-creole")
    body = page.locator("body").inner_text()
    lines = body.splitlines()
    leaks = [lines[max(0, n - 2):n + 2] for n, line in enumerate(lines) if LEAK.search(line)]
    assert not leaks and not errors, (leaks, errors)
    wide = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    assert wide <= 1, f"the page is {wide}px wider than the phone"
    ctx.close()
