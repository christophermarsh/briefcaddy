"""The small things the four buyer visits still listed (docs/research/buyer_walkthrough*.md), one test for each place the logic changed.
The screen's own part (the top bar, the client picker, the link beside its row) is walked in tests/e2e/test_polish.py.

Everyone here is made up ("Ana Clara Exemplo Souza", "Rosa Exemplo", "Beatriz Exemplo Lima")."""

# ruff: noqa: F811  (the fixtures imported from test_restricted are used as arguments)
from __future__ import annotations

import json
from pathlib import Path

import re

import pytest
from PIL import Image, ImageDraw

import restricted
from fill import load_field_map
from review import bundle
from test_bundle import demo  # noqa: F401 -- the demo client's built packet (a module-scoped fixture)
from test_restricted import app, world  # noqa: F401 -- the made-up office the restricted-case tests build (fixtures)
import schema_path

_REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def screen_demo(demo):
    """Review the retained fictional I-94 before checking its printed field."""
    import documents
    import subject_attribution as subjects
    import packet
    from review.state import Catalog, build_items, record_decision, refill
    from review.overview import review_row

    row = next(r for r in subjects.views(demo) if r["type"] == "i94")
    person = next(p for p in documents.read(demo)["case_subjects"]["people"] if p["case_role"] == "applicant")
    subjects.assign(demo, row["instance_id"], row["fingerprint"], {"holder": person["id"]},
                    "Fictional Source Reviewer", "paralegal", note="Reviewed this retained fictional I-94 for the printed-item regression.")
    field_map = load_field_map(schema_path.path("field_map", "i485"))
    template = schema_path.path("template", "i485")
    policies = json.loads(schema_path.path("law", "policy_sijs").read_text())["policies"]
    catalog = Catalog(field_map, template, policies)
    item = next(i for i in build_items(demo, field_map, template, catalog)["open"] if i["id"] == "fact:applicant.i94_number")
    record_decision(demo, item, {"action": "confirm", "reviewer": "Fictional Source Reviewer", "role": "paralegal",
                                "evidence_fingerprints": item["evidence_fingerprints"]})
    refill(demo, field_map, template)
    packet.build(demo, review_row(demo, field_map, template, catalog), "Fictional Source Reviewer", packet.load_filing("i485"))
    return demo


# -- 1. wording ---------------------------------------------------------------------------------------------------------------


def test_the_law_is_named_with_the_right_article():
    assert restricted.law_phrase("208.6") == "an asylum case (8 CFR 208.6)"
    assert restricted.law_phrase("1367") == "a VAWA, T or U visa case (8 U.S.C. 1367)"
    assert restricted.law_phrase(None) == "" and restricted.law_phrase("nothing") == ""


def test_the_case_page_and_the_refusal_say_an_asylum_case(world):
    case = world / "case-ana"
    (case / "status.json").write_text(json.dumps({"journey": {"track": {"value": "asylum"}}}), encoding="utf-8")
    state = restricted.state(case)
    assert state["law"] == "208.6" and state["law_phrase"].startswith("an asylum case")
    with pytest.raises(ValueError) as e:
        restricted.mark(case, False, "", "Sam Attorney", "attorney")
    assert "This is an asylum case" in str(e.value) and "a asylum" not in str(e.value)
    vawa = restricted.state(world / "case-rosa")
    assert vawa["law_phrase"].startswith("a VAWA, T or U visa case")


# -- 3. the client list: a person, the kind of case, the id ---------------------------------------------------------------------


def test_the_client_list_gives_each_case_its_name_and_kind(app, world):
    import journey

    journey.mark(world / "case-ana", "track", "Ana Attorney", value="family")  # the case page's "kind of case", chosen by a person
    app.overview("attorney", None)  # the dashboard builds each row once; the list reads them from the case folders
    rows = {r["id"]: r for r in app.clients(None)}
    assert rows["case-ana"]["name"] == "ANA CLARA EXEMPLO SOUZA" and rows["case-ana"]["kind"] == "Family-based"
    assert rows["case-rosa"]["kind"] and rows["case-rosa"]["restricted"] is True and rows["case-rosa"]["name"] == "ROSA EXEMPLO"


def test_a_case_the_dashboard_has_not_built_yet_still_lists_by_its_id(app, world):
    rows = {r["id"]: r for r in app.clients(None)}  # nothing built before: the list never breaks (as installed the lists build the row themselves and name it; walking every ask, no cached row means no name)
    assert rows["case-ana"]["id"] == "case-ana" and rows["case-ana"]["name"] in (None, "ANA CLARA EXEMPLO SOUZA") and rows["case-ana"]["kind"] in (None, "Family-based", "Special Immigrant Juvenile")


# -- 4. search results name their case; an empty search says what it looked at ----------------------------------------------------


def test_each_search_result_names_the_kind_of_case(app, world):
    app.overview("attorney", None)  # the dashboard has built each case's row (the overnight run does it too); the kind is read from it
    found = app.search({"q": "Passaporte"}, "attorney", None)
    assert found["results"] and all(h["case"] and "kind" in h for h in found["results"])
    assert all(h["kind"] for h in found["results"])  # every case has a kind: the track its documents show, or the one a person chose


def test_a_search_that_finds_nothing_says_how_many_documents_were_read(app, world):
    found = app.search({"q": "zzzzqqqq"}, "attorney", None)
    assert found["total"] == 0 and found["documents"] >= 6 and found["cases"] >= 3
    para = app.search({"q": "zzzzqqqq"}, "paralegal", {"role": "paralegal", "email": "jane@firm.example", "name": "Jane Doe"})
    assert para["documents"] < found["documents"]  # the restricted case's documents are not counted for her


# -- 5. expiring documents: what is watched, and for whom ------------------------------------------------------------------------


def test_every_kind_of_document_the_watch_knows_says_which_cases_it_is_watched_for():
    import expiry

    kinds = set(expiry.rules()["kinds"])
    assert kinds == set(expiry.WATCHED_FOR), kinds ^ set(expiry.WATCHED_FOR)  # a new kind in the rules file needs its words here
    watched = {w["name"]: w for w in expiry.watched()}
    assert "consular" in watched["Passport"]["for"] and "travel" in watched["Passport"]["for"]
    assert all(w["rule"] and w["source"] for w in watched.values())
    assert "—" not in json.dumps(expiry.watched()) and " -- " not in json.dumps(expiry.watched())


def test_an_empty_expiring_list_still_carries_what_is_watched(app, world):
    got = app.expiring({"days": "1"}, "attorney", None)
    assert got["watched"] and got["cases_checked"] >= 3
    assert {"id", "name", "for", "rule", "source"} <= set(got["watched"][0])


# -- 6. the client list: not invited yet is not invited -------------------------------------------------------------------------


def test_a_client_added_but_not_invited_is_in_its_own_stage(app, world):
    stages = {s["id"]: s for s in app.overview("attorney", None)["stages"]}
    assert stages["not_invited"]["name"] == "Not invited yet" and stages["not_invited"]["count"] == 1  # pilot-nova: in the portal, nothing sent
    from portal.store import PortalStore

    store = PortalStore(world.parent / "portal")
    store.update_profile("pilot-nova", invited_at="2026-10-02T09:00:00-04:00")
    app.roster.touch("pilot-nova")  # (the app's own invite route does; the test writes the portal's profile directly)
    stages = {s["id"]: s for s in app.overview("attorney", None)["stages"]}
    assert stages["not_invited"]["count"] == 0 and stages["invited"]["count"] == 1


# -- 8. the packet's check says what the packet is ---------------------------------------------------------------------------------


def test_a_draft_packet_says_one_thing(world):
    import prefile
    from datetime import date

    d = world / "case-ana"
    (d / "packet.json").write_text(json.dumps({"built_at": "2026-10-02T10:00:00+00:00", "built_by": "Jane", "forms": [], "draft": True,
                                               "problems": ["40 review cards still open."]}), encoding="utf-8")
    check = next(c for c in prefile.check(d, "i485", date(2026, 10, 2))["checks"] if c["id"] == "draft")
    assert check["title"] == "Draft until 1 problem is settled"
    assert check["text"].startswith("This packet is marked DRAFT because it was built while these were open")
    assert "Not a draft" not in check["title"] + check["text"] and "40 review cards still open." in check["text"]


# -- 9. the review bundle -----------------------------------------------------------------------------------------------------------


def _scan(lines, size=(600, 400)):
    """A made-up scan: black text lines on white (the default font), one every 40 pixels."""
    image = Image.new("L", size, 255)
    draw = ImageDraw.Draw(image)
    for y in lines:
        for x in range(20, 400, 8):
            draw.rectangle((x, y, x + 3, y + 18), fill=0)  # a line of writing, 18 pixels high (strokes, not a ruled bar)
    return image.convert("RGB")


def test_a_crop_starts_and_ends_between_lines_and_boxes_the_answer():
    image = _scan([40, 100, 160])
    box = (30, 98, 390, 120)  # the answer is the line at 100 (it stands 98 to 120)
    plain = bundle.crop(image, box)
    whole = bundle.crop_whole_lines(image, box)
    dark = lambda im, y: sum(1 for x in range(im.width) if im.convert("L").getpixel((x, y)) < 100)  # noqa: E731
    # the review card's strip pads 45 pixels: it starts inside the line at 40 and ends inside the line at 160
    assert plain.height == (120 + 45) - (98 - 45) and dark(plain, 0) > 0
    # the bundle's crop moved its edges to blank rows, so no line is cut, and drew a box round the answer
    top = whole.convert("L")
    assert dark(whole, 0) == 0 and dark(whole, whole.height - 1) == 0
    assert any(dark(whole, y) >= whole.width * 0.5 for y in range(whole.height))  # the box's top and bottom lines run across the crop
    assert whole.height >= plain.height - 2 * 40 and top.size[0] == plain.size[0]


def test_a_crop_with_no_gap_in_reach_keeps_the_strip():
    image = _scan(list(range(0, 400, 20)))  # lines a pixel apart: no blank row within one pixel of the edge
    box = (30, 200, 390, 218)
    assert bundle.crop_whole_lines(image, box, reach=1).height == bundle.crop(image, box).height


def test_the_item_a_tooltip_names_is_not_a_form_name():
    assert bundle._item_label("Part 1. 12. Enter Expiration Date of Authorized Stay Shown on Form I - 94. Enter as 2-digit Month, 2-digit Day, and 4-digit Year") \
        .startswith("12. Enter Expiration Date")
    assert bundle._label_for("26. A. Enter Form I - 94 Arrival-Departure Record Number") == "Enter Form I - 94 Arrival-Departure Record Number"


def test_only_the_one_known_stray_tooltip_number_is_overridden():
    from review.state import STRAY_REFS, ref_by_name

    assert STRAY_REFS == {"P1Line12_I94": ("Part 1, Item 26", "Part 1, Item 12")}
    assert ref_by_name("Part 1, Item 26", "form1[0].#subform[2].P1Line12_I94[0]") == "Part 1, Item 12"
    for ref, name in (("Part 1, Item 12", "form1[0].#subform[2].Pt1Line12_Date[0]"), ("Part 4, Item 7", "form1[0].#subform[5].Pt4Line7_Name[0]"),
                      ("Part 9, Item 74", "form1[0].#subform[13].Pt9Line76_YesNo[0]"),  # a box named for another line than its tooltip prints: the tooltip stands
                      ("Part 3, Item 6", "form1[0].Pt3Line5_FamilyName[0]"), ("Part 1, Item 26", "form1[0].Pt1Line26_Other[0]"), ("", "anything")):
        assert ref_by_name(ref, name) == ref, (ref, name)


def test_the_bundles_part_and_item_follow_the_forms_printed_numbers(screen_demo):
    """The verifier's regression: the box's name must never override the printed item number (Part 9 items 65 to 83, the G-28's Part 3 Item 6)."""
    from review.state import LABEL_OVERRIDES, Catalog, STRAY_REFS, i485_ref

    field_map = load_field_map(schema_path.path("field_map", "i485"))
    policies = json.loads((schema_path.path("law", "policy_sijs")).read_text(encoding="utf-8"))["policies"]
    catalog = Catalog(field_map, schema_path.path("template", "i485"), policies)
    checked = 0
    for key in (k for k in field_map if k not in LABEL_OVERRIDES):  # the firm's own overrides are deliberate
        fields = catalog._fields(key)
        if fields:
            tip = catalog.tooltips.get(fields[0], "")
            expected = i485_ref(tip)
            if any(stray in fields[0] for stray in STRAY_REFS) and expected == "Part 1, Item 26":
                expected = "Part 1, Item 12"
            assert catalog.ref(key) == expected, (key, fields[0], catalog.ref(key), expected)
            checked += 1
    assert checked > 200
    rows = bundle.rows(screen_demo, "i485")["rows"]
    for row in rows:  # the verifier's example: "74. Since April 1, 1997..." is Part 9 Item 74, not 76
        if row["form"] == "I-485" and row["label"].startswith("74. Since April 1, 1997"):
            assert row["ref"] == "Part 9, Item 74", row["ref"]
    g28 = [r for r in rows if r["form"] == "G-28" and r["label"].startswith("6. A.")]
    assert g28 and all(r["ref"] == "Part 3, Item 6" for r in g28), [(r["ref"], r["label"][:30]) for r in g28]
    i94 = next(r for r in rows if r["form"] == "I-485" and r["label"].startswith("Enter Form I - 94 Arrival-Departure Record Number"))
    assert i94["ref"] == "Part 1, Item 12"  # the stray "26. A." is closed, not moved
    part1 = [int(m.group(1)) for r in rows if r["form"] == "I-485" and (m := re.fullmatch(r"Part 1, Item (\d+)", r.get("ref") or ""))]
    assert part1 == sorted(part1), part1


def test_which_source_was_used_when_several_give_the_same_value():
    def src(kind, title, same=True, confidence=0.9):
        return {"kind": kind, "title": title, "same": same, "confidence": confidence}

    note = bundle._used_note([src("portal", "The client's answer in the portal (10/01/2026)"), src("document", "The I-360 approval notice"), src("document", "The I-94")], "resolved")
    assert note == "Used: the client's answer in the portal; the I-360 approval notice and the I-94 agree."
    assert bundle._used_note([src("document", "The passport"), src("portal", "The client's answer in the portal (10/01/2026)")], "resolved") \
        == "Used: the passport; the client's answer in the portal agrees."
    # a conflict: the surest reader's value stands, and the one that differs is named
    note = bundle._used_note([src("portal", "The client's answer in the portal", same=False, confidence=0.5), src("document", "The passport", confidence=0.95)], "conflict")
    assert note == "Used: the passport. The client's answer in the portal says something else (shown below).", note
    assert bundle._used_note([src("document", "The passport")], "resolved") == ""  # one source: nothing to say
    assert bundle._used_note([src("document", "The passport", same=False), src("portal", "The client's answer", same=False)], "resolved") == ""


# -- 12. a person on the case who is not a client ------------------------------------------------------------------------------------


def test_a_person_on_the_case_is_a_record_not_a_client(world):
    import journey

    d = world / "case-ana"
    journey.mark(d, "person", "Paulo Paralegal", value={"given_name": "Maria", "family_name": "Exemplo Souza", "relationship": "Parent", "person": "petitioner",
                                                          "phone": "(555) 010-0101", "email": "maria.exemplo@example.com"})
    j = journey.journey(d)
    (p,) = j["people"]
    assert p["name"] == "Maria Exemplo Souza" and p["person"] == "petitioner" and p["relationship"] == "Parent" and p["by"] == "Paulo Paralegal" and p["id"] == "person.1"
    assert ("petitioner", "Petitioner") in [tuple(t) for t in j["person_tags"]]
    assert not (world.parent / "portal" / "clients" / "maria-exemplo-souza").exists()  # not a portal client: no consent, nothing is ever sent
    journey.mark(d, "person", "Paulo Paralegal", value={"given_name": "Joao", "family_name": "Exemplo", "relationship": "Sibling", "person": "parent"})
    assert [x["id"] for x in journey.journey(d)["people"]] == ["person.1", "person.2"]
    journey.mark(d, "person_remove", "Paulo Paralegal", item="person.1")
    journey.mark(d, "person", "Paulo Paralegal", value={"given_name": "Ines", "relationship": "Spouse", "person": "spouse"})
    assert [x["id"] for x in journey.journey(d)["people"]] == ["person.2", "person.3"]  # a removed number is not given twice


@pytest.mark.parametrize("value, why", [
    ({"relationship": "Parent", "person": "petitioner"}, "name"),
    ({"given_name": "Maria", "relationship": "Friend", "person": "petitioner"}, "related"),
    ({"given_name": "Maria", "relationship": "Parent", "person": "applicant"}, "whose documents"),
    ({"given_name": "Maria", "relationship": "Parent", "person": "nobody"}, "whose documents"),
    ({"given_name": "Maria", "relationship": "Parent", "person": "petitioner", "email": "not an address"}, "email"),
])
def test_a_person_record_is_checked(world, value, why):
    import journey

    with pytest.raises(ValueError, match=why):
        journey.mark(world / "case-ana", "person", "Paulo Paralegal", value=value)
    with pytest.raises(ValueError, match="Enter your name"):
        journey.mark(world / "case-ana", "person", "", value={"given_name": "Maria", "relationship": "Parent", "person": "petitioner"})
    with pytest.raises(ValueError, match="No such person"):
        journey.mark(world / "case-ana", "person_remove", "Paulo Paralegal", item="person.9")


def test_the_petitioners_details_can_fill_the_petition_where_it_has_no_answer(world):
    import family
    import journey

    d = world / "case-ana"
    journey.mark(d, "person", "Paulo Paralegal", value={"given_name": "Maria", "family_name": "Exemplo Souza", "relationship": "Parent", "person": "petitioner",
                                                          "phone": "(555) 010-0101", "email": "maria.exemplo@example.com"})
    journey.mark(d, "person", "Paulo Paralegal", value={"given_name": "Joao", "relationship": "Sibling", "person": "parent"})
    filled = family.use_person(d, "person.1", "Paulo Paralegal", "attorney")
    assert any("family name" in x for x in filled) and any("email" in x for x in filled)
    answers = {q["key"]: q["value"] for q in family.status(d)["questions"]}
    assert answers["petitioner.family_name"] == "EXEMPLO SOUZA" and answers["petitioner.email"] == "MARIA.EXEMPLO@EXAMPLE.COM"
    with pytest.raises(ValueError, match="nothing was changed"):  # a second press changes nothing
        family.use_person(d, "person.1", "Paulo Paralegal", "attorney")
    with pytest.raises(ValueError, match="Only the petitioner"):
        family.use_person(d, "person.2", "Paulo Paralegal", "attorney")
    with pytest.raises(ValueError, match="No such person"):
        family.use_person(d, "person.9", "Paulo Paralegal", "attorney")
