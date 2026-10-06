"""The verifier's findings on the agreement and the case's end (src/engagement.py), each held by a test: no letter reaches the client before its
wording is approved; a letter is sent only from the wording approved; a translation keeps every bracketed word of its English; a signature the case
has not seen still stops a decline; a declined or ended client gets nothing on any path; no code in a client's letter; the attorney sends; a prospect
not processed yet can be given an agreement or declined; the reports count an ended case once. Everyone here is made up ("Ana Clara Exemplo Souza")."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import clock
import engagement
import packet
import settings
from portal import messages
from portal.app import create_app
from portal.notify import Notifier
from portal.store import PortalStore
from rules import approval

sys.path.insert(0, str(Path(__file__).resolve().parent))
import firm_world  # noqa: E402
import schema_path
from communication_fixture import installation, approve_client, accepted_link

REPO = Path(__file__).resolve().parent.parent
NAME = "Ana Clara Exemplo Souza"
FEE = "A flat fee of $2,500."
H = {"X-Portal": "1"}


@pytest.fixture
def firm(tmp_path, monkeypatch):
    data = installation(tmp_path, monkeypatch)
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    clients = data / "clients"
    d = firm_world.make_case(clients, "case-ana")
    store = PortalStore(data / "portal")
    store.add_client("case-ana", NAME, email="ana@example.com", language="pt")
    return SimpleNamespace(root=data, clients=clients, case=d, store=store, portal=data / "portal")


def approve():
    approval.approve(engagement.PRACTICE_ID, "Sam Attorney", "attorney")


def app_for(firm):
    from review.server import ReviewApp

    return ReviewApp(firm.clients, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, portal_root=firm.portal)


def outbox(firm) -> int:
    p = firm.portal / "outbox.jsonl"
    return len(p.read_text(encoding="utf-8").splitlines()) if p.exists() else 0


def links(firm) -> int:
    p = firm.portal / "auth.json"
    return len(json.loads(p.read_text(encoding="utf-8")).get("links") or {}) if p.exists() else 0


# 1. no end letter before the wording is approved, and never a DRAFT letter on the client's page


def test_no_case_ends_before_the_wording_is_approved_and_no_draft_letter_reaches_the_portal(firm):
    with pytest.raises(ValueError, match="approves the letters' wording first"):
        engagement.end(firm.case, "closed", "Sam Attorney", "attorney", reason="Done.", portal_root=firm.portal)
    assert engagement.state(firm.case) == "open" and not firm.store.engagement("case-ana")
    approve()
    engagement.end(firm.case, "closed", "Sam Attorney", "attorney", reason="Done.", portal_root=firm.portal)
    shown = firm.store.engagement("case-ana")["ended"]["letter"]
    assert shown and "O seu assunto com o nosso escritório" in " ".join(shown["texts"]["pt"])
    rec = engagement.read(firm.case)
    draft = dict(rec["letters"][-1], approved=False)
    engagement._portal_end(firm.case, rec, draft, firm.portal)
    assert firm.store.engagement("case-ana")["ended"]["letter"] is None  # a letter not approved is never put in front of the client


# 2. a letter goes out only from the wording approved


def test_a_letter_made_from_wording_that_changed_since_is_made_again_before_it_is_sent_or_signed(firm):
    doc = engagement.document("main", "engagement")
    unapproved = "\n\n".join(doc["texts"]["en"]).replace("Thank you for choosing", "UNAPPROVED WORDING: thank you for choosing")
    engagement.save_document("main", "engagement", {"en": unapproved}, "Sam Attorney", "attorney")
    engagement.make_agreement(firm.case, ["i485"], FEE, "Paulo Paralegal", "paralegal", portal_root=firm.portal)
    letter_id = engagement.read(firm.case)["letters"][-1]["id"]
    assert engagement.read(firm.case)["letters"][-1]["wording_hash"] == engagement.document("main", "engagement")["wording_hash"]
    engagement.save_document("main", "engagement", {"en": "\n\n".join(doc["texts"]["en"])}, "Sam Attorney", "attorney")  # the attorney puts the shipped words back
    approve()  # and approves those
    for act in (lambda: engagement.send(firm.case, letter_id, "Sam Attorney", "attorney", firm.portal),
                lambda: engagement.paper(firm.case, letter_id, b"%PDF-1.4 a scan", "2026-10-04", "Paulo Paralegal", "paralegal", firm.portal)):
        with pytest.raises(ValueError, match="The wording changed since this letter was made: make it again."):
            act()
    assert engagement.view(firm.case, firm.portal, "attorney")["agreement"]["stale"]
    engagement.make_agreement(firm.case, ["i485"], FEE, "Paulo Paralegal", "paralegal", portal_root=firm.portal)
    again = engagement.read(firm.case)["letters"][-1]
    assert "UNAPPROVED" not in " ".join(again["texts"]["en"])
    engagement.send(firm.case, again["id"], "Sam Attorney", "attorney", firm.portal)
    assert firm.store.engagement("case-ana")["letter"]["id"] == again["id"]


# 3. a translation keeps every bracketed word of its English, paragraph by paragraph


def test_a_translation_that_drops_or_adds_a_bracketed_word_is_refused_naming_the_paragraph(firm):
    doc = engagement.document("main", "engagement")
    pt = list(doc["texts"]["pt"])
    dropped = [p.replace("[fee]", "a definir") for p in pt]
    with pytest.raises(ValueError, match=r"The Portuguese letter, paragraph 5, leaves out \[fee\]"):
        engagement.save_document("main", "engagement", {"pt": "\n\n".join(dropped)}, "Sam Attorney", "attorney")
    added = pt[:1] + [pt[1] + " [fee]"] + pt[2:]
    with pytest.raises(ValueError, match=r"paragraph 2, holds \[fee\], which the English paragraph does not"):
        engagement.save_document("main", "engagement", {"pt": "\n\n".join(added)}, "Sam Attorney", "attorney")
    with pytest.raises(ValueError, match="has 9 paragraphs and the English 10"):
        engagement.save_document("main", "engagement", {"pt": "\n\n".join(pt[:-1])}, "Sam Attorney", "attorney")
    assert engagement.document("main", "engagement")["version"] == 0  # nothing was saved


def test_the_english_sent_back_unchanged_or_put_back_as_it_was_leaves_the_translations_current(firm):
    doc = engagement.document("main", "closing")
    english = "\n\n".join(doc["texts"]["en"])
    assert engagement.save_document("main", "closing", {"en": english}, "Sam Attorney", "attorney")["version"] == 0  # no change, no new version
    edited = engagement.save_document("main", "closing", {"en": english.replace("With our best wishes,", "Best wishes,")}, "Sam Attorney", "attorney")
    assert not edited["current"]["pt"]
    back = engagement.save_document("main", "closing", {"en": english}, "Sam Attorney", "attorney")
    assert back["current"] == {"en": True, "pt": True, "es": True, "ht": True}


# 4. a signature the case has not recorded yet still stops a decline


def test_a_decline_after_a_portal_signature_the_case_had_not_seen_is_refused(firm):
    approve()
    engagement.make_agreement(firm.case, ["i485"], FEE, "Paulo Paralegal", "paralegal", portal_root=firm.portal)
    letter_id = engagement.read(firm.case)["letters"][-1]["id"]
    engagement.send(firm.case, letter_id, "Sam Attorney", "attorney", firm.portal)
    firm.store.sign_agreement("case-ana", letter_id, NAME, "203.0.113.9", "pt")  # the portal on another host: the case has not seen it
    with pytest.raises(ValueError, match="Withdraw or close it instead"):
        engagement.end(firm.case, "declined", "Sam Attorney", "attorney", reason="x", portal_root=firm.portal)
    assert engagement.read(firm.case)["letters"][-1]["signature"]["address"] == "203.0.113.9"


# 5. a declined or ended client gets nothing, on any path, and no link is made


@pytest.mark.parametrize("ending", ["declined", "closed"])
def test_a_declined_or_ended_client_gets_no_message_and_no_link_on_any_path(firm, ending):
    from review import front_desk

    approve()
    app = app_for(firm)
    approve_client(firm.store, "case-ana")
    assert Notifier(firm.portal / "outbox.jsonl", env={}, store=firm.store).send(firm.store.profile("case-ana"), "reminder")[0]["result"].startswith("dry-run")
    assert outbox(firm) == 1 and links(firm) == 1  # retained dry-run proof, never usable accepted access
    engagement.end(firm.case, ending, "Sam Attorney", "attorney", reason="Done.", portal_root=firm.portal)
    before, made = outbox(firm), links(firm)
    said = app.remind("case-ana", {"reviewer": "Paulo Paralegal"})["delivery"]
    assert said["status"] == "held" and said["text"].startswith("Not sent. The client's case with the office has ended")
    assert app.client_invite("case-ana", {"reviewer": "Paulo Paralegal", "again": True})["delivery"]["status"] == "held"
    assert app.ask_client("case-ana", {"reviewer": "Paulo Paralegal", "text": "Send your passport."})["delivery"]["status"] == "held"
    app.ask_client("case-ana", {"reviewer": "Paulo Paralegal", "text": "And your birth certificate.", "queue": True})
    with pytest.raises(ValueError, match="Nothing is waiting"):
        app.ask_send("case-ana", {"reviewer": "Paulo Paralegal"})
    assert any(r.get("language_hold") for r in firm.store.requests("case-ana"))  # retained drafts, not a send
    reply = firm.store.add_message("case-ana", "office", "We have your papers.", by="Paulo Paralegal")
    assert messages.notify(firm.store, Notifier(firm.portal / "outbox.jsonl", cases_root=firm.clients), "case-ana",
                           app._sign_in_link(firm.store, "case-ana"), reply["id"])["status"] == "held"
    assert engagement._tell_client(firm.store, "case-ana", firm.clients).startswith("Not sent.")
    client = TestClient(create_app(root=firm.portal, base_url="https://portal.example", secure_cookies=False,
                                   notifier=Notifier(firm.portal / "outbox.jsonl", env={}, cases_root=firm.clients)))
    client.post("/api/link", json={"contact": "ana@example.com"}, headers=H)  # "send me a link"
    with pytest.raises(ValueError, match="has ended"):
        front_desk.show_link(firm.store, "case-ana", "Sam Attorney", "https://portal.example", cases_root=firm.clients)
    assert (outbox(firm), links(firm)) == (before, made)  # nothing went out, and no sign-in link was made


# 6. no code in a client's letter, in any language


CODE = re.compile(r"\b[A-Z0-9]+_[A-Z0-9_]+\b|\b[A-Z]{1,4}\d{2,4}[A-Z]?\b")


def test_no_letter_in_any_language_holds_a_filing_code(firm, monkeypatch):
    for lang in engagement.LANGS:
        for f in packet.FILINGS:
            words = engagement._filing_words([f], lang)
            assert words and not CODE.search(words), (lang, f, words)
    assert "Suplemento B" in engagement._filing_words(["u_cert"], "pt") and "I-912" in engagement._filing_words(["i912"], "es")
    approve()
    for lang in engagement.LANGS:
        firm.store.update_profile("case-ana", language=lang)
        engagement.make_agreement(firm.case, list(packet.FILINGS), FEE, "Paulo Paralegal", "paralegal", portal_root=firm.portal)
        letter = engagement.read(firm.case)["letters"][-1]
        for lg, paras in letter["texts"].items():
            assert not CODE.search(" ".join(paras)), (lang, lg, CODE.findall(" ".join(paras)))
        assert set(letter["texts"]) == ({"en"} | {lang})


# 7. the office prepares the agreement; the attorney sends it


def test_a_paralegal_makes_the_agreement_and_records_a_paper_signature_and_only_an_attorney_sends_it(firm):
    approve()
    engagement.make_agreement(firm.case, ["i485"], FEE, "Paulo Paralegal", "paralegal", portal_root=firm.portal)
    letter_id = engagement.read(firm.case)["letters"][-1]["id"]
    with pytest.raises(PermissionError, match="Only an attorney sends"):
        engagement.send(firm.case, letter_id, "Paulo Paralegal", "paralegal", firm.portal)
    assert not engagement.view(firm.case, firm.portal, "paralegal")["can_send"]
    engagement.paper(firm.case, letter_id, b"%PDF-1.4 a scan", "2026-10-04", "Paulo Paralegal", "paralegal", firm.portal)
    assert engagement.read(firm.case)["letters"][-1]["signature"]["by"] == "Paulo Paralegal"


def test_sending_the_agreement_tells_the_client_there_is_news(firm):
    approve()
    approve_client(firm.store, "case-ana")
    engagement.make_agreement(firm.case, ["i485"], FEE, "Paulo Paralegal", "paralegal", portal_root=firm.portal)
    letter_id = engagement.read(firm.case)["letters"][-1]["id"]
    engagement.send(firm.case, letter_id, "Sam Attorney", "attorney", firm.portal)
    row = json.loads((firm.portal / "outbox.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["to"] == "ana@example.com" and "novidade no seu caso" in row["subject"] and "/l/" in json.dumps(row)  # "there's news", in Portuguese
    assert FEE not in json.dumps(row, ensure_ascii=False) and "contrato" not in json.dumps(row, ensure_ascii=False).lower()  # no case details in it
    assert engagement.read(firm.case)["letters"][-1]["sent"]["delivery"]


# 8. a prospect not processed yet; the conflict search's decline


def test_a_prospect_not_processed_yet_is_given_an_agreement_or_declined_and_a_conflict_decline_is_an_end(firm):
    approve()
    (firm.clients / "pilot-nova").mkdir()
    firm.store.add_client("pilot-nova", "Nova Exemplo Teste", email="nova@example.com", language="es")
    app = app_for(firm)
    assert app.engagement_view("pilot-nova")["state"] == "open" and not (firm.clients / "pilot-nova" / "fact_graph.json").exists()
    app.engagement_change("pilot-nova", {"action": "make", "filings": ["i485"], "fee": FEE, "reviewer": "Paulo Paralegal"}, "paralegal")
    assert (firm.clients / "pilot-nova" / engagement.FILE).exists() and not (firm.clients / "pilot-nova" / "fact_graph.json").exists()
    app.engagement_change("pilot-nova", {"action": "end", "state": "declined", "reason": "Not our kind of case.", "reviewer": "Sam Attorney"}, "attorney")
    letter = engagement.read(firm.clients / "pilot-nova")["letters"][-1]
    assert letter["kind"] == "non_engagement" and "Nova Exemplo Teste" in " ".join(letter["texts"]["es"])
    assert firm.store.stopped("pilot-nova")
    with pytest.raises(LookupError):
        app.engagement_view("nobody-here")
    # the conflict search declined another client: "Declined (conflict)", held, and offered the letter
    (firm.clients / "pilot-bia").mkdir()
    firm.store.add_client("pilot-bia", "Bia Exemplo", email="bia@example.com", language="pt")
    held = firm.clients / "pilot-bia"
    held.mkdir(exist_ok=True)
    (held / "conflict_check.json").write_text(json.dumps({"decision": {"decision": "declined", "at": "2026-10-04T09:00:00-04:00", "by": "Sam Attorney"}}),
                                              encoding="utf-8")
    assert engagement.conflict_declined(held) == {"state": "declined", "name": "Declined (conflict)", "since": "2026-10-04", "conflict": True}
    assert app.engagement_view("pilot-bia")["conflict_declined"]["name"] == "Declined (conflict)"
    assert not Notifier(firm.portal / "outbox.jsonl", cases_root=firm.clients).allowed(firm.store.profile("pilot-bia"))
    app.engagement_change("pilot-bia", {"action": "end", "state": "declined", "reason": "The conflict.", "reviewer": "Sam Attorney"}, "attorney")
    assert engagement.end_info(held)["name"] == "Declined"


# 10. the smaller ones


def test_the_keeping_date_follows_the_office_setting_set_after_the_case_ended(firm):
    approve()
    engagement.end(firm.case, "closed", "Sam Attorney", "attorney", reason="Done.", portal_root=firm.portal)
    words = engagement.view(firm.case, firm.portal, "attorney")["end"]["retention_words"]
    assert words.startswith("No keeping date yet") and "office office" not in words and "the Main office" in words or "office (Settings" in words
    settings.save("firm", {"office.retention_years": "6"}, "Sam Attorney")
    words = engagement.view(firm.case, firm.portal, "attorney")["end"]["retention_words"]
    assert words.startswith("Planning proposal only: Proposed date 10/05/2032") and "office office" not in words
    assert "retention" not in engagement.read(firm.case)["end"]  # only the end date is stored
    assert engagement.office_words("Main office") == "the Main office" and engagement.office_words("Orlando, FL") == "the Orlando, FL office"


def test_the_disengagement_letter_lists_a_deadline_already_passed_and_says_the_wording_is_the_offices(firm, monkeypatch):
    approve()
    monkeypatch.setattr(engagement, "known_deadlines", lambda d: [{"date": "2026-09-20", "what": "Answer the request for evidence", "passed": True},
                                                                  {"date": "2026-11-20", "what": "Biometrics", "passed": False}])
    engagement.end(firm.case, "withdrawn", "Sam Attorney", "attorney", reason="x", ended_by="client", portal_root=firm.portal)
    letter = engagement.read(firm.case)["letters"][-1]
    assert "09/20/2026 (already passed), Answer the request for evidence; 11/20/2026, Biometrics." in " ".join(letter["texts"]["en"])
    pt = " ".join(letter["texts"]["pt"])
    assert "20/09/2026 (já passou)" in pt and "está em inglês, como o escritório o registra" in pt


def test_an_ended_portal_refuses_every_write_and_a_long_name_is_refused_not_cut(firm):
    approve()
    engagement.make_agreement(firm.case, ["i485"], FEE, "Paulo Paralegal", "paralegal", portal_root=firm.portal)
    letter_id = engagement.read(firm.case)["letters"][-1]["id"]
    engagement.send(firm.case, letter_id, "Sam Attorney", "attorney", firm.portal)
    client = TestClient(create_app(root=firm.portal, base_url="https://portal.example", secure_cookies=False,
                                   notifier=Notifier(firm.portal / "outbox.jsonl", env={})))
    approve_client(firm.store, "case-ana")
    client.get(f"/l/{accepted_link(firm.store, 'case-ana')}", follow_redirects=False)
    r = client.post("/api/agreement", json={"letter": letter_id, "agree": True, "signature": "A" * 10000}, headers=H)
    assert (r.status_code, r.json()["detail"]) == (400, "name_too_long") and not firm.store.engagement("case-ana").get("signed")
    assert client.post("/api/agreement", json={"letter": letter_id, "agree": True, "signature": NAME}, headers=H).status_code == 200
    engagement.sync(firm.case, firm.portal)
    with pytest.raises(ValueError, match="too long"):
        engagement.countersign(firm.case, letter_id, "B" * 200, "Sam Attorney", "attorney", firm.portal)
    engagement.end(firm.case, "closed", "Sam Attorney", "attorney", reason="Done.", portal_root=firm.portal)
    assert client.post("/api/language", json={"language": "en"}, headers=H).status_code == 401
    assert client.post("/api/message-seen", headers=H).status_code == 401


def test_a_deadline_a_person_set_is_never_copied_into_the_disengagement_letter(monkeypatch):
    """docs/decisions.md, I1 after verification: the product's own deadlines go in the letter; a deadline a person typed (src/deadlines_set.py,
    marked "set") is the office's own note and stays out, whatever its date."""
    import journey

    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    monkeypatch.setattr(journey, "journey", lambda d: {"deadlines": [
        {"date": "2026-11-20", "what": "Answer the request for evidence on the I-360"},
        {"date": "2026-11-01", "what": "Call the client about the fee", "set": True, "who": "kim@firm.example"},
        {"date": "2026-09-20", "what": "File the motion", "set": True},
        {"what": "No date on this one"}]})
    out = engagement.known_deadlines(Path("nowhere"))
    assert out == [{"date": "2026-11-20", "what": "Answer the request for evidence on the I-360", "passed": False}]
