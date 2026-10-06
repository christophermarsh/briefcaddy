"""The loop that finds the next wrong box (src/audit_fill.py, tools/fill_audit.py, src/rebuild.py): the same box changed the same way across cases, from filled forms
the office corrected or filled by hand and from the reviewers' Saves; the line at three cases; what a protected case's reader may see; the morning report; the
history; and "Rebuild the forms" after a release. Everyone here is made up (the demonstration client, cloned: every name EXEMPLO)."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
from pathlib import Path

import pytest

import accuracy
import accuracy_samples
import audit_fill
import compare
import events
import overnight
import packet
import rebuild
import version
from review import reports
from review.server import ReviewApp
from review.state import refill
import schema_path

REPO = Path(__file__).resolve().parent.parent
FIELD_MAP, TEMPLATE = schema_path.path("field_map", "i485"), schema_path.path("template", "i485")
CASES = ["case-a", "case-b", "case-c", "case-d"]
OTHER_NAMES = "applicant.na.other_names"  # Part 1, Item 2: NOT APPLICABLE by a firm policy unless the client has other names
ROW = {"summary": {"name": "ANA SAMPLE", "a_number": "A099000001", "dob": "2006-01-02"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}
SECRETS = ("EXEMPLO TESTE", "CAMPINAS", "SOROCABA")  # values the firm-wide view must never carry


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """The demonstration client processed by the pipeline, cloned into four cases. Copied for each test: a test writes decisions and records."""
    root = tmp_path_factory.mktemp("audit-base")
    clients = accuracy_samples._seed(root)
    for name in CASES:
        accuracy_samples._clone(clients, name)
    return clients


@pytest.fixture
def clients(base, tmp_path, monkeypatch):
    out = tmp_path / "data" / "clients"
    shutil.copytree(base, out)
    monkeypatch.setenv("I485_AUDIT_FILL", str(tmp_path / "data" / "audit_fill.json"))
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "data" / "events.jsonl"))
    monkeypatch.setenv("I485_REFERENCE", str(tmp_path / "data" / "reference"))
    monkeypatch.setenv("I485_ACCURACY_HISTORY", str(tmp_path / "data" / "accuracy_history.jsonl"))
    return out


def decision(key, action="set", values=None, reviewer="Paulo Paralegal", note="The client uses another name.", kind="fact", **extra):
    return {"action": action, "values": values or {}, "reviewer": reviewer, "at": "2026-10-02T10:00:00-04:00", "note": note,
            "item": {"id": f"{kind}:{key}", "kind": kind, "level": "review", "title": "t", "group": "g", "facts": [key]}, **extra}


def save(clients, case, log):
    (clients / case / "decisions.json").write_text(json.dumps(log), encoding="utf-8")


def other_names(case_clients, cases, value="EXEMPLO TESTE"):
    for name in cases:
        save(case_clients, name, {f"fact:{OTHER_NAMES}": decision(OTHER_NAMES, values={OTHER_NAMES: value})})


# -- the kind of change, in words ------------------------------------------------------------------------------------------------

def test_a_change_is_described_in_words_with_no_value_in_them():
    word = lambda before, after, ident: audit_fill.change_words(audit_fill.kind_of(before, after), audit_fill.noun_of(ident, before, after))  # noqa: E731
    assert word("NOT APPLICABLE", "EXEMPLO TESTE", OTHER_NAMES) == "from NOT APPLICABLE to a name"
    assert word("N/A", "ANA", "Pt1Line2_GivenName[0]") == "from NOT APPLICABLE to a name"
    assert word("SOROCABA", "CAMPINAS", "applicant.mother_birth_city") == "from a city to another city"
    assert word("SOROCABA", "CAMPINAS", "Pt1Line7_CityTownOfBirth[0]") == "from a city to another city"
    assert word("", "12 SAMPLE STREET", "applicant.physical_street") == "from blank to a street"
    assert word("MA", "", "applicant.physical_state") == "from a state to blank"
    assert word("/Y", "/N", "Pt9Line76_YesNo") == "from Yes to No" and word("No", "Yes", "x") == "from No to Yes"
    assert word("", "03/04/1982", "Pt5Line8_X") == "from blank to a date"  # no word of the box says what it holds: the value's shape does
    assert word("", "ABC", "Pt5Line8_X") == "from blank to a value" and word("ABC", "DEF", "x") == "from one value to another"
    assert word("EXEMPLO", "NOT APPLICABLE", "applicant.family_name") == "from a name to NOT APPLICABLE"


# -- the groups and the line -------------------------------------------------------------------------------------------------------

def row(case, source=audit_fill.FORM, ident="applicant.family_name", before="NOT APPLICABLE", after="SAMPLE", ref="Part 1, Item 2", label="Family name", form="i485", **extra):
    return audit_fill._row(case, source, form, ident, ref, label, before, after, **extra)


def test_the_same_box_changed_the_same_way_on_three_cases_is_one_line_and_two_is_not():
    rows = [row(c) for c in ("a", "b")] + [row("c", ident="applicant.dob", before="01/02/2005", after="02/01/2005", ref="Part 1, Item 5", label="Date of birth")]
    assert audit_fill.alerts(audit_fill.groups(rows)) == []
    rows.append(row("c"))
    got = audit_fill.alerts(audit_fill.groups(rows))
    assert [a["text"] for a in got] == ["The office changes Part 1, Item 2 (Family name) from NOT APPLICABLE to a name on 3 cases: a rule may be missing."]
    # the same box changed another way is another group; the same case twice is one case; a form and a Save for one case are one case
    rows += [row("c", before="SOUZA", after="SAMPLE"), row("a", source=audit_fill.SAVE, by="Paulo Paralegal", at="2026-10-02T10:00:00-04:00", reason="x")]
    by_kind = {g["words"]: g for g in audit_fill.groups(rows) if g["label"] == "Family name"}
    assert len(by_kind["from NOT APPLICABLE to a name"]["cases"]) == 3 and len(by_kind["from a name to another name"]["cases"]) == 1
    assert by_kind["from NOT APPLICABLE to a name"]["seen"] == [audit_fill.FORM, audit_fill.SAVE]
    assert audit_fill.THRESHOLD == 3


def test_a_change_seen_as_a_save_and_on_a_companion_form_is_one_group():
    boxes = audit_fill.Boxes()
    key = "applicant.mother_birth_city"  # on the I-130A's map and not the I-485's
    assert boxes.form_of(key) == "i130a" and boxes.form_of("applicant.family_name") == "i485" and boxes.form_of("questionnaire.nothing_here") == ""
    ref, label = boxes.name("i130a", key, "")
    from_form = audit_fill._row("a", audit_fill.FORM, "i130a", key, ref, label, "SOROCABA", "CAMPINAS", basis="reference")
    from_save = audit_fill._row("b", audit_fill.SAVE, "i130a", key, *boxes.name(boxes.form_of(key), key, ""), "SOROCABA", "CAMPINAS")
    [g] = audit_fill.groups([from_form, from_save])
    assert g["cases"] == ["a", "b"] and g["seen"] == [audit_fill.FORM, audit_fill.SAVE] and g["words"] == "from a city to another city"
    # a fact several forms hold is one box of the first form that holds it for a Save: the same fact on another form's reference is that form's box (a group of its own)
    other = audit_fill._row("c", audit_fill.FORM, "n400", "applicant.family_name", "Form N-400, Part 2", "Family name", "A", "B")
    assert len(audit_fill.groups([other, audit_fill._row("d", audit_fill.SAVE, "i485", "applicant.family_name", "Part 1, Item 1", "Family name", "A", "B")])) == 2


def test_the_a_number_printed_on_every_page_is_one_box():
    assert audit_fill._ident("", "Pt1Line4_AlienNumber[7]") == audit_fill._ident("", "Pt1Line4_AlienNumber[22]") == "Pt1Line4_AlienNumber"
    assert audit_fill._ident("applicant.a_number", "Pt1Line4_AlienNumber[3]") == "applicant.a_number"


# -- the forms: filled by hand, and corrected on screen ----------------------------------------------------------------------------

def test_a_form_the_office_corrected_is_compared_with_the_forms_the_product_alone_would_have_filled(clients, tmp_path):
    other_names(clients, CASES)
    save(clients, "case-a", {f"fact:{OTHER_NAMES}": decision(OTHER_NAMES, values={OTHER_NAMES: "EXEMPLO TESTE"}),
                             "fact:applicant.mother_birth_city": decision("applicant.mother_birth_city", values={"applicant.mother_birth_city": "CAMPINAS"})})
    found = audit_fill.audit_forms(clients, tmp_path / "work")
    assert found["figures"]["cases"] == 4 and found["figures"]["forms"] == 4 and found["figures"]["boxes"] > 1000 and found["skipped"] == []
    rows = found["rows"]
    item2 = [r for r in rows if r["ident"] == OTHER_NAMES]
    assert len(item2) == 4 and {r["case"] for r in item2} == set(CASES)  # the three boxes of Item 2 are one box: one change for each case
    assert item2[0]["before"] == "NOT APPLICABLE" and item2[0]["after"] == "EXEMPLO TESTE" and item2[0]["ref"] == "Part 1, Item 2" and item2[0]["basis"] == "review"
    g = next(g for g in audit_fill.groups(rows) if g["label"] == "Other names used")
    assert g["words"] == "from NOT APPLICABLE to a name" and len(g["cases"]) == 4
    assert audit_fill.alerts([g])[0]["text"] == "The office changes Part 1, Item 2 (Other names used) from NOT APPLICABLE to a name on 4 cases: a rule may be missing."
    # a box one case changed is not a line
    assert [a["text"] for a in audit_fill.alerts(audit_fill.groups(rows))] == [audit_fill.alerts([g])[0]["text"]]


def test_an_absence_mark_is_kept_on_both_sides_so_it_is_never_a_rule_that_may_be_missing(clients, tmp_path):
    import absence

    paper = next(p for p in absence.papers() if p not in absence.in_folder(clients / "case-a") and absence.boxes(p))
    other_names(clients, ["case-a", "case-b", "case-c"])
    for case in CASES:  # the mark on all four: three of them also have a Save, the fourth has only the mark
        absence.mark(clients / case, paper, "never_had", "the client came as a small child", "Paulo Paralegal", "paralegal")
    plain_pair = audit_fill.audit_forms(clients, tmp_path / "work")
    assert plain_pair["figures"]["cases"] == 3 and {r["case"] for r in plain_pair["rows"]} == {"case-a", "case-b", "case-c"}  # a case with only an absence mark is not audited
    assert [g["words"] for g in audit_fill.groups(plain_pair["rows"])] == ["from NOT APPLICABLE to a name"]
    assert [a["text"] for a in audit_fill.alerts(audit_fill.groups(plain_pair["rows"]))] == [
        "The office changes Part 1, Item 2 (Other names used) from NOT APPLICABLE to a name on 3 cases: a rule may be missing."]  # nothing from the boxes the mark wrote
    rows, figures = audit_fill.mine_saves(clients)
    assert figures == {"cases": 3, "saves": 3}  # the mark is not a Save
    assert audit_fill._has_saves(clients / "case-d") is False


def test_an_audit_that_stopped_goes_on_from_its_checkpoint_and_ends_with_the_same_catalog(clients, tmp_path, monkeypatch):
    other_names(clients, CASES)
    whole = audit_fill.audit_forms(clients, tmp_path / "whole")
    monkeypatch.setattr(audit_fill, "CHECKPOINT", 1)
    real, calls = accuracy.run_one, []

    def dies_on_the_third(ref, asked):
        calls.append(ref.case)
        if len(calls) == 3:
            raise KeyboardInterrupt  # the night's run was stopped
        return real(ref, asked)

    monkeypatch.setattr(accuracy, "run_one", dies_on_the_third)
    with pytest.raises(KeyboardInterrupt):
        audit_fill.run(clients, tmp_path / "work", who="Test", saves=False)
    part = audit_fill.partial_path(clients)
    assert part.exists() and stat.S_IMODE(part.stat().st_mode) == 0o600 and not audit_fill.path(clients).exists()  # nothing written to the catalog until the end
    assert json.loads(part.read_text(encoding="utf-8"))["done"] == CASES[:2]
    monkeypatch.setattr(accuracy, "run_one", real)
    calls.clear()
    monkeypatch.setattr(accuracy, "run_one", lambda ref, asked: (calls.append(ref.case), real(ref, asked))[1])
    data = audit_fill.run(clients, tmp_path / "work", who="Test", saves=False, resume=True)
    assert calls == CASES[2:] and not part.exists()  # the cases already compared were not compared again
    assert data["forms"]["forms"] == whole["figures"]["forms"] == 4 and data["forms"]["boxes"] == whole["figures"]["boxes"] and data["forms"]["changed"] == whole["figures"]["changed"]
    assert sorted((r["case"], r["ident"], r["after"]) for r in data["rows"]) == sorted((r["case"], r["ident"], r["after"]) for r in whole["rows"])
    # a checkpoint of another release is not trusted
    audit_fill._write_private(part, {"software": "1999.1.1", "done": CASES, "rows": [], "cases": [], "forms": 9, "boxes": 9, "identical": 9, "skipped": [], "at": "x"})
    calls.clear()
    assert audit_fill.run(clients, tmp_path / "work2", who="Test", saves=False, resume=True)["forms"]["forms"] == 4 and len(calls) == 4


def test_a_name_choice_is_not_a_save(clients):
    save(clients, "case-a", {"names:applicant.name_current": decision("applicant.name_current", values={"applicant.name_current": "x"}, kind="names")})
    assert audit_fill.mine_saves(clients)[1] == {"cases": 0, "saves": 0} and not audit_fill._has_saves(clients / "case-a")


def test_a_decision_taken_back_and_a_confirmation_are_not_changes(clients, tmp_path):
    other_names(clients, ["case-a", "case-b", "case-c"])
    save(clients, "case-d", {f"fact:{OTHER_NAMES}": decision(OTHER_NAMES, values={OTHER_NAMES: "EXEMPLO TESTE"}, undone={"by": "Ana", "at": "2026-10-02T11:00:00-04:00"}),
                             "fact:applicant.family_name": decision("applicant.family_name", action="confirm")})
    found = audit_fill.audit_forms(clients, tmp_path / "work")
    assert {r["case"] for r in found["rows"]} == {"case-a", "case-b", "case-c"} and found["figures"]["cases"] == 3
    assert len(audit_fill.alerts(audit_fill.groups(found["rows"]))) == 1 and "3 cases" in audit_fill.alerts(audit_fill.groups(found["rows"]))[0]["text"]


def test_a_hand_filled_reference_is_the_offices_and_a_box_marked_as_its_own_error_is_not(clients, tmp_path):
    ref_dir = Path(os.environ["I485_REFERENCE"])
    ref_dir.mkdir(parents=True)
    for case in ("case-a", "case-b", "case-c"):  # three people typed the same name over the product's NOT APPLICABLE
        accuracy_samples._edit(clients / case / "i485_filled.pdf", ref_dir / f"{case}.pdf", [("Pt1Line2_FamilyName[0]", "OUTRO", "another name")])
    found = audit_fill.audit_forms(clients, tmp_path / "work")
    changed = [r for r in found["rows"] if r["after"] == "OUTRO"]
    assert {r["case"] for r in changed} == {"case-a", "case-b", "case-c"} and all(r["basis"] == "reference" and r["before"] == "NOT APPLICABLE" for r in changed)
    assert found["figures"]["forms"] == 3
    accuracy.add_mark(ref_dir, "case-c", "i485", "Pt1Line2_FamilyName[0]", "The reference had a typing mistake.", "Ana Attorney")
    again = audit_fill.audit_forms(clients, tmp_path / "work2")
    assert {r["case"] for r in again["rows"] if r["after"] == "OUTRO"} == {"case-a", "case-b"}  # the reference's own error is not a change the office made
    assert audit_fill.alerts(audit_fill.groups(again["rows"])) == []


# -- the Saves ------------------------------------------------------------------------------------------------------------------------

def test_the_reviewers_saves_are_mined_and_never_a_change_the_product_made(clients):
    other_names(clients, ["case-a", "case-b", "case-c"])
    save(clients, "case-d", {f"fact:{OTHER_NAMES}": decision(OTHER_NAMES, values={OTHER_NAMES: "EXEMPLO TESTE"}, reviewer=""),  # no person behind it
                             "fact:applicant.mother_birth_city": decision("applicant.mother_birth_city", action="blank"),
                             "reading:applicant.nta": decision("applicant.nta", action="acknowledge")})
    rows, figures = audit_fill.mine_saves(clients)
    assert figures == {"cases": 4, "saves": 4}
    assert {r["case"] for r in rows if r["ident"] == OTHER_NAMES} == {"case-a", "case-b", "case-c"}
    blanked = next(r for r in rows if r["case"] == "case-d")
    assert blanked["kind"] == "value_to_blank" and blanked["before"] == "SOROCABA" and blanked["after"] == "" and blanked["by"] == "Paulo Paralegal"
    saved = next(r for r in rows if r["case"] == "case-a")
    assert saved["reason"] == "The client uses another name." and saved["at"].startswith("2026-10-02") and saved["source"] == audit_fill.SAVE
    assert [g["words"] for g in audit_fill.groups(rows) if len(g["cases"]) >= 3] == ["from NOT APPLICABLE to a name"]


# -- what a reader of Reports may see ---------------------------------------------------------------------------------------------------

def table(built, id_):
    return next(t for t in built["tables"] if t["id"] == id_)


def test_reports_leave_a_protected_case_out_of_every_row_count_and_line_and_show_no_value(clients, tmp_path, monkeypatch):
    other_names(clients, CASES)
    held = audit_fill.run(clients, tmp_path / "work", who="Test")
    # a second group: three cases, one of them protected, and a third group on the protected case alone
    held["rows"] += [row(c, ident="applicant.dob", before="03/14/2006", after="03/14/2005", ref="Part 1, Item 5", label="Date of birth") for c in ("case-a", "case-c", "case-d")]
    held["rows"] += [row("case-c", ident="applicant.a_number", before="", after="A099000001", ref="Part 1, Item 4", label="A-Number")]
    audit_fill.write(clients, held)
    rows = [{"id": c, "summary": {"name": c.upper()}, "stage": "review", "office": "Massachusetts", "open_items": 0, "last_activity": "2026-09-20T10:00:00+00:00"} for c in CASES]
    mine = [r for r in rows if r["id"] != "case-c"]  # the reader may not open case-c
    everyone = reports.build(rows, clients, role="attorney")
    para = reports.build(mine, clients, role="paralegal", scope={"hidden": {"case-c"}, "confidential": set()})
    one = table(everyone, "audit")["rows"]
    two = table(para, "audit")["rows"]
    assert len(one) == 3 and len(two) == 2  # the group on the protected case alone is no row for the reader who may not open it
    top_all, top_para = next(r for r in one if r["box"].endswith("Other names used")), next(r for r in two if r["box"].endswith("Other names used"))
    assert top_all["cases"] == 4 and top_para["cases"] == 3  # the protected case is not counted for someone who may not open it
    assert top_all["clients"] == ["CASE-A", "CASE-B", "CASE-C", "CASE-D"] and top_para["clients"] == ["CASE-A", "CASE-B", "CASE-D"]
    assert top_para["change"] == "from NOT APPLICABLE to a name" and top_para["form"] == "Form I-485" and top_para["box"] == "Part 1, Item 2: Other names used"
    assert not any("A-Number" in r["box"] for r in two) and any("A-Number" in r["box"] for r in one)
    # the three-case line fires only from cases the reader may open: with the protected case the date of birth is on three cases, without it on two
    dob_all, dob_para = next(r for r in one if "Date of birth" in r["box"]), next(r for r in two if "Date of birth" in r["box"])
    assert dob_all["cases"] == 3 and dob_para["cases"] == 2
    assert any("Item 5" in a["text"] for a in everyone["alerts"]) and not any("Item 5" in a["text"] for a in para["alerts"])
    assert len(everyone["alerts"]) == 2 and len(para["alerts"]) == 1 and "Item 2" in para["alerts"][0]["text"] and "on 3 cases" in para["alerts"][0]["text"]
    text = json.dumps([everyone, para])
    assert not any(s in text for s in SECRETS) and "NOT APPLICABLE to a name" in text  # the change in words, never a client's value
    assert "case-c" not in json.dumps(para).lower() and "Only the cases you may open are counted" in json.dumps(para)  # the protected case is in no row, no list and no line
    csv = reports.to_csv(table(para, "audit"))
    assert "CASE-C" not in csv and "Other names used" in csv and "EXEMPLO TESTE" not in csv and "A-Number" not in csv
    # the page says so when nothing has been counted
    monkeypatch.setenv("I485_AUDIT_FILL", str(tmp_path / "never-run.json"))
    empty = reports.build(rows, clients, role="attorney")
    assert table(empty, "audit")["rows"] == [] and "Not counted yet" in table(empty, "audit")["about"] and empty["alerts"] == []


def test_the_values_are_inside_the_case_only_and_a_private_number_is_masked(clients, tmp_path):
    other_names(clients, CASES)
    save(clients, "case-a", {f"fact:{OTHER_NAMES}": decision(OTHER_NAMES, values={OTHER_NAMES: "EXEMPLO TESTE"}),
                             "fact:applicant.ssn": decision("applicant.ssn", values={"applicant.ssn": "123456789"})})
    audit_fill.run(clients, tmp_path / "work", who="Test")
    app = ReviewApp(clients, FIELD_MAP, TEMPLATE, None)
    mine = app.audit_case("case-a")["rows"]
    item2 = next(r for r in mine if r["box"].endswith("Other names used"))
    assert item2["before"] == "NOT APPLICABLE" and item2["after"] == "EXEMPLO TESTE" and item2["others"] == 3 and "Saves" in item2["seen"] and item2["by"] == "Paulo Paralegal"
    assert "ident" not in item2
    assert "123456789" not in json.dumps(mine)  # a number the screen never prints in full
    with pytest.raises(LookupError):
        app.audit_case("nobody-here")


# -- the catalog on disk, the ledger, the morning report ---------------------------------------------------------------------------------

def test_the_catalog_is_owner_only_and_never_exported_and_every_write_is_a_ledger_row(clients, tmp_path):
    import records

    other_names(clients, CASES)
    audit_fill.run(clients, tmp_path / "work", who="Ana Attorney")
    target = audit_fill.path(clients)
    assert target.name == "audit_fill.json" and stat.S_IMODE(target.stat().st_mode) == 0o600
    assert records.coverage("firm", "audit_fill.json") is not None and "audit_fill.json" in records.NEVER_PATTERNS["firm"]
    assert not records.by_id("audit_fill")["exported"]
    ledger = [r for r in events.rows(os.environ["I485_EVENTS"]) if r["kind"] == "upkeep"]
    assert ledger[-1]["action"] == "audited" and ledger[-1]["who"] == "Ana Attorney" and ledger[-1]["via"] == "tool" and ledger[-1]["case"] is None
    assert not any(s in ledger[-1]["what"] for s in SECRETS)
    audit_fill.nightly(clients)
    assert [r for r in events.rows(os.environ["I485_EVENTS"]) if r["kind"] == "upkeep"][-1]["action"] == "mined"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_the_nightly_run_counts_the_saves_keeps_the_forms_half_and_writes_the_morning_line(clients, tmp_path):
    other_names(clients, CASES)
    first = audit_fill.run(clients, tmp_path / "work", who="Test", saves=False)
    assert first["saves"] is None and all(r["source"] == audit_fill.FORM for r in first["rows"])
    text = audit_fill.nightly(clients)
    assert text.splitlines()[0] == ("Boxes the office changes: 4 Saves on 4 cases; 1 box changed the same way on 3 or more cases (Reports, Boxes the office changes).")
    assert text.splitlines()[1] == "The office changes Part 1, Item 2 (Other names used) from NOT APPLICABLE to a name on 4 cases: a rule may be missing."
    assert len(text.splitlines()) == 3 and text.splitlines()[2] == audit_fill.PROTECTED_NOTE and not any(s in text for s in SECRETS)
    kept = audit_fill.read(clients)
    assert {r["source"] for r in kept["rows"]} == {audit_fill.FORM, audit_fill.SAVE} and kept["forms"]["day"] and kept["saves"]["saves"] == 4
    other = tmp_path / "quiet"
    shutil.copytree(clients, other)
    for case in CASES:
        (other / case / "decisions.json").unlink()
    os.environ["I485_AUDIT_FILL"] = str(tmp_path / "quiet-audit.json")
    assert audit_fill.nightly(other) == "Boxes the office changes: 0 Saves on 0 cases; no box is changed the same way on 3 or more cases.\n" + audit_fill.PROTECTED_NOTE


def test_the_morning_report_counts_only_the_cases_that_are_not_protected_and_says_so(clients, tmp_path):
    import restricted

    other_names(clients, CASES)
    restricted.mark(clients / "case-c", True, "A made-up protected case", "Sam Attorney", "attorney")
    text = audit_fill.nightly(clients)
    lines = text.splitlines()
    assert lines[0] == "Boxes the office changes: 3 Saves on 3 cases; 1 box changed the same way on 3 or more cases (Reports, Boxes the office changes)."
    assert lines[1] == "The office changes Part 1, Item 2 (Other names used) from NOT APPLICABLE to a name on 3 cases: a rule may be missing."
    assert lines[-1] == audit_fill.PROTECTED_NOTE and "case-c" not in text.lower() and not any(s in text for s in SECRETS)
    # the catalog keeps every case, so the people who may open the protected one see it on its own page
    assert {r["case"] for r in audit_fill.read(clients)["rows"]} == set(CASES)
    app = ReviewApp(clients, FIELD_MAP, TEMPLATE, None)
    assert next(r for r in app.audit_case("case-c")["rows"] if r["box"].endswith("Other names used"))["others"] == 3  # the other three, which are not protected
    assert next(r for r in app.audit_case("case-a")["rows"] if r["box"].endswith("Other names used"))["others"] == 2  # not the protected one
    # two ordinary cases and a protected one make no line in the morning report
    for case in ("case-b", "case-d"):
        (clients / case / "decisions.json").unlink()
    assert "no box is changed the same way" in audit_fill.nightly(clients).splitlines()[0]


def test_the_overnight_run_puts_the_line_in_the_morning_report(clients, tmp_path, monkeypatch):
    other_names(clients, CASES)
    monkeypatch.setenv("I485_AUDIT", "1")
    (tmp_path / "src-clients" / "case-a" / "source").mkdir(parents=True)
    (tmp_path / "src-clients" / "case-a" / "source" / "x.pdf").write_bytes(b"%PDF-1.4")
    data = tmp_path / "data"
    monkeypatch.setattr(overnight, "journeys", lambda *a, **k: "")
    monkeypatch.setattr(overnight, "search_index", lambda *a, **k: "")
    monkeypatch.setattr(overnight, "query_layer", lambda *a, **k: "")
    monkeypatch.setenv("I485_ACCURACY", "0")
    monkeypatch.setenv("I485_CASE_STATUS", "0")
    monkeypatch.setenv("I485_CLIO", "0")
    monkeypatch.setenv("I485_STAFF_REMINDERS", "0")
    monkeypatch.setenv("I485_CLIENT_REMINDERS", "0")
    monkeypatch.setenv("I485_NOTICE_INBOX", "0")
    overnight.run(tmp_path / "src-clients", clients, data, runner=lambda name, source, out: {"status": "done", "client": name, "counts": {"blocking": 0, "review": 0}, "seconds": 0.1, "errors": {}},
                  log=lambda *_: None)
    report = (data / "batch_report.txt").read_text(encoding="utf-8")
    assert "  Boxes the office changes: 4 Saves on 4 cases; 1 box changed the same way on 3 or more cases" in report
    assert "  The office changes Part 1, Item 2 (Other names used) from NOT APPLICABLE to a name on 4 cases: a rule may be missing." in report
    assert not any(s in report for s in SECRETS) and "case-a" not in report.split("Boxes the office changes")[1]
    monkeypatch.setenv("I485_AUDIT", "0")
    assert overnight.audit_night(clients) == "Boxes the office changes: switched off."


# -- the history ---------------------------------------------------------------------------------------------------------------------

def _night(day, boxes, changed, version_="2026.10.9", audit_day=None, groups=0, forms=3):
    return {"day": day, "at": day + "T03:00:00-04:00", "version": version_, "set": "samples", "references": 4, "cases": 3, "forms": {},
            "boxes": boxes, "identical": boxes - changed, "different": changed, "unfilled": 0,
            "audit": {"forms_day": audit_day or day, "boxes": boxes, "changed": changed, "forms": forms, "groups": groups, "cases": forms, "saves": 0, "save_cases": 0}}


def test_the_audits_counts_go_into_the_history_so_a_drift_shows(clients, tmp_path):
    other_names(clients, CASES)
    audit_fill.run(clients, tmp_path / "work", who="Test")
    counts = accuracy.audit_counts(clients)
    assert counts["forms"] == 4 and counts["changed"] == 4 and counts["groups"] == 1 and counts["boxes"] == audit_fill.read(clients)["forms"]["boxes"]
    record = accuracy.history_record({"at": "2026-10-03T03:00:00-04:00", "day": "2026-10-03", "version": version.VERSION, "samples": {"total": {"boxes": 0, "identical": 0, "different": 0, "unfilled": 0},
                                      "references": 0, "cases": 0, "forms": {}}}, "samples", counts)
    assert record["audit"] == counts and "audit" not in accuracy.history_record({"at": "x", "day": "x", "version": "x", "samples": {
        "total": {"boxes": 0, "identical": 0, "different": 0, "unfilled": 0}, "references": 0, "cases": 0, "forms": {}}}, "samples")
    history = [_night("2026-09-01", 5000, 100, "2026.10.8", forms=3), _night("2026-10-01", 5200, 400, "2026.10.9", groups=2, forms=4)]
    assert accuracy.audit_line(history) == ("Boxes the office changed 8% of 5,200 on 10/01/2026, 2% of 5,000 on 09/01/2026, on 4 forms the office filled or corrected; "
                                            "2 boxes changed the same way on 3 or more cases.")
    assert accuracy.audit_rises(history) == ["Boxes the office changed rose from 2% of 5,000 to 8% of 5,200 after the 2026.10.9 update: a person should look."]
    assert accuracy.audit_rises([_night("2026-09-01", 5000, 100), _night("2026-10-01", 5000, 150)]) == []  # a rise of one point is not flagged
    assert accuracy.audit_line([{"day": "2026-10-01", "boxes": 5, "identical": 5}]) == "" and accuracy.audit_rises(history[:1]) == []
    # one audit counted on many nights is one audit
    assert accuracy.audit_line([_night("2026-10-01", 5200, 400), _night("2026-10-02", 5200, 400, audit_day="2026-10-01")]).count("on 10/01/2026") == 1
    # and the Accuracy record's screen says it, with a rise among its sentences
    accuracy.append_history(accuracy.history_path(clients), history[0])
    accuracy.append_history(accuracy.history_path(clients), history[1])
    shown = accuracy.screen(clients)
    assert shown["audit"].startswith("Boxes the office changed 8% of 5,200 on 10/01/2026") and any("rose from 2% of 5,000 to 8% of 5,200" in d for d in shown["drops"])
    assert "—" not in shown["audit"] and " -- " not in shown["audit"]


# -- rebuild this case ------------------------------------------------------------------------------------------------------------------

def _old_case(clients, monkeypatch):
    """A case processed and its packet built under an earlier release that did not have the overstay rule: the rules it was processed with are recorded in the case."""
    d = clients / "case-a"
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    meta["derivation"]["rules"] = [r for r in meta["derivation"]["rules"] if r != "OVERSTAY-01"]
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(version, "VERSION", "2026.10.9")
    refill(d, __import__("fill").load_field_map(FIELD_MAP), TEMPLATE)
    packet.build(d, ROW, "Paulo Paralegal", packet.load_filing("i485"))
    monkeypatch.setattr(version, "VERSION", "2026.10.10")  # a release has come since
    return d


def test_after_a_release_the_case_offers_a_rebuild_and_shows_the_boxes_that_changed_old_and_new_with_the_reason(clients, monkeypatch):
    d = _old_case(clients, monkeypatch)
    app = ReviewApp(clients, FIELD_MAP, TEMPLATE, None)
    manifest = json.loads((d / "packet.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "2026.10.9"
    card = app.rebuild_card("case-a", "i485")
    assert card["stale"] and card["offer"] and card["built_version"] == "2026.10.9" and card["version"] == "2026.10.10" and card["pending"] is None
    schema = packet.for_case(packet.load_filing("i485"), d)
    before = rebuild.packet_boxes(d, schema, json.loads((d / "packet.json").read_text(encoding="utf-8"))["forms"])  # what the packet holds, from the packet's own file
    assert before["i485"]["Pt1Line1_FamilyName[0]"] == compare.read_fields(d / "i485_filled.pdf")["Pt1Line1_FamilyName[0]"][0]
    assert "g28" in before and before["g28"] and not (d / rebuild.PREVIOUS).exists()
    kept = {n: (d / n).read_bytes() for n in ("i485_filled.pdf", "packet.pdf", "packet.json", "meta.json", "fact_graph.json")}

    card = app.rebuild_change("case-a", {"filing": "i485", "action": "start", "reviewer": "Paulo Paralegal"})
    assert not card["stale"] and not card["offer"] and card["built_version"] == "2026.10.10"
    pending = card["pending"]
    assert pending["by"] == "Paulo Paralegal" and pending["from_version"] == "2026.10.9" and pending["to_version"] == "2026.10.10" and pending["confirmed"] is None
    item74 = next(c for c in pending["changes"] if "Item 74" in c["box"] or "Item 13" in c["box"] or "unlawful" in c["box"].lower())
    assert {c["old"] for c in pending["changes"]} <= {"Yes", "No", "Left blank"} and {c["new"] for c in pending["changes"]} <= {"Yes", "No", "Left blank"}
    assert len(pending["changes"]) == 2 and all(c["form"] == "Form I-485" and c["why"] == "rule" for c in pending["changes"])
    assert all(c["reason"].startswith("A rule new in this release: the overstay rule") for c in pending["changes"]) and item74["old"] != item74["new"]
    assert all("key" not in c for c in pending["changes"]) and "—" not in json.dumps(card) and " -- " not in json.dumps(card)
    assert json.loads((d / "packet.json").read_text(encoding="utf-8"))["version"] == "2026.10.10"
    # the rules the case is worked out with are this release's now
    assert "OVERSTAY-01" in json.loads((d / "meta.json").read_text(encoding="utf-8"))["derivation"]["rules"]
    # the packet is a draft again until a person confirms; a second rebuild waits for it
    plan = packet.plan(d, ROW, packet.load_filing("i485"))
    assert rebuild.UNCONFIRMED in plan["problems"] and not plan["ready"] and json.loads((d / "packet.json").read_text(encoding="utf-8"))["draft"]
    assert app.items("case-a")["g28_stale"][-1].startswith("The rebuilt forms for ") and rebuild.notes(d)
    # the previous forms are kept, byte for byte, until a person confirms; the card says which forms it shows
    assert card["previous"] and "The forms and the packet shown are the rebuilt ones." in card["shows"]
    assert all((d / rebuild.PREVIOUS / f"i485.{n}").read_bytes() == b for n, b in kept.items())
    assert stat.S_IMODE((d / rebuild.PREVIOUS).stat().st_mode) == 0o700  # owner-only, as records.py and the data statement say
    assert {stat.S_IMODE(p.stat().st_mode) for p in (d / rebuild.PREVIOUS).iterdir()} == {0o600}
    assert (d / "i485_filled.pdf").read_bytes() != kept["i485_filled.pdf"] and not (d / "decisions.json").exists()  # the rebuild writes no decision
    with pytest.raises(ValueError, match="waiting for a person to confirm"):
        app.rebuild_change("case-a", {"filing": "i485", "action": "start", "reviewer": "Paulo Paralegal"})
    with pytest.raises(ValueError, match="Enter your name"):
        app.rebuild_change("case-a", {"filing": "i485", "action": "confirm", "reviewer": ""})

    card = app.rebuild_change("case-a", {"filing": "i485", "action": "confirm", "reviewer": "Ana Attorney"})
    assert card["pending"]["confirmed"]["by"] == "Ana Attorney" and card["pending"]["changes"] == pending["changes"]
    assert rebuild.UNCONFIRMED not in packet.plan(d, ROW, packet.load_filing("i485"))["problems"] and rebuild.notes(d) == []
    assert not (d / rebuild.PREVIOUS).exists() and not card["previous"]  # confirmed: the copies are dropped
    assert [h["action"] for h in card["history"]] == ["rebuilt", "confirmed"] and card["history"][0]["changed"] == 2
    with pytest.raises(ValueError, match="nothing waiting"):
        app.rebuild_change("case-a", {"filing": "i485", "action": "confirm", "reviewer": "Ana Attorney"})
    with pytest.raises(ValueError, match="Choose what to do"):
        app.rebuild_change("case-a", {"filing": "i485", "action": "other", "reviewer": "Ana Attorney"})
    said = [r for r in events.rows(os.environ["I485_EVENTS"], case="case-a") if r["kind"] == "packet" and r["action"] in ("rebuilt", "confirmed")]
    assert [r["action"] for r in said] == ["rebuilt", "confirmed"] and said[0]["who"] == "Paulo Paralegal" and said[1]["who"] == "Ana Attorney"
    assert "2 boxes changed" in said[0]["what"] and "2026.10.10" in said[0]["what"]


def test_a_rebuild_that_changes_nothing_says_so_and_asks_for_nothing(clients, monkeypatch):
    d = clients / "case-b"
    monkeypatch.setattr(version, "VERSION", "2026.10.9")
    refill(d, __import__("fill").load_field_map(FIELD_MAP), TEMPLATE)
    packet.build(d, ROW, "Paulo Paralegal", packet.load_filing("i485"))
    monkeypatch.setattr(version, "VERSION", "2026.10.10")
    app = ReviewApp(clients, FIELD_MAP, TEMPLATE, None)
    assert app.rebuild_card("case-b", "i485")["offer"]
    card = app.rebuild_change("case-b", {"filing": "i485", "action": "start", "reviewer": "Paulo Paralegal"})
    assert card["pending"] is None and not card["offer"] and card["history"][-1]["changed"] == 0
    assert rebuild.problems(d, "i485") == [] and packet.plan(d, ROW, packet.load_filing("i485"))["problems"].count(rebuild.UNCONFIRMED) == 0
    assert app.rebuild_card("case-b", "i485")["stale"] is False


def test_a_packet_built_before_releases_were_recorded_is_offered_a_rebuild_and_a_case_with_no_packet_has_no_card(clients):
    app = ReviewApp(clients, FIELD_MAP, TEMPLATE, None)
    assert app.rebuild_card("case-c", "i485") is None
    with pytest.raises(ValueError, match="Build the packet first"):
        app.rebuild_change("case-c", {"filing": "i485", "action": "start", "reviewer": "Paulo Paralegal"})
    d = clients / "case-c"
    refill(d, __import__("fill").load_field_map(FIELD_MAP), TEMPLATE)
    packet.build(d, ROW, "Paulo Paralegal", packet.load_filing("i485"))
    manifest = json.loads((d / "packet.json").read_text(encoding="utf-8"))
    del manifest["version"]
    (d / "packet.json").write_text(json.dumps(manifest), encoding="utf-8")
    card = app.rebuild_card("case-c", "i485")
    assert card["stale"] and card["offer"] and card["built_version"] == ""


def test_keep_the_previous_forms_puts_every_file_back_and_the_rebuild_can_be_asked_again(clients, monkeypatch):
    d = _old_case(clients, monkeypatch)
    app = ReviewApp(clients, FIELD_MAP, TEMPLATE, None)
    names = ("i485_filled.pdf", "packet.pdf", "packet.json", "meta.json", "fact_graph.json", "fact_graph_reviewed.json", "flag_report.txt", "companions.json")
    before = {n: (d / n).read_bytes() for n in names}
    with pytest.raises(ValueError, match="nothing to put back"):
        app.rebuild_change("case-a", {"filing": "i485", "action": "keep", "reviewer": "Paulo Paralegal"})
    app.rebuild_change("case-a", {"filing": "i485", "action": "start", "reviewer": "Paulo Paralegal"})
    assert (d / "meta.json").read_bytes() != before["meta.json"]
    card = app.rebuild_change("case-a", {"filing": "i485", "action": "keep", "reviewer": "Ana Attorney"})
    assert {n: (d / n).read_bytes() for n in names} == before and not (d / rebuild.PREVIOUS).exists()  # everything as it was, the copies dropped
    assert card["pending"] is None and card["stale"] and card["offer"] and not card["previous"]
    assert [h["action"] for h in card["history"]] == ["rebuilt", "kept_previous"] and card["history"][1]["by"] == "Ana Attorney"
    assert rebuild.problems(d, "i485") == [] and rebuild._read(d)["rules_added"] == []
    said = [r for r in events.rows(os.environ["I485_EVENTS"], case="case-a") if r["kind"] == "packet" and r["action"] == "kept_previous"]
    assert len(said) == 1 and said[0]["who"] == "Ana Attorney" and "was put back" in said[0]["what"]
    again = app.rebuild_change("case-a", {"filing": "i485", "action": "start", "reviewer": "Paulo Paralegal"})  # offered again, and the reasons still say the rule is new
    assert len(again["pending"]["changes"]) == 2 and all(c["reason"].startswith("A rule new in this release") for c in again["pending"]["changes"])
    with pytest.raises(ValueError, match="waiting for a person to confirm"):
        app.rebuild_change("case-a", {"filing": "i485", "action": "start", "reviewer": "Paulo Paralegal"})


def test_a_failed_rebuild_leaves_nothing_changed_and_a_retry_still_says_which_rule_is_new(clients, monkeypatch):
    d = _old_case(clients, monkeypatch)
    names = ("i485_filled.pdf", "packet.pdf", "packet.json", "meta.json", "fact_graph.json", "fact_graph_reviewed.json", "flag_report.txt", "companions.json")
    before = {n: (d / n).read_bytes() for n in names}
    schema = packet.for_case(packet.load_filing("i485"), d)

    def broken():
        refill(d, __import__("fill").load_field_map(FIELD_MAP), TEMPLATE)  # the forms were already written when it failed
        raise RuntimeError("the form could not be filled")

    with pytest.raises(RuntimeError):
        rebuild.start(d, "i485", schema, "Paulo Paralegal", None, broken, audit_fill.Boxes())
    assert rebuild.problems(d, "i485") == [] and rebuild._read(d)["filings"]["i485"]["pending"] is None
    assert {n: (d / n).read_bytes() for n in names} == before and not (d / rebuild.PREVIOUS).exists()  # the rule list too: meta.json is as it was
    app = ReviewApp(clients, FIELD_MAP, TEMPLATE, None)
    card = app.rebuild_change("case-a", {"filing": "i485", "action": "start", "reviewer": "Paulo Paralegal"})
    assert all(c["reason"].startswith("A rule new in this release: the overstay rule") for c in card["pending"]["changes"])


def test_a_second_filings_rebuild_still_knows_which_rules_were_new_since_its_packet_was_built():
    data = {"rules_added": [{"filing": "i485", "to_version": "2026.10.10", "rules": ["OVERSTAY-01"]}, {"filing": "i485", "to_version": "2026.11.2", "rules": ["NTA-01"]}]}
    assert rebuild.added_since(data, "2026.10.9", set()) == {"OVERSTAY-01", "NTA-01"}
    assert rebuild.added_since(data, "2026.10.10", set()) == {"NTA-01"}  # a packet built with the release that brought it: not new to it
    assert rebuild.added_since(data, "", {"X"}) == {"X", "OVERSTAY-01", "NTA-01"} and rebuild.added_since({}, "2026.10.9", set()) == set()
    assert rebuild._vkey("2026.10.10") > rebuild._vkey("2026.10.9") and rebuild._vkey("") == ()


def test_the_reasons_name_what_changed_in_plain_words():
    old = {"a": {"value": "No", "status": "resolved", "derived_by": None, "sources": ["x"]}, "b": {"value": None, "status": "conflict", "derived_by": None, "sources": ["x", "y"]},
           "c": {"value": "1", "status": "resolved", "derived_by": None, "sources": ["x"]}, "d": {"value": "1", "status": "resolved", "derived_by": None, "sources": ["x"]}}
    new = {"a": {"value": "Yes", "status": "resolved", "derived_by": "OVERSTAY-01", "sources": []}, "b": {"value": "Z", "status": "resolved", "derived_by": None, "sources": ["x", "y"]},
           "c": {"value": "1", "status": "resolved", "derived_by": None, "sources": ["z"]}, "d": {"value": "2", "status": "resolved", "derived_by": None, "sources": ["x"]}}
    assert rebuild.reason("a", old, new, {"OVERSTAY-01"})[0] == "rule" and rebuild.reason("a", old, new, {"OVERSTAY-01"})[1].startswith("A rule new in this release: the overstay rule")
    assert rebuild.reason("a", old, new, set())[1].startswith("A rule gives a different answer now: the overstay rule")
    assert rebuild.reason("b", old, new, set()) == ("settled", "A value that was open is settled now.")
    assert rebuild.reason("c", old, new, set()) == ("document", "A document is read another way.")
    assert rebuild.reason("d", old, new, set()) == ("fact", "A fact the box is built from changed.")
    assert rebuild.reason("", old, new, set())[0] == "written" and rebuild.reason("zzz", old, new, set())[0] == "written"
    for text in (rebuild.UNCONFIRMED, rebuild.reason("b", old, new, set())[1]):
        assert "—" not in text and " -- " not in text and not re.search(r"\.(py|json)\b", text)


def test_the_boxes_the_rebuild_compares_are_the_forms_own(clients):
    d = clients / "case-a"
    got = rebuild.box_values(d, [{"id": "i485", "file": "i485_filled.pdf"}, {"id": "gone", "file": "none.pdf"}, {"id": "note", "file": "flag_report.txt"}])
    assert set(got) == {"i485"} and got["i485"]["Pt1Line1_FamilyName[0]"] == compare.read_fields(d / "i485_filled.pdf")["Pt1Line1_FamilyName[0]"][0]
    assert all(v for v in got["i485"].values())
