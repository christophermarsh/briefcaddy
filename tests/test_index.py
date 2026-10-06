"""The firm-wide document index (src/index.py): documents.json of every case in one SQLite file with full-text search.
Everyone here is made up. The records are fixtures in the shape src/documents.py writes (data/clients/<id>/documents.json)."""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

import index
import offices
import second_factor
import settings
from factgraph import FactGraph
import schema_path

_REPO = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 2)


def doc(doc_id, type_, text="", *, person="applicant", file=None, pages=None, issued=None, expires=None, confidential=None,
        a_number="", receipt="", passport="", ssn_last4="", translated=None, language="pt", quality="readable"):
    return {"id": doc_id, "files": [file or f"{doc_id}.pdf"], "pages": pages or [1], "type": type_, "confidence": 0.9, "person": person,
            "person_set_by": None, "language": language, "issued": issued, "expires": expires,
            "identifiers": {"a_number": a_number, "receipt": receipt, "passport": passport, "ssn_last4": ssn_last4},
            "quality": quality, "hash": doc_id * 4, "source": "scan inbox", "added": "2026-09-30T10:00:00+00:00", "roles": [], "tags": [],
            "confidential": confidential, "text": text, "translated": translated}


def case(root: Path, case_id: str, docs, *, name="Ana Clara Exemplo Souza", state="MA", filings=()):
    """A case folder as the pipeline leaves it: its fact graph (the name, the state), its documents.json, its status.json."""
    d = root / case_id
    d.mkdir(parents=True, exist_ok=True)
    g = FactGraph(case_id)
    given, _, family = name.partition(" ")
    for key, value in {"applicant.given_name": given, "applicant.family_name": family, "applicant.physical_state": state}.items():
        g.add_source(key, "questionnaire", "intake_questionnaire", value, value, 0.95)
    g.save(d / "fact_graph.json")
    if docs is not None:
        (d / "documents.json").write_text(json.dumps({"version": 1, "built": "2026-10-01T02:00:00+00:00", "documents": list(docs)}), encoding="utf-8")
    if filings:
        (d / "status.json").write_text(json.dumps({"filings": [{"filing": f, "mailed_on": "2026-09-01"} for f in filings]}), encoding="utf-8")
    return d


@pytest.fixture
def root(tmp_path):
    return tmp_path / "clients"


@pytest.fixture
def db(tmp_path):
    return tmp_path / "index.db"


def found(db, *args, **kw):
    return index.search(*args, db_path=db, **kw)


def ids(result):
    return sorted((r["case"], r["doc"]) for r in result["results"])


# -- building and searching text ------------------------------------------------------------------


def test_every_case_is_indexed_and_a_word_finds_the_document(root, db):
    case(root, "case-a", [doc("a1", "birth_certificate", "Certidao de Nascimento. Nome: Ana Clara. Mae: Maria Exemplo Lima. Cidade: Sorocaba", file="birth.pdf", pages=[3, 4]),
                          doc("a2", "lease", "Lease agreement for 10 Example Street, Springfield. Landlord: Example Realty")])
    case(root, "case-b", [doc("b1", "passport", "Passport. Surname EXEMPLO. Given names MARCOS. Nationality BRASIL")], name="Marcos Exemplo")
    stats = index.rebuild_all(root, db)
    assert stats["rebuilt"] == 2 and stats["documents"] == 3

    r = found(db, "Sorocaba")
    assert ids(r) == [("case-a", "a1")] and r["total"] == 1
    hit = r["results"][0]
    assert hit["type_name"] == "Birth certificate" and hit["name"] == "Ana Clara Exemplo Souza" and hit["person"] == "applicant"
    assert hit["file"] == "birth.pdf" and hit["page"] == 3  # the page to open: the first of the document's own pages
    assert hit["match"] == "text" and "\x01Sorocaba\x02" in hit["snippet"]  # the match, between the markers the page turns into <mark>
    assert found(db, "landlord example")["total"] == 1  # every word has to be there, in any order
    assert found(db, "nothing like this")["total"] == 0


def test_the_last_word_is_a_prefix_and_accents_dont_matter(root, db):
    case(root, "case-a", [doc("a1", "birth_certificate", "Nome do pai: José Exemplo Souza. Naturalidade: São Paulo")])
    index.rebuild_all(root, db)
    assert found(db, "Jose")["total"] == 1 and found(db, "sao paulo")["total"] == 1  # Portuguese names typed without accents
    assert found(db, "natural")["total"] == 1  # still typing
    assert found(db, "José Exe")["total"] == 1


def test_the_english_translation_is_searched_too(root, db):
    case(root, "case-a", [doc("a1", "birth_certificate", "Certidao de Nascimento. Filiacao: Maria", translated="Birth certificate. Parentage: Maria")])
    index.rebuild_all(root, db)
    r = found(db, "parentage")
    assert ids(r) == [("case-a", "a1")] and "\x01Parentage\x02" in r["results"][0]["snippet"]


def test_what_is_typed_can_never_be_a_syntax_error(root, db):
    case(root, "case-a", [doc("a1", "lease", "Lease for apartment 4B, rent $1,200 (monthly) - NEAR the bus")])
    index.rebuild_all(root, db)
    for hostile in ['"', "AND OR", "a OR", "NEAR(", "*", 'rent" OR "x', "col:val", "(((", "-", "'; drop table documents; --"]:
        found(db, hostile)  # no exception
    assert found(db, "rent")["total"] == 1 and found(db, "")["total"] == 0  # nothing asked: never a dump of every document


def test_a_case_with_no_record_is_skipped(root, db):
    case(root, "case-a", [doc("a1", "lease", "Lease")])
    case(root, "case-new", None)  # processed before the record existed, or not yet
    stats = index.rebuild_all(root, db)
    assert stats["rebuilt"] == 1 and stats["skipped"] == 1 and stats["documents"] == 1
    assert index.rebuild(root / "case-new", db) == 0  # one case on its own: nothing, and no error
    assert found(db, "lease")["total"] == 1


def test_a_record_that_cant_be_read_keeps_the_rows_it_had(root, db):
    d = case(root, "case-a", [doc("a1", "lease", "Lease for Example Street")])
    index.rebuild_all(root, db)
    (d / "documents.json").write_text("{ not json", encoding="utf-8")
    assert index.rebuild_changed(root, db)["unreadable"] == 1
    assert found(db, "example street")["total"] == 1


def test_odd_records_are_tolerated(root, db):
    odd = [{"id": "x1", "type": "lease", "text": "Lease at Example Street", "files": "not a list", "pages": None, "identifiers": "none"},
           {"no_id": True}, "a string", doc("x1", "lease", "a repeat of the first id")]
    case(root, "case-a", odd)
    index.rebuild_all(root, db)
    r = found(db, "example street")
    assert r["total"] == 1 and r["results"][0]["file"] is None and r["results"][0]["page"] is None


def test_a_full_social_security_number_is_never_stored(root, db):
    case(root, "case-a", [doc("a1", "ssn_card", "SOCIAL SECURITY 123-45-6789 ANA CLARA EXEMPLO SOUZA", ssn_last4="6789"),
                          doc("a2", "tax_return", "Taxpayer SSN: 987 65 4321. Spouse 111224444")])
    index.rebuild_all(root, db)
    with closing(sqlite3.connect(db)) as raw:
        everything = " ".join(str(v) for row in raw.execute("select text, translated from text_fts") for v in row)
        columns = " ".join(str(v) for row in raw.execute("select * from documents") for v in row)
    assert "123-45-6789" not in everything and "987 65 4321" not in everything and "XXX-XX-6789" in everything and "XXX-XX-4321" in everything
    assert "6789" in columns and "123456789" not in columns  # the last four the record holds; no more
    assert found(db, "123-45")["total"] == 0 and found(db, "exemplo souza")["total"] == 1


# -- identifiers ---------------------------------------------------------------------------------


def test_an_a_number_or_receipt_typed_in_the_box_matches_exactly(root, db):
    case(root, "case-a", [doc("a1", "green_card", "Permanent resident card", a_number="A-012 345 678", receipt="IOE0999000300"),
                          doc("a2", "passport", "Passport", passport="ya1234567", a_number="")])
    case(root, "case-b", [doc("b1", "uscis_notice", "Notice of action. Receipt IOE0999000300 for case-a's I-130. Beneficiary A012345679")],
         name="Marcos Exemplo")
    index.rebuild_all(root, db)
    for typed in ("A012345678", "a-012-345-678", "012345678", "A 012 345 678", "12345678"):  # one number however it is written
        r = found(db, typed)
        assert ids(r) == [("case-a", "a1")] and r["results"][0]["match"] == "identifier", typed
    r = found(db, "IOE0999000300")
    assert ids(r) == [("case-a", "a1"), ("case-b", "b1")]  # the card's identifier, then the notice that only mentions it
    assert [x["match"] for x in r["results"]] == ["identifier", "text"]
    assert ids(found(db, "YA1234567")) == [("case-a", "a2")]
    assert found(db, "A012345670")["total"] == 0  # one digit off is not a match


def test_a_name_that_looks_like_nothing_special_is_just_text(root, db):
    case(root, "case-a", [doc("a1", "lease", "Lease. Tenant Ana Clara 5 years")])
    index.rebuild_all(root, db)
    assert found(db, "tenant ana")["total"] == 1


# -- filters --------------------------------------------------------------------------------------


def test_filters_by_type_person_and_office(root, db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    florida = settings.add_office("Ana Attorney")
    settings.save(florida, {"office.name": "Miami, FL", "office.states": "FL"}, "Ana Attorney")
    case(root, "case-ma", [doc("m1", "birth_certificate", "Birth certificate of the child", person="child_1"),
                           doc("m2", "birth_certificate", "Birth certificate of the applicant")], state="MA")
    case(root, "case-fl", [doc("f1", "birth_certificate", "Birth certificate of the applicant")], name="Beatriz Exemplo", state="FL")
    index.rebuild_all(root, db)
    assert found(db, "birth", types=["birth_certificate"])["total"] == 3 and found(db, "birth", types=["lease"])["total"] == 0
    assert ids(found(db, "birth", person="child_1")) == [("case-ma", "m1")]
    main = offices.offices()[0]
    ma = found(db, "birth", office=main["name"])
    assert ids(ma) == [("case-ma", "m1"), ("case-ma", "m2")] and ma["results"][0]["office"] == main["name"]
    assert ids(found(db, "birth", office=main["id"])) == ids(ma)
    assert ids(found(db, "birth", office="Miami, FL")) == [("case-fl", "f1")]
    assert ids(found(db, "birth", office=florida)) == [("case-fl", "f1")]  # by the office's id as well
    assert ids(found(db, types=["birth_certificate"], person="child_1")) == [("case-ma", "m1")]  # filters alone list documents too
    with pytest.raises(ValueError):
        found(db, "birth", expiring_before="next month")


# -- what only the attorney sees ------------------------------------------------------------------


def test_restricted_documents_are_left_out_and_counted(root, db):
    case(root, "case-vawa", [doc("v1", "affidavit", "Declaration of the applicant: the abuse began in 2024", confidential="1367"),
                             doc("v2", "lease", "Lease. The applicant lives at Example Street")])
    case(root, "case-asylum", [doc("s1", "affidavit", "Declaration of the applicant: the abuse stopped when she left", confidential="208.6")],
         name="Beatriz Exemplo")
    case(root, "case-plain", [doc("p1", "affidavit", "Declaration of the applicant about their employment")], name="Caio Exemplo")
    index.rebuild_all(root, db)
    hidden = found(db, "declaration applicant")
    assert ids(hidden) == [("case-plain", "p1")] and hidden["total"] == 1 and hidden["restricted"] == 2
    assert "abuse" not in json.dumps(hidden)  # nothing of the restricted ones is in what comes back
    assert found(db, "abuse")["results"] == [] and found(db, "abuse")["restricted"] == 2  # the count, never a name
    allowed = found(db, "declaration applicant", include_confidential=True)
    assert len(allowed["results"]) == 3 and allowed["restricted"] == 0 and sum(r["confidential"] for r in allowed["results"]) == 2
    assert found(db, "lease", include_confidential=False)["restricted"] == 0


def test_a_protected_case_restricts_every_document_in_it(root, db):
    """8 U.S.C. 1367 covers any information about a VAWA, T or U beneficiary, so a filing on the case restricts the
    lease and the passport too, whatever each record says (documents.case_confidentiality, asked at index time)."""
    case(root, "case-u", [doc("u1", "lease", "Lease. The applicant lives at Example Street"),
                          doc("u2", "passport", "Passport of the applicant")], name="Dora Exemplo", filings=["u_visa"])
    case(root, "case-plain", [doc("p1", "lease", "Lease. The applicant lives at Example Street")], name="Caio Exemplo")
    index.rebuild_all(root, db)
    hidden = found(db, "lease")
    assert ids(hidden) == [("case-plain", "p1")] and hidden["restricted"] == 1 and "Dora" not in json.dumps(hidden)
    allowed = found(db, "lease", include_confidential=True)
    assert sorted(ids(allowed)) == [("case-plain", "p1"), ("case-u", "u1")] and sum(r["confidential"] for r in allowed["results"]) == 1


# -- keeping it current ---------------------------------------------------------------------------


def test_only_the_cases_that_changed_are_read_again(root, db):
    a = case(root, "case-a", [doc("a1", "lease", "Lease for Example Street")])
    case(root, "case-b", [doc("b1", "passport", "Passport EXEMPLO")], name="Marcos Exemplo")
    assert index.rebuild_changed(root, db)["rebuilt"] == 2
    again = index.rebuild_changed(root, db)
    assert again["rebuilt"] == 0 and again["unchanged"] == 2

    (a / "documents.json").write_text(json.dumps({"version": 1, "documents": [doc("a1", "lease", "Lease for Example Street"),
                                                                              doc("a2", "bank_statement", "Bank statement Example Credit Union")]}), encoding="utf-8")
    third = index.rebuild_changed(root, db)
    assert third["rebuilt"] == 1 and third["unchanged"] == 1 and third["documents"] == 3
    assert ids(found(db, "credit union")) == [("case-a", "a2")]

    (a / "status.json").write_text(json.dumps({"filings": [{"filing": "eoir28"}]}), encoding="utf-8")  # a filing recorded changes the case too
    assert index.rebuild_changed(root, db)["rebuilt"] == 1


def test_a_change_to_meta_json_alone_builds_the_case_again(root, db):
    """The wave G verifier's note: a document classified or added changes meta.json (which documents were read, and how each was classified), and the case's
    rows are built again even when documents.json, the fact graph and status.json have not changed."""
    a = case(root, "case-a", [doc("a1", "lease", "Lease for Example Street")])
    case(root, "case-b", [doc("b1", "passport", "Passport EXEMPLO")], name="Marcos Exemplo")
    (a / "meta.json").write_text(json.dumps({"client_id": "case-a", "classifications": {"a1.pdf": "lease"}}), encoding="utf-8")
    assert index.rebuild_changed(root, db)["rebuilt"] == 2 and index.rebuild_changed(root, db)["rebuilt"] == 0
    (a / "meta.json").write_text(json.dumps({"client_id": "case-a", "classifications": {"a1.pdf": "lease", "a2.pdf": "bank_statement"}}), encoding="utf-8")
    again = index.rebuild_changed(root, db)
    assert again["rebuilt"] == 1 and again["unchanged"] == 1
    assert "meta.json" in index.SIGNED


def test_one_case_is_rebuilt_in_place(root, db):
    a = case(root, "case-a", [doc("a1", "lease", "old words"), doc("a2", "lease", "more old words")])
    case(root, "case-b", [doc("b1", "lease", "old words too")], name="Marcos Exemplo")
    index.rebuild_all(root, db)
    case(root, "case-a", [doc("a1", "lease", "new words")])
    assert index.rebuild(a, db) == 1
    assert ids(found(db, "old words")) == [("case-b", "b1")] and ids(found(db, "new words")) == [("case-a", "a1")]
    assert index.rebuild(a, db) == 1  # again: no duplicates
    assert found(db, "new")["total"] == 1


def test_a_removed_record_or_folder_takes_its_rows_with_it(root, db, tmp_path):
    a = case(root, "case-a", [doc("a1", "lease", "Lease words")])
    b = case(root, "case-b", [doc("b1", "lease", "Lease words")], name="Marcos Exemplo")
    index.rebuild_all(root, db)
    (a / "documents.json").unlink()
    (b / "documents.json").rename(tmp_path / "elsewhere.json")
    import shutil

    shutil.rmtree(b)
    stats = index.rebuild_changed(root, db)
    assert stats["removed"] == 2 and stats["documents"] == 0 and found(db, "lease")["total"] == 0


def test_a_change_of_office_rebuilds_every_case(root, db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    case(root, "case-fl", [doc("f1", "lease", "Lease")], state="FL")
    index.rebuild_changed(root, db)
    assert found(db, "lease", office="Miami, FL")["total"] == 0  # no Miami office yet: it was the main office's
    florida = settings.add_office("Ana Attorney")
    settings.save(florida, {"office.name": "Miami, FL", "office.states": "FL"}, "Ana Attorney")
    assert index.rebuild_changed(root, db)["rebuilt"] == 1
    assert found(db, "lease", office="Miami, FL")["total"] == 1


def test_refresh_is_throttled(root, db, monkeypatch):
    case(root, "case-a", [doc("a1", "lease", "Lease")])
    index._LAST_REFRESH.clear()
    index.refresh(root, db)
    case(root, "case-b", [doc("b1", "lease", "Lease")], name="Marcos Exemplo")
    index.refresh(root, db)  # within the minute: not looked at again
    assert found(db, "lease")["total"] == 1
    index.refresh(root, db, every=0)
    assert found(db, "lease")["total"] == 2


# -- a damaged file ---------------------------------------------------------------------------------


def test_a_damaged_index_file_is_set_aside_and_built_again(root, db):
    case(root, "case-a", [doc("a1", "lease", "Lease for Example Street")])
    index.rebuild_all(root, db)
    db.write_bytes(b"this was a database once" * 400)
    r = found(db, "lease")  # a search on a broken file: nothing, and no crash
    assert r["results"] == [] and Path(str(db) + ".damaged").exists()  # the old file is kept aside for whoever looks
    index.refresh(root, db, every=0)
    assert found(db, "lease")["total"] == 1


def test_rebuild_and_the_nightly_run_recover_a_damaged_file(root, db):
    a = case(root, "case-a", [doc("a1", "lease", "Lease for Example Street")])
    case(root, "case-b", [doc("b1", "lease", "Another Example lease")], name="Marcos Exemplo")
    index.rebuild_all(root, db)
    db.write_bytes(b"\x00garbage" * 5000)
    assert index.rebuild(a, db) == 1  # the case just processed is in a new file ...
    assert found(db, "lease")["total"] == 1
    index.rebuild_changed(root, db)  # ... and the nightly run fills in the rest
    assert found(db, "lease")["total"] == 2


def test_the_nightly_integrity_check_makes_a_broken_file_new(root, db):
    case(root, "case-a", [doc("a1", "lease", "Lease for Example Street")])
    index.rebuild_all(root, db)
    raw = bytearray(db.read_bytes())
    for i in range(4096 * 2, len(raw), 97):  # damage the pages behind the first two (the schema)
        raw[i] ^= 0xFF
    for sidecar in ("-wal", "-shm"):
        Path(str(db) + sidecar).unlink(missing_ok=True)
    db.write_bytes(bytes(raw))
    stats = index.rebuild_changed(root, db, integrity=True)
    assert stats["documents"] == 1 and found(db, "lease")["total"] == 1


def test_an_index_from_an_older_version_is_dropped_and_built_again(root, db):
    case(root, "case-a", [doc("a1", "lease", "Lease")])
    index.rebuild_all(root, db)
    with closing(sqlite3.connect(db)) as raw:
        raw.execute("pragma user_version = 0")
        raw.execute("pragma user_version = 99")
        raw.commit()
    assert index.rebuild_changed(root, db)["rebuilt"] == 1 and found(db, "lease")["total"] == 1


def test_a_missing_index_finds_nothing(db):
    assert found(db, "anything") == {"query": "anything", "results": [], "total": 0, "restricted": 0, "limit": 50}


# -- the questions the firm asks ------------------------------------------------------------------


def saved(today=TODAY):
    return {s["id"]: s for s in index.saved_searches(today)}


def run_saved(db, which, **extra):
    p = dict(saved()[which]["params"])
    return found(db, types=p.pop("type").split(","), **p, **extra)


def test_work_permits_expiring_in_90_days(root, db):
    case(root, "case-soon", [doc("e1", "ead", "Employment authorization", expires="2026-11-20", file="ead.pdf")], name="Ana Soon")
    case(root, "case-far", [doc("e2", "ead", "Employment authorization", expires="2027-05-01")], name="Ana Far")
    case(root, "case-lapsed", [doc("e3", "ead", "Employment authorization", expires="2026-08-01")], name="Ana Lapsed")
    case(root, "case-renewed", [doc("e4", "work_permit", "Old card", expires="2026-11-01"), doc("e5", "work_permit", "New card", expires="2028-11-01")], name="Ana Renewed")
    case(root, "case-spouse", [doc("e6", "ead", "Spouse's card", person="spouse", expires="2026-12-15"),
                               doc("e7", "ead", "Applicant's card", expires="2028-01-01")], name="Ana Spouse")
    case(root, "case-passport", [doc("p1", "passport", "Passport", expires="2026-11-15")], name="Ana Passport")
    index.rebuild_all(root, db)
    r = run_saved(db, "ead_90")
    assert ids(r) == [("case-soon", "e1"), ("case-spouse", "e6")]  # the soon ones; not far, lapsed, replaced by a newer card or another kind
    assert [x["expires"] for x in r["results"]] == ["2026-11-20", "2026-12-15"]  # the soonest first
    assert saved()["ead_90"]["params"]["expiring_before"] == "2026-12-31" and saved()["ead_90"]["params"]["expiring_after"] == "2026-10-02"


def test_notice_to_appear_with_no_eoir28(root, db):
    case(root, "case-open", [doc("n1", "nta", "Notice to Appear. Immigration court")], name="Ana Open")
    case(root, "case-old-type", [doc("n2", "notice_to_appear", "Notice to Appear")], name="Ana Older")  # the classifier's own name for it
    case(root, "case-filed", [doc("n3", "nta", "Notice to Appear")], name="Ana Filed", filings=["i485", "eoir28"])
    case(root, "case-other", [doc("n4", "nta", "Notice to Appear")], name="Ana Other", filings=["i485"])
    case(root, "case-none", [doc("l1", "lease", "Lease")], name="Ana Lease")
    index.rebuild_all(root, db)
    assert ids(run_saved(db, "nta_no_eoir28")) == [("case-old-type", "n2"), ("case-open", "n1"), ("case-other", "n4")]


def test_police_clearances_older_than_two_years(root, db):
    case(root, "case-old", [doc("c1", "police_clearance", "Certidao de antecedentes", issued="2024-06-01")], name="Ana Old")
    case(root, "case-new", [doc("c2", "police_clearance", "Certidao de antecedentes", issued="2026-08-01")], name="Ana New")
    case(root, "case-replaced", [doc("c3", "police_clearance", "Old one", issued="2023-01-01"), doc("c4", "police_clearance", "New one", issued="2026-09-01")], name="Ana Replaced")
    case(root, "case-edge", [doc("c5", "police_clearance", "Certidao", issued="2024-10-03")], name="Ana Edge")  # 729 days: not yet older than two years
    case(root, "case-undated", [doc("c6", "police_clearance", "No date")], name="Ana Undated")
    index.rebuild_all(root, db)
    assert ids(run_saved(db, "police_old")) == [("case-old", "c1")]
    assert saved()["police_old"]["params"]["issued_before"] == "2024-10-02"


def test_the_saved_searches_are_filters_and_nothing_more():
    for s in index.saved_searches(TODAY):
        assert set(s["params"]) <= {"type", "expiring_before", "expiring_after", "issued_before", "no_filing"} and s["label"]
    assert [s["label"] for s in index.saved_searches(TODAY)][1] == "Notice to Appear with no EOIR-28 filed"


def test_type_names_come_from_the_taxonomy_when_there_is_one(monkeypatch, tmp_path):
    assert index.type_name("ead") == "Work permit (EAD)" and index.type_name("some_new_kind") == "Some new kind" and index.type_name("") == "Document"
    fake = schema_path.schemas_in(tmp_path)
    schema_path.folder("register", fake).mkdir(parents=True)
    schema_path.path("register", "document_types", fake).write_text(json.dumps({"types": [{"id": "police_clearance", "name": "Police clearance (certified)"}]}), encoding="utf-8")
    monkeypatch.setattr(index, "SCHEMAS", fake)
    assert index.type_name("police_clearance") == "Police clearance (certified)"
    schema_path.path("register", "document_types", fake).write_text(json.dumps({"types": {"ead": {"name": "Employment authorization card"}}}), encoding="utf-8")
    index._NAMES.clear()
    assert index.type_name("ead") == "Employment authorization card"
    index._NAMES.clear()


# -- the nightly run and the pipeline ---------------------------------------------------------------


def test_the_nightly_run_updates_the_index_and_never_stops_on_it(root, db, monkeypatch):
    import overnight

    monkeypatch.setenv("I485_INDEX", str(db))
    case(root, "case-a", [doc("a1", "lease", "Lease for Example Street")])
    assert overnight.search_index(root) == "Search index: 1 case(s) updated, 1 documents in all."
    assert overnight.search_index(root) == "Search index: 0 case(s) updated, 1 documents in all."
    monkeypatch.setattr(index, "rebuild_changed", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    assert "couldn't update (RuntimeError)" in overnight.search_index(root)  # said, not raised


def test_processing_a_case_never_fails_on_the_index(root, db, monkeypatch, capsys):
    import process_clients

    monkeypatch.setenv("I485_INDEX", str(db))
    d = case(root, "case-a", [doc("a1", "lease", "Lease for Example Street")])
    process_clients._index(d)
    assert found(db, "example street")["total"] == 1
    monkeypatch.setattr(index, "rebuild", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    process_clients._index(d)  # no exception
    assert "search index couldn't be updated" in capsys.readouterr().err


# -- the review app ---------------------------------------------------------------------------------

PASSWORD = "a long enough password for the test"  # secret-scan: allow (a made-up test password)


@pytest.fixture
def staff_server(root, db, monkeypatch):
    from review.auth import Accounts
    from review.server import ReviewApp, make_handler, serve

    monkeypatch.setenv("I485_INDEX", str(db))
    index._LAST_REFRESH.clear()
    case(root, "case-a", [doc("a1", "birth_certificate", "Certidao de Nascimento de Ana Clara, Sorocaba", file="birth.pdf", pages=[2], a_number="A012345678"),
                          doc("a2", "affidavit", "Declaration about the abuse in 2024", confidential="1367")])
    case(root, "case-b", [doc("b1", "nta", "Notice to Appear")], name="Marcos Exemplo")
    accounts = Accounts(root.parent / "staff.json")
    for email, name, role in (("jane@firm.example", "Jane Doe", "paralegal"), ("sam@firm.example", "Sam Attorney", "attorney")):
        accounts.change_password(email, accounts.add(email, name, role), PASSWORD)
    second_factor.set_up(accounts, "sam@firm.example", PASSWORD)  # an attorney signs in with a code (review/auth.py)
    app = ReviewApp(root, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, accounts=accounts)
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _call(url, body=None, cookie=None):
    headers = {"Content-Type": "application/json", "X-Review-App": "1"} | ({"Cookie": cookie} if cookie else {})
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers.get("Set-Cookie")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), None


def _sign_in(base, email):
    _, body, cookie = _call(base + "/api/login", {"email": email, "password": PASSWORD})
    return second_factor.finish(base, email, cookie.split(";")[0]) if body["user"].get("second_factor") else cookie.split(";")[0]


def test_the_search_api_needs_a_sign_in_and_hides_restricted_documents_from_a_paralegal(staff_server):
    base = staff_server
    assert _call(base + "/api/search?q=sorocaba")[0] == 401
    paralegal, attorney = _sign_in(base, "jane@firm.example"), _sign_in(base, "sam@firm.example")

    status, body, _ = _call(base + "/api/search?q=sorocaba", cookie=paralegal)
    assert status == 200 and body["total"] == 1 and body["results"][0]["case"] == "case-a" and body["results"][0]["file"] == "birth.pdf"
    assert body["results"][0]["page"] == 2 and "\x01Sorocaba\x02" in body["results"][0]["snippet"]
    assert [s["id"] for s in body["saved"]] == ["ead_90", "nta_no_eoir28", "police_old"] and {t["id"] for t in body["types"]} >= {"nta", "birth_certificate"}

    status, body, _ = _call(base + "/api/search?q=abuse", cookie=paralegal)
    assert body["results"] == [] and body["restricted"] == 1  # "1 more in restricted documents", nothing else
    assert _call(base + "/api/search?q=abuse", cookie=attorney)[1]["results"][0]["doc"] == "a2"

    assert ids(_call(base + "/api/search?q=A-012-345-678", cookie=paralegal)[1]) == [("case-a", "a1")]  # an identifier typed in the box
    assert ids(_call(base + "/api/search?type=nta,notice_to_appear&no_filing=eoir28", cookie=paralegal)[1]) == [("case-b", "b1")]
    assert _call(base + "/api/search?expiring_before=soon", cookie=paralegal)[0] == 400
    assert _call(base + "/api/search", cookie=paralegal)[1]["results"] == []  # nothing asked, nothing listed


def test_the_search_finds_a_case_the_nightly_run_has_not_reached(staff_server, root, monkeypatch):
    monkeypatch.setenv("I485_WALK_EVERY", "0")  # the old pace, on purpose: every search looks at every folder first (as installed, the ledger and a walk every ten minutes do: tests/test_scale.py)
    base = staff_server
    cookie = _sign_in(base, "jane@firm.example")
    assert _call(base + "/api/search?q=sorocaba", cookie=cookie)[1]["total"] == 1
    case(root, "case-c", [doc("c1", "lease", "Contrato em Sorocaba")], name="Caio Exemplo")
    index._LAST_REFRESH.clear()  # a minute later
    assert _call(base + "/api/search?q=sorocaba", cookie=cookie)[1]["total"] == 2


def test_the_review_app_builds_a_missing_or_damaged_index_by_itself(staff_server, db, monkeypatch):
    monkeypatch.setenv("I485_WALK_EVERY", "0")  # the old pace, on purpose: a damaged file is set aside and rebuilt before the answer (as installed, the answer says it was damaged and the next search finds)
    base = staff_server
    cookie = _sign_in(base, "jane@firm.example")
    assert _call(base + "/api/search?q=sorocaba", cookie=cookie)[1]["total"] == 1
    db.write_bytes(b"not a database" * 1000)
    index._LAST_REFRESH.clear()
    assert _call(base + "/api/search?q=sorocaba", cookie=cookie)[1]["total"] == 1  # set aside, built again, answered
    db.unlink()
    index._LAST_REFRESH.clear()
    assert _call(base + "/api/search?q=sorocaba", cookie=cookie)[1]["total"] == 1


def test_the_default_place_is_next_to_the_client_folders(tmp_path, monkeypatch):
    monkeypatch.delenv("I485_INDEX", raising=False)
    assert index.default_path(tmp_path / "data" / "clients") == (tmp_path / "data" / "index.db").resolve()
    monkeypatch.setenv("I485_INDEX", str(tmp_path / "elsewhere.db"))
    assert index.default_path(tmp_path / "data" / "clients") == tmp_path / "elsewhere.db"


def test_the_index_file_is_never_committed():
    ignore = (_REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/data/" in ignore  # data/index.db lives under it


def test_the_type_list_does_not_reveal_types_that_exist_only_in_restricted_documents(staff_server):
    base = staff_server
    paralegal, attorney = _sign_in(base, "jane@firm.example"), _sign_in(base, "sam@firm.example")
    seen = {t["id"]: t["count"] for t in _call(base + "/api/search", cookie=paralegal)[1]["types"]}
    assert "affidavit" not in seen and seen == {"birth_certificate": 1, "nta": 1}
    assert {t["id"]: t["count"] for t in _call(base + "/api/search", cookie=attorney)[1]["types"]}["affidavit"] == 1


def test_plain_words_for_a_bad_limit_a_bad_date_and_a_capped_count(staff_server, root, db):
    base = staff_server
    cookie = _sign_in(base, "jane@firm.example")
    status, body, _ = _call(base + "/api/search?q=sorocaba&limit=abc", cookie=cookie)
    assert status == 400 and body["error"] == "Limit must be a number."
    status, body, _ = _call(base + "/api/search?expiring_before=soon", cookie=cookie)
    assert status == 400 and body["error"] == "Expiring before: a date like 12/31/2026 is needed"
    assert found(db, types=["nta"], expiring_before="12/31/2026")["total"] == 0  # typed as staff write it; ISO works too
    assert found(db, types=["nta"], expiring_before="2026-12-31")["total"] == 0
    assert _call(base + "/api/search?q=sorocaba", cookie=cookie)[1]["capped"] is False


def test_a_query_with_no_words_is_no_query(root, db):
    case(root, "case-a", [doc("a1", "nta", "Notice to Appear"), doc("a2", "lease", "Lease")])
    index.rebuild_all(root, db)
    assert ids(found(db, "?!", types=["nta"])) == [("case-a", "a1")]  # the filter runs
    assert found(db, "?!")["total"] == 0  # nothing asked
