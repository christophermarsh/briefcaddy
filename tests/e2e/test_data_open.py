"""The firm's data, open (brief H1), in the browser: "What changed on this case" on the case page, "What changed across the firm" under Settings, Staff, and
"Export the firm's data" on Keeping current. Everyone here is made up; a paralegal sees the case's fold (it is their own work) and none of the firm's.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

HEADERS = {"X-Review-App": "1", "Content-Type": "application/json"}


def _post(s, world, path, body):
    r = s.page.request.post(world["review"].rstrip("/") + path, headers=HEADERS, data=json.dumps(body))
    assert r.status == 200, (path, r.status, r.text())
    return r.json()


def _ledger(world) -> Path:
    """The world's ledger: one file a month beside the name the servers were started with (I485_EVENTS)."""
    base = Path(world["env"]["I485_EVENTS"])
    return base.with_name(f"{base.stem}-{datetime.now(timezone.utc):%Y-%m}{base.suffix}")


def _rows(response) -> list[dict]:
    return list(csv.DictReader(io.StringIO(response.body().decode("utf-8-sig"))))


def test_the_case_page_says_what_changed_on_the_case_and_a_paralegal_sees_it_too(world, attorney, paralegal):
    _post(attorney, world, "/api/office", {"client": "case-spouse", "office": "main"})  # a change a person made: the ledger names who
    attorney.open("case-spouse", "done")
    attorney.page.wait_for_selector("#case-changes table.rows")
    text = attorney.page.locator("#case-changes").inner_text()
    assert "What changed on this case" in text and "The latest 20" in text and "Ana Attorney" in text and "Attorney" in text
    assert "Chose the office for the case" in text and "The case's office" in text
    assert re.search(r"\d{2}/\d{2}/\d{4} \d{1,2}:\d{2} (AM|PM)", text), "MM/DD/YYYY and the office's time"
    for ugly in ("office.json", "events", "undefined", "null", "—", " -- "):
        assert ugly not in text, ugly
    attorney.check("data_open_case_changes")
    # the paralegal opens the same case: its fold is theirs to read (the route is gated by may_open like every route that opens one case)
    paralegal.open("case-spouse", "done")
    paralegal.page.wait_for_selector("#case-changes table.rows")
    assert "Chose the office for the case" in paralegal.page.locator("#case-changes").inner_text()
    paralegal.check("data_open_case_changes_paralegal")


def test_every_change_a_page_at_a_time_filtered_and_as_a_file(world, attorney):
    path = _ledger(world)
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    rows = [{"at": (start + timedelta(seconds=i)).isoformat(), "who": "Paulo Paralegal" if i % 2 else "Ana Attorney", "role": "paralegal" if i % 2 else "attorney", "via": "staff",
             "case": "case-resident", "kind": "decisions" if i % 3 else "documents", "version": 1, "action": "confirmed", "what": f"Confirmed: applicant thing {i}"} for i in range(60)]
    with open(path, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))
    attorney.open("case-resident", "done")
    attorney.page.wait_for_selector("#case-changes table.rows")
    assert attorney.page.locator("#case-changes table.rows tr").count() == 21  # the header and the latest 20
    attorney.page.locator("#changes-all").click()
    attorney.page.wait_for_selector("#changes-log table.rows")
    assert attorney.page.locator("#changes-log table.rows tr").count() == 51
    pager = attorney.page.locator("#changes-log .pager").inner_text()
    assert pager.startswith("1 to 50 of ") and "Page 1 of 2" in pager
    attorney.page.locator("#changes-log").get_by_role("button", name="Next").click()
    attorney.page.wait_for_function("() => document.querySelector('#changes-log .pager').innerText.includes('Page 2 of 2')")
    attorney.page.locator("#changes-log").get_by_label("Person", exact=True).select_option("Paulo Paralegal")
    attorney.page.wait_for_function("() => !document.querySelector('#changes-log table.rows').innerText.includes('Ana Attorney')")
    attorney.check("data_open_case_changes_all")
    href = attorney.page.locator("#changes-log-csv").get_attribute("href")
    got = _rows(attorney.page.request.get(world["review"].rstrip("/") + href))
    # every row is the paralegal's (the filter), and all 30 of this test's are in the file; other files' tests may have added decisions by the same person to this case
    ours = [r for r in got if r["What changed"].startswith("Confirmed: applicant thing")]
    assert len(ours) == 30 and {r["Who"] for r in got} == {"Paulo Paralegal"} and set(got[0]) == {"When", "Who", "Role", "Kind", "Action", "What changed", "Case", "Record version"}
    # across the firm: the same rows with their case, and a filter by kind
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#settings:staff")
    attorney.page.wait_for_selector("#set-staff table#staff-rows")
    attorney.page.locator("#staff-changes > summary").click()
    attorney.page.wait_for_selector("#firm-changes table.rows")
    text = attorney.page.locator("#firm-changes").inner_text()
    assert "Confirmed: applicant thing" in text and "case-resident" in text and "Review decisions and answers" in text
    firm = attorney.page.locator("#firm-changes")
    firm.get_by_label("Kind", exact=True).select_option("documents")
    attorney.page.wait_for_function("() => !document.querySelector('#firm-changes table.rows').innerText.includes('Review decisions and answers')")
    firm.get_by_label("From", exact=True).fill("2020-01-01")
    firm.get_by_label("To", exact=True).fill("2020-01-02")
    attorney.page.wait_for_selector("#firm-changes .empty")
    firm.get_by_label("From", exact=True).fill("")
    firm.get_by_label("To", exact=True).fill("")
    attorney.settle(300)
    attorney.check("data_open_firm_changes")
    href = attorney.page.locator("#firm-changes-csv").get_attribute("href")
    csv_file = attorney.page.request.get(world["review"].rstrip("/") + href)
    assert csv_file.status == 200 and "attachment" in csv_file.headers["content-disposition"] and {r["Kind"] for r in _rows(csv_file)} == {"Documents"}


def test_a_paralegal_cannot_read_what_changed_across_the_firm_or_export_it(world, paralegal):
    paralegal.page.goto("about:blank")
    paralegal.page.goto(world["review"] + "#settings")
    paralegal.page.wait_for_selector(".setsec")
    assert paralegal.page.locator("#staff-changes").count() == 0
    for route in ("/api/firm_events", "/api/firm_events.csv", "/api/export-firm", "/api/export-firm.zip?name=i485-firm-data-2026-10-03.zip"):
        r = paralegal.page.request.get(world["review"].rstrip("/") + route)
        assert r.status == 403 and "attorney" in r.text(), route
    assert paralegal.page.request.post(world["review"].rstrip("/") + "/api/export-firm", headers=HEADERS, data="{}").status == 403
    paralegal.page.goto(world["review"])
    paralegal.settle()
    paralegal.page.get_by_role("button", name=re.compile("Keeping current")).click()
    paralegal.settle()
    assert paralegal.page.locator("#export-firm").count() == 0
    paralegal.check("data_open_paralegal")


def test_the_attorney_exports_the_firms_data_from_keeping_current_and_downloads_it(world, attorney):
    attorney.page.goto(world["review"])
    attorney.settle()
    attorney.page.get_by_role("button", name=re.compile("Keeping current")).click()
    attorney.page.wait_for_selector("#export-firm")
    section = attorney.page.locator("#export-firm")
    text = section.inner_text()
    assert "Export the firm's data" in text and "restricted cases too" in text and "need none of our software" in text and "not encrypted" in text
    for ugly in ("export_firm", "events-", ".json", "undefined", "null", "—", " -- "):
        assert ugly not in text, ugly
    attorney.check("data_open_export_before")
    section.get_by_role("button", name="Export the firm's data").click()  # the page asks "Make the export now?": the screen accepts
    attorney.page.wait_for_selector("#export-done", timeout=180000)
    done = attorney.page.locator("#export-done").inner_text()
    assert done.startswith("Finished ") and re.search(r"\d+ files, \d+(\.\d)? (KB|MB|GB)\.", done) and "Download" in done
    attorney.check("data_open_export_done")
    href = attorney.page.locator("#export-done a").get_attribute("href")
    got = attorney.page.request.get(world["review"].rstrip("/") + href)
    body = got.body()
    assert got.status == 200 and got.headers["content-type"] == "application/zip" and "attachment" in got.headers["content-disposition"] and body[:2] == b"PK"
    import hashlib
    import zipfile

    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = set(z.namelist())
        assert {"README.txt", "data_dictionary.md", "manifest.json", "MANIFEST.md"} <= names and any(n.startswith("cases/case-asylum/") for n in names), "a restricted-by-law case is in it"
        manifest = json.loads(z.read("manifest.json"))
        assert all(hashlib.sha256(z.read(f["path"])).hexdigest() == f["sha256"] for f in manifest["files"])
        assert not any(n.endswith((".key", "auth.json", "deployment.json")) for n in names)
    # the earlier exports are listed and the page remembers them after a reload
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"])
    attorney.settle()
    attorney.page.get_by_role("button", name=re.compile("Keeping current")).click()
    attorney.page.wait_for_selector("#export-earlier")
    assert "Earlier exports (1)" in attorney.page.locator("#export-earlier").inner_text() or "Earlier exports" in attorney.page.locator("#export-earlier").inner_text()
    # who exported: the staff access log, in words
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#settings:staff")
    attorney.page.wait_for_selector("#set-staff table#staff-rows")
    attorney.page.locator("#staff-did > summary").click()
    attorney.page.wait_for_selector("#staff-did-log table.rows")
    attorney.page.locator("#staff-did-log").get_by_label("Kind", exact=True).select_option("export")
    attorney.page.wait_for_function("() => document.querySelector('#staff-did-log').innerText.includes('Exported all of the firm')")
    assert "Ana Attorney" in attorney.page.locator("#staff-did-log").inner_text()
