"""Part 14 on the form's own page (src/fill/continuation.py, src/fill/where.py, src/part14_voice.py): copies of the template's own
Additional Information page for what its boxes can't hold, every entry's Page, Part and Item read from the edition's own template and never
typed, text wrapped to the box in the form's own font, the office's voice for the entries, every other form's page, and the review bundle's
account of where each entry landed. Everyone here is made up (the Exemplo family)."""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject

import clock
import offices
import part14_voice
import settings
from assemble import assemble
from factgraph import FactGraph
from fill import fill_pdf, where
from fill.continuation import (COPY_MARK, Block, _Page, attach_sheet_allowed, box_lines, find_page, finish_part14, place_blocks,
                               plain_sheet_pdf, registry, text_width, wrap)
from rules import approval
import schema_path

REPO = Path(__file__).resolve().parent.parent
SCHEMAS = schema_path.ROOT
I485 = schema_path.path("template", "i485")
EDITION = where.edition_of()

sys.path.insert(0, str(Path(__file__).resolve().parent))


def blank(tmp_path: Path, template: Path = I485, name: str = "filled.pdf") -> Path:
    out = tmp_path / name
    fill_pdf(template, {}, out)
    return out


def values(pdf: Path) -> dict[str, str]:
    """{full field name: value} of every box with a value in a PDF."""
    return {k: str(v["/V"]) for k, v in (PdfReader(str(pdf)).get_fields() or {}).items() if v.get("/V") not in (None, "")}


# -- where_is: the page, part and item of an answer, from the edition's own template -------------------------------------------------

# What the form itself prints for ten answers, read from the 09/18/26 form's own pages (a person checked each on the page): the
# numbers are the test's ground truth for this edition; the product never holds them.
TEN = {
    "applicant.part9.worked_without_authorization": ("14", "9", "12"),     # "12. Have you EVER worked in the United States without authorization?"
    "applicant.part9.in_removal_proceedings": ("14", "9", "14"),            # "14. ... removal, exclusion, rescission, or deportation proceedings"
    "applicant.part9.final_order_of_removal": ("14", "9", "15"),
    "applicant.part9.pt9line75": ("20", "9", "73"),                         # entered without inspection: this edition's item 73 on page 20
    "applicant.part9.unlawfully_present_since_1997": ("20", "9", "74"),     # unlawfully present: item 74 on page 20
    "applicant.part9.pt9line84a": ("21", "9", "82.a."),                     # a sub-item, printed "82.a."
    "applicant.prior_address_street": ("4", "1", "18"),                     # Part 1, item 18: the prior address sits on the page after the question
    "applicant.employer1_name": ("8", "4", "7"),
    "applicant.prior_spouse_family_name": ("11", "6", "11"),
    "applicant.part9.org1_name": ("13", "9", "2"),
}


@pytest.mark.parametrize("key,spot", sorted(TEN.items()))
def test_where_is_reads_the_edition_not_the_field_name(key, spot):
    assert where.where_is(EDITION, key) == spot
    page, part, item = spot
    text = " ".join(PdfReader(str(I485)).pages[int(page) - 1].extract_text().split())
    assert f"Part {part}." in text or int(page) > 1  # the page prints the part (a continued part says so at the top of the page)
    assert re.search(rf"(?<![\d.]){re.escape(item.rstrip('.'))}\.", " ".join(PdfReader(str(I485)).pages[i].extract_text() for i in (int(page) - 2, int(page) - 1)))


def test_this_editions_item_73_is_not_the_older_editions_item_75():
    """The firm's older example numbered the entered-without-inspection answer 75 (the 01/20/25 edition); this edition prints 73. The
    field is still named Pt9Line75: a number is never taken from a field name."""
    assert where.where_is(EDITION, "applicant.part9.pt9line75") == ("20", "9", "73")
    assert "Pt9Line75" in str(where._i485_map(str(SCHEMAS))["applicant.part9.pt9line75"])


def test_an_edition_the_product_does_not_hold_is_refused():
    with pytest.raises(where.EditionMismatch, match="09/18/26 edition"):
        where.where_is("01/20/25", "applicant.part9.pt9line75")


def test_an_answer_the_form_cannot_vouch_for_is_refused_not_guessed():
    with pytest.raises(where.NotFound, match="prints Part 6"):
        where.where_is(EDITION, "applicant.times_married")  # its tooltip says Part 5; the page prints Part 6 there
    with pytest.raises(where.NotFound, match="item number"):
        where.where_is(EDITION, "applicant.a_number")
    with pytest.raises(where.NotFound):
        where.where_is(EDITION, "applicant.no_such_answer")


def test_nearly_every_answer_the_map_places_is_printed_where_its_tooltip_says():
    resolved, refused = 0, []
    for key in where._i485_map(str(SCHEMAS)):
        try:
            page, part, item = where.where_is(EDITION, key)
        except where.NotFound as why:
            refused.append((key, str(why)))
            continue
        resolved += 1
        number = item.rstrip(".")
        printed = "\n".join(PdfReader(str(I485)).pages[i].extract_text() for i in (int(page) - 2, int(page) - 1) if i >= 0)
        assert re.search(rf"(?<![\d.]){re.escape(number)}\.", printed), (key, page, item)
    assert resolved >= 270, refused[:5]  # the rest are named in plain words, never guessed (the form's tooltip names no item or the wrong part)


def _tiny_form(path: Path, item_on_page: int) -> None:
    """A two-page form with one box whose tooltip says "Part 3 ... 7."; the item is printed on page 1 or page 2."""
    writer = PdfWriter()
    pages = []
    for n in (1, 2):
        p = _Page()
        p.text(54, 20, f"Form Z-1   Edition 01/01/30   Page {n} of 2", "F3", 8)
        if n == item_on_page:
            p.text(54, 740, "Part 3. Information About You" + ("" if n == 1 else " (continued)"), "F2", 12)
            p.text(54, 700, "7. Where were you born?", "F3", 10)
        else:
            p.text(54, 740, "Part 2. Something Else", "F2", 12)
            p.text(54, 700, "1. A different question", "F3", 10)
        page = p.to_page(writer)
        pages.append(writer.add_page(page))
    box = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"), NameObject("/FT"): NameObject("/Tx"),
                            NameObject("/T"): TextStringObject("Born[0]"), NameObject("/Rect"): ArrayObject([NumberObject(54), NumberObject(650), NumberObject(300), NumberObject(668)]),
                            NameObject("/TU"): TextStringObject("Part 3. Information About You. 7. Where were you born? Enter the city.")})
    pages[item_on_page - 1][NameObject("/Annots")] = ArrayObject([writer._add_object(box)])
    with open(path, "wb") as fh:
        writer.write(fh)


def test_the_numbers_follow_the_template_when_the_edition_changes(tmp_path):
    first, second = tmp_path / "z_old.pdf", tmp_path / "z_new.pdf"
    _tiny_form(first, 1)
    _tiny_form(second, 2)
    spec = {"fields": ["Born[0]"]}
    assert where.where_is_in(first, spec) == ("1", "3", "7")
    assert where.where_is_in(second, spec) == ("2", "3", "7")  # the new edition moved the item to the next page: the entry follows


def _all_categories_graph() -> FactGraph:
    g = FactGraph("t")
    add = lambda k, v: g.add_source(k, "q.pdf", "intake_questionnaire", v, v, 0.7, tier=3)
    add("applicant.family_name", "EXEMPLO SOUZA")
    add("applicant.given_name", "ANA CLARA")
    add("applicant.a_number", "A000000000")
    add("applicant.physical_address_since", "2024-06-01")
    for k in (1, 2, 3):
        for part, value in (("street", f"{k}0 SAMPLE ST"), ("city", "TESTVILLE"), ("state", "MA"), ("zip", "02100"), ("date_from", f"20{15 + k}-01-01"), ("date_to", f"20{15 + k}-12-31")):
            add(f"questionnaire.prior_address{k}_{part}", value)
    for k in (1, 2):
        for part, value in (("employer", f"SHOP {k}"), ("occupation", "CLERK"), ("city", "TESTVILLE"), ("state", "MA"), ("date_from", f"20{18 + k}-01-01"), ("date_to", "2024-12-31")):
            add(f"questionnaire.prior_employer{k}_{part}", value)
    add("questionnaire.other_a_numbers", "111111111, 222222222")
    for k in (1, 2):
        for part, value in (("name", f"EX SPOUSE{k} EXEMPLO"), ("date_married", f"20{10 + k}-05-01"), ("date_ended", f"20{12 + k}-05-01"), ("how_ended", "DIVORCED")):
            add(f"questionnaire.prior_spouse{k}_{part}", value)
    for k in (1, 2, 3):
        add(f"questionnaire.organization{k}_name", f"CLUB {k}")
        add(f"questionnaire.organization{k}_nature", "SPORTS")
    for k in (1, 2, 3, 4):
        add(f"questionnaire.child{k}_name", f"CHILD{k} EXEMPLO")
        add(f"questionnaire.child{k}_dob", f"201{k}-03-04")
        add(f"questionnaire.child{k}_country", "USA")
    assemble(g)
    return g


CATEGORIES = {"PRIOR ADDRESS": "applicant.prior_address_street", "EMPLOYMENT": "applicant.employer1_name", "OTHER A-NUMBERS": "applicant.other_a_numbers",
              "PRIOR MARRIAGE": "applicant.prior_spouse_family_name", "ORGANIZATIONS": "applicant.part9.org1_name", "OTHER CHILDREN": "applicant.total_children"}


def test_every_part14_entry_carries_the_page_its_item_sits_on_in_this_edition():
    from batch import part14_blocks

    entries = part14_blocks(_all_categories_graph())
    seen = set()
    for _n, block in entries:
        title = next(t for t in CATEGORIES if block.text.startswith(t))
        seen.add(title)
        assert (block.page, block.part, block.item) == where.where_is(EDITION, CATEGORIES[title]), title
        page = int(block.page)
        printed = "\n".join(PdfReader(str(I485)).pages[i].extract_text() for i in (page - 2, page - 1) if i >= 0)
        assert re.search(rf"(?<![\d.]){re.escape(block.item.rstrip('.'))}\.", printed), (title, block.page, block.item)  # the item is printed on that page
        assert (block.page, block.part, block.item) != ("", "", "")
    assert seen == set(CATEGORIES)


# -- fitting an entry to the box ------------------------------------------------------------------------------------------------------

def test_an_entry_wraps_to_the_boxs_own_width_in_its_own_font():
    entry = find_page(I485).entries[0]
    story = "Yes, I previously worked without employment authorization in order to support myself while my case was pending. " * 5
    lines = wrap(I485, entry.text, story)
    assert len(lines) > 3
    assert all(text_width(I485, entry.text, line) <= entry.text.width - 4 for line in lines)
    assert " ".join(lines).split() == story.split()  # every word is there, in order
    assert entry.text.font == "CourierNewPS-BoldMT" and entry.text.size == 10  # the form's own face and size, from the box itself
    assert max(len(line) for line in lines) == 85  # 515.9 points at 6 points a letter, less the box's own margin


def test_a_word_wider_than_the_box_is_broken_and_nothing_is_dropped():
    entry = find_page(I485).entries[0]
    word = "X" * 200
    lines = wrap(I485, entry.text, word)
    assert len(lines) == 3 and "".join(lines) == word


def test_an_entry_longer_than_a_box_continues_in_the_next_with_the_same_page_part_item():
    story = " ".join(f"Sentence {n} of the explanation goes here." for n in range(1, 60))
    layout = place_blocks(I485, [Block("14", "9", "12", "ITEM 12\n" + story)])
    page = find_page(I485)
    assert len(layout.placements) >= 3
    for chunk, placed in enumerate(layout.placements):
        assert (placed.page, placed.part, placed.item) == ("14", "9", "12")
        assert len(placed.lines) <= box_lines(I485, page.entries[placed.slot].text)
        assert (placed.lines[0] == "(continued)") == (chunk > 0)
    # nothing is cut: the words of every box, less the "(continued)" headings, are the entry's words
    said = [w for p in layout.placements for line in p.lines if line != "(continued)" for w in line.split()]
    assert said == ("ITEM 12 " + story).split()
    assert layout.where(0).startswith("the form's own Part 14 page, box 2") and "continues in" in layout.where(0)


def test_the_forms_own_boxes_take_the_first_entries_then_copies_in_order():
    blocks = [Block("4", "1", "18", f"ENTRY {n}") for n in range(1, 10)]
    layout = place_blocks(I485, blocks)
    assert layout.slots == 4 and layout.copies == 2
    assert [(p.copy, p.slot) for p in layout.placements] == [(0, 0), (0, 1), (0, 2), (0, 3), (1, 0), (1, 1), (1, 2), (1, 3), (2, 0)]
    assert [p.lines[0] for p in layout.placements] == [f"ENTRY {n}" for n in range(1, 10)]  # in order
    assert layout.where(8) == "copy 2 of the Part 14 page, box 2"


def test_a_number_longer_than_its_box_is_left_blank_and_listed():
    layout = place_blocks(I485, [Block("123", "9", "12", "TEXT")])
    assert layout.placements[0].page == "" and layout.problems and "page number" in layout.problems[0]


def test_text_is_filled_without_accents_like_every_other_box(tmp_path):
    out = blank(tmp_path)
    finish_part14(out, [Block("4", "1", "18", "SÃO PAULO, AÇÃO")], I485, family="SOUZA")
    assert "SAO PAULO, ACAO" in values(out)["form1[0].#subform[24].P14_Line2_AdditionalInfo[0]"]


# -- the copies ------------------------------------------------------------------------------------------------------------------------

def test_copies_are_the_forms_own_page_with_fields_of_their_own_all_filled(tmp_path):
    out = blank(tmp_path)
    blocks = [Block("4", "1", "18", f"PRIOR ADDRESS (CONTINUED)\n{n}0 EXAMPLE ST, TESTVILLE, MA 02100") for n in range(1, 10)]
    layout = finish_part14(out, blocks, I485, family="EXEMPLO SOUZA", given="ANA CLARA", middle="MARIA", a_number="A000000000")
    reader = PdfReader(str(out))
    assert layout.copies == 2 and len(reader.pages) == 24 + 2
    for number, page in ((1, reader.pages[24]), (2, reader.pages[25])):
        text = page.extract_text()
        assert "Page 24 of 24" in text and "Part 14. Additional Information" in text and "make copies of this page" in text  # the form's page, footer untouched
        names = [str(a.get_object().get("/T")) for a in page["/Annots"]]
        assert names and all(n.endswith(f"{COPY_MARK}{number}") for n in names)
    every = [str(a.get_object()["/T"]) for p in reader.pages[24:] for a in p["/Annots"]]
    assert len(every) == len(set(every))  # every box on every copy is its own field
    have = values(out)
    for number in (1, 2):
        assert have[f"Pt1Line1_FamilyName[1]{COPY_MARK}{number}"] == "EXEMPLO SOUZA"
        assert have[f"Pt1Line1_GivenName[1]{COPY_MARK}{number}"] == "ANA CLARA" and have[f"Pt1Line1_MiddleName[1]{COPY_MARK}{number}"] == "MARIA"
        assert have[f"Pt1Line4_AlienNumber[24]{COPY_MARK}{number}"] == "000000000"
    # block 5 is the first on copy 1, block 9 the first on copy 2; each with the Page, Part and Item the entry carries
    assert have[f"P14_Line2_AdditionalInfo[0]{COPY_MARK}1"].splitlines()[1] == "50 EXAMPLE ST, TESTVILLE, MA 02100"
    assert have[f"P14_Line2_AdditionalInfo[0]{COPY_MARK}2"].splitlines()[1] == "90 EXAMPLE ST, TESTVILLE, MA 02100"
    for number in (1, 2):
        assert [have[f"Pt9Line3{p}_{n}Number[0]{COPY_MARK}{number}"] for p, n in (("a", "Page"), ("b", "Part"), ("c", "Item"))] == ["4", "1", "18"]
    assert f"P14_Line3_AdditionalInfo[0]{COPY_MARK}2" not in have  # copy 2 holds one entry: its other boxes are empty
    # the first four are on the form's own page, in its own boxes
    assert have["form1[0].#subform[24].P14_Line2_AdditionalInfo[0]"].splitlines()[1] == "10 EXAMPLE ST, TESTVILLE, MA 02100"
    assert have["form1[0].#subform[24].P14_Line5_AdditionalInfo[0]"].splitlines()[1] == "40 EXAMPLE ST, TESTVILLE, MA 02100"


def test_the_forms_own_page_is_rewritten_and_the_box_lengths_are_the_forms(tmp_path):
    out = blank(tmp_path)
    # a value the fill put in the form's own box is replaced by the wrapped entry (the field map's raw text is not left to run off the box)
    fill_pdf(I485, {"form1[0].#subform[24].P14_Line2_AdditionalInfo[0]": "RAW\n" * 12}, out)
    finish_part14(out, [Block("4", "1", "18", "WRAPPED")], I485)
    have = values(out)
    assert have["form1[0].#subform[24].P14_Line2_AdditionalInfo[0]"] == "WRAPPED"
    page = find_page(I485)
    for entry in page.entries:
        assert (entry.page.max_len, entry.part.max_len, entry.item.max_len) == (2, 6, 6)


def test_the_name_on_the_forms_own_page_is_filled_when_the_fill_left_it_blank(tmp_path):
    out = blank(tmp_path)
    finish_part14(out, [Block("4", "1", "18", "ONE")], I485, family="EXEMPLO", given="ANA", a_number="A123456789")
    have = values(out)
    assert have["form1[0].#subform[24].Pt1Line1_FamilyName[1]"] == "EXEMPLO" and have["form1[0].#subform[24].Pt1Line1_GivenName[1]"] == "ANA"
    fill_pdf(I485, {"form1[0].#subform[24].Pt1Line1_FamilyName[1]": "ALREADY"}, out)  # a name the fill already put there stays
    finish_part14(out, [Block("4", "1", "18", "ONE")], I485, family="OTHER")
    assert values(out)["form1[0].#subform[24].Pt1Line1_FamilyName[1]"] == "ALREADY"


def test_the_rendered_copy_shows_the_entries_in_the_forms_boxes(tmp_path):
    """Drawn with PDFium (the engine behind a browser's PDF reader), as a person opening the PDF sees it: the boxes of the form's page
    and of its copy hold dark text where the blank page has none. (Looked at by eye once, 10/03/2026: the lines sit on the form's own
    rules, the name and A-Number are in their boxes, the footer reads Page 24 of 24.)"""
    pdfium = pytest.importorskip("pypdfium2")
    out = blank(tmp_path)
    story = "Yes, I previously worked without employment authorization in order to support myself. " * 6
    finish_part14(out, [Block("14", "9", "12", "ITEM 12\n" + story)] + [Block("4", "1", "18", f"ENTRY {n}") for n in range(2, 8)], I485,
                  family="EXEMPLO", given="ANA", a_number="A000000000")

    def render(path: Path, index: int):
        doc = pdfium.PdfDocument(str(path))
        doc.init_forms()
        return doc[index].render(scale=1).to_pil().convert("L")

    blank_page = render(I485, 23)
    for index in (23, 24):  # the form's own page and its copy
        shown = render(out, index)
        assert shown.size == blank_page.size
        # box 2 (the first entry's text area) and the name boxes: darker than on the blank page
        for rect in (find_page(I485).entries[0].text.rect, find_page(I485).family[0].rect):
            x0, y0, x1, y1 = (int(v) for v in rect)
            box = (x0, 792 - y1, x1, 792 - y0)
            dark = lambda im: sum(im.crop(box).histogram()[:100])  # noqa: E731  (pixels darker than 100 of 255)
            assert dark(shown) > dark(blank_page) + 30, (index, rect)
    shown = render(out, 24)
    shown.save(tmp_path / "copy1.png")
    assert (tmp_path / "copy1.png").stat().st_size > 5000


# -- every other form ------------------------------------------------------------------------------------------------------------------

# Every template the product fills and the page of it that is the form's own Additional Information page (1-based), read from each template
# on 10/03/2026; None where the template has no such page (the form's own line about attaching a sheet is in schemas/law/part14_pages.json).
PAGES = {
    "ar11": None, "eoir26": None, "eoir26a": None, "eoir27": None, "eoir28": None, "eoir42a": None, "eoir42b": None, "g1145": None, "g1450": None,
    "g28": 4, "g639": 10, "i130": 12, "i130a": 6, "i131": 14, "i134": 10, "i192": 9, "i212": 10, "i290b": 5, "i360": 19, "i485": 24, "i485supa": None,
    "i589": 12, "i601": 9, "i601a": 9, "i730": None, "i751": 11, "i765": 7, "i765ws": None, "i821": 13, "i821d": 7, "i864": 12, "i864a": 8, "i864ez": 8,
    "i90": 7, "i912": 8, "i914": 12, "i914a": 12, "i914b": 5, "i918": 11, "i918a": 12, "i918b": 5, "n336": 7, "n400": 13, "n565": 7, "n600": 14,
}


def test_every_template_the_product_fills_is_listed_with_its_page():
    stems = set(schema_path.names("template", SCHEMAS))
    assert stems == set(PAGES), "a new template needs its page (or its registry line) listed here"
    assert set(registry()["forms"]) == {k for k, v in PAGES.items() if v is None}  # a form with no page has a registry line, and only those do


@pytest.mark.parametrize("form", sorted(PAGES))
def test_each_forms_own_page_is_found_and_copies_of_it_fill(form, tmp_path):
    template = schema_path.path("template", form, SCHEMAS)
    page = find_page(template)
    if PAGES[form] is None:
        assert page is None
        return
    assert page.index + 1 == PAGES[form]
    assert page.entries and all(e.part is not None and e.item is not None for e in page.entries)
    assert page.family or page.full_name  # the person is named at the top of every sheet
    out = tmp_path / f"{form}.pdf"
    fill_pdf(template, {}, out)
    pages_before = len(PdfReader(str(out)).pages)
    blocks = [Block("2", "1", "3", f"ENTRY {n}") for n in range(1, len(page.entries) + 2)]  # one more than the page's own boxes
    layout = finish_part14(out, blocks, template, family="EXEMPLO", given="ANA", a_number="A000000000")
    assert layout.copies == 1
    reader = PdfReader(str(out))
    assert len(reader.pages) == pages_before + 1
    have = values(out)
    copy = {k: v for k, v in have.items() if k.endswith(f"{COPY_MARK}1")}
    assert any(v == "ENTRY " + str(len(page.entries) + 1) for v in copy.values())  # the overflow entry is on the copy
    assert any(v == "EXEMPLO" or v == "ANA EXEMPLO" for v in copy.values())  # the name is filled
    names = [str(a.get_object()["/T"]) for a in reader.pages[-1]["/Annots"]]
    assert len(names) == len(set(names)) and all(COPY_MARK in n for n in names)
    for entry in page.entries:  # the boxes' own lengths are kept
        for box in (entry.page, entry.part, entry.item):
            if box is not None and box.max_len:
                assert all(len(v) <= box.max_len for k, v in copy.items() if k.startswith(box.short))


def test_a_form_with_no_page_gets_a_plain_sheet_only_where_its_own_text_says_to_attach_one():
    for form, entry in registry()["forms"].items():
        template = PdfReader(str(schema_path.path("template", form, SCHEMAS)))
        if template.is_encrypted:
            template.decrypt("")
        if not entry["sheet"]:
            assert attach_sheet_allowed(form) is None
            with pytest.raises(ValueError, match="does not say to attach a sheet"):
                plain_sheet_pdf(form, "x", [Block("1", "1", "1", "TEXT")])
            continue
        printed = " ".join(" ".join(template.pages[entry["page"] - 1].extract_text().split()).lower().replace("’", "'").split())
        assert " ".join(entry["line"].lower().split()) in printed, (form, entry["line"])  # the form's own line, on the page it names
        sheet = PdfReader(__import__("io").BytesIO(plain_sheet_pdf(form, f"Form {form.upper()}, additional sheet", [Block("1", "1", "1", "TEXT\n" * 5)],
                                                                      family="EXEMPLO", a_number="A000000000"))).pages[0].extract_text()
        assert "A-000000000" in sheet and "TEXT" in sheet


def test_the_n400_puts_its_overflow_on_copies_of_its_own_page_not_nowhere(tmp_path):
    """The N-400 had four Part 14 boxes and dropped any entry beyond them; now the fifth and later go on copies of its own page."""
    from fill.companion import fill_companions, load_profile

    g = FactGraph("t")
    add = lambda k, v: g.add_source(k, "q.pdf", "intake_questionnaire", v, v, 0.7, tier=3)
    add("applicant.family_name", "EXEMPLO")
    add("applicant.given_name", "ANA")
    for n in range(1, 7):
        for part, value in (("page", "3"), ("part", "4"), ("item", "1"), ("text", f"ADDRESS (CONTINUED): {n}0 SAMPLE ST")):
            add(f"n400.p14_block{n}_{part}", value)
    profile = load_profile()
    profile["forms"] = {"n400": profile["forms"]["n400"]}
    result = fill_companions(g, tmp_path, profile)
    assert result["n400"]["additional_page_copies"] == 1
    have = values(tmp_path / "n400_filled.pdf")
    copy = [v for k, v in have.items() if k.endswith(f"{COPY_MARK}1")]
    assert "ADDRESS (CONTINUED): 50 SAMPLE ST" in copy and "ADDRESS (CONTINUED): 60 SAMPLE ST" in copy and "EXEMPLO" in copy
    assert len(PdfReader(str(tmp_path / "n400_filled.pdf")).pages) == 14 + 1


# -- the voice -------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def firm(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    case = tmp_path / "case-ana"
    case.mkdir()
    return case


def test_the_voice_is_not_set_until_the_office_sets_it_and_the_attorney_approves_it(firm):
    unset = part14_voice.voice(firm)
    assert unset["voice"] == "client" and unset["chosen"] is None and not unset["approved"]  # the client's voice, marked not set
    assert "not set" in unset["note"] and "client's voice" in unset["note"]
    field = next(f for s in settings.specs() if s["id"] == "firm" for f in s["fields"] if f["key"] == part14_voice.KEY)
    assert field["label"] == "Part 14 entries are written" and [o[0] for o in field["options"]] == ["", "client", "office"]
    settings.save("firm", {part14_voice.KEY: "office"}, "Sam Attorney")
    chosen = part14_voice.voice(firm)
    assert chosen["chosen"] == "office" and chosen["voice"] == "client" and "has not approved" in chosen["note"]  # chosen, not approved: still the client's
    approval.approve(part14_voice.PRACTICE_ID, "Sam Attorney", "attorney")
    approved = part14_voice.voice(firm)
    assert approved["voice"] == "office" and approved["approved"] and "office's voice" in approved["note"]
    settings.save("firm", {part14_voice.KEY: "client"}, "Sam Attorney")  # a change asks for the approval again, like a practice
    assert part14_voice.practice()["state"] == "changed"
    assert part14_voice.voice(firm)["voice"] == "client" and not part14_voice.voice(firm)["approved"]


def test_one_voice_per_packet_the_case_offices(firm):
    settings.add_office("Sam Attorney")
    other = offices.offices()[1]["section"]
    settings.save("firm", {part14_voice.KEY: "client"}, "Sam Attorney")
    settings.save(other, {part14_voice.KEY: "office"}, "Sam Attorney")
    assert part14_voice.choices() == {"main": "client", other: "office"}
    approval.approve(part14_voice.PRACTICE_ID, "Sam Attorney", "attorney")
    assert part14_voice.voice(firm)["voice"] == "client"  # the main office files this case
    (firm / "office.json").write_text('{"office": "%s", "by": "Sam Attorney"}' % other, encoding="utf-8")  # chosen on the case
    assert part14_voice.voice(firm)["voice"] == "office"


def test_the_attorney_reads_the_practice_and_the_cards_sentences_word_for_word():
    review = " ".join((REPO / "docs" / "attorney_review.md").read_text(encoding="utf-8").split())
    assert " ".join(part14_voice.PRACTICE.split()) in review
    for sentence in ("The voice for Part 14 entries is not set for", "has not approved it yet (Keeping current)", "(the office's policy, approved)"):
        assert sentence in review


def test_the_overflow_entries_are_lists_and_have_no_voice():
    from batch import part14_blocks

    for _n, block in part14_blocks(_all_categories_graph()):
        assert not re.search(r"\b(I|my|me|the applicant|The applicant)\b", block.text)  # a list line, not a sentence in anyone's voice


# -- the review bundle ---------------------------------------------------------------------------------------------------------------

def test_the_bundle_says_where_each_entry_landed_and_the_line_it_came_from():
    from batch import part14_blocks
    from review import bundle

    g = _all_categories_graph()
    entries = part14_blocks(g)
    assert len(entries) > 4  # more than the form's own boxes
    rows = [{"form": "I-485", "key": f"applicant.p14_block{n}_text", "ref": "Part 14", "label": "x", "value": "y", "boxes": 1, "sources": []}
            for n, _ in entries[:4]]
    bundle._part14_landings(rows, g, I485, "I-485", lambda key: [{"kind": "filing", "title": "t", "text": "t"}])
    assert len(rows) == len(entries)  # an entry past the form's own boxes gets a row of its own
    first, last = rows[0], rows[-1]
    assert first["landed"] == "the form's own Part 14 page, box 2"
    assert re.fullmatch(r"copy \d of the Part 14 page, box \d", last["landed"])
    assert first["value"].startswith(("PRIOR ADDRESS", "EMPLOYMENT")) and first["line"].startswith("the client's")
    assert all(not re.search(r"[a-z]+_[a-z]+|questionnaire\.", r["line"]) for r in rows)  # words, never a key
    assert bundle._p14_line("composed from questionnaire.prior_address3") == "the client's address history, line 3"
    assert bundle._p14_line("composed from questionnaire.other_a_numbers") == "the client's other A-Numbers"


def test_copy_fields_are_not_listed_as_untraced_boxes(tmp_path):
    from review import bundle

    out = blank(tmp_path)
    finish_part14(out, [Block("4", "1", "18", f"E{n}") for n in range(1, 7)], I485, family="EXEMPLO")
    names = [name for name, *_ in bundle._filled(out)]
    assert names and not any(COPY_MARK in n for n in names)


# -- the ground truth: what the printed 09/18/26 pages say, pinned for every answer a producer or the explanations use -----------------------
# Each (page, item) below was read off the printed page beside the box (the label a person reading the form would name), by an independent
# positional reading and by hand, and is checked again here against the page text; none comes from a tooltip or a field name. A tooltip that
# names another item must be refused, not believed: the parent boxes' say "Parent 2. 7. Enter Date of Birth" and the visa-abroad boxes' say
# "complete Item Numbers 2. through 4. below".

PART9 = {
    "violated_nonimmigrant_status": ("14", "13"),
    "unlawfully_present_since_1997": ("20", "74"),
    "denied_admission": ("14", "10"),
    "denied_visa": ("14", "11"),
    "worked_without_authorization": ("14", "12"),
    "in_removal_proceedings": ("14", "14"),
    "final_order_of_removal": ("14", "15"),
    "prior_order_reinstated": ("14", "16"),
    "voluntary_departure_not_departed": ("14", "17"),
    "applied_relief_from_removal": ("14", "18"),
    "j_exchange_two_year_requirement": ("14", "19"),
    "arrested_cited_charged_detained": ("14", "22"),
    "committed_crime": ("15", "23"),
    "pt8line1": ("13", "1"),
    "pt8line25": ("15", "25"),
    "pt8line26": ("15", "26"),
    "pt8line27": ("15", "27"),
    "pt8line28": ("15", "28"),
    "pt8line30": ("15", "30"),
    "pt8line31": ("15", "31"),
    "pt8line32": ("15", "32"),
    "pt8line33": ("15", "33"),
    "pt8line34": ("15", "34"),
    "pt8line35a": ("15", "35.a."),
    "pt8line36": ("15", "36"),
    "pt8line37": ("15", "37"),
    "pt8line38": ("16", "38"),
    "pt8line41": ("16", "41"),
    "pt8line42c": ("16", "42.c."),
    "pt8line42d": ("16", "42.d."),
    "pt8line43a": ("16", "43.a."),
    "pt8line43b": ("16", "43.b."),
    "pt8line43c": ("16", "43.c."),
    "pt8line43d": ("16", "43.d."),
    "pt8line43e": ("16", "43.e."),
    "pt8line43f": ("16", "43.f."),
    "pt8line43g": ("16", "43.g."),
    "pt8line43h": ("16", "43.h."),
    "pt8line43i": ("16", "43.i."),
    "pt8line44": ("16", "44"),
    "pt8line45": ("16", "45"),
    "pt8line47": ("17", "47"),
    "pt8line48": ("17", "48"),
    "pt8line50": ("17", "50"),
    "pt8line52": ("17", "52"),
    "pt8line53d": ("17", "53.d."),
    "pt8line54": ("17", "54"),
    "pt9line24": ("15", "24"),
    "pt9line39": ("16", "39"),
    "pt9line67": ("20", "65"),
    "pt9line68": ("20", "66"),
    "pt9line69": ("20", "67"),
    "pt9line70": ("20", "68"),
    "pt9line71": ("20", "69"),
    "pt9line72": ("20", "70"),
    "pt9line73": ("20", "71"),
    "pt9line74": ("20", "72"),
    "pt9line75": ("20", "73"),
    "pt9line78a": ("21", "76.a."),
    "pt9line78b": ("21", "76.b."),
    "pt9line79": ("21", "77"),
    "pt9line80": ("21", "78"),
    "pt9line81": ("21", "79"),
    "pt9line82": ("21", "80"),
    "pt9line83": ("21", "81"),
    "pt9line84a": ("21", "82.a."),
    "pt9line84b": ("21", "82.b."),
    "pt9line84c": ("21", "82.c."),
    "pt9line85": ("21", "83"),
    "a": ("16", "42.a."),
    "b": ("16", "42.b."),
    "pt8line24b": ("14", "20"),
    "pt8line24c": ("14", "21"),
    "pt8line29": ("15", "29"),
    "pt8line35b": ("15", "35.b."),
    "pt8line40": ("16", "40"),
    "pt8line46": ("17", "46"),
    "pt8line49": ("17", "49"),
    "pt8line51": ("17", "51"),
    "pt8line53a": ("17", "53.a."),
    "pt8line53b": ("17", "53.b."),
    "pt8line53c": ("17", "53.c."),
    "pt8line55": ("17", "55"),
    "pt9line77": ("21", "75"),
    "org1_name": ("13", "2"),
    "org1_city": ("13", "3"),
    "org1_state": ("13", "3"),
    "org1_country": ("13", "3"),
    "org1_nature": ("13", "4"),
    "org1_date_from": ("13", "5"),
    "org1_date_to": ("13", "5"),
    "org2_name": ("13", "6"),
    "org2_city": ("14", "7"),
    "org2_state": ("14", "7"),
    "org2_nature": ("14", "8"),
    "org2_involvement": ("14", "8"),
    "org2_date_from": ("14", "9"),
    "org2_date_to": ("14", "9"),
}

OTHERS = {  # key: (page, part, item)
    # the six overflow lists
    "applicant.prior_address_street": ("4", "1", "18"), "applicant.employer1_name": ("8", "4", "7"), "applicant.other_a_numbers": ("2", "1", "5"),
    "applicant.prior_spouse_family_name": ("11", "6", "11"), "applicant.part9.org1_name": ("13", "9", "2"), "applicant.total_children": ("12", "7", "1"),
    # the parents: the tooltips name "Parent 2." (the second parent), the page prints the item beside the box
    "applicant.mother_birth_family_name": ("9", "5", "2"), "applicant.mother_birth_given_name": ("9", "5", "2"), "applicant.mother_birth_middle_name": ("9", "5", "2"),
    "applicant.mother_dob": ("9", "5", "3"), "applicant.mother_country_of_birth": ("10", "5", "4"),
    "applicant.father_family_name": ("10", "5", "5"), "applicant.father_given_name": ("10", "5", "5"),
    "applicant.father_birth_family_name": ("10", "5", "6"), "applicant.father_birth_given_name": ("10", "5", "6"), "applicant.father_birth_middle_name": ("10", "5", "6"),
    "applicant.father_dob": ("10", "5", "7"), "applicant.father_country_of_birth": ("10", "5", "8"),
    # the immigrant visa applied for abroad ("complete Item Numbers 2. through 4. below")
    "applicant.part4.visa_abroad_city": ("8", "4", "2"), "applicant.part4.visa_abroad_decision": ("8", "4", "3"),
}

# Answers the template's own tooltip cannot vouch for: refused, so a person fills the box (K2's arrival answers among them: item 11, whose tooltip is garbled).
REFUSED = ["applicant.last_arrival_manner", "applicant.last_arrival_admitted_as", "applicant.last_arrival_paroled_as", "applicant.last_arrival_other",
           "applicant.part4.visa_abroad_country", "applicant.i94_number", "applicant.times_married", "applicant.a_number"]

SUPPLEMENT_B = {"b1a": ("5", "B", "1.A"), "b1b": ("5", "B", "1.B"), "b2": ("6", "B", "2"), "b3a": ("6", "B", "3.A"), "b3b": ("6", "B", "3.B"),
                "b4": ("6", "B", "4"), "c1": ("7", "C", "1"), "c2b": ("7", "C", "2.A-2.B"), "c3": ("7", "C", "3"), "c4": ("8", "C", "4"),
                "c5": ("8", "C", "5"), "c6": ("8", "C", "6")}


def _printed_before(page: str, item: str) -> bool:
    printed = "\n".join(PdfReader(str(I485)).pages[i].extract_text() for i in (int(page) - 2, int(page) - 1) if i >= 0)
    return bool(re.search(rf"(?<![\d.]){re.escape(item.rstrip('.'))}\.", printed))


def test_ground_truth_every_answer_a_producer_or_the_explanations_use():
    table = {f"applicant.part9.{k}": (page, "9", item) for k, (page, item) in PART9.items()} | OTHERS
    assert len(table) > 110
    wrong = {key: (where.where_is(EDITION, key), spot) for key, spot in table.items() if where.where_is(EDITION, key) != spot}
    assert not wrong, wrong
    for key, (page, _part, item) in table.items():
        assert _printed_before(page, item), (key, page, item)  # the item's label is on that page or the one before (read from the page text)


def test_a_tooltip_that_names_another_item_is_refused_not_believed():
    for key in REFUSED:
        with pytest.raises(where.NotFound):
            where.where_is(EDITION, key)
    with pytest.raises(where.NotFound, match="prints item 11"):
        where.where_is(EDITION, "applicant.last_arrival_manner")


def test_supplement_b_questions_are_the_i589_pages_as_printed():
    import asylum

    assert {k: asylum.spot(k) for k in SUPPLEMENT_B} == SUPPLEMENT_B


def test_only_the_i485_is_read_for_now():
    with pytest.raises(where.NotFound, match="I-485's own template only"):
        where.where_is("01/20/25", "n400.child1_name", "n400")  # the N-400's tooltips name item 1 where the page prints 2


# -- a spot the form cannot vouch for is flagged, never a silent blank ---------------------------------------------------------------

def _refusing(monkeypatch):
    def refuse(edition, key, *a, **k):
        raise where.NotFound("The form's tooltip does not agree with its page.")

    monkeypatch.setattr(where, "where_is", refuse)


def test_a_refused_spot_is_one_flag_per_entry_the_card_and_the_bundle_say_so(monkeypatch, tmp_path):
    from batch import _part14_continuation, part14_blocks, part14_spot_flags
    from review import bundle

    _refusing(monkeypatch)
    g = _all_categories_graph()
    entries = part14_blocks(g)
    assert entries and all((b.page, b.part, b.item) == ("", "", "") for _n, b in entries)  # blank, as before
    flags = part14_spot_flags(g)
    assert len(flags) == len(entries)  # one flag per entry
    assert flags[0].message == "Part 14 entry 1 has no page, part or item the edition can vouch for: enter them by hand on the form's Part 14 page."
    assert flags[0].fact_key == "applicant.p14_block1_text" and flags[0].level == "review" and flags[0].kind == "alert"
    out = blank(tmp_path)
    shown = _part14_continuation(g, {}, out, I485)
    assert [f.message for f in shown if "no page, part or item" in f.message] == [f.message for f in flags]  # the fill's report says it too
    rows = []
    bundle._part14_landings(rows, g, I485, "I-485", lambda key: [])
    assert all("blank (the form could not vouch for it): enter it by hand" in r["landed"] for r in rows)
    assert "enter them by hand" in (REPO / "src" / "review" / "static" / "index.html").read_text(encoding="utf-8")  # the card's own line


def test_the_open_flag_is_a_review_card_so_the_packet_stays_a_draft(monkeypatch, tmp_path):
    from fill import load_field_map
    from review import state

    _refusing(monkeypatch)
    case = tmp_path / "case"
    case.mkdir()
    (case / "meta.json").write_text("{}", encoding="utf-8")
    _all_categories_graph().save(case / "fact_graph.json")
    _graph, flags = state.current_flags(case, load_field_map(schema_path.path("field_map", "i485")), I485)
    mine = [f for f in flags if "no page, part or item" in f.message]
    assert mine and all(f.level == "review" for f in mine)  # an open review item: the card queue, and so "review cards still open"
    assert state.item_id(mine[0]) == "alert:applicant.p14_block1_text"
    # the card's own title says what it is (not "Questionnaire missing"), and its group stays the attorney's
    field_map = load_field_map(schema_path.path("field_map", "i485"))
    built = state.build_items(case, field_map, I485, state.Catalog(field_map, I485), pending=False)
    item = next(i for i in built["open"] if i["id"] == "alert:applicant.p14_block1_text")
    assert item["title"] == "Part 14 entry needs its page, part and item" and item["kind"] == "alert" and item["group"] == "attorney"


def test_nothing_is_flagged_when_the_edition_vouches_for_every_spot():
    from batch import part14_spot_flags

    assert part14_spot_flags(_all_categories_graph()) == []


# -- small things the verifier found ------------------------------------------------------------------------------------------------

def test_a_boxs_number_is_the_one_the_page_prints_not_the_tooltips():
    assert [e.label for e in find_page(I485).entries] == ["2", "3", "4", "5"]
    assert [e.label for e in find_page(schema_path.path("template", "n400")).entries] == ["2", "3", "4", "5"]  # the N-400's tooltips say 3, 4, 5, 6
    g28 = schema_path.path("template", "g28")  # prints "2.a. 2.b. 2.c." beside an entry: no plain number, so the box is named by its place on the page
    assert [e.label for e in find_page(g28).entries] == [""] * 5 and place_blocks(g28, [Block("1", "1", "1", "ONE")]).where(0).endswith("box 1")
    layout = place_blocks(schema_path.path("template", "n400"), [Block("3", "4", "1", "ONE")])
    assert layout.where(0) == "the form's own Part 14 page, box 2"


def test_the_i918_page_has_its_five_entries_even_where_a_tooltip_says_enter_the_page_number():
    assert len(find_page(schema_path.path("template", "i918")).entries) == 5


def test_a_copy_keeps_the_centred_boxes_and_the_forms_barcode_field(tmp_path):
    out = blank(tmp_path)
    finish_part14(out, [Block("4", "1", "18", f"E{n}") for n in range(1, 6)], I485, family="EXEMPLO")
    reader = PdfReader(str(out))
    fields = reader.get_fields()

    def widget(page, short):  # the box's own dictionary: its alignment is not in the field summary
        return next(a.get_object() for a in page["/Annots"] if str(a.get_object()["/T"]).startswith(short))

    own, copy = widget(reader.pages[23], "Pt9Line3a_PageNumber[0]"), widget(reader.pages[24], f"Pt9Line3a_PageNumber[0]{COPY_MARK}1")
    assert copy.get("/Q") == own.get("/Q") == 1  # centred, like the form's own page
    assert f"PDF417BarCode2[0]{COPY_MARK}1" in fields  # the page's own barcode field, as a photocopy would carry it


def test_a_template_replaced_in_place_is_read_again(tmp_path):
    import shutil
    import os

    path = tmp_path / "form.pdf"
    shutil.copy(schema_path.path("template", "g28"), path)
    assert find_page(path).index == 3 and len(find_page(path).entries) == 5
    shutil.copy(I485, path)
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 5_000_000_000))
    assert find_page(path).index == 23 and len(find_page(path).entries) == 4
    assert where._index(str(path))["edition"] == "09/18/26"  # the template index follows the file too


# -- the N-400's Part 14 numbers: typed in src/naturalization.py, correct for the edition held, pinned so a new edition fails loudly -------------

N400_TYPED = {  # entry: (page, part, item), as naturalization.py types them (01/20/25 edition)
    "ADDRESS (CONTINUED)": ("3", "4", "1"), "EMPLOYMENT (CONTINUED)": ("5", "7", "1"), "TRIP (CONTINUED)": ("6", "8", "1"),
    "CHILD (CONTINUED)": ("5", "6", "2"), "CRIME OR OFFENSE (CONTINUED)": ("8", "9", "15"),
}


def test_the_n400s_typed_part14_numbers_are_the_printed_ones_for_the_edition_held():
    template = schema_path.path("template", "n400")
    assert where.edition_of("n400") == "01/20/25", "a new N-400 edition: read each Part 14 page, part and item off its pages again, then update this pin"
    source = (REPO / "src" / "naturalization.py").read_text(encoding="utf-8")
    pages = PdfReader(str(template))
    if pages.is_encrypted:
        pages.decrypt("")
    index = where._index(str(template))
    for entry, (page, part, item) in N400_TYPED.items():
        assert f'("{page}", "{part}", "{item}", ' in source and entry in source, entry  # the code still types exactly these
        text = pages.pages[int(page) - 1].extract_text()
        assert any(p == part for _y, p in index["headings"][int(page)]), (entry, "the page prints no Part " + part + " heading")
        assert re.search(rf"(?<![\d.]){item}\.", text), (entry, "the page prints no item " + item)


# -- the names timeline's Part 14 block goes through the same spot, layout and bundle ---------------------------------------------------

def _third_other_name_graph():
    import test_name_events as names
    from extract.name_change_order import extract as extract_order

    g = names._graph(extra=(("decreto.pdf", "name_change_order", extract_order(names.ORDER)),),)
    for key, value in (("questionnaire.other_name1_given", "ANINHA"), ("questionnaire.other_name1_family", "SOUZA")):
        g.add_source(key, "portal questionnaire", "intake_questionnaire", value, value, 0.95, tier=3)
    assemble(g)
    return g


def test_the_other_names_block_takes_its_spot_from_the_edition_and_flows_through_the_layout_and_the_bundle():
    from batch import part14_blocks
    from review import bundle

    g = _third_other_name_graph()
    entries = [(n, b) for n, b in part14_blocks(g) if "OTHER NAMES USED" in b.text]
    assert len(entries) == 1
    n, block = entries[0]
    assert (block.page, block.part, block.item) == where.where_is(EDITION, "applicant.na.other_names")  # computed, not typed
    assert "name timeline" in block.source
    assert place_blocks(I485, [b for _n, b in part14_blocks(g)]).placements  # laid out with every other entry
    rows = []
    bundle._part14_landings(rows, g, I485, "I-485", lambda key: [])
    mine = next(r for r in rows if r["key"] == f"applicant.p14_block{n}_text")
    assert mine["landed"].startswith("the form's own Part 14 page, box") and "name timeline" in mine["line"]
    html = (REPO / "src" / "review" / "static" / "index.html").read_text(encoding="utf-8")
    assert "composed from the client's name timeline" in html and "other names used" in html  # the card's "what this continues" label


def test_a_refused_spot_for_the_other_names_block_is_blank_and_flagged(monkeypatch):
    from batch import part14_blocks, part14_spot_flags

    _refusing(monkeypatch)
    g = _third_other_name_graph()
    assert any("OTHER NAMES USED" in b.text and (b.page, b.part, b.item) == ("", "", "") for _n, b in part14_blocks(g))
    assert any("no page, part or item" in f.message for f in part14_spot_flags(g))
