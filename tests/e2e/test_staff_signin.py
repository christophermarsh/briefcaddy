"""Signing in with the firm's Microsoft 365 or Google account, and "Who viewed this", in the browser.

The world's review app ships with passwords only: no provider buttons. A second review app on the same made-up
world is started with a connectors file that switches both providers on (and made-up secrets): both buttons
show, Microsoft first, each going to the app's own /auth/start. Nothing here reaches Microsoft or Google.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import REPO, _port, _wait


def _signed_out(browser, url):
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_selector("input[name=email]")
    return ctx, page, errors


def test_the_sign_in_screen_offers_microsoft_and_google_only_when_switched_on(world, browser, tmp_path):
    from review.auth import WRONG

    ctx, page, errors = _signed_out(browser, world["review"])
    assert page.locator("a", has_text="Sign in with").count() == 0  # passwords only, as shipped
    ctx.close()

    connectors = tmp_path / "connectors.json"
    connectors.write_text(json.dumps({"active": {"sign_in": ["google", "microsoft"]},
                                      "sign_in": {"google_domain": "example.com", "redirect_uri": "https://review.example.com/auth/callback"}}))
    env = world["env"] | {"I485_CONNECTORS": str(connectors), "GOOGLE_OAUTH_CLIENT_ID": "made-up", "GOOGLE_OAUTH_CLIENT_SECRET": "made-up",
                          "MS_TENANT_ID": "made-up-tenant", "MS_CLIENT_ID": "made-up", "MS_CLIENT_SECRET": "made-up"}
    port = _port()
    log = open(tmp_path / "review.log", "w")
    review = subprocess.Popen([sys.executable, "src/review/server.py", "--data", str(world["clients"]), "--port", str(port),
                               "--users", str(world["users"]), "--portal", str(world["portal"])], cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        _wait(f"http://127.0.0.1:{port}/")
        ctx, page, errors = _signed_out(browser, f"http://127.0.0.1:{port}/")
        page.wait_for_selector("a:has-text('Sign in with Microsoft')")
        links = page.locator("a", has_text="Sign in with")
        assert links.all_inner_texts() == ["Sign in with Microsoft", "Sign in with Google"]
        assert links.first.get_attribute("href") == "/auth/start?provider=microsoft"
        assert page.locator("input[name=password]").is_visible()  # passwords keep working beside them
        if os.environ.get("E2E_SHOTS"):
            Path(os.environ["E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(os.environ["E2E_SHOTS"]) / "sign_in_providers.png"), full_page=True)
        # back from the provider with an account that isn't on the staff list: the words of a wrong password
        page.goto(f"http://127.0.0.1:{port}/?signin=refused")
        page.wait_for_selector("form .err:not(:empty)")
        assert page.locator("form .err").inner_text() == WRONG
        assert "signin=" not in page.url  # the reason isn't left in the address
        assert not errors, errors
        ctx.close()
    finally:
        review.terminate()
        log.close()
    assert "Traceback" not in (tmp_path / "review.log").read_text(errors="replace")


def test_the_attorney_sees_who_viewed_a_case(attorney, paralegal):
    paralegal.open("demo-ana", "check")  # the paralegal opens the case and its scans
    paralegal.open("demo-ana", "done")
    assert "Who viewed this" not in paralegal.text()  # the attorney's
    attorney.open("demo-ana", "done")
    attorney.page.wait_for_selector("#viewed table")
    body = attorney.check("decision_log_who_viewed")
    viewed = attorney.page.locator("#viewed").inner_text()
    assert "Paulo Paralegal" in viewed and "Opened the case" in viewed, viewed
    assert "Who viewed this" in body
