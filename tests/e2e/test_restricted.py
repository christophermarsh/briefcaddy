"""Staff and restricted cases, in the browser: the Settings page's Staff section, and a case the attorney restricts that the
paralegal cannot find until the attorney names her on it (src/restricted.py). Everyone here is made up.
"""

from __future__ import annotations


def _settings(screen):
    screen.page.goto("about:blank")
    screen.page.goto(screen.world["review"] + "#settings")
    screen.page.wait_for_selector("#set-firm, .setsec")
    screen.settle(400)


def test_the_attorney_manages_staff_on_the_settings_page(world, attorney, paralegal, browser):
    _settings(attorney)
    attorney.page.wait_for_selector("#set-staff table#staff-rows")
    body = attorney.check("settings_staff")
    staff = attorney.page.locator("#set-staff").inner_text()
    assert "Staff" in body and "Paulo Paralegal" in staff and "Ana Attorney" in staff and "Another attorney changes your own account" in staff
    attorney.page.fill("input[aria-label=\"New person's full name\"]", "Kim Exemplo")
    attorney.page.fill("input[aria-label=\"New person's work email\"]", "kim@example.com")
    attorney.page.get_by_role("button", name="Add the person").click()
    attorney.page.wait_for_selector("#one-time")
    shown = attorney.page.locator("#one-time").inner_text()
    assert "One-time password for kim@example.com" in shown and "Shown once" in shown
    attorney.check("settings_staff_added")
    row = attorney.page.locator("#staff-rows tr", has_text="Kim Exemplo")
    assert "Has not signed in yet" in row.inner_text()
    row.get_by_role("button", name="Turn off").click()  # the confirmation is accepted
    attorney.page.wait_for_function("() => document.querySelector('#staff-rows').innerText.includes('Turned off')")
    assert attorney.page.locator("#one-time").count() == 0  # the password is gone from the screen with the next change
    attorney.check("settings_staff_turned_off")

    _settings(paralegal)
    assert paralegal.page.locator("#set-staff").count() == 0  # the attorney's
    paralegal.check("settings_paralegal")

    ctx = browser.new_context(viewport={"width": 1400, "height": 900})  # signed out: whom to ask, no reset by email
    page = ctx.new_page()
    page.goto(world["review"])
    page.wait_for_selector("input[name=email]")
    assert "Forgot your password? Ask your attorney for a reset." in page.locator("#main form").inner_text()
    ctx.close()


def test_a_restricted_case_is_invisible_to_the_paralegal_until_the_attorney_names_her(world, attorney, paralegal):
    import world as w

    attorney.open("case-family", "done")
    attorney.page.wait_for_selector("#access")
    attorney.page.fill("textarea[aria-label='Why this case is restricted']", "The client is a minor.")
    attorney.page.get_by_role("button", name="Restrict this case").click()
    attorney.page.wait_for_selector("#restricted-banner")
    assert "The case is restricted" in attorney.toast()
    banner = attorney.page.locator("#restricted-banner").inner_text()
    assert "Restricted case" in banner and "Attorneys may open it" in banner and "messages are sent by hand" in banner
    attorney.check("restricted_case_attorney")

    paralegal.page.goto("about:blank")
    paralegal.page.goto(world["review"] + "#all")
    paralegal.page.wait_for_selector("#client-rows")
    paralegal.settle()
    assert "case-family" not in paralegal.page.locator("#client-rows").inner_text()
    assert paralegal.page.locator("#client option[value='case-family']").count() == 0
    paralegal.check("restricted_case_paralegal_all_clients")
    paralegal.open("case-family", "journey")  # a link to it: the case does not open, the client list does
    assert "case-family" not in paralegal.page.url or paralegal.page.locator("#case h1").inner_text() == "All clients"

    attorney.open("case-family", "done")
    attorney.page.wait_for_selector("#access select")
    attorney.page.select_option("select[aria-label='Staff member to name on the case']", w.PARALEGAL[0])
    attorney.page.get_by_role("button", name="Let them open it").click()
    attorney.page.wait_for_function("() => document.querySelector('#access').innerText.includes('named by')")
    assert "Paulo Paralegal" in attorney.page.locator("#restricted-banner").inner_text()
    attorney.check("restricted_case_named")

    paralegal.open("case-family", "journey")
    paralegal.page.wait_for_selector("#restricted-banner")
    assert "Paulo Paralegal" in paralegal.page.locator("#restricted-banner").inner_text()
    assert paralegal.page.locator("#access").count() == 0  # the controls are the attorney's
    paralegal.check("restricted_case_paralegal_named")
    paralegal.page.goto("about:blank")
    paralegal.page.goto(world["review"] + "#all")
    paralegal.page.wait_for_selector("#client-rows")
    paralegal.settle()
    assert "case-family" in paralegal.page.locator("#client-rows").inner_text()

    # put the world back for the other files: take her off, lift the restriction
    attorney.open("case-family", "done")
    attorney.page.wait_for_selector("#access")
    attorney.page.locator("#access li", has_text="Paulo Paralegal").get_by_role("button", name="Take off").click()
    attorney.page.wait_for_function("() => !document.querySelector('#access').innerText.includes('named by')")
    attorney.page.get_by_role("button", name="Lift the restriction").click()
    attorney.page.wait_for_function("() => !document.getElementById('restricted-banner')")
    attorney.check("restricted_case_lifted")
