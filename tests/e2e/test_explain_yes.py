"""Explain the Yes answers, in the browser (src/part14_explain.py, brief L2): a client in removal proceedings with an approved I-360 answers Part 9
items 1 and 14 Yes; the packet is held, one line per item; the tab suggests item 14's explanation from the firm's DRAFT wording with the receipt and
the approval date filled from the I-360's notice and says the attorney adds the citation; item 1 has no wording, so a person writes it; the attorney
approves both, a change takes item 1's approval back, and once approved again the packet is no longer held by them. Everyone here is made up (the
demo client, cloned). E2E_SHOTS keeps a screenshot of each step."""

from __future__ import annotations

import pytest

CASE = "case-explain"
ITEM14 = ("Yes, I was placed in removal proceedings before the immigration court, and those proceedings are pending. I am applying to adjust status based "
          "on my approved Special Immigrant Juvenile petition (Form I-360, receipt number IOE0912345678), approved on 05/01/2025.")


@pytest.fixture(scope="module")
def explain_case(world):
    import world as w

    d = w.clone(world["root"], "case-court", CASE)
    (d / "status.json").unlink(missing_ok=True)  # the court case as the world made it: another test may have recorded a hearing on case-court since
    w.add_fact(d, "applicant.part9.in_removal_proceedings", "Yes", "nta.pdf", "notice_to_appear")
    w.add_notice(d, "IOE0912345678", "I-360", "approval", "2025-05-01")
    return d


def _until(page, item: str, words: str) -> None:
    """Waits for the tab, drawn again after a change, to show these words on the item's card (the screen is redrawn after the answer comes back)."""
    page.wait_for_function("([s, t]) => { const e = document.querySelector(s); return !!e && e.innerText.includes(t); }",
                           arg=[f'section[data-item="{item}"]', words], timeout=30000)


def _press(attorney, item: str, button: str, then: str) -> None:
    attorney.page.locator(f'section[data-item="{item}"]').locator("button", has_text=button).click()
    attorney.toast()
    _until(attorney.page, item, then)
    attorney.settle()


def _approve_keeping(attorney, item: str) -> None:
    """The attorney approves a text that holds a place and a date: the firm keeps what is approved as one of its own wordings (src/wordings.py), so the card first asks
    "make this a blank?" for each; the default (a blank) is taken."""
    card = attorney.page.locator(f'section[data-item="{item}"]')
    card.locator("button", has_text="Approve for Part 14").click()
    keep = card.locator('button[id^="ex-keep-approve-"]')
    # a place no fact of the case holds is offered (the panel); the client's own city is a blank already and nothing is asked (the approval goes straight through)
    attorney.page.wait_for_function("(s) => { const c = document.querySelector(s); return !!c && (!!c.querySelector('button[id^=\"ex-keep-approve-\"]') || c.innerText.includes('Approved by')); }",
                                    arg=f'section[data-item="{item}"]', timeout=30000)
    if keep.count():
        keep.click()
        attorney.toast()
    _until(attorney.page, item, "Approved by")
    attorney.settle()


def test_the_tab_suggests_the_attorney_approves_and_the_packet_is_no_longer_held(world, explain_case, attorney):
    page = attorney.page
    attorney.open(CASE, "packet", "i485")
    held = attorney.text()
    assert "Part 9, item 14 says Yes and has no explanation in Part 14." in held and "Part 9, item 1 says Yes and has no explanation in Part 14." in held

    attorney.open(CASE, "explain")
    _until(page, "14", "Waiting for the attorney")
    text = attorney.check("explain-1-start")
    assert "Explain the Yes answers" in text and "Holding the packet" in text
    assert "The voice for Part 14 entries is not set for" in text
    assert "Part 9, item 1" in text and "Part 9, item 14" in text
    assert "No wording ships for this item" in text  # item 1: a person writes it
    item14 = page.locator('section[data-item="14"]')
    assert item14.locator("textarea").input_value() == ITEM14
    words = item14.inner_text()
    assert "The I-360 approval notice" in words and "No citation in the text: the attorney adds the citation" in words

    # item 1: no wording ships, the attorney types it, saves it, approves it
    page.locator('section[data-item="1"] textarea').fill("Yes, I am a member of a church choir in Springfield, Massachusetts, since 06/01/2022.")
    _press(attorney, "1", "Save the text", "Edited by")
    _approve_keeping(attorney, "1")  # the text holds a place and a date: the card asks "make this a blank?" before the firm keeps it (brief L3)
    # item 14: approved as suggested
    _press(attorney, "14", "Approve for Part 14", "Approved by")
    # the banner is redrawn a moment after the card says "Approved by": wait for it to go, then look (a one-shot read caught it in between once)
    page.wait_for_function("() => !document.body.innerText.includes('Holding the packet')", timeout=10000)
    text = attorney.check("explain-2-approved")
    assert "Holding the packet" not in text

    # a change after the approval takes it back
    page.locator('section[data-item="1"] textarea').fill("Yes, I am a member of a church choir in Springfield, Massachusetts.")
    _press(attorney, "1", "Save the text", "Waiting for the attorney")
    text = attorney.check("explain-3-changed")
    assert "Part 9, item 1 says Yes and has no explanation in Part 14." in text and "Part 9, item 14" not in text.split("Holding the packet")[1].split("A suggestion")[0]
    _approve_keeping(attorney, "1")

    attorney.open(CASE, "packet", "i485")
    after = attorney.check("explain-4-packet")
    assert "Part 9, item" not in after
