"""The client's side on a phone (390 pixels), the visits' leftovers: the way out and the safety line on every client's first screen, the
list of what the office asked with a button to each, "sent to the office" until the office marks it done, a message's confirmation on
screen, the stage ladder collapsed so the appointment is on the first screen, a new photo seen by the office at once (and read at once
when the portal and the review app share a machine), and every screen in each language walked for English words. Made-up clients only:
the showcase client (Beatriz, src/portal/demo.py) and two copies of the demo client. E2E_SHOTS keeps a screenshot of each step."""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest
from conftest import LEAK, REPO
from PIL import Image

sys.path.insert(0, str(REPO / "tools"))
from portal_english import english_words  # noqa: E402

PHONE = {"width": 390, "height": 844}
NAMES_ON_SCREEN = re.compile(r"Example Street|Springfield|Application Support Center|made-up place", re.I)  # the made-up appointment's place, as the notice words it


def _shot(page, name: str) -> None:
    if os.environ.get("E2E_SHOTS"):
        Path(os.environ["E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(os.environ["E2E_SHOTS"]) / f"safety-{name}.png"), full_page=True)


def _png(tmp_path: Path, name: str = "photo.png") -> str:
    out = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(out, format="PNG")
    path = tmp_path / name
    path.write_bytes(out.getvalue())
    return str(path)


def _phone(browser, world, client: str, store, lang: str = "pt-BR"):
    """A phone signed in with a fresh link, which fails the test on a page error."""
    ctx = browser.new_context(viewport=PHONE, locale=lang, has_touch=True)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{world['portal_url']}/l/{store.new_link_token(client)}")
    page.wait_for_load_state("networkidle")
    return ctx, page, errors


def _sound(page, errors) -> None:
    body = page.locator("body").inner_text()
    assert not [line for line in body.splitlines() if LEAK.search(line)], body[:600]
    assert page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 1  # no sideways scroll at 390 pixels
    assert not errors, errors


@pytest.fixture(scope="module")
def showcase(world):
    from portal import demo
    from portal.store import PortalStore

    store = PortalStore(world["portal"])
    built = demo.showcase(store, world["clients"], process=True)
    yield store, built
    demo.reset(store, world["clients"], demo.SHOWCASE_ID)


def _copy_of_ana(world, new_id: str, here: bool):
    """A client with a processed case of her own, made from the demo client: here=True when the case was made from her own uploads in
    this portal (the portal and the review app share a machine), False when it was not (the portal on another host)."""
    from portal.store import PortalStore

    w, root = world["world"], world["root"]
    store = PortalStore(world["portal"])
    store.add_client(new_id, f"{new_id} Exemplo", email=f"{new_id}@example.com", language="pt")
    store.update_profile(new_id, status="started")
    store.save_answers(new_id, store.answers("demo-ana"))
    case = w.clone(root, "demo-ana", new_id)  # the case, and a copy of the uploads in this client's own folder
    if not here:
        meta = json.loads((case / "meta.json").read_text(encoding="utf-8"))
        meta["source_folder"] = str(root / "elsewhere" / new_id)  # the case was made from folders on another machine
        (case / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    store.update_uploads(new_id, store.uploads("demo-ana"))
    return store


def _retake_task(store, client: str, doc_id: str = "passport") -> None:
    store.save_tasks(client, [{"id": "retake:e2e", "kind": "retake", "doc_id": doc_id, "upload": "e2e", "doc_en": "passport", "why_en": "blurry",
                               "asked_at": "2026-10-02T10:00:00-04:00", "text": "A foto do passaporte está fora de foco ou tremida. Pode tirar outra?",
                               "texts": {"pt": "A foto do passaporte está fora de foco ou tremida. Pode tirar outra?", "es": "La foto de su pasaporte está borrosa. ¿Puede tomar otra?",
                                         "en": "The photo of your passport is blurry. Could you take another?", "ht": "Foto paspò ou a flou. Èske ou ka pran yon lòt?"}}])


# -- the way out, the safety line, the ladder ------------------------------------------------------------------------


def test_the_way_out_the_safety_line_and_the_appointment_on_the_first_screen(world, browser, showcase):
    store, built = showcase
    ctx, page, errors = _phone(browser, world, "demo-bia", store)
    try:
        # the way out: small, top right, on the first screen
        leave = page.locator("#leave")
        box = leave.bounding_box()
        assert leave.is_visible() and box["y"] < 60 and box["x"] + box["width"] <= 390 and leave.inner_text().strip() == "Sair"
        assert leave.get_attribute("aria-label") == "Sair desta página"
        # the safety line, once, with its link; no word about why
        line = page.locator(".note.safety")
        text = line.inner_text()
        assert "Se outra pessoa usa o seu celular, você pode sair a qualquer momento e voltar com um link novo. O escritório também pode conversar com você por telefone." in text
        assert "Sair agora" in text and not re.search(r"vawa|abus|viol", page.locator("body").inner_text(), re.I)
        # the ladder shows the current stage; the appointment is on the first screen
        assert page.locator(".path li:visible").count() == 1 and page.get_by_role("button", name="Mostrar tudo").is_visible()
        button = page.get_by_role("button", name="O que levar e onde").bounding_box()
        assert button["y"] + button["height"] <= 844, button
        _shot(page, "1-welcome-phone")
        _sound(page, errors)
        page.get_by_role("button", name="Mostrar tudo").click()
        assert page.locator(".path li:visible").count() > 3 and page.get_by_role("button", name="Mostrar menos").is_visible()
        page.get_by_role("button", name="Mostrar menos").click()
        assert page.locator(".path li:visible").count() == 1
        # the way out on a screen deeper in the questionnaire, and in the sign-in screen's own header: the same button
        page.get_by_role("button", name="Continuar de onde parei").click()
        page.wait_for_timeout(500)
        assert page.locator("#leave").is_visible()
        _sound(page, errors)
    finally:
        ctx.close()


def test_leaving_replaces_the_page_and_signing_out_ends_the_session(world, browser, showcase):
    store, built = showcase
    ctx, page, errors = _phone(browser, world, "demo-bia", store)
    try:
        page.route("https://www.weather.gov/**", lambda route: route.fulfill(status=200, content_type="text/html", body="<html><body>Weather</body></html>"))
        cookie = next(c for c in ctx.cookies() if c["name"] == "portal_session")
        before = page.evaluate("history.length")
        page.locator("#leave").click()
        page.wait_for_url("https://www.weather.gov/**")
        assert page.evaluate("history.length") == before  # replaced, not added: the portal is not one back-press away
        assert "Weather" in page.locator("body").inner_text() and "Beatriz" not in page.content()
        # the session ended with it: the portal's address shows nothing to whoever holds the phone next, and the old cookie is dead
        assert ctx.request.get(f"{world['portal_url']}/api/me").status == 401
        other = browser.new_context()
        other.add_cookies([cookie])
        assert other.request.get(f"{world['portal_url']}/api/me").status == 401  # a replayed cookie
        other.close()
    finally:
        ctx.close()
    ctx, page, errors = _phone(browser, world, "demo-bia", store)
    try:
        page.get_by_role("button", name="Sair agora").click()
        page.wait_for_selector(".signin")
        assert "Você saiu. Para voltar, peça um link novo abaixo." in page.locator("main").inner_text()
        assert page.evaluate("fetch('/api/me', {credentials: 'same-origin'}).then((r) => r.status)") == 401  # the session ended on the server
        assert page.locator("#leave").is_visible()  # the way out is on the sign-in screen too
        _shot(page, "2-signed-out")
        _sound(page, errors)
    finally:
        ctx.close()


@pytest.mark.parametrize("lang", ["pt", "es", "en", "ht"])
@pytest.mark.parametrize("width", [320, 390])
def test_the_leave_button_is_a_target_a_thumb_finds_and_the_header_still_fits(world, browser, showcase, lang, width):
    store, built = showcase
    ctx, page, errors = _phone(browser, world, "demo-bia", store)
    try:
        page.set_viewport_size({"width": width, "height": 844})
        page.locator("#lang").select_option(lang)
        page.wait_for_timeout(800)
        box = page.locator("#leave").bounding_box()
        assert box["height"] >= 44 and box["width"] >= 44 and box["x"] + box["width"] <= width, box
        assert page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 1  # the Creole header too
        page.locator("#lang").select_option("pt")
        page.wait_for_timeout(600)
        assert not errors, errors
    finally:
        ctx.close()


@pytest.mark.parametrize("sheet", ["appointment", "questions", "messages"])
def test_leave_works_under_an_open_sheet(world, browser, showcase, sheet):
    store, built = showcase
    ctx, page, errors = _phone(browser, world, "demo-bia", store)
    try:
        page.route("https://www.weather.gov/**", lambda route: route.fulfill(status=200, content_type="text/html", body="<html><body>Weather</body></html>"))
        if sheet == "appointment":
            page.get_by_role("button", name="O que levar e onde").click()
            page.wait_for_selector("#layer .prep")
        elif sheet == "questions":
            page.locator("#helpbtn").click()
            page.wait_for_selector("#layer .faq")
        else:
            page.locator("#msgbtn").click()
            page.wait_for_selector("#layer .thread")
        page.locator("#leave").click()  # a real tap on the button, not a script call: the overlay must not take it
        page.wait_for_url("https://www.weather.gov/**")
        assert "Weather" in page.locator("body").inner_text()
        assert ctx.request.get(f"{world['portal_url']}/api/me").status == 401
    finally:
        ctx.close()


# -- what the office asked: a list with a button to each; "sent to the office" until it is marked done ---------------------


def test_each_thing_the_office_asked_has_its_own_button_and_the_answer_stays_sent_until_marked_done(world, browser, paralegal, tmp_path):
    from portal.store import PortalStore

    store = _copy_of_ana(world, "safe-ask", here=True)
    store.add_request("safe-ask", "What is your father's date of birth?", None, "Paulo Paralegal", facts=["applicant.father_dob"],
                      typed={"type": "date", "text_client": "Qual é a data de nascimento do seu pai?", "language": "pt", "machine_translated": False, "needs_translator": False})
    _retake_task(store, "safe-ask")
    ctx, page, errors = _phone(browser, world, "safe-ask", store)
    try:
        asked = page.locator(".asked-line")
        assert "O escritório pediu 2 coisas a você" in asked.inner_text()
        rows = asked.locator(".todo-row")
        assert rows.count() == 2 and "Qual é a data de nascimento do seu pai?" in rows.nth(0).inner_text() and "passaporte" in rows.nth(1).inner_text()
        _shot(page, "3-welcome-list")
        _sound(page, errors)
        rows.nth(0).get_by_role("button", name="Ver agora").click()  # its own place: the question, with its date picker, not step 12
        page.wait_for_timeout(400)
        item = page.locator(".alert .item")
        assert item.count() == 1 and item.locator("input[type=date]").count() == 1 and "Etapa" not in page.locator("main").inner_text()
        item.locator("input[type=date]").fill("1970-05-04")
        item.locator("input[type=date]").dispatch_event("change")
        item.get_by_role("button", name="Enviar resposta").click()
        page.wait_for_selector(".note.sent")
        sent = page.locator(".note.sent").first.inner_text()  # the welcome screen again, with the line where the box was
        assert re.search(r"Qual é a data de nascimento do seu pai\?\s+Enviado ao escritório em \d{1,2} de \w+ de \d{4}\. Eles vão ler; não há mais nada a fazer aqui\.", sent), sent
        assert "Qual é a data de nascimento do seu pai?" not in page.locator(".asked-line").inner_text()  # only the photo is still asked
        _shot(page, "4-sent")
        page.reload()
        page.wait_for_load_state("networkidle")
        assert page.locator(".note.sent").count() == 1  # still there on the next visit
        _sound(page, errors)
        # the retake: its own place, the camera; after the photo, "Thank you, we have it" (the office reads it tonight or at once)
        page.locator(".asked-line .todo-row").get_by_role("button", name="Ver agora").click()
        camera = page.locator(".alert input[type=file][capture=environment]")
        assert camera.count() == 1
    finally:
        ctx.close()

    # the office marks the answer done on the case's Requests list: the client's line goes
    paralegal.open("safe-ask", "fix")
    box = paralegal.page.locator("#requests")
    if box.locator("details").get_attribute("open") is None:
        box.locator("summary").click()
    box.get_by_role("button", name="Mark done").click()
    assert "Marked done" in paralegal.toast()
    ctx, page, errors = _phone(browser, world, "safe-ask", store)
    try:
        assert page.locator(".note.sent").count() == 0
        _sound(page, errors)
    finally:
        ctx.close()
    assert PortalStore(world["portal"]).requests("safe-ask")[0]["settled_by"] == "Paulo Paralegal"


def test_a_message_from_the_phone_is_confirmed_above_the_fold(world, browser, showcase):
    store, built = showcase
    ctx, page, errors = _phone(browser, world, "demo-bia", store)
    try:
        page.locator("#msgbtn").click()
        page.locator("#layer textarea").fill("Muito obrigada! Vou levar a carta.")
        page.get_by_role("button", name="Enviar mensagem").click()
        page.wait_for_selector("#layer .note:not([hidden])")
        note = page.locator("#layer .note:not([hidden])")
        box = note.bounding_box()
        assert "Mensagem enviada. O escritório vai responder aqui." in note.inner_text() and box["y"] + box["height"] <= 844, box  # on screen, not below the thread
        _shot(page, "5-message-sent")
        _sound(page, errors)
        # the time of each message is in Portuguese (24 hours), on the office's clock
        times = re.findall(r"· (\d{1,2}:\d{2}[^\n]*)", page.locator("#layer .thread").inner_text())
        assert times and not [t for t in times if re.search(r"AM|PM", t)], times
    finally:
        ctx.close()


# -- a new photo ------------------------------------------------------------------------------------------------------


def test_a_new_photo_is_seen_by_the_office_at_once_and_waits_for_tonight_when_the_case_is_elsewhere(world, browser, paralegal, tmp_path):
    store = _copy_of_ana(world, "safe-late", here=False)
    _retake_task(store, "safe-late")
    ctx, page, errors = _phone(browser, world, "safe-late", store)
    try:
        page.locator(".asked-line").get_by_role("button", name="Ver agora").click()
        page.locator(".alert input[type=file][capture=environment]").set_input_files(_png(tmp_path))
        page.wait_for_selector(".note.sent")
        assert "Obrigado, recebemos." in page.locator(".note.sent").inner_text() and "fora de foco" not in page.locator("main").inner_text()
        _sound(page, errors)
        _shot(page, "6-thank-you")
        page.reload()
        page.wait_for_load_state("networkidle")
        assert "Obrigado, recebemos." in page.locator(".note.sent").inner_text()  # until the photo is read
    finally:
        ctx.close()
    # the office: My work and the Documents tab, at once
    paralegal.page.goto("about:blank")
    paralegal.page.goto(f"{paralegal.world['review']}#work")
    paralegal.settle(1500)
    body = paralegal.check("safety-my-work")
    assert "photos that arrived: " in body.lower() and "New from the client: passport (retake), not read yet. It will be read tonight." in body
    paralegal.open("safe-late", "documents")
    body = paralegal.check("safety-documents-tonight")
    assert re.search(r"New from the client, \d\d/\d\d/\d{4}\s*:\s*passport \(retake\), not read yet\. It will be read tonight\.", body), body[:800]


def test_a_new_photo_is_read_at_once_when_the_case_is_on_this_machine(world, browser, paralegal, tmp_path):
    from portal import demo
    from portal.store import PortalStore

    store = _copy_of_ana(world, "safe-now", here=True)
    _retake_task(store, "safe-now")
    ctx, page, errors = _phone(browser, world, "safe-now", store)
    try:
        page.locator(".asked-line").get_by_role("button", name="Ver agora").click()
        scan = tmp_path / "passport-again.pdf"
        scan.write_bytes(demo.document_pdf(demo.DOCUMENTS["passport"][1] + ["EXEMPLO: SECOND PHOTO"]))  # a readable passport page, other bytes
        page.locator(".alert input[type=file]:not([capture])").set_input_files(str(scan))
        page.wait_for_selector(".note.sent")
        _sound(page, errors)
        end = time.time() + 120  # the portal reads it in the background, on the same path a staff upload takes
        while time.time() < end and any(u["status"] == "received" for u in PortalStore(world["portal"]).uploads("safe-now")):
            time.sleep(1)
        ups = PortalStore(world["portal"]).uploads("safe-now")
        assert not any(u["status"] == "received" for u in ups), "the new photo was not read in two minutes"
        page.reload()
        page.wait_for_load_state("networkidle")
        # read: "Thank you, we have it" is gone, and nothing is asked again: the new page is a readable passport
        assert "Obrigado, recebemos." not in page.locator("main").inner_text() and page.locator(".asked-line").count() == 0
    finally:
        ctx.close()
    paralegal.open("safe-now", "documents")
    body = paralegal.check("safety-documents-read")
    assert "not read yet" not in body and "New from the client" not in body  # nothing waiting: the reader has run
    assert len(re.findall(r"\bPassport\b", body)) >= 2  # the new page is a record of its own beside the first


# -- every screen, in each language, for English words ---------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["pt", "es", "ht"])
def test_every_screen_is_in_the_clients_language(world, browser, showcase, lang):
    store, built = showcase
    ctx, page, errors = _phone(browser, world, "demo-bia", store)
    seen: dict[str, str] = {}
    try:
        page.locator("#lang").select_option(lang)
        page.wait_for_timeout(1000)

        def read(name: str) -> None:
            seen[name] = page.locator("body").inner_text()

        read("welcome")
        thread_free = "() => { const l = document.querySelector('#layer'); const t = l.querySelector('.thread'); return l.innerText.replace(t ? t.innerText : '', ''); }"
        page.get_by_role("button", name=re.compile("^(Continuar|Seguir|Kontinye)")).first.click()
        page.wait_for_timeout(500)
        for i in range(page.evaluate("steps().length")):
            page.evaluate(f"go({i})")
            page.wait_for_timeout(250)
            read(f"step {i + 1}")
        page.evaluate("S.step = null; S.welcomed = false; render()")
        page.locator(".appt .btn, .nextappt .btn").first.click()  # the appointment page
        page.wait_for_selector("#layer .prep")
        read("appointment page")
        page.keyboard.press("Escape")
        page.locator("#helpbtn").click()
        page.wait_for_selector("#layer .faq")
        read("questions")
        page.keyboard.press("Escape")
        page.locator("#msgbtn").click()
        page.wait_for_selector("#layer .thread")
        seen["messages"] = page.evaluate(thread_free)  # the office's own words in the thread are English on purpose; the screen around them is not
        _shot(page, f"7-{lang}")
        page.keyboard.press("Escape")
        _sound(page, errors)
        page.locator("#lang").select_option("pt")  # the showcase client reads Portuguese again for the next test
        page.wait_for_timeout(800)
    finally:
        ctx.close()
    found = {}
    for name, text in seen.items():
        for line in text.splitlines():
            if NAMES_ON_SCREEN.search(line) or "Exemplo" in line or line.startswith("•"):  # the made-up place and person; a file name the client chose
                continue
            if re.search(r"inglês|inglés|an angle", line):  # "the office's own words (in English)" and the English that follows: shown next to the draft on purpose
                continue
            if words := english_words(line):
                found[f"{name}: {line[:80]}"] = words
    assert not found, "\n".join(f"{k} -> {v}" for k, v in list(found.items())[:30])
    for name, text in seen.items():
        assert not re.search(r"\b(AM|PM)\b", text) or lang == "en", f"{name}: a 12-hour time on a {lang} screen"


# -- "send me a link" refused (brief J2, verification R3) ---------------------------------------------------------------------


@pytest.mark.parametrize("lang,busy,done", [("pt-BR", "Muitos pedidos desta rede agora", "Pronto!"), ("en-US", "Too many requests from this network", "Done!")])
def test_send_me_a_link_says_too_many_requests_never_done_when_the_server_refuses(world, browser, lang, busy, done):
    """A network that went over its allowance (429) is told so in the client's language, never "Done!". The refusal is the server's own answer,
    replayed here so the world's portal keeps its allowance for the other tests (tests/test_rate_limits.py makes the server give it)."""
    ctx = browser.new_context(viewport=PHONE, locale=lang)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.route("**/api/link", lambda r: r.fulfill(status=429, content_type="application/json", body='{"detail": "too many attempts"}'))
        page.goto(world["portal_url"] + "/")
        page.locator("input[autocomplete=email]").fill("someone@example.com")
        page.locator(".card.signin button.big").click()
        note = page.locator(".card.signin p.note")
        note.wait_for(state="visible")
        assert busy in note.inner_text() and done not in note.inner_text()
        _shot(page, f"link-busy-{lang}")
        page.unroute("**/api/link")
        page.route("**/api/link", lambda r: r.fulfill(status=200, content_type="application/json", body='{"ok": true}'))
        page.locator(".card.signin button.big").click()
        page.wait_for_function("(d) => document.querySelector('.card.signin p.note').textContent.startsWith(d)", arg=done)
        assert not errors, errors
    finally:
        ctx.close()
