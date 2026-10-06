"""Find across the firm, in the browser (src/find.py, the Search page): an attorney's switch, a question in plain words, twenty passages at most,
each with where it is and why it matched, one click to the case at that page, a confidential document never a hit for a paralegal not named
on its case, and the attorney's one click that switches it off. Everyone here is made up.
"""

from __future__ import annotations

import json

from conftest import paralegal_not_named_on
from test_search import _record, _write

COURT = "Notice to Appear. The respondent crossed the river near the bridge at night and was detained by the border patrol for three days."
SECRET = "The applicant hid in the church basement for three nights after the threats from her former partner."


def _switch(screen, on: bool) -> None:
    r = screen.page.request.post(screen.world["review"].rstrip("/") + "/api/settings", headers={"X-Review-App": "1", "Content-Type": "application/json"},
                                 data=json.dumps({"section": "drafting", "values": {"find_across": "on" if on else "off"}, "reviewer": "Sam Attorney"}))
    assert r.status == 200, r.text()


def _seed(world, attorney) -> None:
    _write(world, "case-court", [_record("n1", "nta", "nta.pdf", COURT)])
    _write(world, "case-sij", [_record("s1", "affidavit", "sij-order.pdf", SECRET, confidential="1367")])
    import find

    find.rebuild_all(world["clients"])  # the index as the night's run leaves it (the review app's follower keeps it current from there)


def _ask(screen, question: str) -> str:
    screen.page.goto("about:blank")
    screen.page.goto(screen.world["review"] + "#search")
    screen.page.wait_for_selector("#find-question")
    screen.page.fill("#find-question", question)
    screen.page.press("#find-question", "Enter")
    screen.page.wait_for_selector("#find-count")
    screen.settle(300)
    return screen.page.locator("#find-across").inner_text()


def test_a_question_finds_the_passage_says_why_and_opens_the_case_at_the_page(world, attorney):
    _seed(world, attorney)
    _switch(attorney, True)
    try:
        panel = _ask(attorney, "detained by the border patrol near the river")
        attorney.check("find_across_hits")
        row = attorney.page.locator("#find-hits tr", has_text="border patrol")
        assert row.count() >= 1 and "page 1" in row.first.inner_text() and "words in common" in row.first.inner_text()
        assert "Who asked what" in panel  # the attorney sees the questions, with who asked them
        assert row.first.locator("a", has_text="Open at page 1").count() == 1
        row.first.get_by_role("button").first.click()  # one click: the case, at its documents
        attorney.page.wait_for_selector("#case .who h1")
        attorney.settle()
        assert attorney.page.url.endswith("#case-court") and not attorney.errors
    finally:
        _switch(attorney, False)


def test_a_confidential_passage_is_never_a_hit_for_a_paralegal_and_the_attorney_switches_it_off_in_one_click(world, paralegal, attorney):
    _seed(world, attorney)
    _switch(attorney, True)
    try:
        with paralegal_not_named_on(world, "case-sij"):
            panel = _ask(paralegal, "hid in the church basement after the threats")
            paralegal.check("find_across_paralegal")
            assert paralegal.page.locator("#find-hits").count() == 0 and "three nights" not in panel and "former partner" not in panel
            assert "Nothing close enough" in panel and "Who asked what" not in panel and "Switch Find across the firm off" not in panel
        panel = _ask(attorney, "hid in the church basement after the threats")
        assert "church basement for three nights" in panel
        attorney.page.get_by_role("button", name="Switch Find across the firm off").click()
        attorney.page.wait_for_selector("#find-off-note")
        attorney.check("find_across_off")
        assert "Off." in attorney.page.locator("#find-across").inner_text()
    finally:
        _switch(attorney, False)
