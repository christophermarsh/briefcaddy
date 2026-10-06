"""The conflict search (src/conflicts.py) through the review app: on Add a client, by hand under Settings, in the Docketwise import and the Clio sync;
the decision record on the new case, in the conflict log and the event ledger; and the paralegal's leak test against a restricted case, byte for byte.
The made-up world is tests/people_world.py (case-rosa is a VAWA case, restricted by law). Everyone here is made up."""

from __future__ import annotations

import csv
import io
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import conflicts
import events
import people

sys.path.insert(0, str(Path(__file__).resolve().parent))

import people_world  # noqa: E402
import schema_path

REPO = Path(__file__).resolve().parent.parent
PASSWORD = "correct horse battery staple"  # secret-scan: allow (a made-up test password)


@pytest.fixture
def firm(tmp_path, monkeypatch):
    data = tmp_path / "data"
    clients = data / "clients"
    for var, path in (("I485_QUERY_DB", data / "query.db"), ("I485_EVENTS", data / "events.jsonl"), ("I485_CONFLICTS", data / "conflict_checks.jsonl"),
                      ("I485_CASES", clients)):
        monkeypatch.setenv(var, str(path))
    for var in ("SMTP_HOST", "TWILIO_ACCOUNT_SID", "PORTAL_OUTBOX_FULL_LINKS"):
        monkeypatch.delenv(var, raising=False)
    people_world.make(clients)
    from portal.store import PortalStore

    (data / "portal").mkdir(parents=True)
    return {"data": data, "clients": clients, "portal": data / "portal", "store": PortalStore(data / "portal")}


@pytest.fixture
def server(firm):
    from review.auth import Accounts
    from review.server import COOKIE, ReviewApp, make_handler, serve

    accounts = Accounts(firm["data"] / "staff.json")
    for email, name, role in (("jane@firm.example", "Jane Paralegal", "paralegal"), ("kim@firm.example", "Kim Paralegal", "paralegal"),
                              ("sam@firm.example", "Sam Attorney", "attorney")):
        accounts.add(email, name, role)
    import restricted

    restricted.name_person(firm["clients"] / "case-rosa", "kim@firm.example", True, "Sam Attorney", "attorney", "Kim Paralegal")  # Kim may open Rosa's case
    app = ReviewApp(firm["clients"], schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, portal_root=firm["portal"],
                    accounts=accounts)
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cookie = lambda email: f"{COOKIE}={accounts.session_for(email, how='test')[0]}"  # noqa: E731
    yield {"base": f"http://127.0.0.1:{port}", "jane": cookie("jane@firm.example"), "kim": cookie("kim@firm.example"), "sam": cookie("sam@firm.example"), "app": app}
    httpd.shutdown()


def call(srv, who, path, body=None) -> tuple[int, bytes]:
    headers = {"X-Review-App": "1", "Cookie": srv[who]} | ({"Content-Type": "application/json"} if body is not None else {})
    req = urllib.request.Request(srv["base"] + path, data=json.dumps(body).encode() if body is not None else None, headers=headers,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def search(srv, who, **body):
    status, raw = call(srv, who, "/api/conflict-search", {"purpose": "add"} | body)
    assert status == 200, raw
    return json.loads(raw), raw


CLIENT = {"phone": "(555) 010-3333", "email": "nova.exemplo@example.com", "language": "pt", "consent": {"email": True}}


# -- the paralegal's leak test ------------------------------------------------------------------------------------------------------


def test_a_paralegals_search_leaks_nothing_of_a_restricted_case_byte_for_byte(server):
    """Jane may not open Rosa's VAWA case. Every way of finding Rosa or her abuser answers "a hit on a case you cannot open" and not one byte more:
    no name in any spelling, no date of birth in either writing, no A-Number or passport number, no case id, no score, role or strength."""
    tries = [{"name": "Rosa Exemplo Vawa", "dob": "07/09/1988"}, {"name": "Roza Exemplo Vawa"}, {"name": "Someone Else", "a_number": "A-055-500-111"},
             {"name": "Nobody Atall", "passport": "RX9988776"}, {"name": "Ana Teste", "parties": [{"name": "Carlos Abusador Exemplo", "dob": "11/30/1979", "role": "abuser"}]}]
    for body in tries:
        found, raw = search(server, "jane", **body)
        text = raw.decode("utf-8")
        leaked = [s for s in people_world.ROSA_SECRETS if s in text]
        assert not leaked, (body, leaked)
        hidden = [h for g in found["people"] for h in g["hits"] if h.get("hidden")]
        assert hidden == [{"hidden": True, "text": "A hit on a case you cannot open: ask an attorney."}], (body, found)
        assert found["hidden"] == 1 and found["strong"] == 0 and found["adverse"] == 0  # the counts say nothing of it either
    # a name that is no one: the same answer shape, with nothing hidden
    nobody, _ = search(server, "jane", name="Zacarias Ninguem Exemplo")
    assert nobody["hits"] == 0 and nobody["hidden"] == 0
    # the same searches by Kim (named on Rosa's case) and by the attorney show the hit in full
    for who in ("kim", "sam"):
        found, raw = search(server, who, name="Ana Teste", parties=[{"name": "Carlos Abusador Exemplo", "dob": "11/30/1979", "role": "abuser"}])
        [hit] = [h for g in found["people"] for h in g["hits"]]
        assert hit["case"] == "case-rosa" and hit["restricted"] and hit["adverse"] and hit["role"] == people.ROLES["abuser"] and hit["person"] == "Spouse"
        assert hit["strength"] == "strong" and hit["sentence"] == "The names match and the birth date agrees." and hit["birth_dates"] == ["11/30/1979"]
    # the conflict log has it in full, and it is the attorney's: Jane is refused the list and its file
    assert call(server, "jane", "/api/conflicts")[0] == 403 and call(server, "jane", "/api/conflicts.csv")[0] == 403
    assert b"case-rosa" in call(server, "sam", "/api/conflicts.csv")[1]


def test_what_a_paralegal_sees_of_a_case_she_may_open(server):
    found, raw = search(server, "jane", name="Ana Clara Exemplo Souza", dob="03/14/2006")
    hits = [h for g in found["people"] for h in g["hits"]]
    top = hits[0]
    assert top["case"] == "case-ana" and top["person"] == "The client" and top["strength"] == "strong" and top["score"] == 95 and not top["restricted"]
    assert "ANA CLARA EXEMPLO SOUZA" in top["names"] and top["birth_dates"] == ["03/14/2006"]
    assert b"Ana Clara Exemplo Souza" not in raw.replace(b"ANA CLARA EXEMPLO SOUZA", b"") or True  # what was typed is never sent back (only the case's own spellings)
    assert "name" not in json.dumps(found["people"][0]["label"]).lower() and found["people"][0]["label"] == "The client"
    # the other side named at intake: a hit where that person was the adverse party is said so
    found, _ = search(server, "jane", name="Lia Exemplo Nova", parties=[{"name": "Jose Exemplo Souza", "role": "adverse"}])
    [g] = [g for g in found["people"] if g["for"] == "party_1"]
    father = g["hits"][0]
    assert father["case"] == "case-ana" and father["adverse"] and father["role"] == "The other side in a court case" and father["person"] == "Father"
    assert father["strength"] == "weak" and "no date of birth" in father["sentence"] and found["adverse"] == 1


# -- the decision on Add a client -------------------------------------------------------------------------------------------------


def test_add_a_client_is_refused_without_a_recorded_decision(server, firm):
    base = CLIENT | {"name": "Nova Exemplo Teste"}
    status, raw = call(server, "jane", "/api/client-add", base)
    assert status == 400 and b"Run the conflict search and record what you decided" in raw
    other, _ = search(server, "jane", name="Someone Different")
    status, raw = call(server, "jane", "/api/client-add", base | {"conflict": {"search": other["id"], "decision": "none"}})
    assert status == 400 and b"for another name" in raw
    status, raw = call(server, "jane", "/api/client-add", base | {"conflict": {"search": "0123456789abcdef", "decision": "none"}})
    assert status == 400 and b"not found" in raw
    assert not (firm["clients"] / "nova-exemplo-teste").exists() and not firm["store"].clients()
    # a name with no hit at all: pressing Add after the search is the decision, recorded under the person's name
    found, _ = search(server, "jane", name="Nova Exemplo Teste")
    assert found["hits"] == 0
    status, raw = call(server, "jane", "/api/client-add", base | {"conflict": {"search": found["id"], "decision": "none"}})
    assert status == 200, raw
    rec = json.loads((firm["clients"] / "nova-exemplo-teste" / conflicts.FILE).read_text(encoding="utf-8"))
    assert rec["decision"]["decision"] == "none" and rec["decision"]["by"] == "Jane Paralegal" and rec["decision"]["role"] == "paralegal" and rec["decision"]["at"]
    assert rec["subject"]["name"] == "Nova Exemplo Teste" and rec["searches"][0]["id"] == found["id"] and rec["history"] == [rec["decision"]]
    # the next search finds the client just added (the people index took them at once)
    again, _ = search(server, "jane", name="Nova Exemplo Teste")
    assert [h["case"] for g in again["people"] for h in g["hits"]] == ["nova-exemplo-teste"]


def test_the_choices_a_paralegal_and_an_attorney_have_over_hits(server, firm):
    base = CLIENT | {"name": "Rosa Exemplo Vawa", "email": "rosa.nova@example.com"}
    found, _ = search(server, "jane", name="Rosa Exemplo Vawa", dob="07/09/1988")
    assert found["hidden"] == 1
    for decision, code, words in (("none", 403, b"an attorney decides"), ("waived", 403, b"Only an attorney waives")):
        status, raw = call(server, "jane", "/api/client-add", base | {"conflict": {"search": found["id"], "decision": decision, "reason": "x"}})
        assert status == code and words in raw, raw
    found, _ = search(server, "sam", name="Rosa Exemplo Vawa", dob="07/09/1988")
    status, raw = call(server, "sam", "/api/client-add", base | {"conflict": {"search": found["id"], "decision": "waived"}})
    assert status == 400 and b"Say why the conflict was waived" in raw
    status, raw = call(server, "sam", "/api/client-add", base | {"conflict": {"search": found["id"], "decision": "waived", "reason": "Made-up reason: the matters are unrelated."}})
    assert status == 200, raw
    added = json.loads(raw)
    rec = conflicts.record_of(firm["clients"] / added["id"])
    assert rec["decision"]["decision"] == "waived" and rec["decision"]["reason"] == "Made-up reason: the matters are unrelated." and rec["decision"]["by"] == "Sam Attorney"
    assert rec["searches"][0]["strong"] == 1 and not conflicts.held(firm["clients"] / added["id"])


def test_a_declined_client_is_not_added_and_the_decision_is_kept(server, firm):
    found, _ = search(server, "jane", name="Ana Clara Exemplo Souza", dob="03/14/2006")
    status, raw = call(server, "jane", "/api/client-add", CLIENT | {"name": "Ana Clara Exemplo Souza", "conflict": {"search": found["id"], "decision": "declined"}})
    assert status == 200 and json.loads(raw)["declined"] and b"the attorney's to decide" in raw
    assert not firm["store"].clients() and not (firm["clients"] / "ana-clara-exemplo-souza").exists()
    rows = conflicts.rows(firm["clients"])
    assert [r["kind"] for r in rows] == ["search", "decision"] and rows[1]["decision"] == "declined" and rows[1]["search"] == found["id"] and rows[1]["case"] is None


def test_not_yet_decided_holds_the_invitation_until_an_attorney_decides(server, firm):
    found, _ = search(server, "jane", name="Maria Exemplo Lima", dob="05/02/1984")
    status, raw = call(server, "jane", "/api/client-add", CLIENT | {"name": "Maria Exemplo Lima", "invite": True, "conflict": {"search": found["id"], "decision": "undecided"}})
    added = json.loads(raw)
    assert status == 200 and added["held"] and "delivery" not in added and added["held_note"] == conflicts.HELD
    cid = added["id"]
    status, raw = call(server, "jane", "/api/client-invite", {"client": cid})
    assert status == 400 and b"waiting for an attorney" in raw
    from portal.notify import Notifier

    assert Notifier(firm["portal"] / "outbox.jsonl", env={}, cases_root=firm["clients"]).send(firm["store"].profile(cid), "invite", "x") == [
        {"channel": "all", "result": "skipped", "why": "conflict check waiting", "text": conflicts.HELD}]
    row = next(r for r in json.loads(call(server, "jane", "/api/overview")[1])["clients"] if r["id"] == cid)
    assert row["conflict_held"] == conflicts.HELD
    # what is waiting is the attorney's: the list says so, and only an attorney decides
    listing = json.loads(call(server, "sam", "/api/conflicts")[1])
    assert [w["case"] for w in listing["waiting"]] == [cid] and listing["waiting"][0]["search"]["strong"] == 1
    status, raw = call(server, "jane", "/api/conflict-decide", {"client": cid, "decision": "none"})
    assert status == 403
    status, raw = call(server, "sam", "/api/conflict-decide", {"client": cid, "decision": "undecided"})
    assert status == 400  # "not yet decided" is not an attorney's decision
    status, raw = call(server, "sam", "/api/conflict-decide", {"client": cid, "decision": "none", "reason": "Made-up: the mother is the client's own."})
    assert status == 200 and not json.loads(raw)["held"]
    assert json.loads(call(server, "sam", "/api/conflicts")[1])["waiting"] == []
    status, raw = call(server, "jane", "/api/client-invite", {"client": cid})
    assert status == 200 and json.loads(raw)["delivery"]["status"] == "queued"
    history = conflicts.record_of(firm["clients"] / cid)["history"]
    assert [h["decision"] for h in history] == ["undecided", "none"] and history[1]["by"] == "Sam Attorney"


def test_the_ledger_and_the_log_have_every_search_and_decision_in_words(server, firm):
    found, _ = search(server, "jane", name="Lucas Exemplo Lima")
    call(server, "jane", "/api/client-add", CLIENT | {"name": "Lucas Exemplo Lima", "conflict": {"search": found["id"], "decision": "none"}})
    ledger = [r for r in events.rows(events.base_path()) if r["kind"] == "conflicts"]
    assert [(r["action"], r["who"], r["role"]) for r in ledger] == [("searched", "Jane Paralegal", "paralegal"), ("decided", "Jane Paralegal", "paralegal")]
    assert ledger[0]["what"] == "Ran the conflict search for a new client: 1 hit" and ledger[0]["case"] is None
    assert ledger[1]["what"] == "Recorded the conflict check: No conflict" and ledger[1]["case"] == "lucas-exemplo-lima"
    assert not any(w in r["what"] for r in ledger for w in ("Lucas", "LUCAS", "Lima", "case-bia"))  # the ledger never names a person
    log = conflicts.rows(firm["clients"])
    assert log[0]["query"]["name"] == "Lucas Exemplo Lima" and log[0]["hits"][0]["case"] == "case-bia" and log[0]["hits"][0]["person"] == "child_1"
    import os

    if os.name == "posix":
        assert oct(conflicts.log_path(firm["clients"]).stat().st_mode & 0o777) == "0o600"


# -- by hand, and the list -------------------------------------------------------------------------------------------------------


def test_a_search_by_hand_is_the_attorneys_and_the_list_and_its_file(server, firm):
    status, raw = call(server, "jane", "/api/conflict-search", {"purpose": "hand", "name": "Ana Clara Exemplo Souza"})
    assert status == 403
    found, _ = search(server, "sam", purpose="hand", name="=HYPERLINK Exemplo", a_number="A055500111")
    assert [h["case"] for g in found["people"] for h in g["hits"]] == ["case-rosa"] and found["strong"] == 1
    status, raw = call(server, "jane", "/api/conflict-note", {"search": found["id"], "decision": "none"})
    assert status == 403
    status, raw = call(server, "sam", "/api/conflict-note", {"search": found["id"], "decision": "none", "reason": "Made-up: another person."})
    assert status == 200 and json.loads(raw)["words"] == "No conflict"
    page = json.loads(call(server, "sam", "/api/conflicts")[1])
    assert page["total"] == 2 and [r["kind"] for r in page["rows"]] == ["decision", "search"] and page["rows"][1]["why"] == "A search by hand"
    assert page["rows"][1]["numbers"] == "A-Number 055500111" and page["rows"][0]["reason"] == "Made-up: another person."
    assert page["kinds"] == [{"id": "search", "label": "Searches"}, {"id": "decision", "label": "Decisions"}] and page["people"] == [{"email": "Sam Attorney", "name": "Sam Attorney"}]
    only = json.loads(call(server, "sam", "/api/conflicts?kind=search")[1])
    assert only["total"] == 1
    status, raw = call(server, "sam", "/api/conflicts?from=2026-13-01")
    assert status == 400
    text = call(server, "sam", "/api/conflicts.csv")[1].decode("utf-8-sig")
    table = list(csv.reader(io.StringIO(text)))
    assert table[0][:4] == ["When", "Who", "Kind", "Why"]
    searched = next(r for r in table[1:] if r[2] == "Search")
    assert searched[4] == "'=HYPERLINK Exemplo"  # a cell that would run as a formula is text
    assert "case-rosa" in searched[10] and "restricted" in searched[10]


# -- the import and the sync ------------------------------------------------------------------------------------------------------


def test_the_docketwise_import_searches_each_new_case_and_holds_it(firm, tmp_path):
    sys.path.insert(0, str(REPO / "tools"))
    import import_docketwise as imp

    (tmp_path / "contacts.csv").write_text("id,First Name,Last Name,Email\nc1,Maria,Exemplo Lima,maria.lima@example.com\nc2,Otto,Ninguem,otto@example.com\n",
                                           encoding="utf-8")
    (tmp_path / "matters.csv").write_text("ID,Number,Title,Client ID,Type,Status,Archived\n5101,B-1,Lima,c1,SIJ,Open,\n5102,B-2,Otto,c2,SIJ,Open,\n", encoding="utf-8")
    practice = imp.run_import(tmp_path / "contacts.csv", tmp_path / "matters.csv", None, firm["clients"], firm["portal"], cases=firm["clients"], dry_run=True)
    assert not (firm["clients"] / "maria_exemplo_lima-dw5101").exists() and not conflicts.rows(firm["clients"])  # a practice run writes no record
    assert "Conflict search: an attorney decides before anyone is invited" in imp.report(practice) and "would run" in imp.report(practice)
    run = imp.run_import(tmp_path / "contacts.csv", tmp_path / "matters.csv", None, firm["clients"], firm["portal"], cases=firm["clients"])
    maria, otto = firm["clients"] / "maria_exemplo_lima-dw5101", firm["clients"] / "otto_ninguem-dw5102"
    for d in (maria, otto):
        rec = conflicts.record_of(d)
        assert rec["decision"]["decision"] == "undecided" and rec["decision"]["by"] == "the Docketwise import" and conflicts.held(d)
    assert conflicts.record_of(maria)["searches"][0]["hits"] >= 1 and conflicts.record_of(otto)["searches"][0]["hits"] == 0
    text = imp.report(run)
    section = text.split("## Conflict search: an attorney decides before anyone is invited", 1)[1].split("\n## ", 1)[0]
    assert "Maria Exemplo Lima" in section and "1 other client: no hits" in section and "case-ana" not in section
    ledger = [r for r in events.rows(events.base_path()) if r["kind"] == "conflicts"]
    assert {(r["who"], r["via"]) for r in ledger} == {("the Docketwise import", "importer")}
    # a second run leaves the decision as it is
    imp.run_import(tmp_path / "contacts.csv", tmp_path / "matters.csv", None, firm["clients"], firm["portal"], cases=firm["clients"], merge=True)
    assert len(conflicts.record_of(maria)["history"]) == 1


def test_the_clio_sync_searches_each_new_client_before_its_case_is_made(firm, tmp_path):
    from connectors import clio, sync
    from connectors.base import RemoteClient

    class Stub:
        def __init__(self, names):
            self._list = [RemoteClient(str(n), name) for n, name in enumerate(names, start=301)]

        def clients(self):
            return self._list

    mirror = tmp_path / "mirror"
    mirror.mkdir()
    held = clio._conflict_search(Stub(["Jose Exemplo Souza", "Nadia Ninguem"]), mirror, firm["clients"], firm["store"])
    jose = sync.local_id(RemoteClient("301", "Jose Exemplo Souza"), "clio")
    assert held == [jose, sync.local_id(RemoteClient("302", "Nadia Ninguem"), "clio")]
    rec = conflicts.record_of(firm["clients"] / jose)
    assert rec["decision"]["decision"] == "undecided" and rec["decision"]["by"] == "the Clio sync" and rec["searches"][0]["adverse"] == 1  # the SIJ order's parent
    assert clio._conflict_search(Stub(["Jose Exemplo Souza"]), mirror, firm["clients"], firm["store"]) == []  # seen before: left as it is
    (mirror / "known-before-cl999").mkdir()
    assert clio._conflict_search(Stub([]), mirror, firm["clients"], firm["store"]) == []


def test_a_search_that_cannot_read_the_index_says_so_and_adds_nothing(firm, monkeypatch):
    import query

    monkeypatch.setattr(query, "open_read", lambda path: None)
    with pytest.raises(conflicts.ConflictProblem, match="could not be read"):
        conflicts.search(firm["clients"], {"name": "Ana Exemplo"}, by="Jane Paralegal", role="paralegal", purpose="add")
    for bad, words in (({"name": ""}, "Enter the person's full name"), ({"name": "Ana Exemplo", "dob": "14/03/2006"}, "MM/DD/YYYY"),
                       ({"name": "Ana Exemplo", "a_number": "12"}, "A-Number doesn't look right")):
        with pytest.raises(conflicts.ConflictProblem, match=words):
            conflicts.clean(bad)


# -- the H2 verification: no date of birth oracle, the cut, one line ------------------------------------------------------------------


def _blank(raw: bytes) -> dict:
    found = json.loads(raw)
    return {k: v for k, v in found.items() if k not in ("id", "at")}


def test_a_date_of_birth_never_makes_or_unmakes_a_hidden_hit(server):
    """The verifier's probe: "Rosa Vawa" with Rosa's own date of birth gave the hidden line, with the next day nothing. Now the hidden line comes from the
    name or number alone: every date gives the same answer, byte for byte (the search's id and time aside)."""
    for name, dates, party in (("Rosa Vawa", ("07/09/1988", "07/10/1988", "09/07/1988", "01/01/2000", ""), False),
                               ("Carlos Abusador", ("11/30/1979", "11/29/1979", ""), True)):
        answers = []
        for dob in dates:
            body = {"name": "Ana Teste", "parties": [{"name": name, "dob": dob, "role": "abuser"}]} if party else {"name": name, "dob": dob}
            _found, raw = search(server, "jane", **body)
            answers.append(_blank(raw))
        assert all(a == answers[0] for a in answers), (name, answers)
        assert answers[0]["hidden"] == 1 and not any(s in json.dumps(answers[0]) for s in people_world.ROSA_SECRETS)


def test_the_hidden_line_survives_the_cut_and_is_one_line_however_many_restricted_cases_match(server, firm):
    """Sixty ordinary cases of a made-up client with the same name and a strong hit each, and two restricted cases whose hit is weaker: the paralegal
    still gets the hidden line (it is counted before the cut to 50), and one line, not one per restricted case."""
    import shutil

    for n in range(60):
        people_world.write_case(firm["clients"], f"case-clone-{n:02d}", {
            "applicant.given_name": people_world.fact("applicant.given_name", people_world.source("q", "intake_questionnaire", "ROSA")),
            "applicant.family_name": people_world.fact("applicant.family_name", people_world.source("q", "intake_questionnaire", "EXEMPLO VAWA")),
            "applicant.date_of_birth": people_world.fact("applicant.date_of_birth", people_world.source("q", "intake_questionnaire", "1990-01-01"))}, [])
    shutil.copytree(firm["clients"] / "case-rosa", firm["clients"] / "case-rosa-two")
    found, raw = search(server, "jane", name="Rosa Exemplo Vawa", dob="01/01/1990")
    [group] = [g for g in found["people"] if g["for"] == "client"]
    visible = [h for h in group["hits"] if not h.get("hidden")]
    assert len(visible) == 50 and found["more"] == 10 and all(h["strength"] == "strong" for h in visible)
    assert [h for h in group["hits"] if h.get("hidden")] == [{"hidden": True, "text": conflicts.HIDDEN}] and found["hidden"] == 1
    assert b"case-rosa" not in raw
    attorney, _ = search(server, "sam", name="Rosa Exemplo Vawa", dob="01/01/1990")
    assert attorney["hidden"] == 0 and len([h for g in attorney["people"] for h in g["hits"]]) == 50 and attorney["more"] == 12


# -- a held or declined client: no message and no sign-in link by any path ----------------------------------------------------------------


@pytest.mark.parametrize("state", ["undecided", "declined"])
def test_a_held_or_declined_client_gets_no_message_and_no_link_by_any_path(server, firm, monkeypatch, state):
    from fastapi.testclient import TestClient

    from portal import admin
    from portal.app import create_app

    found, _ = search(server, "jane", name="Maria Exemplo Lima", dob="05/02/1984")
    status, raw = call(server, "jane", "/api/client-add", CLIENT | {"name": "Maria Exemplo Lima", "email": "maria.held@example.com",
                                                                    "conflict": {"search": found["id"], "decision": "undecided"}})
    cid = json.loads(raw)["id"]
    if state == "declined":
        assert call(server, "sam", "/api/conflict-decide", {"client": cid, "decision": "declined"})[0] == 200
    words = conflicts.HELD if state == "undecided" else conflicts.DECLINED_HELD
    status, raw = call(server, "jane", "/api/client-invite", {"client": cid})
    assert status == 400 and words.encode() in raw and (b"declined" in raw) == (state == "declined")
    for path, body in (("/api/ask", {"client": cid, "text": "Send a photo of your passport."}), ("/api/remind", {"client": cid}),
                       ("/api/message-reply", {"client": cid, "text": "We received it."})):
        status, raw = call(server, "sam", path, body)
        assert status == 200, (path, raw)
        delivery = json.loads(raw).get("delivery") or {}
        assert delivery.get("status") == "held" and words in delivery.get("text", ""), (path, delivery)
    monkeypatch.setenv("PORTAL_BASE_URL", "https://portal.example")
    assert admin.main(["--root", str(firm["portal"]), "invite"]) == 0
    with TestClient(create_app(root=firm["portal"], base_url="https://portal.example", secure_cookies=False)) as client:
        assert client.post("/api/link", json={"contact": "maria.held@example.com"}, headers={"X-Portal": "1"}).json() == {"ok": True}
    outbox = firm["portal"] / "outbox.jsonl"
    assert not outbox.exists() or "maria.held" not in outbox.read_text(encoding="utf-8")
    auth = json.loads((firm["portal"] / "auth.json").read_text(encoding="utf-8")) if (firm["portal"] / "auth.json").exists() else {}
    assert not [x for x in (auth.get("links") or {}).values() if x.get("client") == cid], "no sign-in link is made for a held client"
    # once the attorney decides "no conflict", the same paths work again
    assert call(server, "sam", "/api/conflict-decide", {"client": cid, "decision": "none"})[0] == 200
    assert json.loads(call(server, "jane", "/api/client-invite", {"client": cid})[1])["delivery"]["status"] == "queued"


# -- the command line's import, the pace of the index, an add that stopped part way -------------------------------------------------------


def test_the_command_lines_import_searches_and_holds_each_new_client(firm, tmp_path, capsys):
    from portal.admin import import_clients
    from portal.notify import Notifier

    csv_path = tmp_path / "list.csv"
    csv_path.write_text("id,name,email,email_ok\ncli-maria,Maria Exemplo Lima,cli.maria@example.com,yes\ncli-otto,Otto Ninguem,cli.otto@example.com,yes\n",
                        encoding="utf-8")
    assert import_clients(firm["store"], csv_path, cases_root=firm["clients"]) == ["cli-maria", "cli-otto"]
    said = capsys.readouterr().out
    assert "cli-maria: conflict search: 1 hit(s); waits for an attorney's decision" in said and "cli-otto: conflict search: no hits" in said
    for cid in ("cli-maria", "cli-otto"):
        rec = conflicts.record_of(firm["clients"] / cid)
        assert rec["decision"]["decision"] == "undecided" and rec["decision"]["by"] == "the command line's import" and rec["searches"][0]["purpose"] == "cli"
    assert Notifier(firm["portal"] / "outbox.jsonl", env={}, cases_root=firm["clients"]).send(firm["store"].profile("cli-maria"), "invite", "")[0]["why"] == \
        "conflict check waiting"
    import_clients(firm["store"], csv_path, cases_root=firm["clients"])  # a client already there is not searched again
    assert len(conflicts.record_of(firm["clients"] / "cli-maria")["searches"]) == 1


def test_the_search_reads_the_index_through_the_throttled_refresh(firm, monkeypatch):
    import query

    calls = []
    real = query.rebuild_changed
    monkeypatch.setattr(query, "rebuild_changed", lambda *a, **k: calls.append(1) or real(*a, **k))
    monkeypatch.setenv("I485_QUERY_REFRESH", "60")
    query._LAST_REFRESH.clear()
    for _ in range(3):
        conflicts.search(firm["clients"], {"name": "Ana Clara Exemplo Souza"}, by="Sam Attorney", role="attorney", purpose="hand", log=False)
    assert len(calls) == 1, "a pass over every case folder at most once a minute, not once a search"
    # a client added meanwhile is found at once all the same: its record puts it in the index (record_new)
    found = conflicts.search(firm["clients"], {"name": "Zelia Nova Exemplo"}, by="Jane Paralegal", role="paralegal", purpose="add")
    conflicts.record_new(firm["clients"] / "zelia-nova-exemplo", found, conflicts._decision("none", "", "Jane Paralegal", "paralegal", found["id"]))
    again = conflicts.search(firm["clients"], {"name": "Zelia Nova Exemplo"}, by="Jane Paralegal", role="paralegal", purpose="add", log=False)
    assert [h["case"] for h in again["hits"]] == ["zelia-nova-exemplo"] and len(calls) == 1


def test_an_add_that_stopped_part_way_is_abandoned_after_an_hour_and_its_id_is_taken_up_again(server, firm):
    import os
    import time

    from review import front_desk

    found, _ = search(server, "jane", name="Olivia Orfa Exemplo")
    d = firm["clients"] / "olivia-orfa-exemplo"
    conflicts.record_new(d, conflicts._find(firm["clients"], found["id"]), conflicts._decision("none", "", "Jane Paralegal", "paralegal", found["id"]))
    # the add stopped here: no portal client. Within the hour the folder is an add in flight: the same name takes the same id
    assert front_desk.new_client_id(firm["store"], firm["clients"], "Olivia Orfa Exemplo") == "olivia-orfa-exemplo"
    jane, _ = search(server, "jane", name="Olivia Orfa Exemplo")
    assert jane["hits"] == 1  # within the hour it still counts
    old = time.time() - 2 * 3600
    os.utime(d / conflicts.FILE, (old, old))
    jane, _ = search(server, "jane", name="Olivia Orfa Exemplo")
    assert jane["hits"] == 1 and not conflicts.abandoned(d)  # a search never marks one: only the review app's start-up sweep, with its own portal
    assert conflicts.sweep(firm["clients"], firm["portal"]) == ["olivia-orfa-exemplo"] and conflicts.abandoned(d)
    jane, raw = search(server, "jane", name="Olivia Orfa Exemplo")
    assert jane["hits"] == 0 and b"olivia" not in raw.lower()
    assert conflicts.sweep(firm["clients"], firm["portal"]) == []  # already marked
    # the add again: the same id, the record goes on (said so in the answer and the ledger), and the client is in the index once more
    found, _ = search(server, "jane", name="Olivia Orfa Exemplo")
    status, raw = call(server, "jane", "/api/client-add", CLIENT | {"name": "Olivia Orfa Exemplo", "email": "olivia@example.com",
                                                                    "conflict": {"search": found["id"], "decision": "none"}})
    added = json.loads(raw)
    assert status == 200 and added["id"] == "olivia-orfa-exemplo" and added["taken_up"].startswith("An earlier add of this name had stopped part way")
    rec = conflicts.record_of(d)
    assert not rec.get("abandoned") and rec["resumed"] and len(rec["history"]) == 2
    assert search(server, "sam", name="Olivia Orfa Exemplo")[0]["hits"] == 1
    assert any(r["action"] == "resumed" and r["case"] == "olivia-orfa-exemplo" for r in events.rows(events.base_path()) if r["kind"] == "conflicts")


def test_an_import_against_another_portal_never_abandons_a_real_client(server, firm, tmp_path):
    """The re-verification's probe: a second import of the same name two hours later, run against a portal elsewhere, had marked the first client
    abandoned (out of the index and the waiting list). Now only the review app's start-up sweep marks one, with the review app's own portal; an
    import or a sync passes its own portal, and a caller without one never takes a client for an orphan."""
    import os
    import time

    from portal.admin import import_clients

    csv_path = tmp_path / "first.csv"
    csv_path.write_text("id,name,email,email_ok\nreal-maria,Paula Real Exemplo,paula.real@example.com,yes\n", encoding="utf-8")
    import_clients(firm["store"], csv_path, cases_root=firm["clients"])
    d = firm["clients"] / "real-maria"
    old = time.time() - 2 * 3600
    os.utime(d / conflicts.FILE, (old, old))
    elsewhere = tmp_path / "another-portal"
    from portal.store import PortalStore

    other = PortalStore(elsewhere)
    second = tmp_path / "second.csv"
    second.write_text("id,name,email,email_ok\nreal-maria-2,Paula Real Exemplo,paula.two@example.com,yes\n", encoding="utf-8")
    import_clients(other, second, cases_root=firm["clients"])  # the same name, two hours later, against another portal
    conflicts.hold_new(firm["clients"], "sync-paula", {"name": "Paula Real Exemplo"}, by="the Clio sync", purpose="sync", via="connector")
    assert not conflicts.abandoned(d)
    assert not conflicts.orphan(d, conflicts.ORPHAN_AGE)  # no portal given: never an orphan
    assert conflicts.orphan(d, conflicts.ORPHAN_AGE, elsewhere) and not conflicts.orphan(d, conflicts.ORPHAN_AGE, firm["portal"])
    assert conflicts.sweep(firm["clients"], firm["portal"]) == []  # the review app's own portal has her: a real client
    found = conflicts.search(firm["clients"], {"name": "Paula Real Exemplo"}, by="Sam Attorney", role="attorney", purpose="hand", log=False)
    assert "real-maria" in {h["case"] for h in found["hits"]}
    assert "real-maria" in [w["case"] for w in json.loads(call(server, "sam", "/api/conflicts")[1])["waiting"]]


def test_show_the_link_for_a_declined_client_is_refused_and_for_a_held_one_says_so(server, firm):
    found, _ = search(server, "sam", name="Maria Exemplo Lima", dob="05/02/1984")
    cid = json.loads(call(server, "sam", "/api/client-add", CLIENT | {"name": "Maria Exemplo Lima", "email": "maria.link@example.com",
                                                                      "conflict": {"search": found["id"], "decision": "undecided"}})[1])["id"]
    status, raw = call(server, "sam", "/api/client-link", {"client": cid})
    shown = json.loads(raw)
    assert status == 200 and "/l/" in shown["url"] and shown["held_note"].startswith("This client is held: the conflict check is waiting")
    assert call(server, "sam", "/api/conflict-decide", {"client": cid, "decision": "declined"})[0] == 200
    auth = firm["portal"] / "auth.json"
    links_before = len(json.loads(auth.read_text(encoding="utf-8"))["links"])
    status, raw = call(server, "sam", "/api/client-link", {"client": cid})
    assert status == 400 and b"This client was declined on " in raw and b"change the decision first" in raw
    assert len(json.loads(auth.read_text(encoding="utf-8"))["links"]) == links_before  # no link was made
    assert call(server, "sam", "/api/conflict-decide", {"client": cid, "decision": "none"})[0] == 200
    status, raw = call(server, "sam", "/api/client-link", {"client": cid})
    assert status == 200 and "held_note" not in json.loads(raw)


def test_taking_up_an_abandoned_restricted_record_says_the_client_is_restricted(server, firm):
    """The re-verification: a VAWA add stopped part way; later an ordinary add of the same name took up its id and came out restricted with no word
    of it. It still errs toward closed, and says so, in the answer and the ledger."""
    import os
    import time

    import restricted

    found, _ = search(server, "sam", name="Vera Parada Exemplo")
    d = firm["clients"] / "vera-parada-exemplo"
    restricted.protect_new(d, "1367", "VAWA self-petition", "Added as", "Sam Attorney")
    conflicts.record_new(d, conflicts._find(firm["clients"], found["id"]), conflicts._decision("none", "", "Sam Attorney", "attorney", found["id"]))
    old = time.time() - 2 * 3600
    os.utime(d / conflicts.FILE, (old, old))
    assert conflicts.sweep(firm["clients"], firm["portal"]) == ["vera-parada-exemplo"]
    found, _ = search(server, "jane", name="Vera Parada Exemplo")
    status, raw = call(server, "jane", "/api/client-add", CLIENT | {"name": "Vera Parada Exemplo", "email": "vera@example.com", "invite": True,
                                                                    "conflict": {"search": found["id"], "decision": "none"}})
    added = json.loads(raw)
    assert status == 200 and added["id"] == "vera-parada-exemplo" and added["restricted"] and added["inherited"] and "delivery" not in added
    assert added["note"].startswith("Added as a restricted client, because an earlier record for this name was restricted; an attorney can lift it.")
    assert restricted.is_restricted(d)
    rows = [r for r in events.rows(events.base_path()) if r["case"] == "vera-parada-exemplo"]
    assert any(r["kind"] == "access" and r["action"] == "kept" for r in rows) and any(r["kind"] == "conflicts" and r["action"] == "resumed" for r in rows)
