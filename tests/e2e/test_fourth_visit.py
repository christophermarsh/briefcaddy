"""Fictional example or implementation helper."""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest
from conftest import REPO, Screen

ENGLISH = "What is your father's date of birth?"
PORTUGUESE = "Qual é a data de nascimento do seu pai?"


def _list(s):
    s.page.goto("about:blank")
    s.page.goto(s.world["review"] + "#all")
    s.page.wait_for_selector("#client-rows")
    s.settle(300)


def _settings(s, focus=""):
    s.page.goto("about:blank")
    s.page.goto(s.world["review"] + "#settings" + (":" + focus if focus else ""))
    s.page.wait_for_selector(".setsec")
    s.settle(400)


# -- 1. a protected client before any document is processed ---------------------------------------------------------------


def test_a_vawa_client_added_on_the_screen_is_restricted_at_once_and_not_invited(world, attorney, paralegal):
    from portal.store import PortalStore

    s = attorney
    _list(s)
    s.page.get_by_role("button", name="Add a client").click()
    form = s.page.locator("#add-client-form")
    form.get_by_label("Client's full name").fill("Rosa Exemplo Vawa")
    form.get_by_label("Client's phone number").fill("(555) 010-4242")
    form.get_by_label("Client's language").select_option("es")
    form.get_by_role("checkbox", name="Text messages").check()
    invite = form.get_by_role("checkbox", name="Send the invitation now")
    invite.check()
    form.get_by_label("Kind of case").select_option("vawa")
    # chosen VAWA: the form says so before anything is added, and the invitation tick goes off
    held = s.page.locator("#add-held")
    assert held.is_visible() and "Restricted: the office gives the client their link in person." in held.inner_text()
    assert "8 U.S.C. 1367" in held.inner_text() and invite.is_disabled() and not invite.is_checked()
    s.check("fourth_add_vawa_form")
    s.page.locator("#add-client-save").click()
    said = s.toast()
    assert said.startswith("Added Rosa Exemplo Vawa, restricted from the start: a VAWA, T or U visa case (8 U.S.C. 1367).") and "in person" in said
    s.page.wait_for_selector("tr.row:has-text('Rosa Exemplo Vawa')")
    row = s.page.locator("tr.row", has_text="Rosa Exemplo Vawa")
    text = row.inner_text()
    assert "Restricted" in text and "No invitation was sent" in text and row.get_by_role("button", name="Invite", exact=True).count() == 0
    row.locator("details.rowmenu summary").click()
    assert row.get_by_role("button", name="Show the link").count() == 1 and row.get_by_role("button", name="Send the invitation again").count() == 0
    s.check("fourth_add_vawa_row")

    store = PortalStore(world["portal"])
    [rosa] = [c for c in store.clients() if c.startswith("rosa-exemplo-vawa")]
    assert not store.profile(rosa).get("invited_at") and json.loads((world["clients"] / rosa / "access.json").read_text(encoding="utf-8"))["marked"]["on"]
    outbox = world["portal"] / "outbox.jsonl"
    assert not outbox.exists() or "4242" not in outbox.read_text(encoding="utf-8")

    # the paralegal, not named on it: the client is nowhere on her screens
    _list(paralegal)
    assert "Rosa Exemplo Vawa" not in paralegal.page.locator("body").inner_text()
    paralegal.page.goto("about:blank")
    paralegal.page.goto(world["review"] + "#reports")
    paralegal.page.wait_for_selector("#reports-count")
    assert "Rosa Exemplo Vawa" not in paralegal.page.locator("body").inner_text()
    paralegal.check("fourth_paralegal_no_vawa")


# -- 2. a typed answer reaches the box at once ---------------------------------------------------------------------------------


def test_a_typed_answer_fills_the_box_without_any_worker(world, browser, paralegal):
    """Reproduces the shopper exactly: ask a typed question from the case, answer it on the phone, go back to the card. The world's
    servers run no portal worker, as the shopper's did not: before the fix the card still said "Needs an answer"."""
    from portal.store import PortalStore

    w = world["world"]
    w.clone(world["root"], "demo-ana", "case-typed")  # a portal client of its own, so no other test's answers are here
    store = PortalStore(world["portal"])
    store.add_client("case-typed", "Ana Clara Exemplo Souza", email="case-typed@example.com", language="pt")
    s = paralegal
    s.open("case-typed", "fix")
    card = s.page.locator("article, .card", has_text="Father: Date of birth").filter(has=s.page.get_by_role("button", name="Ask the client")).last
    if card.count() == 0:
        pytest.skip("no father's date of birth card in this world")
    card.get_by_role("button", name="Ask the client").click()
    form = s.page.locator(".ask")
    with s.page.expect_response(lambda r: "ask-preview" in r.url and ENGLISH in (r.request.post_data or ""), timeout=60000):
        form.get_by_label("Your question, in English").fill(ENGLISH)
    form.get_by_label("The question in Portuguese").fill(PORTUGUESE)
    form.get_by_role("button", name="Add to the client's list").click()
    s.toast()
    s.settle()
    s.page.locator("section.tray", has_text="Questions for the client").get_by_role("button", name=re.compile("Send all")).click()
    s.toast()

    link = subprocess.run([sys.executable, "src/portal/admin.py", "link", "case-typed"], cwd=REPO, env=world["env"],
                          capture_output=True, text=True).stdout.split()[0]
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
    try:
        page = ctx.new_page()
        page.goto(link)
        page.wait_for_load_state("networkidle")
        if page.locator(".btn.big").count():
            page.locator(".btn.big").first.click()
            page.wait_for_timeout(800)
        item = page.locator(".alert .item", has_text=PORTUGUESE)
        item.locator("input[type=date]").fill("1970-05-04")
        item.locator("input[type=date]").dispatch_event("change")
        item.get_by_role("button", name="Enviar resposta").click()
        page.wait_for_timeout(800)
        assert page.locator(".alert .item", has_text=PORTUGUESE).count() == 0
    finally:
        ctx.close()

    s.open("case-typed", "fix")  # back to the card: no longer waiting for an answer
    fix = s.page.locator("#main").inner_text()
    assert not s.page.locator("article.card", has_text="Father: Date of birth").filter(has_text="Needs an answer").count(), fix[:600]
    s.open("case-typed", "check")
    card = s.page.locator("article.card", has_text="Father").filter(has_text="Filled from the client's answer").first
    card.wait_for()
    text = card.inner_text()
    values = card.locator("input").evaluate_all("(xs) => xs.map((x) => x.value)")
    assert "1970-05-04" in values or "05/04/1970" in text, (values, text[:800])
    assert "from the client, answering the office's question" in text  # where it came from, as before
    s.check("fourth_typed_answer_filled")


# -- 3. the Clio screen tells the truth -------------------------------------------------------------------------------------


def test_the_clio_screen_says_it_has_not_run_against_a_real_clio(attorney):
    _settings(attorney, "connections")
    attorney.page.wait_for_selector("#clio-status")
    said = attorney.page.locator("#clio-unproven").inner_text()
    assert "built from Clio's published documentation and has not yet run against a real Clio account" in said
    assert "permissions" in said and "confirmed on the first connection" in said
    assert attorney.page.locator("#clio-sync-state").inner_text() == "Not connected"
    section = attorney.page.locator("#set-connections").inner_text()
    assert "Syncs every night" not in section and "Not connected" in section
    attorney.check("fourth_clio_unproven")


# -- 4. small fixes ------------------------------------------------------------------------------------------------------------


def test_restrict_and_switch_on_say_what_is_missing(attorney):
    attorney.open("case-sij", "done")
    attorney.page.wait_for_selector("#access")
    attorney.page.get_by_role("button", name="Restrict this case").click()
    said = attorney.toast(ok=False)
    assert said.startswith("Say why the case is restricted first")
    assert "Say why the case is restricted first" in attorney.page.locator("#access .err").inner_text()
    assert attorney.page.evaluate("document.activeElement.getAttribute('aria-label')") == "Why this case is restricted"
    assert attorney.page.locator("#restricted-banner").count() == 0  # nothing happened
    attorney.open("case-asylum", "done")  # restricted by law: its automatic messages are off
    attorney.page.wait_for_selector("#access")
    assert "it is an asylum case (8 CFR 208.6)" in attorney.page.locator("#access").inner_text()  # never "a asylum"
    attorney.page.get_by_role("button", name="Switch them on").click()
    assert attorney.toast(ok=False).startswith("Say why the client may get automatic texts and emails first")
    attorney.check("fourth_empty_reasons")


def test_changing_the_questionnaire_after_the_client_started_asks_first(world, paralegal):
    from portal.store import PortalStore

    s = paralegal
    store = PortalStore(world["portal"])
    s.open("demo-ana", "documents")
    asked: list[str] = []
    s.page.on("dialog", lambda d: asked.append(d.message))  # the Screen accepts every dialog; this one only reads it
    before = store.profile("demo-ana").get("filing") or "i485"
    other = "n400" if before == "i485" else "i485"
    s.page.get_by_label("Questionnaire the client answers").select_option(other)
    s.toast()
    assert asked and "has started answering" in asked[0] and "saved answers are kept" in asked[0], asked
    assert store.profile("demo-ana")["filing"] == other
    s.settle()
    s.page.get_by_label("Questionnaire the client answers").select_option(before)  # as it was
    s.toast()
    assert store.profile("demo-ana")["filing"] == before


def test_reports_say_what_they_count(attorney):
    attorney.page.goto("about:blank")
    attorney.page.goto(attorney.world["review"] + "#reports")
    attorney.page.wait_for_selector("#reports-count")
    said = attorney.page.locator("#reports-count").inner_text()
    assert re.search(r"\d+ cases? with a case file", said) and re.search(r"\d+ clients? (has|have) no case file yet", said), said
    attorney.check("fourth_reports")


@pytest.mark.parametrize("width", [1600, 1400, 1000])
def test_the_search_box_shows_its_words(browser, world, width):
    import world as w

    s = Screen(browser, world, w.PARALEGAL)
    try:
        s.page.set_viewport_size({"width": width, "height": 900})
        for where in ("all", "case"):
            if where == "case":
                s.open("case-sij", "check")
            else:
                _list(s)
            s.page.evaluate("fitSearchWords()")
            fits = s.page.evaluate("""() => {
                const box = document.getElementById('find-q'), css = getComputedStyle(box);
                const ctx = document.createElement('canvas').getContext('2d');
                ctx.font = `${css.fontStyle} ${css.fontWeight} ${css.fontSize} ${css.fontFamily}`;
                return [box.placeholder, ctx.measureText(box.placeholder).width, box.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight)];
            }""")
            assert fits[1] <= fits[2], fits  # the words fit: never "Search doc"
            if width == 1600 and where == "all":  # room for the words in full; a fuller bar says "Search", never a cut word
                assert fits[0] == "Search documents", fits
            # nothing beside it covers it
            gap = s.page.evaluate("() => document.querySelector('.topbar .who').getBoundingClientRect().left - document.getElementById('find-q').getBoundingClientRect().right")
            assert gap >= 0, gap
            if width >= 1400:
                s.check(f"fourth_search_{width}_{where}")
            elif s.shots:  # at 1,000 pixels the client list itself is wider than the window (its table): the bar is what this looks at
                s.page.locator(".topbar").screenshot(path=str(s.shots / f"fourth_search_{width}_{where}.png"))
    finally:
        s.close()


def test_the_n400_questions_are_grouped_with_their_opening_words(paralegal):
    s = paralegal
    s.open("case-spouse", "packet", "n400")
    s.page.wait_for_selector("details.qgroup")
    groups = s.page.locator("details.qgroup")
    assert groups.count() >= 4 and s.page.locator("details.qgroup button:has-text('Save this group')").count() == groups.count()
    part9 = groups.filter(has_text="Part 9")
    if part9.get_attribute("open") is None:
        part9.locator("summary").click()
    card = part9.locator("article.qcard", has_text="Part 9, 7d:")
    assert card.locator(".qlead").inner_text().startswith("Have you EVER ordered, incited, called for")  # the sentence 7.d finishes
    assert "When a question includes the word" in part9.inner_text()
    height = s.page.evaluate("document.documentElement.scrollHeight")
    assert height < 20000, height  # was 27,014 pixels with 90 Save buttons
    s.check("fourth_n400_groups")
    first = part9.locator("article.qcard", has_text="EVER claimed to be a U.S. citizen").first
    first.locator("select").select_option("No")
    part9.get_by_role("button", name="Save this group").click()
    assert s.toast() == "Saved."
    s.settle()
    part9 = s.page.locator("details.qgroup").filter(has_text="Part 9")
    if part9.get_attribute("open") is None:
        part9.locator("summary").click()
    first = part9.locator("article.qcard", has_text="EVER claimed to be a U.S. citizen").first
    assert "Answered by Paulo Paralegal" in first.inner_text() and first.locator("select").input_value() == "No"
    part9.get_by_role("button", name="Save this group").click()
    assert s.toast() == "Nothing changed in this group."


def test_the_n400_asks_the_client_her_own_questions_and_hides_what_her_basis_does_not_ask(world, paralegal):
    """The coordinator's addendum: "Legally change their name at naturalization?" said "Ask: the attorney"; it is the client's, and is
    asked in the portal. The spouse's questions showed on a General (5 years) filing; they are said once as not asked."""
    from portal.store import PortalStore

    w = world["world"]
    d = w.clone(world["root"], "demo-ana", "case-cit")
    w.add_fact(d, "n400.lpr_date", "2018-03-01", "green-card.pdf", "green_card")
    store = PortalStore(world["portal"])
    store.add_client("case-cit", "Ana Clara Exemplo Souza", email="case-cit@example.com", language="en")
    s = paralegal
    s.open("case-cit", "packet", "n400")
    s.page.wait_for_selector("details.qgroup")
    body = s.page.locator("#main").inner_text()
    assert body.count("Not asked: basis is General (5 years)") == 1 and "Part 5, 5.a" not in body
    about = s.page.locator("details.qgroup").filter(has_text="About the client")
    if about.get_attribute("open") is None:
        about.locator("summary").click()
    card = about.locator("article.qcard", has_text="Legally change their name at naturalization?")
    assert "Ask: the client" in card.inner_text() and "the attorney" not in card.inner_text()
    assert "Ask: the client" in about.locator("article.qcard", has_text="Should the SSA issue a Social Security card").inner_text()
    card.get_by_role("button", name="Ask the client in the portal").click()
    form = s.page.locator(".ask")
    assert form.get_by_label("Kind of answer").input_value() == "yes_no" and "Legally change their name" in form.get_by_label("Your question, in English").input_value()
    form.get_by_role("button", name="Add to the client's list").click()
    assert s.toast().startswith("Added.")
    s.check("fourth_n400_ask_client")
    s.settle()
    s.page.locator("section.tray", has_text="Questions for the client").get_by_role("button", name=re.compile("Send all")).click()  # the list goes to the client, one message
    s.toast()
    [asked] = [r for r in store.requests("case-cit") if r.get("facts") == ["n400.name_change"]]
    assert asked["status"] == "open" and asked["type"] == "yes_no"
    store.answer_request("case-cit", asked["id"], reply="No")  # the client answers in the portal (the phone screens: test_portal_questions)
    s.open("case-cit", "packet", "n400")
    s.page.wait_for_selector("details.qgroup")
    card = s.page.locator("article.qcard", has_text="Legally change their name at naturalization?")
    assert card.locator("select").input_value() == "No" and "the client's answer to the office's question" in card.inner_text()

    # the attorney's basis becomes spouse-based: the spouse's questions are asked, and the "not asked" line goes
    elig = s.page.locator("details.qgroup").filter(has_text="Eligibility")
    if elig.get_attribute("open") is None:
        elig.locator("summary").click()
    elig.locator("article.qcard", has_text="Basis of eligibility").locator("select").select_option("Spouse of U.S. citizen")
    elig.get_by_role("button", name="Save this group").click()
    s.toast()
    s.settle()
    body = s.page.locator("#main").inner_text()
    assert "Not asked: basis is" not in body and s.page.locator("details.qgroup").filter(has_text="The spouse").count() == 1
    s.check("fourth_n400_spouse_asked")


def test_the_release_notes_are_under_the_version_number(paralegal):
    import version

    _settings(paralegal)
    notes = paralegal.page.locator("#releases")
    notes.wait_for()
    text = notes.inner_text()
    assert f"What changed in {version.VERSION}" in text and "What changed in 2026.10.4" in text
    assert f"Software version {version.VERSION}, released 10/03/2026." in paralegal.page.locator("#case").inner_text()
    assert notes.locator("details").first.get_attribute("open") is not None  # the newest is open
    paralegal.check("fourth_release_notes")


def test_a_turned_off_persons_open_page_clears_itself(browser, world, attorney):
    """Turned off while her page shows the client list: within a minute the page asks whether she is still signed in, and when she
    is not every name goes and the sign-in screen shows. The page's clock is run forward a minute instead of waiting."""
    from review.auth import Accounts

    accounts = Accounts(world["users"])
    who = ("lee.exemplo@example.com", "Lee Exemplo", "lee-exemplo-pass-1")
    accounts.add(who[0], who[1], "paralegal")
    accounts._set_password(who[0], who[2], must_change=False)
    s = Screen(browser, world, who)
    try:
        s.page.clock.install()  # the page loads again under the test's clock (its minute timer then runs on it)
        s.page.goto("about:blank")
        s.page.goto(world["review"] + "#all")
        s.page.wait_for_selector("#client-rows")
        s.settle(300)
        assert "Exemplo" in s.page.locator("#client-rows").inner_text()
        accounts.update(who[0], by=attorney.who[0], active=False)  # as Turn off does: her sessions end at once
        s.page.clock.fast_forward("01:05")
        s.page.wait_for_selector("input[name=password]", timeout=15000)
        body = s.page.locator("body").inner_text()
        assert "Sign in" in body and "Ana Clara" not in body and "case-" not in body and s.page.locator("#client option").count() == 0
        assert not s.page.locator("#settings").is_visible()  # nothing behind Settings for someone signed out
        s.check("fourth_turned_off_cleared")
    finally:
        s.close()
