"""The small things the buyer's four visits still listed (docs/research/buyer_walkthrough*.md), walked on the screen the way he did:
the sign-in page's bar, "Choose a client", a search result's case, an empty Expiring documents list, the client list (the link beside its
row, the invitation menu, not-invited-yet, the form's labels), the questionnaire in a row's menu, a draft packet's sentence, and the
family page's person who is not a client.

The made-up world's cases get a document record each (the shape src/documents.py writes) for the search part."""

from __future__ import annotations

import json
import re
import time

import world as w
from conftest import after_password, remembered, wait_until_searchable

BAKERY = "Notice to Appear. Immigration Court, Boston. The respondent worked at Example Bakery and entered without inspection."


def _record(doc_id, type_, file, text, **kw):
    return {"id": doc_id, "files": [file], "pages": [1], "type": type_, "confidence": 0.9, "person": "applicant", "person_set_by": None,
            "language": "en", "issued": None, "expires": None, "identifiers": {"a_number": "", "receipt": "", "passport": "", "ssn_last4": ""},
            "quality": "readable", "hash": doc_id * 4, "source": "scan inbox", "added": "2026-09-30T10:00:00+00:00", "roles": [], "tags": [],
            "confidential": None, "text": text, "translated": None} | kw


RESTRICTED = "Declaration of the applicant: the abuse began in 2024 and the police were called twice."


def _seed(world, screen=None):
    """The same two cases' documents tests/e2e/test_search.py seeds: the session's search index is built once, so every file that searches seeds alike."""
    for case, docs in (("case-court", [_record("n1", "nta", "nta.pdf", BAKERY)]),
                       ("case-sij", [_record("s1", "affidavit", "sij-order.pdf", RESTRICTED, confidential="1367")])):
        (world["clients"] / case / "documents.json").write_text(json.dumps({"version": 1, "built": "2026-10-01T02:00:00+00:00", "documents": docs}), encoding="utf-8")
    if screen is not None:  # the index looks for new records at most once a minute: wait until the seeded ones are found
        wait_until_searchable(world, screen, "bakery")


def _fits(s, row, where):
    """No sideways page scroll, and every item of the row's open menu and of the link panel (if shown) inside the window."""
    wide = s.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    assert wide <= 1, f"{where}: the page scrolls sideways by {wide}px"
    width = s.page.evaluate("() => window.innerWidth")
    items = [row.locator("details.rowmenu summary"), *row.locator("details.rowmenu .acts2 > *").all()]
    if s.page.locator("#link-panel").count():
        panel = s.page.locator("#link-panel")
        items += [panel, panel.get_by_role("button", name="Copy"), panel.get_by_role("button", name="Close"), panel.get_by_label("The client's sign-in link")]
    for item in items:
        box = item.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, (where, box, width)


def _fits_once_laid_out(s, row, where, seconds: float = 15):
    """_fits, asked again until it holds: after a window resize the page lays itself out in steps (a fixed pause was the old wait for that); fails with _fits' own message if it never does."""
    end = time.time() + seconds
    while True:
        try:
            return _fits(s, row, where)
        except AssertionError:
            if time.time() > end:
                raise
            s.page.wait_for_timeout(100)


def _list(s, world):
    s.page.goto("about:blank")
    s.page.goto(world["review"] + "#all")
    s.page.wait_for_selector("#client-rows")
    s.settle(300)


# -- 2. the signed-out page ----------------------------------------------------------------------------------------------------


def test_the_sign_in_page_has_no_buttons_for_someone_who_is_not_signed_in(browser, world):
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    remembered(ctx, world, w.ATTORNEY)  # the attorney's own computer: no code asked (review/auth.py)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(world["review"])
    page.wait_for_selector("input[name=email]")
    for sel in ("#settings", "#all", "#client", "#find"):
        assert page.locator(sel).is_hidden(), sel  # a visitor has no use for Settings, the client list or Search
    assert "Settings" not in page.locator(".topbar").inner_text()
    page.fill("input[name=email]", w.ATTORNEY[0])
    page.fill("input[name=password]", w.ATTORNEY[2])
    page.get_by_role("button", name="Sign in").click()
    after_password(page, ctx, world, w.ATTORNEY)
    assert page.locator("#settings").is_visible() and page.locator("#find").is_visible()
    page.wait_for_selector("text=Where each client stands")  # the work list drawn: a list that arrives after Sign out (a slow machine) would be drawn over the sign-in screen
    page.get_by_role("button", name="Sign out").click()  # and the bar empties again when they leave
    page.wait_for_selector("input[name=email]")
    assert page.locator("#settings").is_hidden() and page.locator("#client").is_hidden()
    assert not errors, errors
    ctx.close()


# -- 3. Choose a client ----------------------------------------------------------------------------------------------------------


def test_choose_a_client_shows_the_person_the_kind_of_case_and_finds_as_you_type(world, attorney):
    s = attorney
    _list(s, world)
    options = s.page.locator("#client option").all_inner_texts()
    asylum = next(o for o in options if o.endswith("case-asylum") or "case-asylum" in o)
    assert re.match(r"Ana Clara Exemplo Souza · Asylum · case-asylum( · \d+ blocking)? · restricted$", asylum), asylum  # the person first, the id after the kind
    assert not any(re.fullmatch(r"case-[a-z]+", o) for o in options if o), "an option is still just an id"

    s.page.locator("#client").click()  # the list opens under the box, with a search field
    pick = s.page.locator("#client-pick")
    pick.wait_for()
    assert pick.locator("input").evaluate("e => e === document.activeElement")
    assert pick.locator(".pick-item").count() >= 10
    first = pick.locator(".pick-item").first.inner_text()
    assert "Ana Clara Exemplo Souza" in first and "case-" in first
    s.check("polish_picker_open")
    pick.locator("input").fill("asylum")  # a kind of case and an id both find it
    shown = pick.locator(".pick-item").all_inner_texts()  # every asylum case of this worker's world (another file may have made its own): all of them, and ours among them
    assert shown and all("asylum" in t.lower() for t in shown) and sum("case-asylum" in t for t in shown) == 1, shown
    item = pick.locator(".pick-item", has_text="case-asylum").first
    assert "Ana Clara Exemplo Souza" in item.inner_text() and "Asylum" in item.inner_text() and "case-asylum" in item.inner_text()
    assert item.locator(".badge", has_text="Restricted").count() == 1  # marked the way it is everywhere else
    pick.locator("input").fill("zzzz")
    assert pick.locator(".pick-item").count() == 0 and "No client matches" in pick.inner_text()
    pick.locator("input").fill("sij")
    pick.locator("input").press("Enter")  # the keyboard picks the first match
    s.page.wait_for_function("() => location.hash === '#case-sij'")
    s.settle()
    assert s.page.locator("#client").input_value() == "case-sij" and s.page.locator("#client-pick").count() == 0
    s.page.locator("#client").focus()  # and the keyboard opens it
    s.page.keyboard.press("Enter")
    s.page.locator("#client-pick").wait_for()
    s.page.keyboard.press("Escape")
    assert s.page.locator("#client-pick").count() == 0
    s.check("polish_picked")


def test_choose_a_client_for_a_paralegal_leaves_out_the_case_she_may_not_open(world, paralegal, asylum_closed_to_the_paralegal):
    s = paralegal
    _list(s, world)
    s.page.locator("#client").click()
    s.page.locator("#client-pick").wait_for()
    s.page.locator("#client-pick input").fill("asylum")
    assert not [t for t in s.page.locator(".pick-item").all_inner_texts() if "case-asylum" in t]  # a restricted case is not in her list, not even by its kind (another file's asylum case, which she is named on, may be)


# -- 4. a search result names its case and opens its document ------------------------------------------------------------------------


def test_a_search_result_names_its_case_and_opens_the_documents_tab_on_the_document(world, attorney):
    _seed(world, attorney)
    s = attorney
    _list(s, world)  # the dashboard builds each case's row, which the result's "kind of case" is read from
    s.page.fill("#find-q", "bakery")
    s.page.press("#find-q", "Enter")
    s.page.wait_for_selector("#search-count")
    s.settle(300)
    row = s.page.locator("table.hits tr", has_text="Notice to Appear")
    cell = row.locator("td").first.inner_text()
    assert "Ana Clara Exemplo Souza" in cell and "case-court" in cell and "·" in cell  # the person, the kind of case, the id
    # after a search the top bar is back to normal: the search box is not left open over its neighbours (the white pill of visit 3)
    box = s.page.locator("#find-q").bounding_box()
    assert box["width"] <= 175 and not s.page.evaluate("document.activeElement === document.getElementById('find-q')")
    assert s.page.locator("#find-q").input_value() == "bakery"
    s.check("polish_search_result")
    row.get_by_role("button").first.click()
    s.page.wait_for_selector("#documents")
    row = s.page.locator("#doc-n1")
    row.wait_for()
    assert "found" in (row.get_attribute("class") or "")  # scrolled to and marked for a moment
    assert "Needs attention" not in s.page.locator("#main .pagehead").inner_text() and "Documents" in s.page.locator("#main .pagehead").inner_text()
    s.check("polish_search_to_documents")


def test_an_empty_saved_search_says_what_was_looked_at(world, attorney):
    _seed(world)
    s = attorney
    s.page.goto("about:blank")
    s.page.goto(world["review"] + "#search")
    s.page.wait_for_selector("#search-count")
    s.page.get_by_role("button", name="Police clearances older than two years").click()
    s.page.wait_for_function("() => document.getElementById('search-count').innerText.includes('read in')")
    text = s.page.locator("#search-count").inner_text()
    assert re.search(r"No police clearance is older than two years \(\d+ documents? read in \d+ cases?, \d\d/\d\d/\d{4}\)", text), text
    s.check("polish_search_empty")


# -- 5. expiring documents ---------------------------------------------------------------------------------------------------------


def test_an_empty_expiring_list_says_what_is_watched_and_for_which_cases(world, attorney):
    s = attorney
    s.page.goto("about:blank")
    s.page.goto(world["review"] + "#deadlines")
    s.page.wait_for_selector("#due-tab-expiring")
    s.page.locator("#due-tab-expiring").click()
    s.page.wait_for_selector("#expiring-count")
    s.page.select_option("select[aria-label='How far ahead']", "30")
    s.page.wait_for_function("() => document.getElementById('expiring-count').innerText.startsWith('0 documents')")
    watched = s.page.locator("#expiring-watched")
    assert watched.get_attribute("open") is not None  # open when the list is empty, so it does not look broken
    text = watched.inner_text()
    assert "What is watched, and for which cases" in text and re.search(r"\d+ cases? checked", text)
    assert "Passport" in text and "Consular processing cases and travel cases only" in text
    assert "Work permit (EAD)" in text and "I-94 admit-until date" in text and "Police clearance" in text
    assert "A passport is only" not in text and " -- " not in text and "—" not in text
    assert "a passport only for consular processing and travel cases" in s.page.locator("#expiring-rows").inner_text()
    s.check("polish_expiring_empty")


# -- 6. the client list -----------------------------------------------------------------------------------------------------------------


def test_the_client_list_not_invited_link_menu_and_labels(world, attorney):
    s = attorney
    _list(s, world)
    tiles = s.page.locator(".tile .l").all_inner_texts()
    assert "Not invited yet" in tiles and tiles.index("Not invited yet") < tiles.index("Invited")
    nova = s.page.locator("tr.row[data-client='pilot-nova']")
    assert "Not invited yet" in nova.inner_text() and nova.get_by_role("button", name="Invite", exact=True).count() == 1
    s.page.locator(".tile", has_text="Not invited yet").click()  # the stage filters the list to the ones nobody has written to (the server filters: the page is asked for again)
    s.page.wait_for_selector("tr.row[data-client='case-sij']", state="detached")
    assert s.page.locator("tr.row[data-client='pilot-nova']").count() == 1 and s.page.locator("tr.row[data-client='case-sij']").count() == 0
    s.page.locator(".tile", has_text="Not invited yet").click()
    s.page.wait_for_selector("tr.row[data-client='case-sij']")  # every case again (the page is asked for again)

    # the form's labels are tied to their boxes
    s.page.get_by_role("button", name="Add a client").click()
    form = s.page.locator("#add-client-form")
    tied = form.evaluate("""f => [...f.querySelectorAll('label.setfield[for]')].map(l => [l.textContent.trim().slice(0, 30), !!l.control && l.control.id === l.htmlFor])""")
    assert len(tied) >= 6 and all(ok for _t, ok in tied), tied
    assert form.locator("[role=group]").get_attribute("aria-labelledby") == "add-consent-lbl" and s.page.locator("#add-consent-lbl").count() == 1
    assert s.page.locator("input[aria-label='Search clients by name or ID']").count() == 1
    form.get_by_label("Client's full name").fill("Polish Exemplo Teste")
    form.get_by_label("Client's email address").fill("polish.exemplo@example.com")
    form.get_by_label("Client's phone number").fill("(555) 010-7777")
    form.get_by_role("checkbox", name="Email").check()
    form.get_by_role("checkbox", name="Send the invitation now").check()
    s.page.locator("#add-client-save").click()
    assert s.toast().startswith("Added Polish Exemplo Teste.")
    row = s.page.locator("tr.row", has_text="Polish Exemplo Teste")
    row.wait_for()

    # More: the invitation menu is not cut off, and the link opens beside the row
    row.locator("details.rowmenu summary").click()
    again = row.get_by_role("button", name="Send the invitation again")
    box, cell = again.bounding_box(), row.locator("td").last.bounding_box()
    assert box["x"] + box["width"] <= cell["x"] + cell["width"] + 1, (box, cell)
    assert again.evaluate("b => b.scrollWidth <= b.clientWidth + 1")  # the words fit inside the button
    _fits(s, row, "the menu at 1400")
    row.get_by_role("button", name="Show the link").click()
    panel = s.page.locator("#link-panel")
    panel.wait_for()
    client_id = row.get_attribute("data-client")
    assert s.page.locator(f"tr.row[data-client='{client_id}'] + tr.linkrow #link-panel").count() == 1  # right under the client it is for
    s.page.fill("input[aria-label='Search clients by name or ID']", "polish")  # filtering the list keeps it beside its row
    assert s.page.locator(f"tr.row[data-client='{client_id}'] + tr.linkrow #link-panel").count() == 1
    assert panel.evaluate("p => { const r = p.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }")  # on screen, not a page away
    _fits(s, row, "the link at 1400")
    s.page.set_viewport_size({"width": 1000, "height": 800})  # a narrow window: still no sideways page scroll, and nothing out of view
    s.page.wait_for_function("() => window.innerWidth === 1000")
    _fits_once_laid_out(s, row, "the menu and the link at 1000")
    s.page.set_viewport_size({"width": 1400, "height": 900})
    s.check("polish_link_beside_row")
    panel.get_by_role("button", name="Close").click()
    assert s.page.locator("#link-panel").count() == 0 and s.page.locator("tr.linkrow").count() == 0


def test_an_invitation_that_reached_nobody_says_so_once_and_leaves_the_client_not_invited(world, paralegal):
    s = paralegal
    _list(s, world)
    s.page.get_by_role("button", name="Add a client").click()
    form = s.page.locator("#add-client-form")
    form.get_by_label("Client's full name").fill("Sem Canal Exemplo")
    form.get_by_label("Client's email address").fill("sem.canal@example.com")
    s.page.locator("#add-client-save").click()  # no way of being reached agreed to
    s.toast()
    row = s.page.locator("tr.row", has_text="Sem Canal Exemplo")
    row.wait_for()
    row.get_by_role("button", name="Invite", exact=True).click()
    said = s.toast(ok=False)
    assert said.startswith("Not sent: no channel the client agreed to") and "Not sent: Not sent" not in said, said
    s.page.locator("tr.row", has_text="Sem Canal Exemplo").filter(has_text="Not invited yet").wait_for()  # the list is drawn again after the refusal
    row = s.page.locator("tr.row", has_text="Sem Canal Exemplo")
    assert "Not invited yet" in row.inner_text() and row.get_by_role("button", name="Invite", exact=True).count() == 1


# -- 7. the questionnaire in a row's menu --------------------------------------------------------------------------------------------


def test_the_questionnaire_of_an_invited_only_client_is_in_the_rows_menu(world, paralegal):
    s = paralegal
    _list(s, world)
    row = s.page.locator("tr.row[data-client='pilot-nova']")
    row.locator("details.rowmenu summary").click()
    pick = row.locator("select.q-pick")
    pick.wait_for()
    s.page.wait_for_function("() => document.querySelector(\"tr.row[data-client='pilot-nova'] select.q-pick\").options.length > 1")
    assert pick.input_value() == "i485" and "Citizenship (N-400)" in pick.inner_text()
    pick.select_option("n400")
    assert "now answers the Citizenship (N-400) questionnaire" in s.toast()
    from portal.store import PortalStore

    store = PortalStore(world["portal"])
    assert store.profile("pilot-nova")["filing"] == "n400" and store.profile("pilot-nova")["filing_changed"]["by"] == "Paulo Paralegal"
    # a client who has started answering (saved answers) is asked first, in the words the case page uses (front_desk.change_warning)
    store.save_answers("pilot-nova", {"applicant.given_name": "NOVA"})
    _list(s, world)
    row = s.page.locator("tr.row[data-client='pilot-nova']")
    row.locator("details.rowmenu summary").click()
    row.locator("select.q-pick").wait_for()
    s.page.wait_for_function("() => document.querySelector(\"tr.row[data-client='pilot-nova'] select.q-pick\").options.length > 1")
    s.page.evaluate("() => { window.confirm = (m) => { window.__asked = m; return false; }; }")  # the person says no
    row.locator("select.q-pick").select_option("i485")
    s.page.wait_for_function("() => window.__asked !== undefined")  # the page asked (the change handler ran), not a pause
    asked = s.page.evaluate("() => window.__asked")
    assert asked and "has started answering the Citizenship (N-400) questionnaire. Change it anyway?" in asked and "come back if it is changed back" in asked, asked
    assert "may not carry over" not in asked
    assert row.locator("select.q-pick").input_value() == "n400"  # the box goes back to what it was
    assert store.profile("pilot-nova")["filing"] == "n400"  # declined: nothing changed
    s.page.evaluate("() => { window.confirm = (m) => { window.__asked = m; return true; }; }")  # the person says yes: sent again as confirmed, no error
    row.locator("select.q-pick").select_option("i485")
    assert "now answers the Green card" in s.toast()  # a success, not the warning shown as an error
    assert store.profile("pilot-nova")["filing"] == "i485" and store.profile("pilot-nova")["filing_changed"]["by"] == "Paulo Paralegal"
    (world["portal"] / "clients" / "pilot-nova" / "answers.json").unlink()  # as the world was


# -- 8. a draft packet says one thing -----------------------------------------------------------------------------------------------


def test_a_packet_built_with_open_problems_says_it_is_a_draft_in_one_sentence(world, attorney):
    s = attorney
    s.open("case-sij", "packet")
    s.page.get_by_role("button", name=re.compile("Build packet|Rebuild packet")).click()
    s.page.wait_for_function("() => document.getElementById('toast').innerText.startsWith('Packet built')", timeout=180000)
    s.settle(1500)
    s.page.wait_for_selector("th:has-text('Ready to mail? Not yet')")
    todo = s.page.locator("section", has_text="Ready to mail? Not yet").last.inner_text()
    assert re.search(r"Draft until \d+ problems? (are|is) settled", todo), todo
    assert "This packet is marked DRAFT because it was built while these were open" in todo
    assert "Not a draft" not in todo and "The packet was built with open problems" not in todo
    s.check("polish_packet_draft")


# -- 12. a person who is not a client -----------------------------------------------------------------------------------------------


def test_a_petitioner_who_is_not_a_client_gets_a_record_under_family_members(world, paralegal):
    s = paralegal
    s.open("case-family", "journey")
    box = s.page.locator("#people-box")
    box.scroll_into_view_if_needed()
    assert "people who are not clients" in box.inner_text().lower() and "nothing is ever sent to them" in box.inner_text()
    box.get_by_label("Their given name").fill("Marcos")
    box.get_by_label("Their family name").fill("Exemplo")
    box.get_by_label("How they are related to this client").select_option("Parent")
    box.get_by_label("Whose documents are theirs").select_option("petitioner")
    box.get_by_label("Their phone").fill("(555) 010-0404")
    box.get_by_label("Their email").fill("marcos.exemplo@example.com")
    box.locator("#person-add").click()
    assert s.toast() == "Recorded."
    s.page.wait_for_selector("#people-rows")
    row = s.page.locator("#people-rows tr", has_text="Marcos Exemplo")
    text = row.inner_text()
    assert "Parent" in text and "(555) 010-0404" in text and "marcos.exemplo@example.com" in text and "Their documents are marked Petitioner" in text
    s.check("polish_person")
    assert not (world["portal"] / "clients" / "marcos-exemplo").exists()  # not a client: no portal place, no invitation
    row.get_by_role("button", name="Use on the family petition").click()
    said = s.toast()
    assert said.startswith("Filled in on the petition:") and "email" in said
    decisions = json.loads((world["clients"] / "case-family" / "decisions.json").read_text(encoding="utf-8"))  # each answer is a typed answer, by whoever pressed it
    mine = decisions["family:petitioner.email"]
    assert mine["values"] == {"petitioner.email": "MARCOS.EXEMPLO@EXAMPLE.COM"} and mine["reviewer"] == "Paulo Paralegal"  # typed answers go in capitals
    assert mine["note"] == "from Marcos Exemplo's record on the case"
    row.get_by_role("button", name="Remove").click()
    s.page.wait_for_function("() => !document.getElementById('people-rows')")
