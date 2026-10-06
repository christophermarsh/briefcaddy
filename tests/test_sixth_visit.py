# ruff: noqa: F811  (the fixtures imported from test_restricted are used as arguments)
"""Fictional example or implementation helper."""

from __future__ import annotations

import json
import schema_path
import sys
import time
from pathlib import Path

import pytest
from pypdf import PdfReader

from test_restricted import world  # noqa: F401 -- the made-up restricted world (its cases and review app), used as an argument below

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = schema_path.path("template", "i485")
# the shipped sample firm's name, attorney, bar number, account number, street, phone and e-mail (schemas/firm/firm_profile.json, companion_forms.json,
# cover_letter.json): none may reach a form or a letter on a copy whose office is not saved
_SAMPLE = json.loads(schema_path.path("firm", "firm_profile").read_text(encoding="utf-8"))["facts"]
SAMPLE_WORDS = {str(_SAMPLE[k]).upper() for k in ("firm.business_name", "firm.preparer_family_name", "firm.attorney_bar_number", "firm.uscis_online_account_number",
                                                  "applicant.mailing_street", "firm.phone", "firm.email")}


@pytest.fixture
def fresh_settings(tmp_path, monkeypatch):
    """A fresh install: nothing saved under Settings."""
    import settings

    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    return settings


@pytest.fixture
def demo_case(tmp_path):
    from portal import demo as seed
    from portal.store import PortalStore

    seed.seed(PortalStore(tmp_path / "portal"), tmp_path / "clients")
    return tmp_path / "clients" / seed.DEMO_ID


def _boxes(path: Path) -> list[str]:
    fields = PdfReader(str(path)).get_fields() or {}
    return [str(v.get("/V") or "") for v in fields.values() if v.get("/V") not in (None, "", "/Off")]


# -- 2. an unconfigured copy prints no firm -----------------------------------------------------------------------------------------------------


def test_the_shipped_firm_profile_is_marked_a_sample():
    assert json.loads(schema_path.path("firm", "firm_profile").read_text(encoding="utf-8"))["sample"] is True


def test_a_packet_built_on_a_fresh_install_carries_nothing_of_the_sample_firm_and_waits_for_the_office(fresh_settings, demo_case):
    import offices
    import packet
    from fill import load_field_map
    from review.overview import review_row
    from review.state import Catalog, refill

    assert fresh_settings.identity_withheld() and not fresh_settings.values("firm")
    field_map = load_field_map(schema_path.path("field_map", "i485"))
    policies = json.loads(schema_path.path("law", "policy_sijs").read_text(encoding="utf-8"))["policies"]
    refill(demo_case, field_map, TEMPLATE)
    manifest = packet.build(demo_case, review_row(demo_case, field_map, TEMPLATE, Catalog(field_map, TEMPLATE, policies)), "Jane", packet.load_filing("i485"))
    assert offices.NO_OFFICE in manifest["problems"] and manifest["draft"]          # one blocker: the office first
    assert offices.NO_OFFICE.startswith("Save the office under Settings before this packet can be built")
    filled = sorted(demo_case.glob("*_filled.pdf"))
    assert {p.name for p in filled} >= {"i485_filled.pdf", "g28_filled.pdf", "i765_filled.pdf"}
    for pdf in filled:
        for value in _boxes(pdf):
            assert not any(word in value.upper() for word in SAMPLE_WORDS), (pdf.name, value)
    text = " ".join((p.extract_text() or "") for p in PdfReader(str(demo_case / "packet.pdf")).pages).upper()
    for word in SAMPLE_WORDS - {"2025550100"}:
        assert word not in text, word
    assert "884-1000" not in text and "GEORGES |" not in text                       # the shipped letterhead's phone and name are not on the cover letter


def test_the_g28_card_says_to_save_the_office_instead_of_naming_a_firm(fresh_settings, demo_case):
    import g28
    import offices
    from factgraph import FactGraph

    graph = FactGraph.load(demo_case / "fact_graph.json")
    card = g28.card(demo_case, ["g28", "i485", "i765"], graph)
    assert card["no_office"] == offices.NO_OFFICE and card["attorney"]["name"] == "" and card["attorney"]["bar"] == ""
    assert not any(word in json.dumps(card).upper() for word in SAMPLE_WORDS)


def test_once_the_office_is_saved_the_forms_carry_it_and_the_blocker_goes(fresh_settings, demo_case):
    import offices

    office = {"firm.business_name": "EXEMPLO IMIGRACAO LAW LLC", "firm.preparer_given_name": "BEATRIZ", "firm.preparer_family_name": "EXEMPLO",
              "firm.attorney_bar_number": "000111", "firm.street": "1 EXAMPLE PLAZA", "firm.city": "BOSTON", "firm.state": "MA", "firm.zip": "02101",
              "firm.phone": "6175550100"}
    fresh_settings.PATH.write_text(json.dumps({"firm": {"values": office, "updated_by": "Ana Attorney", "updated_at": "2026-10-04T09:00:00-04:00", "history": []}}),
                                   encoding="utf-8")
    assert not fresh_settings.identity_withheld()
    assert offices.NO_OFFICE not in offices.problems(demo_case)
    letter = offices.letter({"letterhead": {"name_light": "X | ", "name_bold": "Y", "address": "", "tagline": "", "attorneys": ""}, "signer": {"name": "", "lines": []}}, demo_case)
    assert letter["letterhead"]["name_bold"] == "EXEMPLO IMIGRACAO LAW LLC" and letter["letterhead"]["name_light"] == ""


def test_with_no_office_saved_the_notices_addressee_is_unknown_and_no_takeover_is_assumed(fresh_settings):
    import journey

    rep = journey.representation([{"addressed_to": "SMITH & JONES LLP"}])
    assert rep["who"] == "unknown" and "Save the office under Settings" in rep["text"]


# -- 3. one mailing address per envelope; 4. the G-28 card waits for the approval it depends on -------------------------------------------------


@pytest.fixture
def firm(tmp_path, monkeypatch):
    """A made-up firm configured on the test's own Settings file, with its own rule approvals and ledger (as tests/test_g28_choices.py has it)."""
    import settings
    from test_g28_choices import OFFICE

    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "approved.json"))
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    settings.save("firm", OFFICE, "Test Attorney")
    return tmp_path


def _envelope_case(tmp_path, category=None):
    """A made-up case whose I-485 carries the office's address in care of the firm (as a batch-built case holds it) and the client's home on file."""
    from factgraph import FactGraph
    from test_g28_choices import FIRM_CARE_OF, HOME

    d = tmp_path / "case-ana"
    d.mkdir(exist_ok=True)
    graph = FactGraph("case-ana")
    for key, value in ({**HOME, **({"applicant.filing_category": category} if category else {})}).items():
        graph.add_source(key, "intake.pdf", "intake_questionnaire", value, value, 0.95, tier=3)
    for key, value in FIRM_CARE_OF.items():
        graph.add_source(key, "firm_profile.json", "firm_profile", value, value, 1.0)
    graph.save(d / "fact_graph.json")
    (d / "meta.json").write_text(json.dumps({"client_id": "case-ana"}), encoding="utf-8")
    return d


FORMS = ["g28", "i485", "i765"]


def test_two_mailing_addresses_in_one_envelope_hold_the_packet_until_a_person_chooses(firm, tmp_path):
    import g28
    from review.state import reviewed_graph
    from test_g28_choices import approve

    d = _envelope_case(tmp_path)
    held = g28.envelope(d, FORMS)
    assert len(held) == 1 and "The G-28 sends the client's mail to the client (12 SAMPLE STREET" in held[0]
    assert "while the I-485 and the I-765 send it to the office, in care of the firm (100 EXAMPLE WAY" in held[0] and "one envelope, two mailing addresses" in held[0]
    assert held[0].endswith("Approve the office's G-28 choices on Settings, then choose one address on the case's G-28 card.")  # unapproved: the approval first
    assert held[0] in g28.problems(d, FORMS)
    approve()
    held = g28.envelope(d, FORMS)
    assert "choose \"The client's own address\" for the other forms on the case's G-28 card, or have the G-28 carry the office's address" in held[0]
    g28.change(d, "others", "client", "The client's own address on every form.", "Jane Paralegal", "paralegal", reviewed_graph(d, g28_card=False))
    assert g28.envelope(d, FORMS) == []                                           # one address now: the client's
    assert g28.envelope(d, ["g28"]) == [] and g28.envelope(d, ["i485", "i765"]) == []  # a packet with only one side has nothing to compare


@pytest.mark.parametrize("category", ["T nonimmigrant (I-914)", "U nonimmigrant (I-918)"])
def test_for_a_t_or_u_case_the_blocker_says_the_attorney_decides(firm, tmp_path, category):
    import g28
    from test_g28_choices import approve

    approve()
    held = g28.envelope(_envelope_case(tmp_path, category), FORMS)
    assert len(held) == 1 and "For a T or U case the attorney decides which address each form carries" in held[0]
    assert "choose \"The client's own address\"" not in held[0]                    # not pushed either way


def test_the_card_cannot_be_confirmed_or_changed_while_the_office_choices_wait_and_the_ledger_says_refused(firm, tmp_path):
    import events
    import g28
    from review.state import reviewed_graph
    from test_g28_choices import approve

    d = _envelope_case(tmp_path)
    graph = reviewed_graph(d, g28_card=False)
    for attempt in (lambda: g28.confirm(d, "Jane Paralegal", "paralegal", graph),
                    lambda: g28.change(d, "mail", "physical", "anything", "Jane Paralegal", "paralegal", graph),
                    lambda: g28.change(d, "1a", True, "the client asked", "Ana Attorney", "attorney", graph)):
        with pytest.raises(PermissionError) as refused:
            attempt()
        assert str(refused.value) == g28.WAITING
    rows = [r for r in events.rows(events.base_path(tmp_path)) if r["kind"] == "decisions"]
    assert [r["action"] for r in rows] == ["refused", "refused", "refused"]
    assert rows[0]["what"] == "Refused: confirming the G-28's choices for the case (the office's G-28 choices are not approved yet)"
    card = g28.card(d, FORMS, graph)
    assert card["policy"]["waiting"] and card["policy"]["says"] == g28.WAITING and "an attorney" in g28.WAITING  # what waits, and who approves it
    approve()
    g28.confirm(d, "Jane Paralegal", "paralegal", reviewed_graph(d, g28_card=False))
    assert g28.state(d)["state"] == "confirmed"


# -- 5. the arrival card holds what it says it holds ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("client_state", ["AZ", "NJ"])
def test_item_10s_city_and_state_stay_empty_until_a_person_saves_even_when_the_clients_state_equals_the_reading(client_state):
    """Fictional example or implementation helper."""
    import arrival
    from factgraph import FactGraph
    from fill import load_field_map
    from fill.field_map import map_facts_to_fields
    from test_nta_full import SAN_LUIS, _graph

    g = _graph({"applicant.last_arrival_city": "NOGALES", "applicant.last_arrival_state": client_state, "applicant.last_arrival_date_self_reported": "2021-12-03"},
               nta=SAN_LUIS)
    assert arrival.reading(g)["state"] == "AZ"
    for key in ("applicant.last_arrival_city", "applicant.last_arrival_state"):
        assert g.get(key).status == "resolved" and g.get(key).value is None, key
    boxes = map_facts_to_fields(g, load_field_map(schema_path.path("field_map", "i485"))).values
    filled = {k.rsplit(".", 1)[-1]: v for k, v in boxes.items() if v not in (None, "")}
    assert "Pt1Line10_State[0]" not in filled and "Pt1Line10_CityTown[0]" not in filled, filled  # the form's boxes: blank until the save
    # a person saves the card: the boxes hold what they saved
    g.set_by_review("applicant.last_arrival_city", "SAN LUIS", "Paulo Paralegal")
    g.set_by_review("applicant.last_arrival_state", "AZ", "Paulo Paralegal")
    arrival.settle(g)
    assert (g.get("applicant.last_arrival_city").value, g.get("applicant.last_arrival_state").value) == ("SAN LUIS", "AZ")
    assert isinstance(g, FactGraph)


# -- 6. the smaller ones --------------------------------------------------------------------------------------------------------------------


def test_tps_for_a_country_never_designated_asks_nothing_and_offers_no_packet(world):
    import filing_questions
    from datetime import date
    from test_fifth_visit import _tps_case

    panel = filing_questions.status("tps", _tps_case(world, "BRAZIL"), date(2026, 10, 4))
    assert panel["closed"] and panel["questions"] == [] and panel["client_questions"] == []
    assert [n["title"] for n in panel["notes"]] == ["Not on USCIS's list"] and "there is nothing to file" in panel["notes"][0]["text"]


def test_shows_it_now_is_said_only_for_a_filing_the_clients_page_shows():
    import journey

    assert not journey.client_shows({"filing": "court_motion"}) and not journey.client_shows({"filing": "court_bond"})  # no wording for the client yet
    assert journey.client_shows({"filing": "n565"}) and journey.client_shows({"filing": "i485"}) and journey.client_shows({"filing": "bia"})
    server = (REPO / "src" / "review" / "server.py").read_text(encoding="utf-8")
    assert 'page = "not_shown"' in server and "journey.client_shows(record)" in server
    page = (REPO / "src" / "review" / "static" / "index.html").read_text(encoding="utf-8")
    assert "The client's page does not show this kind of filing." in page


def test_a_wording_in_the_other_voice_is_left_out_and_said_so_and_a_taken_back_approval_never_reads_approved(tmp_path, monkeypatch):
    import clock
    import part14_explain as px
    import part14_voice
    import settings
    from datetime import datetime
    from rules import approval
    from test_part14_explanations import APPROVED_I360, ARRIVED, NAME, K, entry, make_case, yes
    from test_wordings import ABSENTIA_2, I360_2, NAME2

    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "portal"))
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    first = make_case(tmp_path, NAME | ARRIVED | APPROVED_I360 | yes("in_removal_proceedings"), ABSENTIA_2)
    px.approve(first, K["in_removal_proceedings"], "Sam Attorney", "attorney")
    settings.save("firm", {part14_voice.KEY: "office"}, "Sam Attorney")              # the office now writes in its own voice
    approval.approve(part14_voice.PRACTICE_ID, "Sam Attorney", "attorney")
    second = make_case(tmp_path, NAME2 | ARRIVED | I360_2 | yes("in_removal_proceedings"), ABSENTIA_2, name="case-lima")
    e = entry(second, "in_removal_proceedings")
    assert e["firm"]["offers"] == [] and e["firm"]["left_out_voice"] == 1             # counted, as a wording the court's record contradicts is
    page = (REPO / "src" / "review" / "static" / "index.html").read_text(encoding="utf-8")
    assert "left out: written in the other voice, before the office's choice of voice changed." in page
    assert "Not approved now: the approval of ${e.approval.date} was taken back." in page   # never "Approved by" on an approval that no longer holds
    first_e = entry(first, "in_removal_proceedings")
    assert first_e["approval"] and not first_e["approval"]["holds"]


def test_a_courts_city_is_offered_as_a_place_to_keep_never_blanked_as_a_name_unasked():
    import wordings

    text = "Yes, I was placed in removal proceedings, and those proceedings are still pending before the Boston Immigration Court."
    asked = wordings.abstract(text)
    assert asked["offers"] == [{"token": "Boston Immigration Court", "type": "place", "words": "a place", "choice": "slot"}] and asked["blanked"] == []
    assert wordings.abstract(text, choices={"Boston Immigration Court": "keep"})["text"] == text      # kept as text when the attorney says so
    assert "{place_1}" in asked["text"] and "{name_" not in asked["text"]                            # a blank only as a place, never "[a name]"
    other = wordings.abstract("The case was heard by the Newark Immigration Court in 2024.")
    assert [o["type"] for o in other["offers"]] == ["place"]
    assert [o["type"] for o in wordings.abstract("I met Maria Souza in 2020.")["blanked"]] == ["name", "date"]  # a person's name is still always a blank


def test_the_products_own_approved_part_14_entries_are_not_boxes_the_office_changes():
    import audit_fill

    part14 = {"action": "set", "item": {"kind": "part14", "id": "part14:text:x"}}
    card = {"action": "set", "item": {"kind": "fact", "id": "fact:applicant.ssn"}}
    assert not audit_fill.is_save(part14) and audit_fill.is_save(card)


def test_the_try_that_locks_the_account_says_so_and_the_only_attorney_is_told_where_to_turn(tmp_path):
    from review import auth

    accounts = auth.Accounts(tmp_path / "users.json")
    accounts.add("beatriz@example.com", "Beatriz Exemplo", "attorney")
    accounts._set_password("beatriz@example.com", "a-made-up-passphrase-77", must_change=False)
    said = []
    for n in range(auth.MAX_FAILURES):
        with pytest.raises(ValueError) as wrong:
            accounts.sign_in("beatriz@example.com", f"wrong-password-{n}")
        said.append(str(wrong.value))
    assert said[:-1] == [auth.WRONG] * (auth.MAX_FAILURES - 1)
    assert said[-1].startswith("Too many wrong tries: this account is locked for 15 minutes.")       # the fifth says it locked
    assert "if you are the only attorney, the firm's IT person resets it on the server" in said[-1]
    assert "### Locked out" in (REPO / "docs" / "deployment.md").read_text(encoding="utf-8")


def test_the_clients_have_it_at_hand_list_leaves_out_a_paper_the_office_recorded_the_client_has_none_of(tmp_path, monkeypatch):
    import absence
    from fastapi.testclient import TestClient
    from portal.app import create_app
    from portal.notify import Notifier
    from portal.store import PortalStore

    monkeypatch.setenv("I485_CASES", str(tmp_path / "clients"))
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    store = PortalStore(tmp_path / "portal")
    store.add_client("case-ana", "Ana Exemplo Souza", email="ana@example.com", language="pt")
    case = tmp_path / "clients" / "case-ana"
    case.mkdir(parents=True)
    (case / "meta.json").write_text(json.dumps({"client_id": "case-ana", "classifications": {}}), encoding="utf-8")
    client = TestClient(create_app(root=tmp_path / "portal", base_url="https://portal.example", secure_cookies=False, notifier=Notifier(tmp_path / "portal" / "outbox.jsonl", env={})))
    client.get(f"/l/{store.new_link_token('case-ana')}", follow_redirects=False)
    assert client.get("/api/me").json()["office_has_none"] == []
    absence.mark(case, "passport", "never_had", "came as a small child", "Paulo Paralegal", "paralegal")
    assert client.get("/api/me").json()["office_has_none"] == ["passport"]
    page = (REPO / "src" / "portal" / "static" / "portal.html").read_text(encoding="utf-8")
    assert 'const READY_TYPES = ["passport", "birth_certificate", "work_permit", "ssn_card", null];' in page and "office_has_none" in page


def test_the_decision_log_says_why_a_mark_was_lifted_and_the_small_wording_fixes():
    page = (REPO / "src" / "review" / "static" / "index.html").read_text(encoding="utf-8")
    assert "${h.undone.why || \"the item was reopened\"}" in page                                           # an arriving passport says it lifted the mark
    assert "Choose the name every form will carry (one of the names above), or type the name the client uses now" in page  # the names card's Save
    assert "p.short.toLowerCase()" not in page                                                              # "I-94", never "i-94"
    state = (REPO / "src" / "review" / "state.py").read_text(encoding="utf-8")
    assert '"why": lifted' in state
    from fill import load_field_map
    from review.state import Catalog

    catalog = Catalog(load_field_map(schema_path.path("field_map", "i485")), TEMPLATE, [])
    assert catalog.label("applicant.last_arrival_date") == "Date of last arrival"                           # never the form's tooltip
    import settings

    labels = [f["label"] for f in settings._g28_fields()]
    assert any("the notice with the I-94" in x for x in labels) and not any("i-94" in x for x in labels)


# -- 17. the first look at an unread firm -------------------------------------------------------------------------------------------------------


def test_the_first_list_of_an_unread_2000_case_firm_answers_within_the_lists_budget_and_says_how_many_are_still_being_read(tmp_path, monkeypatch):
    """Fictional example or implementation helper."""
    sys.path.insert(0, str(REPO / "tools"))
    import make_world
    import measure_pages
    from review import roster

    manifest = make_world.build(tmp_path / "w", cases=2000, views=200, ledger=2000, restricted=300, staff=6, sources=0, log=lambda *_: None)
    for key, value in manifest["env"].items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("I485_ROSTER", str(tmp_path / "roster.json"))  # no saved copy: the firm has never been read
    monkeypatch.setenv("I485_WALK_EVERY", "600")  # as installed
    monkeypatch.delenv("I485_ROSTER_BUDGET", raising=False)
    r = roster.Roster(Path(manifest["clients"]), {}, "", None, Path(manifest["portal"]))
    started = time.perf_counter()
    r.sync()
    first = r.rows(None)
    took = time.perf_counter() - started
    assert took < measure_pages.BUDGET_LIST * 1.5, took                                 # the lists' budget (1.5 seconds), with the scale tests' slack
    assert r.still_reading() > 0 and len(first) < 2000 and r.walks == 0                  # what is read so far, and how many are still being read
    r.first_walk.join(900)
    r.sync()
    assert r.still_reading() == 0 and len(r.rows(None)) == 2000 and r.walks == 1        # every case, once
