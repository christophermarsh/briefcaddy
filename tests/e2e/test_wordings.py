"""The firm's wording library, in the browser (src/wordings.py, brief L3): an attorney's approval on a case becomes a firm wording (the card asks "make this a
blank?" for the date and place the text still holds); the next case with the same answer is offered it with its blanks, "the office wrote this on 1 case"; a paralegal
picks it, fills the blanks, edits it, and the attorney's approval makes a new version; Settings, Firm wordings: a candidate from a past filing waits until the attorney
approves it in bulk, a wording is retired and stays; Reports counts them. Everyone here is made up (the demo client, cloned). E2E_SHOTS keeps a screenshot of each step."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

A, B, C = "case-word-a", "case-word-b", "case-word-c"
ITEM = "23"
KEY = "applicant.part9.committed_crime"
TEXT_A = "Yes, I was cited for driving without a license in Worcester, Massachusetts on 03/03/2024."
FIRST = "Yes, I was cited for driving without a license in [a place] on [a date]."


@pytest.fixture(scope="module")
def cases(world):
    import world as w

    out = {}
    for name in (A, B, C):
        d = w.clone(world["root"], "case-court", name)
        (d / "status.json").unlink(missing_ok=True)  # the court case as the world made it
        w.drop_facts(d, KEY)  # the demo client answered No: this client says Yes
        w.add_fact(d, KEY, "Yes", "nta.pdf", "notice_to_appear")
        out[name] = d
    return out


def KEEP(page):
    """The panel that asks "make this a blank?" before the firm keeps a wording, on the item's card."""
    return page.locator(f'section[data-item="{ITEM}"] div.callout[id^="ex-keep-"]:not([id^="ex-keep-approve-"])')


def _library(world) -> list[dict]:
    folder = Path(world["clients"]).parent / "wordings" / "i485" / "committed_crime"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.is_dir() else []


def _until(page, words: str, item: str = ITEM) -> None:
    """Waits for the tab, drawn again after a change, to show these words on the item's card."""
    try:
        page.wait_for_function("([s, t]) => { const e = document.querySelector(s); return !!e && e.innerText.includes(t); }", arg=[f'section[data-item="{item}"]', words], timeout=30000)
    except Exception as exc:  # noqa: BLE001 -- say what the screen showed
        raise AssertionError(f"never saw {words!r} on item {item}; the page said: {page.locator('#main').inner_text()[:1500]}") from exc


def _press(who, button: str, then: str, item: str = ITEM) -> None:
    who.page.locator(f'section[data-item="{item}"]').locator("button", has_text=button).first.click()
    who.toast()
    _until(who.page, then, item)
    who.settle()


def test_an_approval_becomes_a_firm_wording_with_the_dates_and_places_as_blanks(world, cases, attorney):
    page = attorney.page
    attorney.open(A, "explain")
    _until(page, "To write")
    card = page.locator(f'section[data-item="{ITEM}"]')
    assert "The firm's own wordings for this answer" not in card.inner_text()  # nothing learned yet
    card.locator("textarea").fill(TEXT_A)
    _press(attorney, "Save the text", "Edited by")
    card.locator("button", has_text="Approve for Part 14").click()
    KEEP(page).wait_for()
    keep = KEEP(page).inner_text()
    assert "Before the firm keeps this wording" in keep and "“Worcester, Massachusetts”" in keep and "“03/03/2024”" not in keep  # only a place is offered
    assert "Every date, number and person's name in the text becomes a blank" in keep and "cannot be found reliably" in keep
    assert "As the firm will keep it: Yes, I was cited for driving without a license in [a place] on [a date]." in keep
    attorney.check("wordings-1-keep")
    page.locator(f'section[data-item="{ITEM}"] button[id^="ex-keep-approve-"]').click()
    attorney.toast()
    _until(page, "Approved by")
    assert "Kept as one of the firm's own wordings, for the next case with the same facts." in page.locator(f'section[data-item="{ITEM}"] [id^="ex-kept-"]').inner_text()
    (one,) = _library(world)
    assert one["text"] == "Yes, I was cited for driving without a license in {place_1} on {date_1}." and one["status"] == "approved"
    assert "Worcester" not in json.dumps(one) and "03/03/2024" not in json.dumps(one)


def test_the_next_case_is_offered_it_a_paralegal_picks_and_edits_and_the_attorney_approves_a_new_version(world, cases, paralegal, attorney):
    page = paralegal.page
    paralegal.open(B, "explain")
    _until(page, "The firm's own wordings for this answer (1)")
    card = page.locator(f'section[data-item="{ITEM}"]')
    offer = card.locator('div.decl-line[id^="ex-firm-"]').first
    text = offer.inner_text()
    assert FIRST in text and "The office wrote this on 1 case, approved by" in text and "The facts that match:" in text
    assert "Blanks to fill: [a place], [a date]." in text
    assert card.locator("textarea").input_value() == ""  # nothing is applied: the text stays empty until a person picks
    paralegal.check("wordings-2-offer")
    card.locator('button[id^="ex-firm-use-"]').first.click()
    paralegal.toast()
    _until(page, "Firm wording chosen by")
    assert card.locator("textarea").input_value() == FIRST
    # the blanks are the paralegal's to fill: the wording is not approved with them, and the paralegal cannot approve
    card.locator("textarea").fill("Yes, I was cited for driving without a license in Boston, Massachusetts on 04/04/2024. The charge was dismissed.")
    _press(paralegal, "Save the text", "Edited by")
    assert card.locator("button", has_text="Approve for Part 14").count() == 0
    # the attorney approves it: a new version of the wording it started from
    attorney.open(B, "explain")
    _until(attorney.page, "Waiting for the attorney")
    attorney.page.locator(f'section[data-item="{ITEM}"]').locator("button", has_text="Approve for Part 14").click()
    KEEP(attorney.page).wait_for()
    assert "“Boston, Massachusetts”" in KEEP(attorney.page).inner_text()
    attorney.page.locator(f'section[data-item="{ITEM}"] button[id^="ex-keep-approve-"]').click()
    attorney.toast()
    _until(attorney.page, "Approved by")
    assert "Kept as a new version of the firm's wording it started from." in attorney.page.locator(f'section[data-item="{ITEM}"] [id^="ex-kept-"]').inner_text()
    mine = sorted(_library(world), key=lambda r: r["number"])
    assert [r["number"] for r in mine] == [1, 2] and mine[1]["parent"] == mine[0]["id"]
    assert mine[1]["text"] == "Yes, I was cited for driving without a license in {place_1} on {date_1}. The charge was dismissed." and "Boston" not in json.dumps(mine[1])
    assert [e["case"] for e in mine[0]["edits"]] == [B]


def test_settings_a_candidate_from_a_past_filing_waits_for_the_attorney_a_wording_is_retired_and_stays(world, cases, attorney, paralegal):
    base = Path(world["clients"]).parent / "wordings" / "i485" / "committed_crime"
    candidate = {"id": "w-pastfiling1", "form": "i485", "edition": "09/18/26", "key": KEY, "part": "9", "item": ITEM, "page": "14", "voice": "client",
                 "office": _library(world)[0]["office"], "office_name": _library(world)[0]["office_name"], "text": "Yes, I received a traffic citation in {place_1} on {date_1}, and it was dismissed.",
                 "slots": [], "pattern": {"present": [], "absent": []}, "status": "candidate", "origin": "past_filing", "created": "2026-10-04T09:00:00-04:00", "approved": None, "parent": None,
                 "number": 1, "uses": [], "edits": [], "from_file": "a-past-case.pdf", "history": []}
    (base / "w-pastfiling1.json").write_text(json.dumps(candidate), encoding="utf-8")
    # offered on no case until approved
    paralegal.open(C, "explain")
    _until(paralegal.page, "The firm's own wordings for this answer (2)")
    assert "traffic citation" not in paralegal.page.locator("#main").inner_text()
    # Settings: the attorney reads it, approves it in bulk, and retires the first wording
    page = attorney.page
    page.goto("about:blank")
    page.goto(world["review"] + "#settings")
    page.locator("#set-firm-wordings").wait_for()
    page.locator("#wordings-candidates").wait_for()
    box = page.locator("#set-firm-wordings")
    text = box.inner_text()
    assert "from past filings, not approved yet (1)" in text.lower() and "Yes, I received a traffic citation in [a place] on [a date], and it was dismissed." in text
    assert "Part 9, item 23" in text and "From a past filing." in text  # (the file is named only to someone who may open the case it is named for: this one names none)
    attorney.check("wordings-3-settings")
    page.locator(".wd-tick").first.check()
    page.locator("#wordings-approve").click()
    attorney.toast()
    page.wait_for_function("() => !document.getElementById('wordings-candidates')")
    approved = box.inner_text()
    assert "From the office's past filings, approved by" in approved and "Not used on a case here yet." in approved
    first = next(r for r in _library(world) if r["number"] == 1 and r["origin"] == "approval")
    card = page.locator(f"#wording-{first['id']}")
    assert "Used on 1 case: " in card.inner_text() and "Picked 1 time; edited after picking 100% of the time." in card.inner_text()
    assert f"{A}" in card.inner_text()  # an attorney may open every case: the ids are listed
    card.locator("input[aria-label='Why this wording is retired']").fill("the office says it another way")
    card.locator("button", has_text="Retire").click()
    attorney.toast()
    page.wait_for_function("(id) => document.getElementById('wording-' + id).innerText.includes('Retired')", arg=first["id"])
    attorney.settle()
    assert next(r for r in _library(world) if r["id"] == first["id"])["status"] == "retired"  # never deleted: the file stays
    assert "Set aside by" in page.locator(f"#wording-{first['id']}").inner_text()
    attorney.check("wordings-4-retired")
    # on the next case: the retired one is gone, the new version and the approved candidate are offered
    paralegal.open(C, "explain")
    _until(paralegal.page, "The firm's own wordings for this answer (2)")
    offered = paralegal.page.locator(f'section[data-item="{ITEM}"] div.callout[id^="ex-firm-"]').inner_text()
    assert "The charge was dismissed." in offered and "traffic citation" in offered and "From the office's past filings, approved by" in offered
    assert "in [a place] on [a date]." in offered
    paralegal.check("wordings-5-offers")


def test_reports_count_the_wordings_and_name_no_case(world, cases, attorney, paralegal):
    for who in (attorney, paralegal):
        who.page.goto("about:blank")
        who.page.goto(world["review"] + "#reports")
        who.page.locator("#report-wordings").wait_for()
        table = who.page.locator("#report-wordings")
        text = table.inner_text()
        assert "answer" in text.lower() and "edited after picking" in text.lower() and "Retired" in text and "Approved" in text and "100%" in text
        assert "case-word" not in who.page.locator("#main").inner_text()
        assert who.page.locator("#csv-wordings").get_attribute("href").startswith("/api/reports.csv?table=wordings")
        who.check("wordings-6-reports-" + ("attorney" if who is attorney else "paralegal"))
        body = who.page.locator("#main").inner_text()
        assert ".json" not in body and "—" not in body and " -- " not in body
