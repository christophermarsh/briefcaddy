"""The people index (src/people.py, the query layer's people table): every person on every case, from a made-up world (tests/people_world.py) written
directly, never by processing documents. Everyone here is made up."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

import people
import query

sys.path.insert(0, str(Path(__file__).resolve().parent))

import people_world  # noqa: E402


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_QUERY_DB", str(tmp_path / "data" / "query.db"))
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "data" / "events.jsonl"))
    clients = tmp_path / "data" / "clients"
    cases = people_world.make(clients)
    query.rebuild_all(clients)
    return {"clients": clients, "db": tmp_path / "data" / "query.db", **cases}


def rows(db: Path, case: str) -> dict[str, dict]:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        out = {}
        for r in conn.execute("select * from people where case_id = ?", (case,)):
            r = dict(r)
            for col in ("names", "birth_dates", "a_numbers", "passports", "countries", "documents"):
                r[col] = json.loads(r[col])
            out[r["person"]] = r
        return out


def test_every_person_on_a_case_with_every_spelling_kept_as_written(world):
    ana = rows(world["db"], "case-ana")
    assert set(ana) == {"applicant", "father", "mother", "petitioner"}  # the SIJ order's parent is the father: one row
    client = ana["applicant"]
    assert client["role"] == "client" and client["relationship"] == "The client"
    names = {n["name"] for n in client["names"]}
    assert {"ANA CLARA EXEMPLO SOUZA", "Ana Clara Exemplo Sousa"} <= names  # the passport's and the questionnaire's, as each wrote it
    passport = next(n for n in client["names"] if n["name"] == "ANA CLARA EXEMPLO SOUZA" and n["given"])
    assert passport["given"] == "ANA CLARA" and passport["family"] == "EXEMPLO SOUZA"
    assert {"from": "applicant given name and applicant family name", "document": "Passport"} in passport["sources"]
    assert any(s["document"] == "Birth certificate" and s["from"] == "applicant birth certificate name" for n in client["names"] for s in n["sources"])
    assert [d["value"] for d in client["birth_dates"]] == ["2006-03-14"] and client["birth_dates"][0]["written"] == "14 MAR 2006"
    assert [p["value"] for p in client["passports"]] == ["FZ1234567"]
    assert [c["value"] for c in client["countries"]] == ["BRASIL"]
    assert {d["id"] for d in client["documents"]} == {"a1", "a2"} and client["restricted"] == 0
    mother = ana["mother"]
    assert mother["role"] == "parent" and mother["relationship"] == "Mother" and [n["name"] for n in mother["names"]] == ["MARIA EXEMPLO LIMA"]
    assert mother["birth_dates"][0]["value"] == "1984-05-02" and mother["birth_dates"][0]["sources"][0]["document"] == "The client's questionnaire"


def test_the_parent_the_sij_order_names_is_the_father_and_the_other_side(world):
    father = rows(world["db"], "case-ana")["father"]
    assert father["role"] == "adverse" and father["relationship"] == "Father"
    assert any(s["from"] == "sij parent name" for n in father["names"] for s in n["sources"])


def test_the_vawa_abuser_who_is_the_spouse_is_one_row_with_the_abusers_role(world):
    rosa = rows(world["db"], "case-rosa")
    assert set(rosa) == {"applicant", "spouse"}
    spouse = rosa["spouse"]
    assert spouse["role"] == "abuser" and spouse["relationship"] == "Spouse"
    assert spouse["birth_dates"][0]["value"] == "1979-11-30" and spouse["birth_dates"][0]["sources"][0]["document"] == "Typed on the review screen"
    assert {d["id"] for d in spouse["documents"]} == {"r2"}
    client = rosa["applicant"]
    assert client["restricted"] == 1 and [a["value"] for a in client["a_numbers"]] == ["055500111"] and [p["value"] for p in client["passports"]] == ["RX9988776"]


def test_the_case_pages_people_and_the_documents_person_tags(world):
    ana = rows(world["db"], "case-ana")
    assert ana["petitioner"]["role"] == "petitioner" and ana["petitioner"]["names"][0]["name"] == "Paulo Exemplo Tio"
    assert ana["petitioner"]["names"][0]["sources"] == [{"from": "the person's name on the case page", "document": "Recorded on the case page"}]
    bia = rows(world["db"], "case-bia")
    assert set(bia) == {"applicant", "petitioner", "child_1"}
    assert bia["petitioner"]["birth_dates"][0]["value"] == "1970-01-20" and bia["petitioner"]["documents"] == [{"id": "b1", "type": people._doc_words("us_passport")}]
    assert bia["child_1"]["role"] == "child" and bia["child_1"]["names"][0]["name"] == "LUCAS EXEMPLO LIMA" and bia["child_1"]["documents"][0]["id"] == "b2"


def test_a_case_is_rebuilt_when_its_records_change(world):
    d = world["ana"]
    graph = json.loads((d / "fact_graph.json").read_text(encoding="utf-8"))
    graph["facts"]["applicant.spouse_given_name"] = people_world.fact("applicant.spouse_given_name", people_world.source("mar.pdf", "marriage_certificate", "TIAGO"))
    graph["facts"]["applicant.spouse_family_name"] = people_world.fact("applicant.spouse_family_name", people_world.source("mar.pdf", "marriage_certificate", "NOVO EXEMPLO"))
    (d / "fact_graph.json").write_text(json.dumps(graph), encoding="utf-8")
    out = query.rebuild_changed(world["clients"])
    assert out["rebuilt"] == 1 and out["unchanged"] == 2  # only the case that changed
    assert rows(world["db"], "case-ana")["spouse"]["names"][0]["name"] == "TIAGO NOVO EXEMPLO"
    query.rebuild(d)  # and one case at a time, as the review app does after a change
    assert "spouse" in rows(world["db"], "case-ana")
    # a case folder that is gone loses its people
    import shutil

    shutil.rmtree(world["bia"])
    query.rebuild_changed(world["clients"])
    assert rows(world["db"], "case-bia") == {}


def test_a_client_added_with_only_the_conflict_check_is_in_the_index_with_the_other_side(world):
    d = world["clients"] / "nova-exemplo"
    d.mkdir()
    (d / people.FILE).write_text(json.dumps({"version": 1, "subject": {"name": "Nova Exemplo Teste", "other_names": ["Nova Teste"], "dob": "2001-02-03",
                                                                       "a_number": "", "passport": ""},
                                             "parties": [{"name": "Rui Adverso Exemplo", "dob": None, "role": "trafficker"}], "searches": [], "decision": None,
                                             "history": []}), encoding="utf-8")
    query.rebuild(d)
    got = rows(world["db"], "nova-exemplo")
    assert {n["name"] for n in got["applicant"]["names"]} == {"Nova Exemplo Teste", "Nova Teste"} and got["applicant"]["birth_dates"][0]["value"] == "2001-02-03"
    assert got["party_1"]["role"] == "trafficker" and got["party_1"]["names"][0]["name"] == "Rui Adverso Exemplo"
    assert got["party_1"]["names"][0]["sources"] == [{"from": "the conflict search", "document": "Named when the client was added"}]


def test_a_value_that_names_nobody_is_left_out(world):
    d = world["bia"]
    graph = json.loads((d / "fact_graph.json").read_text(encoding="utf-8"))
    graph["facts"]["applicant.father_given_name"] = people_world.fact("applicant.father_given_name", people_world.source("q", "intake_questionnaire", "NOT APPLICABLE"))
    graph["facts"]["applicant.father_family_name"] = people_world.fact("applicant.father_family_name", people_world.source("q", "intake_questionnaire", "NOT APPLICABLE"))
    (d / "fact_graph.json").write_text(json.dumps(graph), encoding="utf-8")
    query.rebuild(d)
    assert "father" not in rows(world["db"], "case-bia")


def test_a_name_the_reader_worked_out_is_the_name_not_its_note(world):
    """A derived name's raw value is the reader's note on how it split the name (src/assemble.py); the name kept is the value, and a raw value that is
    the same name in other capitals is kept as written."""
    d = world["bia"]
    graph = json.loads((d / "fact_graph.json").read_text(encoding="utf-8"))
    note = "client wrote 'RUI EXEMPLO PAI'. Split: EXEMPLO is a family surname"
    graph["facts"]["applicant.father_given_name"] = people_world.fact("applicant.father_given_name", people_world.source("q", "intake_questionnaire", note, "RUI"))
    graph["facts"]["applicant.father_family_name"] = people_world.fact("applicant.father_family_name", people_world.source("q", "intake_questionnaire", "Exemplo Pai", "EXEMPLO PAI"))
    (d / "fact_graph.json").write_text(json.dumps(graph), encoding="utf-8")
    query.rebuild(d)
    assert [n["name"] for n in rows(world["db"], "case-bia")["father"]["names"]] == ["RUI Exemplo Pai"]


def test_a_persons_data_is_in_the_people_table_only_and_the_file_is_owner_only(world):
    """The people table is the one place in query.db a person's own data is (the data statement and the dictionary say so)."""
    with sqlite3.connect(world["db"]) as conn:
        tables = [t for (t,) in conn.execute("select name from sqlite_master where type = 'table'")]
        assert "people" in tables
        elsewhere = "\n".join(str(v) for t in tables if t != "people" for row in conn.execute(f"select * from {t}") for v in row)
        inside = "\n".join(str(v) for row in conn.execute("select * from people") for v in row)
    for value in ("EXEMPLO SOUZA", "Sousa", "2006-03-14", "055500111", "FZ1234567", "ABUSADOR", "1979-11-30", "MARIA EXEMPLO LIMA"):
        assert value in inside and value not in elsewhere, value
    if os.name == "posix":
        assert stat.S_IMODE(world["db"].stat().st_mode) == 0o600
    assert ("people", "names") in query.PERSON_COLUMNS and "people" in query.TABLES


def test_a_case_whose_people_cannot_be_read_keeps_its_other_rows(world, monkeypatch, capsys):
    def broken(*a, **k):
        raise RuntimeError("made-up failure")
    monkeypatch.setattr(people, "rows", broken)
    query.rebuild(world["ana"])
    with sqlite3.connect(world["db"]) as conn:
        assert conn.execute("select count(*) from cases where case_id = 'case-ana'").fetchone()[0] == 1
        assert conn.execute("select count(*) from people where case_id = 'case-ana'").fetchone()[0] == 0
    assert "people index: case not indexed (RuntimeError)" in capsys.readouterr().err


# -- the people the filing questions name (the H2 verification): each typed on the review screen, as the filing questions are -------------


def _typed(d: Path, values: dict) -> None:
    log = json.loads((d / "decisions.json").read_text(encoding="utf-8")) if (d / "decisions.json").exists() else {}
    log["typed-" + str(len(log))] = {"action": "set", "values": values, "reviewer": "Sam Attorney", "note": "", "at": "2026-09-02T10:00:00-04:00",
                                     "item": {"id": "x", "kind": "answer", "level": "review", "title": "t", "group": "g", "facts": []}}
    (d / "decisions.json").write_text(json.dumps(log), encoding="utf-8")
    query.rebuild(d)


@pytest.mark.parametrize("values,tag,role,relationship,name,dob", [
    ({"uvisa.m1_given_name": "Ulisses", "uvisa.m1_family_name": "Perpetrador Exemplo", "uvisa.m1_dob": "1975-04-05", "uvisa.m1_relationship": "Spouse",
      "uvisa.m1_perpetrator": "Yes"}, "uvisa_member_1", "adverse", "Spouse", "Ulisses Perpetrador Exemplo", "1975-04-05"),
    ({"uvisa.m2_given_name": "Ursula", "uvisa.m2_family_name": "Familia Exemplo", "uvisa.m2_perpetrator": "No"}, "uvisa_member_2", "relative",
     "A family member on the U visa", "Ursula Familia Exemplo", None),
    ({"parole.b1_given_name": "Bento", "parole.b1_family_name": "Beneficiario Exemplo", "parole.b1_dob": "2001-06-07", "parole.b1_relationship": "brother"},
     "parole_beneficiary_1", "beneficiary", "brother", "Bento Beneficiario Exemplo", "2001-06-07"),
    ({"tvisa.m1.given_name": "Tereza", "tvisa.m1.family_name": "Familia Exemplo", "tvisa.m1.dob": "1999-08-09"}, "tvisa_member_1", "relative",
     "A family member on the T visa", "Tereza Familia Exemplo", "1999-08-09"),
    ({"n565.official_given_name": "Otavio", "n565.official_family_name": "Oficial Exemplo"}, "n565_official", "other",
     "The foreign official the special certificate is for", "Otavio Oficial Exemplo", None),
    ({"petitioner.prior_spouse1_given_name": "Priscila", "petitioner.prior_spouse1_family_name": "Antiga Exemplo"}, "petitioner_former_spouse_1", "relative",
     "The petitioner's former spouse", "Priscila Antiga Exemplo", None),
])
def test_the_people_the_filing_questions_name_are_in_the_index_and_found(world, values, tag, role, relationship, name, dob):
    import conflicts

    _typed(world["bia"], values)
    row = rows(world["db"], "case-bia")[tag]
    assert (row["role"], row["relationship"], row["names"][0]["name"]) == (role, relationship, name)
    assert [d["value"] for d in row["birth_dates"]] == ([dob] if dob else [])
    found = conflicts.search(world["clients"], {"name": name, "dob": dob or ""}, by="Sam Attorney", role="attorney", purpose="hand", log=False)
    assert [(h["case"], h["person"]) for h in found["hits"]] == [("case-bia", tag)] and (found["hits"][0]["adverse"] is (role == "adverse"))


def test_the_clients_other_names_on_the_g639_and_the_i90_card(world):
    _typed(world["bia"], {"g639.other_given_name": "Bea", "g639.other_family_name": "Exemplo Antiga", "i90.card_given_name": "Beatriz",
                          "i90.card_family_name": "Exemplo Cartao"})
    names = {n["name"] for n in rows(world["db"], "case-bia")["applicant"]["names"]}
    assert {"Bea Exemplo Antiga", "Beatriz Exemplo Cartao"} <= names


def test_more_of_the_family_the_questionnaire_and_the_documents_name(world):
    d = world["bia"]
    graph = json.loads((d / "fact_graph.json").read_text(encoding="utf-8"))
    src = people_world.source
    for key, s in {"questionnaire.spouse_name": src("q", "intake_questionnaire", "Sergio Esposo Exemplo"),
                   "questionnaire.child1_dob": src("q", "intake_questionnaire", "05/06/2015", "2015-05-06"),
                   "applicant.child1_a_number": src("q", "intake_questionnaire", "A011122233"),
                   "applicant.father_given_name": src("cert.pdf", "birth_certificate", "PEDRO"),
                   "applicant.father_middle_name": src("cert.pdf", "birth_certificate", "PAULO"),
                   "applicant.father_family_name": src("cert.pdf", "birth_certificate", "EXEMPLO LIMA"),
                   "applicant.birth_cert.parent_a_name": src("cert.pdf", "birth_certificate", "PEDRO PAULO EXEMPLO LIMA"),
                   "applicant.prior_spouse_given_name": src("q", "intake_questionnaire", "Rui"),
                   "applicant.prior_spouse_family_name": src("q", "intake_questionnaire", "Antigo Exemplo"),
                   "applicant.prior_spouse_dob": src("q", "intake_questionnaire", "1980-01-01"),
                   "applicant.country_of_birth": src("cert.pdf", "birth_certificate", "BIRTH CERTIFICATE BORN IN SOROCABA SAO PAULO", "BRASIL")}.items():
        graph["facts"][key] = people_world.fact(key, s)
    (d / "fact_graph.json").write_text(json.dumps(graph), encoding="utf-8")
    query.rebuild(d)
    got = rows(world["db"], "case-bia")
    assert got["spouse"]["names"][0]["name"] == "Sergio Esposo Exemplo"
    assert got["child_1"]["birth_dates"][0]["value"] == "2015-05-06" and got["child_1"]["a_numbers"][0]["value"] == "011122233"
    assert "parent_a" not in got and {n["name"] for n in got["father"]["names"]} >= {"PEDRO PAULO EXEMPLO LIMA"}  # one row: the birth certificate's parent is the father
    assert got["former_spouse"]["birth_dates"][0]["value"] == "1980-01-01"
    assert [c["value"] for c in got["applicant"]["countries"]] == ["BRASIL"]  # the reader's note is not a country
