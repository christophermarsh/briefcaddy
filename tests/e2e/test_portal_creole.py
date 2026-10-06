"""The client portal in Haitian Creole (a MACHINE DRAFT for the firm's certified
translator), for a made-up client: the welcome, the "your case" card with its
appointment written in Creole words, the first sections of the questionnaire,
a date answered and written back in Creole, and the language switch -- on a
phone and on a computer. Creole sentences run longer than English ones: nothing
may scroll sideways or spill out of its button. E2E_SHOTS keeps a screenshot of each.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from conftest import LEAK, REPO


def _client(world, client: str) -> str:
    """Rose Egzanp Jean (made up), just invited in Creole, with a "your case" page and a fingerprint appointment."""
    import journey
    from portal.bank import languages
    from portal.store import PortalStore

    store = PortalStore(world["portal"])
    store.add_client(client, "Rose Egzanp Jean", email=f"{client}@example.com", language="ht")
    j = {"stage": "intake", "stage_index": 0, "today": "2026-10-02", "stages": [{"id": "intake"}, {"id": "i485_ready"}, {"id": "i485_pending"}],
         "notices": [{"kind": "biometrics", "appointment": "2099-10-20 09:00", "date": "2099-09-01", "form": "I-485"}], "filings": []}
    store.save_journey(client, {lang: journey.client_view(j, lang) for lang in languages()})
    out = subprocess.run([sys.executable, "src/portal/admin.py", "link", client], cwd=REPO, env=world["env"], capture_output=True, text=True)
    return out.stdout.split()[0]


def _look(page, name: str) -> str:
    """The page as the client sees it: no script errors, nothing leaked, nothing sideways, no word spilling out of its box."""
    page.wait_for_load_state("networkidle")  # (what the test just did has been drawn: each step waits for its own words before it looks)
    shots = os.environ.get("E2E_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(shots) / f"creole-{name}.png"), full_page=True)
    body = page.locator("body").inner_text()
    assert not [line for line in body.splitlines() if LEAK.search(line)], name
    wide = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    assert wide <= 1, f"{name}: the page is {wide}px wider than the screen"
    spilled = page.evaluate("""() => [...document.querySelectorAll('.btn, .choice, .pill, .iconbtn, .seg button, .chip, .status, .link, .ready span, .steplist button')]
        .filter((n) => n.offsetParent && n.scrollWidth > n.clientWidth + 1).map((n) => n.innerText.slice(0, 60))""")
    assert not spilled, f"{name}: text wider than its box: {spilled}"
    return body


@pytest.mark.parametrize("width, height", [(390, 844), (1280, 900)], ids=["phone", "computer"])
def test_a_client_answers_in_haitian_creole(world, browser, width, height):
    link = _client(world, f"pilot-rose-{width}")  # one made-up client per screen size: each starts from the welcome
    ctx = browser.new_context(viewport={"width": width, "height": height}, locale="en-US")  # a phone set to English: the portal still speaks Creole
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(link)
    page.wait_for_load_state("networkidle")
    body = _look(page, f"{width}-welcome")
    assert "Bonjou, Rose!" in body and "Ann kòmanse" in body and "Dosye ou" in body
    weekday = ["lendi", "madi", "mèkredi", "jedi", "vandredi", "samdi", "dimanch"][date(2099, 10, 20).weekday()]
    assert f"{weekday} 20 oktòb 2099" in body  # the appointment, in Creole words (browsers have no Creole calendar)
    assert page.evaluate("document.documentElement.lang") == "ht"
    assert page.locator("#lang").input_value() == "ht" and "Kreyòl ayisyen" in page.locator("#lang").inner_text()
    assert page.evaluate("() => Object.keys(UI.en).filter((k) => !(k in UI.ht))") == []  # every screen word has its Creole

    page.get_by_role("button", name="Ann kòmanse").click()
    page.locator("main", has_text="Enfòmasyon sou ou").wait_for()
    body = _look(page, f"{width}-1-about")
    assert "Enfòmasyon sou ou" in body and "Premye non ak dezyèm non" in body
    assert width >= 900 or "Etap 1 sou" in body  # the step counter is on the phone; a computer has the list of steps beside the page
    page.locator("#q_dob").fill("2001-10-05")
    page.locator("#q_dob").dispatch_event("change")
    page.locator("#q_dob").locator("xpath=..", has_text="5 oktòb 2001").wait_for()  # the date written back in Creole
    page.get_by_role("radio", name="Fi", exact=True).click()
    page.wait_for_function("() => [...document.querySelectorAll('[role=radio]')].some((r) => r.getAttribute('aria-checked') === 'true')")  # the answer is kept and the page drawn again
    assert page.get_by_role("radio", name="Fi", exact=True).get_attribute("aria-checked") == "true"
    page.locator("summary", has_text="Ou pa sèten?").first.click()
    assert page.locator("details.tip[open] p").first.is_visible()
    _look(page, f"{width}-1-about-answered")

    for n, title in ((2, "Adrès"), (3, "Travay ak lekòl"), (4, "Istwa imigrasyon")):
        page.get_by_role("button", name="Kontinye").click()
        page.locator("main", has_text=title).first.wait_for()
        body = _look(page, f"{width}-{n}-section")
        assert title in body and (width >= 900 or f"Etap {n} sou" in body), body[:400]

    page.locator("#lang").select_option("en")  # the client switches language: everything follows, then back
    page.locator("main", has_text="Immigration history").wait_for()
    page.locator("#lang").select_option("ht")
    page.locator("main", has_text="Istwa imigrasyon").wait_for()
    if width < 900:  # on a phone the steps open as a sheet
        page.locator(".pill").click()
        page.locator("body", has_text="Etap yo").wait_for()
        assert "Etap yo" in _look(page, f"{width}-steps")
    assert not errors, errors
    ctx.close()
