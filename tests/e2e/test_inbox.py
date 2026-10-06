"""The notice inbox in the browser (src/inbox.py, My work's "Read the inbox now" and the Inbox page).

A made-up stranger's I-797 is scanned into the inbox; reading it leaves it waiting (no case has its receipt number or
name), and the paralegal places it on a case from the "this belongs to" picker.
"""

from __future__ import annotations

import json
from pathlib import Path

STRANGER = ["Department of Homeland Security", "U.S. Citizenship and Immigration Services", "I-797C, Notice of Action",
            "EXEMPLO: DEMONSTRATION DOCUMENT", "Receipt Number Case Type", "IOE0999100099 I765 - APPLICATION FOR EMPLOYMENT AUTHORIZATION",
            "Received Date Priority Date Applicant", "09/29/2026 ESTRANHO, JOAO EXEMPLO", "Notice Date Page", "09/29/2026 1 of 1",
            "Notice Type: Receipt Notice", "Receipt Notice"]


def test_a_waiting_notice_is_placed_from_the_picker(world, paralegal):
    from portal.demo import document_pdf

    box = Path(world["env"]["I485_INBOX"])
    box.mkdir(parents=True, exist_ok=True)
    (box / "morning mail.pdf").write_bytes(document_pdf(STRANGER))
    s = paralegal
    s.page.goto("about:blank")
    s.page.goto(world["review"] + "#work")
    s.page.wait_for_selector("#inbox-bar")
    s.settle(300)
    assert "0 notices waiting for a person, 1 scan not read yet" in s.page.locator("#inbox-count").inner_text()
    s.check("inbox_my_work")

    s.page.locator("#inbox-bar").get_by_role("button", name="Read the inbox now").click()
    s.page.wait_for_selector("tr.inbox-waiting")
    s.settle(300)
    body = s.check("inbox_waiting")
    assert "I-765 receipt notice (receipt IOE0999100099), notice of 09/29/2026" in body
    assert "Name on the notice: ESTRANHO, JOAO EXEMPLO" in body and "No case has this receipt number" in body
    row = s.page.locator("tr.inbox-waiting").first
    assert row.get_by_role("link", name="Open the scan").get_attribute("href").startswith("/api/inbox/file?id=")

    row.get_by_role("searchbox", name="Find the case").fill("case-court")
    s.page.wait_for_function("() => [...document.querySelectorAll('tr.inbox-waiting select option')].some((o) => o.value === 'case-court')")
    row.get_by_role("combobox", name="This belongs to").select_option("case-court")
    row.get_by_role("button", name="Place on this case").click()
    s.toast()
    s.page.wait_for_function("() => document.body.innerText.includes('Nothing waiting')")
    body = s.check("inbox_placed")
    recent = s.page.locator("#inbox-recent").inner_text()
    assert "Placed by Paulo Paralegal" in recent and "case-court" in recent

    meta = json.loads((world["clients"] / "case-court" / "meta.json").read_text(encoding="utf-8"))
    placed = [n for n in meta["classifications"] if n.startswith("inbox_") and n.endswith("morning_mail.pdf")]
    assert placed and (Path(meta["source_folder"]) / placed[0]).exists()
    assert not list((box / "waiting").iterdir())

    s.page.locator("#inbox-recent").get_by_role("button", name="Open").first.click()  # the case, at where it stands
    s.page.wait_for_selector("#case .who h1")
    s.settle()
    assert s.page.url.endswith("#case-court")
    assert not s.errors


def test_a_court_hearing_notice_becomes_a_hearing_the_paralegal_confirms(world, paralegal):
    """The made-up world's cases are copies of one client (one A-Number, one name): the court's notice waits, is placed
    on the court case, and its hearing is confirmed there against the notice."""
    from datetime import date, timedelta

    from portal.demo import document_pdf

    when = date.today() + timedelta(days=60)
    court = ["UNITED STATES DEPARTMENT OF JUSTICE", "EXECUTIVE OFFICE FOR IMMIGRATION REVIEW", "IMMIGRATION COURT", "EXEMPLO: DEMONSTRATION DOCUMENT",
             "RE: EXEMPLO SOUZA, ANA CLARA", "FILE: A099-000-123", "NOTICE OF IN-PERSON HEARING IN REMOVAL PROCEEDINGS",
             "Your case has been scheduled for a MASTER hearing before the Immigration Judge",
             f"on {when.strftime('%b')} {when.day}, {when.year} at 9:00 A.M. at:", "1 EXAMPLE PLAZA, ROOM 100", "BOSTON, MA 02110"]
    import restricted
    import world as w

    # the world's cases share one A-Number, so the asylum case (restricted, src/restricted.py) is among the notice's possible
    # cases: the attorney has named this paralegal on it, or the notice would wait nameless for an attorney
    restricted.name_person(world["clients"] / "case-asylum", w.PARALEGAL[0], True, w.ATTORNEY[1], "attorney", w.PARALEGAL[1])
    box = Path(world["env"]["I485_INBOX"])
    box.mkdir(parents=True, exist_ok=True)
    (box / "court.pdf").write_bytes(document_pdf(court))
    s = paralegal
    s.page.goto("about:blank")
    s.page.goto(world["review"] + "#inbox")
    s.page.wait_for_selector("#inbox-waiting")
    s.settle(300)
    s.page.get_by_role("button", name="Read the inbox now").click()
    row = s.page.locator("tr.inbox-waiting", has_text="Immigration court hearing notice")
    row.wait_for()
    assert f"master calendar hearing on {when.strftime('%m/%d/%Y')} at 9:00 AM" in row.inner_text()
    assert "cases have this A-Number" in row.inner_text() and row.locator("select option").count() > 1  # the cases offered first
    row.get_by_role("searchbox", name="Find the case").fill("case-court")  # another test may have changed case-court's documents
    s.page.wait_for_function("() => [...document.querySelectorAll('tr.inbox-waiting select option')].some((o) => o.value === 'case-court')")
    row.get_by_role("combobox", name="This belongs to").select_option("case-court")
    row.get_by_role("button", name="Place on this case").click()
    s.toast()
    s.page.locator("#inbox-recent", has_text="confirm it against the notice").wait_for()

    s.open("case-court", "journey")
    body = s.check("inbox_hearing_to_confirm")
    assert "Read from the court's hearing notice: check the date, time and place against the notice" in body
    assert "A hearing was read from the court's notice" in body  # the step, on the case's list
    s.page.get_by_role("button", name="Confirm", exact=True).click()
    s.page.wait_for_function("() => document.body.innerText.includes('confirmed by Paulo Paralegal')")
    s.check("inbox_hearing_confirmed")
    status = json.loads((world["clients"] / "case-court" / "status.json").read_text(encoding="utf-8"))
    (h,) = [x for x in status["journey"]["hearings"] if x.get("source")]
    assert h["date"] == when.isoformat() and h["confirmed"]["by"] == "Paulo Paralegal"
