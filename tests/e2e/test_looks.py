"""Every main screen in both appearances (light and dark): no page errors, no
leaked null/undefined, nothing scrolling sideways, form controls readable (a
dropdown's text and background differ) -- and a screenshot of each when
E2E_SHOTS is set, for the visual review. Also: the appearance switch is
remembered, the Settings page saves, questions for the client wait in one
list, and family members' cases link both ways.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from conftest import Screen


def _contrast_ok(page) -> list[str]:
    """Every visible <select>: its text color differs from its background (the old dark mode showed light text on white)."""
    return page.evaluate("""() => [...document.querySelectorAll('select')].filter((s) => s.offsetParent).map((s) => {
        const cs = getComputedStyle(s); return cs.color === cs.backgroundColor ? (s.getAttribute('aria-label') || s.id || 'select') : null; }).filter(Boolean)""")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_main_screens_in_both_appearances(world, browser, scheme):
    import world as w

    s = Screen(browser, world, w.ATTORNEY, color_scheme=scheme)
    try:
        s.page.goto(world["review"])
        s.settle()
        s.check(f"look-{scheme}-all-clients")
        for tab in ("journey", "documents", "fix", "check", "attorney", "packet"):
            s.open("demo-ana", tab)
            body = s.check(f"look-{scheme}-demo-{tab}")
            assert "applicant." not in body and " -- " not in body, (tab, re.findall(r".{40}(?:applicant\.| -- ).{40}", body)[:3])
            assert not _contrast_ok(s.page), (tab, _contrast_ok(s.page))
        s.page.get_by_role("button", name=re.compile("Settings")).first.click()
        s.page.wait_for_selector("#set-policies")  # the page's last section: settings, then the firm's policies, are read one after the other
        s.settle()
        body = s.check(f"look-{scheme}-settings")
        assert "Visa Bulletin: EB-4" in body and "schemas/" not in body
    finally:
        s.close()


def test_the_appearance_switch_is_remembered(world, attorney):
    attorney.page.get_by_role("button", name="Dark").click()
    assert attorney.page.evaluate("document.documentElement.dataset.theme") == "dark"
    attorney.page.reload()
    attorney.page.wait_for_selector("#client", state="visible")
    assert attorney.page.evaluate("document.documentElement.dataset.theme") == "dark"
    attorney.page.get_by_role("button", name="Match this computer").click()
    assert attorney.page.evaluate("document.documentElement.dataset.theme") is None


def test_settings_save_with_who_and_when(world, attorney, paralegal):
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#settings:visa_bulletin_eb4")
    attorney.settle()
    sec = attorney.page.locator("#set-visa_bulletin_eb4")
    sec.locator("select").first.select_option(index=2)                                  # this month
    sec.locator("label", has_text="Mexico").locator("input[type=checkbox]").check()     # Current
    sec.get_by_role("button", name="Save").click()
    assert attorney.toast() == "Saved."
    attorney.settle()
    assert "Last changed by Ana Attorney" in attorney.page.locator("#set-visa_bulletin_eb4").inner_text()
    paralegal.page.goto("about:blank")
    paralegal.page.goto(world["review"] + "#settings")
    paralegal.settle()
    assert "Only an attorney can change these" in paralegal.check("settings-paralegal")
    assert paralegal.page.locator("#set-firm").get_by_role("button", name="Save").is_disabled()


def test_questions_wait_in_one_list_and_family_links(world, paralegal):
    paralegal.open("demo-ana", "fix")
    asks = paralegal.page.get_by_role("button", name="Ask the client")
    if asks.count() >= 2:
        for i in range(2):
            paralegal.page.get_by_role("button", name="Ask the client").nth(0).click()
            paralegal.page.get_by_role("button", name="Add to the client's list").click()
            paralegal.toast()
            paralegal.settle()
        tray = paralegal.page.locator("section.tray")
        assert "2 waiting" in tray.inner_text()
        tray.get_by_role("button", name=re.compile("Send all 2")).click()
        assert "Queued, no mail server configured" in paralegal.toast()  # the made-up world has no mail server: never "Sent"
    paralegal.open("case-sij", "journey")
    paralegal.page.select_option("select[aria-label=\"Family member's case\"]", "case-family")
    paralegal.page.select_option("select[aria-label='Relationship']", "Sibling")
    paralegal.page.get_by_role("button", name="Link", exact=True).click()
    paralegal.toast()
    paralegal.open("case-family", "journey")
    assert "case-sij" in paralegal.check("family-linked") and "Sibling" in paralegal.page.locator("section", has_text="Family members").inner_text()


def test_cards_say_the_question_and_the_answer(world, attorney):
    """The user's screenshots (10/2026): an alert titled by a code, a follow-up with "nothing covers this", a broken Open link."""
    attorney.open("demo-ana", "attorney")
    body = attorney.check("cards-attorney")
    assert "CLIENT NOT SURE" not in body and "Part 9 follow-up question for the attorney" not in body and "Tier 3" not in body
    unsure = attorney.page.locator("article, .card", has_text="What the client answered").first
    assert "I'm not sure" in unsure.inner_text() and "deport" in unsure.inner_text()
    follow = attorney.page.locator("article, .card", has_text="Why this is asked").first
    assert "Item 74" in follow.inner_text() and "Nothing in the client's documents" not in follow.inner_text()
    attorney.open("demo-ana", "check")
    link = attorney.page.get_by_role("link", name=re.compile("Open the answers")).first
    page = attorney.page.context.request.get(world["review"].rstrip("/") + link.get_attribute("href").split("#")[0])
    assert page.status == 200 and "answers from the client portal" in page.text() and "unknown document" not in page.text()


def test_a_client_still_answering_shows_what_they_saved(world, paralegal):
    """The dashboard row of a client who hasn't submitted: how far along, and their answers so far."""
    from portal.store import PortalStore

    portal = PortalStore(world["portal"])
    portal.add_client("pilot-so-far", "Ana Teste", email="ana.teste@example.com")
    portal.save_answers("pilot-so-far", {"given_name": "Ana", "family_name": "Teste", "used_other_names": "No"})
    paralegal.page.goto("about:blank")
    paralegal.page.goto(world["review"] + "#all")
    paralegal.settle()
    ids = [r["id"] for r in paralegal.page.context.request.get(world["review"] + "api/overview").json()["clients"]]
    paralegal.check("answers-so-far-dashboard")
    assert "pilot-so-far" in ids, ids
    row = paralegal.page.locator("tr.row", has_text="pilot-so-far")
    assert re.search(r"3 of \d+ answered", row.inner_text())
    href = row.get_by_role("link", name="Answers so far").get_attribute("href")
    page = paralegal.page.context.request.get(world["review"].rstrip("/") + href)
    assert page.status == 200 and "Not submitted yet" in page.text() and "Teste" in page.text()


def test_every_case_sign_off_tab_reads_cleanly(world, attorney):
    """Every legal alert on every made-up case: no "null" (an alert has no answer to trace), and the documents it is about open."""
    ids = [r["id"] for r in attorney.page.context.request.get(world["review"] + "api/overview").json()["clients"] if r.get("processed_at")]
    assert "case-court" in ids
    for cid in ids:
        attorney.open(cid, "attorney")
        attorney.check(f"sign-off-{cid}")
    attorney.open("case-court", "attorney")
    nta = attorney.page.locator("article.card", has_text="Removal proceedings").first
    assert "Notice to Appear" in nta.inner_text() and nta.get_by_role("link", name=re.compile("Open")).count()


def test_the_sign_off_tab_writes_years_as_years(world, attorney):
    """The buyer's walkthrough: "Since April 1, 19 97" on the legal screen (the I-485's own tooltip has a typesetting gap)."""
    attorney.open("demo-ana", "attorney")
    body = attorney.check("sign-off-year")
    assert "19 97" not in body and "D H S" not in body
    assert "1997" in body  # the Part 9 question is on this tab, spelled as a year
    attorney.open("demo-ana", "check")
    assert "19 97" not in attorney.check("check-year")


def test_a_message_says_queued_when_nothing_is_configured(world, paralegal):
    """The made-up world has no mail server: the screen must say so, with the date, never "Sent"."""
    paralegal.open("demo-ana", "fix")
    if paralegal.page.get_by_role("button", name="Ask the client").count() == 0:
        pytest.skip("no card to ask about in this world")
    paralegal.page.get_by_role("button", name="Ask the client").nth(0).click()
    paralegal.page.get_by_role("button", name="Add to the client's list").click()
    paralegal.toast()
    paralegal.settle()
    paralegal.page.locator("section.tray").get_by_role("button", name=re.compile("Send all")).click()
    said = paralegal.toast()
    assert "Queued, no mail server configured" in said and re.search(r"\(\d\d/\d\d/\d{4}\)", said) and "Sent" not in said, said
    paralegal.settle()
    paralegal.page.locator("#requests summary").click()  # the list of what was asked opens
    log = paralegal.page.locator("#requests").inner_text()
    assert "Message: Queued, no mail server configured" in log and "Sent" not in log.replace("Sent the document", ""), log


def test_the_front_page_shows_no_red_deadline_that_passed_long_ago(world, attorney):
    """The buyer saw a 2020 asylum deadline in red on the front page: a deadline long past is grey, "passed MM/DD/YYYY"."""
    from datetime import date, datetime

    attorney.page.goto(world["review"])
    attorney.settle()
    overview = attorney.page.context.request.get(world["review"] + "api/overview").json()
    long_past = [d for r in overview["clients"] for d in r["due"] if d["level"] == "passed"]  # each row carries its own deadlines
    assert long_past, "the made-up world has a deadline from years ago (the asylum case)"
    body = attorney.check("front-page-passed")
    for d in long_past:
        iso = d["date"]
        assert f"passed {iso[5:7]}/{iso[8:10]}/{iso[:4]}" in body, d
    red = attorney.page.locator("tr.row .due.overdue, tr.row .due.urgent").all_inner_texts()
    for text in red:
        day = datetime.strptime(text.split(" · ")[0], "%m/%d/%Y").date()
        assert (date.today() - day).days <= 60, text  # nothing red on the front page is older than two months
    grey = attorney.page.locator("tr.row .due.passed")
    assert grey.count() >= 1
    colors = attorney.page.evaluate("""() => { const c = (cls) => { const e = document.createElement('span'); e.className = 'due ' + cls; document.body.append(e);
        const v = getComputedStyle(e).color; e.remove(); return v; }; return [c('passed'), c('overdue')]; }""")
    assert colors[0] != colors[1], colors  # grey, not the alarm colour

def test_a_rule_reads_in_plain_words_and_the_packet_has_a_review_bundle(world, attorney, paralegal):
    """docs/design_plan.md Part 4: the sign-off card says what a rule does, where it comes from and whether an attorney
    approved it for every case (no bare "OVERSTAY-01"); Keeping current approves it; the packet tab builds the review bundle."""
    attorney.open("demo-ana", "attorney")
    group = attorney.page.locator("section.group", has_text="OVERSTAY-01").first
    text = group.inner_text()
    assert "the overstay rule" in text and "admit-until date is earlier than the I-360's priority date" in text, text
    assert "Source: Built-in practice, taken from filed forms the product was built on (recorded 09/29/2026). It is not your firm's practice until an attorney approves it for every case." in text and "decision log" not in text, text
    assert "For every case: not yet approved." in text, text  # a stop between the two, never "For every case Not yet approved"
    assert not re.search(r"Approve \d", text) and re.search(r"Approve (all \d+ )?for this client", text), text  # a verb and whose, not a bare count
    policy = attorney.page.locator("section.group", has_text="NO-CREWMAN").first.inner_text()
    assert "Special Immigrant Juvenile clients are not crewmen" in policy and "client 1" not in attorney.check("signoff-plain-rules")

    # Keeping current: the attorney approves the rule for every case; the card then says who and when
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#all")
    attorney.settle()
    attorney.page.get_by_role("button", name=re.compile("Keeping current")).click()
    attorney.settle()
    row = attorney.page.locator("#rules-approval tr", has_text="OVERSTAY-01")
    assert "Not yet approved" in row.inner_text()
    row.get_by_role("button", name="Approve").click()
    assert attorney.toast() == "Approved."
    attorney.settle()
    assert re.search(r"Approved by Ana Attorney on \d{2}/\d{2}/\d{4}", attorney.page.locator("#rules-approval tr", has_text="OVERSTAY-01").inner_text())
    attorney.check("keeping-current-rules")
    attorney.open("demo-ana", "attorney")
    assert re.search(r"For every case: approved by Ana Attorney on \d{2}/\d{2}/\d{4}\.", attorney.page.locator("section.group", has_text="OVERSTAY-01").first.inner_text())

    # a paralegal reads the rules but can't approve them, and the accuracy record is the attorney's
    paralegal.page.goto("about:blank")
    paralegal.page.goto(world["review"] + "#all")
    paralegal.settle()
    paralegal.page.get_by_role("button", name=re.compile("Keeping current")).click()
    paralegal.settle()
    assert paralegal.page.locator("#rules-approval").get_by_role("button", name="Approve").count() == 0
    assert paralegal.page.context.request.get(world["review"] + "api/accuracy").status == 403

    # the packet tab: build the packet, then its review bundle
    attorney.open("demo-ana", "packet", "i485")
    attorney.page.get_by_role("button", name=re.compile("uild packet")).first.click()
    assert "Packet built" in attorney.toast()
    attorney.settle()
    attorney.page.get_by_role("button", name=re.compile("^Review bundle$")).click()
    assert "Review bundle built" in attorney.toast()
    attorney.settle()
    link = attorney.page.get_by_role("link", name=re.compile("Open review bundle"))
    attorney.check("packet-review-bundle")
    pdf = paralegal.page.context.request.get(world["review"].rstrip("/") + link.get_attribute("href"))  # attorney or paralegal
    assert pdf.status == 200 and pdf.body()[:5] == b"%PDF-"

    # the accuracy record, from Keeping current, with its method on the page
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#accuracy-record")
    attorney.settle()
    body = attorney.check("accuracy-record")
    assert "Accuracy record" in body and "How these numbers are counted" in body and "Nothing is estimated" in body
    export = attorney.page.get_by_role("link", name=re.compile("Export as PDF")).get_attribute("href")
    assert attorney.page.context.request.get(world["review"].rstrip("/") + export).body()[:5] == b"%PDF-"


# Implementation note.


@pytest.mark.parametrize("width,height", [(1366, 768), (1400, 900), (1920, 1080)])
def test_the_documents_tab_fits_the_screen_and_the_thread_sits_below_the_table(world, browser, width, height):
    """3.2 / 10: "Add what it shows" ran off the right edge at 1400 pixels, and the case's message thread sat above the table."""
    import world as w
    from portal.store import PortalStore

    store = PortalStore(world["portal"])
    if not [m for m in store.messages("demo-ana") if m["from"] == "client"]:
        store.add_message("demo-ana", "client", "Hello, I have a question about my passport photo.")
    s = Screen(browser, world, w.PARALEGAL)
    try:
        s.page.set_viewport_size({"width": width, "height": height})
        s.open("demo-ana", "documents")
        s.page.wait_for_selector("#documents tr + tr")
        s.check(f"third-documents-{width}")
        edge = s.page.evaluate("""() => {
            const box = document.querySelector('#documents').parentElement, c = box.getBoundingClientRect();
            const clipped = [...document.querySelectorAll('#documents select')].filter((e) => { const r = e.getBoundingClientRect(); return r.right > c.right + 1 || r.right > innerWidth; })
              .map((e) => e.getAttribute('aria-label'));
            return {clipped, inner: box.scrollWidth - box.clientWidth};
        }""")
        assert edge["clipped"] == [] and edge["inner"] <= 1, edge  # every dropdown inside the card, nothing to scroll sideways
        order = s.page.evaluate("""() => ({ table: document.querySelector('#documents').getBoundingClientRect().bottom,
            thread: document.querySelector('#messages').getBoundingClientRect().top })""")
        assert order["thread"] >= order["table"] - 1, order  # the thread comes after the documents, in its own panel
        assert "Message from the client" in s.page.locator("#messages").inner_text()
    finally:
        s.close()


def test_a_search_result_names_its_case_and_opens_the_documents_tab_at_that_document(world, attorney):
    """3.8: the results said only the client's name, and a click went to Needs attention. 3.9: the wide search box stayed over Settings."""
    attorney.page.set_viewport_size({"width": 1366, "height": 768})
    attorney.page.fill("#find-q", "passport")
    attorney.page.wait_for_function("() => document.getElementById('find-q').getBoundingClientRect().width >= 230")  # the box opens wide while you type (a transition): wait for it, not a pause
    typing =attorney.page.locator("#find-q").bounding_box()
    settings = attorney.page.locator("#settings").bounding_box()
    assert typing["x"] >= settings["x"] + settings["width"] and typing["width"] >= 230  # the floating "pill" of the buyer's screenshot is this box, wide while you type: it now opens to the right, never over Settings
    attorney.page.press("#find-q", "Enter")
    attorney.page.wait_for_selector("#search-count")
    attorney.settle(300)
    box = attorney.page.locator("#find-q").bounding_box()
    others = [attorney.page.locator(sel).bounding_box() for sel in ("#settings", "#client")]
    assert all(box["x"] >= o["x"] + o["width"] for o in others), (box, others)  # the wide box does not stay over Settings and the client list
    row = attorney.page.locator("table.hits tr", has_text="demo-ana").first
    assert "demo-ana" in row.inner_text() and "Special Immigrant Juvenile · demo-ana" in row.inner_text()
    row.get_by_role("button").first.click()
    attorney.page.wait_for_selector("#documents tr.found")
    attorney.settle(300)
    assert "tab=documents" in attorney.page.url and attorney.page.url.endswith("#demo-ana")
    found = attorney.page.locator("#documents tr.found")
    assert found.count() == 1 and found.get_attribute("id").startswith("doc-")
    where = found.bounding_box()
    assert 0 <= where["y"] <= attorney.page.viewport_size["height"]  # scrolled into view
    attorney.check("third-search-to-documents")


def test_a_saved_search_that_finds_nothing_says_how_much_it_looked_at(world, attorney):
    """3.13: an empty list looked exactly like a broken one."""
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#search")
    attorney.page.wait_for_selector("#search-count")
    attorney.page.get_by_role("button", name="Police clearances older than two years").click()
    attorney.page.wait_for_function("() => document.getElementById('search-count').innerText.includes('read in')")
    said = attorney.page.locator("#search-count").inner_text()
    assert re.fullmatch(r"No police clearance is older than two years \(\d+ documents? read in \d+ cases?, \d\d/\d\d/\d{4}\)\.", said), said
    attorney.check("third-saved-none")


def test_the_welcome_screen_says_what_the_office_asked_and_goes_straight_to_it(world, browser):
    """3.11: the office's question and the retake waited behind "Continue"; Continue went to the last step. Now each thing is a row
    with its own button, and the button opens that one thing on its own screen (tests/e2e/test_portal_safety.py has the rest)."""
    import subprocess
    import sys

    from conftest import REPO
    from portal.store import PortalStore

    store = PortalStore(world["portal"])
    store.add_request("pilot-nova", "What is your father's date of birth?", None, "Paulo Paralegal",
                      typed={"type": "date", "text_client": "Qual é a data de nascimento do seu pai?", "language": "pt",
                             "machine_translated": False, "needs_translator": False})
    link = subprocess.run([sys.executable, "src/portal/admin.py", "link", "pilot-nova"], cwd=REPO, env=world["env"], capture_output=True, text=True).stdout.split()[0]
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="pt-BR", has_touch=True)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(link)
        page.wait_for_load_state("networkidle")
        line = page.locator(".asked-line")
        assert "O escritório pediu 1 coisa a você" in line.inner_text() and "Qual é a data de nascimento do seu pai?" in line.locator(".todo-row").inner_text()
        if os.environ.get("E2E_SHOTS"):
            page.screenshot(path=str(Path(os.environ["E2E_SHOTS"]) / "third-welcome-pt.png"), full_page=True)
        line.get_by_role("button", name="Ver agora").click()
        item = page.locator(".alert .item", has_text="Qual é a data de nascimento do seu pai?")
        item.wait_for()  # drawn when the page has the questions (not a fixed pause)
        assert item.count() == 1 and item.locator("input[type=date]").count() == 1  # the question itself, with its date picker
        assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 1 and not errors
    finally:
        ctx.close()
