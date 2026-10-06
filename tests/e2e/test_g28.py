"""The G-28's firm choices, in the browser (src/g28.py): the attorney sets an office's choices on Settings, with the form's own words beside each, and
approves them; on a case's packet tab the paralegal sees "The G-28 for this case", confirms it under their own name, changes one choice with a reason, takes
the change back; the packet is not ready until the card is confirmed; the filled G-28 carries what the card says.

A second review app over the world's cases is started with its own settings and approvals (the shared world's switches stay as they were); the office is the
made-up Exemplo office. The case is the made-up demo client, cloned.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

from pypdf import PdfReader

from conftest import REPO, Screen, _port, _wait

OFFICE = {"firm.business_name": "EXEMPLO LAW LLP", "firm.preparer_given_name": "ANA", "firm.preparer_family_name": "EXEMPLO", "firm.attorney_bar_number": "123456",
          "firm.street": "100 EXAMPLE WAY", "firm.city": "SPRINGFIELD", "firm.state": "MA", "firm.zip": "01101", "firm.phone": "5555550100",
          "office.name": "Springfield, MA", "office.states": "MA,NH,CT,NY,FL"}


def test_the_g28_card_the_office_choices_the_gate_and_the_filled_form(world, browser, tmp_path):
    import world as w

    import g28

    w.clone(world["root"], "demo-ana", "case-g28")
    port = _port()
    base = f"http://127.0.0.1:{port}/"
    env = world["env"] | {"I485_SETTINGS": str(tmp_path / "settings.json"), "I485_RULES_APPROVED": str(tmp_path / "rules_approved.json")}
    (tmp_path / "getting_started.json").write_bytes((Path(world["env"]["I485_SETTINGS"]).parent / "getting_started.json").read_bytes())
    log = open(tmp_path / "review.log", "w")
    review = subprocess.Popen([sys.executable, "src/review/server.py", "--data", str(world["clients"]), "--port", str(port),
                               "--users", str(world["users"]), "--portal", str(world["portal"])], cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    attorney = paralegal = None
    try:
        _wait(base)
        world.setdefault("last_steps", {})
        world.setdefault("devices", {})
        attorney = Screen(browser, world | {"review": base}, w.ATTORNEY)
        page = attorney.page
        # the made-up office is saved first (the identity a packet needs), by the same route the Settings page uses
        saved = page.request.post(base + "api/settings", data=json.dumps({"section": "firm", "values": OFFICE, "reviewer": w.ATTORNEY[1]}),
                                  headers={"Content-Type": "application/json", "X-Review-App": "1"})
        assert saved.status == 200, saved.text()

        # Settings, Main office: the four choices, off, each with the form's own words; the practice is not approved yet
        page.goto("about:blank")
        page.goto(base + "#settings:firm")
        page.wait_for_selector("#set-firm", state="visible")
        sec = page.locator("#set-firm")
        text = sec.inner_text()
        assert "the g-28: this office's choices" in text.lower()  # a group's heading is set in capitals
        assert g28.mail_note() in text and "The attorney decides whether it is right for the firm." in text
        for item in g28.ITEMS:
            assert g28.register()["part4"][item]["words"] in text
        mail = sec.locator("label.setfield", has_text="On the G-28, the client's mailing address").locator("select")
        one_a = sec.locator("label.setfield", has_text="Part 4, item 1.a").locator("select")
        assert mail.input_value() == "off" and one_a.input_value() == "off"
        practice = page.locator("#practice-g28-firm")
        assert "Not yet approved" in practice.inner_text() and "Springfield, MA: the client's mailing address on the G-28 is the client's own address" in practice.inner_text()
        attorney.check("g28-settings")
        mail.select_option("on")
        one_a.select_option("on")
        sec.get_by_role("button", name="Save").click()
        assert attorney.toast() == "Saved."
        page.wait_for_selector("#set-firm", state="visible")
        practice = page.locator("#practice-g28-firm")
        assert "Springfield, MA: the client's mailing address on the G-28 is this office's address; item 1.a is marked" in practice.inner_text()

        # a paralegal opens the case before the attorney approves: the client's own address, Part 4 blank, and the card says so
        paralegal = Screen(browser, world | {"review": base}, w.PARALEGAL)
        paralegal.open("case-g28", "packet", "i485")
        card = paralegal.page.locator("#g28-card")
        card.wait_for(state="visible")
        said = card.inner_text()
        assert "The G-28 for this case" in said and "Not confirmed yet" in said
        assert "Waiting for an attorney to approve the office's G-28 choices" in said and "the form carries the client's own address and a blank Part 4" in said
        assert card.get_by_role("button", name="Confirm these choices").is_disabled()  # Implementation note.
        assert "The attorney who signs: ANA EXEMPLO, bar number 123456 (Springfield, MA)" in said
        assert "The attorney decides whether these choices are right for this case." in said
        assert g28.mail_note() in said and g28.register()["part4"]["1a"]["words"] in said
        assert "The instruction line reads:" in said and "Form G-28 Instructions, edition 09/17/18" in said
        assert "The attorney decides which cases this covers." in said and "special immigrant juvenile (SIJ)" in said and "(T nonimmigrant)" in said  # the paragraph, whole
        refused = paralegal.page.request.post(base + "api/g28", headers={"Content-Type": "application/json", "X-Review-App": "1"}, data=json.dumps(
            {"client": "case-g28", "filing": "i485", "action": "change", "item": "mail", "value": "office", "reason": "The client asked.", "reviewer": w.PARALEGAL[1]}))
        assert refused.status == 403 and refused.json()["error"] == g28.WAITING  # refused in words, never dropped silently
        assert "The G-28's choices were not confirmed for this case." in paralegal.text()
        assert ".json" not in said and "g28." not in said and "applicant." not in said
        paralegal.check("g28-card-unapproved")

        # the attorney approves the practice, in Settings
        page.goto("about:blank")
        page.goto(base + "#settings:firm")
        page.wait_for_selector("#practice-g28-firm", state="visible")
        page.locator("#practice-g28-firm").get_by_role("button", name="Approve this practice").click()
        assert attorney.toast() == "Approved."
        page.wait_for_selector("#practice-g28-firm", state="visible")
        assert "Approved by Ana Attorney" in page.locator("#practice-g28-firm").inner_text()

        # the card now starts from the office's choices; the paralegal confirms it under their own name and the gate opens
        paralegal.open("case-g28", "packet", "i485")
        card = paralegal.page.locator("#g28-card")
        assert "Approved by Ana Attorney" in card.inner_text()
        assert paralegal.page.locator("#g28-pick-mail").input_value() == "office"
        assert paralegal.page.locator("#g28-mark-1a").is_checked() and not paralegal.page.locator("#g28-mark-1b").is_checked()
        assert "100 EXAMPLE WAY; SPRINGFIELD, MA 01101" in card.inner_text()
        card.get_by_role("button", name="Confirm these choices").click()
        assert "Confirmed:" in paralegal.toast()
        paralegal.settle()
        card = paralegal.page.locator("#g28-card")
        assert f"Confirmed by {w.PARALEGAL[1]} on" in card.inner_text()
        assert "The G-28's choices were not confirmed for this case." not in paralegal.text()
        paralegal.check("g28-card-confirmed")

        # a change for this case needs a reason; it unconfirms the card and the packet says so again; undo puts it back
        row = card.locator(".g28-row[data-item='1b']")
        row.locator("#g28-mark-1b").check()
        row.get_by_role("button", name="Save this change").click()
        assert "Say why this case differs" in paralegal.toast(ok=False)
        row.get_by_placeholder("Why this case differs from the office's setting").fill("The client will be away for a month.")
        row.get_by_role("button", name="Save this change").click()
        assert "Saved: confirm the card again." in paralegal.toast()
        paralegal.settle()
        card = paralegal.page.locator("#g28-card")
        assert "Changed since it was confirmed" in card.inner_text()
        assert f"Changed for this case by {w.PARALEGAL[1]}" in card.inner_text() and "The client will be away for a month." in card.inner_text()
        assert "The G-28's choices were not confirmed for this case." in paralegal.text()
        card.get_by_role("button", name="Take back the last change").click()
        assert "taken back" in paralegal.toast()
        paralegal.settle()
        card = paralegal.page.locator("#g28-card")
        assert f"Confirmed by {w.PARALEGAL[1]} on" in card.inner_text() and not paralegal.page.locator("#g28-mark-1b").is_checked()
        card.locator("details.how summary").click()
        assert "Took back the last change" in card.inner_text()

        # the other forms: each form's instruction line and what it carries
        assert "The mailing address on the other forms in this packet" in card.inner_text()
        assert "Form I-485 Instructions, edition 09/18/26" in card.inner_text() and "Form I-765 Instructions, edition 08/21/25" in card.inner_text()
        assert "not allowed" not in card.inner_text() and "Client's own address" not in card.locator("[data-form]").first.inner_text()

        # the filled G-28 carries the card: build the packet, open the form
        paralegal.page.get_by_role("button", name="Build packet").click()
        assert "Packet built" in paralegal.toast()
        paralegal.settle()
        r = paralegal.page.request.get(base.rstrip("/") + "/api/form?client=case-g28&form=g28")
        assert r.status == 200
        values = {}
        for name, field in (PdfReader(io.BytesIO(r.body())).get_fields() or {}).items():
            values.setdefault(name.rsplit(".", 1)[-1], []).append(field.get("/V"))

        def box(short):
            got = [v for v in values.get(short, []) if v not in (None, "", "/Off")]
            return str(got[0]).strip() if got else None

        assert box("Line12a_StreetNumberName[0]") == "100 EXAMPLE WAY" and box("Line12c_CityOrTown[0]") == "SPRINGFIELD" and box("Line12e_ZipCode[0]") == "01101"
        assert box("Line12b_AptSteFlrNumber[0]") is None
        assert box("Pt4Line2a_CheckBox2a[0]") == "/Y" and box("Pt4Line2b_CheckBox2b[0]") is None and box("Pt4Line2c_CheckBox2c[0]") is None
        assert box("Pt1Line2a_FamilyName[0]") == "EXEMPLO" and box("Pt2Line1b_BarNumber[0]") == "123456"

        # the review bundle says who confirmed it
        paralegal.page.get_by_role("button", name="Review bundle").click()
        assert "Review bundle built" in paralegal.toast()
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(paralegal.page.request.get(base.rstrip("/") + "/api/review-bundle.pdf?client=case-g28&filing=i485").body())).pages)
        assert "The G-28 for this case: the choices and who made them" in text and f"Confirmed by {w.PARALEGAL[1]}" in text
    finally:
        for s in (attorney, paralegal):
            if s is not None:
                s.close()
        review.terminate()
        log.close()
