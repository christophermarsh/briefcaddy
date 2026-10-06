"""The client's case, in their words, in the browser (src/client_case.py, src/client_reminders.py): the client's page on a phone in Portuguese shows USCIS's own
status line in English with the sentence in Portuguese that says so, what happens next, and a preparation sheet that downloads as a PDF; a reminder for an
appointment waits in the outbox in Portuguese; one tap of "how was this step" is kept on the case and counted in the firm's Reports, sentence and all.
Everyone here is made up. E2E_SHOTS keeps a screenshot of each step."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

CASE = "case-i3"
NAME = "Clara Exemplo Teste"
RECEIPT = "IOE0999000555"
SENTENCE = "Foi muito rápido, obrigada."


def _shot(page, name: str) -> None:
    shots = os.environ.get("E2E_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(shots) / f"client-case-{name}.png"), full_page=True)


def _notice(w, d, kind: str, when: date, appointment: date, time: str, where: str, bring: str | None = None) -> None:
    """An appointment notice as the notice reader records it, with the appointment, its place and (when the notice has one) its own list of what to bring."""
    w.add_notice(d, RECEIPT, "I-485", kind, when.isoformat())
    doc, slug = f"notice-i-485-{kind}-{when.isoformat()}.pdf", f"{kind}_{when.isoformat().replace('-', '')}"
    w.add_fact(d, f"folder.notice.{RECEIPT}.{slug}.date", when.isoformat(), doc, "uscis_notice")
    w.add_fact(d, f"folder.notice.{RECEIPT}.{slug}.appointment", f"{appointment.isoformat()} {time}", doc, "uscis_notice")
    w.add_fact(d, f"folder.notice.{RECEIPT}.{slug}.where", where, doc, "uscis_notice")
    if bring:
        w.add_fact(d, f"folder.notice.{RECEIPT}.{slug}.bring", bring, doc, "uscis_notice")


@pytest.fixture(scope="module")
def client(world):
    """A case cloned from the demo client, in the portal in Portuguese: a fingerprint appointment ten days ago (a step done), another in a week and an
    interview tomorrow (the reminders), and USCIS's status line saved for its receipt, with the firm's API keys on this machine."""
    import case_status
    import clock
    import journey
    from portal.store import PortalStore

    w = world["world"]
    d = w.clone(world["root"], "demo-ana", CASE)
    today = date.today()
    w.add_notice(d, RECEIPT, "I-485", "receipt", (today - timedelta(days=40)).isoformat())
    _notice(w, d, "biometrics", today - timedelta(days=30), today - timedelta(days=10), "10:00 AM", "USCIS ASC, 1 Example Way, Boston, MA 02110")
    _notice(w, d, "biometrics", today - timedelta(days=3), today + timedelta(days=7), "09:30 AM", "USCIS ASC, 1 Example Way, Boston, MA 02110",
            "This appointment notice\nA valid photo ID")
    _notice(w, d, "interview", today - timedelta(days=2), today + timedelta(days=1), "11:00 AM", "USCIS Field Office, 2 Example Plaza, Boston, MA 02110")
    # a person checked the interview's address against the paper; the fingerprint appointment's address nobody checked
    journey.mark(d, "place_confirm", "Paulo Paralegal", f"{RECEIPT}.interview.{(today - timedelta(days=2)).isoformat()}", "USCIS Field Office, 2 Example Plaza, Boston, MA 02110")
    case_status.save(d, RECEIPT, case_status.record_of(
        {"receiptNumber": RECEIPT, "formType": "I-485", "submittedDate": "09-05-2026 14:28:46", "modifiedDate": "09-05-2026 14:28:46",
         "current_case_status_text_en": "Case Was Received and A Receipt Notice Was Sent",
         "current_case_status_desc_en": "<p>On September 5, 2026, we received your Form I-485. We sent you a receipt notice.</p>", "hist_case_status": []},
        datetime.now(timezone.utc), "production"))
    store = PortalStore(world["portal"])
    store.add_client(CASE, NAME, email="clara@example.com", phone="+1 555 010 0199", language="pt", consent={"email": True, "sms": False, "whatsapp": False})
    mp = pytest.MonkeyPatch()
    mp.setenv("USCIS_CASE_STATUS_CLIENT_ID", "test-id")  # the firm has the keys: nothing is shown without them
    mp.setenv("USCIS_CASE_STATUS_CLIENT_SECRET", "test-secret")
    try:
        import client_case

        client_case._keys["at"] = 0.0
        journey.push_client(world["clients"], world["portal"], CASE, notify=False, today=clock.today(), strict=True)
    finally:
        mp.undo()
    return store


def test_the_clients_phone_in_portuguese_shows_uscis_words_what_happens_next_and_a_sheet_to_keep(world, browser, client):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="pt-BR", has_touch=True)
    phone = ctx.new_page()
    errors = []
    phone.on("pageerror", lambda e: errors.append(str(e)))
    phone.goto(f"{world['portal_url']}/l/{client.new_link_token(CASE)}")
    phone.wait_for_load_state("networkidle")
    case = phone.locator(".card.case")
    case.wait_for()
    text = case.inner_text()
    assert "O que o USCIS diz sobre o seu caso" in text and "Case Was Received and A Receipt Notice Was Sent" in text
    assert "Isto é o que o USCIS diz, em inglês" in text and "O USCIS disse em" in text
    assert "On September 5, 2026, we received your Form I-485" in text  # USCIS's own sentence, in its own English
    assert "O que acontece depois" in text and "coleta de digitais" in text  # the paragraph for the stage, in Portuguese
    _shot(phone, "1-page")
    # the preparation sheet: the page for the nearest appointment opens with the day, what to bring and the notice's own list
    case.get_by_role("button", name="O que levar e onde").first.click()
    layer = phone.locator("#layer .panel")
    layer.wait_for(state="visible")
    sheet = layer.inner_text()
    assert "como vai ser o dia" in sheet.lower() and "Baixar em PDF" in sheet and "Feita com as páginas do próprio USCIS (lidas em 3 de outubro de 2026) e com os conselhos do próprio escritório" in sheet
    _shot(phone, "2-sheet")
    href = layer.locator("#sheet-pdf").get_attribute("href")
    first = ctx.request.get(world["portal_url"] + href)  # the phone downloads it with its own session
    assert first.status == 200 and first.headers["content-type"] == "application/pdf" and first.body().startswith(b"%PDF")
    assert ctx.request.get(world["portal_url"] + "/api/sheet/99").status == 404
    assert not errors
    ctx.close()


def test_a_reminder_for_the_appointment_waits_in_the_outbox_in_portuguese(world, client):
    import client_reminders

    line = client_reminders.nightly(world["clients"], world["root"], date.today())
    assert "waiting in the outbox (no provider)" in line
    rows = [json.loads(x) for x in (world["portal"] / "outbox.jsonl").read_text(encoding="utf-8").splitlines()]
    mine = [r for r in rows if r["to"] == "clara@example.com" and ("compromisso" in r["subject"])]
    assert len(mine) == 2  # the week before the fingerprint appointment, the day before the interview (an e-mail each: no text consent)
    day = next(r for r in mine if "amanhã" in r["subject"])
    assert day["body"].startswith("Lembrete do escritório") and "a sua entrevista amanhã" in day["body"] and "às 11:00" in day["body"]
    assert "Local: USCIS Field Office, 2 Example Plaza, Boston, MA 02110. Confira este endereço na sua carta." in day["body"] and "Leve a sua carta e o seu passaporte." in day["body"]
    week = next(r for r in mine if "amanhã" not in r["subject"])
    assert "O endereço está na sua carta. Confira lá." in week["body"] and "Example Way" not in week["body"]  # an address nobody checked is not sent
    assert client_reminders.nightly(world["clients"], world["root"], date.today()) == "Client reminders: nothing to send."  # not twice


def test_the_list_from_the_notice_reaches_the_phone_only_after_staff_confirm_it(world, browser, client, paralegal):
    """The notice reader's list of what to bring is held back until a person checks it against the paper on the case page: then the phone's sheet quotes it."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="pt-BR", has_touch=True)
    phone = ctx.new_page()
    phone.goto(f"{world['portal_url']}/l/{client.new_link_token(CASE)}")
    case = phone.locator(".card.case")
    case.wait_for()
    case.get_by_role("button", name="O que levar e onde").nth(1).click()  # the fingerprint appointment in a week
    layer = phone.locator("#layer .panel")
    layer.wait_for(state="visible")
    assert "Leve o que a sua carta pedir." in layer.inner_text() and "This appointment notice" not in layer.inner_text()  # read, not confirmed: no list
    _shot(phone, "4-before-confirming")
    page = paralegal.page
    paralegal.open(CASE, "journey")
    panel = page.locator("#notice-reads")
    panel.wait_for()
    assert "This is what the notice says?" in panel.inner_text() and "This appointment notice" in panel.inner_text()
    paralegal.check("notice-reads")
    panel.locator(".callout", has_text="List of what to bring").get_by_role("button", name="Confirm").last.click()  # the list is below the address
    paralegal.toast()
    paralegal.settle()
    assert "checked" in page.locator("#notice-reads").inner_text()
    phone.reload()
    phone.locator(".card.case").wait_for()
    phone.locator(".card.case").get_by_role("button", name="O que levar e onde").nth(1).click()
    sheet = phone.locator("#layer .panel").inner_text()
    assert "This appointment notice" in sheet and "A valid photo ID" in sheet and "a sua carta lista estas coisas" in sheet.lower()
    _shot(phone, "5-after-confirming")
    ctx.close()


def test_one_tap_of_feedback_on_the_phone_and_the_firms_reports(world, browser, client, attorney):
    import client_case

    ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="pt-BR", has_touch=True)
    phone = ctx.new_page()
    phone.goto(f"{world['portal_url']}/l/{client.new_link_token(CASE)}")
    card = phone.locator("#feedback")
    card.wait_for()
    assert "Como foi este passo?" in card.inner_text() and "Só o escritório vê isto." in card.inner_text()
    _shot(phone, "3-feedback")
    card.locator("[data-face=good]").click()
    card.locator("textarea").fill(SENTENCE)
    card.get_by_role("button", name="Enviar").click()
    phone.locator("#feedback-thanks").wait_for()
    assert phone.locator("#feedback-thanks").inner_text() == "Obrigado. O escritório vai ler isto."
    given = client.feedback(CASE)
    assert given[0]["face"] == "good" and given[0]["comment"] == SENTENCE and given[0]["language"] == "pt"
    ctx.close()
    step = client_case.STEP_NAMES[given[0]["kind"]]
    # the firm's Reports: counts per step and per office, and the sentence for the people who may open the case
    page = attorney.page
    page.goto("about:blank")
    page.goto(world["review"] + "#all")
    page.wait_for_selector("#client-rows")
    page.get_by_role("button", name="Reports").click()
    page.wait_for_selector("#report-client_feedback")
    attorney.settle(300)
    counts = page.locator("#report-client_feedback").inner_text()
    assert step in counts and "Every office" in counts and "not good" in counts.lower()
    assert SENTENCE not in counts  # a count holds no sentence and no case
    words = page.locator("#report-client_feedback_words").inner_text()
    assert SENTENCE in words and "Good" in words and step in words
    assert "Reminders sent to clients" in page.locator("#main").inner_text() and "The week before" in page.locator("#report-client_reminders").inner_text()
    attorney.check("reports-client-feedback")
    assert (world["clients"] / CASE / client_case.FEEDBACK).exists()  # on the case
    # My work: nothing waits a week for this client
    page.goto("about:blank")
    page.goto(world["review"] + "#all")
    page.wait_for_selector("#client-rows")
    page.get_by_role("button", name="My work").click()
    page.wait_for_selector("#inbox-bar")
    attorney.settle(300)
    assert "Answers waiting a week" not in page.locator("#main").inner_text()
