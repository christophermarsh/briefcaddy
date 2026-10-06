"""The document-type classifier trained here (src/learning/textcat.py) on
invented documents (src/learning/synthetic.py), and its place in shadow mode."""

import json
import random
import re

import pytest

from learning import report, shadow, synthetic, textcat
from learning.store import connect
import schema_path


def test_every_type_is_written_and_the_same_seed_gives_the_same_documents():
    docs = synthetic.generate(5, seed=3)
    assert {k for _, k in docs} == set(synthetic.WRITERS) and all(t.strip() for t, _ in docs)
    assert docs == synthetic.generate(5, seed=3) and docs != synthetic.generate(5, seed=4)


def test_the_values_are_invented():
    fakes = [synthetic.Faker(random.Random(i)) for i in range(50)]
    ssns = re.findall(r"\b\d{3}-\d{2}-\d{4}\b", "\n".join("\n".join(synthetic.ssn_card(f)) for f in fakes))
    assert ssns and all(s.startswith("9") and s[4] == "0" for s in ssns)  # 9xx-0x-: never issued
    names = {w for f in fakes for w in f"{f.given} {f.surname} {f.mother} {f.father}".split()}
    assert names <= set(synthetic.FIRST) | set(synthetic.LAST)  # common names only, never a client's


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    path = tmp_path_factory.mktemp("models") / "document_type.pkl"
    card = textcat.train(path, n_per_type=40, seed=0)
    return path, card


def test_trained_model_reads_clean_documents_and_says_what_trained_it(model):
    path, card = model
    assert card["data"] == synthetic.VERSION and "no client documents" in card["trained_on"]
    assert json.loads(path.with_suffix(".json").read_text())["version"] == card["version"]
    kind, p, secs = textcat.document_type("SOCIAL SECURITY\n966-04-1234\nANA SOUZA\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION", path)
    assert kind == "ssn_card" and 0 < p <= 1 and secs < 1
    assert textcat.document_type("NOTICE TO APPEAR\nIn removal proceedings under section 240 of the Immigration and Nationality Act", path)[0] == "notice_to_appear"


def test_an_untrained_model_says_how_to_train_it(tmp_path):
    with pytest.raises(FileNotFoundError, match="textcat.py train"):
        textcat.document_type("anything", tmp_path / "none.pkl")


def _settings(tmp_path, model_path, threshold=0.6):
    path = tmp_path / "learning.json"
    path.write_text(json.dumps({"shadow": {"document_type_textcat": {"enabled": True, "path": str(model_path), "threshold": threshold}}}))
    return path


def test_the_classifier_answers_in_shadow_mode_beside_the_rules(tmp_path, model, monkeypatch):
    monkeypatch.setenv("I485_SHADOW", "1")
    from types import SimpleNamespace

    docs = [("ssn.pdf", "SOCIAL SECURITY 966-04-1234 VALID FOR WORK ONLY WITH DHS AUTHORIZATION"), ("q.pdf", "Questionario")]
    classes = {"ssn.pdf": SimpleNamespace(doc_type="ssn_card"), "q.pdf": SimpleNamespace(doc_type="intake_questionnaire")}
    assert shadow.observe_document_types("c1", docs, classes, db_path=tmp_path / "l.db", settings_path=_settings(tmp_path, model[0])) == 1
    [row] = connect(tmp_path / "l.db").execute("select model, wording, answer, mode from model_runs").fetchall()
    assert (row["model"], row["wording"], row["answer"], row["mode"]) == ("textcat", model[1]["version"], "ssn_card", "shadow")


def test_a_missing_model_file_costs_one_error_row(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_SHADOW", "1")
    n = shadow.observe_document_types("c1", [("a.pdf", "text"), ("b.pdf", "more")], {}, db_path=tmp_path / "l.db",
                                      settings_path=_settings(tmp_path, tmp_path / "missing.pkl"))
    rows = connect(tmp_path / "l.db").execute("select wording, error from model_runs").fetchall()
    assert n == 0 and len(rows) == 1 and rows[0]["wording"] == "untrained" and "textcat.py train" in rows[0]["error"]


def test_each_model_is_judged_at_its_own_threshold(tmp_path, monkeypatch):
    from learning.store import record_run

    monkeypatch.setattr(report, "thresholds", lambda: {"textcat": 0.6, "nimble:x": 0.9})
    db = connect(tmp_path / "l.db")
    for model in ("textcat", "nimble:x"):
        record_run(db, client="c1", doc="a.pdf", task="document_type", model=model, wording="w", rules="unclassified", answer="passport",
                   probability=0.7, ms=1)
    db.close()
    got = {m["model"]: (m["rescued"], m["unsure"], m["threshold"]) for m in report.document_type_report(tmp_path / "l.db")["models"]}
    assert got == {"textcat": (1, 0, 0.6), "nimble:x": (0, 1, 0.9)}


# --- public specimens (schemas/registers/training_sources.json, tools/fetch_specimens.py) ------------

def test_every_specimen_source_says_what_it_is_and_why_it_may_be_used():

    sources = json.loads((schema_path.path("register", "training_sources")).read_text(encoding="utf-8"))["sources"]
    assert len({s["id"] for s in sources}) == len(sources) > 50
    for s in sources:
        assert s["type"] in synthetic.WRITERS and s["url"].startswith("https://") and s["license"] and s["from"], s["id"]
        assert s.get("pages") != [], f"{s['id']}: choose its pages"


def test_specimens_unreadable_or_unused_pages_are_left_out_and_copies_are_capped(tmp_path):
    path = tmp_path / "specimens.json"
    path.write_text(json.dumps([
        {"id": "ead", "type": "work_permit", "page": 1, "used": True, "text": "EMPLOYMENT AUTHORIZATION CARD\nSurname\nCard Expires\nCategory C14"},
        {"id": "mrz", "type": "passport", "page": 1, "used": True, "text": "~~ .. ° ¢\nP<BRAEXEMPLO<<ANA<<<<<<<<<"},  # little text, but a passport MRZ line
        {"id": "noise", "type": "visa", "page": 1, "used": True, "text": "~ . ° ¢ ae Sh :: ‘ oe -"},  # nothing read: teaches nothing
        {"id": "unchosen", "type": "birth_certificate", "page": 3, "used": False, "text": "CERTIDAO DE NASCIMENTO REGISTRO CIVIL"},
        {"id": "green", "type": "permanent_resident_card", "page": 1, "used": True, "text": "PERMANENT RESIDENT CARD"}]))
    pages = textcat.specimens(path)
    assert [sid for _, _, sid in pages] == ["ead", "mrz"]
    copies = textcat.specimen_copies(pages, n_per_type=600)
    assert sum(k == "work_permit" for _, k in copies) == 1 + 20  # as read + at most 20 damaged copies
    many = textcat.specimen_copies(pages[:1] * 50, n_per_type=600)  # 50 pages of a type share a quarter of 600: 3 copies each
    assert len(many) == 50 * (1 + 3)


# --- every client country (learning/synthetic_countries.py) and the rules that read them -------

def test_every_country_writes_its_own_documents_with_real_places():
    from extract import geo
    from learning.synthetic_countries import PROFILES, Person, birth_record, marriage_record, national_id, passport_page

    for iso in PROFILES:
        p = Person(random.Random(1), iso)
        assert p.region in p.regions and (iso == "BR" or p.region in geo.regions_for_place(iso, p.city))
        for write in (marriage_record, national_id, passport_page):
            assert write(p), (iso, write.__name__)
        text = "\n".join(passport_page(Person(random.Random(2), iso)))
        assert f"<{PROFILES[iso][0]}" in text  # the country's own MRZ code
        if iso != "BR":
            assert birth_record(Person(random.Random(3), iso))


def test_no_transliterated_place_names():
    from learning.synthetic_countries import _places

    for iso in ("GY", "EC", "HT"):
        names = {place for place, _ in _places(iso)[0]}
        assert "DZHORDZHTAUN" not in names and "AO TA WA LUO" not in names
        assert {"GEORGETOWN", "OTAVALO", "JACMEL"} & names


def test_the_rules_read_other_countries_registries():
    from classify import classify_text

    peru = "REGISTRO NACIONAL DE IDENTIFICACION Y ESTADO CIVIL\nACTA DE NACIMIENTO\nFECHA DE NACIMIENTO\nDATOS DE LOS PADRES\nREGISTRADOR CIVIL"
    assert classify_text(peru).doc_type == "birth_certificate"
    assert classify_text("RENIEC\nACTA DE MATRIMONIO\nFECHA DE CELEBRACIÓN\nREGISTRADOR CIVIL").doc_type == "marriage_certificate"
    assert classify_text("RENIEC\nACTA DE DEFUNCIÓN\nREGISTRADOR CIVIL").doc_type == "unclassified"  # not a type we file
    # an unverified title alone stays below the line; with its registry named, it's placed
    assert classify_text("ACTE DE NAISSANCE").doc_type == "unclassified"
    assert classify_text("EXTRAIT DES REGISTRES DE L'ETAT CIVIL\nACTE DE NAISSANCE").doc_type == "birth_certificate"
    assert classify_text("REPUBLIQUE D'HAITI\nPASSEPORT\nP<HTIDESIR<<LOUIS<<<<<<<<<<").doc_type == "passport"


def test_a_brazilian_identity_card_is_not_a_birth_certificate():
    from classify import classify_text

    rg = "REPUBLICA FEDERATIVA DO BRASIL\nCARTEIRA DE IDENTIDADE\nREGISTRO GERAL\nFILIACAO\nJOSE SILVA\nDATA DE NASCIMENTO\n01/02/2005"
    assert classify_text(rg).doc_type != "birth_certificate"
