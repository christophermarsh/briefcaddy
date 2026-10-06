"""The loop that finds the next wrong box, in the browser (src/audit_fill.py, src/rebuild.py): Reports' "Boxes the office changes" with its line at three cases and the
cases listed only to those who may open them; the boxes the office changed on a case, inside the case; and, after a release, "Rebuild the forms" on the packet tab with
the boxes that changed (old and new, with the reason) for a person to confirm. The made-up world: the demonstration client, cloned (every name EXEMPLO)."""

from __future__ import annotations

import json
from pathlib import Path
import schema_path

OTHER_NAMES = "applicant.na.other_names"
ROW = {"summary": {"name": "ANA SAMPLE", "a_number": "A099000001", "dob": "2006-01-02"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}


def _catalog(world, monkeypatch):
    """The catalog the audit would have written: Part 1, Item 2 changed from NOT APPLICABLE to a name on four cases (one of them protected), and a date of birth on two."""
    import audit_fill

    monkeypatch.setenv("I485_AUDIT_FILL", world["env"]["I485_AUDIT_FILL"])
    clients = Path(world["clients"])
    rows = [audit_fill._row(c, audit_fill.FORM, "i485", OTHER_NAMES, "Part 1, Item 2", "Other names used", "NOT APPLICABLE", "EXEMPLO TESTE", basis="reference")
            for c in ("case-sij", "case-family", "case-spouse", "case-asylum")]
    rows += [audit_fill._row(c, audit_fill.SAVE, "i485", "applicant.dob", "Part 1, Item 5", "Date of birth", "03/14/2006", "03/14/2005", by="Paulo Paralegal",
                             at="2026-10-02T10:00:00-04:00", reason="The birth certificate shows 2005.") for c in ("case-resident", "case-court")]
    audit_fill.write(clients, {"version": 1, "software": "2026.10.9", "forms": {"day": "2026-10-01", "cases": 4, "forms": 4, "boxes": 2800, "identical": 2796, "changed": 4},
                               "saves": {"day": "2026-10-03", "cases": 2, "saves": 2}, "rows": rows, "skipped": []})
    return audit_fill.group_id("i485", OTHER_NAMES, "na_to_value")


def test_reports_name_the_box_the_office_keeps_changing_and_list_the_cases_only_to_those_who_may_open_them(world, attorney, paralegal, monkeypatch, asylum_closed_to_the_paralegal):
    gid = _catalog(world, monkeypatch)
    for who, listed in ((attorney, 4), (paralegal, 3)):  # the protected asylum case is the attorney's to see: for the paralegal it is in no count, row or line
        line = f"The office changes Part 1, Item 2 (Other names used) from NOT APPLICABLE to a name on {listed} cases: a rule may be missing."
        who.page.goto("about:blank")
        who.page.goto(world["review"] + "#reports")
        who.page.locator("#report-audit").wait_for()
        callout = who.page.locator(".audit-line")
        assert callout.count() == 1 and line in callout.inner_text()
        row = who.page.locator(f"#report-row-{gid}")
        text = row.inner_text()
        assert "Part 1, Item 2: Other names used" in text and "from NOT APPLICABLE to a name" in text and "Form I-485" in text and "Filed and corrected forms" in text
        assert row.locator("td").nth(3).inner_text() == str(listed)  # the cases this reader may open
        assert f"{listed} clients" in text
        two =who.page.locator("#report-audit tr", has_text="Date of birth")
        assert two.count() == 1 and "Reviewers' Saves" in two.inner_text()  # two cases: a row, no line
        assert "EXEMPLO TESTE" not in who.page.locator("#main").inner_text() and "03/14/2006" not in who.page.locator("#main").inner_text()  # never a client's value here
        assert "Only the cases you may open are counted here." in who.page.locator("#main").inner_text()
        who.check("reports-audit-" + ("attorney" if who is attorney else "paralegal"))
    # the line links to its group
    attorney.page.get_by_role("link", name="Show the cases").click()
    attorney.page.wait_for_function("(id) => document.getElementById(id).classList.contains('flash')", arg=f"report-row-{gid}")
    body = attorney.page.locator("#main").inner_text()
    assert ".json" not in body and "—" not in body and " -- " not in body and "audit_fill" not in body


def test_the_values_are_inside_the_case(world, attorney, monkeypatch):
    _catalog(world, monkeypatch)
    attorney.open("case-sij", "packet", "i485")
    card = attorney.page.locator("#audit-card")
    card.wait_for()
    card.locator("summary").click()
    text = card.inner_text()
    assert "Boxes the office changed on this case (1)" in text and "Part 1, Item 2: Other names used" in text
    assert "NOT APPLICABLE" in text and "EXEMPLO TESTE" in text and "Changed the same way on 2 other cases." in text  # the protected asylum case is not one of them
    assert "Nothing here changes a form: a person decides whether a rule is missing." in text
    attorney.check("audit-case-card")
    attorney.open("case-resident", "packet", "i485")
    attorney.page.locator("#audit-card").locator("summary").click()
    assert "03/14/2006" in attorney.page.locator("#audit-card").inner_text() and "The birth certificate shows 2005." in attorney.page.locator("#audit-card").inner_text()


def test_after_a_release_the_case_offers_a_rebuild_and_the_boxes_that_changed_wait_for_a_person_to_confirm(world, paralegal, monkeypatch):
    import world as w

    import packet
    import version
    from fill import load_field_map
    from review.state import refill

    w.clone(world["root"], "demo-ana", "case-rebuild")
    d = Path(world["clients"]) / "case-rebuild"
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    meta["derivation"]["rules"] = [r for r in meta["derivation"]["rules"] if r != "OVERSTAY-01"]  # processed by a release that did not have the overstay rule yet
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(version, "VERSION", "2026.9.1")
    repo = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("I485_EVENTS", world["env"]["I485_EVENTS"])
    refill(d, load_field_map(schema_path.path("field_map", "i485", schema_path.schemas_in(repo))), schema_path.path("template", "i485", schema_path.schemas_in(repo)))
    packet.build(d, ROW, "Paulo Paralegal", packet.load_filing("i485"))
    monkeypatch.undo()

    paralegal.open("case-rebuild", "packet", "i485")
    offer = paralegal.page.locator("#rebuild-offer")
    offer.wait_for()
    assert f"This packet was built with release 2026.9.1. This is release {version.VERSION}." in offer.inner_text()
    assert "Nothing changes until you press the button." in offer.inner_text()
    paralegal.check("rebuild-offer")
    paralegal.page.get_by_role("button", name="Rebuild the forms").click()
    assert "The forms are rebuilt" in paralegal.toast()
    paralegal.settle()
    card = paralegal.page.locator("#rebuild-card")
    rows = card.locator("#rebuild-changes tr[data-why]")
    assert rows.count() == 2 and paralegal.page.locator("#rebuild-offer").count() == 0
    text = card.inner_text()
    assert "Rebuilt by Paulo Paralegal on" in text and "2 boxes changed." in text and "Waiting for your confirmation" in text
    assert "A rule new in this release: the overstay rule" in text and "Form I-485" in text
    assert {rows.nth(i).locator("td").nth(2).inner_text() for i in range(2)} | {rows.nth(i).locator("td").nth(3).inner_text() for i in range(2)} <= {"Yes", "No", "Left blank"}
    assert "The forms were rebuilt after a new release and the boxes that changed have not been confirmed." in paralegal.text()  # the packet is not ready
    assert ".json" not in text and "OVERSTAY" not in text and "—" not in text and " -- " not in text
    paralegal.check("rebuild-changes")
    assert (d / "rebuild.json").exists() and "The forms and the packet shown are the rebuilt ones." in card.locator("#rebuild-shows").inner_text()
    # the person may decline: every file goes back as it was, and the rebuild is offered again
    before_rebuild = (d / "rebuild_previous" / "i485.i485_filled.pdf").read_bytes()
    card.get_by_role("button", name="Keep the previous forms").click()
    assert "The previous forms are back as they were." in paralegal.toast()
    paralegal.settle()
    assert paralegal.page.locator("#rebuild-offer").count() == 1 and paralegal.page.locator("#rebuild-changes").count() == 0
    assert "kept the previous forms on" in paralegal.page.locator("#rebuild-kept").inner_text() and (d / "i485_filled.pdf").read_bytes() == before_rebuild
    assert not (d / "rebuild_previous").exists() and "have not been confirmed" not in paralegal.text()
    paralegal.check("rebuild-kept")
    paralegal.page.get_by_role("button", name="Rebuild the forms").click()
    assert "The forms are rebuilt" in paralegal.toast()
    paralegal.settle()
    card = paralegal.page.locator("#rebuild-card")
    assert card.locator("#rebuild-changes tr[data-why]").count() == 2
    card.get_by_role("button", name="Confirm these changes").click()
    assert "Confirmed" in paralegal.toast()
    paralegal.settle()
    card = paralegal.page.locator("#rebuild-card")
    assert "Confirmed by Paulo Paralegal on" in card.inner_text() and card.get_by_role("button", name="Confirm these changes").count() == 0
    assert "have not been confirmed" not in paralegal.text()
    paralegal.check("rebuild-confirmed")
