"""The client has no such document (src/absence.py), in the browser, on a made-up client whose folder has no passport, visa or I-94: the Documents
tab lists the papers the filing asks about; a paralegal records that the client has no passport, with a reason and a few words; the filled I-485's
item 10 reads NOT APPLICABLE; the packet's checklist says "not available"; the client's own "No" about the I-94 sits beside its row and is never
the mark; and Take the mark off puts every box back. Everyone here is made up (EXEMPLO).
"""

from __future__ import annotations

import io
import json
import re
import shutil

import pytest
from pypdf import PdfReader
import schema_path

CASE = "case-nopaper"
ITEM_10 = ("Pt1Line10_PassportNum[0]", "Pt1Line10_Passport[0]", "Pt1Line10_ExpDate[0]", "Pt1Line10_VisaNum[0]", "Pt1Line10_NonImmDate[0]")


@pytest.fixture(scope="module")
def nopaper(world):
    """A copy of the demo client with no passport, visa or I-94 anywhere: not in the folder, not in what was read from it."""
    w = world["world"]
    d = w.clone(world["clients"].parent, "demo-ana", CASE)
    w.drop_facts(d, "applicant.travel_document", "applicant.visa_", "applicant.i94_number", "applicant.i94_class", "applicant.i94_admit", "applicant.i94_family",
                 "applicant.i94_given", "folder.passport.", "questionnaire.has_i94_or_parole", "questionnaire.visa")
    w.drop_docs(d, "passport", "i94", "visa")
    path = d / "documents.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["documents"] = [r for r in data["documents"] if r["type"] not in ("passport", "i94", "visa")]
    path.write_text(json.dumps(data), encoding="utf-8")
    w.add_fact(d, "questionnaire.has_i94_or_parole", "No", "portal questionnaire", "portal")
    from fill import load_field_map
    from review.state import refill

    refill(d, load_field_map(schema_path.path("field_map", "i485")), schema_path.path("template", "i485"))  # the demo's form still holds the passport it had
    yield d
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    shutil.rmtree(meta["source_folder"], ignore_errors=True)
    shutil.rmtree(d, ignore_errors=True)


def _form(world, screen) -> dict:
    body = screen.page.request.get(world["review"].rstrip("/") + f"/api/filled?client={CASE}").body()
    return PdfReader(io.BytesIO(body)).get_fields()


def _item(fields, short):
    return fields[next(n for n in fields if n.endswith(short))].get("/V")


def _row(screen, paper: str):
    return screen.page.locator("#paper-" + paper)


def _open_papers(screen) -> None:
    """The Documents tab with the I-485's papers listed (the tab opens on the filing the case's stage leads to)."""
    screen.open(CASE, "documents")
    picker = screen.page.get_by_label("The filing whose papers are listed")
    if picker.input_value() != "i485":
        picker.select_option("i485")
        screen.settle()


def test_a_paralegal_records_that_the_client_has_no_passport_and_the_form_follows(world, paralegal, nopaper):
    _open_papers(paralegal)
    paralegal.check("absence-list")
    papers = paralegal.page.locator("#papers")
    assert "Papers this filing asks about" in papers.inner_text()
    names = [t.strip() for t in papers.locator("#paper-rows tr td:first-child b").all_inner_texts()]
    assert names[:3] == ["Passport or travel document", "U.S. visa", "Form I-94 (arrival record)"] and "Birth certificate" in names
    passport = _row(paralegal, "passport")
    assert "Not in the folder" in passport.inner_text()
    # the client said No about the I-94: the client's word, beside the row, and not the mark
    i94 = _row(paralegal, "i94")
    said = i94.inner_text()
    assert "Does the client have an I-94 or a parole paper?: No" in said and "the client's word: it is not the mark" in said and "Not in the folder" in said
    assert "The client has none" not in said
    # the birth certificate cannot be done without; the I-94 may be, and the line it rests on is on the row
    assert "The filing cannot do without this paper." in _row(paralegal, "birth_certificate").inner_text()
    assert "This filing may do without it: Form I-485 Instructions, edition 09/18/26, page 8" in i94.inner_text()
    assert _item(_form(world, paralegal), ITEM_10[0]) in (None, "")  # the box is blank before the mark

    passport.locator("summary").first.click()
    passport.get_by_label("Why the client has no passport or travel document").select_option(label="The client never had one")
    passport.get_by_label("A note about the passport or travel document").fill("came as a small child")
    passport.get_by_role("button", name="Save").click()
    assert paralegal.toast() == "Recorded: the client has no passport or travel document."
    paralegal.settle()
    passport = _row(paralegal, "passport")
    marked = passport.inner_text()
    assert "The client has none" in marked and "Marked by Paulo Paralegal" in marked and "the client never had one (came as a small child)" in marked
    assert re.search(r"on \d\d/\d\d/\d{4}", marked)
    assert "Not in the folder" in _row(paralegal, "i94").inner_text()  # only the passport was marked

    # item 10 on the filled form: the five boxes
    fields = _form(world, paralegal)
    for short in ITEM_10[:3]:
        assert _item(fields, short) == "NOT APPLICABLE", short
    assert _item(fields, "P1Line12_I94[0]") in (None, "")  # the I-94 was not marked: its boxes are the case's own
    paralegal.check("absence-marked")

    # the packet's checklist
    paralegal.open(CASE, "packet", "i485")
    text = paralegal.text()
    assert "Passport or travel document: not available: the client never had one (came as a small child). Marked by Paulo Paralegal" in text
    assert "I-485, Part 1, Item 12: I-94 number: blank, with no value and no mark" in text  # the I-94 is still a blank nobody has answered
    assert "—" not in text and " -- " not in text
    paralegal.check("absence-packet")

    # the Decision log has it, with who and when; taking the mark off puts the boxes back
    _open_papers(paralegal)
    _row(paralegal, "passport").get_by_role("button", name="Take the mark off").click()
    assert paralegal.toast() == "The mark is taken off."
    paralegal.settle()
    try:  # the row is drawn again after the app's answer: a read straight after the toast may be of the row before it
        paralegal.page.locator("#paper-passport", has_text="Not in the folder").wait_for()
    except Exception:  # noqa: BLE001 -- the assert that follows says what the row held
        pass
    assert "Not in the folder" in _row(paralegal, "passport").inner_text()
    assert _item(_form(world, paralegal), ITEM_10[0]) in (None, "")
    paralegal.open(CASE, "done")
    assert "The client has no passport or travel document" in paralegal.text()  # kept in the log, reopened
    paralegal.check("absence-undone")
