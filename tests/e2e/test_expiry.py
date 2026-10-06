"""What's due, "Expiring documents" (src/expiry.py), and the Reports page (src/review/reports.py), on the made-up world.

Each case gets the document records a processed folder would have (the shape src/documents.py writes), dated from today:
a green card running out (a resident), a passport and a police clearance in a consular case, and a driver's license in an
asylum case (a protected one: for an attorney only).
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

import pytest


def _record(doc_id, type_, **kw):
    return {"id": doc_id, "files": [f"{doc_id}.pdf"], "doc_ids": [f"{doc_id}.pdf"], "pages": [1], "type": type_, "confidence": 0.9, "person": "applicant", "person_set_by": None,
            "language": "en", "issued": None, "expires": None, "identifiers": {"a_number": "", "receipt": "", "passport": "", "ssn_last4": ""}, "quality": "readable",
            "hash": doc_id * 4, "source": "folder", "added": "2026-09-30T10:00:00+00:00", "roles": [], "tags": [], "confidential": None, "text": "", "translated": None} | kw


def _write(world, case, docs):
    (world["clients"] / case / "documents.json").write_text(json.dumps({"version": 1, "built": "2026-10-01T02:00:00+00:00", "documents": docs}), encoding="utf-8")


def _classified(world, case, doc_type):
    """The file of that kind the case's folder already holds (meta.json): a seeded record stands for it, since documents.load builds a
    record for every classified file the records leave out, and the newest document of a kind for a person is the one listed."""
    meta = json.loads((world["clients"] / case / "meta.json").read_text(encoding="utf-8"))
    return next(d for d, k in (meta.get("classifications") or {}).items() if k == doc_type)


def _seed(world):
    today = date.today()
    in_days = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
    passport = _classified(world, "case-consular", "passport")
    _write(world, "case-resident", [_record("gc01", "green_card", issued="2017-03-01", expires=in_days(90))])
    _write(world, "case-consular", [_record("pp01", "passport", files=[passport], doc_ids=[passport], expires=in_days(25)),
                                    _record("pc01", "police_clearance", issued=(today - timedelta(days=730 - 30)).isoformat())])  # two years: 30 days left
    _write(world, "case-asylum", [_record("dl01", "drivers_license", expires=in_days(10))])


@pytest.fixture(autouse=True)
def restore_the_world(world):
    """The world is shared by every browser file in a session: put each case's document record back as it was, so another file's
    saved searches (the police clearances older than two years, say) never find what these tests invented."""
    files = {p: p.read_bytes() for p in world["clients"].glob("*/documents.json")}
    yield
    for p in world["clients"].glob("*/documents.json"):
        if p not in files:
            p.unlink()
    for p, data in files.items():
        p.write_bytes(data)  # a new modification time, so every cached row and index entry is built again


def _open_the_tab(screen):
    screen.page.goto("about:blank")
    screen.page.goto(screen.world["review"] + "#deadlines")
    screen.page.wait_for_selector("#due-tab-expiring")
    screen.settle(300)
    screen.page.locator("#due-tab-expiring").click()
    screen.page.wait_for_selector("#expiring-count")
    screen.settle(300)


def test_the_expiring_documents_tab_lists_what_runs_out_with_its_rule_and_the_filing_to_open(world, attorney):
    _seed(world)
    _open_the_tab(attorney)
    body = attorney.check("expiring_documents")
    assert "Expiring documents" in body and re.search(r"\d+ documents? in the next 90 days or already expired", body)
    rows = attorney.page.locator("#expiring-rows tr", has_text="Green card")
    assert rows.count() == 1
    text = rows.inner_text()
    assert "Green card (permanent resident card) expires" in text and "Form I-90" in text and "in 90 days" in text and "Nobody yet" in text
    assert re.search(r"\d\d/\d\d/\d{4}", text) and not re.search(r"\d{4}-\d{2}-\d{2}", text) and "green_card" not in text
    rows.locator("summary", has_text="Why this date").click()
    assert "I-90 Instructions" in rows.inner_text() and "read 10/02/2026" in rows.inner_text()  # the rule and where it was read
    assert attorney.page.locator("#expiring-rows tr", has_text="Passport").count() == 1 and "consular processing" in attorney.page.locator("#expiring-rows").inner_text()
    assert "Police clearance" in attorney.page.locator("#expiring-rows").inner_text()

    rows.get_by_role("button", name=re.compile(r"I-90 application")).click()  # the filing it opens, on the case
    attorney.page.wait_for_selector("#case .who h1")
    attorney.settle()
    assert attorney.page.url.endswith("#case-resident") and "filing=i90" in attorney.page.url and "tab=packet" in attorney.page.url
    assert not attorney.errors


def test_the_horizon_and_the_person_responsible_filter_the_list(world, attorney):
    _seed(world)
    _open_the_tab(attorney)
    attorney.page.get_by_label("How far ahead").select_option(label="Next 30 days")
    attorney.page.wait_for_function("() => document.getElementById('expiring-count').innerText.includes('30 days')")
    attorney.settle(300)
    table = attorney.page.locator("#expiring-rows").inner_text()
    assert "Passport" in table and "Green card" not in table  # 25 days against 90
    attorney.page.get_by_label("Person responsible").select_option(label="Nobody yet")
    attorney.page.wait_for_function("() => document.getElementById('expiring-count').innerText.length > 0")
    attorney.settle(300)
    attorney.check("expiring_documents_filtered")
    assert "Passport" in attorney.page.locator("#expiring-rows").inner_text()


def test_a_protected_cases_documents_are_for_an_attorney_only(world, attorney, paralegal):
    import restricted
    import world as w

    _seed(world)
    asylum = world["clients"] / "case-asylum"  # an asylum case: restricted (src/restricted.py); another file may have named the paralegal on it
    restricted.name_person(asylum, w.PARALEGAL[0], False, w.ATTORNEY[1], "attorney")
    _open_the_tab(attorney)
    assert "Driver's license" in attorney.page.locator("#expiring-rows").inner_text()
    _open_the_tab(paralegal)
    body = paralegal.check("expiring_documents_paralegal")
    # not named on the case: it is in no list and no count for her
    assert "Driver's license" not in paralegal.page.locator("#expiring-rows").inner_text() and "in protected cases" not in body
    restricted.name_person(asylum, w.PARALEGAL[0], True, w.ATTORNEY[1], "attorney", w.PARALEGAL[1])
    _open_the_tab(paralegal)
    assert "Driver's license" in paralegal.page.locator("#expiring-rows").inner_text()  # named on it: hers to see


def test_whats_due_still_opens_on_the_deadlines_and_the_documents_are_in_them_too(world, attorney):
    _seed(world)
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#deadlines")
    attorney.page.wait_for_selector("#due-tab-deadlines")
    attorney.settle(300)
    body = attorney.check("whats_due_deadlines")
    assert attorney.page.locator("#due-tab-deadlines").get_attribute("aria-pressed") == "true"
    assert "Passport expires" in body and "needed for consular processing" in body  # the radar's deadline is on the ordinary list (inside 60 days)


def test_a_paralegal_reads_a_date_off_a_document_and_the_radar_starts_watching_it(world, paralegal):
    _seed(world)
    _write(world, "case-family", [_record("ap01", "advance_parole")])  # no reader reads an advance parole document's end date
    paralegal.open("case-family", "documents")
    row = paralegal.page.locator("#documents tr", has_text="Advance parole")
    row.locator("summary", has_text="Set the dates").click()
    row.get_by_label(re.compile("ends", re.I)).fill((date.today() + timedelta(days=60)).isoformat())
    row.get_by_role("button", name="Save the dates").click()
    assert paralegal.toast() == "Saved."
    paralegal.settle()
    assert "Set by Paulo Paralegal" in paralegal.page.locator("#documents tr", has_text="Advance parole").inner_text()
    _open_the_tab(paralegal)
    rows = paralegal.page.locator("#expiring-rows tr", has_text="Advance parole")
    assert rows.count() == 1 and "in 60 days" in rows.inner_text() and "I-131 application" in rows.inner_text()
    assert paralegal.check("expiring_documents_set_by_hand")


def test_the_reports_page_counts_the_cases_and_a_table_downloads_as_csv(world, attorney):
    _seed(world)
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#reports")
    attorney.page.wait_for_selector("#report-stages")
    attorney.settle(300)
    body = attorney.check("reports")
    for words in ("Cases by stage", "Cases by filing", "Cases by office", "Cases by reviewer", "Documents by type", "Documents by scan quality", "Last night's run", "Every case"):
        assert words in body, words
    assert "Paralegal review" in attorney.page.locator("#report-stages").inner_text() and "Download CSV" in body
    assert not re.search(r"\bfees?\b|billing|invoice|\$", body, re.I)  # counts and lists only
    assert re.search(r"\d\d/\d\d/\d{4}", attorney.page.locator("#report-cases").inner_text())  # dates as MM/DD/YYYY
    with attorney.page.expect_download() as download:
        attorney.page.locator("#csv-stages").click()
    assert download.value.suggested_filename == "report-stages.csv"
    first = open(download.value.path(), encoding="utf-8-sig").read().splitlines()[0]
    assert first == "Stage,Cases,Clients"
    assert not attorney.errors


def test_a_paralegal_reaches_the_reports_from_keeping_current(world, paralegal):
    _seed(world)
    paralegal.page.goto("about:blank")
    paralegal.page.goto(world["review"] + "#all")
    paralegal.page.wait_for_selector("#client-rows")
    paralegal.page.get_by_role("button", name="Keeping current").click()
    paralegal.page.get_by_role("button", name="Reports").click()
    paralegal.page.wait_for_selector("#report-stages")
    paralegal.settle(300)
    body = paralegal.check("reports_paralegal")
    assert "Reports" in body and "Documents by type" in body
