"""The data dictionary (docs/data_dictionary.md, made by tools/data_dictionary.py from src/records.py and the SQL comments): regenerated here, it must be the
committed file, and what the product really writes must be what it says. Everyone here is made up."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace


import documents
import events
import index
import journey
import query
import records
import restricted
from review import state

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import data_dictionary  # noqa: E402
import firm_world  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def test_the_committed_dictionary_is_what_the_code_says_now():
    committed = (REPO / "docs" / "data_dictionary.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == data_dictionary.render(), "docs/data_dictionary.md is out of date: run python tools/data_dictionary.py"
    assert data_dictionary.main(["--check"]) == 0


def test_the_tool_fails_the_check_when_the_file_differs(tmp_path, monkeypatch, capsys):
    stale = tmp_path / "data_dictionary.md"
    stale.write_text(data_dictionary.render() + "a hand edit\n", encoding="utf-8")
    monkeypatch.setattr(data_dictionary, "OUT", stale)
    assert data_dictionary.main(["--check"]) == 1 and "out of date" in capsys.readouterr().err
    assert data_dictionary.main([]) == 0 and stale.read_text(encoding="utf-8") == data_dictionary.render()


def test_every_record_and_field_has_a_sentence_and_nothing_is_hand_dated():
    ids = [r["id"] for r in records.RECORDS]
    assert len(ids) == len(set(ids))
    for r in records.RECORDS:
        for key in ("title", "area", "files", "where", "format", "written_by", "versions", "fields", "exported"):
            assert key in r, (r["id"], key)
        assert r["area"] in ("case", "portal", "firm", "logs") and (r["version"] is None or isinstance(r["version"], int))
        for name, kind, meaning, flag in r["fields"]:
            assert name and kind and meaning.strip().endswith(".") and flag in ("", records.PERSON, records.SECRET), (r["id"], name)
        assert r["versions"] or not r["fields"] or r["id"] in ("files", "reference")
    text = data_dictionary.render()
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d", text), "no time stamp in it: it changes only when a record does"
    assert " -- " not in text and "—" not in text


def test_every_sql_column_and_table_has_a_sentence_and_the_parsed_tables_are_the_real_ones(tmp_path):
    for module, make in ((index, lambda p: index.connect(p)), (query, lambda p: query.connect(p))):
        tables = data_dictionary.sql_tables(module.SCHEMA)
        assert tables and all(module.TABLES.get(t["name"]) for t in tables), (module.__name__, [t["name"] for t in tables if not module.TABLES.get(t["name"])])
        for t in tables:
            assert all(c["meaning"] for c in t["columns"]), (module.__name__, t["name"], [c["name"] for c in t["columns"] if not c["meaning"]])
        db = make(tmp_path / f"{module.__name__}.db")
        for t in tables:
            real = [r[1] for r in db.execute(f"pragma table_info({t['name']})")]
            assert [c["name"] for c in t["columns"]] == real, (module.__name__, t["name"])
        db.close()
    assert set(query.TABLES) >= {t["name"] for t in data_dictionary.sql_tables(query.SCHEMA)}


def _documented(record_id: str, prefix: str = "") -> set[str]:
    """The first name of each documented field that starts with prefix (such as "documents[]." or "<item id>."), after it."""
    out = set()
    for name, *_rest in records.by_id(record_id)["fields"]:
        if name.startswith(prefix):
            out.add(re.split(r"[.\[]", name[len(prefix):])[0])
    return out


def _names(record_id: str) -> set[str]:
    return {name for name, *_ in records.by_id(record_id)["fields"]}


def test_what_the_product_really_writes_is_what_the_dictionary_says(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    clients = tmp_path / "data" / "clients"
    clients.mkdir(parents=True)
    d = firm_world.make_case(clients, "ana-exemplo", decisions=0)
    # a document record, as the reader builds it
    built = documents.build(d / "source", {"passport-0.pdf": SimpleNamespace(doc_type="passport", confidence=0.9)}, {}, texts={"passport-0.pdf": "made up"}, measure=False)
    record = built["documents"][0]
    documents.save_run(d, built)
    saved = documents.read(d)
    rid = saved["documents"][0]["id"]
    documents.set_person(d, rid, "spouse", "Jane Paralegal", "paralegal")
    documents.set_language(d, rid, "pt", "Jane Paralegal", "paralegal")
    documents.set_dates(d, rid, "2020-01-01|2030-01-01", "Jane Paralegal", "paralegal")
    documents.set_quality(d, rid, "blurry", "Jane Paralegal", "paralegal")
    documents.tag(d, rid, documents.roles()[0], "Jane Paralegal", "paralegal")
    after = documents.read(d)
    assert set(after) == {"version", "built", "documents", "boundary_decisions", "case_subjects", "subject_assignments"} \
        and after["version"] == records.by_id("documents")["version"] == documents.VERSION
    ours = _documented("documents", "documents[].")
    for rec in (record, after["documents"][0]):
        assert set(rec) <= ours, f"fields the dictionary does not say: {sorted(set(rec) - ours)}"
    # A typed-only dictionary probe exercises the decision record shape. Critical document fields require their source prerequisites first (covered in test_critical_actions).
    item = {"id": "i1", "kind": "reading", "level": "review", "title": "t", "group": "g", "actions": ["confirm", "set"], "facts": [{"key": "questionnaire.dictionary_probe", "input": {"type": "text"}}]}
    state.record_decision(d, item, {"action": "set", "values": {"questionnaire.dictionary_probe": "Typed fixture answer"}, "reviewer": "Jane Paralegal", "role": "paralegal", "note": "n", "old": "was"})
    state.undo_decision(d, "i1", "Sam Attorney", "attorney")
    entry = state.load_decision_log(d)["i1"]
    ours = _documented("decisions", "<item id>.")
    assert set(entry) <= ours and set(entry["history"][0]) <= ours | {"undone"} and set(entry["item"]) <= {"id", "kind", "level", "title", "group", "facts"}
    # the timeline's marks
    for action, kwargs in (("done", {"item": "x"}), ("stage", {"value": "i360_ready"}), ("track", {"value": "sij"}), ("lpr_date", {"value": "2026-01-02"}), ("ours", {}),
                           ("hearing", {"value": {"date": "2026-12-01", "kind": "Bond"}}), ("oath", {"value": {"date": "2026-12-02"}}), ("moved", {"value": {"date": "2026-01-02", "address": "x"}}),
                           ("person", {"value": {"given_name": "A", "family_name": "B", "relationship": "Parent", "person": "parent"}})):
        journey.mark(d, action, "Sam Attorney", **kwargs)
    status = json.loads((d / "status.json").read_text(encoding="utf-8"))
    said = {n.split(".")[1] for n in _names("journey") if n.startswith("journey.")}
    assert set(status["journey"]) == {"done", "stage", "track", "lpr_date", "ours", "hearings", "oath", "moves", "people"} and set(status["journey"]) <= said
    assert set(status) <= _documented("journey")
    # who may open it
    restricted.mark(d, True, "a reason", "Sam Attorney", "attorney")
    restricted.name_person(d, "jane@firm.example", True, "Sam Attorney", "attorney", "Jane Paralegal")
    restricted.set_messages(d, True, "why", "Sam Attorney", "attorney")
    access = json.loads((d / "access.json").read_text(encoding="utf-8"))
    assert set(access) <= _documented("access") and set(access["marked"]) <= {"on", "by", "at", "reason", "law"}
    # the ledger's row
    row = events.record("decisions", "confirmed", "x", case="ana-exemplo", who="Sam", role="attorney")
    assert tuple(row) == events.FIELDS + events.CHAIN and set(row) == _documented("events")
    # every file the case folder holds is one the catalog lists, except a stray the firm put there
    import fnmatch

    held = {p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()}
    patterns = records.patterns("case", exported_only=False)
    strays = {h for h in held if not any(len(p.split("/")) == len(h.split("/")) and all(fnmatch.fnmatchcase(a, b) for a, b in zip(h.split("/"), p.split("/"))) for p in patterns)}
    assert strays == set(), strays


def test_the_ledger_and_the_log_rows_say_only_what_the_dictionary_says(tmp_path, monkeypatch):
    from review.auth import Accounts

    accounts = Accounts(tmp_path / "users.json")
    accounts.add("kim@firm.example", "Kim Exemplo", "paralegal", by="sam@firm.example")
    accounts.from_address("127.0.0.1")
    accounts.log("signed_in", "kim@firm.example", how="code")
    rows = [json.loads(line) for line in accounts.log_path.read_text(encoding="utf-8").splitlines()]
    assert rows and set().union(*[set(r) for r in rows]) <= _documented("accounts_log") | {"role", "next", "reason", "how", "minutes", "times_today", "action", "person", "restricted"}
    users = json.loads((tmp_path / "users.json").read_text(encoding="utf-8"))
    assert set(users) == {"users", "sessions"} == _documented("accounts")
    assert set(users["users"]["kim@firm.example"]) <= {"name", "role", "active", "created_at", "salt", "hash", "scrypt", "must_change", "failures", "locked_until", "lockouts",
                                                        "lock_minutes", "totp", "devices"}
    assert {"name", "role", "active", "created_at", "must_change"} <= set(users["users"]["kim@firm.example"])


def test_the_dictionary_says_where_a_persons_data_is_and_what_is_never_exported():
    text = data_dictionary.render()
    section = text.split("## Where a person's data is")[1].split("## What an export")[0]
    for needle in ("`facts.<key>.value`", "`documents[].text`", "`answers.json`", "`tasks.json`", "`documents[].files`", "`case`", "Every table holds a person",
                   "The case id of every row (it can be a name)"):
        assert needle in section, needle
    never = text.split("## What an export of the firm's data leaves out")[1]
    for needle in ("deployment.json", "password hashes", "sign-in table", "index.db and query.db", "Backups"):
        assert needle in never, needle
    assert "version 1" in text and "schema version" in text and text.count("**Version:**") >= len(records.RECORDS)
