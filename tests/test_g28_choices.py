"""The G-28's firm choices (src/g28.py): the offices' policies and the attorney's approval, the card on every case, the confirmation the packet
needs, and every box of the filled G-28 for each combination of choices. Everyone here is made up (the Exemplo office at a made-up address)."""

import json
from pathlib import Path

import pytest
from pypdf import PdfReader

import events
import g28
import packet
import settings
from factgraph import FactGraph
from fill.companion import fill_companions, load_profile
from review.state import reviewed_graph
from rules import approval
import schema_path

OFFICE = {"firm.business_name": "EXEMPLO LAW LLP", "firm.preparer_given_name": "ANA", "firm.preparer_family_name": "EXEMPLO", "firm.attorney_bar_number": "123456",
          "firm.street": "100 EXAMPLE WAY", "firm.city": "SPRINGFIELD", "firm.state": "MA", "firm.zip": "01101", "firm.phone": "5555550100",
          "office.name": "Springfield, MA", "office.states": "MA"}
HOME = {"applicant.physical_street": "12 SAMPLE STREET", "applicant.physical_city": "WORCESTER", "applicant.physical_state": "MA", "applicant.physical_zip": "01602",
        "applicant.family_name": "SOUZA", "applicant.given_name": "ANA", "applicant.middle_name": "CLARA"}
OWN_MAIL = {"applicant.mailing_street": "PO BOX 77", "applicant.mailing_city": "WORCESTER", "applicant.mailing_state": "MA", "applicant.mailing_zip": "01603",
            "applicant.mailing_same_as_physical": "No"}


@pytest.fixture(autouse=True)
def firm(tmp_path, monkeypatch):
    """A made-up firm configured on this test's own Settings file, with its own rule approvals and its own ledger."""
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "approved.json"))
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    settings.save("firm", OFFICE, "Test Attorney")
    return tmp_path


def office_policy(**values):
    settings.save("firm", {f"office.g28_{k}": v for k, v in values.items()}, "Test Attorney")


def approve():
    approval.approve(g28.PRACTICE_ID, "Test Attorney", "attorney")


def make_case(tmp_path, facts=None, **extra):
    d = tmp_path / "case-ana"
    d.mkdir(exist_ok=True)
    graph = FactGraph("case-ana")
    for key, value in ({**HOME, **(facts or {})}).items():
        graph.add_source(key, "intake.pdf", "intake_questionnaire", value, value, 0.95, tier=3)
    graph.save(d / "fact_graph.json")
    (d / "meta.json").write_text(json.dumps({"client_id": "case-ana"}), encoding="utf-8")
    return d


def card(d, forms=("g28", "i485", "i765")):
    return g28.card(d, list(forms), reviewed_graph(d, g28_card=False))


# -- the policies on Settings ----------------------------------------------------------------------------------------------

def test_every_office_has_the_four_choices_with_the_forms_own_words_beside_them():
    fields = {f["key"]: f for s in settings.specs() if s["id"] == "firm" for f in s["fields"]}
    mail = fields["office.g28_mail"]
    assert mail["value"] == "off" and mail["group"] == settings.G28_GROUP
    assert g28.mail_note() in mail["note"]  # the form's note, word for word
    assert "The attorney decides" in mail["note"]
    assert "or adding an office, means an attorney approves the G-28 choices again, for every office" in mail["note"]
    for item in g28.ITEMS:
        f = fields[f"office.g28_{item}"]
        assert f["value"] == "off"
        assert g28.register()["part4"][item]["words"] in f["note"]
    settings.add_office("Test Attorney")
    other = next(s for s in settings.specs() if s.get("office"))
    assert {f["key"] for f in other["fields"]} >= {"office.g28_mail", "office.g28_1a", "office.g28_1b", "office.g28_1c", "firm.unit_type", "firm.apt"}


def test_the_forms_words_are_the_forms_own():
    text = "\n".join(page.extract_text() for page in PdfReader(str(schema_path.path("template", "g28"))).pages)
    flat = " ".join(text.split())
    for item in g28.ITEMS:
        assert g28.register()["part4"][item]["words"] in flat
    assert g28.mail_note() in flat
    assert "NOTE: If your notice contains Form I-94" in flat and g28.register()["part4"]["1b"]["note"] in flat


def test_the_register_re_dumps_byte_for_byte():
    raw = Path(g28.REGISTER).read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert json.dumps(json.loads(raw), indent=2, ensure_ascii=False) + "\n" == raw


def test_a_choice_must_be_one_of_the_options():
    with pytest.raises(ValueError):
        settings.save("firm", {"office.g28_mail": "maybe"}, "Test Attorney")


def test_the_attorney_approves_the_practice_and_a_change_to_a_choice_undoes_the_approval():
    assert g28.practice()["state"] == "not_approved" and not g28.approved()
    office_policy(mail="on", **{"1a": "on"})
    approve()
    assert g28.approved()
    done = g28.practice()
    assert done["state"] == "approved" and "Test Attorney" in done["text"]
    assert "Springfield, MA: the client's mailing address on the G-28 is this office's address" in done["plain_text"]
    office_policy(**{"1b": "on"})  # the words the attorney read no longer match
    assert g28.practice()["state"] == "changed" and not g28.approved()
    approve()
    assert g28.approved()


def test_a_changed_instruction_paragraph_unapproves_the_practice_as_the_register_step_says(tmp_path, monkeypatch):
    approve()
    assert g28.approved()
    changed = json.loads(Path(g28.REGISTER).read_text(encoding="utf-8"))
    changed["instructions"]["i485"]["line"] += " (a later edition adds a sentence)"
    copy = tmp_path / "g28_choices.json"
    copy.write_text(json.dumps(changed), encoding="utf-8")
    monkeypatch.setattr(g28, "REGISTER", copy)
    assert g28.practice()["state"] == "changed" and not g28.approved()
    item = next(i for i in json.loads((schema_path.path("register", "maintenance")).read_text(encoding="utf-8"))["items"] if i["id"] == "g28_safe_address_lines")
    assert "shows as changed until it is approved again" in " ".join(item["steps"])  # the register's step and the code say the same


def test_unapproved_the_client_own_address_is_filled_and_part_4_is_blank(tmp_path):
    office_policy(mail="on", **{"1a": "on", "1b": "on", "1c": "on"})
    d = make_case(tmp_path)
    c = card(d)
    assert c["policy"]["approved"] is False and c["policy"]["says"] == g28.WAITING and c["policy"]["waiting"]
    assert c["mail"]["chosen"] == "physical" and [p["marked"] for p in c["part4"]] == [False, False, False]
    assert [p["office_setting"] for p in c["part4"]] == [False, False, False]
    approve()
    c = card(d)
    assert c["policy"]["approved"] and c["policy"]["says"] == ""
    assert c["mail"]["chosen"] == "office" and [p["marked"] for p in c["part4"]] == [True, True, True]


# -- the card ----------------------------------------------------------------------------------------------------------

def test_the_card_shows_the_address_the_part_4_choices_the_attorney_and_the_client(tmp_path):
    office_policy(mail="on", **{"1a": "on", "1c": "on"})
    approve()
    c = card(make_case(tmp_path))
    assert c["title"] == "The G-28 for this case" and c["forms"] == ["g28"]
    assert c["attorney"] == {"name": "ANA EXEMPLO", "bar": "123456", "office": "Springfield, MA", "address": ["100 EXAMPLE WAY", "SPRINGFIELD, MA 01101"]}
    assert c["client"]["name"] == "ANA CLARA SOUZA"
    assert c["mail"]["note"] == g28.mail_note()
    by_id = {o["id"]: o for o in c["mail"]["options"]}
    assert by_id["office"]["lines"] == ["100 EXAMPLE WAY", "SPRINGFIELD, MA 01101"]
    assert by_id["physical"]["lines"] == ["12 SAMPLE STREET", "WORCESTER, MA 01602"]
    assert not by_id["mailing"]["available"] and by_id["mailing"]["why_not"]
    assert [(p["id"], p["marked"], p["words"]) for p in c["part4"]] == [(i, i != "1b", g28.register()["part4"][i]["words"]) for i in g28.ITEMS]
    assert c["state"] == "not_confirmed" and c["decides"].startswith("The attorney decides")


def test_when_the_client_has_two_addresses_the_card_offers_both(tmp_path):
    approve()
    c = card(make_case(tmp_path, OWN_MAIL))
    by_id = {o["id"]: o for o in c["mail"]["options"]}
    assert by_id["physical"]["available"] and by_id["mailing"]["available"] and by_id["mailing"]["lines"][0] == "PO BOX 77"
    d = tmp_path / "case-ana"
    g28.change(d, "mail", "mailing", "The client asked for her own mailing address.", "Jane Paralegal", "paralegal", reviewed_graph(d, g28_card=False))
    assert card(d)["mail"]["chosen"] == "mailing"


def test_the_card_is_none_for_a_packet_with_no_g28_it_governs(tmp_path):
    d = make_case(tmp_path)
    assert g28.card(d, ["i485", "i765"], reviewed_graph(d, g28_card=False)) is None
    assert g28.card(d, ["g28_i130"], reviewed_graph(d, g28_card=False)) is None  # the petitioner's G-28 is the petitioner's own
    assert g28.problems(d, ["i485"]) == []


def test_the_firms_own_address_in_care_of_is_not_the_clients_mailing_address(tmp_path):
    d = make_case(tmp_path)
    graph = reviewed_graph(d, g28_card=False)
    for key, value in {"applicant.mailing_street": "100 EXAMPLE WAY", "applicant.mailing_in_care_of": "EXEMPLO LAW LLP"}.items():
        graph.add_source(key, "firm_profile.json", "firm_profile", value, value, 1.0)
    assert g28.client_addresses(graph)["mailing"] is None


# -- confirm, change, undo ---------------------------------------------------------------------------------------------

def ledger(tmp_path):
    return [r for r in events.rows(events.base_path(tmp_path)) if r["kind"] == "decisions"]


def test_a_person_confirms_the_card_under_their_name_and_it_is_in_the_ledger(tmp_path):
    approve()
    d = make_case(tmp_path)
    with pytest.raises(ValueError, match="Enter your name"):
        g28.confirm(d, "", "paralegal")
    assert card(d)["state"] == "not_confirmed"
    g28.confirm(d, "Jane Paralegal", "paralegal", reviewed_graph(d, g28_card=False))
    c = card(d)
    assert c["state"] == "confirmed" and c["confirmed"]["by"] == "Jane Paralegal" and c["confirmed"]["role"] == "paralegal"
    assert [(r["who"], r["role"], r["case"], r["action"]) for r in ledger(tmp_path)] == [("Jane Paralegal", "paralegal", "case-ana", "confirmed")]
    assert "Jane" not in ledger(tmp_path)[0]["what"] and ledger(tmp_path)[0]["what"] == "Confirmed the G-28's choices for the case"


def test_a_change_needs_a_reason_and_a_name_and_unconfirms_the_card(tmp_path):
    office_policy(**{"1a": "on"})
    approve()
    d = make_case(tmp_path)
    graph = reviewed_graph(d, g28_card=False)
    g28.confirm(d, "Jane Paralegal", "paralegal", graph)
    with pytest.raises(ValueError, match="Say why"):
        g28.change(d, "1a", False, "", "Jane Paralegal", "paralegal", graph)
    with pytest.raises(ValueError, match="Enter your name"):
        g28.change(d, "1a", False, "The client lives with the attorney's office staff.", "", "paralegal", graph)
    assert card(d)["state"] == "confirmed"
    g28.change(d, "1a", False, "The client wants the notices at home.", "Sam Paralegal", "paralegal", graph)
    c = card(d)
    assert c["state"] == "changed" and not c["part4"][0]["marked"]
    assert c["part4"][0]["changed"] == {"reason": "The client wants the notices at home.", "by": "Sam Paralegal", "role": "paralegal", "at": c["part4"][0]["changed"]["at"]}
    assert c["part4"][0]["office_setting"] is True  # the office's setting is still shown beside it
    assert g28.problems(d, ["g28"]) == [g28.NOT_CONFIRMED]
    g28.confirm(d, "Sam Paralegal", "paralegal", graph)
    assert g28.problems(d, ["g28"]) == []
    assert [r["action"] for r in ledger(tmp_path)] == ["confirmed", "changed", "confirmed"]
    assert all("client wants" not in r["what"] for r in ledger(tmp_path))  # a ledger row never carries what a person wrote


def test_a_ledger_row_names_the_g28_once(tmp_path):
    approve()
    d = make_case(tmp_path, OWN_MAIL)
    graph = reviewed_graph(d, g28_card=False)
    g28.change(d, "mail", "mailing", "The client gave a mailing address.", "Jane", "paralegal", graph)
    g28.change(d, "mail", "physical", "", "Jane", "paralegal", graph)
    g28.change(d, "1a", True, "The client asked for it.", "Jane", "paralegal", graph)
    g28.change(d, "others", "client", "The client's own address is wanted.", "Jane", "paralegal", graph)
    assert [r["what"] for r in ledger(tmp_path)] == [
        "Changed the client's mailing address on the G-28", "Went back to the office's setting for the client's mailing address on the G-28",
        "Changed Part 4, item 1.a on the G-28", "Changed the mailing address on the I-485 and the I-765, from the G-28's card"]
    assert all(r["what"].count("G-28") == 1 or "from the G-28's card" in r["what"] for r in ledger(tmp_path))


def test_going_back_to_the_offices_setting_needs_no_reason(tmp_path):
    office_policy(**{"1c": "on"})
    approve()
    d = make_case(tmp_path)
    graph = reviewed_graph(d, g28_card=False)
    g28.change(d, "1c", False, "The client wants the I-94 sent to the attorney too.", "Jane", "paralegal", graph)
    assert not card(d)["part4"][2]["marked"]
    g28.change(d, "1c", True, "", "Jane", "paralegal", graph)
    c = card(d)
    assert c["part4"][2]["marked"] and c["part4"][2]["changed"] is None
    assert [r["action"] for r in ledger(tmp_path)] == ["changed", "reset"]


def test_undo_puts_the_card_back_as_it_was(tmp_path):
    approve()
    d = make_case(tmp_path)
    graph = reviewed_graph(d, g28_card=False)
    with pytest.raises(ValueError, match="nothing to take back"):
        g28.undo(d, "Jane", "paralegal")
    g28.confirm(d, "Jane", "paralegal", graph)
    g28.change(d, "1b", True, "The client will be away; the attorney can receive the card.", "Jane", "paralegal", graph)
    assert card(d)["state"] == "changed" and card(d)["part4"][1]["marked"]
    g28.undo(d, "Sam Paralegal", "paralegal")
    c = card(d)
    assert c["state"] == "confirmed" and not c["part4"][1]["marked"]
    g28.undo(d, "Sam Paralegal", "paralegal")  # the confirmation itself
    assert card(d)["state"] == "not_confirmed"
    with pytest.raises(ValueError, match="nothing to take back"):
        g28.undo(d, "Sam Paralegal", "paralegal")
    assert [r["action"] for r in ledger(tmp_path)] == ["confirmed", "changed", "undone", "undone"]
    log = json.loads((d / g28.FILE).read_text(encoding="utf-8"))["history"]
    assert [h["action"] for h in log] == ["confirmed", "changed", "undone", "undone"] and log[1]["undone"]["by"] == "Sam Paralegal"


def test_a_change_to_the_office_setting_or_its_approval_unconfirms_the_card(tmp_path):
    office_policy(mail="on")
    approve()
    d = make_case(tmp_path)
    g28.confirm(d, "Jane", "paralegal", reviewed_graph(d, g28_card=False))
    assert g28.problems(d, ["g28"]) == []
    office_policy(mail="off")
    assert g28.problems(d, ["g28"]) == [g28.NOT_CONFIRMED] and card(d)["state"] == "changed"


def test_a_confirmation_covers_the_address_lines_it_was_made_on(tmp_path):
    office_policy(mail="on")
    approve()
    d = make_case(tmp_path)
    g28.confirm(d, "Jane", "paralegal", reviewed_graph(d, g28_card=False))
    saved = json.loads((d / g28.FILE).read_text(encoding="utf-8"))["confirmed"]["address"]
    assert saved == {"carried": ["100 EXAMPLE WAY", "SPRINGFIELD, MA 01101"], "office": ["100 EXAMPLE WAY", "SPRINGFIELD, MA 01101"]}
    assert g28.problems(d, ["g28"]) == []
    settings.save("firm", {"firm.street": "200 NEW ROAD"}, "Test Attorney")  # the street is not in the practice's words: the approval stands
    assert g28.approved() and g28.problems(d, ["g28"]) == [g28.NOT_CONFIRMED]
    c = card(d)
    assert c["state"] == "changed" and "The address the form carries in item 13 changed." in c["why"] and "The office's address changed." in c["why"]
    g28.confirm(d, "Jane", "paralegal", reviewed_graph(d, g28_card=False))
    assert g28.problems(d, ["g28"]) == [] and card(d)["why"] == ""


def test_the_clients_address_changing_or_turning_into_a_conflict_unconfirms_the_card(tmp_path):
    approve()
    d = make_case(tmp_path)
    g28.confirm(d, "Jane", "paralegal", reviewed_graph(d, g28_card=False))
    assert g28.problems(d, ["g28"]) == []
    graph = FactGraph.load(d / "fact_graph.json")
    graph.add_source("applicant.physical_street", "other.pdf", "utility_bill", "99 OTHER STREET", "99 OTHER STREET", 0.9)  # a second, different street
    graph.save(d / "fact_graph.json")
    assert g28.problems(d, ["g28"]) == [g28.NOT_CONFIRMED]
    c = card(d)
    assert c["state"] == "changed" and c["why"] == "The address the form carries in item 13 is not settled now."


def test_an_address_not_on_file_cannot_be_chosen(tmp_path):
    approve()
    d = make_case(tmp_path)
    graph = reviewed_graph(d, g28_card=False)
    with pytest.raises(ValueError, match="not on file"):
        g28.change(d, "mail", "mailing", "The client has a mailing address.", "Jane", "paralegal", graph)
    settings.save("firm", {"firm.zip": ""}, "Test Attorney")
    approve()  # a change to the office section asks for the approval again
    with pytest.raises(ValueError, match="not on file"):
        g28.change(d, "mail", "office", "The office is the safe address.", "Jane", "paralegal", graph)


# -- the gate ----------------------------------------------------------------------------------------------------------

ROW = {"summary": {"name": "ANA SAMPLE"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}


def test_the_packet_is_not_ready_until_the_g28_card_is_confirmed(tmp_path, monkeypatch):
    d = make_case(tmp_path)
    schema = packet.load_schema() | {"forms": ["g28", "i485", "i765"], "cover_letter": False}
    p = packet.plan(d, ROW, schema)
    assert g28.NOT_CONFIRMED == "The G-28's choices were not confirmed for this case." and g28.NOT_CONFIRMED in p["problems"] and not p["ready"]
    approve()
    g28.confirm(d, "Jane Paralegal", "paralegal", reviewed_graph(d, g28_card=False))
    assert g28.NOT_CONFIRMED not in packet.plan(d, ROW, schema)["problems"]
    only_i485 = packet.plan(d, ROW, schema | {"forms": ["i485"]})
    assert g28.NOT_CONFIRMED not in only_i485["problems"]


def built_case(tmp_path):
    from fill import fill_pdf

    d = make_case(tmp_path)
    (d / "i485_filled.pdf").parent.mkdir(exist_ok=True)
    fill_pdf(schema_path.path("template", "i485"), {}, d / "i485_filled.pdf")
    return d, packet.load_schema() | {"forms": ["g28", "i485"], "cover_letter": False, "index_sheet": True}


def test_a_packet_built_before_a_change_says_so_and_is_not_ready_until_rebuilt(tmp_path):
    approve()
    d, schema = built_case(tmp_path)
    graph = reviewed_graph(d, g28_card=False)
    g28.confirm(d, "Jane", "paralegal", graph)
    manifest = packet.build(d, ROW, "Jane", schema)
    assert manifest["g28"]["confirmed"] and manifest["g28"]["facts"]["g28.mail_street"] == "12 SAMPLE STREET"
    assert not any("changed after this packet was built" in p for p in packet.plan(d, ROW, schema)["problems"])  # as built: nothing to say
    assert g28.stale_notes(d) == []
    g28.change(d, "1c", True, "The client asked for the I-94 notice to go to the attorney's office.", "Jane", "paralegal", graph)
    g28.change(d, "1c", False, "", "Jane", "paralegal", graph)  # and back: the card holds what it held, the packet matches it again
    g28.confirm(d, "Jane", "paralegal", graph)
    assert g28.stale_notes(d) == []
    office_policy(**{"1a": "on"})
    approve()
    g28.confirm(d, "Jane", "paralegal", reviewed_graph(d, g28_card=False))  # the office's setting changed what the G-28 carries
    p = packet.plan(d, ROW, schema)
    said = [x for x in p["problems"] if "changed after this packet was built" in x]
    assert said and said[0].startswith("The G-28 choices changed after this packet was built on ") and said[0].endswith("; rebuild it.") and not p["ready"]
    assert len(g28.stale_notes(d)) == 1  # the case page's note reads the same manifest
    manifest = packet.build(d, ROW, "Jane", schema)  # rebuilt: matches, and the build itself is not held back by the old manifest
    assert manifest["g28"]["facts"]["g28.part4_1a"] == "Yes" and not any("changed after" in x for x in manifest["problems"])
    assert not any("changed after this packet was built" in x for x in packet.plan(d, ROW, schema)["problems"])


def test_the_case_pages_note_names_a_packet_built_before_a_change(tmp_path):
    approve()
    d, schema = built_case(tmp_path)
    graph = reviewed_graph(d, g28_card=False)
    g28.confirm(d, "Jane", "paralegal", graph)
    packet.build(d, ROW, "Jane", schema)
    assert g28.stale_notes(d) == []
    office_policy(**{"1b": "on"})
    approve()
    notes = g28.stale_notes(d)
    assert len(notes) == 1 and notes[0].startswith("The G-28 choices changed after this packet was built on ") and "rebuild it" in notes[0]


# -- the filled G-28, box by box ---------------------------------------------------------------------------------------

def filled(tmp_path, d):
    """The G-28 filled the way a packet fills it: the card's facts on the reviewed case."""
    graph = reviewed_graph(d)
    g28.apply(graph, d)
    profile = load_profile()
    profile["forms"] = {"g28": profile["forms"]["g28"]}
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    fill_companions(graph, out, profile)
    values: dict[str, list] = {}
    for name, field in (PdfReader(str(out / "g28_filled.pdf")).get_fields() or {}).items():
        values.setdefault(name.rsplit(".", 1)[-1], []).append(field.get("/V"))
    return values


def box(values, short):
    got = [v for v in values.get(short, []) if v not in (None, "", "/Off")]
    return str(got[0]).strip() if got else None


CLIENT_BOXES = {"Line12a_StreetNumberName[0]": "12 SAMPLE STREET", "Line12c_CityOrTown[0]": "WORCESTER", "Line12d_State[0]": "MA", "Line12e_ZipCode[0]": "01602"}
OFFICE_BOXES = {"Line12a_StreetNumberName[0]": "100 EXAMPLE WAY", "Line12c_CityOrTown[0]": "SPRINGFIELD", "Line12d_State[0]": "MA", "Line12e_ZipCode[0]": "01101"}


@pytest.mark.parametrize("short", sorted(CLIENT_BOXES))
@pytest.mark.parametrize("policy,expect", [("off", CLIENT_BOXES), ("on", OFFICE_BOXES)])
def test_item_13_is_the_clients_address_or_the_offices_by_the_policy(tmp_path, short, policy, expect):
    office_policy(mail=policy)
    approve()
    assert box(filled(tmp_path, make_case(tmp_path)), short) == expect[short]


@pytest.mark.parametrize("short", sorted(CLIENT_BOXES))
def test_item_13_is_the_clients_own_address_while_the_policy_is_not_approved(tmp_path, short):
    office_policy(mail="on")
    assert box(filled(tmp_path, make_case(tmp_path)), short) == CLIENT_BOXES[short]


def test_item_13_with_no_suite_has_a_blank_suite_line_for_the_office_and_for_the_client(tmp_path):
    office_policy(mail="on")
    approve()
    v = filled(tmp_path, make_case(tmp_path))
    assert box(v, "Line12b_AptSteFlrNumber[0]") is None and box(v, "Line12b_Unit[0]") is None and box(v, "Line12b_Unit[1]") is None and box(v, "Line12b_Unit[2]") is None
    assert box(v, "Line3b_AptSteFlrNumber[0]") is None  # and the attorney's own address in Part 1


@pytest.mark.parametrize("kind,on_value,index", [("STE", "/ STE", 0), ("FLR", "/ FLR", 1), ("APT", "/ APT", 2)])
def test_item_13_carries_the_offices_suite_floor_or_apartment_when_its_address_has_one(tmp_path, kind, on_value, index):
    settings.save("firm", {"firm.unit_type": kind, "firm.apt": "200", "office.g28_mail": "on"}, "Test Attorney")
    approve()
    v = filled(tmp_path, make_case(tmp_path))
    assert box(v, "Line12b_AptSteFlrNumber[0]") == "200"
    assert box(v, f"Line12b_Unit[{index}]") == on_value
    assert box(v, "Line3b_AptSteFlrNumber[0]") == "200" and box(v, f"Line3b_Unit[{index}]") == on_value  # Part 1, item 3.b: the attorney's own address too


def test_the_offices_blank_suite_line_is_never_the_clients_apartment(tmp_path):
    office_policy(mail="on")
    approve()
    d = make_case(tmp_path, {"applicant.physical_apt": "2", "applicant.physical_unit_type": "APT"})
    v = filled(tmp_path, d)
    assert box(v, "Line12a_StreetNumberName[0]") == "100 EXAMPLE WAY"
    assert box(v, "Line12b_AptSteFlrNumber[0]") is None and box(v, "Line12b_Unit[2]") is None


def test_an_apartment_number_with_no_type_reaches_13_b_as_master_wrote_it(tmp_path):
    d = make_case(tmp_path, {"applicant.physical_apt": "3B"})  # a reviewer's typed apartment: no APT, STE or FLR
    v = filled(tmp_path, d)
    assert box(v, "Line12b_AptSteFlrNumber[0]") == "3B"
    assert all(box(v, f"Line12b_Unit[{i}]") is None for i in range(3))
    assert card(d)["mail"]["options"][1]["lines"] == ["12 SAMPLE STREET, 3B", "WORCESTER, MA 01602"]
    graph = reviewed_graph(d)  # and the same on the path with no card (master's)
    profile = load_profile()
    profile["forms"] = {"g28": profile["forms"]["g28"]}
    out = tmp_path / "nocard"
    out.mkdir()
    fill_companions(graph, out, profile)
    plain = {n.rsplit(".", 1)[-1]: f.get("/V") for n, f in (PdfReader(str(out / "g28_filled.pdf")).get_fields() or {}).items()}
    assert str(plain["Line12b_AptSteFlrNumber[0]"]).strip() == "3B"


def test_item_13_carries_the_clients_own_apartment_when_it_has_one(tmp_path):
    d = make_case(tmp_path, {"applicant.physical_apt": "3B", "applicant.physical_unit_type": "APT"})
    v = filled(tmp_path, d)
    assert box(v, "Line12b_AptSteFlrNumber[0]") == "3B" and box(v, "Line12b_Unit[2]") == "/ APT"


def test_item_13_is_the_clients_mailing_address_when_chosen(tmp_path):
    approve()
    d = make_case(tmp_path, OWN_MAIL)
    g28.change(d, "mail", "mailing", "The client gave a mailing address.", "Jane", "paralegal", reviewed_graph(d, g28_card=False))
    v = filled(tmp_path, d)
    assert box(v, "Line12a_StreetNumberName[0]") == "PO BOX 77" and box(v, "Line12e_ZipCode[0]") == "01603"


def test_the_attorneys_name_and_bar_number_are_the_offices(tmp_path):
    v = filled(tmp_path, make_case(tmp_path))
    assert box(v, "Pt1Line2a_FamilyName[0]") == "EXEMPLO" and box(v, "Pt1Line2b_GivenName[0]") == "ANA" and box(v, "Pt2Line1b_BarNumber[0]") == "123456"
    assert box(v, "Line3a_StreetNumber[0]") == "100 EXAMPLE WAY" and box(v, "Line3c_CityOrTown[0]") == "SPRINGFIELD" and box(v, "Line3e_ZipCode[0]") == "01101"


def test_the_clients_name_boxes_are_the_one_settled_name(tmp_path):
    v = filled(tmp_path, make_case(tmp_path))
    assert box(v, "Pt3Line5a_FamilyName[0]") == "SOUZA" and box(v, "Pt3Line5b_GivenName[0]") == "ANA" and box(v, "Pt3Line5c_MiddleName[0]") == "CLARA"


PART4_BOXES = {"1a": "Pt4Line2a_CheckBox2a[0]", "1b": "Pt4Line2b_CheckBox2b[0]", "1c": "Pt4Line2c_CheckBox2c[0]"}


@pytest.mark.parametrize("item", g28.ITEMS)
@pytest.mark.parametrize("setting", ["on", "off"])
def test_part_4_boxes_follow_the_office_setting_once_approved(tmp_path, item, setting):
    office_policy(**{item: setting})
    approve()
    v = filled(tmp_path, make_case(tmp_path))
    assert (box(v, PART4_BOXES[item]) == "/Y") is (setting == "on")
    for other in set(g28.ITEMS) - {item}:
        assert box(v, PART4_BOXES[other]) is None


@pytest.mark.parametrize("item", g28.ITEMS)
def test_part_4_is_blank_while_the_policy_is_not_approved(tmp_path, item):
    office_policy(**{item: "on"})
    assert box(filled(tmp_path, make_case(tmp_path)), PART4_BOXES[item]) is None


@pytest.mark.parametrize("item", g28.ITEMS)
def test_a_persons_change_on_the_case_reaches_the_form_once_the_practice_is_approved(tmp_path, item):
    approve()
    d = make_case(tmp_path)
    g28.change(d, item, True, "The client asked for it.", "Jane", "paralegal", reviewed_graph(d, g28_card=False))
    assert box(filled(tmp_path, d), PART4_BOXES[item]) == "/Y"


@pytest.mark.parametrize("item,value", [("mail", "office"), ("1a", True), ("1b", True), ("1c", True), ("others", "office"), ("mail", "mailing"), ("others", "client")])
def test_while_the_practice_is_not_approved_every_change_is_refused_for_everyone_with_a_sentence_and_a_ledger_row(tmp_path, item, value):
    d = make_case(tmp_path, OWN_MAIL)
    graph = reviewed_graph(d, g28_card=False)
    for role in ("paralegal", "attorney"):
        with pytest.raises(PermissionError) as refused:
            g28.change(d, item, value, "The client asked for it.", "Someone", role, graph)
        assert str(refused.value) == g28.WAITING and "Approve this practice" in g28.WAITING  # what is waiting and where it is approved
    assert not (d / g28.FILE).exists()  # nothing recorded on the card
    rows = ledger(tmp_path)
    assert [r["action"] for r in rows] == ["refused", "refused"] and rows[0]["what"].startswith("Refused: a change to ")  # never dropped silently
    v = filled(tmp_path, d)
    assert box(v, "Line12a_StreetNumberName[0]") == "12 SAMPLE STREET" and all(box(v, b) is None for b in PART4_BOXES.values())
    assert card(d)["policy"]["says"] == g28.WAITING


def test_while_the_practice_is_not_approved_the_card_cannot_be_confirmed(tmp_path):
    d = make_case(tmp_path)
    with pytest.raises(PermissionError) as refused:
        g28.confirm(d, "Jane Paralegal", "paralegal", reviewed_graph(d, g28_card=False))
    assert str(refused.value) == g28.WAITING and card(d)["state"] == "not_confirmed"
    assert [r["action"] for r in ledger(tmp_path)] == ["refused"]
    approve()
    g28.confirm(d, "Jane Paralegal", "paralegal", reviewed_graph(d, g28_card=False))
    assert card(d)["state"] == "confirmed"


def test_a_change_recorded_before_the_approval_lapsed_does_not_reach_the_form(tmp_path):
    office_policy(mail="on", **{"1a": "on"})
    approve()
    d = make_case(tmp_path)
    graph = reviewed_graph(d, g28_card=False)
    g28.change(d, "mail", "office", "The office is the safe address.", "Jane", "paralegal", graph)
    g28.change(d, "1b", True, "The client asked for it.", "Jane", "paralegal", graph)
    office_policy(**{"1c": "on"})  # a switch changes: the approval lapses
    v = filled(tmp_path, d)
    assert box(v, "Line12a_StreetNumberName[0]") == "12 SAMPLE STREET" and all(box(v, b) is None for b in PART4_BOXES.values())
    c = card(d)
    assert c["mail"]["chosen"] == "physical" and [p["marked"] for p in c["part4"]] == [False, False, False]


def test_a_g28_filled_without_a_card_keeps_the_clients_address_and_a_blank_part_4(tmp_path):
    graph = reviewed_graph(make_case(tmp_path))
    profile = load_profile()
    profile["forms"] = {"g28": profile["forms"]["g28"]}
    out = tmp_path / "plain"
    out.mkdir()
    fill_companions(graph, out, profile)  # the accuracy tool's way: no card
    values: dict[str, list] = {}
    for name, field in (PdfReader(str(out / "g28_filled.pdf")).get_fields() or {}).items():
        values.setdefault(name.rsplit(".", 1)[-1], []).append(field.get("/V"))
    assert box(values, "Line12a_StreetNumberName[0]") == "12 SAMPLE STREET" and box(values, "Pt4Line2a_CheckBox2a[0]") is None


# -- the I-485's and the I-765's mailing address ------------------------------------------------------------------------

T_CASE = {"applicant.filing_category": "T nonimmigrant (I-914)"}
FIRM_CARE_OF = {"applicant.mailing_street": "100 EXAMPLE WAY", "applicant.mailing_city": "SPRINGFIELD", "applicant.mailing_state": "MA", "applicant.mailing_zip": "01101",
                "applicant.mailing_in_care_of": "EXEMPLO LAW LLP", "applicant.mailing_same_as_physical": "No"}


def with_firm_care_of(graph):
    """The case as a batch-built one holds it: the firm's details as the I-485's mailing address, in care of the firm."""
    for key, value in FIRM_CARE_OF.items():
        graph.add_source(key, "firm_profile.json", "firm_profile", value, value, 1.0)
    return graph


def test_the_instruction_paragraphs_are_held_whole_with_where_and_when_they_were_read():
    for form in ("g28", "g28_part4", "i485", "i765"):
        ins = g28.instruction(form)
        assert ins["line"] and ins["document"] and ins["url"].startswith("https://www.uscis.gov/") and ins["read"] == "2026-10-03"
        assert "allowed_when" not in ins  # the product holds the words and enforces no reading of them
    i485 = g28.instruction("i485")["line"]
    for sentence in ("If you have a pending or approved petition or application for Violence Against Women Act (VAWA) benefits, as a human trafficking victim "
                     "(T nonimmigrant), or as a victim of qualifying criminal activity (U nonimmigrant), and you do not feel safe receiving mail about this "
                     "application at your physical address, provide a safe mailing address in this field.",
                     "If you are filing as a special immigrant juvenile (SIJ), you may designate an alternate mailing address in this field.",
                     "The safe or alternate address may be a P.O. Box or the address of a friend, your attorney or accredited representative, a community-based "
                     "organization that is helping you, or any other address where you can safely and timely receive mail.",
                     "If your mail is sent to someone other than yourself, please include an “In Care Of Name” as part of your mailing address."):
        assert sentence in i485
    assert i485.startswith("7. Current Mailing Address (Safe or Alternate Address, if applicable).")
    i765 = g28.instruction("i765")["line"]
    assert "Do not use your attorney’s or other legal representative’s address unless you want your EAD sent to their address." in i765
    assert "you may also direct USCIS to send your correspondence and EAD to your attorney’s business address" in i765 and "(T nonimmigrant)" in i765


def test_the_paragraphs_are_what_uscis_prints(tmp_path):
    """The held paragraphs read as the instructions' own text does (a copy of the PDF text is not in the repository: this holds the two sentences a
    reader could miss, so a paragraph cut short again fails)."""
    held = g28.instruction("i485")["line"]
    assert held.index("(T nonimmigrant)") < held.index("special immigrant juvenile") < held.index("The safe or alternate address may be")


def test_the_card_names_each_form_its_paragraph_and_what_it_carries(tmp_path):
    d = make_case(tmp_path, {"applicant.filing_category": "SIJS"})
    c = card(d)
    forms = {f["id"]: f for f in c["others"]["forms"]}
    assert set(forms) == {"i485", "i765"}
    assert forms["i485"]["line"] == g28.instruction("i485")["line"] and forms["i765"]["line"] == g28.instruction("i765")["line"]
    assert all(f["url"] and f["read"] and f["document"] for f in forms.values())
    assert c["others"]["carries"]["who"] == "client" and c["others"]["decides"] == "The attorney decides which cases this covers."
    assert not any(k in c["others"] for k in ("can_choose_office", "needs_choice", "why_client"))  # no reading of the paragraph is enforced


@pytest.mark.parametrize("category", ["SIJS", "T nonimmigrant (I-914)", "U nonimmigrant (I-918)", "Immediate relative", None])
def test_whatever_the_category_the_card_is_confirmed_with_no_choice_on_the_other_forms(tmp_path, category):
    d = make_case(tmp_path, {"applicant.filing_category": category} if category else {})
    graph = with_firm_care_of(reviewed_graph(d, g28_card=False))
    c = g28.card(d, ["g28", "i485", "i765"], graph)
    assert c["others"]["carries"]["who"] == "office" and c["others"]["chosen"] is None
    approve()
    g28.confirm(d, "Jane", "paralegal", graph)  # never asks for "client"
    assert g28.problems(d, ["g28", "i485"]) == []
    out = g28.apply_mailing(with_firm_care_of(reviewed_graph(d, g28_card=False)), d)  # nobody chose: the firm's in-care-of address is untouched
    assert out.get("applicant.mailing_in_care_of").value == "EXEMPLO LAW LLP" and out.get("applicant.mailing_street").value == "100 EXAMPLE WAY"


def test_on_a_t_case_the_offices_address_is_accepted_once_the_practice_is_approved(tmp_path):
    office_policy(mail="on")
    approve()
    d = make_case(tmp_path, T_CASE)
    graph = with_firm_care_of(reviewed_graph(d, g28_card=False))
    g28.change(d, "others", "office", "The client cannot safely receive mail at home.", "Jane", "paralegal", graph)
    after = g28.apply_mailing(reviewed_graph(d, g28_card=False), d)
    assert after.get("applicant.mailing_street").value == "100 EXAMPLE WAY" and after.get("applicant.mailing_in_care_of").value == "EXEMPLO LAW LLP"
    assert after.get("applicant.mailing_same_as_physical").value == "No"
    g28.confirm(d, "Jane", "paralegal", graph)
    assert card(d)["state"] == "confirmed"


def test_the_offices_address_on_the_other_forms_is_laid_over_a_clients_own_mailing_address(tmp_path):
    approve()
    d = make_case(tmp_path, {**T_CASE, **OWN_MAIL})
    g28.change(d, "others", "office", "The client asked for the office's address.", "Jane", "paralegal", reviewed_graph(d, g28_card=False))
    out = reviewed_graph(d)
    assert out.get("applicant.mailing_street").value == "100 EXAMPLE WAY" and out.get("applicant.mailing_same_as_physical").value == "No"


def test_choosing_the_clients_own_address_takes_the_firm_care_of_address_off_the_i485_when_a_person_chooses_it(tmp_path):
    d = make_case(tmp_path, {"applicant.filing_category": "Immediate relative"})
    reviewed = with_firm_care_of(reviewed_graph(d))  # the firm's details, as a packet's reviewed case holds them
    out = g28.apply_mailing(reviewed, d)  # no choice recorded: untouched
    assert out.get("applicant.mailing_street").value == "100 EXAMPLE WAY"
    approve()
    g28.change(d, "others", "client", "The client gave a home address to use.", "Jane", "paralegal", reviewed_graph(d, g28_card=False))
    out = g28.apply_mailing(reviewed, d)
    assert out.get("applicant.mailing_street") is None and out.get("applicant.mailing_same_as_physical").value == "Yes"


@pytest.mark.parametrize("ids,expect", [(["g28", "i485", "i765"], [("i485", "i485"), ("i765", "i765")]), (["g28_ead", "ead"], [("ead", "i765")]),
                                        (["g28_daca", "i821d", "i765_daca"], [("i765_daca", "i765")]), (["g28_tps", "i821", "i765_tps"], [("i765_tps", "i765")]),
                                        (["g28", "i485"], [("i485", "i485")]), (["g28_i360"], [])])
def test_one_helper_says_which_forms_ask_for_a_safe_address_whatever_id_the_i765_has(ids, expect):
    assert g28.other_forms(ids) == expect


def test_a_stand_alone_i765_packet_gets_the_other_forms_section_and_the_choice_is_accepted(tmp_path):
    approve()
    d = make_case(tmp_path)
    graph = reviewed_graph(d, g28_card=False)
    c = g28.card(d, ["g28_ead", "ead"], graph)
    assert c is not None and [f["id"] for f in c["others"]["forms"]] == ["ead"] and c["others"]["forms"][0]["line"] == g28.instruction("i765")["line"]
    g28.change(d, "others", "office", "The client wants the work permit sent to the office.", "Jane", "paralegal", graph)
    assert g28.card(d, ["g28_ead", "ead"], graph)["others"]["chosen"] == "office"


# -- the T and U petitions' G-28s are not the card's -------------------------------------------------------------------

def fill_form(tmp_path, d, form_id, extra=None):
    """A G-28 filled the way a T or U filing fills it (src/t_visa.py): its own facts on the reviewed case, no card."""
    graph = reviewed_graph(d)
    for key, value in (extra or {}).items():
        graph.add_source(key, "filing", "derived", value, value, 1.0)
    profile = load_profile()
    profile["forms"] = {form_id: profile["forms"][form_id]}
    out = tmp_path / form_id
    out.mkdir(exist_ok=True)
    fill_companions(graph, out, profile)
    values: dict[str, list] = {}
    for name, field in (PdfReader(str(out / profile["forms"][form_id]["output"])).get_fields() or {}).items():
        values.setdefault(name.rsplit(".", 1)[-1], []).append(field.get("/V"))
    return values


def test_the_card_does_not_govern_the_t_and_u_petitions_g28s():
    assert g28.governed(["g28_i914", "g28_i918", "g28_i130", "g28_vawa"]) == []
    assert g28.governed(["g28", "g28_i914", "g28_ead", "g28_i918"]) == ["g28", "g28_ead"]
    forms = load_profile()["forms"]
    for fid in ("g28_i914", "g28_i918"):
        assert g28.GOVERNED_KEY not in forms[fid]["map"] and not any(k.startswith("g28.") for k in forms[fid]["map"])


@pytest.mark.parametrize("form_id,safe", [("g28_i914", "tvisa"), ("g28_i918", "uvisa")])
def test_a_t_and_a_u_g28_carry_what_their_own_filing_gave_them_with_the_offices_switch_on(tmp_path, form_id, safe):
    """With the office's switch on and approved, the T and the U petition's G-28 still carry the item 13 address their own filing fills: not the card's
    (the office's address, a Part 4 mark), whatever the paralegal confirms for the I-485's G-28."""
    office_policy(mail="on", **{"1a": "on", "1b": "on", "1c": "on"})
    approve()
    d = make_case(tmp_path)
    g28.change(d, "mail", "office", "The office is the safe address.", "Jane", "paralegal", reviewed_graph(d, g28_card=False))
    safe_address = {f"{safe}.safe_street": "9 FRIEND LANE", f"{safe}.safe_city": "BOSTON", f"{safe}.safe_state": "MA", f"{safe}.safe_zip": "02108"}
    v = fill_form(tmp_path, d, form_id, safe_address)
    assert box(v, "Line12a_StreetNumberName[0]") == "12 SAMPLE STREET" and box(v, "Line12c_CityOrTown[0]") == "WORCESTER" and box(v, "Line12e_ZipCode[0]") == "01602"
    assert box(v, "Pt4Line2a_CheckBox2a[0]") is None and box(v, "Pt4Line2b_CheckBox2b[0]") is None and box(v, "Pt4Line2c_CheckBox2c[0]") is None
    assert box(v, "Pt1Line2a_FamilyName[0]") == "EXEMPLO"  # the attorney is the office's, as on every G-28


@pytest.mark.parametrize("form_id", ["g28_i914", "g28_i918"])
def test_the_t_and_u_g28s_match_the_maps_master_wrote_apart_from_the_attorneys_suite_line(form_id):
    old = json.loads((Path(__file__).parent / "fixtures" / "g28_t_u_maps_master.json").read_text(encoding="utf-8"))[form_id]
    now = load_profile()["forms"][form_id]
    added = {"firm.unit_type", "firm.apt"}
    assert {k: v for k, v in now["map"].items() if k not in added} == old["map"]
    assert set(now["optional"]) - set(old["optional"]) == added


# -- the review bundle and the record ----------------------------------------------------------------------------------

def test_the_review_bundle_says_who_confirmed_and_what_was_changed(tmp_path):
    office_policy(**{"1a": "on"})
    approve()
    d = make_case(tmp_path)
    assert g28.bundle_rows(d) is None
    graph = reviewed_graph(d, g28_card=False)
    g28.change(d, "1a", False, "The client wants notices at home.", "Sam", "paralegal", graph)
    g28.confirm(d, "Jane Paralegal", "paralegal", graph)
    rows = g28.bundle_rows(d)
    assert rows["state"] == "confirmed" and rows["confirmed"]["by"] == "Jane Paralegal"
    assert rows["part4"][0]["marked"] is False and rows["part4"][0]["office_setting"] is True and rows["part4"][0]["changed"]["by"] == "Sam"
    assert rows["mail_note"] == g28.mail_note() and [h["what"] for h in rows["history"]][-1] == "Confirmed the G-28's choices"


def test_the_record_is_listed_in_the_data_dictionary():
    import records

    entry = next(r for r in records.RECORDS if r["id"] == "g28_choices")
    assert entry["files"] == [g28.FILE] and {f[0] for f in entry["fields"]} >= {"choices", "confirmed", "history"}


def test_the_card_never_says_a_choice_complies_with_anything(tmp_path):
    office_policy(mail="on", **{"1a": "on", "1b": "on", "1c": "on"})
    approve()
    text = json.dumps(card(make_case(tmp_path, {"applicant.filing_category": "SIJS"}))).lower()
    for word in ("complies", "compliant", "in compliance", "legally required", "satisfies"):
        assert word not in text
    assert "the attorney decides" in text
