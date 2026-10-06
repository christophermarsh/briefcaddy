"""What the attorney can see of what the office did, in the browser (src/review/oversight.py): "What staff did", "Who viewed this" in full,
"Who viewed what", the restricted cases that send automatic messages, and the firm policies' record and way back.
Everyone here is made up; a paralegal sees none of it.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import clock

TODAY = clock.us_date(clock.stamp())
HEADERS = {"X-Review-App": "1", "Content-Type": "application/json"}


def _settings(s, world, focus=""):
    s.page.goto("about:blank")
    s.page.goto(world["review"] + "#settings" + (":" + focus if focus else ""))
    s.page.wait_for_selector(".setsec")
    s.settle(400)


def _fold(s, selector):
    s.page.locator(selector + " > summary").click()
    s.settle(300)


def _rows(response) -> list[dict]:
    return list(csv.DictReader(io.StringIO(response.body().decode("utf-8-sig"))))


def _post(s, world, path, body):
    r = s.page.request.post(world["review"].rstrip("/") + path, headers=HEADERS, data=json.dumps(body))
    assert r.status == 200, (path, r.status, r.text())
    return r.json()


def test_the_attorney_reads_what_staff_did_and_the_paralegal_cannot(world, attorney, paralegal):
    paralegal.page.request.post(world["review"].rstrip("/") + "/api/login", headers=HEADERS,
                                data=json.dumps({"email": "nobody@example.com", "password": "a wrong password"}))  # a failed sign-in, from this computer
    _settings(attorney, world, "staff")
    attorney.page.wait_for_selector("#set-staff table#staff-rows")
    _fold(attorney, "#staff-did")
    attorney.page.wait_for_selector("#staff-did-log table.rows")
    text = attorney.page.locator("#staff-did-log").inner_text()
    assert "Signed in" in text and "Ana Attorney" in text and "127.0.0.1" in text
    assert attorney.page.locator("#staff-did-log .pager .count").inner_text().startswith("1 to ")
    attorney.check("oversight_staff_did")
    log = attorney.page.locator("#staff-did-log")
    log.get_by_label("Kind", exact=True).select_option("failed")
    attorney.page.wait_for_function("() => document.querySelector('#staff-did-log').innerText.includes('Tried an email that is not on the staff list')")
    filtered = attorney.page.locator("#staff-did-log").inner_text()
    assert "nobody@example.com" in filtered and "Signed in" not in filtered
    log.get_by_label("Kind", exact=True).select_option("")
    log.get_by_label("Person", exact=True).select_option("attorney@example.com")
    attorney.page.wait_for_function("() => { const a = document.querySelector('#staff-did-log-csv'); return !!a && new URL(a.href).searchParams.get('person') === 'attorney@example.com'; }")  # the log has been read for this person
    attorney.page.wait_for_load_state("networkidle")  # (the reads for the earlier choices have all been answered: the last answer drawn is this one's)
    assert "Ana Attorney" in attorney.page.locator("#staff-did-log").inner_text()
    log.get_by_label("From", exact=True).fill("2020-01-01")
    log.get_by_label("To", exact=True).fill("2020-01-02")
    attorney.page.wait_for_selector("#staff-did-log .empty")
    assert "Nothing matches these filters." in attorney.page.locator("#staff-did-log").inner_text()
    log.get_by_label("From", exact=True).fill("")
    log.get_by_label("To", exact=True).fill("")
    # the download link is rewritten when the log has been read again for the cleared dates (src/review/static/index.html pagedLog): read before that it still carries the two
    # dates and the file is empty. Wait for the link itself, not a pause
    attorney.page.wait_for_function("""() => { const a = document.querySelector('#staff-did-log-csv'); if (!a) return false;
        const p = new URL(a.href).searchParams; return !p.get('from') && !p.get('to') && p.get('person') === 'attorney@example.com' && !!document.querySelector('#staff-did-log table.rows'); }""")
    href =attorney.page.locator("#staff-did-log-csv").get_attribute("href")
    csv_file = attorney.page.request.get(world["review"].rstrip("/") + href)  # the file for the person chosen, every row of theirs
    assert csv_file.status == 200 and "attachment" in csv_file.headers["content-disposition"]
    rows = _rows(csv_file)
    assert rows and "attorney@example.com" in {r["Email"] for r in rows} and {r["Email"] for r in rows} <= {"", "attorney@example.com"} and set(rows[0]) == {"When", "Who", "Email", "Kind", "What", "Case", "Restricted case", "From"}
    attorney.check("oversight_staff_did_filtered")

    _settings(paralegal, world)
    assert paralegal.page.locator("#set-staff").count() == 0 and paralegal.page.locator("#staff-did").count() == 0
    for route in ("/api/staff_log", "/api/staff_log.csv", "/api/views", "/api/views.csv", "/api/messages_on", "/api/policy_changes", "/api/policy_changes.csv"):
        r = paralegal.page.request.get(world["review"].rstrip("/") + route)
        assert r.status == 403 and "attorney" in r.text(), route
    paralegal.check("oversight_paralegal")


def _write_views(world, case, count):
    """Rows in the view log for one case from made-up people (the app's own openings are on top of them)."""
    path = Path(world["users"]).with_name("review_views.jsonl")
    start = datetime.now(timezone.utc) - timedelta(hours=3)
    rows = [{"at": (start + timedelta(minutes=i)).isoformat(), "email": "paralegal@example.com" if i % 2 else "attorney@example.com",
             "name": "Paulo Paralegal" if i % 2 else "Ana Attorney", "role": "paralegal" if i % 2 else "attorney", "client": case,
             "kind": "packet" if i % 3 == 0 else "filled_form", "file": f"form{i}.pdf", "address": "10.1.2.3"} for i in range(count)]
    with open(path, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))


def test_who_viewed_this_shows_the_latest_20_then_every_row_and_the_firm_wide_list(world, attorney):
    _write_views(world, "case-spouse", 70)
    attorney.open("case-spouse", "done")
    attorney.page.wait_for_selector("#viewed table.rows")
    assert attorney.page.locator("#viewed table.rows tr").count() == 21  # the header and the latest 20
    assert "The latest 20" in attorney.page.locator("#viewed").inner_text()
    attorney.check("oversight_viewed_latest")
    attorney.page.get_by_role("button", name="Show all").click()
    attorney.page.wait_for_selector("#viewed-log table.rows")
    assert attorney.page.locator("#viewed-log table.rows tr").count() == 51
    pager = attorney.page.locator("#viewed-log .pager").inner_text()
    assert pager.startswith("1 to 50 of ") and "Page 1 of 2" in pager
    attorney.page.locator("#viewed-log").get_by_role("button", name="Next").click()
    attorney.page.wait_for_function("() => document.querySelector('#viewed-log .pager').innerText.includes('Page 2 of 2')")
    attorney.page.locator("#viewed-log").get_by_label("Person", exact=True).select_option("paralegal@example.com")
    attorney.page.wait_for_function("() => !document.querySelector('#viewed-log table.rows').innerText.includes('Ana Attorney')")
    assert "Paulo Paralegal" in attorney.page.locator("#viewed-log").inner_text()
    attorney.check("oversight_viewed_all")
    href = attorney.page.locator("#viewed-log-csv").get_attribute("href")
    rows = _rows(attorney.page.request.get(world["review"].rstrip("/") + href))
    assert len(rows) >= 35 and {r["Email"] for r in rows} == {"paralegal@example.com"}
    attorney.page.get_by_role("button", name="Show only the latest 20").click()
    attorney.page.wait_for_selector("#viewed-all")

    _settings(attorney, world, "staff")
    _fold(attorney, "#staff-viewed")
    attorney.page.wait_for_selector("#firm-views table.rows")
    assert "case-spouse" in attorney.page.locator("#firm-views").inner_text()
    attorney.page.locator("#firm-views").get_by_label("Person", exact=True).select_option("paralegal@example.com")
    attorney.page.wait_for_function("() => !document.querySelector('#firm-views table.rows').innerText.includes('Ana Attorney')")
    attorney.check("oversight_viewed_firm")
    attorney.page.get_by_role("button", name="Totals by person and day").click()
    attorney.page.wait_for_selector("#firm-views-day table.rows")
    totals = attorney.page.locator("#firm-views-day table.rows").inner_text()
    assert TODAY in totals and "Paulo Paralegal" in totals and "Ana Attorney" in totals
    attorney.check("oversight_viewed_day")
    attorney.page.locator("#firm-views-day").get_by_label("Restricted cases only", exact=True).check()
    attorney.settle(300)
    href = attorney.page.locator("#firm-views-day-csv").get_attribute("href")
    assert "restricted=1" in href and "group=day" in href
    assert attorney.page.request.get(world["review"].rstrip("/") + href).status == 200


def test_restricted_cases_that_send_automatic_messages(world, attorney, paralegal):
    case = "case-daca"
    _post(attorney, world, "/api/access", {"client": case, "action": "mark", "reason": "The client is a minor.", "reviewer": "Ana Attorney"})
    try:
        _post(attorney, world, "/api/access", {"client": case, "action": "messages_on", "reason": "The client asked for texts.", "reviewer": "Ana Attorney"})
        attorney.page.goto("about:blank")
        attorney.page.goto(world["review"] + "#all")
        attorney.page.wait_for_selector("#client-rows")
        attorney.settle(300)
        chip = attorney.page.locator("#chip-messages")
        assert "Messages on (restricted)" in chip.inner_text() and "1" in chip.inner_text()
        assert attorney.page.locator("#client-rows tr.row").count() > 1
        chip.click()
        attorney.page.wait_for_function("() => document.querySelectorAll('#client-rows tr.row').length === 1")
        assert case in attorney.page.locator("#client-rows").inner_text()
        attorney.check("oversight_chip")
        paralegal.page.goto("about:blank")
        paralegal.page.goto(world["review"] + "#all")
        paralegal.page.wait_for_selector("#client-rows")
        paralegal.settle(300)
        assert paralegal.page.locator("#chip-messages").count() == 0  # the attorney's
        paralegal.check("oversight_chip_paralegal")

        _settings(attorney, world, "staff")
        _fold(attorney, "#staff-messages")
        attorney.page.wait_for_selector("#messages-on-rows")
        listed = attorney.page.locator("#messages-on-rows").inner_text()
        assert case in listed and "Ana Attorney" in listed and "The client asked for texts." in listed
        attorney.check("oversight_messages_on")
        attorney.page.locator("#messages-on-rows").get_by_role("button", name="Switch off").click()
        assert "Automatic messages are off" in attorney.toast()
        attorney.page.wait_for_function("() => document.querySelector('#staff-messages').innerText.includes('No restricted case sends automatic messages.')")
        attorney.check("oversight_messages_off")
        last = json.loads((Path(world["clients"]) / case / "access.json").read_text(encoding="utf-8"))["history"][-1]
        assert last["what"] == "Switched automatic messages off" and last["by"] == "Ana Attorney"
    finally:
        _post(attorney, world, "/api/access", {"client": case, "action": "unmark", "reviewer": "Ana Attorney"})  # as it was, for the other tests


def test_each_policy_shows_its_edits_and_goes_back_to_the_shipped_wording(world, attorney, paralegal):
    s = attorney
    _settings(s, world, "policies")
    s.page.wait_for_selector("#set-policies")
    card = s.page.locator("details.policy", has_text="Not a crewman")
    card.locator("summary").first.click()
    shipped = card.get_by_label("Words for Not a crewman").input_value()
    assert card.get_by_role("button", name="Back to the shipped wording").count() == 0  # nothing to go back from
    card.get_by_label("Words for Not a crewman").fill("The firm's own words: this client is not a crewman.")
    card.get_by_role("button", name="Save").click()
    assert s.toast().startswith("Saved.")
    s.settle(300)
    card = s.page.locator("details.policy", has_text="Not a crewman")
    record = card.locator("div.policy-record")
    assert "Every change to this policy (1)" in record.inner_text()
    record.get_by_role("button", name="Every change to this policy").click()
    text = record.inner_text()
    assert "Ana Attorney" in text and "Changed the words" in text and shipped in text and "The firm's own words" in text and "→" in text
    s.check("oversight_policy_record")

    _post(s, world, "/api/rules/approve", {"rule": "POLICY:NO-CREWMAN", "reviewer": "Ana Attorney"})  # approved as the firm edited it
    _settings(s, world, "policy-NO-CREWMAN")
    card = s.page.locator("details.policy", has_text="Not a crewman")
    assert f"Approved as edited by the firm on {TODAY}" in card.inner_text()
    s.check("oversight_policy_approved_as_edited")

    card.get_by_role("button", name="Back to the shipped wording").click()  # the confirmation is accepted
    assert "Back to the shipped wording" in s.toast()
    s.settle(300)
    card = s.page.locator("details.policy", has_text="Not a crewman")
    assert card.get_by_label("Words for Not a crewman").input_value() == shipped
    assert "Every change to this policy (2)" in card.locator("div.policy-record").inner_text()
    assert "Changed since approval" in card.inner_text() and card.get_by_role("button", name="Back to the shipped wording").count() == 0
    card.locator("div.policy-record").get_by_role("button", name="Every change to this policy").click()
    assert "Went back to the shipped wording" in card.locator("div.policy-record").inner_text()
    s.check("oversight_policy_reverted")

    _fold(s, "#policy-changes")
    s.page.wait_for_selector("#policy-changes-rows")
    every = s.page.locator("#policy-changes-rows").inner_text()
    assert "Not a crewman" in every and "Went back to the shipped wording" in every and "Changed the words" in every
    rows = _rows(s.page.request.get(world["review"].rstrip("/") + s.page.locator("#policy-changes-csv").get_attribute("href")))
    assert {"Not a crewman"} <= {r["Policy"] for r in rows} and {"Ana Attorney"} <= {r["Who"] for r in rows}
    s.check("oversight_policy_changes")
    stored = json.loads(Path(world["env"]["I485_POLICIES_FIRM"]).read_text(encoding="utf-8"))["edits"]["NO-CREWMAN"]
    assert [bool(r.get("reverted")) for r in stored] == [False, True]  # the history keeps what was undone

    _settings(paralegal, world, "policies")
    paralegal.page.wait_for_selector("#set-policies")
    assert paralegal.page.locator("#policy-changes").count() == 0 and paralegal.page.locator("div.policy-record").count() == 0
    paralegal.check("oversight_policy_paralegal")
