"""Short form names for staff (schemas/registers/document_types.json "short_name"): a type whose name is longer than 40 characters has a short one beside it, used where a staff screen
lists a document in a column, with the long name on hover; the long name stays in the review bundle and the classifier never reads the short one. Everyone here is made up."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import documents
import index
from classify import patterns
import schema_path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
TAXONOMY = json.loads((schema_path.path("register", "document_types")).read_text(encoding="utf-8"))
LIMIT = 40


def test_every_name_over_forty_characters_has_a_short_one_that_fits_and_no_other_type_has_one():
    long = [t for t in TAXONOMY["types"] if len(t["name"]) > LIMIT]
    assert len(long) == 25
    for t in long:
        short = t.get("short_name")
        assert short and len(short) <= LIMIT and short != t["name"], t["id"]
        assert "—" not in short and " -- " not in short and "_" not in short, t["id"]
    assert not [t["id"] for t in TAXONOMY["types"] if len(t["name"]) <= LIMIT and "short_name" in t]
    # a short name says the same thing: it keeps the form's number (I-765, N-648 ...) the long one has
    for t in long:
        form = re.search(r"[A-Z]-\d+[A-Z]?", t["name"])
        if form:
            assert form.group(0) in t["short_name"], t["id"]
    # the two I-693 types are told apart in a column: an opened one is not fileable, so the short name says so
    short = {t["id"]: t["short_name"] for t in long}
    assert "sealed" in short["i693_envelope"] and "opened" in short["i693"]
    # the file is still exactly what it re-dumps to
    raw = (schema_path.path("register", "document_types")).read_text(encoding="utf-8")
    assert raw == json.dumps(TAXONOMY, indent=2, ensure_ascii=False) + "\n"


def test_the_long_name_is_what_everything_but_a_column_uses_and_the_classifier_never_reads_the_short_one():
    for t in TAXONOMY["types"]:
        assert documents.name(t["id"]) == t["name"] == patterns.NAMES[t["id"]] == patterns.name(t["id"]), t["id"]
        assert documents.short_name(t["id"]) == t.get("short_name", t["name"])
        assert index.type_name(t["id"]) == t["name"] and index.type_short(t["id"]) == t.get("short_name", t["name"])
    assert documents.name("n648") == "Form N-648 (medical certification for a disability exception)" and documents.short_name("n648") == "Form N-648 (disability exception)"
    assert documents.short_name("passport") == "Passport" and documents.short_name("no_such_type") == "No such type"
    # the classifier and the review bundle never name a short name
    for path in ("src/classify/classifier.py", "src/review/bundle.py", "src/learning/decision.py"):
        assert "short_name" not in (REPO / path).read_text(encoding="utf-8"), path


def test_a_column_gets_the_short_name_and_the_long_one_comes_with_it_for_the_hover(tmp_path, monkeypatch):
    import firm_world
    import query
    from review import reports

    clients = tmp_path / "clients"
    clients.mkdir()
    d = firm_world.make_case(clients, "case-ana")
    built = documents.build(d / "source", {"n648-0.pdf": type("C", (), {"doc_type": "n648", "confidence": 0.9})()}, {}, texts={"n648-0.pdf": "medical certification"}, measure=False)
    documents.save_run(d, built)
    monkeypatch.setenv("I485_INDEX", str(tmp_path / "index.db"))
    index.rebuild_all(clients, tmp_path / "index.db")
    found = index.search("medical", db_path=tmp_path / "index.db")
    [hit] = [h for h in found["results"] if h["type"] == "n648"]
    assert hit["type_name"] == "Form N-648 (medical certification for a disability exception)" and hit["type_short"] == "Form N-648 (disability exception)"
    # the Reports table: the screen's column gets the short name, the row keeps the long one (and the CSV is written from the long one)
    monkeypatch.setattr(query, "document_counts", lambda root, hidden, confidential: ([("n648", 2, 1), ("passport", 3, 2)], [], 0))
    types, _qualities, _hidden = reports._documents(clients, "attorney", None)
    assert types[0] == ("Form N-648 (medical certification for a disability exception)", 2, 1, "Form N-648 (disability exception)") and types[1][0] == types[1][3] == "Passport"
