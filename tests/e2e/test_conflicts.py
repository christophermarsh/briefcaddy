"""The conflict search in the browser (src/conflicts.py): Add a client with a hit, the decision, the attorney's Settings page, and what a paralegal
sees of a hit on a case she may not open. The world's cases are clones of one made-up client, Ana Clara Exemplo Souza (tests/e2e/world.py); her
asylum case is restricted by law. Everyone here is made up."""

from __future__ import annotations

import json


def _list(s, world):
    s.page.goto("about:blank")
    s.page.goto(world["review"] + "#all")
    s.page.wait_for_selector("#client-rows")
    s.settle(300)


def _settings(s, world):
    s.page.goto("about:blank")
    s.page.goto(world["review"] + "#settings")
    s.page.wait_for_selector(".setsec")
    s.settle(400)


def test_add_a_client_with_a_hit_the_decision_and_the_attorneys_settings(world, attorney, paralegal):
    import restricted
    import world as w

    asylum = world["clients"] / "case-asylum"
    named = any(p.get("email") == w.PARALEGAL[0] for p in restricted.record(asylum)["people"])  # another file may have named her on it
    if named:
        restricted.name_person(asylum, w.PARALEGAL[0], False, w.ATTORNEY[1], "attorney")
    try:
        s = paralegal
        _list(s, world)
        s.page.get_by_role("button", name="Add a client").click()
        form = s.page.locator("#add-client-form")
        form.get_by_label("Client's full name").fill("Ana Clara Exemplo Souza")
        form.get_by_label("Client's email address").fill("ana.h2.conflict@example.com")
        form.get_by_label("Client's phone number").fill("(555) 010-8282")
        form.get_by_label("Client's date of birth").fill("03/14/2006")
        form.get_by_label("Other side, person 1: full name").fill("Jose Exemplo Souza")
        form.get_by_label("Other side, person 1: who they are").select_option("adverse")
        form.get_by_role("checkbox", name="Email").check()
        s.page.locator("#add-client-save").click()
        said = s.toast()
        assert said.startswith("The conflict search found hits.")
        box = s.page.locator("#add-conflict")
        text = box.inner_text()
        assert "Strong hit" in text and "What a hit means is the attorney's call." in text and "The names match and the birth date agrees." in text
        assert "A hit on a case you cannot open: ask an attorney." in text and "case-" not in text  # the asylum case is restricted by law; and no case is named by its id
        hits = box.locator(".conflict-hits").inner_text()
        assert "conflict" not in hits.lower()  # a hit is a hit, never "a conflict": what it means is the attorney's call
        assert box.get_by_role("radio", name="Conflict: an attorney waived it.").count() == 0  # the waiver is the attorney's
        s.check("conflicts_add_hits")
        s.page.locator("#add-client-save").click()  # no choice yet
        assert "Choose what you decided" in s.toast(ok=False)
        box.get_by_role("radio", name="Not yet decided: an attorney decides.", exact=False).check()
        s.page.locator("#add-client-save").click()
        said = s.toast()
        assert said.startswith("Added Ana Clara Exemplo Souza.") and "waiting for an attorney's decision" in said
        row = s.page.locator("tr.row[data-client^='ana-clara-exemplo-souza']")
        row.wait_for()
        assert "waiting for an attorney's decision" in row.inner_text() and row.get_by_role("button", name="Invite", exact=True).count() == 0
        s.check("conflicts_added_held")
        _settings(s, world)
        assert s.page.locator("#set-conflicts").count() == 0  # the search by hand and the list are the attorney's
        s.check("conflicts_paralegal_settings")

        a = attorney
        _settings(a, world)
        a.page.wait_for_selector("#set-conflicts #conflict-hand-search")
        a.page.fill("input[aria-label='Full name to search for']", "Ze Exemplo Souza")
        a.page.locator("#conflict-hand-search").click()
        a.page.wait_for_selector("#conflict-hand-result .hit")
        result = a.page.locator("#conflict-hand-result").inner_text()
        assert "allowing a nickname" in result and "Weak hit" in result
        a.page.locator("#conflict-hand-result").get_by_role("radio", name="No conflict.").check()
        a.page.locator("#conflict-hand-record").click()
        assert a.toast() == "Recorded: No conflict."
        a.check("conflicts_hand_search")
        a.page.locator("#conflicts-waiting summary").click()
        item = a.page.locator(".waiting-case").first
        item.wait_for()
        text = item.inner_text()
        assert "Ana Clara Exemplo Souza" in text and "Restricted case" in text and "A hit on a case you cannot open" not in text  # the attorney sees every hit in full
        assert "case-" not in text  # cases are named by their client, never by their id
        a.check("conflicts_waiting")
        item.get_by_role("radio", name="Conflict: an attorney waived it.").check()
        item.get_by_label("Why the conflict was waived").fill("Made-up reason: the same client, a new matter.")
        item.get_by_role("button", name="Record the decision").click()
        assert a.toast().startswith("Recorded.")
        a.page.wait_for_function("() => document.querySelector('#conflicts-waiting-list').innerText.includes('Nothing is waiting')")
        a.page.locator("#conflicts-log summary").click()
        a.page.wait_for_selector("#conflict-checks table")
        log = a.page.locator("#conflict-checks").inner_text()
        assert "Conflict: an attorney waived it" in log and "A search by hand" in log and "Paulo Paralegal" in log
        assert a.page.locator("#conflict-checks-csv").get_attribute("href").startswith("/api/conflicts.csv")
        a.check("conflicts_log")
        # the client's invitation goes now
        _list(s, world)
        row = s.page.locator("tr.row[data-client^='ana-clara-exemplo-souza']")
        assert row.locator(".conflict-held").count() == 0 and row.get_by_role("button", name="Invite", exact=True).count() == 1
    finally:
        if named:
            restricted.name_person(asylum, w.PARALEGAL[0], True, w.ATTORNEY[1], "attorney", w.PARALEGAL[1])
    rows = [json.loads(x) for x in (world["clients"].parent / "conflict_checks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in rows][-2:] == ["decision", "decision"] and rows[-1]["decision"] == "waived" and rows[-1]["by"] == w.ATTORNEY[1]
