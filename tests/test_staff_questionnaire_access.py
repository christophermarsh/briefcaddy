"""Real protected HTTP and own-client portal access with fictional records."""
# ruff: noqa: F811 -- shared pytest fixtures
import json
import io

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from portal.app import create_app
from portal.communication_consent import eligibility
from portal.contact_control import status
from portal.store import _hash
from test_prospects import firm, server, call, ok  # noqa: F401
from test_staff_consent_http import client


def access(server, cid, who="jane"):
    return ok(server, who, "/api/communication", {"client": cid, "action": "questionnaire_link", "agreed": True})["link"]


def test_paralegal_can_hand_over_full_questionnaire_without_provider_or_wording_review(server, firm):
    cid = client(server, language="pt")
    (firm["data"] / "communication_notice.json").unlink()
    for agreed in (None, False, "true", 1):
        assert call(server, "jane", "/api/communication", {"client": cid, "action": "questionnaire_link", "agreed": agreed})[0] == 400
    link = access(server, cid)
    from review.overview import add_portal, settle
    rows = {}
    add_portal(rows, firm["portal"] / "clients" / cid)
    settle(rows[cid])
    assert rows[cid]["stage"] == "invited"
    assert rows[cid]["questionnaire_link_created_at"]
    assert rows[cid]["invited_at"] is None  # manual handover never claims provider delivery
    assert link["hours"] == 72
    token = link["url"].rsplit("/", 1)[1]
    with TestClient(create_app(firm["portal"])) as phone:
        response = phone.get("/l/" + token, follow_redirects=False)
        assert response.status_code == 200 and "Open questionnaire" in response.text
        assert not phone.cookies
        # Link previews can visit repeatedly without using the credential.
        assert phone.get("/l/" + token).status_code == 200
        assert phone.post("/api/access-link", json={"token": token}).status_code == 403
        response = phone.post("/api/access-link", json={"token": token}, headers={"X-Portal": "1"})
        assert response.status_code == 200
        assert phone.get("/api/me").status_code == 200
        assert phone.put("/api/answers", json={"first_name": "Fictional"}, headers={"X-Portal": "1"}).status_code == 200
        saved = phone.put("/api/answers", json={"given_name": "Fictional"}, headers={"X-Portal": "1"})
        assert saved.status_code == 200 and "given_name" in saved.json()["accepted"]
        add_portal(rows, firm["portal"] / "clients" / cid)
        settle(rows[cid])
        assert rows[cid]["stage"] == "answering"
        assert rows[cid]["answers_so_far"]["answered"] >= 1
        passport = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(passport)
        uploaded = phone.post("/api/upload", data={"doc_id": "passport", "attempt": "f" * 32},
                              files={"file": ("fictional-passport.pdf", passport.getvalue(), "application/pdf")}, headers={"X-Portal": "1"})
        assert uploaded.status_code == 200 and uploaded.json()["upload_outcome"]["status"] == "complete"
        retained = firm["store"].uploads(cid)
        assert len(retained) == 1 and retained[0]["doc_id"] == "passport"
        original = firm["store"].client_dir(cid) / "uploads" / retained[0]["stored"]
        assert original.read_bytes() == passport.getvalue()
        assert cid in firm["store"].queued()
        from portal.demo import answers
        assert phone.put("/api/answers", json=answers(), headers={"X-Portal": "1"}).status_code == 200
        submitted = phone.post("/api/submit", json={"agree": True, "signature": "Fictional Phone Tester"}, headers={"X-Portal": "1"})
        assert submitted.status_code == 200
        assert firm["store"].profile(cid)["status"] == "submitted"
        assert cid in firm["store"].queued()
        assert phone.get("/l/" + token, follow_redirects=False).headers["location"] == "/?expired=1"
    assert not status(firm["store"].communication_scope(), firm["store"], cid, "email")["verified"]
    record = json.loads((firm["portal"] / "clients" / cid / "communication_consent.json").read_text())
    proof = record["questionnaire_access"]
    saved = json.loads((firm["portal"] / "clients" / cid / "consent-evidence" / (proof["sha256"] + ".json")).read_text())
    assert saved["actor"] == "jane@firm.example" and saved["actor_role"] == "paralegal"


@pytest.mark.parametrize("change", ["revoke", "phone", "disabled", "tamper", "replacement", "expiry"])
def test_handover_links_and_sessions_fail_closed_after_changes(server, firm, change):
    cid = client(server)
    token = access(server, cid)["url"].rsplit("/", 1)[1]
    session = firm["store"].redeem_link(token)
    assert session and firm["store"].session_client(session) == cid
    if change == "revoke":
        ok(server, "jane", "/api/communication", {"client": cid, "action": "revoke", "channels": ["email"]})
    elif change in ("phone", "language"):
        firm["store"].update_profile(cid, **({"phone": "+15550109999"} if change == "phone" else {"language": "es"}))
    elif change == "disabled":
        server["accounts"].update("jane@firm.example", active=False)
    elif change == "replacement":
        access(server, cid)
    else:
        auth = firm["store"]._auth()
        row = auth["sessions"][_hash(session)]
        if change == "expiry":
            row["expires"] = "2000-01-01T00:00:00+00:00"
            firm["store"]._write(firm["portal"] / "auth.json", auth)
        else:
            ref = row["evidence"]
            (firm["portal"] / "clients" / cid / "consent-evidence" / (ref["sha256"] + ".json")).write_text('{}')
    assert firm["store"].session_client(session) is None


@pytest.mark.parametrize("legacy", [False, True])
def test_language_switch_keeps_questionnaire_session_and_answers(server, firm, legacy):
    cid = client(server, language="en")
    token = access(server, cid)["url"].rsplit("/", 1)[1]
    if legacy:
        from portal.communication_consent import _manual_binding
        auth = firm["store"]._auth()
        row = auth["links"][_hash(token)]
        row.pop("questionnaire_binding_version")
        row["profile_sha256"] = _manual_binding(firm["store"].profile(cid))
        firm["store"]._write(firm["portal"] / "auth.json", auth)
    with TestClient(create_app(firm["portal"])) as phone:
        headers = {"X-Portal": "1"}
        assert phone.post("/api/access-link", json={"token": token}, headers=headers).status_code == 200
        saved = phone.put("/api/answers", json={"given_name": "Fictional"}, headers=headers)
        assert saved.status_code == 200 and "given_name" in saved.json()["accepted"]
        for lang in ("pt", "es", "en", "pt"):
            response = phone.post("/api/language", json={"language": lang}, headers=headers)
            assert response.status_code == 200
            assert response.json()["language"] == lang
            assert phone.get("/api/me").status_code == 200
        assert firm["store"].answers(cid)["given_name"] == "Fictional"
        firm["store"].update_profile(cid, phone="+15550109999")
        assert phone.get("/api/me").status_code == 401


def test_staff_records_client_permission_without_uploaded_evidence(server, firm):
    cid = client(server, language="pt")
    (firm["data"] / "communication_notice.json").unlink()
    body = {"client": cid, "action": "record_permission", "channels": ["email"], "agreed": True, "note": "Fictional client agreed in person."}
    token = access(server, cid)["url"].rsplit("/", 1)[1]
    assert call(server, "jane", "/api/communication", dict(body, agreed=False))[0] == 400
    result = ok(server, "jane", "/api/communication", body)
    assert result["saved"] and result["channels"] == ["email"]
    assert eligibility(firm["store"].communication_scope(), firm["store"], cid, "email")["allowed"]
    session = firm["store"].redeem_link(token)
    assert session and firm["store"].session_client(session) == cid
    firm["store"].update_profile(cid, email="changed@example.test")
    assert not eligibility(firm["store"].communication_scope(), firm["store"], cid, "email")["allowed"]


def test_hidden_case_access_and_missing_notice_report_expected_errors(server, firm):
    hidden = call(server, "jane", "/api/communication", {"client": "case-rosa", "action": "questionnaire_link", "agreed": True})
    missing = call(server, "jane", "/api/communication", {"client": "nonexistent", "action": "questionnaire_link", "agreed": True})
    assert hidden[0] == missing[0] == 404
    from test_staff_consent_http import retain
    ref = retain(server, "/api/client-wording", {})
    (firm["data"] / "communication_notice.json").unlink()
    code, message = call(server, "sam", "/api/client-wording", {"action": "attorney_review", "language": "pt", "evidence": ref})
    assert code == 400 and b"consent notice" in message


def test_prospect_questionnaire_handover_prepares_own_portal_without_extra_click(server, firm):
    from test_prospects import CALL
    pid = ok(server, "jane", "/api/prospect-new", CALL | {"name": "Fictional Handover Prospect"})["id"]
    result = ok(server, "jane", "/api/prospect-communication", {"prospect": pid, "action": "questionnaire_link", "agreed": True})
    from prospects import store
    prospects_store = store(firm["portal"])
    token = result["link"]["url"].rsplit("/", 1)[1]
    session = prospects_store.redeem_link(token)
    assert session and prospects_store.session_client(session) == pid
    assert firm["store"].session_client(session) is None

def test_busy_reader_preserves_questionnaire_session_and_answers(server, firm, monkeypatch):
    from contextlib import contextmanager
    from portal import communication_consent
    cid = client(server, language="pt")
    token = access(server, cid)["url"].rsplit("/", 1)[1]
    with TestClient(create_app(firm["portal"])) as phone:
        assert phone.post("/api/access-link", json={"token": token}, headers={"X-Portal": "1"}).status_code == 200
        assert phone.put("/api/answers", json={"given_name": "Fictional"}, headers={"X-Portal": "1"}).status_code == 200
        cookie = phone.cookies.get("portal_session")
        @contextmanager
        def busy(*args, **kwargs):
            raise TimeoutError("reader holds installation gate")
            yield
        with monkeypatch.context() as patch:
            patch.setattr(communication_consent, "gate", busy)
            for method, path, kwargs in [("get", "/api/me", {}), ("put", "/api/answers", {"json": {"given_name": "Changed"}, "headers": {"X-Portal": "1"}})]:
                response = getattr(phone, method)(path, **kwargs)
                assert response.status_code == 503
                assert response.json()["error"] == "server_busy"
                assert phone.cookies.get("portal_session") == cookie
        assert phone.get("/api/me").status_code == 200
        assert firm["store"].answers(cid)["given_name"] == "Fictional"
        assert phone.put("/api/answers", json={"given_name": "Changed"}, headers={"X-Portal": "1"}).status_code == 200
        assert firm["store"].answers(cid)["given_name"] == "Changed"

@pytest.mark.parametrize("filing", ["i485", "n400", "parole"])
def test_questionnaire_offers_distinct_license_front_and_back(filing):
    from portal.bank import bank_for, required_documents, load_help
    bank = bank_for({"filing": filing})
    offered = {d["id"]: d for d in required_documents({}, bank)}
    assert "drivers_license" in offered and "drivers_license_back" in offered
    for language in ("en", "pt", "es", "ht"):
        assert offered["drivers_license"]["label"][language] != offered["drivers_license_back"]["label"][language]
        assert load_help()["documents"]["drivers_license_back"][language]

def test_license_front_receipt_does_not_fill_back_slot(server, firm):
    from test_staff_upload_recovery import pdf
    cid = client(server, language="en")
    token = access(server, cid)["url"].rsplit("/", 1)[1]
    with TestClient(create_app(firm["portal"])) as phone:
        assert phone.post("/api/access-link", json={"token": token}, headers={"X-Portal": "1"}).status_code == 200
        def upload(side, attempt):
            return phone.post("/api/upload", data={"doc_id": side, "attempt": attempt}, files={"file": ("fictional-license.pdf", pdf(), "application/pdf")}, headers={"X-Portal": "1"})
        assert upload("drivers_license", "a" * 32).status_code == 200
        docs = {d["id"]: d for d in phone.get("/api/me").json()["documents"]}
        assert "front" in docs["drivers_license"]["label"] and "back" in docs["drivers_license_back"]["label"]
        assert len(docs["drivers_license"]["uploads"]) == 1
        assert not docs["drivers_license_back"]["uploads"]
        assert upload("drivers_license_back", "b" * 32).status_code == 200
        docs = {d["id"]: d for d in phone.get("/api/me").json()["documents"]}
        assert len(docs["drivers_license"]["uploads"]) == len(docs["drivers_license_back"]["uploads"]) == 1
