"""Search every document, in the browser (src/index.py, the top bar's Search box and the Search page).

The made-up world's cases get a document record each (the shape src/documents.py writes), with a few words of OCR text;
the review app builds its index from them on the first search.
"""

from __future__ import annotations

import json
import re

from conftest import wait_until_searchable

BAKERY = "Notice to Appear. Immigration Court, Boston. The respondent worked at Example Bakery and entered without inspection."
RESTRICTED = "Declaration of the applicant: the abuse began in 2024 and the police were called twice."


def _record(doc_id, type_, file, text, **kw):
    return {"id": doc_id, "files": [file], "pages": [1], "type": type_, "confidence": 0.9, "person": "applicant", "person_set_by": None,
            "language": "en", "issued": None, "expires": None, "identifiers": {"a_number": "", "receipt": "", "passport": "", "ssn_last4": ""},
            "quality": "readable", "hash": doc_id * 4, "source": "scan inbox", "added": "2026-09-30T10:00:00+00:00", "roles": [], "tags": [],
            "confidential": None, "text": text, "translated": None} | kw


def _write(world, case, docs):
    (world["clients"] / case / "documents.json").write_text(json.dumps({"version": 1, "built": "2026-10-01T02:00:00+00:00", "documents": docs}), encoding="utf-8")


def _seed(world, screen=None):
    _write(world, "case-court", [_record("n1", "nta", "nta.pdf", BAKERY)])
    _write(world, "case-sij", [_record("s1", "affidavit", "sij-order.pdf", RESTRICTED, confidential="1367")])
    if screen is not None:  # the index looks for new records at most once a minute: wait until the seeded ones are found
        wait_until_searchable(world, screen, "bakery")


def _search_from_the_top_bar(screen, words):
    screen.page.fill("#find-q", words)
    screen.page.press("#find-q", "Enter")
    screen.page.wait_for_selector("#search-count")
    screen.settle(300)


def test_a_word_in_a_document_finds_it_and_opens_the_case(world, attorney):
    _seed(world, attorney)
    _search_from_the_top_bar(attorney, "bakery")
    body = attorney.check("search_results")
    assert "Search every document" in body and "1 document found" in body
    row = attorney.page.locator("table.hits tr", has_text="Notice to Appear")
    assert row.count() == 1
    assert row.locator("mark").inner_text().lower() == "bakery"  # the match is highlighted
    link = row.locator("a", has_text="Open at page 1")
    assert re.search(r"/api/file\?client=case-court&doc=nta\.pdf#page=1$", link.get_attribute("href"))  # the existing document view, at the page
    pdf = attorney.page.request.get(attorney.world["review"].rstrip("/") + "/api/file?client=case-court&doc=nta.pdf")
    assert pdf.status == 200 and pdf.body().startswith(b"%PDF")

    row.get_by_role("button").first.click()  # the client's name opens the case
    attorney.page.wait_for_selector("#case .who h1")
    attorney.settle()
    assert attorney.page.url.endswith("#case-court") and "Search every document" not in attorney.text()
    assert not attorney.errors


def test_a_paralegal_is_told_how_many_restricted_documents_match_and_no_more(world, paralegal, attorney):
    _seed(world, attorney)
    _search_from_the_top_bar(paralegal, "abuse")
    body = paralegal.check("search_restricted_paralegal")
    assert "0 documents found" in body and "1 more in restricted documents" in body
    assert "abuse" not in paralegal.text().lower().replace("1 more in restricted documents", "") and paralegal.page.locator("table.hits").count() == 0

    _search_from_the_top_bar(attorney, "abuse")
    body = attorney.check("search_restricted_attorney")
    assert "1 document found" in body and "Restricted" in body and attorney.page.locator("table.hits mark").inner_text().lower() == "abuse"


def test_the_questions_the_firm_asks_are_buttons(world, attorney):
    _seed(world, attorney)
    attorney.page.goto("about:blank")  # a fresh load: the address differs only after the #
    attorney.page.goto(world["review"] + "#search")  # a link to the Search page
    attorney.page.wait_for_selector("#search-count")
    attorney.settle(300)
    labels = attorney.page.locator(".setnav button").all_inner_texts()
    assert labels == ["Work permits (EADs) expiring in 90 days", "Notice to Appear with no EOIR-28 filed", "Police clearances older than two years"]
    attorney.page.get_by_role("button", name="Notice to Appear with no EOIR-28 filed").click()
    attorney.page.wait_for_function("() => document.getElementById('search-count').innerText.includes('found')")
    body = attorney.check("search_saved")
    # at least the court case's notice: another file's test (test_more_filings.py, an asylum case in court) may have added a second one to this world
    assert re.search(r"\d+ documents? found", body) and "Notice to Appear" in body and "case-court" in body
    attorney.page.get_by_role("button", name="Police clearances older than two years").click()
    attorney.page.wait_for_function("() => document.getElementById('search-count').innerText.includes('read in')")  # no documents: said in words, with how many documents and cases were looked at
    attorney.check("search_saved_none")


def test_the_top_bar_box_opens_to_a_usable_width_without_widening_the_page(world, attorney):
    attorney.open("case-court", "packet")
    box = attorney.page.locator("#find-q")
    before = box.bounding_box()["width"]
    box.focus()
    attorney.page.wait_for_timeout(400)
    assert box.bounding_box()["width"] >= 230 > before
    attorney.page.keyboard.type("an A-Number or a long word")
    assert attorney.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 1
