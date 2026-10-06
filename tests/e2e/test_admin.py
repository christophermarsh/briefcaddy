"""The office's own screens, end to end: a new staff member's first sign-in and
password, a reset, the "Keeping current" register and "Reader accuracy" pages,
"Apply & re-fill" -- and a client sending a document from the portal.
The world is made up (tests/e2e/world.py).
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date

from conftest import LEAK, REPO, press


def _users(world, *args: str) -> str:
    out = subprocess.run([sys.executable, "src/review/users.py", "--file", str(world["users"]), *args], cwd=REPO, env=world["env"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _one_time(stdout: str) -> str:
    """The one-time password users.py prints (the longest token on its output)."""
    return max(re.findall(r"[A-Za-z0-9_\-]{12,}", stdout), key=len)


def _sign_in(page, url: str, email: str, password: str) -> None:
    page.context.clear_cookies()  # signed out: a fresh sign-in
    page.goto("about:blank")
    page.goto(url)
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", password)
    press(page, "Sign in")
    page.wait_for_timeout(800)


def test_a_new_staff_member_chooses_a_password_and_a_reset_ends_the_old_one(world, browser):
    temporary = _one_time(_users(world, "add", "nina@example.com", "Nina New", "paralegal"))
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    _sign_in(page, world["review"], "nina@example.com", temporary)
    assert "Choose your password" in page.locator("body").inner_text()        # a one-time password must be replaced first
    page.fill("input[name=new_password]", "a long sentence to remember")
    page.fill("input[name=again]", "a different long sentence")
    press(page, "Save and continue")
    page.wait_for_timeout(500)
    assert "don't match" in page.locator("body").inner_text()
    page.fill("input[name=again]", "a long sentence to remember")
    press(page, "Save and continue")
    page.wait_for_selector("#client", state="visible")                       # signed in, on the work list
    assert "Nina New" in page.locator("body").inner_text()
    _sign_in(page, world["review"], "nina@example.com", temporary)            # the one-time password no longer works
    assert page.locator("#client").is_hidden() and "Sign in" in page.locator("body").inner_text()
    second = _one_time(_users(world, "reset", "nina@example.com"))           # a reset: the chosen password stops working too
    _sign_in(page, world["review"], "nina@example.com", "a long sentence to remember")
    assert page.locator("#client").is_hidden()
    _sign_in(page, world["review"], "nina@example.com", second)
    assert "Choose your password" in page.locator("body").inner_text()
    assert not errors, errors
    ctx.close()


def test_a_new_attorney_sets_up_an_authenticator_app_then_signs_in_with_codes(world, browser):
    """The second factor (review/auth.py): the app set up at the first sign-in (the secret read off the screen, the code
    computed here as the phone would), recovery codes shown once, a wrong code refused, "remember this computer", and a
    recovery code in place of the phone."""
    import os
    from pathlib import Path

    from conftest import code_for
    from review import totp

    temporary = _one_time(_users(world, "add", "nora@example.com", "Nora Attorney", "attorney"))
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    shots = Path(os.environ["E2E_SHOTS"]) if os.environ.get("E2E_SHOTS") else None
    shot = lambda name: shots and (shots.mkdir(parents=True, exist_ok=True), page.screenshot(path=str(shots / f"{name}.png"), full_page=True))  # noqa: E731
    _sign_in(page, world["review"], "nora@example.com", temporary)
    page.fill("input[name=new_password]", "nora's own long sentence")
    page.fill("input[name=again]", "nora's own long sentence")
    press(page, "Save and continue")
    page.wait_for_selector("#enrol-key")
    body = page.locator("body").inner_text()
    assert "Set up your authenticator app" in body and "a stolen password alone can't open client files" in body
    assert page.locator("img[alt^='The code to scan']").is_visible()
    shot("second-factor-enrol")
    letters = page.locator("#enrol-key").inner_text().replace(" ", "")
    world["secrets"]["nora@example.com"] = __import__("base64").b32decode(letters + "=" * (-len(letters) % 8))
    page.fill("input[name=code]", code_for(world, "nora@example.com"))
    press(page, "Confirm and continue")
    page.wait_for_selector("#recovery-codes")
    recovery = page.locator("#recovery-codes code").all_inner_texts()
    assert len(recovery) == 8 and "not shown again" in page.locator("body").inner_text()
    shot("second-factor-recovery-codes")
    page.get_by_role("button", name="I've kept them: continue").click()
    page.wait_for_selector("#client", state="visible")                        # signed in
    assert "Nora Attorney" in page.locator("body").inner_text()

    # the next sign-in: the password, then the code (a wrong one first)
    page.wait_for_selector("#getting-started")  # an attorney's first sign-in opens Getting started by itself (src/getting_started.py), drawn before anything can cover the sign-in screen
    page.get_by_role("button", name="Sign out").click()
    page.fill("input[name=email]", "nora@example.com")
    page.fill("input[name=password]", "nora's own long sentence")
    press(page, "Sign in")
    page.wait_for_selector("input[name=code]")
    body = page.locator("body").inner_text()
    assert "Enter your code" in body and "Lost your phone? Use a recovery code, or ask your attorney for a reset." in body
    shot("second-factor-code")
    good = code_for(world, "nora@example.com")
    page.fill("input[name=code]", str((int(good) + 1) % 10 ** totp.DIGITS).zfill(totp.DIGITS))
    press(page, "Sign in")
    page.wait_for_selector("form .err:not(:empty)")
    assert "That code didn't work" in page.locator("form .err").inner_text() and page.locator("#client").is_hidden()
    page.fill("input[name=code]", good)
    page.locator("input[name=remember]").check()
    press(page, "Sign in")
    page.wait_for_selector("#client", state="visible")
    # this computer is remembered: the password alone, for 30 days
    page.wait_for_selector("text=Where each client stands")  # the work list drawn (a late one would cover the sign-in screen)
    page.get_by_role("button", name="Sign out").click()
    page.fill("input[name=email]", "nora@example.com")
    page.fill("input[name=password]", "nora's own long sentence")
    press(page, "Sign in")
    page.wait_for_selector("#client", state="visible")
    # another computer, no phone: a recovery code
    _sign_in(page, world["review"], "nora@example.com", "nora's own long sentence")
    page.wait_for_selector("input[name=code]")
    page.fill("input[name=code]", recovery[0])
    press(page, "Sign in")
    page.wait_for_selector("#client", state="visible")
    assert "7 left" in page.locator("#toast").inner_text()
    _sign_in(page, world["review"], "nora@example.com", "nora's own long sentence")
    page.wait_for_selector("input[name=code]")
    page.fill("input[name=code]", recovery[0])  # each works once
    press(page, "Sign in")
    page.wait_for_selector("form .err:not(:empty)")
    assert page.locator("#client").is_hidden()
    assert not errors, errors
    ctx.close()


def test_the_attorney_requires_a_code_for_everyone(world, attorney, browser):
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#settings")
    attorney.settle()
    codes = attorney.page.locator("#staff-codes")
    assert "Attorneys always use one" in codes.inner_text() and "people without the app are signed out now and asked to set it up" in codes.inner_text()
    row = attorney.page.locator("#staff-rows tr", has_text="Ana Attorney")
    assert "Set up" in row.inner_text()
    try:
        codes.get_by_label("Require a code for everyone").check()
        codes.get_by_role("button", name="Save").click()
        assert attorney.toast() == "Saved."
        attorney.settle()
        assert "Last changed by Ana Attorney" in attorney.page.locator("#staff-codes").inner_text()
        assert "asked at next sign-in" in attorney.page.locator("#staff-rows tr", has_text="Paulo Paralegal").inner_text()
        attorney.check("settings-staff-codes")
        import world as w

        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        _sign_in(page, world["review"], w.PARALEGAL[0], w.PARALEGAL[2])
        page.wait_for_selector("#enrol-key")                                       # the paralegal sets the app up now
        assert page.locator("#client").is_hidden()
        ctx.close()
    finally:  # back as shipped, for the other tests' paralegal
        attorney.page.locator("#staff-codes").get_by_label("Require a code for everyone").uncheck()
        attorney.page.locator("#staff-codes").get_by_role("button", name="Save").click()
        assert attorney.toast() == "Saved."


def test_keeping_current_records_who_checked_it(world, attorney):
    attorney.page.goto(world["review"])
    attorney.settle()
    attorney.page.locator("#all").click()
    attorney.page.get_by_role("button", name=re.compile("Keeping current")).click()
    attorney.settle()
    body = attorney.check("keeping-current")
    assert "Keeping current" in body and "Firm responsibilities" in body and "family-sponsored" in body
    assert "Provider responsibilities" in body and "schemas/" not in body and "src/" not in body and "Kept in" not in body
    row = attorney.page.locator("tr").filter(has_text="this month's family-sponsored").first
    row.get_by_role("button", name="Mark checked").click()
    assert attorney.toast() == "Recorded."
    attorney.settle()
    row = attorney.page.locator("tr").filter(has_text="this month's family-sponsored").first
    assert date.today().strftime("%m/%d/%Y") in row.inner_text() and "Ana Attorney" in row.inner_text()
    # who, in the firm's own log -- never in the register we ship
    assert "Ana Attorney" in (world["register"].parent / "maintenance_log.json").read_text(encoding="utf-8")
    assert "Ana Attorney" not in world["register"].read_text(encoding="utf-8")
    attorney.page.get_by_role("button", name="Back to every case").click()
    attorney.settle()
    attorney.page.get_by_role("button", name=re.compile("Reader review outcomes")).click()
    attorney.settle()
    body = attorney.check("reader-accuracy")
    assert "Reader review outcomes" in body and "by source" in body.lower()
    assert "changed or removed" in body.lower()
    assert "staff time has not been measured" in body
    assert "Workflow decisions are not independently adjudicated accuracy or training truth" in body


def test_apply_and_refill_redoes_the_form(world, paralegal):
    paralegal.open("demo-ana", "attention")
    paralegal.page.locator("#apply").click()
    assert paralegal.toast().startswith("Re-filled:")


def test_the_client_sends_a_document_from_the_portal(world, browser, tmp_path):
    link = subprocess.run([sys.executable, "src/portal/admin.py", "link", "pilot-nova"], cwd=REPO, env=world["env"], capture_output=True,
                          text=True).stdout.split()[0]
    ctx = browser.new_context(viewport={"width": 1200, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(link)
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="Começar").click()
    page.wait_for_timeout(800)
    page.locator("#nav button", has_text="Documentos").first.click()
    page.wait_for_timeout(600)
    pdf = tmp_path / "certidao.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
    page.locator("input[type=file][aria-label^='Escolher arquivo']").first.set_input_files(str(pdf))
    page.wait_for_timeout(1500)
    body = page.locator("main").inner_text()
    assert "certidao.pdf" in body, body[:800]
    bad = tmp_path / "notes.txt"
    bad.write_text("not a document")
    page.locator("input[type=file][aria-label^='Escolher arquivo']").first.set_input_files(str(bad))
    page.wait_for_timeout(1200)
    assert "notes.txt" not in page.locator("main").inner_text()               # refused: PDF, JPG or PNG only
    page.reload()
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text()
    assert not [line for line in body.splitlines() if LEAK.search(line)] and not errors, errors
    ctx.close()


def test_a_second_office_files_its_own_cases(world, attorney):
    """A firm with a Florida office (made up): added on Settings, a case moved to it, and its G-28 names that office's attorney."""
    from pypdf import PdfReader

    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#settings")
    attorney.settle()
    attorney.page.get_by_role("button", name="Add an office").click()
    assert "Office added" in attorney.toast()
    attorney.settle()
    sec = attorney.page.locator("section.setsec", has_text="Office: new office")
    for label, value in (("Office name", "Miami, FL"), ("States this office files for", "FL"), ("Attorney: first name", "MARIA"),
                         ("Attorney: last name", "EXEMPLO"), ("Bar number", "1234567"), ("Street", "100 EXAMPLE AVE"), ("City", "MIAMI"),
                         ("State", "FL"), ("ZIP code", "33101"), ("Phone", "3055550100")):
        sec.locator("label.setfield", has=attorney.page.locator("span.setlbl", has_text=re.compile("^" + re.escape(label)))).locator("input").first.fill(value)
    sec.get_by_role("button", name="Save").click()
    assert attorney.toast() == "Saved."
    attorney.open("demo-ana", "packet")
    attorney.page.select_option("select[aria-label='Office the case is filed from']", label="Miami, FL")
    assert "now files from Miami, FL" in attorney.toast()
    attorney.settle()
    assert "Office Miami, FL" in attorney.check("office-chosen").replace("\n", " ") or attorney.page.locator(".office-pick").input_value() != "main"
    attorney.page.get_by_role("button", name=re.compile("uild packet")).first.click()
    assert "Packet built" in attorney.toast()
    g28 = {k: str(v.get("/V")) for k, v in (PdfReader(str(world["clients"] / "demo-ana" / "g28_filled.pdf")).get_fields() or {}).items()}
    assert "EXEMPLO" in g28.values() and "1234567" in g28.values() and "100 EXAMPLE AVE" in g28.values()
    attorney.page.select_option("select[aria-label='Office the case is filed from']", index=0)  # back to the main office for the other tests
    attorney.toast()
