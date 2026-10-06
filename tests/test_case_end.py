"""The case's end states (src/engagement.py): declined, withdrawn, transferred and closed, each the attorney's with a reason and its letter; the
keeping date from the firm's setting (and from a rule only where one was read on the official page); the list of files past their date and the
attorney's destruction record; ended cases off every work list but in Search and All clients; the restriction kept; the portal's closed view.
Everyone here is made up ("Ana Clara Exemplo Souza", "Rosa Exemplo")."""

from __future__ import annotations

import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import clock
import engagement
from file_policy_fixture import handover, archive_arguments, receipt_arguments, disposition
import events
import restricted
import settings
from portal.app import create_app
from portal.notify import Notifier
from portal.store import PortalStore
from rules import approval

sys.path.insert(0, str(Path(__file__).resolve().parent))
import firm_world  # noqa: E402
from communication_fixture import installation, approve_client, accepted_link
from test_restricted import app, call, server, sign_in, world  # noqa: E402,F401 -- the restricted cases' world and its review app

NAME = "Ana Clara Exemplo Souza"
H = {"X-Portal": "1"}


@pytest.fixture
def firm(tmp_path, monkeypatch):
    data = installation(tmp_path, monkeypatch)
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    clients = data / "clients"
    d = firm_world.make_case(clients, "case-ana")
    store = PortalStore(data / "portal")
    store.add_client("case-ana", NAME, email="ana@example.com", language="pt")
    approval.approve(engagement.PRACTICE_ID, "Sam Attorney", "attorney")
    return SimpleNamespace(root=data, clients=clients, case=d, store=store, portal=data / "portal")


def portal_client(firm):
    app_ = create_app(root=firm.portal, base_url="https://portal.example", secure_cookies=False, notifier=Notifier(firm.portal / "outbox.jsonl", env={}))
    client = TestClient(app_)
    return client


def letter_of(firm, kind: str) -> dict:
    return next(x for x in reversed(engagement.read(firm.case)["letters"]) if x["kind"] == kind)


# -- declining ------------------------------------------------------------------------------------------------------------------------


def test_declining_is_the_attorneys_with_a_reason_and_the_persons_link_stops_working_that_day(firm):
    client = portal_client(firm)
    approve_client(firm.store, "case-ana")
    client.get(f"/l/{accepted_link(firm.store, 'case-ana')}", follow_redirects=False)
    assert client.get("/api/me").status_code == 200
    unused = accepted_link(firm.store, "case-ana")
    with pytest.raises(PermissionError):
        engagement.end(firm.case, "declined", "Paulo Paralegal", "paralegal", reason="x", portal_root=firm.portal)
    with pytest.raises(ValueError, match="reason"):
        engagement.end(firm.case, "declined", "Sam Attorney", "attorney", reason=" ", portal_root=firm.portal)
    view = engagement.end(firm.case, "declined", "Sam Attorney", "attorney", reason="Not a matter we take.", matter="a work permit",
                          additions="Here is a list of legal aid offices.", portal_root=firm.portal)
    assert view["state"] == "declined" and view["end"]["reason"] == "Not a matter we take." and engagement.state(firm.case) == "declined"
    en = " ".join(letter_of(firm, "non_engagement")["texts"]["en"])
    assert "Thank you for contacting" in en and "about a work permit" in en and "No attorney-client relationship was formed" in en
    assert "As of 10/05/2026" in en and "Deadlines are yours to watch" in en and "Here is a list of legal aid offices." in en
    assert "Não se formou nenhuma relação" in " ".join(letter_of(firm, "non_engagement")["texts"]["pt"]) and "05/10/2026" in " ".join(letter_of(firm, "non_engagement")["texts"]["pt"])
    # the portal: the session ends, a link made before does not work, "send me a link" sends nothing
    assert client.get("/api/me").status_code == 401
    assert client.get(f"/l/{unused}", follow_redirects=False).headers["location"] == "/?expired=1"
    with pytest.raises(PermissionError):
        firm.store.new_link_token("case-ana")
    before = (firm.portal / "outbox.jsonl").read_text(encoding="utf-8") if (firm.portal / "outbox.jsonl").exists() else ""
    client.post("/api/link", json={"contact": "ana@example.com"}, headers=H)
    assert ((firm.portal / "outbox.jsonl").read_text(encoding="utf-8") if (firm.portal / "outbox.jsonl").exists() else "") == before
    # reopened: the portal works again
    engagement.reopen(firm.case, "The client came back with a new question.", "Sam Attorney", "attorney", firm.portal)
    assert engagement.state(firm.case) == "open" and engagement.read(firm.case)["history"][0]["reopened"]["by"] == "Sam Attorney"
    approve_client(firm.store, "case-ana")  # new documented fictional signoff after decline/reopen
    assert client.get(f"/l/{accepted_link(firm.store, 'case-ana')}", follow_redirects=False).headers["location"] == "/"


def test_a_case_whose_agreement_is_signed_is_withdrawn_not_declined(firm):
    engagement.make_agreement(firm.case, ["i485"], "A flat fee of $2,500.", "Sam Attorney", "attorney", portal_root=firm.portal)
    letter = engagement.read(firm.case)["letters"][-1]
    engagement.paper(firm.case, letter["id"], b"%PDF-1.4 made up signed scan", "2026-10-01", "Sam Attorney", "attorney", firm.portal)
    with pytest.raises(ValueError, match="Withdraw or close"):
        engagement.end(firm.case, "declined", "Sam Attorney", "attorney", reason="x", portal_root=firm.portal)


# -- withdrawing, transferring ----------------------------------------------------------------------------------------------------------


def test_withdrawing_says_when_it_ends_what_is_returned_and_the_deadlines_the_person_must_watch(firm, monkeypatch):
    monkeypatch.setattr(engagement, "known_deadlines", lambda d: [{"date": "2026-11-20", "what": "Answer the request for evidence on the I-360"}])
    with pytest.raises(ValueError, match="who ended"):
        engagement.end(firm.case, "withdrawn", "Sam Attorney", "attorney", reason="The client stopped answering.", portal_root=firm.portal)
    engagement.end(firm.case, "withdrawn", "Sam Attorney", "attorney", reason="The client stopped answering.", ended_by="firm",
                   returned="Your passport and your birth certificate.", portal_root=firm.portal)
    letter = letter_of(firm, "disengagement")
    en = " ".join(letter["texts"]["en"])
    assert "ends on 10/05/2026. We have decided to end our representation." in en and "What we return to you: Your passport and your birth certificate." in en
    assert "the deadlines we know of today are: 11/20/2026, Answer the request for evidence on the I-360." in en and "these deadlines are yours to watch" in en
    assert "20/11/2026" in " ".join(letter["texts"]["pt"]) and letter["deadlines"][0]["date"] == "2026-11-20"
    assert engagement.read(firm.case)["end"]["ended_by"] == "firm"


def test_a_client_moving_to_another_lawyer_is_transferred_and_the_letter_says_so(firm, monkeypatch):
    monkeypatch.setattr(engagement, "known_deadlines", lambda d: [])
    engagement.end(firm.case, "transferred", "Sam Attorney", "attorney", reason="New counsel called.", portal_root=firm.portal)
    en = " ".join(letter_of(firm, "disengagement")["texts"]["en"])
    assert "You told us that another lawyer will represent you from now on." in en and "we know of no deadline in your matter today. That does not mean there is none." in en
    assert engagement.state(firm.case) == "transferred"


# -- closing and the keeping date ------------------------------------------------------------------------------------------------------


def test_closing_keeps_the_file_until_the_date_the_firms_setting_gives_and_says_it_is_the_firms_setting(firm):
    settings.save("firm", {"office.retention_years": "6"}, "Sam Attorney")
    view = engagement.end(firm.case, "closed", "Sam Attorney", "attorney", reason="The green card was approved.", portal_root=firm.portal)
    keep = engagement.retention(firm.case, datetime(2026, 10, 5).date())
    assert keep["until"] == "2032-10-05" and keep["basis"] == "setting" and keep["years"] == 6 and keep["cite"] is None
    assert "the firm's setting for the" in view["end"]["retention_words"] and "Planning proposal only: Proposed date 10/05/2032" in view["end"]["retention_words"] and keep["approved"] is False
    letter = letter_of(firm, "closing")
    assert "planning proposal pending a separate attorney retention determination" in " ".join(letter["texts"]["en"])
    assert "proposta de planejamento, pendente de uma determinação separada do advogado" in " ".join(letter["texts"]["pt"])
    with pytest.raises(ValueError, match="years"):
        settings.save("firm", {"office.retention_years": "six"}, "Sam Attorney")


def test_with_no_setting_there_is_no_keeping_date_and_the_letter_promises_none(firm):
    engagement.end(firm.case, "closed", "Sam Attorney", "attorney", reason="Done.", portal_root=firm.portal)
    keep = engagement.retention(firm.case, datetime(2026, 10, 5).date())
    assert keep["until"] is None and keep["basis"] is None and keep["words"].startswith("Planning proposal only: No keeping date yet") and keep["approved"] is False
    assert "operational closure and an office period do not authorize destruction" in " ".join(letter_of(firm, "closing")["texts"]["en"])
    assert engagement.due(firm.clients)["undated"][0]["case"] == "case-ana"


def test_a_period_comes_from_a_rule_only_where_one_was_read_and_is_cited(firm, monkeypatch):
    assert all(rule["retention_years"] is None for rule in engagement.RULES.values())  # none read on its official page gives one today
    settings.save("firm", {"firm.state": "MA", "office.retention_years": "3"}, "Sam Attorney")
    monkeypatch.setitem(engagement.RULES, "MA", {"retention_years": 7, "retention_cite": "a rule made up for this test"})
    keep = engagement.retention(firm.case, datetime(2026, 10, 5).date())
    assert keep == {"until": "2033-10-05", "years": 7, "basis": "rule", "cite": "a rule made up for this test", "office": keep["office"],
                    "approved": False, "words": "Planning proposal only: Proposed date 10/05/2033: 7 years after the case ended, under a rule made up for this test. Operational closure and an office period do not establish legal termination or destruction authority."}


def test_the_files_past_their_date_are_listed_and_an_attorney_records_each_destroyed_never_the_product(firm, monkeypatch):
    settings.save("firm", {"office.retention_years": "1"}, "Sam Attorney")
    engagement.end(firm.case, "closed", "Sam Attorney", "attorney", reason="Done.", portal_root=firm.portal)
    assert engagement.due(firm.clients)["passed"] == []
    monkeypatch.setattr(clock, "_now_override", datetime(2027, 10, 6, 9, 0))
    listed = engagement.due(firm.clients)["passed"]
    assert [(r["case"], r["until"], r["state_name"]) for r in listed] == [("case-ana", "2027-10-05", "Closed")]
    with pytest.raises(PermissionError):
        engagement.mark_destroyed(firm.clients, "case-ana", "Paulo Paralegal", "paralegal")
    with pytest.raises(ValueError):
        engagement.mark_destroyed(firm.clients, "case-nobody", "Sam Attorney", "attorney")
    # The passed office proposal alone still cannot authorize destruction.
    with pytest.raises(ValueError):
        engagement.mark_destroyed(firm.clients, "case-ana", "Sam Attorney", "attorney")
    monkeypatch.setattr(clock, "_now_override", datetime(2032, 10, 6, 9, 0))
    disposition(firm.case, who="Sam Attorney", portal_root=firm.portal,
                completed_on="2026-10-05", age_status="adult", keep_until="2032-10-05")
    after = engagement.mark_destroyed(firm.clients, "case-ana", "Sam Attorney", "attorney", folder_removed=True, export_kept=False, note="Shredded the paper file.")
    assert after["passed"] == [] and after["destroyed"][0]["case"] == "case-ana" and after["destroyed"][0]["folder_removed"] is True
    saved = json.loads((firm.root / engagement.DESTROYED_FILE).read_text(encoding="utf-8"))
    assert NAME not in json.dumps(saved) and saved["cases"][0]["by"] == "Sam Attorney" and saved["cases"][0]["export_kept"] is False
    assert engagement.read(firm.case)["destroyed"]["by"] == "Sam Attorney" and firm.case.is_dir()  # the product removed nothing
    row = [r for r in events.rows(firm.root / "events.jsonl") if r["action"] == "destroyed"][-1]
    assert row["case"] == "case-ana" and row["what"] == "Recorded the case's file as destroyed after its keeping date: the folder removed"


def test_the_clients_file_is_the_export_of_the_case_handed_over_and_logged(firm):
    import hashlib
    import client_file
    with pytest.raises(PermissionError):
        engagement.export_file(firm.case, firm.clients, "Paulo Paralegal", "paralegal", firm.portal)
    binding = handover(firm.case, who="Sam Attorney", portal_root=firm.portal, include_work_product=True)
    view = engagement.export_file(firm.case, firm.clients, "Sam Attorney", "attorney", firm.portal, expected_binding_sha256=binding)
    path = engagement.file_path(firm.case, firm.clients)
    assert path.parent == firm.root / "exports"
    sha = view["file"]["sha256"]
    assert sha == hashlib.sha256(path.read_bytes()).hexdigest()
    included = client_file.inspect(path, sha)
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        assert view["file"]["files"] == len(names) == len(included) + 2  # JSON and human-readable manifests
        assert names == {row["path"] for row in included} | {"manifest.json", "MANIFEST.md"}
        assert "manifest.json" in names and "COVER.pdf" in names
        assert not {"clients/case-ana/fact_graph.json", "portal/case-ana/profile.json", "clients/case-ana/decisions.json"} & names
        original = (firm.case / "source/passport-0.pdf").read_bytes()
        assert any(archive.read(row["path"]) == original for row in included)
        assert all(hashlib.sha256(archive.read(row["path"])).hexdigest() == row["sha256"] for row in included)
    with pytest.raises(PermissionError):
        engagement.approve_file(firm.case, sha, "Paulo Paralegal", "paralegal", firm.portal, **archive_arguments(firm.case))
    with pytest.raises(ValueError, match="fingerprint"):
        engagement.approve_file(firm.case, "0" * 64, "Sam Attorney", "attorney", firm.portal, **archive_arguments(firm.case))
    with pytest.raises(ValueError, match="approve this exact"):
        engagement.file_returned(firm.case, "2026-10-05", "in_person", "Sam Attorney", "attorney", firm.portal, sha256=sha, **receipt_arguments(firm.case))
    engagement.approve_file(firm.case, sha, "Sam Attorney", "attorney", firm.portal, **archive_arguments(firm.case))
    with pytest.raises(ValueError, match="future"):
        engagement.file_returned(firm.case, "2026-12-01", "mail", "Sam Attorney", "attorney", firm.portal, sha256=sha, **receipt_arguments(firm.case))
    engagement.file_returned(firm.case, "2026-10-05", "in_person", "Sam Attorney", "attorney", firm.portal, sha256=sha, **receipt_arguments(firm.case))
    assert engagement.read(firm.case)["file"]["returned"]["how"] == "in_person"


# -- the portal for an ended case -----------------------------------------------------------------------------------------------------


def test_the_portal_of_a_closed_case_shows_since_when_and_the_closing_letter_and_nothing_else(firm):
    client = portal_client(firm)
    approve_client(firm.store, "case-ana")
    client.get(f"/l/{accepted_link(firm.store, 'case-ana')}", follow_redirects=False)
    engagement.end(firm.case, "closed", "Sam Attorney", "attorney", reason="Done.", portal_root=firm.portal)
    me = client.get("/api/me").json()
    assert me["closed"]["since"] == "2026-10-05" and me["closed"]["state"] == "closed" and me["closed"]["letter"]["language"] == "pt"
    assert "O seu assunto com o nosso escritório" in " ".join(me["closed"]["letter"]["texts"]["pt"])
    assert not {"sections", "documents", "tasks", "answers", "journey", "agreement"} & set(me)  # nothing else
    assert client.put("/api/answers", json={}, headers=H).status_code == 401
    assert client.post("/api/message", json={"text": "hello"}, headers=H).status_code == 401
    assert client.post("/api/submit", json={"agree": True, "signature": NAME}, headers=H).status_code == 401


# -- through the review app: work lists, Search, All clients, the restriction, the paralegal ----------------------------------------


def test_an_ended_case_leaves_the_work_lists_stays_in_search_and_all_clients_and_keeps_its_restriction(server, app, world, monkeypatch, tmp_path):  # noqa: F811
    import documents
    path = world / "case-rosa" / documents.FILE
    evidence = json.loads(path.read_text(encoding="utf-8"))
    expected = {row["id"]: row.get("expires") for row in evidence["documents"]}
    for row in evidence["documents"]:
        row["dates_read"] = True  # fictional current reader result, not an unread legacy row
        row["expires_kind"] = "expires" if row.get("expires") else None
    path.write_text(json.dumps(evidence), encoding="utf-8")
    assert {row["id"]: row.get("expires") for row in documents.load(world / "case-rosa")["documents"]} == expected
    app.roster.sync()
    app.roster.wait(seconds=30)
    assert app.roster.ready
    current = next(row for row in app._rows({"role": "attorney", "email": "sam@firm.example"}) if row["id"] == "case-rosa")
    assert {row["expiry"]["document"]["id"]: row["expiry"]["ends"] for row in current["journey"]["deadlines"] if row.get("expiry")} == {
        key: value for key, value in expected.items() if value}
    monkeypatch.setattr(settings, "PATH", world.parent / "settings.json")
    monkeypatch.setenv("I485_RULES_APPROVED", str(world.parent / "rules_approved.json"))
    sam, jane = sign_in(server, "sam@firm.example"), sign_in(server, "jane@firm.example")
    status, text = call(server + "/api/engagement", sam, {"client": "case-rosa", "action": "end", "state": "closed", "reason": "Done."})
    assert status == 400 and "approves the letters' wording first" in json.loads(text)["error"]  # no end letter before the wording is approved
    approval.approve(engagement.PRACTICE_ID, "Sam Attorney", "attorney")
    assert "case-rosa" in call(server + "/api/expiring?days=365", sam)[1]
    status, text = call(server + "/api/engagement", jane, {"client": "case-ana", "action": "end", "state": "closed", "reason": "Done."})
    assert status == 403 and "Only an attorney" in json.loads(text)["error"]  # a paralegal refused
    assert call(server + "/api/engagement", sam, {"client": "case-rosa", "action": "end", "state": "closed", "reason": "Done."})[0] == 200
    work = call(server + "/api/work", sam)[1]
    overview = json.loads(call(server + "/api/overview?ended=all", sam)[1])
    assert "case-rosa" not in work and "case-rosa" not in call(server + "/api/expiring?days=365", sam)[1]
    assert not any(d.get("client") == "case-rosa" for d in json.loads(call(server + "/api/deadlines?page=1", sam)[1])["items"]) and overview["ended"] == 1
    rosa = next(r for r in overview["clients"] if r["id"] == "case-rosa")
    assert rosa["end"]["state"] == "closed" and rosa["end"]["name"] == "Closed"
    assert sum(s["count"] for s in overview["stages"]) == overview["counts"]["total"] - 1
    reports = json.loads(call(server + "/api/reports", sam)[1])  # the reports count it once, on a row of its own
    by_stage = next(t for t in reports["tables"] if t["id"] == "stages")["rows"]
    assert by_stage[-1]["stage"].startswith("Ended") and by_stage[-1]["cases"] == 1 and sum(r["cases"] for r in by_stage) == reports["cases"]
    found = json.loads(call(server + "/api/search?q=sorocaba", sam)[1])
    assert any(r["case"] == "case-rosa" and r["end"]["state"] == "closed" for r in found["results"])
    # the restriction is untouched: Jane, not named on it, still cannot open it, and the attorney's view says so
    assert restricted.is_restricted(world / "case-rosa")
    assert call(server + "/api/engagement?client=case-rosa", jane)[0] == 404
    assert "case-rosa" not in call(server + "/api/clients", jane)[1]
    view = json.loads(call(server + "/api/engagement?client=case-rosa", sam)[1])
    assert view["state"] == "closed" and view["can_end"] and view["end"]["letter_name"] == "Closing letter"
    assert not json.loads(call(server + "/api/engagement?client=case-ana", jane)[1])["can_end"]
    # the letter opens, and the opening is logged
    status, _ = call(server + f"/api/engagement.pdf?client=case-rosa&id={view['end']['letter']}", sam)
    assert status == 200
    # reopened: back on the lists
    assert call(server + "/api/engagement", sam, {"client": "case-rosa", "action": "reopen", "reason": "A new filing."})[0] == 200
    assert "case-rosa" in call(server + "/api/expiring?days=365", sam)[1]
