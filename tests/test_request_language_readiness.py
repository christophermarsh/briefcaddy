"""Fictional qualified review and intentional English fallback, no providers."""
import json

import pytest
from fastapi.testclient import TestClient

from portal import communication_consent as consent, request_readiness as language, questions, app
from portal.notify import Notifier
from test_communication_consent import firm, granted, STAFF  # noqa: F401 -- pytest fixture registration and helper reexports


def setup(firm, translated=True):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    store.update_profile(client, language="pt")
    granted(firm)
    typed = {"type": "choice", "language": "pt", "text_client": "Pergunta fictícia?" if translated else "", "machine_translated": translated,
             "options": [{"value": "One", "en": "First English choice", "client": "Primeira opção" if translated else ""},
                         {"value": "Two", "en": "Second English choice", "client": "Segunda opção" if translated else ""}]}
    request = store.add_request(client, "Fictional English question?", None, "Fictional Staff", typed=typed)
    return request


def proof(firm):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    return consent.retain_evidence(scope, json.dumps({"fictional": True, "statement": "Actual fictional review/English-choice evidence for this test"}).encode(),
                                   actor_email=STAFF, kind="request_wording", store=store, client=client)


def review(firm, request, mode="translated", publish=True, **changes):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    args = {"actor_email": STAFF, "evidence_ref": changes["evidence_ref"] if "evidence_ref" in changes else proof(firm), "mode": mode, "publish": publish}
    if mode == "translated":
        args.update(reviewer_name="Fictional Qualified Reviewer", qualification="Fictional actual qualified review evidence")
    if mode == "english_fallback":
        args["fallback_reason"] = "Fictional explicitly documented choice to show this question in English; no comprehension assertion."
    return language.review_request(scope, store, client, request["id"], **(args | changes))


def test_machine_translation_alone_is_retained_visible_draft_not_published_or_notified(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    request = setup(firm)
    assert request["status"] == "draft" and request["language_hold"]
    assert store.requests(client)[0]["text_client"] == "Pergunta fictícia?" and not store.client_requests(client)
    assert store.send_drafts(client, "Fictional Staff") == []
    notifier = Notifier(scope.portal / "outbox.jsonl", cases_root=scope.cases, store=store)
    monkeypatch.setattr(notifier, "_email", lambda *a: pytest.fail("unreviewed request reached provider"))
    outcome = notifier.send(store.profile(client), "request")
    assert outcome[0]["result"] == "skipped" and outcome[0]["why"] == "request_language_review_required"
    with pytest.raises(LookupError):
        store.answer_request(client, request["id"], reply="One")


def test_actual_review_publish_real_client_view_and_reply_then_changed_labels_hold(firm):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    request = setup(firm)
    accepted = review(firm, request)
    assert accepted["published"] and accepted["notified"] is False
    current = store.requests(client)[0]
    assert not current["language_hold"] and current["machine_translated"]  # retained provenance, not approval inference
    tokens = []
    consent.dispatch(scope, store, client, "email", lambda _, token: tokens.append(token) or {"status": "sent"})
    session = store.redeem_link(tokens[0])
    with TestClient(app.create_app(scope.portal, base_url="https://fictional.example"), base_url="https://fictional.example") as http:
        http.cookies.set(app.COOKIE, session)
        view = http.get("/api/me")
        assert view.status_code == 200 and next(t for t in view.json()["tasks"] if t["id"] == request["id"])["text"] == "Pergunta fictícia?"
        replied = http.post("/api/request-reply", json={"request": request["id"], "reply": "One"}, headers={"X-Portal": "1"})
        assert replied.status_code == 200
        path = store.client_dir(client) / "requests.json"
        rows = json.loads(path.read_text())
        rows[0]["status"] = "open"
        rows[0]["options"][0]["client"] = "Changed after reviewed"
        path.write_text(json.dumps(rows))
        assert not any(t["id"] == request["id"] for t in http.get("/api/me").json()["tasks"])
        assert http.post("/api/request-reply", json={"request": request["id"], "reply": "One"}, headers={"X-Portal": "1"}).status_code == 400
    assert store.requests(client)[0]["language_hold"] and not store.client_requests(client)


def test_whole_english_fallback_is_explicit_evidenced_and_all_choice_labels_english(firm):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    request = setup(firm, translated=False)
    with pytest.raises(ValueError, match="complete"):
        review(firm, request)
    result = review(firm, request, mode="english_fallback")
    row = store.client_requests(client)[0]
    shown = questions.for_client(row, "pt")
    assert shown["text"] == "Fictional English question?" and shown["in_english"] and shown["intentional_english_fallback"]
    assert shown["fallback_reason"] == result["request"]["language_review"]["fallback_reason"]
    assert [o["label"] for o in shown["options"]] == ["First English choice", "Second English choice"]
    with pytest.raises(ValueError, match="explicit"):
        review(firm, row, mode="english_fallback", fallback_reason="")


@pytest.mark.parametrize("change", ["missing_evidence", "damaged_evidence", "actor_revoked", "foreign_case", "missing_qualification"])
def test_review_requires_fresh_authority_own_evidence_and_actual_qualified_provenance(firm, change):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    request = setup(firm)
    ref = proof(firm)
    args = {"evidence_ref": ref}
    if change == "missing_evidence":
        consent._evidence_path(scope, ref, client=client).unlink()
    elif change == "damaged_evidence":
        consent._evidence_path(scope, ref, client=client).write_bytes(b"damaged")
    elif change == "actor_revoked":
        path = scope.data / "review_users.json"
        accounts = json.loads(path.read_text()); accounts["users"][STAFF]["active"] = False
        path.write_text(json.dumps(accounts))
    elif change == "foreign_case":
        args["evidence_ref"] = ref | {"path": "client-b/evidence.json"}
    else:
        args["qualification"] = ""
    with pytest.raises((ValueError, PermissionError)):
        review(firm, request, **args)
    assert store.requests(client)[0]["status"] == "draft" and not store.client_requests(client)


def test_current_reviewed_exact_bank_text_only_not_arbitrary_doc_id_claim(firm):  # noqa: F811 -- pytest fixture injection
    from portal.bank import load_bank
    scope, store, client = firm
    granted(firm)
    doc = load_bank(scope.root / "schemas/questions/intake.json")["documents"][0]
    typed = {"type": "text", "language": "en", "text_client": doc["label"]["en"]}
    fixed = store.add_request(client, doc["label"]["en"], doc["id"], "Fictional Staff", typed=typed)
    assert fixed["status"] == "open" and not fixed["language_hold"]
    arbitrary = store.add_request(client, "Different arbitrary question", doc["id"], "Fictional Staff", typed=typed | {
        "language_review": {"mode": "as_written", "approved": True}})
    assert arbitrary["status"] == "draft" and arbitrary["language_hold"] and not arbitrary.get("language_review")


def test_native_english_review_uses_same_protected_publication_and_pending_draft(firm):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    request = store.add_request(client, "Fictional own English question", None, "Fictional Staff",
                                typed={"type": "text", "language": "en", "text_client": "Fictional own English question"})
    assert request["status"] == "draft"
    result = review(firm, request, mode="as_written", publish=False)
    assert result["ready"] and not result["published"] and store.requests(client)[0]["status"] == "draft"
    assert [r["id"] for r in store.send_drafts(client, "Fictional Staff")] == [request["id"]]
    assert store.client_requests(client)[0]["status"] == "open"


def test_installed_request_read_waits_then_uses_current_changed_content(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    import threading
    scope, store, client = firm
    request = setup(firm)
    review(firm, request)
    started, read_before_release = threading.Event(), threading.Event()
    failures, output = [], []
    original = store._read
    def observe(path, default):
        if path.name == "requests.json":
            read_before_release.set()
        return original(path, default)
    monkeypatch.setattr(store, "_read", observe)
    def read():
        started.set()
        try:
            output.extend(store.client_requests(client))
        except BaseException as exc:
            failures.append(exc)
    with consent.gate(scope):
        thread = threading.Thread(target=read)
        thread.start()
        assert started.wait(3) and not read_before_release.wait(0.1)
        path = store.client_dir(client) / "requests.json"
        rows = json.loads(path.read_text())
        rows[0]["options"][0]["client"] = "Changed before the reader acquired authority"
        consent._atomic(path, rows)
    thread.join(5)
    assert not thread.is_alive() and not failures and output == []
