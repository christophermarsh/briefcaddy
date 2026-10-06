"""The client has no such document (src/absence.py): the mark with who and when, Undo, the boxes that read NOT APPLICABLE on every form that has them,
the packet's gate and checklist, the list of blank boxes, the lift when the paper arrives, and the client's request closed. Made-up people only."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

import absence
import documents
import events
import packet
from synthetic_documents import process_retained_documents
from factgraph import FactGraph
from fill import load_field_map
from fill.companion import field_map_for, fill_companions, load_profile
from portal.app import create_app
from portal.engine import sync_absences
from portal.notify import Notifier
from review.state import Catalog, build_items, current_flags, load_decision_log, load_decisions, refill, reviewed_graph, save_bundle
import schema_path

_REPO = Path(__file__).resolve().parent.parent
TEMPLATE = schema_path.path("template", "i485")
FIELD_MAP = load_field_map(schema_path.path("field_map", "i485"))
CATALOG = Catalog(FIELD_MAP, TEMPLATE)
SSN = "YOUR SOCIAL SECURITY CARD\n123-45-6789\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION"
ROW_DONE = {"summary": {"name": "ANA SAMPLE", "a_number": "A099000001", "dob": "2006-01-02"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}
BOXES = {b["key"] for spec in absence.papers().values() for b in spec["boxes"]}
NA = ("NOT APPLICABLE", "N/A")


@pytest.fixture
def case(tmp_path):
    """A made-up client whose folder holds a Social Security card and nothing else: no passport, no visa, no I-94."""
    source = tmp_path / "clients" / "case-exemplo" / "source"
    source.mkdir(parents=True)
    result = process_retained_documents("case-exemplo", source, [("ssn.pdf", SSN)])
    out = tmp_path / "data" / "case-exemplo"
    save_bundle(result, out, source)
    refill(out, FIELD_MAP, TEMPLATE)
    return out


def _value(case_dir, short):
    fields = PdfReader(str(case_dir / "i485_filled.pdf")).get_fields()
    name = next(n for n in fields if n.endswith(short))
    return fields[name].get("/V")


def _ledger(case_dir):
    return [r for r in events.rows(events.base_path(), case=case_dir.name) if r["kind"] == "decisions"]


def _arrive(case_dir, kind="passport", person="applicant", **extra):
    """A paper that arrives and is classified: the document record is written, as the reader does."""
    data = documents.read(case_dir) or {"version": 1, "built": None, "documents": []}
    data["documents"].append({"id": "a1b2c3d4e5f60718", "files": [f"{kind}.pdf"], "doc_ids": [f"{kind}.pdf"], "type": kind, "person": person, **extra})
    documents.save(case_dir, data)


# --- the mark ------------------------------------------------------------------------------------------------------


def test_the_mark_is_a_decision_with_who_when_and_why(case):
    mark = absence.mark(case, "passport", "never_had", "came as a small child", "Pat Paralegal", "paralegal")
    assert mark["by"] == "Pat Paralegal" and mark["role"] == "paralegal" and mark["reason"] == "never_had" and mark["line"] == "came as a small child"
    assert re.fullmatch(r"\d\d/\d\d/\d{4}", mark["on"]) and mark["reason_text"] == "the client never had one"
    decision = load_decisions(case)["absent:passport"]
    assert decision["action"] == "absent" and decision["reviewer"] == "Pat Paralegal" and decision["at"] and "The client never had one" in decision["note"]
    row = _ledger(case)[-1]
    assert row["what"] == "Marked absent: Passport or travel document" and row["who"] == "Pat Paralegal" and "never_had" not in json.dumps(row)
    done = next(d for d in build_items(case, FIELD_MAP, TEMPLATE, CATALOG)["done"] if d["id"] == "absent:passport")
    assert done["title"] == "The client has no passport or travel document"  # the Decision log says it in words


def test_the_mark_needs_a_name_a_known_paper_and_a_reason(case):
    with pytest.raises(ValueError, match="Enter your name"):
        absence.mark(case, "passport", "lost", "", "")
    with pytest.raises(ValueError, match="which paper"):
        absence.mark(case, "unicorn", "lost", "", "Pat")
    with pytest.raises(ValueError, match="why the client"):
        absence.mark(case, "passport", "because", "", "Pat")
    assert "absent:passport" not in load_decisions(case)


def test_a_paper_that_is_in_the_folder_cannot_be_marked_absent(case):
    _arrive(case, "passport")
    with pytest.raises(ValueError, match="is in the folder"):
        absence.mark(case, "passport", "lost", "", "Pat")


def test_undo_takes_the_mark_off_and_the_boxes_go_back(case):
    absence.mark(case, "passport", "lost", "", "Pat Paralegal", "paralegal")
    refill(case, FIELD_MAP, TEMPLATE)
    assert _value(case, "Pt1Line10_PassportNum[0]") == "NOT APPLICABLE"
    absence.undo(case, "passport", "Sam Attorney", "attorney")
    refill(case, FIELD_MAP, TEMPLATE)
    assert _value(case, "Pt1Line10_PassportNum[0]") in (None, "")
    assert "absent:passport" not in load_decisions(case) and absence.marks(case) == {}
    log = load_decision_log(case)["absent:passport"]  # kept: who marked it, who took it off
    assert log["undone"]["by"] == "Sam Attorney" and [h["reviewer"] for h in log["history"]] == ["Pat Paralegal"]
    assert _ledger(case)[-1]["what"] == "Reopened: Passport or travel document"


def test_a_mark_made_again_after_an_undo_keeps_every_step(case):
    absence.mark(case, "visa", "never_had", "", "Pat")
    absence.undo(case, "visa", "Pat")
    absence.mark(case, "visa", "lost", "", "Pat")
    assert [h["note"] for h in load_decision_log(case)["absent:visa"]["history"]] == ["The client never had one", "Lost or destroyed"]
    assert absence.marks(case)["visa"]["reason"] == "lost"


# --- the boxes -----------------------------------------------------------------------------------------------------


def test_the_i485_item_10_boxes_read_not_applicable(case):
    for paper in ("passport", "visa"):
        assert absence.mark(case, paper, "never_had", "", "Pat")["paper"] == paper
    refill(case, FIELD_MAP, TEMPLATE)
    for short in ("Pt1Line10_PassportNum[0]", "Pt1Line10_Passport[0]", "Pt1Line10_ExpDate[0]", "Pt1Line10_VisaNum[0]", "Pt1Line10_NonImmDate[0]"):
        assert _value(case, short) == "NOT APPLICABLE", short
    # the I-94's boxes too: its number box holds 11 characters, so it takes the instructions' own N/A
    absence.mark(case, "i94", "lost", "", "Pat")
    refill(case, FIELD_MAP, TEMPLATE)
    assert _value(case, "P1Line12_I94[0]") == "N/A"
    for short in ("Pt1Line12_Status[0]", "Pt1Line12_Date[0]", "P1Line12_FamilyName[0]", "P1Line13_GivenName[0]"):
        assert _value(case, short) == "NOT APPLICABLE", short
    flags = current_flags(case, FIELD_MAP, TEMPLATE)[1]
    assert not [f for f in flags if f.kind == "overflow" and f.fact_key in BOXES]  # no box is left blank for being too short
    assert not [i for i in build_items(case, FIELD_MAP, TEMPLATE, CATALOG)["open"] if any(f["key"] in BOXES for f in i["facts"])]  # nothing to confirm: the mark is the decision


def test_the_mark_is_signed_by_the_person_who_made_it_and_the_facts_stay_empty(case):
    absence.mark(case, "passport", "never_had", "", "Pat Paralegal")
    graph = reviewed_graph(case)
    marker = graph.get("case.absent.passport")
    assert marker.value == "never_had" and marker.review.resolved_by == "Pat Paralegal"
    assert graph.get("applicant.travel_document_number") is None  # the placeholder is written on the form, never into a fact
    assert absence.marked_value(graph, "applicant.travel_document_number", "i485") == "NOT APPLICABLE"
    assert absence.marked_value(graph, "applicant.i94_number", "i485") is None  # another paper, not marked


def test_every_form_that_has_the_boxes_reads_not_applicable(case, tmp_path):
    for paper in ("passport", "visa", "i94"):
        absence.mark(case, paper, "never_had", "", "Pat")
    graph = reviewed_graph(case)
    profile = load_profile()
    wanted = {fid: form for fid, form in profile["forms"].items() if absence.form_keys({"forms": [fid]}) & BOXES}
    assert {"i765", "ead", "i360", "i589", "i130", "i131", "i821", "i914", "i918"} <= set(wanted)
    done = fill_companions(graph, tmp_path / "forms", {**profile, "forms": wanted})
    for fid, form in wanted.items():
        fields = PdfReader(done[fid]["path"]).get_fields()
        mapped = field_map_for(form)
        # a dropdown that lists no such option (the I-130's class of admission lists class codes) stays empty: never a code that is not true
        dropdowns = {entry.split(":")[0] for entry in done[fid]["not_an_option"]}
        assert not done[fid]["too_long"], fid
        for key in BOXES & set(mapped):
            for name in mapped[key]["fields"]:
                if name.rsplit(".", 1)[-1] in dropdowns:
                    assert fields[name].get("/V") in (None, ""), (fid, key)
                else:
                    box = next(b for spec in absence.papers().values() for b in spec["boxes"] if b["key"] == key)
                    assert fields[name].get("/V") == absence.box_value(box, fid), (fid, key)  # the I-589's I-94 box reads None, as its instructions say
    # the forms the owner named that have no such box: the N-400 and the I-131's passport (its I-94 boxes are covered above)
    assert not absence.form_keys({"forms": ["n400"]}) & BOXES
    assert not absence.form_keys({"forms": ["i131"]}) & {"applicant.travel_document_number", "applicant.travel_document_country", "applicant.visa_number"}


def test_a_box_the_case_holds_a_value_for_is_never_replaced(case):
    graph = FactGraph.load(case / "fact_graph.json")
    graph.add_source("applicant.travel_document_number", "portal questionnaire", "intake_questionnaire", "YA1234567", "YA1234567", 0.9, tier=3)
    graph.save(case / "fact_graph.json")
    graph.save(case / "fact_graph_raw.json")
    absence.mark(case, "passport", "lost", "", "Pat")  # the case holds a number: the mark never replaces it, and says so
    fact = reviewed_graph(case).get("applicant.travel_document_number")
    assert fact.value == "YA1234567"
    from assemble import consistency_findings

    said = dict(consistency_findings(reviewed_graph(case)))
    assert "applicant.travel_document_number" in said and "check which is right" in said["applicant.travel_document_number"]
    assert "holds a passport or travel document number" in said["applicant.travel_document_number"]
    # and no half-true block: with one box held, NONE of the paper's boxes read as absent
    refill(case, FIELD_MAP, TEMPLATE)
    assert _value(case, "Pt1Line10_PassportNum[0]") == "YA1234567"
    assert _value(case, "Pt1Line10_Passport[0]") in (None, "") and _value(case, "Pt1Line10_ExpDate[0]") in (None, "")


def test_the_finding_says_an_before_a_vowel(case):
    graph = FactGraph.load(case / "fact_graph.json")
    graph.add_source("applicant.i94_number", "portal questionnaire", "intake_questionnaire", "14335150685", "14335150685", 0.9, tier=3)
    graph.save(case / "fact_graph.json")
    graph.save(case / "fact_graph_raw.json")
    absence.mark(case, "i94", "lost", "", "Pat")
    from assemble import consistency_findings

    assert "holds an I-94 number (14335150685)" in dict(consistency_findings(reviewed_graph(case)))["applicant.i94_number"]


def test_the_office_will_get_it_later_writes_nothing(case):
    absence.mark(case, "passport", "office_later", "", "Pat")
    refill(case, FIELD_MAP, TEMPLATE)
    assert _value(case, "Pt1Line10_PassportNum[0]") in (None, "")  # the paper exists: not applicable would be untrue


def test_a_paper_with_no_boxes_marks_nothing_on_a_form(case):
    absence.mark(case, "birth_certificate", "cannot_obtain", "", "Pat")
    assert reviewed_graph(case).get("case.absent.birth_certificate").value == "cannot_obtain"
    assert not [k for k in reviewed_graph(case).all_facts() if k in BOXES]  # no box of any paper holds a fact


# --- the gate and the checklist -------------------------------------------------------------------------------------


def _schema(**exhibit):
    """The I-485 packet with its I-94 exhibit made required (as the Cuban Adjustment Act's is), with what the test says about it."""
    base = packet.load_schema()
    exhibits = [ex | exhibit if ex["id"] == "admission" else ex for ex in base["exhibits"]]
    return base | {"forms": ["i485"], "cover_letter": False, "index_sheet": True, "exhibits": exhibits}


def _client_with_form(tmp_path, case):
    source = tmp_path / "src"
    source.mkdir()
    meta = json.loads((case / "meta.json").read_text(encoding="utf-8"))
    meta["source_folder"] = str(source)
    (case / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return case


def test_a_required_paper_the_schema_allows_to_be_absent_passes_and_is_listed_not_available(tmp_path, case):
    d = _client_with_form(tmp_path, case)
    schema = _schema(required=True, may_be_absent={"i94": "i485_admission_if_available"})
    assert any(p.startswith("Missing: Evidence of entry") for p in packet.plan(d, ROW_DONE, schema)["problems"])
    absence.mark(d, "i94", "lost", "", "Pat Paralegal")
    p = packet.plan(d, ROW_DONE, schema)
    assert not any("Evidence of entry" in x for x in p["problems"]) and [m["id"] for m in p["missing"]] == ["i360", "birth"]
    assert p["not_available"] == [{"id": "admission", "title": "Evidence of entry: Form I-94 and admission stamp", "papers": ["Form I-94 (arrival record)"]}]
    line = next(c["text"] for c in p["checklist"] if "not available" in c["text"])
    assert line.startswith("Form I-94 (arrival record): not available: lost or destroyed. Marked by Pat Paralegal on ")
    assert "secondary evidence" in line  # what the instructions say to add instead


def test_a_paper_the_filing_cannot_do_without_stays_blocking_with_its_sentence(tmp_path, case):
    d = _client_with_form(tmp_path, case)
    schema = _schema()
    absence.mark(d, "birth_certificate", "cannot_obtain", "", "Pat")  # the I-485's birth certificate is required and no instruction line lets it go
    p = packet.plan(d, ROW_DONE, schema)
    assert any("Missing: Birth certificate" in x and "This paper cannot be marked absent: the filing needs it." in x for x in p["problems"])
    assert not p["ready"] and "birth" in [m["id"] for m in p["missing"]]
    # a paper marked absent where the schema names no line for it is the same
    absence.mark(d, "i94", "lost", "", "Pat")
    p = packet.plan(d, ROW_DONE, _schema(required=True, may_be_absent={}))
    assert any("Evidence of entry" in x and "cannot be marked absent" in x for x in p["problems"])


def test_coming_later_never_passes_a_gate(tmp_path, case):
    d = _client_with_form(tmp_path, case)
    absence.mark(d, "i94", "office_later", "", "Pat")
    p = packet.plan(d, ROW_DONE, _schema(required=True, may_be_absent={"i94": "i485_admission_if_available"}))
    assert any("Evidence of entry" in x and "marked as coming later" in x for x in p["problems"])
    assert any(c["kind"] == "missing" and "the office will get it later" in c["text"] for c in p["checklist"])


def test_the_schemas_name_the_instruction_line_every_allowed_paper_rests_on():
    lines = absence.vocab()["lines"]
    seen = {}
    for path in schema_path.glob("packet"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        for ex in [e for s in [schema, *(schema.get("variants") or {}).values()] for e in s.get("exhibits", [])]:
            for paper, line in (ex.get("may_be_absent") or {}).items():
                assert paper in absence.papers() and line in lines, (path.stem, ex["id"], paper)
                assert set(absence.papers()[paper]["doc_types"]) & set(ex["types"]), (path.stem, ex["id"], paper)
                seen[(path.stem, ex["id"], paper)] = line
    assert seen[("caa", "admission", "i94")] == "i485_admission_if_available"
    assert not any(k[2] in ("birth_certificate", "court_order", "marriage_certificate", "divorce_decree") for k in seen)  # no line lets these go
    for line in lines.values():  # each line: which form's instructions, the edition, the words
        assert line["form"] and line["says"] and line["means"]
        assert line["edition"] or line["says"].startswith("The form's own box")  # an instruction line names its edition; a box's own words need none


def test_the_ready_to_mail_check_still_goes_past_a_missing_paper_only_with_the_attorneys_reason(tmp_path, case):
    d = _client_with_form(tmp_path, case)
    p = packet.plan(d, ROW_DONE, _schema())
    assert not p["ready"]  # the gate is the packet's own problems: the mailing check refuses a draft unless the attorney gives the reason (src/prefile.py)


# --- the blank boxes ------------------------------------------------------------------------------------------------


def test_the_plan_lists_every_blank_box_with_neither_a_value_nor_a_mark(tmp_path, case):
    d = _client_with_form(tmp_path, case)
    schema = _schema()
    before = packet.plan(d, ROW_DONE, schema)
    where = {(b["form_id"], b["key"]): b for b in before["blank_boxes"]}
    passport = where[("i485", "applicant.travel_document_number")]
    assert passport["form"] == "I-485" and passport["where"] == "Part 1, Item 10" and passport["what"] == "Passport or travel document number"
    assert "record on the Documents tab that the client has none" in passport["fix"]
    assert ("i485", "applicant.mother_given_name") in where  # the case's own blanks too, by form and item
    assert any(c["kind"] == "missing" and c["text"].startswith("I-485, Part 1, Item 10: Passport or travel document number: blank") for c in before["checklist"])
    absence.mark(d, "passport", "never_had", "", "Pat")
    refill(d, FIELD_MAP, TEMPLATE)
    after = packet.plan(d, ROW_DONE, schema)
    assert ("i485", "applicant.travel_document_number") not in {(b["form_id"], b["key"]) for b in after["blank_boxes"]}
    assert ("i485", "applicant.mother_given_name") in {(b["form_id"], b["key"]) for b in after["blank_boxes"]}
    assert any(c["text"].startswith("Passport or travel document: not available: the client never had one") for c in after["checklist"])


# --- the paper arrives -----------------------------------------------------------------------------------------------


def test_a_paper_that_arrives_and_is_classified_lifts_the_mark(case):
    absence.mark(case, "passport", "lost", "", "Pat Paralegal")
    assert absence.marks(case)
    _arrive(case, "passport")
    assert absence.marks(case) == {}
    log = load_decision_log(case)["absent:passport"]
    assert log["undone"]["by"] == absence.SYSTEM and "absent:passport" not in load_decisions(case)
    row = _ledger(case)[-1]
    assert row["action"] == "lifted" and row["who"] == absence.SYSTEM and "arrived" in row["what"]
    lifted = absence.lifted_marks(case)["passport"]
    assert lifted["marked_by"] == "Pat Paralegal" and re.fullmatch(r"\d\d/\d\d/\d{4}", lifted["on"])
    listing = absence.listing(case, "i485")
    row = next(r for r in listing["papers"] if r["paper"] == "passport")
    assert row["state"] == "in_folder" and row["lifted"]["marked_by"] == "Pat Paralegal"  # the Documents tab says so
    refill(case, FIELD_MAP, TEMPLATE)
    assert _value(case, "Pt1Line10_PassportNum[0]") in (None, "")  # the boxes fill from the paper, not from the old mark


def test_a_mark_the_arrival_has_not_lifted_yet_no_longer_fills_the_boxes(case):
    absence.mark(case, "passport", "lost", "", "Pat")
    data = {"version": 1, "built": None, "documents": [{"id": "a1", "files": ["p.pdf"], "doc_ids": ["p.pdf"], "type": "passport", "person": "applicant"}]}
    (case / documents.FILE).write_text(json.dumps(data), encoding="utf-8")  # written by another program: no lift has run
    assert reviewed_graph(case).get("case.absent.passport") is None


def test_a_passport_set_to_someone_else_does_not_lift_the_mark(case):
    absence.mark(case, "passport", "never_had", "", "Pat")
    _arrive(case, "passport", person="spouse", person_set_by={"who": "Pat", "at": "2026-10-03T09:00:00-04:00"})
    assert "passport" in absence.marks(case)


# --- the client's word, and the Documents tab's list ----------------------------------------------------------------


def test_the_clients_word_is_shown_beside_the_row_and_is_never_the_mark(case):
    graph = FactGraph("case-exemplo")
    graph.add_source("questionnaire.has_i94_or_parole", "portal questionnaire", "portal", "No", "No", 0.9, tier=3)
    listing = absence.listing(case, "i485", graph)
    row = next(r for r in listing["papers"] if r["paper"] == "i94")
    assert row["client_said"] == [{"label": "Does the client have an I-94 or a parole paper?", "value": "No"}]
    assert row["state"] == "missing" and row["mark"] is None  # the client said No; nobody at the office has recorded it
    refill(case, FIELD_MAP, TEMPLATE)
    assert _value(case, "P1Line12_I94[0]") in (None, "")  # and the boxes are not touched by it


def test_the_list_says_what_each_paper_is_and_what_the_filing_needs(case):
    listing = absence.listing(case, "i485")
    rows = {r["paper"]: r for r in listing["papers"]}
    assert list(rows) == ["passport", "visa", "i94", "birth_certificate"]
    assert rows["birth_certificate"]["need"] == "needed" and rows["i94"]["need"] == "may_be_absent" and "Special immigrant juveniles" in rows["i94"]["line"]
    assert rows["passport"]["state"] == "missing" and rows["passport"]["boxes"]
    absence.mark(case, "birth_certificate", "never_had", "", "Pat")
    row = next(r for r in absence.listing(case, "i485")["papers"] if r["paper"] == "birth_certificate")
    assert row["state"] == "marked" and row["blocking"] == absence.NEEDED and row["mark"]["by"] == "Pat"
    assert [r["id"] for r in listing["reasons"]] == ["never_had", "lost", "cannot_obtain", "expired", "office_later"]


# --- the client's request is closed ---------------------------------------------------------------------------------


@pytest.fixture
def portal(tmp_path):
    app = create_app(root=tmp_path / "portal", base_url="https://portal.example", secure_cookies=False, notifier=Notifier(tmp_path / "outbox.jsonl", env={}))
    store = app.state.store
    store.add_client("case-exemplo", "Ana Clara Exemplo Souza", phone="+15550100199", email="ana@example.com", language="en")
    return TestClient(app), store


def test_the_clients_request_is_closed_with_the_offices_sentence_and_opens_again(portal, case):
    client, store = portal
    asked = store.add_request("case-exemplo", "Please send your passport.", "passport", "Pat")
    store.send_drafts("case-exemplo", "Pat")
    assert store.requests("case-exemplo")[0]["status"] == "open"
    absence.mark(case, "passport", "never_had", "", "Pat")
    assert sync_absences(store, "case-exemplo", case, "Pat") == 1
    request = store.requests("case-exemplo")[0]
    assert request["id"] == asked["id"] and request["status"] == "closed" and request["closed_by"] == "Pat" and request["closed_at"]
    task = next(t for t in store.tasks("case-exemplo") if t.get("office_has"))
    assert task["text"] == "Passport: The office has what it needs" and task["doc_id"] == "passport"
    assert task["texts"]["pt"].endswith("O escritório já tem o que precisa") and task["texts"]["es"].endswith("La oficina ya tiene lo que necesita")
    assert task["texts"]["ht"].endswith("Biwo a gen sa li bezwen an")
    # the client's own page shows it
    r = client.get(f"/l/{store.new_link_token('case-exemplo')}", follow_redirects=False)
    assert r.status_code == 303
    me = client.get("/api/me").json()
    assert not any(t.get("id") == asked["id"] for t in me["tasks"]) and any(t.get("office_has") for t in me["tasks"])
    # the mark is taken off: the request is open again and the sentence goes
    absence.undo(case, "passport", "Pat")
    sync_absences(store, "case-exemplo", case, "Pat")
    assert store.requests("case-exemplo")[0]["status"] == "open" and "closed_by_absence" not in store.requests("case-exemplo")[0]
    assert not [t for t in store.tasks("case-exemplo") if t.get("office_has")]
    rows = [r for r in events.rows(events.base_path(), case="case-exemplo") if r["kind"] == "portal"]
    assert any(r["action"] == "closed" for r in rows) and any(r["action"] == "reopened" for r in rows)  # every write a ledger row


def test_a_paper_the_questionnaire_calls_for_is_not_asked_again(portal, case):
    from portal.bank import bank_for, load_bank
    from portal.engine import client_tasks

    client, store = portal
    answers = {"has_ssn": "Yes"}  # the SSN card is asked for; the passport is offered
    asked_for = [s for s in __import__("portal.bank", fromlist=["required_documents"]).required_documents(answers, load_bank()) if "passport" in s["doc_types"]]
    assert asked_for  # offered ("if you have it")
    tasks = client_tasks(answers, [], FactGraph("case-exemplo"), "en", None, bank_for({}), None, None, [], absence.absent_types(case))
    assert not [t for t in tasks if t.get("office_has")]
    absence.mark(case, "passport", "never_had", "", "Pat")
    tasks = client_tasks(answers, [], FactGraph("case-exemplo"), "en", None, bank_for({}), None, None, [], absence.absent_types(case))
    assert [t["text"] for t in tasks if t.get("office_has")] == ["Passport: The office has what it needs"]


# --- the review app --------------------------------------------------------------------------------------------------


def test_the_review_app_marks_undoes_and_refills(tmp_path, case):
    from review.server import ReviewApp

    root = case.parent
    app = ReviewApp(root, schema_path.path("field_map", "i485"), TEMPLATE, None)
    out = app.absence_change("case-exemplo", {"action": "mark", "paper": "passport", "reason": "lost", "line": "left behind", "reviewer": "Pat Paralegal", "filing": "i485"}, "paralegal")
    row = next(r for r in out["papers"] if r["paper"] == "passport")
    assert row["state"] == "marked" and row["mark"]["line"] == "left behind"
    assert _value(case, "Pt1Line10_PassportNum[0]") == "NOT APPLICABLE"  # the form the paralegal opens next
    assert {f["id"] for f in out["filings"]} >= {"i485", "i360"}
    out = app.absence_change("case-exemplo", {"action": "undo", "paper": "passport", "reviewer": "Pat Paralegal"}, "paralegal")
    assert next(r for r in out["papers"] if r["paper"] == "passport")["state"] == "missing"
    assert _value(case, "Pt1Line10_PassportNum[0]") in (None, "")
    with pytest.raises(ValueError, match="Choose what to do"):
        app.absence_change("case-exemplo", {"action": "erase", "paper": "passport", "reviewer": "Pat"})


# --- the placeholder is written on the form and nowhere else ---------------------------------------------------------


I360 = "I-797, NOTICE OF ACTION\nReceipt Number: WAC1234567890\nU.S. CITIZENSHIP AND IMMIGRATION SERVICES\nCase Type: I360\nPriority Date: 10/14/2022\n"


def _with_facts(case_dir, **facts):
    for name in ("fact_graph.json", "fact_graph_raw.json"):
        graph = FactGraph.load(case_dir / name)
        for key, value in facts.items():
            graph.add_source(key.replace("__", "."), "portal questionnaire", "intake_questionnaire", str(value), value, 0.9, tier=3)
        graph.save(case_dir / name)


def test_a_rule_does_not_answer_part_9_from_the_placeholder(tmp_path):
    """OVERSTAY-01 reads the I-94's end date: with the I-94 marked absent the answer stays with the reviewer, as with no I-94 and no mark."""
    from rules import ALL_RULES

    source = tmp_path / "clients" / "case-exemplo" / "source"
    source.mkdir(parents=True)
    result = process_retained_documents("case-exemplo", source, [("i360.pdf", I360)], rules=ALL_RULES)
    d = tmp_path / "data" / "case-exemplo"
    save_bundle(result, d, source)
    refill(d, FIELD_MAP, TEMPLATE)
    key = "applicant.part9.violated_nonimmigrant_status"
    assert reviewed_graph(d).get(key) is None  # before the mark: left for the reviewer
    absence.mark(d, "i94", "never_had", "", "Pat")
    graph = reviewed_graph(d)
    assert graph.get(key) is None and graph.get("applicant.part9.unlawfully_present_since_1997") is None  # after it: the same
    assert graph.get("applicant.i94_admit_until_date") is None
    refill(d, FIELD_MAP, TEMPLATE)
    assert _value(d, "Pt1Line12_Date[0]") == "NOT APPLICABLE"  # and the box still reads it


@pytest.mark.parametrize("manner", ["ADMITTED", "WITHOUT ADMISSION OR PAROLE"])
def test_no_filing_reads_the_placeholder_as_a_value(case, manner):
    """The graph holds no NOT APPLICABLE or N/A for a paper's box, and what the filings derive (the asylum, bond, cancellation, TPS, asylee and
    T visa modules) is the same with the papers marked as with them missing."""
    import asylee
    import asylum
    import bond
    import cancellation
    import tps
    import t_visa
    from datetime import date

    _with_facts(case, applicant__last_arrival_manner=manner, applicant__i94_arrival_date="2019-07-15", applicant__last_arrival_city="BOSTON",
                applicant__last_arrival_state="MA")
    plain_graph = reviewed_graph(case)
    for paper in ("passport", "visa", "i94"):
        absence.mark(case, paper, "never_had", "", "Pat")
    marked_graph = reviewed_graph(case)
    assert not [k for k in BOXES if marked_graph.get(k) is not None and marked_graph.get(k).value in NA]  # grep-style: no placeholder in the graph
    assert not [k for k, f in marked_graph.all_facts().items() if f.value in NA and not k.startswith("questionnaire.")]
    today = date(2026, 10, 3)

    def derived(graph, module):
        g = FactGraph.from_dict(graph.to_dict())
        out = module.derive(g, today)
        out = out if out is not None else g
        return {k: f.value for k, f in out.all_facts().items() if not k.startswith("case.absent.")}

    for module in (asylum, bond, cancellation, tps, asylee, t_visa):
        assert derived(marked_graph, module) == derived(plain_graph, module), module.__name__
    if manner != "ADMITTED":  # an entry without inspection: the I-589 says so, instead of "NOT APPLICABLE"
        g = FactGraph.from_dict(marked_graph.to_dict())
        asylum.derive(g)
        assert g.get("asylum.entry1_status").value == "EWI"
        assert "NOT APPLICABLE" not in str(g.get("asylum.entry1_status_expires").value if g.get("asylum.entry1_status_expires") else "")


def test_the_i589_i94_box_reads_none_as_its_instructions_say(case, tmp_path):
    absence.mark(case, "i94", "never_had", "", "Pat")
    profile = load_profile()
    done = fill_companions(reviewed_graph(case), tmp_path / "f", {**profile, "forms": {"i589": profile["forms"]["i589"], "i765": profile["forms"]["i765"]}})
    mapped = field_map_for(profile["forms"]["i589"])["applicant.i94_number"]["fields"][0]
    assert PdfReader(done["i589"]["path"]).get_fields()[mapped].get("/V") == "None"
    other = field_map_for(profile["forms"]["i765"])["applicant.i94_number"]["fields"][0]
    assert PdfReader(done["i765"]["path"]).get_fields()[other].get("/V") == "N/A"
    assert "None" in absence.listing(case, "i589")["papers"][2]["boxes"][0]  # the Documents tab says so (the I-589 box)


# --- the dead ends --------------------------------------------------------------------------------------------------------


def test_an_advance_parole_alone_is_not_a_passport(case):
    """A travel document the case holds no number from does not make the passport row "in the folder": the mark is allowed, and item 10 reads it."""
    _arrive(case, "advance_parole")
    row = next(r for r in absence.listing(case, "i485")["papers"] if r["paper"] == "passport")
    assert row["state"] == "missing"
    absence.mark(case, "passport", "never_had", "", "Pat")
    refill(case, FIELD_MAP, TEMPLATE)
    assert _value(case, "Pt1Line10_PassportNum[0]") == "NOT APPLICABLE"


def test_the_office_will_get_it_later_leaves_the_clients_request_open(portal, case):
    client, store = portal
    store.add_request("case-exemplo", "Please send your passport.", "passport", "Pat")
    store.send_drafts("case-exemplo", "Pat")
    absence.mark(case, "passport", "office_later", "", "Pat")
    assert absence.absent_types(case) == set()
    assert sync_absences(store, "case-exemplo", case, "Pat") == 0
    assert store.requests("case-exemplo")[0]["status"] == "open" and not [t for t in store.tasks("case-exemplo") if t.get("office_has")]
    absence.undo(case, "passport", "Pat")
    absence.mark(case, "passport", "lost", "", "Pat")  # a real absence does close it
    assert absence.absent_types(case) == {"passport"}
    assert sync_absences(store, "case-exemplo", case, "Pat") == 1


def test_a_list_on_the_form_with_no_such_choice_is_empty_and_listed(tmp_path, case):
    """The I-130's class of admission lists class codes: NOT APPLICABLE is not a choice. The box stays empty, and the plan lists it by form and item."""
    for paper in ("passport", "visa", "i94"):
        absence.mark(case, paper, "never_had", "", "Pat")
    profile = load_profile()
    done = fill_companions(reviewed_graph(case), tmp_path / "f", {**profile, "forms": {"i130": profile["forms"]["i130"]}})
    assert "applicant.i94_class_of_admission" in done["i130"]["no_option"] and "applicant.i94_class_of_admission" not in done["i130"]["left_blank"]
    schema = {"forms": ["i130"], "exhibits": []}
    rows = absence.blank_boxes(case, schema, done)
    row = next(r for r in rows if r["key"] == "applicant.i94_class_of_admission")
    assert row["form_id"] == "i130" and row["form"] == "I-130" and "has no choice" in row["fix"]
    assert row["where"] in ("", "Part 4, Item 21") or row["where"].startswith("Part 4")  # right, or left off: never wrong


def test_a_companion_blank_is_named_by_the_forms_own_words_not_by_its_key(tmp_path, case):
    profile = load_profile()
    done = fill_companions(reviewed_graph(case), tmp_path / "f", {**profile, "forms": {"i765": profile["forms"]["i765"]}})
    rows = absence.blank_boxes(case, {"forms": ["i765"], "exhibits": []}, done)
    whats = {r["key"]: r["what"] for r in rows}
    assert whats and not [(k, w) for k, w in whats.items() if w in ("Ssn", "Dob") or "_" in w], [(k, w) for k, w in whats.items() if "_" in w]
    assert whats["applicant.mailing_street"] == "Street Number and Name"  # the form's own words, not "Mailing street" made from the key
    assert whats["applicant.travel_document_number"] == "Passport or travel document number"
    places = {r["key"]: r["where"] for r in rows}
    assert places["applicant.travel_document_number"] == "Part 2, Item 18" or places["applicant.travel_document_number"] == ""
    assert all(p == "" or re.fullmatch(r"Part \d+, Item \d+(\.[a-z])?", p) for p in places.values()), places
