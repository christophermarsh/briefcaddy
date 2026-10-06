"""The office's clock in the browser (src/clock.py): a paralegal on a laptop in another time zone still sees the office's
dates and times in the review app; a client sees their own phone's time in the portal, and the page names the office's
zone when it differs. A moment stored in UTC at 8:05 PM in Boston is 10/02, never 10/03.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import clock


@pytest.fixture(scope="module")
def showcase(world):
    """Beatriz (src/portal/demo.py showcase), with an appointment and a thread with the office; removed afterwards."""
    from portal import demo
    from portal.store import PortalStore

    store = PortalStore(world["portal"])
    built = demo.showcase(store, world["clients"], process=True)
    yield store, built
    demo.reset(store, world["clients"], demo.SHOWCASE_ID)


def _shot(page, name: str) -> None:
    if os.environ.get("E2E_SHOTS"):
        Path(os.environ["E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(os.environ["E2E_SHOTS"]) / f"clock-{name}.png"), full_page=True)


def _signed_in(browser, world, zone: str):
    import world as w

    ctx = browser.new_context(viewport={"width": 1400, "height": 900}, timezone_id=zone)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(world["review"])
    page.fill("input[name=email]", w.PARALEGAL[0])
    page.fill("input[name=password]", w.PARALEGAL[2])
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_selector("#client", state="visible")
    return ctx, page, errors


def test_a_laptop_in_tokyo_still_sees_the_offices_dates(browser, world):
    ctx, page, errors = _signed_in(browser, world, "Asia/Tokyo")
    try:
        assert page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone") == "Asia/Tokyo"
        assert page.evaluate("ME.time_zone") == "America/New_York"  # the Settings page's zone (the default: Eastern)
        assert page.evaluate("usDay('2026-10-03T00:05:00+00:00')") == "10/02/2026"  # Tokyo's own date would be 10/03
        assert page.evaluate("usDayTime('2026-10-03T00:05:00+00:00')") == "10/02/2026 8:05 PM"
        assert page.evaluate("usDay('2026-10-03T00:05:00')") == "10/02/2026"  # an old stamp without an offset: UTC
        assert page.evaluate("usDay('2026-10-03')") == "10/03/2026"  # a plain date is never shifted
        assert page.evaluate("mdyTime('2026-10-15T23:30:00-04:00')") == "10/15/2026 11:30 PM"
        assert page.evaluate("officeToday()") == clock.today().isoformat()  # the "Date mailed" box starts on the office's day
        assert not errors, errors
    finally:
        ctx.close()


def test_the_settings_page_offers_the_time_zone(browser, world):
    ctx, page, errors = _signed_in(browser, world, "America/New_York")
    try:
        page.goto("about:blank")
        page.goto(world["review"] + "#settings")
        page.wait_for_selector("#set-firm", state="visible")
        field = page.locator("#set-firm label.setfield", has_text="Time zone")
        assert field.locator("select option:checked").inner_text() == "Eastern (New York, Boston, Miami)"
        assert "Every date, deadline and fee change follows this clock" in field.inner_text()
        field.scroll_into_view_if_needed()
        _shot(page, "settings-time-zone")
        assert not errors, errors
    finally:
        ctx.close()


def _portal(browser, world, store, zone: str):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="pt-BR", has_touch=True, timezone_id=zone)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{world['portal_url']}/l/{store.new_link_token('demo-bia')}")
    page.wait_for_load_state("networkidle")
    return ctx, page, errors


def test_the_portal_names_the_offices_zone_when_the_phone_is_elsewhere(browser, world, showcase):
    store, _ = showcase
    ctx, page, errors = _portal(browser, world, store, "America/Los_Angeles")
    try:
        body = page.locator("body").inner_text()
        _shot(page, "portal-welcome-los-angeles")
        assert "O horário do compromisso é o horário local do lugar do compromisso (como na carta). Fuso horário do escritório:" in body
        page.get_by_role("button", name="Falar com o escritório").first.click()
        page.locator("#layer .msgbox").wait_for(state="visible")
        assert "Os horários mostrados são os do escritório (" in page.locator("#layer").inner_text()
        _shot(page, "portal-thread-los-angeles")
        assert not errors, errors
    finally:
        ctx.close()


def test_the_portal_says_nothing_when_the_phone_keeps_the_offices_time(browser, world, showcase):
    store, _ = showcase
    ctx, page, errors = _portal(browser, world, store, "America/New_York")
    try:
        assert "Fuso horário do escritório" not in page.locator("body").inner_text()
        assert not errors, errors
    finally:
        ctx.close()
