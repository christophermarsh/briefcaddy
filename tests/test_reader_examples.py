"""The firm's reader examples (brief M1, src/reader_examples.py): a confirm or a correction of a value a reader read writes one labelled example, as a
by-product of the decision; the examples are case data behind the case's gate, go with the case at its end, and are counted per reader and field on the
accuracy page and in the morning report, numbers only. Made-up people only (the Exemplo family)."""

from __future__ import annotations

import argparse
import json
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from synthetic_documents import process_retained_documents

import accuracy
import clock
import critical_review
import engagement
import overnight
import reader_examples
import reader_manifest
import records
import restricted
import schema_path
import settings
import subject_attribution
from factgraph import FactGraph
from review import reports
from review.state import record_decision, save_bundle, undo_decision

REPO = Path(__file__).resolve().parents[1]
sys.path.append(str(REPO / "tools"))
import export_firm

DOB_READ, DOB_TYPED = "14 MAR 2006", "2006-03-15"
CITY = "SOROCABA"
PHONE = "5085550147"  # typed by the client on the portal: never an example
SECRETS = (DOB_READ, "2006-03-14", DOB_TYPED, CITY, "Sorocaba", PHONE, "SOUZA", "passport-ana.pdf", "questionario-ana.pdf")
JANE = {"email": "jane@firm.example", "name": "Jane Paralegal", "role": "paralegal"}


def make_case(clients: Path, case: str = "ana-exemplo") -> Path:
    """A processed case: a passport read (date of birth, family name), the paper questionnaire's scan read by the handwriting reader (the city of
    birth, with its place on the page), and the client's phone typed on the portal."""
    d = clients / case
    d.mkdir(parents=True)
    g = FactGraph(case)
    g.add_source("applicant.date_of_birth", "passport-ana.pdf", "passport", DOB_READ, "2006-03-14", 0.91, page=0)
    g.add_source("applicant.family_name", "passport-ana.pdf", "passport", "SOUZA", "SOUZA", 0.95, page=0)
    g.add_source("applicant.birth_city", "questionario-ana.pdf", "intake_questionnaire", "Sorocaba", CITY, 0.8, tier=3)
    g.add_source("applicant.phone", "portal questionnaire", "intake_questionnaire", PHONE, PHONE, 1.0, tier=3)
    g.add_source("applicant.firm_name", "firm profile", "firm_profile", "Exemplo Law", "EXEMPLO LAW", 1.0)
    # Simulated reader edges above keep this fixture's original assertion data.
    # Retain real fictional PDF bytes and use current production boundary/role
    # bindings plus an explicit staff association; no preview/identity guard is
    # replaced. This is not an OCR accuracy fixture.
    source=d/'source'
    passport='PASSPORT\nFICTIONAL ANA EXEMPLO\nSurname SOUZA\nDate of birth 14 MAR 2006'
    paper='INTAKE QUESTIONNAIRE\nFICTIONAL ANA EXEMPLO\nBirth city Sorocaba\nPage 2 of 2'
    result=process_retained_documents(case,source,[('passport-ana.pdf',passport),('questionario-ana.pdf',paper)],
        pages={'questionario-ana.pdf':['INTAKE QUESTIONNAIRE\nFictional cover page\nPage 1 of 2',paper]})
    instances={alias:part for plan in result.boundary_plans.values() for part in plan['instances'] for alias in part['doc_ids']}
    for key,fact in g.all_facts().items():
        for src in fact.sources:
            if src.doc_id not in instances:continue
            field=SimpleNamespace(fact_key=key,raw_value=src.raw_value,normalized_value=src.normalized_value,page=src.page)
            binding=subject_attribution.provenance(instances[src.doc_id],src.doc_type,field)
            for name,value in binding.items():setattr(src,name,value)
            src.read_manifest=reader_manifest.current(src.doc_type)
    result.graph=g
    save_bundle(result,d,source)
    data=__import__('documents').read(d)
    person=data['case_subjects']['people'][0]['id']
    for row in subject_attribution.views(d):
        if row['facts']:
            subject_attribution.assign(d,row['instance_id'],row['fingerprint'],
                {role:person for role in row['roles']},'Fictional fixture reviewer','paralegal')
    evidence = {"questionario-ana.pdf": {"birth_city": {"kind": "text", "facts": {"city": "applicant.birth_city"}, "page": 1, "box": [120, 840, 1500, 910],
                                                        "status": "ok", "values": {"city": CITY}}}}
    (d / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    return d


def item(key: str, kind: str = "text") -> dict:
    return {"id": f"fact:{key}", "kind": "fact", "level": "review", "title": "A made-up card", "group": "g", "actions": ["confirm", "set", "blank"],
            "facts": [{"key": key, "input": {"type": kind}}]}


def decide(d: Path, key: str, action: str = "confirm", value=None, kind: str = "text", who: str = "Jane Paralegal"):
    body = {"action": action, "reviewer": who, "role": "paralegal", "note": ""}
    if value is not None:
        body["values"] = {key: value}
    current=critical_review.context(d)
    body['evidence_fingerprints']={key:current[key]['fingerprint']} if key in current else {}
    return record_decision(d, item(key, kind), body)


def held(d: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(reader_examples.case_folder(d).glob("*.json"))]


@pytest.fixture
def clients(tmp_path, monkeypatch):
    from communication_fixture import installation
    data=installation(tmp_path,monkeypatch)
    monkeypatch.delenv("I485_READER_EXAMPLES", raising=False)  # the product's own place: data/reader_examples beside the case folders
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 4, 10, 30))  # noqa: DTZ001 -- fictional firm-local clock
    root = data / "clients"
    return root


# -- writing ------------------------------------------------------------------------------------------------------------------------------------


def test_a_confirm_writes_one_example_with_the_read_and_the_final_value_equal_owner_only(clients):
    d = make_case(clients)
    assert reader_examples.root(clients) == clients.parent / "reader_examples"
    decide(d, "applicant.date_of_birth")
    [ex] = held(d)
    assert ex["case"] == "ana-exemplo" and ex["doc_type"] == "passport" and ex["field"] == "applicant.date_of_birth" and ex["how"] == "confirm"
    assert ex["read_raw"] == DOB_READ and ex["read_normalized"] == "2006-03-14" == ex["final_value"] and ex["outcome"] == "kept"
    assert ex["document"] == ex["file"] == "passport-ana.pdf" and ex["page"] == 1 and ex["by"] == "Jane Paralegal" and ex["role"] == "paralegal" and ex["at"]
    assert ex["crop"] is None and "No word box is known" in ex["crop_note"]  # a reader of the document's text: said, never guessed
    assert ex["undone"] is None and ex["item"] == "fact:applicant.date_of_birth"
    path = next(reader_examples.case_folder(d).glob("*.json"))
    # every field the example holds is one the data dictionary says
    said = {f[0] for f in records.by_id("reader_examples")["fields"]}
    assert set(ex) <= said, sorted(set(ex) - said)
    probe=clients.parent/'permission-mode-probe';probe.mkdir(mode=0o700)
    import os
    fd=os.open(probe/'probe',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    modes=(stat.S_IMODE((probe/'probe').stat().st_mode),stat.S_IMODE(probe.stat().st_mode))
    if modes!=(0o600,0o700):
        pytest.skip(f'Workspace cannot enforce Unix private creation modes: file={modes[0]:o}, directory={modes[1]:o}; Windows ACL isolation unverified')
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700 and stat.S_IMODE(reader_examples.root(clients).stat().st_mode) == 0o700


def test_a_correction_writes_one_example_with_both_values_and_the_scan_gives_its_crop(clients):
    d = make_case(clients)
    decide(d, "applicant.date_of_birth", "set", DOB_TYPED, kind="date")
    [ex] = held(d)
    assert ex["how"] == "correction" and ex["outcome"] == "changed" and ex["read_normalized"] == "2006-03-14" and ex["final_value"] == DOB_TYPED
    decide(d, "applicant.birth_city", "set", "Votorantim")
    city = next(e for e in held(d) if e["field"] == "applicant.birth_city")
    assert city["doc_type"] == "intake_questionnaire" and city["read_raw"] == "Sorocaba" and city["final_value"] == "VOTORANTIM"
    assert city["crop"]["page"] == 2 and city["crop"]["box"] == [120, 840, 1500, 910] and city["crop"]["question"] == "birth_city" and city["crop_note"] == ""


def test_no_example_from_a_value_no_reader_produced(clients):
    d = make_case(clients)
    decide(d, "applicant.phone")  # typed by the client on the portal
    decide(d, "applicant.phone", "set", "5085550199")
    decide(d, "applicant.firm_name")  # the firm's own details
    decide(d, "applicant.middle_name", "set", "CLARA")  # a person typed what no reader read
    decide(d,"applicant.family_name","blank")  # leave blank: not a read kept or changed
    assert held(d) == []
    # an example never costs a decision: a folder that cannot be written leaves the decision on file
    reader_examples.root(clients).mkdir(parents=True, exist_ok=True)
    reader_examples.case_folder(d).write_text("not a folder", encoding="utf-8")
    assert decide(d, "applicant.family_name")["action"] == "confirm"


def test_a_decision_taken_back_counts_nowhere_and_is_kept_on_file(clients):
    d = make_case(clients)
    decide(d, "applicant.family_name")
    assert len(reader_examples.every(clients, lambda case: True)) == 1
    undo_decision(d, "fact:applicant.family_name", "Sam Attorney", "attorney")
    [ex] = held(d)
    assert ex["undone"]["by"] == "Sam Attorney" and reader_examples.every(clients, lambda case: True) == []
    decide(d, "applicant.family_name")  # decided again: a new example
    assert len(held(d)) == 2 and len(reader_examples.every(clients, lambda case: True)) == 1


def test_the_audit_adds_a_box_the_firms_hand_filled_form_changed_from_a_read(clients):
    d = make_case(clients)
    graph = FactGraph.load(d / "fact_graph.json")
    ref = accuracy.Reference("ana-exemplo", "i485", Path("r.pdf"), Path("o.pdf"), graph, {})
    result = {"case": "ana-exemplo", "form": "i485", "boxes": [
        {"kind": "different", "field": "Pt1Line3_DOB[0]", "cause": "reader", "key": "applicant.date_of_birth", "reference": "03/15/2006", "ours": "03/14/2006"},
        {"kind": "different", "field": "Pt1Line3_DOB[1]", "cause": "reader", "key": "applicant.date_of_birth", "reference": "03/15/2006", "ours": "03/14/2006"},
        {"kind": "different", "field": "Pt1Phone[0]", "cause": "answer", "key": "applicant.phone", "reference": "5085550199", "ours": PHONE},
        {"kind": "unfilled", "field": "Pt1City[0]", "cause": "held", "key": "applicant.birth_city", "reference": "VOTORANTIM", "ours": ""},
        {"kind": "different", "field": "Pt1Family[0]", "cause": "reader", "key": "applicant.family_name", "reference": "SOUSA", "ours": "SOUZA"}]}
    marks = [{"form": "i485", "field": "Pt1Family[0]", "reason": "the reference's own error"}]
    assert len(reader_examples.from_reference(clients, ref, result, marks)) == 1
    [ex] = held(d)
    assert ex["how"] == "hand_filled_form" and ex["outcome"] == "changed" and ex["final_value"] == "03/15/2006" and ex["by"] == reader_examples.FORM_WHO
    assert len(reader_examples.from_reference(clients, ref, result, marks)) == 1 and len(held(d)) == 1  # the next month's audit writes the same example


# -- the gate ------------------------------------------------------------------------------------------------------------------------------------


def test_a_protected_case_is_in_no_count_and_an_example_is_read_only_through_the_gate(clients):
    ana, rosa = make_case(clients), make_case(clients, "rosa-exemplo")
    decide(ana, "applicant.date_of_birth")
    decide(rosa, "applicant.date_of_birth", "set", DOB_TYPED, kind="date")
    restricted.mark(rosa, True, "A made-up reason.", "Sam Attorney", "attorney")
    assert not restricted.visible_to(JANE, rosa)
    for_jane = reader_examples.every(clients, lambda case: restricted.visible_to(JANE, clients / case))
    assert [e["case"] for e in for_jane] == ["ana-exemplo"]
    restricted.name_person(rosa, JANE["email"], True, "Sam Attorney", "attorney", JANE["name"])
    assert {e["case"] for e in reader_examples.every(clients, lambda case: restricted.visible_to(JANE, clients / case))} == {"ana-exemplo", "rosa-exemplo"}
    # the pages every member of staff reads count the cases that are not protected only, and say so
    line = reader_examples.nightly(clients)
    assert line.startswith("Reading: 1 example on 1 field, 0 changed by the office.") and reader_examples.PROTECTED_NOTE in line
    assert reader_examples.figures(reader_examples.every(clients, reader_examples.unprotected(clients)))["examples"] == 1
    # an example whose case folder is gone is read by nothing
    shutil.rmtree(clients / "ana-exemplo")
    assert {e["case"] for e in reader_examples.every(clients, lambda case: True)} == {"rosa-exemplo"}


def test_reports_and_the_morning_report_never_carry_a_value(clients, monkeypatch):
    d = make_case(clients)
    for key in ("applicant.date_of_birth", "applicant.birth_city", "applicant.family_name"):
        decide(d, key, "set", "VOTORANTIM" if key.endswith("city") else ("SOUSA" if key.endswith("name") else DOB_TYPED), kind="date" if "date" in key else "text")
    assert len(held(d)) == 3
    rows = [{"id": "ana-exemplo", "summary": {"name": "ANA-EXEMPLO"}, "stage": "review", "office": "Massachusetts", "open_items": 0, "last_activity": "2026-10-04T10:00:00+00:00"}]
    built = json.dumps(reports.build(rows, clients, role="attorney"))
    monkeypatch.setenv("I485_READING", "1")
    morning = overnight.reading_night(clients)
    assert morning.startswith("Reading: 3 examples on 3 fields, 3 changed by the office.")
    page = "\n".join(reader_examples.render(reader_examples.figures(reader_examples.every(clients, reader_examples.unprotected(clients)))))
    for text in (built, morning, page):
        assert not any(s in text for s in SECRETS + ("VOTORANTIM", "SOUSA")), text[:300]
    assert "Jane Paralegal" not in morning + page  # Reports names the reviewer of record (its own table); the examples add no name anywhere
    assert "| Passport reader | applicant date of birth | 1 | 0 | 1 | 100% |" in page and "—" not in page + morning
    monkeypatch.setenv("I485_READING", "0")
    assert overnight.reading_night(clients) == "Reading: switched off."


# -- the end of a case ---------------------------------------------------------------------------------------------------------------------------


def test_the_export_carries_the_examples_folder(clients, tmp_path):
    d = make_case(clients)
    decide(d, "applicant.date_of_birth")
    [path] = list(reader_examples.case_folder(d).glob("*.json"))
    # the client's file handed over (src/engagement.py export_file): one client
    args = argparse.Namespace(all=False, client="ana-exemplo", data=clients, portal=tmp_path / "data" / "portal", firm_files=False)
    entries, _skipped, _warnings, _read = export_firm.gather(args)
    assert f"reader_examples/ana-exemplo/{path.name}" in {e.arcname for e in entries}
    # the export of the firm's data: every file the catalog lists
    assert export_firm.listed(f"reader_examples/ana-exemplo/{path.name}", records.patterns("firm"))
    w = export_firm.default_where(clients, tmp_path / "data" / "portal", tmp_path / "data" / "review_users.json")
    everything, _, _ = export_firm.everything_entries(w, REPO / "docs" / "data_dictionary.md")
    assert f"firm/reader_examples/ana-exemplo/{path.name}" in {e.arcname for e in everything}


def test_a_case_recorded_destroyed_with_its_folder_removed_takes_its_examples_with_it(clients, tmp_path, monkeypatch):
    from portal.store import PortalStore
    from rules import approval

    approval.approve(engagement.PRACTICE_ID, "Sam Attorney", "attorney")
    cases = {}
    for name in ("case-ana", "case-rosa"):
        cases[name] = make_case(clients,name)
        PortalStore(clients.parent / "portal").add_client(name,"Ana Clara Exemplo Souza" if name=='case-ana' else "Rosa Exemplo",email=name+'@fictional.example',language='pt')
        decide(cases[name], "applicant.date_of_birth")
        assert len(held(cases[name])) == 1
    settings.save("firm", {"office.retention_years": "1"}, "Sam Attorney")
    for d in cases.values():
        engagement.end(d, "closed", "Sam Attorney", "attorney", reason="Done.", portal_root=clients.parent / "portal")
    monkeypatch.setattr(clock, "_now_override", datetime(2032, 10, 5, 9, 0))  # noqa: DTZ001 -- fictional firm-local clock
    # Operational closure/the legacy one-year office proposal is insufficient.
    # This positive lifecycle scenario records separate fictional attorney
    # determinations after the current six-year inclusive minimum.
    with pytest.raises(ValueError,match='legal destruction approval'):
        engagement.mark_destroyed(clients,'case-rosa','Sam Attorney','attorney',folder_removed=False,export_kept=True)
    from file_policy_fixture import disposition
    for d in cases.values():
        disposition(d,who='Sam Attorney',completed_on='2026-10-04',age_status='adult',
                    keep_until='2032-10-04',portal_root=clients.parent/'portal')
    optional=[]
    for name in cases:
        for namespace in ('evaluation-authorizations','evaluation-candidates'):
            folder=clients.parent/namespace/name;folder.mkdir(parents=True)
            path=folder/'fictional.json';path.write_bytes(b'fictional evaluation metadata');optional.append((name,path))
    # destroyed with the folder kept: the examples stay with the folder
    engagement.mark_destroyed(clients, "case-rosa", "Sam Attorney", "attorney", folder_removed=False, export_kept=True)
    assert len(held(cases["case-rosa"])) == 1
    assert all(path.exists() for name,path in optional)
    # the folder removed: its examples go with it, and the record says how many
    own_candidate=clients.parent/'evaluation-candidates'/'case-ana'
    other_bytes=clients.parent/'fictional-external-artifact';other_bytes.write_bytes(b'other-client bytes')
    unsafe=own_candidate/'unsafe-link';unsafe.symlink_to(other_bytes)
    with pytest.raises(ValueError,match='Cleanup remains unresolved'):
        engagement.mark_destroyed(clients,'case-ana','Sam Attorney','attorney',folder_removed=True,export_kept=False)
    assert held(cases['case-ana']) and not any(r['case']=='case-ana' for r in engagement.destroyed(clients))
    assert other_bytes.read_bytes()==b'other-client bytes'
    unsafe.unlink()  # only the deliberately created fictional link, never its target
    engagement.mark_destroyed(clients, "case-ana", "Sam Attorney", "attorney", folder_removed=True, export_kept=False)
    assert not reader_examples.case_folder(cases["case-ana"]).exists() and held(cases["case-rosa"])
    assert all(not path.exists() if name=='case-ana' else path.read_bytes()==b'fictional evaluation metadata' for name,path in optional)
    saved = json.loads((clients.parent / engagement.DESTROYED_FILE).read_text(encoding="utf-8"))
    row = next(r for r in saved["cases"] if r["case"] == "case-ana")
    assert row["examples_removed"] == 1 and "examples_removed" not in next(r for r in saved["cases"] if r["case"] == "case-rosa")
    assert row['evaluation_artifacts_removed']=={'evaluation_authorizations':1,'evaluation_candidates':1}
    assert set(row) <= {f[0].removeprefix("cases[].") for f in records.by_id("destroyed")["fields"]} | {"state_name", "until", "at", "role"}


# -- the figures --------------------------------------------------------------------------------------------------------------------------------


def made_up(doc_type: str, field: str, n: int, changed: int, case: str = "ana-exemplo") -> list[dict]:
    return [{"case": case, "doc_type": doc_type, "field": field, "outcome": "changed" if i < changed else "kept"} for i in range(n)]


SET = (made_up("visa", "applicant.visa_number", 20, 12) + made_up("passport", "applicant.date_of_birth", 25, 5)
       + made_up("intake_questionnaire", "applicant.birth_city", 40, 6, "rosa-exemplo") + made_up("ssn_card", "applicant.ssn", 50, 6)
       + made_up("i94", "applicant.i94_admit_until_date", 30, 3) + made_up("birth_certificate", "applicant.birth_city", 10, 8)
       + made_up("passport", "applicant.family_name", 22, 0))


def test_the_accuracy_reports_numbers_from_a_made_up_set():
    fig = reader_examples.figures(SET)
    assert (fig["examples"], fig["kept"], fig["changed"], fig["cases"], len(fig["fields"])) == (197, 157, 40, 2, 7)
    by = {(r["doc_type"], r["field"]): r for r in fig["fields"]}
    dob = by[("passport", "applicant.date_of_birth")]
    assert (dob["examples"], dob["kept"], dob["changed"], dob["rate"]) == (25, 20, 5, 0.2) and dob["reader"] == "Passport reader"
    # the ten worst: the highest share changed first; a field never changed is not among them
    assert [r["doc_type"] for r in fig["worst"]] == ["birth_certificate", "visa", "passport", "intake_questionnaire", "ssn_card", "i94"]
    # the morning report: at least 20 examples and more than 10% changed, the worst three (the birth certificate has too few; the I-94 is at 10%, not above)
    named = reader_examples.morning_worst(fig)
    assert [(r["doc_type"], r["changed"], r["examples"]) for r in named] == [("visa", 12, 20), ("passport", 5, 25), ("intake_questionnaire", 6, 40)]
    page = "\n".join(reader_examples.render(fig))
    assert "197 examples on 7 fields, from 2 cases: 157 confirmed as read, 40 changed (20%)." in page
    assert "| Birth certificate reader | applicant birth city | 10 | 8 | 80% |" in page and "| Visa reader | applicant visa number | 20 | 12 | 60% |" in page
    assert "No examples yet." in "\n".join(reader_examples.render(None)) and "No examples yet." in "\n".join(reader_examples.render(reader_examples.figures([])))


def test_the_morning_report_names_the_worst_three_fields_only_when_one_has_20_examples_and_more_than_a_tenth_changed(clients):
    for case in ("ana-exemplo", "rosa-exemplo"):
        (clients / case).mkdir()
    for n, e in enumerate(SET):
        rec = e | {"id": f"x{n:04d}", "version": 1, "at": "2026-10-04T10:00:00+00:00"}
        reader_examples._write(reader_examples.root(clients) / e["case"], rec)
    line = reader_examples.nightly(clients)
    assert line.splitlines()[0] == "Reading: 197 examples on 7 fields, 40 changed by the office."
    assert line.splitlines()[1] == ("The fields the office changes most (20 or more examples, more than 10% changed): Visa reader, applicant visa number: 12 of 20 changed (60%); "
                                    "Passport reader, applicant date of birth: 5 of 25 changed (20%); Paper questionnaire (scan), applicant birth city: 6 of 40 changed (15%).")
    few = clients.parent / "few"
    (few / "clients" / "ana-exemplo").mkdir(parents=True)
    for n, e in enumerate(made_up("passport", "applicant.date_of_birth", 19, 10)):
        reader_examples._write(reader_examples.root(few / "clients") / "ana-exemplo", e | {"id": f"y{n:04d}"})
    assert len(reader_examples.nightly(few / "clients").splitlines()) == 2  # 19 examples: a count, no field named


# -- the register --------------------------------------------------------------------------------------------------------------------------------


def test_the_register_item_is_complete_and_the_register_re_dumps_byte_for_byte():
    path = schema_path.path("register", "maintenance")
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert json.dumps(data, indent=2, ensure_ascii=False) + "\n" == text
    items = {i["id"]: i for i in data["items"]}
    item, model = items["reader_examples_review"], items["retention_review"]
    assert list(item) == list(model) and item["cadence"] == "quarterly" and item["party"] == "firm" and item["owner"] == "attorney"
    assert item["firm_steps"] and item["firm_what"] and item["steps"] and item["where"] and item["check"]["type"] == "manual" and item["last_checked"] is None
    assert not any(w in " ".join(item["firm_steps"]) for w in ("schemas/", "src/", ".json", ".py", "IT:", "data/"))
    assert any("reader_examples" in w for w in item["where"])


def test_the_firms_one_sentence_is_in_both_statements_and_the_question_is_the_attorneys():
    for page in ("docs/security/product_data_statement.md", "docs/public/data_statement.md"):
        text = (REPO / page).read_text(encoding="utf-8")
        assert "The firm keeps, on its own machine, one small labelled example" in text, page
    assert "reader examples" in (REPO / "docs" / "attorney_review.md").read_text(encoding="utf-8").lower()
