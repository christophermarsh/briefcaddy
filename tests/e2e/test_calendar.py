"""Deadlines that reach a calendar (src/calendar_feed.py, src/deadlines_set.py, src/closures.py), on the made-up world: Settings, My calendar (the address
shown once, then renewed), Add a deadline on the case page, and the month view on What's due."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from datetime import date

import pytest


@pytest.fixture(autouse=True)
def clean(world):
    """Each test starts with no deadline a person set and no closure the firm added."""
    import json
    from pathlib import Path

    def wipe():
        for p in world["clients"].glob("*/deadlines_set.json"):
            p.unlink()
        saved = Path(world["env"]["I485_SETTINGS"])  # the world's own settings file, which the server reads
        if saved.exists():
            data = json.loads(saved.read_text(encoding="utf-8"))
            if data.pop("closures", None) is not None:
                saved.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    wipe()
    yield
    wipe()


def fetch(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def open_settings(screen) -> None:
    screen.page.goto("about:blank")
    screen.page.goto(screen.world["review"] + "#settings")
    screen.page.wait_for_selector("#set-calendar")
    screen.settle(300)


def test_my_calendar_address_is_shown_once_and_a_new_one_turns_the_old_one_off(world, paralegal):
    open_settings(paralegal)
    section = paralegal.page.locator("#set-calendar")
    if paralegal.page.locator("#cal-off-person").count():  # an earlier run made one: turn it off, then begin
        paralegal.page.locator("#cal-off-person").click()
        paralegal.toast()
        paralegal.settle(300)
    assert "No address yet." in section.inner_text() and "How to subscribe" in section.inner_text() and not paralegal.page.locator("#cal-firm").count()  # the firm's is the attorney's
    paralegal.page.locator("#cal-make-person").click()
    paralegal.toast()
    box = paralegal.page.locator("#cal-address-person")
    box.wait_for()
    first = box.input_value()
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+/calendar/[A-Za-z0-9_-]{43}\.ics", first)
    assert "shown only this once" in section.inner_text()
    status, text = fetch(first)  # a calendar program has no sign-in: the address is the credential
    assert status == 200 and text.startswith("BEGIN:VCALENDAR") and "PRODID" in text
    paralegal.check("my_calendar_made")
    open_settings(paralegal)  # a visit later: the address is not there any more
    assert not paralegal.page.locator("#cal-address-person").count() and re.search(r"Made \d\d/\d\d/\d{4}\. It is not shown again\.", paralegal.page.locator("#set-calendar").inner_text())
    assert first not in paralegal.page.content()
    paralegal.page.locator("#cal-make-person").click()  # "Make a new address": the old one stops working at once
    paralegal.toast()
    second = paralegal.page.locator("#cal-address-person").input_value()
    assert second != first and fetch(second)[0] == 200
    status, body = fetch(first)
    assert status == 404 and body == fetch("http://127.0.0.1:" + first.split(":")[2].split("/")[0] + "/calendar/" + "x" * 43 + ".ics")[1]  # the answer an unknown address gets
    paralegal.check("my_calendar_renewed")


def test_an_attorney_also_has_the_firms_address_and_the_reminders_switch(world, attorney):
    open_settings(attorney)
    assert attorney.page.locator("#cal-firm").count() == 1 and "Every deadline, hearing and appointment of every case" in attorney.page.locator("#cal-firm").inner_text()
    attorney.page.locator("#cal-make-firm").click()
    attorney.toast()
    address = attorney.page.locator("#cal-address-firm").input_value()
    assert fetch(address)[0] == 200
    box = attorney.page.locator("#cal-reminders")
    on = box.is_checked()
    box.click()
    attorney.toast()
    attorney.settle(300)
    assert attorney.page.locator("#cal-reminders").is_checked() is not on  # kept
    attorney.page.locator("#cal-reminders").click()
    attorney.toast()


def test_add_a_deadline_name_who_is_responsible_and_mark_it_done(world, attorney):
    attorney.open("case-sij", "journey")
    assert attorney.page.locator("#case-deadlines").count() == 1
    attorney.page.locator("#add-deadline summary").click()
    attorney.page.fill("#add-deadline-title", "Send the client the interview checklist")
    attorney.page.fill("#add-deadline-date", date.today().isoformat())
    attorney.page.select_option("#add-deadline-who", label="Paulo Paralegal")
    attorney.page.fill("#add-deadline-note", "Use the new template")
    attorney.page.locator("#add-deadline-save").click()
    assert attorney.toast() == "Deadline added."
    attorney.settle(300)
    row = attorney.page.locator("#case-deadline-rows tr", has_text="Send the client the interview checklist")
    assert row.count() == 1 and "Added by Ana Attorney. Use the new template" in row.inner_text() and "today" in row.inner_text()
    assert row.locator("select.who-pick").evaluate("s => s.options[s.selectedIndex].text") == "Paulo Paralegal"
    attorney.check("add_a_deadline")
    # the product's own deadline (the 21st birthday) gets a person responsible by an assignment
    own = attorney.page.locator("#case-deadline-rows tr", has_text="before the 21st birthday")
    own.locator("select.who-pick").select_option(label="Ana Attorney")
    assert attorney.toast() == "Person responsible saved."
    attorney.settle(300)
    assert attorney.page.locator("#case-deadline-rows tr", has_text="before the 21st birthday").locator("select.who-pick").evaluate("s => s.options[s.selectedIndex].text") == "Ana Attorney"
    # it shows in What's due, with the person's name
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#deadlines")
    attorney.page.wait_for_selector("#due-tab-deadlines")
    attorney.settle(300)
    assert re.search(r"Send the client the interview checklist.*Paulo Paralegal", attorney.page.locator("#main").inner_text(), re.S)
    # done: gone from the list, kept under Done with who
    attorney.open("case-sij", "journey")
    attorney.page.locator("#case-deadline-rows tr", has_text="Send the client the interview checklist").get_by_role("button", name="Done").click()
    assert attorney.toast() == "Marked done."
    attorney.settle(300)
    assert not attorney.page.locator("#case-deadline-rows tr", has_text="Send the client the interview checklist").count()
    attorney.page.locator("#done-deadlines summary").click()
    assert "Send the client the interview checklist" in attorney.page.locator("#done-deadlines").inner_text() and "Ana Attorney" in attorney.page.locator("#done-deadlines").inner_text()
    attorney.check("deadline_done")


def month_tab(screen) -> None:
    screen.page.goto("about:blank")
    screen.page.goto(screen.world["review"] + "#deadlines")
    screen.page.wait_for_selector("#due-tab-month")
    screen.settle(300)
    screen.page.locator("#due-tab-month").click()
    screen.page.wait_for_selector("#month-grid")
    screen.settle(300)


def test_the_month_view_marks_closures_lists_a_day_in_full_and_says_what_it_could_not_read(world, attorney):
    import deadlines_set

    today = date.today()
    deadlines_set.add(world["clients"] / "case-sij", "Month view deadline", today.isoformat(), "", "A note", "Ana Attorney",
                      [{"email": "attorney@example.com", "name": "Ana Attorney", "role": "attorney"}])
    month_tab(attorney)
    grid = attorney.page.locator("#month-grid")
    assert [t.lower() for t in grid.locator(".mh").all_inner_texts()] == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # Monday first
    assert attorney.page.locator("#month-label").inner_text() == today.strftime("%B %Y")
    attorney.page.locator("#month-scope-firm").click()
    attorney.page.wait_for_selector("#month-grid")
    attorney.settle(300)
    cell = attorney.page.locator(f"#month-day-{today.isoformat()}")
    assert "today" in (cell.get_attribute("class") or "") and "Month view deadline" in cell.inner_text()
    cell.click()  # a day click lists everything on it, in full
    detail = attorney.page.locator("#month-detail")
    assert "Month view deadline" in detail.inner_text() and re.search(r"\d\d/\d\d/\d{4}", detail.inner_text()) and detail.get_by_role("button", name="Open").count() == 1
    # the closures, from the file: Columbus Day and Thanksgiving; Massachusetts' list was not read, and the screen says so
    attorney.page.evaluate("() => { DUE.month = '2026-10'; }")
    attorney.page.locator("#due-tab-month").click()
    attorney.page.wait_for_selector("#month-grid")
    attorney.settle(300)
    assert attorney.page.locator("#month-label").inner_text() == "October 2026"
    columbus = attorney.page.locator("#month-day-2026-10-12")
    assert "Columbus Day" in columbus.inner_text()
    columbus.click()
    assert "Closed: Columbus Day (Federal holiday)" in attorney.page.locator("#month-detail").inner_text() and "Nothing due on this day." in attorney.page.locator("#month-detail").inner_text()
    note = attorney.page.locator(".month-note").inner_text()
    assert "Massachusetts court closures are not listed here: the official page could not be read. Add them under Settings, Court closures the firm adds." in note
    attorney.page.locator("#month-next").click()
    attorney.page.wait_for_function("() => document.getElementById('month-label').innerText === 'November 2026'")
    assert "Thanksgiving Day" in attorney.page.locator("#month-day-2026-11-26").inner_text() and "Friday after Thanksgiving" in attorney.page.locator("#month-day-2026-11-27").inner_text()
    attorney.page.locator("#month-sources summary").click()
    assert "not read" in attorney.page.locator("#month-sources").inner_text() and "copied from the official page on 10/03/2026" in attorney.page.locator("#month-sources").inner_text()
    attorney.check("month_view")


def test_a_closure_the_firm_adds_under_settings_is_marked_on_the_month(world, attorney):
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#settings:closures")
    attorney.page.wait_for_selector("#set-closures textarea")
    attorney.settle(300)
    attorney.page.fill("#set-closures textarea", "10/20/2026 Courthouse closed for a training day")
    attorney.page.locator("#set-closures").get_by_role("button", name="Save").click()
    attorney.toast()
    month_tab(attorney)
    attorney.page.evaluate("() => { DUE.month = '2026-10'; }")
    attorney.page.locator("#due-tab-month").click()
    attorney.page.wait_for_selector("#month-grid")
    attorney.settle(300)
    assert "Courthouse closed for a training day" in attorney.page.locator("#month-day-2026-10-20").inner_text()
    # a line that is not a date is said in words
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#settings:closures")
    attorney.page.wait_for_selector("#set-closures textarea")
    attorney.page.fill("#set-closures textarea", "next Tuesday closed")
    attorney.page.locator("#set-closures").get_by_role("button", name="Save").click()
    assert "start with the date as MM/DD/YYYY" in attorney.toast(ok=False)
