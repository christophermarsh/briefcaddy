"""My approvals, in the browser (src/approvals.py): the attorney opens the one screen that holds everything only an attorney may approve, approves one Part 14
explanation and confirms one review card from it, and finds both done on the case, recorded as the case page records them; the paralegal has no link to it and is
refused when she asks for it. Everyone here is made up (the demo client, cloned). E2E_SHOTS keeps a screenshot of each step."""

from __future__ import annotations

import pytest

CASE = "case-queue"


@pytest.fixture(scope="module")
def queue_case(world):
    import world as w

    d = w.clone(world["root"], "case-court", CASE)
    (d / "status.json").unlink(missing_ok=True)  # the court case as the world made it
    w.add_fact(d, "applicant.part9.in_removal_proceedings", "Yes", "nta.pdf", "notice_to_appear")
    w.add_notice(d, "IOE0912345678", "I-360", "approval", "2025-05-01")
    return d


def _open_queue(screen) -> None:
    """From All clients, the way a person gets there: the My approvals button (My work says the same, in one line, with a button)."""
    screen.page.locator("#client").select_option("")
    screen.page.locator("#approvals-btn").click()
    screen.page.wait_for_selector("#approval-rows")
    screen.settle()


def _row(screen, kind: str):
    return screen.page.locator(f'tr[data-approval^="{kind}|{CASE}|"]').first


def test_the_attorney_approves_an_explanation_and_confirms_a_card_from_one_screen_and_the_paralegal_cannot_open_it(world, queue_case, attorney, paralegal):
    page = attorney.page
    attorney.open(CASE, "journey")
    _open_queue(attorney)
    text = attorney.check("approvals-1-start")
    assert "My approvals" in text and "waiting, the oldest from" in text and "Each button does what the same button does on the case page" in text
    for words in ("Review cards that need an attorney", "Part 14 explanations", "Practices waiting for approval", "G-28 office switches"):
        assert words in text  # the kinds come from the server's registry
    assert "Raised by anyone" in text

    # the Part 14 explanation the paralegal's draft waits on: the reason, who and since when, and its own buttons
    page.locator('button[data-kind-filter="part14"]').click()
    attorney.settle()
    explain = _row(attorney, "part14")
    explain.wait_for()
    words = explain.inner_text()
    assert "Part 9, item 14" in words and "waits for an attorney's approval" in words and "Waiting since" in words and "Raised by" in words
    assert "Approve" in words and "Ask the paralegal" in words and "Open the case here" in words
    explain.locator('button[data-action="approve"]').click()
    attorney.toast()
    page.wait_for_function("(s) => !document.querySelector(s)", arg=f'tr[data-approval^="part14|{CASE}|"]', timeout=30000)
    attorney.settle()

    # a review card that needs the attorney: confirmed from the queue
    page.locator('button[data-kind-filter="part14"]').click()  # the filter off again
    attorney.settle()
    page.locator('button[data-kind-filter="card"]').click()
    attorney.settle()
    card = page.locator('tr[data-approval^="card|"]:has(button[data-action="confirm"])').first
    card.wait_for()
    row_id = card.get_attribute("data-approval")
    case = row_id.split("|")[1]
    card.locator('button[data-action="confirm"]').click()
    attorney.toast()
    page.wait_for_function("(id) => !document.querySelector(`tr[data-approval=\"${id}\"]`)", arg=row_id, timeout=30000)
    attorney.settle()
    attorney.check("approvals-2-done")

    # both are on their cases, as the case page records them: the explanation approved, and the card confirmed, by the attorney
    attorney.open(CASE, "explain")
    attorney.when_it_says("Approved by")
    assert "Approved by" in attorney.text()
    attorney.open(case, "done")
    log = attorney.text()
    assert "Ana Attorney" in log and "Confirmed" in log

    # the paralegal has no link, and the route refuses her
    para = paralegal.page
    para.locator("#client").select_option("")
    para.wait_for_selector("#client-rows")
    assert para.locator("#approvals-btn").count() == 0 and "My approvals" not in paralegal.text()
    status = para.evaluate("fetch('/api/approvals').then((r) => r.status)")
    assert status == 403
