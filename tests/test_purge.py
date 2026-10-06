"""A case purged at the end of its keeping period, and proven gone (src/purge.py, brief Q1).

The canary test builds one made-up case with every kind of data the product can hold, plants a made-up marker word in each store, checks every
marker is found before the purge (so the search is not blind), purges the case as a job, and then reads every file under the data folder, the
portal, the documents root, the SQLite copies and the next backup: no marker, no name of the client and no case id is anywhere, except the
purge's own record (the case id, its kind, who, when, the reason) and the originals index (the case id, the client's name, the kind of document,
where it is kept). Every file it read is listed. The other tests: the clock per office (proposed, confirmed, from majority), the two steps that
cannot be skipped, the wait and the second attorney, cancelling, and the routes (an attorney's, a paralegal gets 403 and sees nothing).

Everyone here is made up ("Zelda Quillfeather Exemplo").
"""

from __future__ import annotations

import json
import sqlite3
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import case_notes
import clock
import engagement
from file_policy_fixture import own_case_identity, disposition
import events
import find
import index
import jobs
import purge
import query
import restricted
import settings
from rules import approval

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_restricted import app, call, doc, make_case, server, sign_in, world  # noqa: E402,F401 -- the restricted cases' world and its review app

CASE = "case-quill"
NAME = "Zelda Quillfeather Exemplo"
EMAIL = "zelda.quillfeather@example.com"
PHONE = "6175550199"
A_NUMBER = "A098765432"
A_DIGITS = "098765432"
# one made-up word per store: each must be found before the purge and nowhere after it
MARKERS = {
    "documents": "thistledownmark", "packet": "packetfillmark", "decisions": "decisionnotemark", "notes": "casenotemark", "tasks": "calendartaskmark",
    "portal_answers": "portalanswermark", "portal_upload": "portaluploadmark", "views": "viewlogfilemark", "audit": "auditvaluemark",
    "reader_example": "readerexamplemark", "clio": "cliomattermark", "prospect": "prospectcallmark", "prospect_portal": "prospectportalmark",
    "restricted": "restrictreasonmark", "ledger": "ledgerrowmark", "access": "accesslogmark", "conflicts": "conflictquerymark",
    "inbox": "inboxnoticemark", "overnight": "overnightstatemark", "accuracy": "accuracyvaluemark", "reference": "referenceformmark",
    "sync": "syncremotemark", "learning": "learninglabelmark", "jobs": "jobfilemark", "source": "sourcescanmark", "outbox": "outboxbodymark",
    "export": "exportzipmark", "staff_named": "namedstaffmark",
}


@pytest.fixture
def firm(tmp_path, monkeypatch):
    data = tmp_path / "data"
    clients, portal, docs = data / "clients", data / "portal", tmp_path / "documents"
    for env, value in {"I485_INDEX": data / "index.db", "I485_QUERY_DB": data / "query.db", "I485_EVENTS": data / "events.jsonl", "I485_ROSTER": data / "roster.json",
                       "I485_INBOX": data / "inbox", "I485_AUDIT_FILL": data / "audit_fill.json", "I485_JOBS": data / "jobs", "I485_REFERENCE": data / "reference",
                       "I485_RULES_APPROVED": data / "rules_approved.json", "I485_MAINTENANCE_LOG": data / "maintenance_log.json", "I485_CASES": clients, "PORTAL_DATA": portal, "I485_BACKUP_LOG": data / "backup_log.json",
                       "I485_ACCURACY_HISTORY": data / "accuracy_history.jsonl", "I485_CLIENTS_ROOT": docs, "I485_SETTINGS": data / "settings.json"}.items():
        monkeypatch.setenv(env, str(value))
    monkeypatch.setattr(settings, "PATH", data / "settings.json")
    for env in ("I485_WORDINGS", "I485_READER_EXAMPLES", "I485_PROSPECTS", "I485_CONFLICTS", "I485_FIND"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    index._LAST_REFRESH.clear()
    find._FOLLOWERS.clear()
    approval.approve(engagement.PRACTICE_ID, "Sam Attorney", "attorney")
    clients.mkdir(parents=True)
    return type("Firm", (), {"data": data, "clients": clients, "portal": portal, "docs": docs, "tmp": tmp_path})


def _jsonl(path: Path, *rows: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def plant(firm) -> Path:
    """The case with every kind of data the product holds, a marker in each store; and another case that must survive."""
    m = MARKERS
    d = make_case(firm.clients, CASE, NAME, [doc("q1", "declaration", text=f"My declaration: {m['documents']} and the river crossing", a_number=A_NUMBER)],
                  decisions={"i1": ("Sam Attorney", "2026-09-20T10:00:00+00:00")})
    keep = make_case(firm.clients, "case-keep", "Otto Exemplo Keeper", [doc("k1", "passport", text="Passaporte Keeper sorocaba")])
    log = json.loads((d / "decisions.json").read_text())
    log["i1"]["note"] = m["decisions"]
    _json(d / "decisions.json", log)
    (d / "i485_filled.pdf").write_bytes(b"%PDF-1.4 " + m["packet"].encode())
    case_notes.add_note(d, f"Spoke with the client: {m['notes']}", "Sam Attorney", "attorney")
    case_notes.add_task(d, f"Call back {m['tasks']}", "2026-11-02", None, "", "Sam Attorney", [], "Sam Attorney", "attorney")
    restricted.mark(d, True, m["restricted"], "Sam Attorney", "attorney")
    restricted.name_person(d, "jane@firm.example", True, "Sam Attorney", "attorney", f"Jane {m['staff_named']}")
    # the source scans, kept apart in the documents root
    src = firm.docs / CASE / "source"
    src.mkdir(parents=True)
    (src / "scan.pdf").write_bytes(b"%PDF-1.4 " + m["source"].encode())
    meta = json.loads((d / "meta.json").read_text())
    meta["source_folder"] = str(src.resolve())
    _json(d / "meta.json", meta)
    _json(firm.docs / "sync_state.json", {"clients": {CASE: {"remote_id": "r1", "name": NAME, "docs": {"x": {"file": "scan.pdf", "name": m["sync"]}}}}})
    # the client's file made to hand over
    zname = "i485-case-file-2026-10-01-1.zip"
    engagement.exports_folder(firm.clients).mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(engagement.exports_folder(firm.clients) / zname, "w") as z:
        z.writestr("readme.txt", m["export"])
    rec = engagement.read(d)
    rec["file"] = {"name": zname, "files": 1, "bytes": 10, "sha256": "x", "by": "Sam Attorney", "at": "2026-10-01T10:00:00+00:00", "returned": None}
    _json(d / engagement.FILE, rec)
    # the portal: profile, answers, an upload, sign-in links, the queue, the outbox; the first call it began as
    pc = firm.portal / "clients" / CASE
    from portal.store import PortalStore
    profile = PortalStore(firm.portal).add_client(CASE, NAME, email=EMAIL, phone=PHONE, language="pt")
    _json(pc / "profile.json", profile | {"prospect": "pr-quill"})
    _json(pc / "answers.json", {"answers": {"q.story": m["portal_answers"]}})
    (pc / "uploads").mkdir()
    (pc / "uploads" / "photo.pdf").write_bytes(b"%PDF-1.4 " + m["portal_upload"].encode())
    _json(firm.portal / "auth.json", {"links": {"h1": {"client": CASE, "expires": "2027-01-01"}, "h2": {"client": "case-keep", "expires": "2027-01-01"}},
                                      "sessions": {"s1": {"client": CASE, "expires": "2027-01-01"}}})
    (firm.portal / "queue").mkdir(parents=True, exist_ok=True)
    (firm.portal / "queue" / CASE).write_text("2026-10-01")
    _jsonl(firm.portal / "outbox.jsonl", {"at": "x", "channel": "email", "to": EMAIL, "subject": "Your file", "body": f"Dear {NAME}, {m['outbox']}"},
           {"at": "x", "channel": "email", "to": "otto@example.com", "subject": "Hi", "body": "Dear Otto"})
    pr = firm.data / "prospects" / "pr-quill"
    _json(pr / "prospect.json", {"id": "pr-quill", "name": NAME, "became_client": {"id": CASE, "by": "Sam Attorney", "at": "x"}, "how_heard": m["prospect"]})
    # A retained legacy/import profile is still included in strict two-store
    # inventory. Answers-only residue correctly holds Q1 rather than disappearing.
    _json(firm.portal / "prospects" / "clients" / "pr-quill" / "profile.json",
          {"id": "pr-quill", "name": NAME, "email": EMAIL, "phone": PHONE, "language": "pt", "consent": {"email": False}, "closed_on": "2026-10-01"})
    _json(firm.portal / "prospects" / "clients" / "pr-quill" / "answers.json", {"a": m["prospect_portal"]})
    # the firm's logs: the ledger, the view log, the access log, the conflict log, Find's questions
    events.record("documents", "changed", f"Changed a document {m['ledger']}", case_dir=d, who="Sam Attorney")
    events.record("notes", "note_added", "Added a note", case="prospect:pr-quill", home=firm.data, who="Sam Attorney")
    events.record("notes", "note_added", "Added a note", case_dir=keep, who="Sam Attorney")
    _jsonl(firm.data / "review_views.jsonl", {"at": "x", "email": "sam@firm.example", "name": "Sam Attorney", "role": "attorney", "client": CASE, "kind": "document",
                                              "file": f"{m['views']}.pdf"},
           {"at": "x", "email": "sam@firm.example", "name": "Sam Attorney", "role": "attorney", "client": "case-keep", "kind": "case", "file": None})
    _jsonl(firm.data / "review_users_access.jsonl", {"at": "x", "event": "case_access", "email": "sam@firm.example", "client": CASE, "action": "named", "person": m["access"]})
    _jsonl(firm.data / "conflict_checks.jsonl",
           {"kind": "search", "id": "c1", "at": "x", "by": "Sam", "purpose": "add", "case": CASE, "query": {"name": NAME, "other_names": [m["conflicts"]]}, "hits": [], "matched": {}},
           {"kind": "decision", "id": "c2", "case": CASE, "decision": "none", "search": "c1", "by": "Sam", "at": "x"},
           {"kind": "search", "id": "c3", "at": "x", "by": "Sam", "purpose": "add", "case": "case-keep", "query": {"name": "Otto Exemplo Keeper"},
            "hits": [{"for": "client", "case": CASE, "names": [NAME], "client": NAME}], "matched": {"client": [CASE, "case-keep"]}})
    _jsonl(firm.data / "find_questions.jsonl", {"at": "x", "who": "Sam", "role": "attorney", "length": 30, "question": f"where is {NAME}'s passport", "hits": 1})
    # the firm's wordings, the reader's examples, the learning store, the boxes the office changes
    _json(firm.data / "wordings" / "i485" / "worked" / "w1.json", {"id": "w1", "form": "i485", "key": "applicant.part9.worked", "text": "I worked {place}.",
                                                                   "status": "approved", "uses": [{"case": CASE, "at": "x", "by": "Sam"}, {"case": "case-keep", "at": "x", "by": "Sam"}],
                                                                   "edits": [], "from_file": ""})
    _json(firm.data / "reader_examples" / CASE / "e1.json", {"value": m["reader_example"]})
    with sqlite3.connect(firm.data / "learning.db") as db:
        db.execute("create table model_runs (id integer primary key, at text, client text, doc text, task text, answer text)")
        db.execute("create table labels (id integer primary key, at text, client text, doc text, task text, label text)")
        db.execute("insert into labels (client, doc, label) values (?, ?, ?)", (CASE, "q1", m["learning"]))
        db.execute("insert into labels (client, doc, label) values (?, ?, ?)", ("case-keep", "k1", "passport"))
    _json(firm.data / "audit_fill.json", {"version": 1, "rows": [{"case": CASE, "form": "i485", "before": "", "after": m["audit"]}], "skipped": [f"{CASE} (Form I-485): x"]})
    # the overnight run, the accuracy comparison and its references, the inbox, the Clio link, the job queue, the lists' copy
    _json(firm.data / "batch_state.json", {CASE: {"status": "done", "why": m["overnight"]}, "case-keep": {"status": "done"}})
    (firm.data / "batch_report.txt").write_text(f"Overnight run\n    {CASE}: 2 blocking\n    case-keep: 1 blocking\n", encoding="utf-8")
    _json(firm.data / "accuracy_latest.json", {"results": [{"case": CASE, "boxes": [{"reference": m["accuracy"]}]}], "skipped": []})
    (firm.data / "reference").mkdir()
    (firm.data / "reference" / f"{CASE}.pdf").write_bytes(b"%PDF " + m["reference"].encode())
    _json(firm.data / "inbox" / "queue.json", [{"id": "n1", "file": "n1.pdf", "read": {"names": [NAME]}, "what": m["inbox"], "candidates": [{"case": CASE, "name": NAME}]}])
    (firm.data / "inbox" / "waiting").mkdir()
    (firm.data / "inbox" / "waiting" / "n1.pdf").write_bytes(b"%PDF " + m["inbox"].encode())
    _jsonl(firm.data / "inbox" / "inbox_log.jsonl", {"at": "x", "event": "routed", "case": CASE, "name": NAME, "what": m["inbox"]})
    _json(firm.data / "clio" / "state.json", {"matters": {"77": {"case": CASE, "name": NAME, "number": m["clio"]}}, "out": {CASE: {"docs": 1}}, "errors": []})
    job = jobs.submit(jobs.folder_for(firm.clients), "staff_upload", client=CASE, by="Sam", args={"name": f"{m['jobs']}.pdf"})
    done = jobs.folder_for(firm.clients) / "done"  # a reading finished last week, filed as the worker files it
    done.mkdir(exist_ok=True)
    (jobs.folder_for(firm.clients) / f"{job['id']}.json").rename(done / f"{job['id']}.json")
    _json(firm.data / "roster.json", {"version": 1, "entries": {CASE: {"row": {"name": NAME}}, "case-keep": {"row": {"name": "Otto"}}}})
    # the firm-wide copies, built from all of it
    index.rebuild_all(firm.clients)
    query.rebuild_all(firm.clients)
    find.rebuild_all(firm.clients)
    return d


def _read_all(path: Path) -> list[tuple[str, bytes]]:
    """(name, bytes) of a file, and of each member when it is a zip."""
    data = path.read_bytes()
    out = [(str(path), data)]
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            out += [(f"{path}!{n}", z.read(n)) for n in z.namelist()]
    return out


def scan(roots: list[Path], words: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """{word: [files holding it]} over every file under the roots (zip members opened, file names read too); and every file looked at."""
    found: dict[str, list[str]] = {w: [] for w in words}
    looked: list[str] = []
    for root in roots:
        for p in sorted(root.rglob("*")) if root.is_dir() else [root] if root.is_file() else []:
            if not p.is_file():
                continue
            for name, data in _read_all(p):
                looked.append(name)
                low = data.lower()
                for w in words:
                    if w.lower().encode() in low or w.lower() in name.lower():
                        found[w].append(name)
    return found, looked


ALLOWED = {"purges.json", "originals_kept.json"}  # the firm's record of the purge and the originals index: the case id stays in them by design


def leftovers_of_the_case_id(roots: list[Path]) -> list[str]:
    """Every place the case id is, other than the purge's own record: the ledger's purge rows, purges.json, the originals index; and in a SQLite file,
    every row of every table that holds it but the query layer's copy of the purge rows."""
    hits = []
    for root in roots:
        for p in sorted(root.rglob("*")) if root.is_dir() else []:
            if not p.is_file() or p.name in ALLOWED:
                continue
            if p.name.startswith("events-") and p.suffix == ".jsonl":
                for line in p.read_text(encoding="utf-8").splitlines():
                    if CASE in line and json.loads(line).get("kind") != "purge":
                        hits.append(f"{p}: {line[:120]}")
                continue
            if p.read_bytes()[:16] == b"SQLite format 3\x00":
                with sqlite3.connect(p) as db:
                    for (table,) in db.execute("select name from sqlite_master where type = 'table'"):
                        cols = [r[1] for r in db.execute(f"pragma table_info('{table}')")]
                        for row in db.execute(f"select * from '{table}'"):
                            if any(CASE in str(v) for v in row) and not (table == "events" and dict(zip(cols, row)).get("kind") == "purge"):
                                hits.append(f"{p}:{table}: {str(row)[:120]}")
                continue
            for name, data in _read_all(p):
                if CASE.encode() in data or CASE in name:
                    hits.append(name)
    return hits


def test_a_purged_case_leaves_no_trace_in_any_store_the_product_writes(firm):
    import backups

    d = plant(firm)
    engagement.end(d, "closed", "Sam Attorney", "attorney", reason="Fictional operational completion.", portal_root=firm.portal)
    roots = [firm.data, firm.docs]
    words = list(MARKERS.values()) + [NAME, EMAIL, PHONE, A_DIGITS]
    before, _ = scan(roots, words)
    blind = [w for w, files in before.items() if not files]
    assert not blind, f"markers not found before the purge (the search would be blind to them): {blind}"
    early = backups.make_backup(firm.tmp / "backups", firm.data, clients=firm.docs)  # a backup made before: still holds the case, and the record says so

    # Actual return of the original and explicit lawful authority precede wait/job.
    purge.record_contact(d, "phone", "10/01/2026", f"Left a message for {NAME}", "Sam Attorney", "attorney")
    purge.review_originals(d, [{"doc": "q1", "choice": "returned"}], False, "Sam Attorney", "attorney")
    clock._now_override = datetime(2032, 10, 6, 10, 30)
    disposition(d, who="Sam Attorney", portal_root=firm.portal, completed_on="2026-10-05",
                age_status="adult", keep_until="2032-10-05", originals_status="returned")
    asked = purge.ask(d, f"Client {NAME} asked us to delete the file", "Sam Attorney", "attorney", attorneys=1)
    assert NAME not in json.dumps(asked) and asked["reason"].startswith("Client [name]")
    with pytest.raises(purge.PurgeError):
        purge.submit(firm.clients, CASE, "Sam Attorney")  # its day has not come
    clock._now_override = datetime(2032, 10, 6, 10, 30) + timedelta(days=purge.wait_days(firm.clients))
    purge.submit(firm.clients, CASE, "Sam Attorney", firm.data / "review_views.jsonl", firm.data / "review_users_access.jsonl")
    assert jobs.work(firm.clients, firm.portal, once=True, log=lambda *_: None) == 1
    record = purge.entry(firm.clients, CASE)
    assert record["state"] == "done", record
    print("Stores emptied:", {s["id"]: s["removed"] for s in record["stores"]}, "left:", record["left"])

    # every marker, the client's name, e-mail, phone and A-Number: nowhere, in any file, in the next backup either
    after = backups.make_backup(firm.tmp / "backups-next", firm.data, clients=firm.docs)
    roots_after = roots + [Path(after["archive"])]
    found, looked = scan(roots_after, words)
    print(f"The canary test read {len(looked)} files:\n" + "\n".join(looked))
    # the client's name stays in one place only: the originals index the firm keeps (the brief: the name and the case id, nothing else)
    survivors = {w: [f for f in files if not (w == NAME and f.endswith("originals_kept.json"))] for w, files in found.items()}
    survivors = {w: files for w, files in survivors.items() if files}
    assert not survivors, survivors
    leftovers = leftovers_of_the_case_id(roots_after[:2])
    assert not leftovers, leftovers
    with zipfile.ZipFile(after["archive"]) as z:
        assert not [n for n in z.namelist() if CASE in n or CASE.encode() in z.read(n) and not n.endswith(tuple(ALLOWED)) and "events-" not in n]
    assert len(looked) > 40 and any("index.db" in x for x in looked) and any("query.db" in x for x in looked) and any("find.db" in x for x in looked)

    # what stays: the purge's record (no name, no number), the originals index, the ledger's purge rows; the other case untouched
    assert record["kind"] is not None and record["asked_by"] == "Sam Attorney" and record["contact"] == [{"how": "phone", "on": "2026-10-01", "by": "Sam Attorney"}]
    assert {c["name"] for c in record["copies"]} == {Path(early["archive"]).name}  # the earlier backup still holds the case: the firm deletes it
    kept = purge.originals_kept(firm.clients)
    assert kept == []  # Actually returned originals cannot create a new kept-original index.
    rows = [r for r in events.rows(events.base_path(firm.data)) if r.get("case") == CASE]
    assert [r["action"] for r in rows] == ["contact", "originals", "asked", "started", "purged"] and all(r["kind"] == "purge" for r in rows)
    assert not any(NAME.lower() in json.dumps(r).lower() or "message" in r["what"].lower() for r in rows)
    assert (firm.clients / "case-keep" / "documents.json").exists()
    assert json.loads((firm.portal / "auth.json").read_text())["links"] == {"h2": {"client": "case-keep", "expires": "2027-01-01"}}
    assert index.search("keeper", db_path=index.default_path(firm.clients))["total"] == 1
    w = json.loads((firm.data / "wordings" / "i485" / "worked" / "w1.json").read_text())
    assert [u["case"] for u in w["uses"]] == ["case-keep"]


def test_retained_original_holds_legal_q1_without_any_store_or_index_deletion(firm):
    import client_file_policy as policy
    d = _ended_case(firm, born="1980-03-14")
    own_case_identity(d)
    clock._now_override = datetime(2032, 10, 6, 10, 30)
    from file_policy_fixture import applicability
    out = applicability(d, who="Sam Attorney", portal_root=firm.portal, value={"matter_type": "civil",
        "client_age_status": "adult", "representation_completed_on": "2026-10-05",
        "originals_status": "retained_for_client", "other_requirements": "reviewed_none"})
    with pytest.raises(ValueError, match="protections"):
        policy.approve_retention(d, out["revision"], out["snapshot_sha256"], "2032-10-05",
                                "Fictional attempted review", who="Sam Attorney", role="attorney", portal_root=firm.portal)
    purge.record_contact(d, "phone", "2032-10-06", "Fictional contact", "Sam Attorney", "attorney")
    purge.review_originals(d, [{"doc": "m1", "choice": "kept", "where": "Cabinet 3, drawer B"}], False, "Sam Attorney", "attorney")
    before = {str(p.relative_to(firm.data)): p.read_bytes() for p in firm.data.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        policy.approve_destruction(d, out["revision"], out["snapshot_sha256"], True, "Fictional attempted review",
                                  who="Sam Attorney", role="attorney", portal_root=firm.portal)
    with pytest.raises(ValueError):
        purge.ask(d, "Typed request cannot authorize retained original deletion", "Sam Attorney", "attorney")
    after = {str(p.relative_to(firm.data)): p.read_bytes() for p in firm.data.rglob("*") if p.is_file()}
    assert after == before and purge.entry(firm.clients, d.name) is None
    assert purge.originals_kept(firm.clients) == []


def test_mechanical_cleanup_preserves_an_explicitly_historical_kept_original_index(firm):
    # This lower-level cleanup contract does not establish current Q1 authority.
    d = _ended_case(firm, born="1980-03-14")
    old = {"case": d.name, "client": "Fictional historical original owner", "kind": "Birth certificate",
           "where": "Cabinet 3, drawer B", "purged_on": "2020-01-01"}
    path = firm.data / purge.ORIGINALS_FILE
    _json(path, {"version": 1, "originals": [old]})
    before = path.read_bytes()
    from portal.communication_consent import data_gate
    with data_gate(firm.data), jobs.case_lock(jobs.folder_for(firm.clients), d.name):
        result = purge.empty_stores(firm.clients, d.name, firm.portal)
    assert result["left"] == [] and not d.exists()
    assert path.read_bytes() == before and purge.originals_kept(firm.clients) == [old]
    assert purge.entry(firm.clients, old["case"]) is None


# -- the clock, the steps, the wait -------------------------------------------------------------------------------------------------------------


def _ended_case(firm, case="case-minor", born="2010-03-14"):
    d = make_case(firm.clients, case, "Nina Exemplo Minor", [doc("m1", "birth_certificate", text="Certidao de nascimento")])
    graph = json.loads((d / "fact_graph.json").read_text())
    graph["facts"]["applicant.date_of_birth"] = {"fact_key": "applicant.date_of_birth", "tier": 1, "status": "resolved", "value": born, "sources": []}
    _json(d / "fact_graph.json", graph)
    engagement.end(d, "closed", "Sam Attorney", "attorney", reason="Done.", portal_root=firm.portal)
    return d


def test_the_clock_is_proposed_until_the_attorney_confirms_and_counts_from_majority_in_massachusetts(firm):
    import offices

    settings.save("firm", {"firm.state": "MA"}, "Sam Attorney")
    d = _ended_case(firm)
    c = purge.retention_clock(d)
    assert c["state"] == "proposed" and "Proposed, not confirmed" in c["words"] and "Rule 1.15A" in c["words"] and "To be confirmed by the attorney" in c["words"]
    assert purge.due_now(firm.clients) == [] and c["rule"]["years"] == 6 and c["rule"]["from_majority"] is True
    with pytest.raises(PermissionError):
        purge.confirm_rule(firm.clients, offices.MAIN, 6, True, "Jane Doe", "paralegal")
    purge.confirm_rule(firm.clients, offices.MAIN, 6, True, "Sam Attorney", "attorney")
    c = purge.retention_clock(d)
    # ended 10/05/2026 at 16; the 18th birthday 03/14/2028 is later: kept until 03/14/2034
    assert c["state"] == "running" and c["starts"] == "2028-03-14" and c["until"] == "2034-03-14"
    assert "planning proposal only: the office period entered on 10/05/2026" in c["words"] and "Rule 1.15A" not in c["words"]
    assert engagement.retention(d, datetime(2026, 10, 5).date())["words"] == "Planning proposal only: Proposed date 03/14/2034: the office's retention rule, as the attorney set it on 10/05/2026. Operational closure and an office period do not establish legal termination or destruction authority."
    settings.save("firm", {"office.retention_years": "7"}, "Sam Attorney")  # the office's years changed after the confirmation: confirmed again first
    assert purge.retention_clock(d)["state"] == "proposed"
    purge.confirm_rule(firm.clients, offices.MAIN, 7, False, "Sam Attorney", "attorney")
    clock._now_override = datetime(2033, 10, 6, 9, 0)
    assert purge.retention_clock(d)["state"] == "due" and purge.retention_clock(d)["until"] == "2033-10-05"
    with pytest.raises(purge.PurgeError):
        purge.set_wait(firm.clients, 3, "Sam Attorney", "attorney")  # never fewer than 7 days
    assert purge.wait_days(firm.clients) == 14 and purge.set_wait(firm.clients, 30, "Sam Attorney", "attorney")["wait_days"] == 30


def test_a_florida_office_is_told_the_bar_sets_no_period_and_a_minor_without_a_birth_date_never_runs(firm):
    settings.save("firm", {"firm.state": "FL"}, "Sam Attorney")
    d = _ended_case(firm, born="")
    rule = purge.retention_clock(d)["rule"]
    assert "the Florida Bar sets no single period" in rule["words"] and rule["screen"] == "For a Florida office the Bar sets no fixed period: the figure is the firm's own policy."
    import offices

    purge.confirm_rule(firm.clients, offices.MAIN, 6, True, "Sam Attorney", "attorney")
    assert purge.retention_clock(d)["state"] == "no_birth_date"


def test_neither_step_can_be_skipped_a_purge_waits_a_second_attorney_confirms_and_any_attorney_cancels(firm):
    d = _ended_case(firm)
    own_case_identity(d)
    # Typed requests cannot override unapproved jurisdiction/protection facts.
    with pytest.raises(ValueError):
        purge.ask(d, "The client asked", "Sam Attorney", "attorney")
    clock._now_override = datetime(2034, 3, 15, 10, 30)
    disposition(d, who="Sam Attorney", portal_root=firm.portal, completed_on="2026-10-05",
                age_status="minor", majority_on="2028-03-14", keep_until="2034-03-14")
    with pytest.raises(purge.PurgeError, match="attempt to reach the client"):
        purge.ask(d, "reason", "Sam Attorney", "attorney")
    purge.record_contact(d, "letter", "2026-10-01", "", "Sam Attorney", "attorney")
    with pytest.raises(purge.PurgeError, match="originals"):
        purge.ask(d, "reason", "Sam Attorney", "attorney")
    with pytest.raises(purge.PurgeError, match="not answered"):
        purge.review_originals(d, [], False, "Sam Attorney", "attorney")
    with pytest.raises(purge.PurgeError, match="where"):
        purge.review_originals(d, [{"doc": "m1", "choice": "kept"}], False, "Sam Attorney", "attorney")
    purge.review_originals(d, [], True, "Sam Attorney", "attorney")
    with pytest.raises(PermissionError):
        purge.ask(d, "The client asked", "Jane Doe", "paralegal")
    rec = purge.ask(d, "The client asked", "Sam Attorney", "attorney", attorneys=2)
    assert rec["purge_on"] == "2034-03-29" and rec["needs_confirm"] and not rec["early"] and purge.waiting(firm.clients) == {"case-minor": rec}
    clock._now_override = datetime(2034, 3, 30, 9, 0)
    assert not purge.ready(purge.entry(firm.clients, "case-minor"))  # the day has come, but no second attorney yet
    with pytest.raises(purge.PurgeError, match="second attorney"):
        purge.confirm(firm.clients, "case-minor", "Sam Attorney", "attorney")
    purge.confirm(firm.clients, "case-minor", "Ann Attorney", "attorney")
    assert purge.due_now(firm.clients) == ["case-minor"]
    purge.cancel(firm.clients, "case-minor", "Ann Attorney", "attorney")
    assert purge.due_now(firm.clients) == [] and purge.waiting(firm.clients) == {} and (d / "documents.json").exists()
    with pytest.raises(purge.PurgeError):
        purge.run(firm.clients, "case-minor")  # a cancelled purge never runs
    actions = [r["action"] for r in events.rows(events.base_path(firm.data)) if r.get("kind") == "purge" and r.get("case") == "case-minor"]
    assert actions == ["contact", "originals", "asked", "confirmed", "cancelled"]


def test_the_overnight_step_hands_a_due_purge_to_the_job_worker_and_names_no_case(firm):
    import overnight

    d = _ended_case(firm)
    own_case_identity(d)
    # Typed requests cannot override unapproved jurisdiction/protection facts.
    with pytest.raises(ValueError):
        purge.ask(d, "The client asked", "Sam Attorney", "attorney")
    clock._now_override = datetime(2034, 3, 15, 10, 30)
    disposition(d, who="Sam Attorney", portal_root=firm.portal, completed_on="2026-10-05",
                age_status="minor", majority_on="2028-03-14", keep_until="2034-03-14")
    purge.record_contact(d, "phone", "2026-10-01", "", "Sam Attorney", "attorney")
    purge.review_originals(d, [], True, "Sam Attorney", "attorney")
    purge.ask(d, "The client asked", "Sam Attorney", "attorney")
    assert overnight.purges_due(firm.clients) == "Purges: none due."
    clock._now_override = datetime(2034, 3, 30, 9, 0)
    assert overnight.purges_due(firm.clients) == "Purges: 1 case(s) handed to the job worker to be purged."
    waiting = jobs.jobs(jobs.folder_for(firm.clients), kind="purge", recent=0)
    assert len(waiting) == 1 and waiting[0]["client"] is None and "case-minor" not in json.dumps(waiting[0])
    jobs.work(firm.clients, firm.portal, once=True, log=lambda *_: None)
    assert not d.exists() and purge.entry(firm.clients, "case-minor")["state"] == "done"
    done = list((jobs.folder_for(firm.clients) / "done").glob("*.json"))
    assert done and all("case-minor" not in p.read_text() for p in done)


# -- the routes ------------------------------------------------------------------------------------------------------------------------------------


def test_the_purge_is_an_attorneys_and_a_paralegal_never_starts_confirms_or_sees_one(server, world, monkeypatch):  # noqa: F811
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    for route in ("/api/purge?client=case-ana", "/api/retention-rules"):
        assert call(server + route, jane)[0] == 403, route
    hidden, made_up = call(server + "/api/purge?client=case-rosa", jane), call(server + "/api/purge?client=case-zzzz", jane)
    assert hidden == made_up and hidden[0] == 404  # a restricted case she may not open: the answer a made-up id gets
    for body in ({"client": "case-ana", "action": "contact", "how": "phone", "on": "2026-10-01"}, {"client": "case-ana", "action": "ask", "reason": "x"}):
        assert call(server + "/api/purge", jane, body)[0] == 403
    assert call(server + "/api/retention-rules", jane, {"action": "wait", "days": 30})[0] == 403
    status, text = call(server + "/api/purge?client=case-ana", sam)
    view = json.loads(text)
    assert status == 200 and [s["id"] for s in view["stores"]] == [k for k, _ in purge.STORES] and "Clio" in view["clio"] and view["purge"] is None
    assert call(server + "/api/purge", sam, {"client": "case-ana", "action": "ask", "reason": "The client asked"})[0] == 400  # the two steps first
    assert call(server + "/api/purge", sam, {"client": "case-ana", "action": "contact", "how": "phone", "on": "2026-10-01", "note": "Left a message"})[0] == 200
    assert call(server + "/api/purge", sam, {"client": "case-ana", "action": "originals", "none_held": True})[0] == 200
    d = world / "case-ana"
    own_case_identity(d)
    approval.approve(engagement.PRACTICE_ID, "Sam Attorney", "attorney")
    engagement.end(d, "closed", "Sam Attorney", "attorney", reason="Fictional operational close.", portal_root=world.parent / "portal")
    monkeypatch.setattr(clock, "_now_override", datetime(2032, 10, 6, 10, 30))
    disposition(d, who="Sam Attorney", portal_root=world.parent / "portal", completed_on="2026-10-05", age_status="adult", keep_until="2032-10-05")
    # Accounts are refreshed after the explicit fictional wall-clock advance.
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    status, text = call(server + "/api/purge", sam, {"client": "case-ana", "action": "ask", "reason": "The client asked us to delete the file"})
    on = json.loads(text)["purge"]["purge_on"]
    assert status == 200 and not json.loads(text)["ready"]
    assert json.loads(call(server + "/api/retention-rules", sam)[1])["waiting"][0]["case"] == "case-ana"
    for route in ("/api/overview?ended=all", "/api/search?q=sorocaba"):  # the attorney's lists say "to be purged on"; the paralegal's say nothing of it
        assert on in call(server + route, sam)[1], route
        assert "purge" not in call(server + route, jane)[1], route
    assert call(server + "/api/purge", jane, {"client": "case-ana", "action": "cancel"})[0] == 403
    for route in ("/api/case_events?client=case-ana", "/api/case_events?client=case-ana&page=1", "/api/case_events.csv?client=case-ana"):
        assert "purge" not in call(server + route, jane)[1].lower(), route  # the case's history leaves a purge's rows out for her
    assert "purge" in call(server + "/api/case_events?client=case-ana&page=1", sam)[1].lower()
    assert call(server + "/api/purge", sam, {"client": "case-ana", "action": "run"})[0] == 400  # its day has not come
    assert call(server + "/api/purge", sam, {"client": "case-ana", "action": "cancel"})[0] == 200
    assert "purge_on" not in call(server + "/api/overview?ended=all", sam)[1]


# -- the purge and the ledger's chain (src/ledger_seal.py) -----------------------------------------------------------------------------------------


def test_a_purge_blanks_the_cases_ledger_rows_in_place_and_the_chain_and_the_daily_seals_still_hold(firm):
    import ledger_seal

    d = _ended_case(firm)
    own_case_identity(d)
    clock._now_override = datetime(2034, 3, 15, 10, 30)
    disposition(d, who="Sam Attorney", portal_root=firm.portal, completed_on="2026-10-05",
                age_status="minor", majority_on="2028-03-14", keep_until="2034-03-14")
    events.record("notes", "note_added", "Added a note", case_dir=d, who="Sam Attorney")
    keep = make_case(firm.clients, "case-other", "Otto Exemplo Other", [doc("o1", "passport", text="Passaporte")])
    events.record("notes", "note_added", "Added a note", case_dir=keep, who="Sam Attorney")
    base = events.base_path(firm.data)
    clock._now_override = datetime(2034, 3, 16, 9, 0)
    ledger_seal.nightly(base)  # yesterday's seal, printed and kept by the firm
    sealed = ledger_seal.read_anchors(base)
    assert sealed and ledger_seal.verify(base)["ok"]
    purge.record_contact(d, "phone", "2026-10-01", "", "Sam Attorney", "attorney")
    purge.review_originals(d, [], True, "Sam Attorney", "attorney")
    purge.ask(d, "The client asked", "Sam Attorney", "attorney")
    clock._now_override = datetime(2034, 3, 31, 9, 0)
    purge.run(firm.clients, "case-minor", firm.portal)
    lines = [json.loads(x) for f in events.files(base) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    blanked = [r for r in lines if r.get("kind") == events.REDACTED]
    assert blanked and all(set(r) == {"at", "kind", "purge", "prev", "hash"} for r in blanked)  # its time and its place in the chain, nothing else
    assert not any(r.get("case") == "case-minor" and r.get("kind") != "purge" for r in lines)
    result = ledger_seal.verify(base)
    assert result["ok"], result["line"]
    assert result["blanked"] == len(blanked) and "rows of purged cases blanked" in result["line"]
    assert ledger_seal.read_anchors(base) == sealed  # the seals the firm printed still match: the purge rewrote no hash
    assert "case-minor" not in events.redactions_path(base).read_text(encoding="utf-8")
    # a row blanked by anyone else is caught: the other case's note, made to look like a purge's
    for f in events.files(base):
        rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
        rows = [events.tombstone(r, "forged") if r.get("case") == "case-other" else r for r in rows]
        f.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows), encoding="utf-8")
    bad = ledger_seal.verify(base)
    assert not bad["ok"] and "no purge recorded it" in bad["line"]
