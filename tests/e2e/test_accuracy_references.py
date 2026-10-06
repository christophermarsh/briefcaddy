"""Accuracy against hand-filled references on the screen (src/accuracy.py): Keeping current's Reader accuracy card with the nights'
history in words and the sentence for a fall, the Accuracy record's list of boxes that differ, and an attorney marking that the
reference was the one that was wrong. The made-up world's case-sij gets a hand-filled I-485 (made up by the sample script)."""

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

import accuracy  # noqa: E402
import accuracy_samples  # noqa: E402
import clock  # noqa: E402
import version  # noqa: E402

REFERENCE_EDITS = accuracy_samples.EDITS[("sample-sij", "i485")]  # the made-up person's typing: the world's case-sij is the same made-up client


@pytest.fixture
def references(world, monkeypatch):
    """A hand-filled I-485 for the world's case-sij in the firm's reference folder, and the nights before tonight's."""
    env = world["env"]
    for name in ("I485_ACCURACY_HISTORY", "I485_REFERENCE", "PORTAL_DATA"):
        monkeypatch.setenv(name, env[name])
    clients = Path(world["clients"])
    ref_dir = Path(env["I485_REFERENCE"])
    ref_dir.mkdir(parents=True, exist_ok=True)
    accuracy_samples._edit(clients / "case-sij" / "i485_filled.pdf", ref_dir / "case-sij.pdf", REFERENCE_EDITS)
    yield {"clients": clients, "dir": ref_dir, "history": Path(env["I485_ACCURACY_HISTORY"])}
    for path in (*ref_dir.glob("*"), Path(env["I485_ACCURACY_HISTORY"]), accuracy.latest_path(clients)):
        path.unlink(missing_ok=True)
    ref_dir.rmdir()


def _earlier_night(history: Path) -> None:
    """A night eight days ago, on the release before this one, when every box was identical: tonight's run fell below it."""
    tonight = json.loads(history.read_text(encoding="utf-8").splitlines()[-1])
    before = dict(tonight, day=str(clock.today().fromordinal(clock.today().toordinal() - 8)), version="2026.10.4", identical=tonight["boxes"])
    before["at"] = before["day"] + "T03:00:00-04:00"
    before["forms"] = {k: dict(f, identical=f["boxes"]) for k, f in tonight["forms"].items()}
    history.write_text(json.dumps(before) + "\n" + json.dumps(tonight) + "\n", encoding="utf-8")


def test_the_reader_accuracy_card_the_list_of_differences_and_the_mark(world, attorney, paralegal, references):
    said = accuracy.nightly(references["clients"])
    assert "1 reference (the firm's own references)" in said
    _earlier_night(references["history"])
    record = json.loads(references["history"].read_text(encoding="utf-8").splitlines()[-1])
    boxes, identical = record["boxes"], record["identical"]
    assert 0 < identical < boxes and record["version"] == version.VERSION

    # Keeping current: the card says how the figure has moved, in words, and says so when it fell after an update
    attorney.page.get_by_role("button", name=re.compile("Keeping current")).click()
    attorney.settle()
    card = attorney.page.locator("#reader-accuracy-card")
    card.wait_for()
    text = card.inner_text()
    pct = lambda n: f"{int(100 * n / boxes + 0.5)}% of {boxes:,}"  # noqa: E731
    assert f"Identical boxes {pct(identical)} on {clock.us_date(record['day'])}, {pct(boxes)} a week ago, on 1 reference (the firm's own references)." in text
    assert f"Identical boxes fell from {pct(boxes)} to {pct(identical)} after the {version.VERSION} update: a person should look." in text
    body = attorney.check("keeping-current-accuracy")
    assert "schemas/" not in body and "src/" not in body and "—" not in text and " -- " not in text

    # the accuracy record: last night's figures and the boxes that differ
    card.get_by_role("button", name="Open the accuracy record").click()
    attorney.settle()
    section = attorney.page.locator("#accuracy-references")
    section.wait_for()
    assert "Against hand-filled references" in attorney.text() and f"1 reference (the firm's own references), {pct(identical)} boxes identical" in section.inner_text()
    rows = attorney.page.locator("#accuracy-different tr[data-case]")
    differ = [b for r in accuracy.latest(references["clients"])["results"] for b in r["boxes"] if b["kind"] == "different"]
    assert rows.count() == len(differ) >= 3  # every box both sides filled that is not identical; the unfilled ones are not listed (other tests may have changed the case)
    row = rows.filter(has_text="SOROCABA SP")
    assert "case-sij" in row.inner_text() and "Birth certificate reader" in row.inner_text() and "Read from a document" in row.inner_text()
    assert "/N" not in attorney.text() and "/Y" not in attorney.text()  # a ticked Yes or No is said in words, never as a form code
    if any(b["field"] == "Pt9Line76_YesNo" for b in differ):  # Item 74: a No the person typed against the pipeline's Yes
        item74 = rows.filter(has_text="unlawfully present").inner_text()
        assert "Part 9, Item 74" in item74 and "	No	" in item74 and "	Yes	" in item74
    assert "123456789" not in attorney.text()  # and the unfilled Social Security number is not among the boxes that differ

    # the mark: who, when and why, kept beside the reference and counted from then on
    row.get_by_role("button", name="The reference was wrong").click()
    row.get_by_role("textbox", name="Why the reference was wrong").fill("The birth certificate gives the city with no state after it.")
    row.get_by_role("button", name="Save mark").click()
    assert attorney.toast() == "Marked."
    attorney.settle()
    row = attorney.page.locator("#accuracy-different tr[data-case]").filter(has_text="SOROCABA SP")
    marked = json.loads((references["dir"] / "case-sij.marks.json").read_text(encoding="utf-8"))["marks"]
    assert len(marked) == 1 and marked[0]["by"] == "Ana Attorney" and marked[0]["field"] == "Pt1Line7_CityTownOfBirth[0]"
    assert marked[0]["reason"] == "The birth certificate gives the city with no state after it." and clock.parse(marked[0]["at"])
    shown = row.inner_text()
    assert f"Marked by Ana Attorney on {clock.us_date(marked[0]['at'])}: The birth certificate gives the city with no state after it." in shown
    assert "The reference's own error" in shown and row.get_by_role("button", name="The reference was wrong").count() == 0
    assert "1 different box was marked as the reference's own error." in attorney.page.locator("#accuracy-references").inner_text()
    attorney.check("accuracy-record-references")

    # a paralegal has neither the card nor the routes
    paralegal.page.get_by_role("button", name=re.compile("Keeping current")).click()
    paralegal.settle()
    assert paralegal.page.locator("#reader-accuracy-card").count() == 0
    request = paralegal.page.context.request
    assert request.get(world["review"] + "api/accuracy/references").status == 403
    posted = request.post(world["review"] + "api/accuracy/mark", data=json.dumps({"case": "case-sij", "form": "i485", "field": "Pt7Line3_HeightInches[0]", "reason": "x"}),
                          headers={"X-Review-App": "1", "Content-Type": "application/json"})
    assert posted.status == 403
    assert len(json.loads((references["dir"] / "case-sij.marks.json").read_text(encoding="utf-8"))["marks"]) == 1


def test_with_no_references_of_the_firms_own_the_screen_says_the_ones_it_shows_are_made_up(world, attorney, references, monkeypatch):
    for path in references["dir"].glob("*.pdf"):
        path.unlink()
    said = accuracy.nightly(references["clients"])
    assert "(the made-up references that ship with the product)" in said
    attorney.open("", "")  # the work list
    attorney.page.get_by_role("button", name=re.compile("Keeping current")).click()
    attorney.settle()
    card = attorney.page.locator("#reader-accuracy-card")
    card.wait_for()
    assert "(the made-up references)" in card.inner_text()
    card.get_by_role("button", name="Open the accuracy record").click()
    attorney.settle()
    attorney.page.locator("#accuracy-references").wait_for()
    text = attorney.text()
    assert "made-up references that come with the product" in text and "not how accurate the filling is" in text and "Fewer than 10 references" in text
    assert attorney.page.get_by_role("button", name="The reference was wrong").count() == 0  # only the firm's own references are marked
    assert "Marked by A made-up reviewer on 10/03/2026" in text
    attorney.check("accuracy-record-made-up")
