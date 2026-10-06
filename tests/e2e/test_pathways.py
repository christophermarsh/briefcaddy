"""Every kind of case, end to end, through the screens -- as the paralegal and the
attorney would work it, and as the client sees it in the portal:

  the case page says where the case is and which filing comes next -> the
  button opens that filing -> its questions are answered on screen -> the
  packet is built and opened -> the attorney records the mailing -> USCIS's
  receipt and approval arrive in the folder -> the case moves on and names the
  next filing.

The world is made up (tests/e2e/world.py). Run with E2E=1 (see conftest.py).
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

FILING_NAMES = {"i485": "I-485 packet", "i360": "I-360 petition (SIJ)", "family": "Family (I-130 + I-485)", "n400": "Citizenship (N-400)",
                "i589": "Asylum (I-589)"}


def next_filing(s, client: str, expect: str, label_has: str) -> None:
    """On the case page: the Next callout names the filing, and its button opens the packet tab on it."""
    s.open(client, "journey")
    body = s.check(f"{client}-journey")
    assert "Next:" in body and label_has in body, body[:1500]
    # the callout lists every filing that is next for the case, one line each with its own Open button: another file may have put a court filing
    # (or any other) first on the same case, so take the button on the line for this filing, never the first Open in the callout
    s.page.locator(".callout.info:has-text('Next:') > div > div").filter(has_text=label_has).get_by_role("button", name="Open").first.click()
    s.settle()
    assert s.page.url.endswith(f"#{client}")

    def landed() -> tuple[bool, str]:
        chosen = s.page.locator("#main button[aria-pressed=true]")
        more = s.page.locator("select[aria-label='More filings']").input_value()
        return (expect in FILING_NAMES and FILING_NAMES[expect] in chosen.inner_text()) or more == expect, more

    for _ in range(40):  # the packet tab selects the filing once the server answers: under load that lands after the first settle
        ok, more = landed()
        if ok:
            break
        s.page.wait_for_timeout(250)
    assert ok, (expect, more)


def build(s, name: str) -> str:
    s.page.get_by_role("button", name=re.compile("uild packet")).first.click()
    msg = s.toast()
    assert "Packet built" in msg, msg
    s.settle()
    body = s.check(name)
    assert "Last built" in body
    href = s.page.get_by_role("link", name=re.compile("Open packet")).get_attribute("href")
    r = s.page.request.get(s.world["review"].rstrip("/") + href)
    assert r.status == 200 and r.headers["content-type"] == "application/pdf" and r.body()[:5] == b"%PDF-"
    return body


def answer(s, label: str, value: str, kind: str = "auto") -> None:
    """One question in a filing's panel: the row with this label, its field, Save."""
    row = s.page.locator("tr, article.card").filter(has=s.page.get_by_role("button", name="Save")).filter(has_text=label).first  # a question row (or card, the N-400's), not a checklist line
    field = row.locator("select, input, textarea").first
    tag = field.evaluate("e => e.tagName")
    if tag == "SELECT":
        field.select_option(value)
    else:
        field.fill(value)
    row.get_by_role("button", name="Save").click()
    s.toast()
    s.settle()


def record_mailing(s, tracking: str = "9400111899223456789012") -> None:
    """The attorney's "Mailed it?" -- with a reason when checks failed (a test packet is always a draft)."""
    s.page.fill("input[aria-label='Tracking number'], input[aria-label='Confirmation number']", tracking)
    reason = s.page.locator("input[placeholder^='Reason to']")
    if reason.is_visible():
        reason.fill("E2E: made-up client")
    s.page.get_by_role("button", name=re.compile("Record the (mailing|filing)")).click()
    assert "recorded" in s.toast()
    s.settle()


def stage_is(s, client: str, stage_name: str, next_has: str | None = None) -> str:
    """The case page's heading names the stage (the chips below list every stage); Next names the filing."""
    s.open(client, "journey")
    body = s.check(f"{client}-journey-{stage_name[:20]}")
    heading = s.page.locator("#main section.group h3").first.inner_text()
    assert heading.startswith(stage_name), (heading, stage_name)
    if next_has:
        assert next_has in body
    return body


def no_sideways(page, name: str) -> None:
    """On a phone the page fits the screen: nothing scrolls sideways."""
    wide = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    assert wide <= 1, f"{name}: {wide}px wider than the phone's screen"


# -- the kinds of case ------------------------------------------------------------------------------------------------


def test_sij_from_the_court_order_to_the_green_card_packet(world, paralegal, attorney):
    w, d = world["world"], world["clients"] / "case-sij"
    next_filing(paralegal, "case-sij", "i360", "File the I-360 petition")
    body = paralegal.check("case-sij-i360-panel")
    assert "21st birthday" in body or "I-360" in body
    build(paralegal, "case-sij-i360-built")
    # the attorney mails it; the timeline says so
    attorney.open("case-sij", "packet")
    attorney.page.get_by_role("button", name=re.compile("^" + re.escape("I-360 petition (SIJ)"))).click()
    attorney.settle()
    record_mailing(attorney)
    body = stage_is(attorney, "case-sij", "I-360 filed with USCIS")
    assert "Mailed: " in body
    # USCIS answers: receipt, then approval -> the green card application is next
    w.add_notice(d, "IOE0999000401", "I-360", "receipt", "2026-09-02")
    stage_is(attorney, "case-sij", "I-360 filed with USCIS")
    w.add_notice(d, "IOE0999000401", "I-360", "approval", "2026-09-28")
    stage_is(attorney, "case-sij", "Green card application to prepare and file", "File the I-485 packet")


def test_family_petition_and_green_card_together(world, paralegal, attorney):
    w, d = world["world"], world["clients"] / "case-family"
    next_filing(paralegal, "case-family", "family", "File the family packet")
    paralegal.check("case-family-panel")
    build(paralegal, "case-family-built")
    attorney.open("case-family", "packet")
    record_mailing(attorney)
    stage_is(attorney, "case-family", "Family petition and green card application filed")
    w.add_notice(d, "IOE0999000501", "I-130", "receipt", "2026-09-05")
    w.add_notice(d, "IOE0999000502", "I-485", "receipt", "2026-09-05")
    w.add_notice(d, "IOE0999000502", "I-485", "approval", "2026-09-29")
    stage_is(attorney, "case-family", "Permanent resident", "Citizenship (N-400)")


def test_consular_processing_with_nvc(world, paralegal, attorney):
    next_filing(paralegal, "case-consular", "visa", "Immigrant visa")
    answer(paralegal, "NVC case number", "RIO2026999001")
    answer(paralegal, "Embassy or consulate", "Rio de Janeiro")
    answer(paralegal, "Interview date", "2026-12-03")
    body = paralegal.check("case-consular-panel")
    assert "RIO2026999001" in body or "Where it stands" in body
    build(paralegal, "case-consular-built")
    href = paralegal.page.locator("#main").get_by_role("link", name="Open", exact=True).first.get_attribute("href")
    assert "visa_sheet" in href
    r = paralegal.page.request.get(paralegal.world["review"].rstrip("/") + href)
    assert r.status == 200 and r.body()[:5] == b"%PDF-"
    body = stage_is(paralegal, "case-consular", "Immigrant visa: the National Visa Center and the consulate")
    assert "Immigrant visa interview" in body and "12/03/2026" in body
    # the online filing is recorded with its confirmation number, not a tracking number
    attorney.open("case-consular", "packet")
    assert attorney.page.locator("select[aria-label='Carrier']").input_value() == "Online"
    record_mailing(attorney, "AA0099887766")
    assert "Filed online" in stage_is(attorney, "case-consular", "Immigrant visa")
    # the interview went well: the visa, the entry -- a permanent resident from that day, citizenship next
    paralegal.open("case-consular", "packet", "visa")
    answer(paralegal, "Result of the interview", "Issued")
    answer(paralegal, "Date the client entered the U.S. on the visa", "2026-09-20")
    body = stage_is(paralegal, "case-consular", "Permanent resident", "Citizenship (N-400), from 06/22/2031")  # 5 years from entry, less 90 days
    assert "Entered the U.S. on the immigrant visa" in body


def test_asylum_to_asylee(world, paralegal, attorney):
    w, d = world["world"], world["clients"] / "case-asylum"
    w.name_paralegal(d)  # an asylum case is restricted (8 CFR 208.6, src/restricted.py): the attorney names the paralegal who works it
    next_filing(paralegal, "case-asylum", "i589", "File the asylum application")
    body = paralegal.check("case-asylum-panel")
    assert "1-year deadline" in body
    build(paralegal, "case-asylum-built")
    attorney.open("case-asylum", "packet")
    record_mailing(attorney)
    w.add_notice(d, "ZLA2690000601", "I-589", "receipt", "2026-09-08")
    stage_is(attorney, "case-asylum", "Asylum application filed with USCIS", "advance parole (I-131)")
    w.add_notice(d, "ZLA2690000601", "I-589", "approval", "2026-09-30")
    body = stage_is(attorney, "case-asylum", "Granted asylum (asylee)", "Green card as an asylee (I-485): from 09/30/2027")
    assert "refugee travel document" in body
    # the green card a year later: its own packet (8 CFR 209.2), the I-485 category from the grant
    paralegal.open("case-asylum", "packet", "asylee")
    body = paralegal.check("case-asylum-asylee-panel")
    assert "Part 2, 3.d: asylee or refugee" in body and "Too early" in body and "09/30/2027" in body, body[:1500]
    assert "never in the same envelope" in body
    build(paralegal, "case-asylum-asylee-built")


def test_resident_citizenship_card_and_conditions(world, paralegal, attorney):
    next_filing(paralegal, "case-resident", "n400", "Citizenship (N-400)")
    paralegal.check("case-resident-n400")
    build(paralegal, "case-resident-n400-built")
    # the green card renewal and the conditions on residence: from the More... list
    for filing, needed in (("i90", "Why a new card"), ("i751", "Date the 2-year card expires"), ("n600", "How the client became a citizen"),
                           ("i131", "Which document")):
        paralegal.page.select_option("select[aria-label='More filings']", filing)
        paralegal.settle()
        body = paralegal.check(f"case-resident-{filing}-panel")
        assert needed in body, (filing, body[:600])
        build(paralegal, f"case-resident-{filing}-built")
    paralegal.page.select_option("select[aria-label='More filings']", "i90")
    paralegal.settle()
    answer(paralegal, "Why a new card", "Expired or expires within 6 months")
    assert "Answered by Paulo Paralegal" in paralegal.check("case-resident-i90-answered")


def test_immigration_court_hearings_and_a_decision(world, paralegal, attorney):
    next_filing(paralegal, "case-court", "eoir28", "Appear in immigration court")
    body = paralegal.check("case-court-eoir28")
    assert "EOIR Payment Portal" in body
    build(paralegal, "case-court-eoir28-built")
    # a hearing from the notice; a decision afterwards
    paralegal.open("case-court", "journey")
    paralegal.page.fill("input[aria-label='Hearing date']", "2026-09-24")
    paralegal.page.select_option("select[aria-label='Kind of hearing']", "Individual (merits)")
    paralegal.page.fill("input[aria-label='Court']", "Boston Immigration Court")
    paralegal.page.get_by_role("button", name="Add hearing").click()
    paralegal.toast()
    paralegal.settle()
    paralegal.page.get_by_role("button", name="Record result").click()
    paralegal.page.select_option("select[aria-label='What happened']", "Decision: removal ordered or relief denied")
    paralegal.page.fill("input[aria-label='Decision date']", "2026-09-24")
    paralegal.page.get_by_role("button", name="Save result").click()
    paralegal.toast()
    paralegal.settle()
    body = paralegal.check("case-court-decision")
    assert "Appeal to the BIA" in body and "10/05/2026" in body and "RECEIVED by the Board" in body
    # the appeal itself: first on the case, the EOIR-26 and EOIR-27 from the hearing's result
    next_filing(paralegal, "case-court", "bia", "Appeal to the BIA (EOIR-26): received by the Board by 10/05/2026")
    body = paralegal.check("case-court-bia-panel")
    assert "Item 6: the reasons" in body and "$1,060" in body, body[:1500]
    hearing = paralegal.page.locator("tr").filter(has_text="Where the last hearing was").locator("input").first
    assert hearing.input_value() == "Boston Immigration Court"          # from the hearing on the case page
    build(paralegal, "case-court-bia-built")


def test_a_uscis_request_for_evidence_is_answered(world, attorney):
    w, d = world["world"], world["clients"] / "demo-ana"
    w.add_notice(d, "IOE0999000123", "I-360", "rfe", "2026-09-20")
    w.add_fact(d, "folder.notice.IOE0999000123.rfe_20260920.due", "2026-12-14", "notice-i-360-rfe-2026-09-20.pdf", "uscis_notice")
    attorney.open("demo-ana", "journey")
    body = attorney.check("demo-ana-rfe")
    assert "Answer USCIS's request" in body and "12/14/2026" in body
    box = attorney.page.locator("section", has_text="Answer USCIS's request").first
    box.locator("textarea").first.fill("A certified copy of the state court's order.")
    box.get_by_role("button", name=re.compile("build the response")).click()
    assert "Response built" in attorney.toast()
    attorney.settle()
    assert "Open the response" in attorney.check("demo-ana-rfe-built")


# -- who may do what ------------------------------------------------------------------------------------------------


def test_only_the_attorney_records_a_mailing_or_changes_the_stage(world, paralegal):
    paralegal.open("demo-ana", "packet")
    paralegal.page.get_by_role("button", name=re.compile("uild packet")).first.click()
    paralegal.toast()
    paralegal.settle()
    button = paralegal.page.get_by_role("button", name="Record the mailing")
    assert button.is_disabled() and "attorney" in (button.get_attribute("title") or "").lower()
    paralegal.open("demo-ana", "journey")
    paralegal.page.get_by_text("The folder is incomplete?").click()
    paralegal.page.select_option("select[aria-label='Set the stage']", index=3)
    paralegal.answer_dialogs = "E2E: testing who may"
    paralegal.page.get_by_role("button", name="Save stage").click()
    assert "attorney" in paralegal.toast(ok=False)


# -- the pages everyone starts from --------------------------------------------------------------------------------


@pytest.mark.parametrize("page", ["all", "work", "deadlines"])
def test_the_dashboards(world, attorney, page):
    attorney.page.goto("about:blank")
    attorney.page.goto(f"{world['review']}#{page}")
    attorney.settle(1500)
    body = attorney.check(f"dashboard-{page}")
    heading = {"all": "Every case", "work": "My work", "deadlines": "What's due"}[page]
    assert heading in body, body[:400]
    if page == "all":
        for client in ("case-sij", "case-family", "case-consular", "case-asylum", "case-resident", "case-court"):
            assert client in body
    if page == "work":
        assert "The whole firm" in body and "holds up" in body   # the Visa Bulletin setting: one line for the firm, not one per client


def test_the_client_sees_their_case_in_their_language(world, browser):
    import subprocess
    import sys

    from conftest import REPO

    link = subprocess.run([sys.executable, "src/portal/admin.py", "link", "demo-ana"], cwd=REPO, env=world["env"], capture_output=True, text=True).stdout.split()[0]
    d = world["clients"] / "demo-ana"
    import journey

    journey.mark(d, "hearing", "E2E", value={"date": "2026-11-12", "time": "9:00 AM", "kind": "Master calendar", "court": "Boston Immigration Court"})
    journey.push_to_portal(world["clients"], world["portal"], notify=False)
    ctx = browser.new_context(viewport={"width": 400, "height": 860})  # a phone
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(link)
    page.wait_for_load_state("networkidle")
    for lang, words in (("English", ["Your case", "Immigration court hearing"]), ("Español", ["Su caso", "Audiencia en la corte"]),
                        ("Português", ["O seu caso", "Audiência no tribunal de imigração"])):  # the portal keeps the choice: end on the client's own language
        page.locator("select").first.select_option(label=lang)
        page.locator("body", has_text=words[-1]).wait_for()  # the portal draws the page again in the chosen language: wait for its words, not a pause
        body = page.locator("body").inner_text()
        for word in words:
            assert word in body, (lang, word)
        assert not re.search(r"\bnull\b|\bundefined\b|Bring: Bring", body)
        no_sideways(page, f"portal-{lang}")
    assert not errors, errors
    ctx.close()


# -- the paralegal's everyday review, and the client's questionnaire -----------------------------------------------


def test_a_review_card_is_confirmed_logged_and_undone(world, paralegal):
    paralegal.open("case-family", "check")
    first = paralegal.page.get_by_role("button", name=re.compile("^(Confirm|Save)$")).first
    first.click()
    paralegal.toast()
    paralegal.open("case-family", "done")
    body = paralegal.check("case-family-decision-log")
    assert "Paulo Paralegal" in body
    paralegal.page.get_by_role("button", name=re.compile("Undo")).first.click()
    paralegal.settle()
    # marked undone, never deleted: the log keeps the decision and who reopened it (docs/design_plan.md Part 4.3)
    body = paralegal.check("case-family-decision-log-undone")
    reopened = paralegal.page.locator("section.group").filter(has_text="Last decision").inner_text()  # the table of what was undone (What changed on this case comes after it)
    assert "undone: open again" in body.lower() and "History (2 steps)" in reopened and reopened.count("Paulo Paralegal") >= 2  # who decided, who reopened


def test_a_document_moves_out_of_the_packet_and_back(world, paralegal):
    paralegal.open("case-resident", "packet", "n400")
    mover = paralegal.page.locator("select[aria-label^='Move Passport']").first
    mover.select_option("leave_out")
    paralegal.settle()
    # the packet is laid out again after the move: wait for it, not a fixed pause (slower late in a full run)
    paralegal.page.locator("section.group", has_text="Left out by the paralegal").wait_for(timeout=20000)
    out = paralegal.page.locator("section.group", has_text="Not in the packet")
    assert "passport" in out.inner_text().lower() and "Left out by the paralegal" in out.inner_text()
    paralegal.page.locator("select[aria-label^='Move Passport']").first.select_option("default")
    paralegal.settle()
    # laid out again after the move back too: wait for the label to go, not a fixed pause (it failed late in full runs)
    paralegal.page.locator("section.group", has_text="Left out by the paralegal").wait_for(state="hidden", timeout=20000)
    assert "Left out by the paralegal" not in paralegal.check("case-resident-moved-back")


def test_a_new_client_starts_the_questionnaire(world, browser):
    import subprocess
    import sys

    from conftest import REPO

    link = subprocess.run([sys.executable, "src/portal/admin.py", "link", "pilot-nova"], cwd=REPO, env=world["env"], capture_output=True, text=True).stdout.split()[0]
    ctx = browser.new_context(viewport={"width": 400, "height": 860})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(link)
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text()
    assert "Nova" in body and "Começar" in body, body[:400]
    page.get_by_role("button", name="Começar").click()
    page.wait_for_timeout(800)
    field = page.locator("main input[type=text], main input:not([type]), main textarea").first
    field.fill("Nova")
    page.get_by_role("button", name="Continuar").first.click()
    page.wait_for_timeout(800)
    body = page.locator("body").inner_text()
    assert not re.search(r"\bnull\b|\bundefined\b", body) and not errors, (errors, body[:300])
    no_sideways(page, "portal-questionnaire")
    ctx.close()



def test_a_resident_becomes_a_citizen(world, paralegal, attorney):
    w, d = world["world"], world["clients"] / "case-resident"
    paralegal.open("case-resident", "packet", "n400")
    build(paralegal, "case-resident-n400-for-mailing")
    attorney.open("case-resident", "packet", "n400")
    record_mailing(attorney)
    stage_is(attorney, "case-resident", "Citizenship application (N-400) filed with USCIS")
    w.add_notice(d, "IOE0999000701", "N-400", "receipt", "2026-09-10")
    w.add_notice(d, "IOE0999000701", "N-400", "approval", "2026-09-30")
    stage_is(attorney, "case-resident", "Citizenship approved: the oath ceremony")
    # a citizen only at the oath (INA 337): the attorney records the ceremony from Form N-445
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    attorney.page.fill("input[aria-label='Oath date']", yesterday)
    attorney.page.fill("input[aria-label='Oath place']", "USCIS Boston Field Office")
    attorney.page.get_by_role("button", name="Record the ceremony").click()
    attorney.toast()
    body = stage_is(attorney, "case-resident", "U.S. citizen")
    assert "Next:" not in body          # the end of the road: nothing left to file


def test_marriage_based_green_card_and_its_conditions(world, paralegal, attorney):
    w, d = world["world"], world["clients"] / "case-spouse"
    next_filing(paralegal, "case-spouse", "family", "File the family packet")
    build(paralegal, "case-spouse-built")
    attorney.open("case-spouse", "packet", "family")
    record_mailing(attorney)
    w.add_notice(d, "IOE0999000801", "I-485", "receipt", "2026-09-03")
    w.add_notice(d, "IOE0999000801", "I-485", "approval", "2026-09-29")  # married on 06/14/2025: under 2 years -- a 2-year card
    body = stage_is(attorney, "case-spouse", "Permanent resident",
                    "Remove the conditions on the 2-year green card (I-751): from 07/01/2028")
    assert "09/29/2028" in body          # the card's end: the I-751 deadline on the timeline
    assert "Citizenship (N-400), from 07/01/2029" in body   # married to a U.S. citizen: 3 years (INA 319(a)), less 90 days


def test_a_citizenship_client_gets_the_citizenship_questionnaire(world, browser):
    import subprocess
    import sys

    from conftest import REPO

    link = subprocess.run([sys.executable, "src/portal/admin.py", "link", "pilot-cidadao"], cwd=REPO, env=world["env"], capture_output=True, text=True).stdout.split()[0]
    ctx = browser.new_context(viewport={"width": 400, "height": 860})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(link)
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text()
    assert "solicitud de ciudadanía" in body and "Empezar" in body, body[:500]
    no_sideways(page, "portal-n400")
    assert not errors, errors
    ctx.close()


def test_a_denial_puts_the_motion_first(world, paralegal):
    w, d = world["world"], world["clients"] / "case-sij"
    w.add_notice(d, "IOE0999000901", "I-765", "receipt", "2026-06-01")
    w.add_notice(d, "IOE0999000901", "I-765", "denial", "2026-09-28")
    next_filing(paralegal, "case-sij", "i290b", "Motion or appeal on the denied I-765 (I-290B): by 10/28/2026")
    body = paralegal.check("case-sij-i290b-panel")
    assert "Office that decided" in body and "P.O. BOX 5510" not in body and "P.O. BOX 21100" in body, body[:1500]   # an I-765: Phoenix
    assert "$0" in body                                                                                              # SIJ: before adjusting
    answer(paralegal, "Appeal or motion (Part 2", "Appeal to the AAO: no brief or evidence")
    assert "can't be appealed" in paralegal.check("case-sij-i290b-appeal")                                          # USCIS's chart: an I-765
    answer(paralegal, "Appeal or motion (Part 2", "Motion to reopen")
    answer(paralegal, "Office that decided", "Boston (BOS)")
    body = build(paralegal, "case-sij-i290b-built")
    assert "can't be appealed" not in body
