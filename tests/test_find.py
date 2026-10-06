"""Find across the firm (src/find.py, brief N1): the firm-wide index of meaning.

The acceptance tests the brief names: the name probe (a made-up client's name finds nothing because the text was masked, and finds the sentence
against an unmasked copy), the restricted-case canary (a sentence only a restricted case holds is never a hit, a count or a hint for a paralegal
not named on it, and is a hit for the attorney), rebuild-matches, the switch off refuses the route, and the follower re-indexes a changed page
once. Then what the attorney, the paralegal and the client require: attorney-only notes, deletion at once, the file's protection, no client's
export carrying it, the office's translation found beside the client's words, one line of why and a place to open per hit.

Every test uses the deterministic hashing embedder (conftest: I485_FIND_EMBEDDER=hashing). Everyone here is made up.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
from datetime import datetime
from pathlib import Path

import pytest

import case_notes
import clock
import engagement
import events
import find
import records
import restricted
import settings

sys.path.insert(0, str(Path(__file__).resolve().parent))
import firm_world  # noqa: E402
from test_restricted import app, call, doc, make_case, server, sign_in, world  # noqa: E402,F401 -- the restricted cases' world and its review app

CANARY = "A blue heron nested behind the laundromat while the hearing was postponed for the interpreter"
QUESTION = "heron nested behind the laundromat"
REPO = Path(__file__).resolve().parent.parent


def search(db: Path, question: str, **kw) -> list[dict]:
    return find.search(question, db_path=db, **kw)["results"]


@pytest.fixture
def switched_on():
    settings.save(*find.SETTING[:1], {find.SETTING[1]: "on"}, "Sam Attorney")
    yield
    settings.save(*find.SETTING[:1], {find.SETTING[1]: "off"}, "Sam Attorney")


@pytest.fixture
def canary_world(world):  # noqa: F811
    """The restricted world (tests/test_restricted.py), with the canary sentence only in the VAWA case and a sentence of its own in Ana's, and the index built."""
    for case, line in (("case-rosa", CANARY), ("case-ana", "The interpreter for the asylum hearing was a Portuguese speaker from the court")):
        path = world / case / "documents.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["documents"][0]["text"] = line
        path.write_text(json.dumps(data), encoding="utf-8")
    find.rebuild_all(world)
    return world


# -- the name probe -----------------------------------------------------------------------------------------------------------------------


def test_the_name_probe_a_clients_name_finds_nothing_because_the_text_was_masked(tmp_path, monkeypatch):
    root = tmp_path / "data" / "clients"
    name = "Zelinda Exemplar Quaresmo"
    make_case(root, "case-zq", name, [doc("z1", "declaration", text=f"My name is {name} and I left my village after my uncle threatened me. "
                                                                        "Mãe: Joaquina Exemplar. I was born 03/14/2008, A-Number A-212 345 678.")])
    make_case(root, "case-other", "Ana Exemplo Souza", [doc("o1", "letter", text="The school wrote that the boy attends every day and studies English.")])
    db = tmp_path / "data" / "find.db"
    find.rebuild_all(root, db)
    with sqlite3.connect(db) as con:
        stored = " ".join(r[0] for r in con.execute("select text from passages")).lower()
    assert stored and all(w not in stored for w in ("zelinda", "quaresmo", "exemplar", "joaquina", "212 345", "2008"))  # never in a vector's source text
    assert "[name]" in stored and "[date]" in stored and "[number]" in stored
    assert search(db, name) == [] and search(db, "Zelinda") == [] and search(db, "Quaresmo") == []
    assert [h["case"] for h in search(db, "uncle threatened village")] == ["case-zq"]  # the meaning is still found
    # the same question against an unmasked copy: the mask is what stops it
    monkeypatch.setattr(find.Masker, "__call__", lambda self, text: str(text or ""))
    raw = tmp_path / "data" / "raw.db"
    find.rebuild_all(root, raw)
    hits = search(raw, name)
    assert hits and hits[0]["case"] == "case-zq" and name in hits[0]["text"]


def test_masking_takes_out_numbers_dates_addresses_phones_and_labelled_names_whatever_the_case_knows():
    m = find.Masker(["Rosa Exemplo"])
    text = m("ROSA EXEMPLO lives at 45 Elm Street Apt 3, Boston MA 02134, phone (617) 555-0142, rosa@example.com. Born 7 de março de 2008. "
             "Receipt IOE0912345678, passport YA1234567. Nome do pai: Joaquim Exemplo. Rua das Flores, 123.")
    for leaked in ("ROSA", "EXEMPLO", "Elm", "02134", "555", "rosa@", "2008", "IOE09", "YA123", "Joaquim", "Flores"):
        assert leaked not in text, (leaked, text)
    assert "Boston" in text and "lives at" in text  # what is not a person's data stays, so the meaning does


# -- the restricted-case canary --------------------------------------------------------------------------------------------------------------


def test_the_restricted_case_canary_is_never_a_hit_for_a_paralegal_not_named_on_it_and_is_for_the_attorney(server, canary_world, switched_on):  # noqa: F811
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    status, text = call(server + "/api/find", jane, {"question": QUESTION})
    assert status == 200, text
    body = json.loads(text)
    assert all(h["case"] != "case-rosa" for h in body["results"])
    shown = text.replace(json.dumps(QUESTION), "")  # the question comes back as asked
    for secret in ("heron", "laundromat", "case-rosa", "Rosa", "ROSA", "restricted", "hidden", "more"):
        assert secret not in shown, secret  # no hit, no count, no hint ("and 2 more you may not open")
    assert set(body) == {"question", "results", "built"}
    status, text = call(server + "/api/find", sam, {"question": QUESTION})
    hits = json.loads(text)["results"]
    assert status == 200 and hits[0]["case"] == "case-rosa" and "heron" in hits[0]["text"] and hits[0]["page"] == 1
    # the case the hit opens is the gate's: the same 404 a made-up id gets, for her; the case itself, for the attorney
    hidden, made_up = call(server + "/api/case-notes?client=case-rosa", jane), call(server + "/api/case-notes?client=case-zzzz", jane)
    assert hidden == made_up and hidden[0] == 404
    assert call(server + "/api/case-notes?client=case-rosa", sam)[0] == 200
    # named on the case by an attorney, she finds it
    restricted.name_person(canary_world / "case-rosa", "jane@firm.example", True, "Sam Attorney", "attorney", "Jane Doe")
    status, text = call(server + "/api/find", jane, {"question": QUESTION})
    assert status == 200 and json.loads(text)["results"][0]["case"] == "case-rosa"


def test_a_confidential_document_in_a_case_she_may_open_is_not_a_hit_unless_she_is_named(canary_world):
    db = find.default_path(canary_world)
    rows = search(db, QUESTION)
    assert rows and rows[0]["case"] == "case-rosa"  # the attorney (no gate)
    assert search(db, QUESTION, confidential_cases=set()) == []  # the VAWA case's documents are confidential by law: blanked before the ranking
    assert search(db, QUESTION, confidential_cases={"case-rosa"})[0]["case"] == "case-rosa"


def test_each_question_is_a_ledger_row_with_its_length_and_its_text_is_for_the_attorney_only(server, canary_world, switched_on):  # noqa: F811
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    question = "who postponed the hearing for the interpreter"
    assert call(server + "/api/find", jane, {"question": question})[0] == 200
    rows = [r for r in events.rows(events.base_path(canary_world.parent)) if r["kind"] == "find"]
    assert rows[-1]["who"] == "Jane Doe" and rows[-1]["role"] == "paralegal" and str(len(question)) in rows[-1]["what"] and "postponed" not in rows[-1]["what"]
    status, text = call(server + "/api/find", jane)
    assert status == 200 and "asked" not in json.loads(text) and "postponed" not in text  # the questions' text is the attorney's
    status, text = call(server + "/api/find", sam)
    asked = json.loads(text)["asked"]
    assert asked[0]["question"] == question and asked[0]["who"] == "Jane Doe" and asked[0]["length"] == len(question)
    for route in ("/api/reports", "/api/firm_events", "/api/firm_events.csv"):  # never on Reports, nor in the ledger the screens read
        status, text = call(server + route, sam)
        assert status == 200 and "postponed" not in text, route
    assert stat.S_IMODE(os.stat(find.questions_path(canary_world)).st_mode) == 0o600


# -- rebuild, the switch, the follower -------------------------------------------------------------------------------------------------------


def test_a_rebuilt_index_returns_the_same_hits(canary_world, tmp_path):
    db = find.default_path(canary_world)
    questions = [QUESTION, "interpreter at the hearing", "passport", "declaration of abuse"]
    before = [[(h["case"], h["kind"], h["ref"], h["page"], h["text"], h["score"]) for h in search(db, q)] for q in questions]
    assert any(before)
    db.unlink()
    find.rebuild_all(canary_world)
    after = [[(h["case"], h["kind"], h["ref"], h["page"], h["text"], h["score"]) for h in search(db, q)] for q in questions]
    assert after == before
    other = tmp_path / "elsewhere.db"
    find.rebuild_all(canary_world, other)  # from scratch into another file: the same answers
    assert [[(h["case"], h["ref"], h["text"]) for h in search(other, q)] for q in questions] == [[(x[0], x[2], x[4]) for x in b] for b in before]


def test_switched_off_the_route_refuses_and_the_attorney_switches_it_off_in_one_request(server, canary_world, switched_on):  # noqa: F811
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    assert call(server + "/api/find", sam, {"question": QUESTION})[0] == 200
    status, text = call(server + "/api/settings", jane, {"section": "drafting", "values": {"find_across": "off"}})
    assert status == 403  # a paralegal cannot switch it
    view = json.loads(call(server + "/api/find", jane)[1])
    assert view["on"] is True and view["can_switch"] is False
    assert json.loads(call(server + "/api/find", sam)[1])["can_switch"] is True
    status, _ = call(server + "/api/settings", sam, {"section": "drafting", "values": {"find_across": "off"}})  # the Search page's one button sends this
    assert status == 200
    for who in (jane, sam):
        status, text = call(server + "/api/find", who, {"question": QUESTION})
        assert status == 403 and "switched off" in text and "heron" not in text
    assert json.loads(call(server + "/api/find", sam)[1])["on"] is False


def test_the_follower_reindexes_a_changed_page_once(canary_world, monkeypatch):
    monkeypatch.setenv("I485_WALK_EVERY", "600")
    db = find.default_path(canary_world)
    find._FOLLOWERS.clear()
    built: list[str] = []
    real = find.rebuild
    monkeypatch.setattr(find, "rebuild", lambda client_dir, db_path=None, model=None: built.append(Path(client_dir).name) or real(client_dir, db_path, model))
    find.keep_up(canary_world, db)
    follower = find.follower(canary_world, db)
    follower.wait()
    walks = follower.walks
    path = canary_world / "case-ana" / "documents.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["documents"][0]["text"] = "The landlord returned the security deposit after the inspection of the apartment"
    path.write_text(json.dumps(data), encoding="utf-8")
    events.record("documents", "changed", "Changed a document", case_dir=canary_world / "case-ana", who="Jane Doe")
    find.keep_up(canary_world, db)
    follower.wait()
    find.keep_up(canary_world, db)  # nothing new in the ledger: nothing built again
    follower.wait()
    assert built == ["case-ana"] and follower.walks == walks  # once, and only that case: no walk over every case
    assert [h["case"] for h in search(db, "landlord security deposit inspection")] == ["case-ana"]
    assert search(db, "interpreter Portuguese speaker court") == [] or all(h["ref"] != "a1" for h in search(db, "interpreter Portuguese speaker court"))
    find._FOLLOWERS.clear()


# -- privilege, deletion, the client's export -------------------------------------------------------------------------------------------------


def test_an_attorney_only_note_is_a_hit_for_attorneys_only(server, canary_world, switched_on):  # noqa: F811
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    ana = canary_world / "case-ana"
    status, text = call(server + "/api/case-notes", jane, {"client": "case-ana", "action": "note", "text": "the strategy memo", "attorney_only": True})
    assert status == 403  # only an attorney marks a note for attorneys only
    status, _ = call(server + "/api/case-notes", sam, {"client": "case-ana", "action": "note", "attorney_only": True,
                                                       "text": "Strategy: the uncle's affidavit is weak; ask the cousin for a second statement before the hearing"})
    assert status == 200
    case_notes.add_note(ana, "The client prefers calls after five in the evening", "Jane Doe", "paralegal")
    find.rebuild_all(canary_world)
    status, text = call(server + "/api/find", jane, {"question": "affidavit cousin second statement"})
    assert status == 200 and json.loads(text)["results"] == []  # not a hit, and nothing says there is one
    assert "affidavit" not in call(server + "/api/case-notes?client=case-ana", jane)[1]  # nor on the case page
    status, text = call(server + "/api/find", sam, {"question": "affidavit cousin second statement"})
    hit = json.loads(text)["results"][0]
    assert hit["kind"] == "note" and "affidavit" in hit["text"] and hit["case"] == "case-ana"
    assert "calls after five" in call(server + "/api/find", jane, {"question": "client prefers calls evening"})[1]  # an ordinary note is everyone's who may open the case


def test_a_case_whose_folder_is_taken_away_leaves_the_index_at_the_next_question_not_the_next_build(canary_world):
    import shutil

    db = find.default_path(canary_world)
    assert search(db, "interpreter Portuguese speaker court")[0]["case"] == "case-ana"
    shutil.rmtree(canary_world / "case-ana")
    present = lambda case: (canary_world / case).is_dir()  # noqa: E731 -- what the review app passes
    assert all(h["case"] != "case-ana" for h in search(db, "interpreter Portuguese speaker court", present=present))
    with sqlite3.connect(db) as con:
        assert con.execute("select count(*) from passages where case_id = 'case-ana'").fetchone()[0] == 0


def test_a_case_recorded_destroyed_leaves_the_index_at_once(tmp_path, monkeypatch):
    from rules import approval

    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    approval.approve(engagement.PRACTICE_ID, "Sam Attorney", "attorney")
    clients = tmp_path / "clients"
    d = firm_world.make_case(clients, "case-ana")
    case_notes.add_note(d, "The cousin will bring the school records on Tuesday", "Jane Doe", "paralegal")
    db = find.default_path(clients)
    find.rebuild_all(clients)
    assert search(db, "cousin school records")
    settings.save("firm", {"office.retention_years": "1"}, "Sam Attorney")
    engagement.end(d, "closed", "Sam Attorney", "attorney", reason="Done.")
    monkeypatch.setattr(clock, "_now_override", datetime(2027, 10, 6, 9, 0))
    engagement.mark_destroyed(clients, "case-ana", "Sam Attorney", "attorney", folder_removed=False)
    with sqlite3.connect(db) as con:  # at once: before any build
        assert con.execute("select count(*) from passages where case_id = 'case-ana'").fetchone()[0] == 0
    find.rebuild_changed(clients)  # and the next build does not bring it back (the folder is still there)
    assert search(db, "cousin school records") == []


def test_the_file_is_owner_only_backed_up_catalogued_and_in_no_export(canary_world, tmp_path):
    import backups

    db = find.default_path(canary_world)
    assert stat.S_IMODE(os.stat(db).st_mode) == 0o600
    items = backups.plan(backups.sources(canary_world.parent), [], [])
    assert any(i.arcname == "data/find.db" and i.role == "find" for i in items)
    assert any(d["id"] == "find" and d["where"] == "data/find.db" and not d["exported"] for d in records.DATABASES)
    assert records.coverage("firm", "find.db") == "never" and records.coverage("firm", "find_questions.jsonl") == "listed"
    # the client's own file (one client's export: the case's records and documents) carries no index
    sys.path.append(str(REPO / "tools"))
    import export_firm

    args = argparse.Namespace(all=False, client="case-ana", data=canary_world, portal=canary_world.parent / "portal", firm_files=False)
    entries = export_firm.gather(args)[0]
    names = [str(getattr(e, "arcname", e)) for e in entries]
    assert names and not any("find" in n for n in names)


# -- what a hit is ------------------------------------------------------------------------------------------------------------------------------


def test_the_offices_translation_is_found_beside_the_clients_creole_words(tmp_path):
    root = tmp_path / "data" / "clients"
    creole = doc("h1", "declaration", text="Mwen te kite peyi a paske gang yo te menase fanmi mwen")
    creole["language"], creole["translated"] = "ht", "I left the country because the gangs threatened my family"
    make_case(root, "case-ht", "Lucien Exemplo", [creole])
    db = tmp_path / "data" / "find.db"
    find.rebuild_all(root, db)
    hits = search(db, "gangs threatened family")
    assert hits[0]["kind"] == "translation" and hits[0]["case"] == "case-ht" and hits[0]["ref"] == "h1"
    assert search(db, "gang menase fanmi")[0]["kind"] == "page"  # the client's own words, beside it


def test_twenty_hits_at_most_newest_case_first_each_with_a_passage_a_place_and_why(tmp_path):
    root = tmp_path / "data" / "clients"
    for n in range(25):
        d = doc(f"d{n}", "letter", text="The school confirmed enrollment for the fall semester")
        d["added"] = f"2026-0{1 + n % 9}-{10 + n:02d}T10:00:00+00:00"
        make_case(root, f"case-{n:02d}", f"Pessoa Exemplo{n}", [d])
    long = doc("long", "declaration", text=("The school confirmed enrollment. " * 40))
    make_case(root, "case-long", "Longa Exemplo", [long])
    db = tmp_path / "data" / "find.db"
    find.rebuild_all(root, db)
    hits = search(db, "school confirmed enrollment fall semester")
    assert len(hits) == find.TOP
    equal = [h for h in hits if h["score"] == hits[0]["score"]]
    opened = [json.loads((root / h["case"] / "documents.json").read_text())["documents"][0]["added"] for h in equal]
    assert opened == sorted(opened, reverse=True) and len(equal) > 1  # among equal scores, the newest case first
    for h in hits + search(db, "school confirmed enrollment"):
        assert len(h["text"]) <= find.PASSAGE and h["case"] and h["page"] == 1 and h["why"].startswith("Close in meaning") and "school" in h["why"]


def test_the_server_names_each_hits_case_and_says_when_the_index_is_still_being_built(server, canary_world, switched_on):  # noqa: F811
    sam = sign_in(server, "sam@firm.example")
    status, text = call(server + "/api/find", sam, {"question": "interpreter Portuguese speaker court"})
    hit = json.loads(text)["results"][0]
    assert status == 200 and hit["case"] == "case-ana" and hit["name"] and hit["file"] == "a1.pdf" and hit["why"]
    assert call(server + "/api/find", sam, {"question": "   "})[0] == 400
    assert call(server + "/api/find", sam, {"question": "x" * (find.MAX_QUESTION + 1)})[0] == 400
    find.default_path(canary_world).unlink()
    status, text = call(server + "/api/find", sam, {"question": "interpreter"})
    assert status == 200 and json.loads(text)["built"] is False  # the first build runs in the background; the page says so
    find.follower(canary_world).wait()
    find._FOLLOWERS.clear()


def test_the_overnight_step_indexes_only_when_switched_on(canary_world):
    import overnight

    assert overnight.find_index(canary_world) == "Find across the firm: switched off, nothing indexed."
    settings.save("drafting", {"find_across": "on"}, "Sam Attorney")
    try:
        assert overnight.find_index(canary_world).startswith("Find across the firm: 0 case(s) indexed again")
    finally:
        settings.save("drafting", {"find_across": "off"}, "Sam Attorney")


def test_the_ollama_embedder_speaks_to_the_firms_own_machine_only(monkeypatch):
    from vision import ollama

    sent = {}

    def fake(url, payload, timeout):
        sent.update(url=url, payload=payload)
        return {"embeddings": [[3.0, 4.0]] * len(payload["input"])}

    monkeypatch.setattr(ollama, "_post_http", fake)
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    e = find.OllamaEmbedder("qwen3-embedding:0.6b")
    v = e.question("where is the hearing")
    assert sent["url"] == "http://localhost:11434/api/embed" and sent["payload"]["input"][0].startswith("Instruct:") and abs(float(v @ v) - 1) < 1e-6
    assert e.passages(["a", "b"]).shape == (2, 2) and sent["payload"]["input"] == ["a", "b"]  # a passage goes as it is
    monkeypatch.setattr(ollama, "_post_http", lambda *a: (_ for _ in ()).throw(ConnectionRefusedError()))
    monkeypatch.setattr(ollama, "_is_wsl", lambda: False)
    with pytest.raises(find.ModelUnavailable):
        e.question("anything")
