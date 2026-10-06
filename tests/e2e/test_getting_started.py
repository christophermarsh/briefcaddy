"""The first day, in the browser: "Set up the first attorney" on a new installation, and the Getting started page.

A second review app is started on the same made-up world with an accounts file nobody is in, the way the installer leaves it. Nothing
here touches the world's own staff accounts except one new attorney who has not been shown Getting started yet.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import second_factor
from conftest import LEAK, REPO, _port, _wait, after_password, code_for, remembered

PASSWORD = "a long enough passphrase"  # secret-scan: allow (a made-up test password)


def _shot(page, name):
    if os.environ.get("E2E_SHOTS"):
        Path(os.environ["E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(os.environ["E2E_SHOTS"]) / f"{name}.png"), full_page=True)


def _sound(page, errors, name):
    body = page.locator("body").inner_text()
    assert not [line for line in body.splitlines() if LEAK.search(line)], name
    assert not re.search(r"is not a function|is not defined|Cannot read properties", body), name
    assert page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 1, f"{name}: sideways scrolling"
    assert not errors, errors


def _enrol(page, world, email):
    """An attorney's first sign-in sets the authenticator app up (review/auth.py): the key shown in letters becomes the app, the code
    for now confirms it, and the eight recovery codes are shown once. code_for is the shared helper that keeps each code to its own step."""
    page.wait_for_selector("#enrol-key")
    assert "Set up your authenticator app" in page.locator("h2").first.inner_text()
    world["secrets"][email] = second_factor.secret_from(page.locator("#enrol-key").inner_text())
    page.fill("input[name=code]", code_for(world, email))
    page.get_by_role("button", name="Confirm and continue").click()
    page.wait_for_selector("#recovery-codes")
    assert len(page.locator("#recovery-codes code").all()) == 8
    page.get_by_role("button", name="I've kept them: continue").click()


def _fresh_app(world, tmp_path):
    """A review app on the world's cases with an empty accounts file and a setup code, as the installer leaves it."""
    sys.path.insert(0, str(REPO / "src"))
    from review.auth import Accounts

    users = Accounts(tmp_path / "fresh_users.json")
    code = users.new_setup_code()
    port = _port()
    log = open(tmp_path / "fresh.log", "w")
    proc = subprocess.Popen([sys.executable, "src/review/server.py", "--data", str(world["clients"]), "--port", str(port), "--users", str(tmp_path / "fresh_users.json"),
                             "--portal", str(world["portal"])], cwd=REPO, env=world["env"], stdout=log, stderr=subprocess.STDOUT)
    _wait(f"http://127.0.0.1:{port}/")
    return proc, f"http://127.0.0.1:{port}/", code, users


def test_a_new_installation_asks_for_the_first_attorney_then_shows_the_first_day_and_never_asks_again(world, browser, tmp_path):
    proc, base, code, users = _fresh_app(world, tmp_path)
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "400" not in m.text and "403" not in m.text else None)
    try:
        page.goto(base + f"?setup={code}")
        page.get_by_role("heading", name="Set up the first attorney").wait_for()
        assert "?setup" not in page.url  # the code is taken out of the address bar once used
        assert page.locator("input[name=email]").count() == 1 and page.get_by_role("button", name="Create the account and sign in").is_visible()
        assert "Sign in" not in page.locator("h2").all_inner_texts()  # instead of the sign-in screen
        assert "goes away for good" in page.locator("main, #main").first.inner_text()
        _shot(page, "setup_first_attorney")
        _sound(page, errors, "setup")
        # the two passwords must match, and a short one is refused in words
        page.fill("input[name=name]", "Sam Exemplo")
        page.fill("input[name=email]", "sam@firm.example")
        page.fill("input[name=password]", PASSWORD)
        page.fill("input[name=again]", PASSWORD + "x")
        page.get_by_role("button", name="Create the account and sign in").click()
        page.wait_for_selector("form .err:not(:empty)")
        assert page.locator("form .err").inner_text() == "The two passwords don't match."
        page.fill("input[name=password]", "short")
        page.fill("input[name=again]", "short")
        page.get_by_role("button", name="Create the account and sign in").click()
        page.wait_for_function("() => document.querySelector('form .err').textContent.includes('at least 12')")
        page.fill("input[name=password]", PASSWORD)
        page.fill("input[name=again]", PASSWORD)
        page.get_by_role("button", name="Create the account and sign in").click()
        # the first attorney is handed straight to setting up the authenticator app; finishing it is the first sign-in, and Getting started opens by itself
        _enrol(page, world, "sam@firm.example")
        page.get_by_role("heading", name="Getting started").first.wait_for()
        page.wait_for_selector("#getting-started")
        text = page.locator("#main").inner_text()
        rows = {r.get_attribute("data-item"): r.inner_text() for r in page.locator("#getting-started tr[data-item]").all()}
        assert list(rows) == ["firm", "visa_bulletin", "fees", "translators", "staff", "second_factor", "first_client", "backups", "clio"]
        assert "Done" in rows["second_factor"] and "The attorney has set it up" in rows["second_factor"]  # the one attorney just did
        assert "To do" in rows["firm"] and "To do" in rows["staff"] and "To do" in rows["backups"] and "Optional" in rows["clio"]
        assert "Done" in rows["first_client"] and "clients in the system" in rows["first_client"]  # the world has cases
        assert "of 8 done" in page.locator("#case").inner_text() and "Open" in text
        assert not re.search(r"\.(json|py|db)\b|/api/", text) and "—" not in text and " -- " not in text
        _shot(page, "getting_started_first_day")
        _sound(page, errors, "getting started")

        # Open goes to the place; Settings has a way back
        page.locator("#getting-started tr[data-item=firm]").get_by_role("button", name="Open").click()
        page.get_by_role("heading", name="Settings").first.wait_for()
        page.wait_for_selector("#set-firm.flash, #set-firm")
        page.locator("#open-getting-started").click()
        page.wait_for_selector("#getting-started")

        # the state is read from the data: a backup recorded in the log turns that line Done, with its dates
        log_path = Path(world["env"]["I485_BACKUP_LOG"])
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        log_path.write_text(json.dumps({"last_backup": {"at": now.isoformat()}, "last_test_restore": {"at": (now - timedelta(days=3)).isoformat()}}), encoding="utf-8")
        page.get_by_role("button", name="Settings").click()  # the page reads the data each time it is opened
        page.locator("#open-getting-started").click()
        page.wait_for_selector("#getting-started")
        backup_row = page.locator("#getting-started tr[data-item=backups]").inner_text()
        assert "Done" in backup_row and "Last backup" in backup_row and "3 days ago" in backup_row
        page.locator("#getting-started tr[data-item=backups]").get_by_role("button", name="Open").click()
        page.get_by_role("heading", name="Keeping current").first.wait_for()
        assert "last test restore" in page.locator("#main").inner_text()  # Keeping current shows the same dates
        _shot(page, "keeping_current_backups")
        _sound(page, errors, "keeping current")
        log_path.unlink()

        # it does not open by itself a second time
        page.get_by_role("button", name="Sign out").click()
        page.wait_for_selector("input[name=email]")
        sam = ("sam@firm.example", "Sam Exemplo", PASSWORD)
        remembered(ctx, world, sam)
        page.fill("input[name=email]", sam[0])
        page.fill("input[name=password]", PASSWORD)
        page.get_by_role("button", name="Sign in").click()
        after_password(page, ctx, world, sam)  # the shared helper: the code, remembered, and it waits out the app's sign-in limit
        page.wait_for_selector("#client-rows")
        assert page.locator("#getting-started").count() == 0

        # and the first-attorney screen is gone for good: signed out, the sign-in screen, with or without the old code
        page.get_by_role("button", name="Sign out").click()
        for address in (base, base + f"?setup={code}"):
            page.goto(address)
            page.wait_for_selector("input[name=email]")
            assert page.get_by_role("heading", name="Set up the first attorney").count() == 0
            assert page.get_by_role("button", name="Sign in").is_visible()
        assert [u["email"] for u in users.users()] == ["sam@firm.example"]
        _sound(page, errors, "sign-in afterwards")
    finally:
        ctx.close()
        proc.terminate()


def test_a_new_attorney_is_shown_getting_started_once_and_a_paralegal_never(world, browser, paralegal, tmp_path):
    sys.path.insert(0, str(REPO / "src"))
    from review.auth import Accounts

    temporary = Accounts(world["users"]).add("pat.attorney@example.com", "Pat Attorney", "attorney")
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(world["review"])
        page.fill("input[name=email]", "pat.attorney@example.com")
        page.fill("input[name=password]", temporary)
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_selector("input[name=new_password]")
        page.fill("input[name=new_password]", PASSWORD)
        page.fill("input[name=again]", PASSWORD)
        page.get_by_role("button", name="Save and continue").click()
        _enrol(page, world, "pat.attorney@example.com")  # an attorney sets the app up at the first sign-in
        page.wait_for_selector("#getting-started")
        assert "Also due" in page.locator("#main").inner_text()  # the firm's upkeep that is due, under the day-one list
        pat = ("pat.attorney@example.com", "Pat Attorney", PASSWORD)
        page.get_by_role("button", name="Sign out").click()
        page.wait_for_selector("input[name=email]")
        remembered(ctx, world, pat)
        page.fill("input[name=email]", pat[0])
        page.fill("input[name=password]", PASSWORD)
        page.get_by_role("button", name="Sign in").click()
        after_password(page, ctx, world, pat)
        page.wait_for_selector("#client-rows")
        assert page.locator("#getting-started").count() == 0
    finally:
        ctx.close()
    # a paralegal: no Getting started under Settings, and the route refuses
    paralegal.page.get_by_role("button", name="Settings").click()
    paralegal.page.get_by_role("heading", name="Settings").first.wait_for()
    assert paralegal.page.locator("#open-getting-started").count() == 0
    status = paralegal.page.evaluate("async () => (await fetch('/api/getting-started')).status")
    assert status == 403
    paralegal.errors.clear()  # the browser logs the refusal I just asked for
    paralegal.check("paralegal_settings_no_getting_started")


def test_on_the_computer_itself_the_setup_page_asks_for_the_code_the_app_printed(world, browser, tmp_path):
    """Brief J2: the code is needed on the computer that runs the app too (a forwarder on it makes a stranger look local). Opened with no code in
    the address, the page asks for one; a wrong one is refused in words; the one the app printed at its start works."""
    proc, base, installer_code, users = _fresh_app(world, tmp_path)
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "404" not in m.text else None)
    try:
        printed = ""
        for _ in range(100):  # the app's window (its log here) says the code as it starts
            printed = (tmp_path / "fresh.log").read_text(encoding="utf-8")
            if "?setup=" in printed:
                break
            __import__("time").sleep(0.1)
        code = re.search(r"\?setup=([A-Z0-9-]{19})", printed).group(1)
        assert code != installer_code and "No staff account yet" in printed
        page.goto(base)
        page.get_by_role("heading", name="Set up the first attorney").wait_for()
        assert page.locator("input[name=code]").count() == 1 and "Type the one-time setup code too" in page.locator("#main").inner_text()
        _shot(page, "setup_first_attorney_code_box")
        _sound(page, errors, "setup with the code box")
        page.fill("input[name=code]", "AAAA-BBBB-CCCC-DDDD")
        page.fill("input[name=name]", "Sam Exemplo")
        page.fill("input[name=email]", "sam@firm.example")
        page.fill("input[name=password]", PASSWORD)
        page.fill("input[name=again]", PASSWORD)
        page.get_by_role("button", name="Create the account and sign in").click()
        page.wait_for_function("() => document.querySelector('form .err').textContent.includes('setup code did not work')")
        assert users.needs_setup()
        page.fill("input[name=code]", code.lower())  # typed by hand
        page.get_by_role("button", name="Create the account and sign in").click()
        page.wait_for_selector("#enrol-key")
        assert [u["email"] for u in users.users()] == ["sam@firm.example"]
    finally:
        ctx.close()
        proc.terminate()
