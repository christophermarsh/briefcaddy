"""Fictional example or implementation helper."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from conftest import REPO, Screen
from test_pathways import build

sys.path.insert(0, str(REPO / "tools"))


@pytest.fixture(scope="module", autouse=True)
def _leave_the_world_as_it_was(world):
    """The cases and portal clients these tests make (clones, an import, a client added on the screen) are taken away at the end: other files'
    tests in the same browser session count the world's cases (the client picker, the search)."""
    import shutil

    before = {p.name for p in world["clients"].iterdir()}
    portal = world["portal"] / "clients"
    portal_before = {p.name for p in portal.iterdir()} if portal.exists() else set()
    yield
    for p in world["clients"].iterdir():
        if p.name not in before and p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    for p in portal.iterdir() if portal.exists() else []:
        if p.name not in portal_before and p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


def _list(s):
    s.page.goto("about:blank")
    s.page.goto(s.world["review"] + "#all")
    s.page.wait_for_selector("#client-rows")
    s.settle(300)


def _documents(s, client):
    s.open(client, "documents")
    s.page.wait_for_selector("#documents")


def _api(s, path):
    r = s.page.request.get(s.world["review"].rstrip("/") + path)
    assert r.status == 200, (path, r.status)
    return r.json()


# -- 1. an imported client can be invited --------------------------------------------------------------------------------------


def test_a_client_brought_over_from_docketwise_can_be_invited_from_the_list(world, attorney, tmp_path):
    import import_docketwise as imp

    (tmp_path / "contacts.csv").write_text(
        "id,First Name,Last Name,Email,Mobile Phone Number,Language,Email OK\n"
        "c1,Ana,Importada Exemplo,ana.importada@example.com,+1 555 010 0301,Portuguese,yes\n"
        "c2,Rosa,Asilo Exemplo,rosa.asilo@example.com,+1 555 010 0302,Portuguese,yes\n", encoding="utf-8")
    (tmp_path / "matters.csv").write_text("ID,Number,Title,Client ID,Type,Status,Archived\n"
                                          "5101,A-1,Ana green card,c1,I-485,Open,\n5102,A-2,Rosa asylum,c2,Asilo,Open,\n", encoding="utf-8")
    imp.run_import(tmp_path / "contacts.csv", tmp_path / "matters.csv", None, world["clients"], world["portal"], cases=world["clients"])
    import conflicts
    import world as w

    s = attorney
    _list(s)
    row = s.page.locator("tr.row", has_text="Ana Importada Exemplo")
    assert row.count() == 1
    # an imported client waits for an attorney's conflict decision (src/conflicts.py): no Invite on the row until then
    assert "waiting for an attorney's decision" in row.inner_text() and row.get_by_role("button", name="Invite", exact=True).count() == 0
    [ana] = [p.name for p in world["clients"].iterdir() if p.name.startswith("ana_importada_exemplo")]
    conflicts.decide(world["clients"] / ana, "none", "", by=w.ATTORNEY[1], role="attorney")  # the screen's way is tests/e2e/test_conflicts.py
    _list(s)
    row = s.page.locator("tr.row", has_text="Ana Importada Exemplo")
    row.get_by_role("button", name="Invite", exact=True).click()
    said = s.toast()
    assert "unknown client" not in said and "Invitation" in said, said  # the screen used to answer "unknown client" until the case was processed
    outbox = (world["portal"] / "outbox.jsonl").read_text(encoding="utf-8")
    assert "ana.importada@example.com" in outbox and "rosa.asilo@example.com" not in outbox  # the asylum row is restricted: nothing goes to it
    s.check("fifth_imported_invited")


# -- 2. the firm's own name ----------------------------------------------------------------------------------------------------


def test_the_screens_carry_the_firms_own_name_and_case_review_alone_without_one(world, browser, attorney):
    path = Path(world["env"]["I485_SETTINGS"])
    before = path.read_text(encoding="utf-8") if path.exists() else None
    saved = json.loads(before) if before else {}
    firm = {"Exemplo & Lima Immigration LLP": "EL", "": "CR"}
    try:
        for name, mark in firm.items():
            values = {k: v for k, v in ((saved.get("firm") or {}).get("values") or {}).items() if k != "firm.business_name"}
            path.write_text(json.dumps(saved | {"firm": {"values": values | ({"firm.business_name": name} if name else {})}}), encoding="utf-8")
            attorney.page.goto("about:blank")
            attorney.page.goto(world["review"])
            attorney.page.wait_for_selector("#client", state="visible")
            expected_title = f"Case Review · {name}" if name else "Case Review"
            assert attorney.page.title() == expected_title, attorney.page.title()
            assert attorney.page.locator(".brand .mark").inner_text() == mark
            assert (attorney.page.locator(".brand .sub").text_content() or "").strip() == name
            assert "Georges" not in attorney.page.locator("body").inner_text() and "G|C" not in attorney.page.locator("body").inner_text()
            ctx = browser.new_context(viewport={"width": 1400, "height": 900})  # nobody signed in: the sign-in screen
            try:
                page = ctx.new_page()
                page.goto(world["review"])
                page.wait_for_selector("input[name=password]")
                said = page.locator("#case").inner_text()
                assert said.strip().endswith((f"{name}: Staff only" if name else "Staff only")) and "Georges" not in said, said
                assert page.title() == expected_title
            finally:
                ctx.close()
    finally:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(before, encoding="utf-8")


# -- 3. a document set to someone else leaves the client's answers --------------------------------------------------------------


def test_a_social_security_card_set_to_the_spouse_leaves_the_clients_answers_and_the_decision_log_says_so(world, attorney):
    s = attorney
    client = "case-spouse"

    def ssn():
        items = _api(s, f"/api/items?client={client}")
        text = json.dumps(items["cards"])
        return "123-45-6789" in text or "123456789" in text

    assert ssn()  # the card's number against the client's own answer: a card on Needs attention
    _documents(s, client)
    row = s.page.locator("tr", has=s.page.locator("select[aria-label^='Whose is the social security card']"))
    assert row.count() == 1
    row.locator("select[aria-label^='Whose is the social security card']").select_option("spouse")
    said = s.toast()
    assert "set aside" in said and "the spouse" in said, said
    assert not ssn()  # the card no longer feeds the client's boxes or cards
    s.open(client, "done")
    body = s.check("fifth_card_to_the_spouse")
    assert "Document set to the spouse: not the client's answers" in body.replace("\n", " ") or "set aside" in body, body[:1500]
    _documents(s, client)
    s.page.locator("select[aria-label^='Whose is the social security card']").select_option("applicant")
    assert "client's again" in s.toast()
    assert ssn()  # and setting it back restores the card's number and the question


# -- 4. a language change that would drop the translation asks first -----------------------------------------------------------


def _birth_row(s):
    return s.page.locator("tr", has=s.page.locator("select[aria-label^='Whose is the birth certificate']"))


def _change_language(s, to):
    row = _birth_row(s)
    row.locator("details summary", has_text="Change the language").click()
    row.locator("select[aria-label^='Language of the birth certificate']").select_option(to)
    row.get_by_role("button", name="Save the language").click()


def test_changing_a_translated_birth_certificate_to_english_asks_and_records_the_answer(world, attorney):
    s = attorney
    _documents(s, "case-resident")
    assert "Needs a translation" in _birth_row(s).inner_text()
    _change_language(s, "en")
    box = s.page.locator("dialog.askbox")
    box.wait_for(state="visible")
    assert "This document was marked as needing a translation. Keep that?" in box.inner_text()
    box.get_by_role("button", name="Cancel").click()
    assert "Nothing changed" in s.toast()
    assert "Needs a translation" in _birth_row(s).inner_text()  # a cancel changes nothing
    _change_language(s, "en")
    s.page.locator("dialog.askbox").get_by_role("button", name="No", exact=True).click()
    assert "no longer needs a translation" in s.toast()
    text = _birth_row(s).inner_text()
    assert "Needs a translation" not in text and "dropped by Ana Attorney on" in text, text  # the answer is on the record, with who and when
    s.check("fifth_translation_dropped")
    _change_language(s, "pt")  # back to a foreign language: the badge returns on its own, with no question
    s.toast()
    assert "Needs a translation" in _birth_row(s).inner_text() and s.page.locator("dialog.askbox").count() == 0


def test_a_yes_keeps_the_translation_and_the_badge(world, attorney):
    s = attorney
    _documents(s, "case-court")
    _change_language(s, "en")
    s.page.locator("dialog.askbox").get_by_role("button", name="Yes", exact=True).click()
    assert "still needs a translation" in s.toast()
    text = _birth_row(s).inner_text()
    assert "Needs a translation" in text and "kept by Ana Attorney on" in text, text


# -- 5. the mailing toast and the client's page --------------------------------------------------------------------------------


def test_the_mailing_toast_says_the_clients_page_shows_it_now_and_it_does(world, attorney):
    from portal.store import PortalStore

    s = attorney
    s.open("demo-ana", "packet")
    build(s, "fifth_mailing_built")
    s.page.fill("input[aria-label='Tracking number'], input[aria-label='Confirmation number']", "9400111899223456789012")
    reason = s.page.locator("input[placeholder^='Reason to']")
    if reason.is_visible():
        reason.fill("E2E: made-up client")
    s.page.get_by_role("button", name=re.compile("Record the (mailing|filing)")).click()
    said = s.toast()
    assert "the client's page shows it now" in said and "tonight" not in said, said
    happened = PortalStore(world["portal"]).journey("demo-ana")["en"]["happened"]
    assert happened and happened[0]["text"].startswith("We mailed your application (Form I-") and "(i" not in happened[0]["text"], happened[:1]
    s.settle()
    s.page.get_by_role("button", name="Undo").last.click()  # take the record back out (the screen's own dialog is accepted): other tests start from a case nothing was mailed for
    assert "removed" in s.toast()
    s.settle()


# -- 7. TPS panels on dead ground ----------------------------------------------------------------------------------------------


def _tps(world, s, client, country):
    w = world["world"]
    d = w.clone(world["root"], "demo-ana", client)
    w._not_sij(d)
    w.drop_facts(d, "applicant.citizenship")
    w.add_fact(d, "applicant.citizenship", country)
    s.open(client, "packet")
    s.page.select_option("select[aria-label='More filings']", "tps")
    s.settle()
    return s.check(f"fifth_{client}")


def test_el_salvador_says_nothing_to_file_while_protection_continues_and_offers_nothing(world, paralegal):
    body = _tps(world, paralegal, "case-tps-sv", "EL SALVADOR")
    assert "continued through 09/09/2026" in body and "nothing to file here" in body and "1254a(b)(3)(C)" in body, body[:2500]
    assert "has ended" not in body and "has passed" not in body and "Sept." not in body
    assert "Build packet" not in body and "Rebuild packet" not in body and "Part 1" not in body and "Initial registration or re-registration" not in body
    assert paralegal.page.get_by_role("button", name=re.compile("uild packet")).count() == 0


def test_venezuela_says_terminated_and_shows_nothing_else(world, paralegal):
    body = _tps(world, paralegal, "case-tps-ve", "VENEZUELA")
    assert "Nothing to file now" in body and "Terminated" in body, body[:2500]
    assert paralegal.page.get_by_role("button", name=re.compile("uild packet")).count() == 0 and "Part 1" not in body and "Where it is filed" not in body


# -- 8. the G-639 says online --------------------------------------------------------------------------------------------------


def test_the_foia_request_says_online_never_paper(world, paralegal):
    w = world["world"]
    d = w.clone(world["root"], "demo-ana", "case-foia2")
    w._not_sij(d)
    paralegal.open("case-foia2", "packet")
    paralegal.page.select_option("select[aria-label='More filings']", "g639")
    paralegal.settle()
    body = paralegal.check("fifth_foia")
    assert "Filed online only." in body and "BEFORE YOU ENTER IT ONLINE" in body.upper()
    assert "FILED ON PAPER" not in body.upper() and "BEFORE YOU MAIL IT" not in body.upper()


# -- 9. the grammar suggestion's two lines --------------------------------------------------------------------------------------


def test_a_refused_suggestion_shows_was_and_suggested_on_separate_lines_and_no_change_has_no_accept(world, attorney):
    import drafting
    import settings

    w = world["world"]
    d = w.clone(world["root"], "demo-ana", "case-smooth")
    w._not_sij(d)
    import asylum

    settings.PATH = Path(world["env"]["I485_SETTINGS"])  # the world's own settings: the server reads the same file
    settings.save("drafting", {"grammar_smoothing": "on"}, "Ana Attorney")
    asylum.answer(d, {"asylum.b1a": "Yes", "asylum.b1a_explain": "In 2024 men from the party threatened me, I was afraid.",
                      "asylum.b1b": "Yes", "asylum.b1b_explain": "The police did not helped me and I was afraid."}, "Paulo Paralegal", "paralegal")
    drafting.smooth(d, "i589", "Paulo Paralegal", model=lambda t: ("In 2024 2024, men from the party threatened me, I was afraid, afraid." if t.startswith("In 2024")
                                                                  else t, "fake-model"))
    attorney.open("case-smooth", "packet", "i589")
    attorney.settle()
    body = attorney.check("fifth_smoothing")
    assert "was: " in body and "suggested: " in body and "No change suggested" in body, body[-3000:]
    assert body.count("Accept the suggestion") == 0
    assert "Grammar suggestion refused" in body and "It was:" not in body
    wide = attorney.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    assert wide <= 1


# -- 10. the fifth wrong code ----------------------------------------------------------------------------------------------------


def test_the_fifth_wrong_code_and_the_next_try_say_the_account_is_locked(world, browser):
    from review.auth import Accounts

    sys.path.insert(0, str(REPO / "tests"))
    import second_factor

    accounts = Accounts(world["users"])
    who = ("lock.exemplo@example.com", "Lock Exemplo", "lock-exemplo-pass-1")
    accounts.add(who[0], who[1], "attorney")
    accounts._set_password(who[0], who[2], must_change=False)
    second_factor.set_up(accounts, who[0], who[2])
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    try:
        page = ctx.new_page()
        page.goto(world["review"])
        page.fill("input[name=email]", who[0])
        page.fill("input[name=password]", who[2])
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_selector("input[name=code]")
        for n in range(5):
            page.fill("input[name=code]", "000000")
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_function("() => document.querySelector('form .err') && document.querySelector('form .err').innerText.length > 0 && document.querySelector('input[name=code]') !== null")
            said = page.locator("form .err").inner_text()
            page.wait_for_timeout(150)
        assert "locked for 15 minutes" in said and "took too long" not in said, said  # the fifth try: the lockout sentence the password screen shows
        page.fill("input[name=code]", "000000")
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_selector("input[name=password], form .err:not(:empty)")
        text = page.locator("#main").inner_text()
        assert "locked for 15 minutes" in text and "took too long" not in text, text  # and the next one, which used to say it took too long
    finally:
        ctx.close()


# -- 11. small ones ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("width", [800])
def test_the_top_bar_takes_a_second_line_instead_of_scrolling_sideways(browser, world, width):
    import world as w

    s = Screen(browser, world, w.PARALEGAL)
    try:
        s.page.set_viewport_size({"width": width, "height": 900})
        for where in ("all", "work", "case"):  # not the Search page: a search refreshes the world's index, and other files' tests seed it right after
            s.page.goto("about:blank")
            s.page.goto(world["review"] + ("?tab=check#case-sij" if where == "case" else f"#{where}"))
            s.settle(500)
            wide = s.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            assert wide <= 1, f"{where} at {width}: {wide}px wider than the window"
    finally:
        s.close()


def test_the_row_menu_stays_inside_its_cell_and_the_window(world, attorney):
    s = attorney
    _list(s)
    row = s.page.locator("tr.row", has=s.page.locator("details.rowmenu")).first
    row.get_by_role("button", name="Invite", exact=True).click() if row.get_by_role("button", name="Invite", exact=True).count() else None
    s.settle()
    _list(s)
    row = s.page.locator("tr.row", has=s.page.locator("details.rowmenu")).first
    before = s.page.evaluate("() => document.getElementById('client-rows').getBoundingClientRect().width")
    row.locator("details.rowmenu summary").click()
    edge = s.page.evaluate("""() => { const open = document.querySelector('details.rowmenu[open]'), cell = open.closest('td').getBoundingClientRect();
        return [window.innerWidth, cell.right, ...[...open.querySelectorAll('.btn, select')].map((b) => b.getBoundingClientRect().right)]; }""")
    assert all(x <= edge[0] and x <= edge[1] + 0.5 for x in edge[2:]), edge  # the menu stays in its cell and inside the window
    after = s.page.evaluate("() => document.getElementById('client-rows').getBoundingClientRect().width")
    assert after <= before + 0.5, (before, after)  # and opening it does not widen the list (that was what pushed it past the window)
    assert s.page.evaluate("() => document.getElementById('client-rows').parentElement.scrollWidth - document.getElementById('client-rows').parentElement.clientWidth") <= 1


def test_the_case_viewing_list_says_what_its_restricted_mark_means(world, attorney):
    s = attorney
    s.open("case-sij", "done")
    s.page.wait_for_selector("#viewed")
    body = s.check("fifth_viewed")
    assert "restricted now" in body.lower(), body[:1500]


def test_the_sign_off_cards_reference_is_folded_away(world, attorney):
    s = attorney
    s.open("case-sij", "attorney")
    body = s.check("fifth_signoff")
    assert "Reference: NA-" not in body  # a closed fold shows no code; the Details control holds it
    fold = s.page.locator("details.why.ref")
    if fold.count():
        fold.first.locator("summary").click()
        assert "Reference:" in fold.first.inner_text()


def test_a_turned_off_persons_page_clears_within_a_minute_on_a_ten_second_poll(browser, world, attorney):
    from review.auth import Accounts

    accounts = Accounts(world["users"])
    who = ("tenseconds.exemplo@example.com", "Tenseconds Exemplo", "tenseconds-pass-1")
    accounts.add(who[0], who[1], "paralegal")
    accounts._set_password(who[0], who[2], must_change=False)
    s = Screen(browser, world, who)
    try:
        s.page.clock.install()
        s.page.goto("about:blank")
        s.page.goto(world["review"] + "#all")
        s.page.wait_for_selector("#client-rows")
        s.settle(300)
        accounts.update(who[0], by=attorney.who[0], active=False)
        s.page.clock.fast_forward("00:12")  # one poll of ten seconds, not a minute
        s.page.wait_for_selector("input[name=password]", timeout=15000)
        assert "Sign in" in s.page.locator("body").inner_text()
    finally:
        s.close()
