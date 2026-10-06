"""Settings, Keys and secrets (src/firmsecrets.py; brief R4): the attorney sees each kind of key with whether it is set, where it lives, when it was last changed and by whom, and chooses how
often it is changed; the page never shows a value; a paralegal does not see it. Everything is invented."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

import firmsecrets  # noqa: E402

VALUE = "made-up-mail-password-q7x2"


def open_settings(who, world) -> None:
    who.page.goto("about:blank")
    who.page.goto(world["review"] + "#settings")
    who.settle()
    who.page.wait_for_selector(".setgrid .setfield", state="attached", timeout=20000)


def test_the_attorney_sees_where_each_key_lives_chooses_how_often_to_change_it_and_never_sees_a_value(world, attorney):
    data = world["clients"].parent
    firmsecrets.put("smtp.password", VALUE, "Pat IT", data_root=data, how="rotated")
    open_settings(attorney, world)
    box = attorney.page.locator("#set-secrets")
    box.wait_for(state="attached", timeout=20000)
    mail = attorney.page.locator("#secret-mail")
    text = mail.inner_text()
    assert "Mail" in text and "The vault" in text and "Pat IT" in text and "Not chosen" in text
    assert attorney.page.locator("#secret-clio").inner_text().count("Not set") >= 1  # nothing for Clio in this made-up firm
    assert VALUE not in attorney.page.content() and VALUE not in box.inner_text()
    attorney.page.select_option("#cadence-mail", "monthly")
    mail.get_by_role("button", name="Save").click()
    attorney.settle()
    attorney.page.wait_for_selector("#secret-mail", state="attached", timeout=20000)
    after = attorney.page.locator("#secret-mail")
    assert after.locator("select").input_value() == "monthly"
    assert firmsecrets.read_log(data)["cadence"]["mail"]["cadence"] == "monthly"
    assert VALUE not in attorney.page.content()


def test_a_paralegal_does_not_see_keys_and_secrets(world, paralegal):
    open_settings(paralegal, world)
    assert paralegal.page.locator("#set-secrets").count() == 0
