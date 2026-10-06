"""The case's Documents page (src/documents.py), on the made-up demo client: every document with
its name from the firm's list of types, whose it is, its language, dates and quality, each with where
it came from in words (brief G1); a paralegal says whose the Social Security card is and what else it
shows, and both are kept with who and when.
"""

from __future__ import annotations

import json


def _records(world) -> dict:
    data = json.loads((world["clients"] / "demo-ana" / "documents.json").read_text(encoding="utf-8"))
    return {r["type"]: r for r in data["documents"]}


def _chips(screen, name: str = "Social Security card") -> list[str]:
    row = screen.page.locator("#documents tr", has_text=name)
    return [t.replace("×", "").strip() for t in row.locator(".tag.cat").all_inner_texts()]


def _row(screen, name: str) -> str:
    return screen.page.locator("#documents tr", has_text=name).first.inner_text()


# What the owner expects of the demo's five documents: whose (and why), language (and why), dates, quality, what it shows.
FIVE = {
    "Passport": (["From the document", "Portuguese (read from its words)", "Issued 01/11/2022", "Expires 01/10/2032", "Clear",
                  "Estimated: sharp, good contrast"], ["Identity", "Nationality"]),
    "Birth certificate": (["From the document", "Portuguese (read from its words)", "Issued 05/15/2023", "Clear"], ["Identity", "A family relationship"]),
    "I-360 approval notice": (["From the document", "English (a U.S. government document)", "Notice date 08/20/2025", "Clear"], ["A government notice"]),
    "I-94 arrival record": (["From the document", "English (a U.S. government document)", "Arrived 07/15/2019", "Admitted until 01/14/2020", "Clear"],
                            ["Entry to the U.S."]),
    "Social Security card": (["Assumed: the only person on this case", "English (a U.S. government document)",
                              "None on it (a Social Security card has no date)", "Clear"], ["Identity"]),
}


def test_the_demos_five_documents_say_whose_language_dates_quality_and_what_they_show(world, paralegal):
    paralegal.open("demo-ana", "documents")
    main = paralegal.check("documents-five")
    for name, (words, chips) in FIVE.items():
        row = _row(paralegal, name)
        for w in words:
            assert w in row, (name, w, row)
        assert _chips(paralegal, name) == chips, (name, _chips(paralegal, name))
        assert "None found" not in row and "Not read yet" not in row and "Set it if you know" not in row, row
        assert paralegal.page.get_by_label(f"Whose is the {name.lower()}").input_value() == "applicant"
        assert paralegal.page.get_by_label(f"How readable is the {name.lower()}").input_value() == "readable"  # shown as "Clear"
    assert "Entry to the U.S." not in _chips(paralegal, "Passport")  # a passport proves an entry only with a stamp or a visa in it
    quality = paralegal.page.locator("#documents tr", has_text="Passport").first.locator("td").nth(4)
    assert quality.get_attribute("title").startswith("Estimated from the page: sharp, good contrast")
    # the thread with the client sits below the table, never above it
    assert paralegal.page.evaluate("() => { const m = document.getElementById('messages'), t = document.getElementById('documents');"
                                   " return !m || !!(t.compareDocumentPosition(m) & Node.DOCUMENT_POSITION_FOLLOWING); }")
    # the "Add what it shows" box stays inside its card at 1,400 pixels
    overflow = paralegal.page.evaluate("""() => [...document.querySelectorAll('#documents select[aria-label^="Add what"]')].map((s) => {
        const card = s.closest('section').getBoundingClientRect(), box = s.getBoundingClientRect();
        return box.right - card.right; })""")
    assert overflow and max(overflow) <= 0, overflow
    assert "ssn_card" not in main and "only_person" not in main and "language_basis" not in main  # words, never keys


def test_a_paralegal_sets_whose_a_document_is(world, paralegal):
    paralegal.open("demo-ana", "documents")
    paralegal.check("documents-demo")
    main = paralegal.text()
    for words in ("Passport", "Birth certificate", "I-360 approval notice", "I-94 arrival record", "Social Security card", "Portuguese",
                  "Expires 01/10/2032", "From the document"):
        assert words in main, (words, main[:2000])
    assert "ssn_card" not in main and "birth_certificate" not in main and "i360_approval" not in main  # the taxonomy's names, never ids

    whose = paralegal.page.get_by_label("Whose is the social security card")
    assert whose.input_value() == "applicant"  # it names nobody, and the case has nobody but the client: an assumption, shown as one
    whose.select_option(label="Not known yet")  # the paralegal takes the assumption back...
    assert paralegal.toast() == "Saved."
    paralegal.settle()
    paralegal.page.get_by_label("Whose is the social security card").select_option(label="The client")  # ... and says it herself
    assert paralegal.toast() == "Saved."
    paralegal.settle()
    paralegal.page.get_by_label("Add what the social security card shows").select_option(label="Living in the U.S.")
    assert paralegal.toast() == "Saved."

    paralegal.open("demo-ana", "documents")  # kept, with who and when
    paralegal.check("documents-demo-set")
    assert paralegal.page.get_by_label("Whose is the social security card").input_value() == "applicant"
    row = paralegal.page.locator("#documents tr", has_text="Social Security card").inner_text()
    assert "Set by Paulo Paralegal" in row and "Assumed" not in row, row
    assert _chips(paralegal) == ["Identity", "Living in the U.S."]
    ssn = _records(world)["ssn_card"]
    assert ssn["person"] == "applicant" and ssn["person_set_by"]["who"] == "Paulo Paralegal" and ssn["person_set_by"]["role"] == "paralegal"
    assert ssn["person_basis"] == "set_by_person"
    assert [t["role"] for t in ssn["tags"]] == ["presence"] and ssn["tags"][0]["role_of_who"] == "paralegal"

    paralegal.page.get_by_role("button", name="Remove Living in the U.S.").click()  # a tag taken back
    assert paralegal.toast() == "Saved."
    paralegal.settle()
    assert _chips(paralegal) == ["Identity"] and _records(world)["ssn_card"]["tags"] == []


def test_a_paralegal_corrects_a_documents_language(world, paralegal):
    paralegal.open("demo-ana", "documents")
    row = paralegal.page.locator("#documents tr", has_text="Birth certificate").first
    row.locator("summary", has_text="Change the language").click()
    paralegal.page.get_by_label("Language of the birth certificate").select_option(label="Spanish")
    row.get_by_role("button", name="Save the language").click()
    assert paralegal.toast() == "Saved."
    paralegal.settle()
    row = _row(paralegal, "Birth certificate")
    assert "Spanish" in row and "Set by Paulo Paralegal" in row and "read from its words" not in row, row
    birth = _records(world)["birth_certificate"]
    assert (birth["language"], birth["language_basis"]) == ("es", "set_by_person")
    paralegal.page.locator("#documents tr", has_text="Birth certificate").first.locator("summary", has_text="Change the language").click()
    paralegal.page.get_by_label("Language of the birth certificate").select_option(label="Portuguese")  # put back for the other tests
    paralegal.page.locator("#documents tr", has_text="Birth certificate").first.get_by_role("button", name="Save the language").click()
    assert paralegal.toast() == "Saved."
