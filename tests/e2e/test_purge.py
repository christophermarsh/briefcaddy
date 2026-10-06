"""The purge of a case, in the browser (src/purge.py): on the Agreement and closing tab the attorney records the attempt to reach the client,
reviews the originals, asks to purge with a reason, sees "to be purged on" on the case and on All clients, and cancels; the paralegal sees
nothing of it, on the case or on any list; Settings has the offices' retention rules for the attorney alone. Everyone here is made up.
"""

from __future__ import annotations

import pytest

CASE = "case-purge"


@pytest.fixture
def purge_case(world):
    world["world"].clone(world["root"], "demo-ana", CASE)
    yield CASE


def test_the_attorney_takes_the_steps_asks_and_cancels_and_the_paralegal_sees_nothing(world, attorney, paralegal, purge_case):
    attorney.open(CASE, "engagement")
    attorney.page.wait_for_selector("#purge #purge-contact")
    assert attorney.page.locator("#purge-ask").is_disabled()  # the two steps first
    attorney.page.select_option("#purge-how", "phone")
    attorney.page.fill("#purge-contact-on", "2026-10-01")
    attorney.page.fill("#purge-contact-note", "Left a message about the file")
    attorney.page.click("#purge-contact")
    attorney.page.wait_for_selector("text=Called the client on 10/01/2026", state="attached")
    attorney.page.check("#purge-none-held")
    attorney.page.click("#purge-originals-save")
    attorney.page.wait_for_function("() => !document.getElementById('purge-ask').disabled")
    attorney.page.fill("#purge-reason", "The client asked the office to delete the file")
    attorney.page.click("#purge-ask")
    attorney.page.wait_for_selector("#purge-waiting")
    text = attorney.check("purge_waiting")
    assert "To be purged on" in text and "Cancel the purge" in text and "What the purge deletes" in text

    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"])
    attorney.page.wait_for_selector(f"tr[data-client='{CASE}'] .purge-on")
    assert "To be purged on" in attorney.page.locator(f"tr[data-client='{CASE}']").inner_text()

    paralegal.open(CASE, "engagement")
    paralegal.page.wait_for_timeout(500)
    assert paralegal.page.locator("#purge").count() == 0 and "purge" not in paralegal.text().lower()
    paralegal.page.goto("about:blank")
    paralegal.page.goto(world["review"])
    paralegal.settle()
    assert paralegal.page.locator(".purge-on").count() == 0 and "to be purged" not in paralegal.page.locator("body").inner_text().lower()

    attorney.open(CASE, "engagement")
    attorney.page.wait_for_selector("#purge-cancel")
    attorney.page.click("#purge-cancel")
    attorney.page.wait_for_selector("#purge-ask")
    assert attorney.page.locator("#purge-waiting").count() == 0 and not attorney.errors


def test_settings_shows_the_offices_retention_rules_to_the_attorney_alone(world, attorney, paralegal):
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#settings")
    attorney.page.wait_for_selector("#set-retention .ret-office")
    text = attorney.page.locator("#set-retention").inner_text()
    assert "Keeping closed files and purging" in text and ("Proposed, not confirmed" in text or "Confirmed" in text) and "Days a purge waits" in text
    attorney.check("purge_settings")
    paralegal.page.goto("about:blank")
    paralegal.page.goto(world["review"] + "#settings")
    paralegal.page.wait_for_selector("#main .setsec")
    paralegal.settle()
    assert paralegal.page.locator("#set-retention").count() == 0
