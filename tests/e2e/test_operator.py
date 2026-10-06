"""Everything a paralegal does, on a screen, in the browser (review/front_desk.py, rules/firm_policies.py):
add a client, invite, add a scan to a case, choose the questionnaire, and the attorney's edit of a firm policy.

Everyone here is made up (EXEMPLO); the made-up world's servers send nothing (no mail server: every message is queued).
"""

from __future__ import annotations

import json
from pathlib import Path

import clock

TODAY = clock.us_date(clock.stamp())


def _list(s, world):
    s.page.goto("about:blank")
    s.page.goto(world["review"] + "#all")
    s.page.wait_for_selector("#client-rows")
    s.settle(300)


def _added(s):
    """The toast that says the scan was added (the one before it says it is being added)."""
    s.page.wait_for_function("() => document.getElementById('toast').innerText.startsWith('Added')", timeout=60000)
    return s.toast()


def _add(s, name, email, invite=False, language="es", questionnaire="n400", phone=None):
    s.page.get_by_role("button", name="Add a client").click()
    form = s.page.locator("#add-client-form")
    form.get_by_label("Client's full name").fill(name)
    form.get_by_label("Client's email address").fill(email)
    form.get_by_label("Client's phone number").fill(phone or f"(555) 010-{sum(map(ord, email)) % 9000 + 1000}")
    form.get_by_label("Client's language").select_option(language)
    form.get_by_label("Questionnaire the client answers").select_option(questionnaire)
    form.get_by_role("checkbox", name="Email").check()
    if invite:
        form.get_by_role("checkbox", name="Send the invitation now").check()
    s.page.locator("#add-client-save").click()
    said = s.toast()
    # a name whose first name and a surname match someone already in the world (the conflict search, src/conflicts.py) is not added by the
    # first press: the form asks for a decision first. Said here, not as a row that never appears 30 seconds later.
    assert not said.startswith("The conflict search found hits"), f"{name} matches a client another test added: give this one a first name no other test uses ({said})"
    return said


def test_a_paralegal_adds_a_client_and_invites_them(world, paralegal):
    from portal.store import PortalStore

    s = paralegal
    _list(s, world)
    s.check("operator_list")
    said = _add(s, "Lucia Exemplo Browser", "lucia.browser@example.com", invite=True)
    assert said.startswith("Added Lucia Exemplo Browser.") and "Invitation: Queued, no mail server configured" in said
    s.page.wait_for_selector("tr.row:has-text('Lucia Exemplo Browser')")
    row = s.page.locator("tr.row", has_text="Lucia Exemplo Browser")
    assert "Spanish" in row.inner_text() and "Invited" in row.inner_text()
    assert row.get_by_role("button", name="Remind").count() == 1 and row.get_by_role("button", name="Invite", exact=True).count() == 0  # already invited
    s.check("operator_added")

    store = PortalStore(world["portal"])
    [client] = [c for c in store.clients() if c.startswith("lucia-exemplo-browser")]
    profile = store.profile(client)
    assert profile["filing"] == "n400" and profile["language"] == "es" and profile["consent"] == {"email": True, "sms": False, "whatsapp": False}
    assert profile["added_by"] == "Paulo Paralegal" and profile["last_invite_by"] == "Paulo Paralegal"
    outbox = (world["portal"] / "outbox.jsonl").read_text(encoding="utf-8")
    assert "lucia.browser@example.com" in outbox and "Lucia" not in outbox.split("lucia.browser@example.com", 1)[1].split("\n", 1)[0]  # the message names no one

    # a client added without the invitation: Invite on their row, then the invitation again from the row's menu
    said = _add(s, "Mateo Exemplo Browser", "mateo.browser@example.com")
    assert said == "Added Mateo Exemplo Browser. Not invited yet: use Invite on their row."
    row = s.page.locator("tr.row", has_text="Mateo Exemplo Browser")
    row.get_by_role("button", name="Invite", exact=True).click()
    assert s.toast().startswith("Invitation: Queued")
    s.page.wait_for_selector("tr.row:has-text('Mateo Exemplo Browser') button:has-text('Remind')")  # the list is read again after the send
    row = s.page.locator("tr.row", has_text="Mateo Exemplo Browser")
    row.locator("details.rowmenu summary").click()
    assert row.get_by_role("button", name="Show the link").count() == 0 and row.get_by_role("button", name="Add documents").count() == 1  # the link is the attorney's
    row.get_by_role("button", name="Send the invitation again").click()
    assert s.toast().startswith("Invitation: Queued")
    s.check("operator_invited")
    [mateo] = [c for c in store.clients() if c.startswith("mateo-exemplo-browser")]
    sent = [json.loads(x) for x in (store.client_dir(mateo) / "events.jsonl").read_text(encoding="utf-8").splitlines() if "sent_invite" in x]
    assert len(sent) == 2 and sent[1].get("again") is True


def test_the_attorney_shows_the_link_to_a_client_next_to_her(world, attorney):
    s = attorney
    _list(s, world)
    _add(s, "Helena Exemplo Ponte", "helena.ponte@example.com")  # a first name no other file adds: "Rosa Exemplo" matched the VAWA client of test_fourth_visit
    row = s.page.locator("tr.row", has_text="Helena Exemplo Ponte")
    row.locator("details.rowmenu summary").click()
    row.get_by_role("button", name="Show the link").click()
    panel = s.page.locator("#link-panel")
    panel.wait_for()
    url = panel.get_by_label("The client's sign-in link").input_value()
    assert url.startswith(world["portal_url"] + "/l/") and "works once" in panel.inner_text() and "72 hours" in panel.inner_text()
    s.check("operator_link")
    rows = [json.loads(x) for x in (world["root"] / "review_views.jsonl").read_text(encoding="utf-8").splitlines()]
    shown = [r for r in rows if r["kind"] == "link"]
    assert shown and shown[-1]["email"] == "attorney@example.com" and shown[-1]["client"].startswith("helena-exemplo-ponte")
    assert url not in (world["root"] / "review_views.jsonl").read_text(encoding="utf-8")  # the log says who and when, never the link


def test_a_scan_dragged_onto_the_documents_tab_is_read_and_listed(world, paralegal, tmp_path):
    from portal.demo import document_pdf

    s = paralegal
    s.open("case-sij", "documents")
    assert s.page.locator("#dropzone").count() == 1
    first = tmp_path / "passport photo page.pdf"
    first.write_bytes(document_pdf(["EXEMPLO: DEMONSTRATION DOCUMENT", "PASSPORT", "Surname EXEMPLO SOUZA", "Given names ANA CLARA", "Nationality BRAZILIAN"]))
    before = s.page.locator("#documents tr").count()
    s.page.locator("#dropzone input[type=file]").set_input_files(str(first))
    said = _added(s)
    assert said == "Added 1 document. It is being read now."
    s.page.wait_for_function("(n) => document.querySelectorAll('#documents tr').length > n", arg=before)
    s.page.wait_for_function("() => document.body.innerText.includes('Added by Paulo Paralegal on')", timeout=60000)  # the worker writes who added it a moment after the document
    body = s.check("operator_documents")
    assert "Added by Paulo Paralegal on " + TODAY in body
    assert "scan_" not in body  # never the stored file name
    assert s.page.evaluate("docName('scan_2026-10-02_driver_license_scan.pdf#p1-2')") == "Added by the office 10/02/2026: driver license scan (pages 1 to 2)"
    assert s.page.evaluate("docName('scan_2026-10-02_passport.pdf#p3')") == "Added by the office 10/02/2026: passport (page 3)"

    # the same by dragging: a drop on the zone carries the file
    s.page.evaluate("""async () => {
        const png = Uint8Array.from(atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="), (c) => c.charCodeAt(0));
        const dt = new DataTransfer();
        dt.items.add(new File([png], "photo of a letter.png", { type: "image/png" }));
        document.getElementById("dropzone").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
    }""")
    _added(s)
    s.page.wait_for_function("(n) => document.querySelectorAll('#documents tr').length > n", arg=before + 1)
    meta = json.loads((world["clients"] / "case-sij" / "meta.json").read_text(encoding="utf-8"))
    names = sorted(p.name for p in Path(meta["source_folder"]).iterdir() if p.name.startswith("scan_"))
    assert len(names) == 2 and all(n.endswith(".pdf") for n in names)  # the photo became a PDF, like the portal's
    documents = json.loads((world["clients"] / "case-sij" / "documents.json").read_text(encoding="utf-8"))["documents"]
    assert [d["source"] for d in documents if d.get("added_by")] == ["folder", "folder"]
    s.check("operator_documents_dropped")

    # what may not be added is said, in words
    bad = tmp_path / "notes.txt"
    bad.write_text("not a scan", encoding="utf-8")
    s.page.locator("#dropzone input[type=file]").set_input_files(str(bad))
    assert "Only a PDF, a JPEG or a PNG can be added" in s.toast(ok=False)


def test_the_questionnaire_is_chosen_on_the_case_page(world, paralegal):
    from portal.store import PortalStore

    s = paralegal
    s.open("demo-ana", "documents")
    panel = s.page.locator("#portal-panel")
    assert "Client portal." in panel.inner_text() and panel.get_by_role("button", name="Show the link").count() == 0  # the attorney's
    pick = s.page.get_by_label("Questionnaire the client answers")
    assert pick.input_value() == "i485"
    pick.select_option("n400")
    assert "Citizenship (N-400) questionnaire" in s.toast()
    s.settle()
    assert s.page.get_by_label("Questionnaire the client answers").input_value() == "n400"
    s.check("operator_questionnaire")
    store = PortalStore(world["portal"])
    assert store.profile("demo-ana")["filing"] == "n400" and store.profile("demo-ana")["filing_changed"]["by"] == "Paulo Paralegal"
    s.page.get_by_label("Questionnaire the client answers").select_option("i485")  # as it was
    s.toast()
    assert store.profile("demo-ana")["filing"] == "i485"


def test_the_attorney_edits_a_firm_policy_in_settings(world, attorney, paralegal):
    s = attorney
    s.page.goto("about:blank")
    s.page.goto(world["review"] + "#settings:policies")
    s.page.wait_for_selector("#set-policies")
    s.settle(300)
    card = s.page.locator("details.policy", has_text="Not filing with the immigration court")
    card.locator("summary").click()
    words = card.get_by_label("Words for Not filing with the immigration court")
    assert "immigration court" in words.input_value()
    s.check("operator_policies")
    words.fill("The client is not in removal proceedings. The firm answers the court question Yes on every I-485.")
    card.locator("select").select_option("Yes")
    card.get_by_role("button", name="Save").click()
    assert s.toast().startswith("Saved.")
    s.settle(300)
    card = s.page.locator("details.policy", has_text="Not filing with the immigration court")
    assert card.get_attribute("open") is not None  # the card you edited stays open
    text = card.inner_text()
    assert f"The firm's wording and answer, edited by Ana Attorney on {TODAY}." in text and "Edited by the firm" in text and "Changed since approval" not in text
    assert "The wording the product shipped with" in text
    body = s.check("operator_policy_edited")
    assert "1 edited by the firm" in body
    stored = json.loads(Path(world["env"]["I485_POLICIES_FIRM"]).read_text(encoding="utf-8"))["edits"]["NOT-EOIR"]
    assert stored[-1]["by"] == "Ana Attorney" and stored[-1]["set"] and "Yes" in stored[-1]["set"].values()

    # the sign-off card says whose words these are
    s.open("demo-ana", "attorney")
    body = s.check("operator_policy_on_signoff")
    assert "Edited by the firm" in body and "The firm's own The firm's" not in body and f"The firm's wording and answer, edited by Ana Attorney on {TODAY}" in body
    assert "answers the court question Yes" in body

    # switched off: it fills nothing; switched on again with the shipped answer
    s.page.goto("about:blank")
    s.page.goto(world["review"] + "#settings:policy-NOT-EOIR")
    s.page.wait_for_selector("details.policy[open]")
    s.page.locator("details.policy[open]").get_by_role("button", name="Switch off for the firm").click()
    assert s.toast() == "Switched off."
    s.settle(300)
    assert "switched off by Ana Attorney" in s.page.locator("details.policy[open]").inner_text()
    s.open("demo-ana", "attorney")
    assert "answers the court question Yes" not in s.check("operator_policy_off")
    s.page.goto("about:blank")
    s.page.goto(world["review"] + "#settings:policy-NOT-EOIR")
    s.page.wait_for_selector("details.policy[open]")
    card = s.page.locator("details.policy[open]")
    card.get_by_role("button", name="Switch on again").click()
    s.toast()
    s.settle(300)
    card = s.page.locator("details.policy[open]")
    card.get_by_label("Words for Not filing with the immigration court").fill("The folder shows the client is not filing with the immigration court: the court question is answered No.")
    card.locator("select").select_option("No")
    card.get_by_role("button", name="Save").click()
    s.toast()

    # a paralegal reads them and changes nothing
    p = paralegal
    p.page.goto("about:blank")
    p.page.goto(world["review"] + "#settings:policy-NOT-EOIR")
    p.page.wait_for_selector("details.policy[open]")
    p.settle(300)
    card = p.page.locator("details.policy[open]")
    assert card.get_by_role("button", name="Save").count() == 0 and card.get_by_role("button", name="Switch off for the firm").count() == 0
    assert card.get_by_label("Words for Not filing with the immigration court").is_disabled()
    p.check("operator_policies_paralegal")


def test_the_n400_questions_are_cards_not_a_wall_of_rows(world, paralegal):
    """A card per question, grouped by part with one Save per group (tests/e2e/test_fourth_visit.py saves one)."""
    s = paralegal
    s.open("case-spouse", "packet", "n400")
    s.page.wait_for_selector("article.card")
    for group in s.page.locator("details.qgroup:not([open]) > summary").all():
        group.click()
    cards = s.page.locator("article.card")
    assert cards.count() > 40 and s.page.locator("#main table.rows button:has-text('Save')").count() == 0  # a card per question, no wall of rows
    body = s.check("operator_n400_cards")
    assert "EVER claimed to be a U.S. citizen" in body
