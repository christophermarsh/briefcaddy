"""Protected fictional staff adapters; real localhost HTTP, synthetic dispatch only."""
import base64
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from portal.app import create_app
from portal.notify import Notifier
from portal.store import _now
from test_prospects import firm, server, call, ok, CALL  # noqa: F401 -- shared canonical installation/server fixtures

# Fixture arguments intentionally shadow imported pytest fixtures.
# ruff: noqa: F811


def client(srv, *, language="en", name="Fictional Consent Person"):
    found = ok(srv, "jane", "/api/conflict-search", {"purpose": "add", "name": name})
    return ok(srv, "jane", "/api/client-add", {"name": name, "phone": "", "email": name.lower().replace(" ", "-") + "@example.test",
        "language": language, "filing": "i485", "consent": {"email": True}, "invite": False,
        "conflict": {"search": found["id"], "decision": "none"}})["id"]


def retain(srv, route, body, content=b'{"fictional":true,"actual_evidence":"review or signoff"}'):
    return ok(srv, "sam", route, dict(body, action="evidence", content_base64=base64.b64encode(content).decode()))["evidence"]


def wording(srv, lang="en"):
    ref = retain(srv, "/api/client-wording", {})
    reviewed = ok(srv, "sam", "/api/client-wording", {"action": "attorney_review", "language": lang, "evidence": ref,
        "actor_email": "spoofed@example.test", "root": "/other/firm"})
    assert reviewed["actor_email"] == "sam@firm.example"
    if lang != "en":
        ok(srv, "sam", "/api/client-wording", {"action": "translation_review", "language": lang, "evidence": ref,
            "reviewer_name": "Fictional qualified reviewer", "qualification": "Fictional actual qualified review"})
    return ref


def grant(srv, cid, *, lang="en", route="/api/communication", key="client"):
    wording(srv, lang)
    ref = retain(srv, route, {key: cid, "kind": "consent"})
    result = ok(srv, "jane", route, {key: cid, "action": "grant", "channel": "email", "evidence": ref,
        "client_approved_at": (_now() - timedelta(minutes=1)).isoformat(), "notice_version": "fictional-caller-notice-v1",
        "language": lang, "source_kind": "in_person", "approval_description": "FICTIONAL actual client approval in person",
        "actor_email": "sam@firm.example", "role": "attorney"})
    assert result["grant"]["actor"] == "jane@firm.example"
    return ref


def test_import_switch_cannot_grant_and_own_evidence_is_bounded(server, firm):
    cid = client(server)
    view = ok(server, "jane", "/api/communication?client=" + cid)
    assert not view["can_send"] and not view["readiness"]["ready"]
    for content in ("", "!invalid", base64.b64encode(b'[]').decode()):
        assert call(server, "jane", "/api/communication", {"client": cid, "action": "evidence", "kind": "consent", "content_base64": content})[0] == 400
    ref = retain(server, "/api/communication", {"client": cid, "kind": "consent"})
    assert ok(server, "jane", "/api/communication", {"client": cid, "action": "evidence_read", "evidence": ref})["content_base64"]
    other = client(server, name="Other Fictional Consent Person")
    assert call(server, "sam", "/api/communication", {"client": other, "action": "evidence_read", "evidence": ref})[0] == 400
    assert call(server, "sam", "/api/communication", {"client": cid, "action": "evidence_read", "evidence": dict(ref, path="/tmp/anything")})[0] == 400
    assert call(server, "sam", "/api/communication", {"client": cid, "action": "grant"})[0] == 400
    assert not ok(server, "jane", "/api/communication?client=" + cid)["can_send"]


def test_readiness_separates_attorney_and_qualified_review_and_changed_wording(server, firm):
    ref = retain(server, "/api/client-wording", {})
    assert call(server, "jane", "/api/client-wording", {"action": "attorney_review", "language": "ht", "evidence": ref, "role": "attorney"})[0] == 403
    ok(server, "sam", "/api/client-wording", {"action": "attorney_review", "language": "ht", "evidence": ref})
    assert ok(server, "sam", "/api/client-wording?language=ht")["reason"] == "qualified_translation_review_required"
    ok(server, "sam", "/api/client-wording", {"action": "translation_review", "language": "ht", "evidence": ref,
        "reviewer_name": "Fictional qualified reviewer", "qualification": "Fictional actual qualified review"})
    assert ok(server, "sam", "/api/client-wording?language=ht")["ready"]
    (firm["root"] / "src/portal/static/consent.html").write_text("Changed fictional wording")
    assert not ok(server, "sam", "/api/client-wording?language=ht")["ready"]


def test_documented_signoff_revoke_contact_change_and_retained_evidence(server, firm):
    cid = client(server)
    ref = grant(server, cid)
    assert ok(server, "jane", "/api/communication?client=" + cid)["can_send"]
    assert call(server, "jane", "/api/communication", {"client": cid, "action": "revoke", "channels": "email"})[0] == 400
    result = ok(server, "jane", "/api/communication", {"client": cid, "action": "revoke", "channels": ["email"]})
    assert result["state"] == "revoked" and result["audit_status"] in ("recorded", "unavailable")
    assert not ok(server, "jane", "/api/communication?client=" + cid)["can_send"]
    grant(server, cid)
    firm["store"].update_profile(cid, email="changed-fictional@example.test")
    assert not ok(server, "jane", "/api/communication?client=" + cid)["can_send"]
    assert ok(server, "jane", "/api/communication", {"client": cid, "action": "evidence_read", "evidence": ref})


def test_assisted_main_link_is_consent_only_then_separate_invite(server, firm, monkeypatch):
    cid = client(server)
    wording(server)
    assert call(server, "jane", "/api/client-link", {"client": cid})[0] == 403
    link = ok(server, "sam", "/api/client-link", {"client": cid})
    with TestClient(create_app(firm["portal"], base_url="http://testserver", secure_cookies=False)) as phone:
        response = phone.get(link["url"].replace("http://testserver", ""), follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/consent"
        assert phone.get("/api/me").status_code == 401
    grant(server, cid)
    monkeypatch.setattr(Notifier, "_email", lambda *a: "sent")
    assert ok(server, "jane", "/api/client-invite", {"client": cid})["delivery"]["status"] == "sent"


def test_prospect_evidence_uses_own_store_and_hidden_equals_nonexistent(server, firm):
    cid = client(server)
    pid = ok(server, "sam", "/api/prospect-new", CALL | {"name": "Protected Fictional Prospect", "kind": "vawa"})["id"]
    hidden = call(server, "jane", "/api/prospect-communication?prospect=" + pid)
    assert hidden == call(server, "jane", "/api/prospect-communication?prospect=nonexistent") and hidden[0] == 404
    ok(server, "sam", "/api/prospect-communication", {"prospect": pid, "action": "prepare"})
    ref = retain(server, "/api/prospect-communication", {"prospect": pid, "kind": "consent"})
    assert (firm["portal"] / "prospects/clients" / pid / "consent-evidence" / (ref["sha256"] + ".json")).is_file()
    assert call(server, "sam", "/api/communication", {"client": cid, "action": "evidence_read", "evidence": ref})[0] == 400


@pytest.mark.parametrize("provider,expected", [(None, "queued"), ("sent", "sent"), ("failed: uncertain", "failed")])
def test_reminder_attempt_vs_provider_acceptance_and_revoked_retry(server, firm, monkeypatch, provider, expected):
    cid = client(server)
    grant(server, cid)
    if provider:
        monkeypatch.setattr(Notifier, "_email", lambda *a: provider)
    result = ok(server, "jane", "/api/remind", {"client": cid})
    assert result["delivery"]["status"] == expected
    profile = firm["store"].profile(cid)
    assert profile.get("last_reminder_attempt_at")
    assert bool(profile.get("last_reminder_at")) == (provider == "sent")
    prior = profile.get("last_reminder_at")
    ok(server, "jane", "/api/communication", {"client": cid, "action": "revoke", "channels": ["email"]})
    result = ok(server, "jane", "/api/remind", {"client": cid})
    assert result["delivery"]["status"] == "none"
    assert firm["store"].profile(cid).get("last_reminder_at") == prior


def test_request_review_publishes_without_sending_and_requires_full_translation(server, firm):
    cid = client(server, language="pt")
    grant(server, cid, lang="pt")
    request = ok(server, "jane", "/api/ask", {"client": cid, "text": "Fictional choice?", "type": "choice", "queue": True,
        "options": ["A", "B"], "text_client": "Escolha ficticia?", "options_client": ["A", ""]})["request"]
    ref = retain(server, "/api/communication", {"client": cid, "kind": "request_wording"})
    body = {"client": cid, "action": "request_review", "request": request["id"], "mode": "translated", "evidence": ref,
        "reviewer_name": "Fictional reviewer", "qualification": "Fictional qualified review", "publish": True}
    assert call(server, "jane", "/api/communication", body)[0] == 400
    result = ok(server, "jane", "/api/communication", body | {"mode": "english_fallback", "fallback_reason": "Actual fictional deliberate whole-English choice"})
    assert result["ready"] and result["published"] and result["notified"] is False
    assert not (firm["portal"] / "outbox.jsonl").exists()


def test_current_actor_disabled_cannot_reuse_cookie_or_spoof_body(server, firm):
    cid = client(server)
    server["accounts"].update("jane@firm.example", active=False)
    assert call(server, "jane", "/api/communication", {"client": cid, "action": "revoke", "channels": ["email"], "actor_email": "sam@firm.example"})[0] == 401


def test_missing_profile_same_id_recovery_with_current_acl(server, firm, monkeypatch):
    from portal import contact_transitions as transitions
    import restricted
    cid = "fictional-interrupted-enrollment"
    (firm["clients"] / cid).mkdir()
    store = firm["store"]
    with monkeypatch.context() as fault:
        save = transitions._save
        def crash(*args):
            save(*args)
            if (args[-1].get("transition") or {}).get("enrollment"):
                raise OSError("fictional coordinator crash")
        fault.setattr(transitions, "_save", crash)
        with pytest.raises(OSError):
            store.add_client(cid, "Fictional Interrupted Enrollment", email="interrupted@example.test", language="en")
    assert not (store.client_dir(cid) / "profile.json").exists()
    assert call(server, "jane", "/api/communication?client=" + cid)[0] == 404
    view = ok(server, "jane", "/api/contact-recovery?client=" + cid)
    assert view["state"] == "pending" and view["proposed_profile"]["id"] == cid
    restricted.mark(firm["clients"] / cid, True, "Fictional restricted recovery", "Sam Attorney", "attorney")
    hidden = call(server, "jane", "/api/contact-recovery?client=" + cid)
    assert hidden == call(server, "jane", "/api/contact-recovery?client=unknown-fictional") and hidden[0] == 404
    assert call(server, "jane", "/api/contact-recovery", {"client": cid, "action": "recover_enrollment", "role": "attorney"})[0] == 404
    result = ok(server, "sam", "/api/contact-recovery", {"client": cid, "action": "recover_enrollment"})
    assert result["state"] == "ready" and result["client"] == cid
    assert store.profile(cid)["name"] == "Fictional Interrupted Enrollment"
    assert not (store.client_dir(cid + "-2")).exists()
    assert not ok(server, "sam", "/api/communication?client=" + cid)["can_send"]


def test_declined_channel_direct_request_and_retry_never_calls_provider(server, firm, monkeypatch):
    cid = client(server)
    wording(server)
    calls = []
    monkeypatch.setattr(Notifier, "_email", lambda *args: calls.append(args) or "sent")
    request = ok(server, "jane", "/api/ask", {"client": cid, "text": "Fictional question", "queue": True})["request"]
    ref = retain(server, "/api/communication", {"client": cid, "kind": "request_wording"})
    ok(server, "jane", "/api/communication", {"client": cid, "action": "request_review", "request": request["id"],
        "evidence": ref, "mode": "as_written"})
    result = ok(server, "jane", "/api/ask-send", {"client": cid})
    assert result["delivery"]["status"] == "none" and not calls
    direct = ok(server, "jane", "/api/ask", {"client": cid, "text": "Fictional direct question"})
    assert direct["delivery"]["status"] == "none" and not calls
    assert call(server, "jane", "/api/ask-send", {"client": cid})[0] == 400
    assert not calls and not (firm["portal"] / "outbox.jsonl").exists()


def test_malformed_actions_and_encoded_evidence_limit_are_meaningful_4xx(server, firm):
    cid = client(server)
    for field in ("action", "language", "mode", "request", "channel", "kind", "format", "reviewer_name", "qualification"):
        assert call(server, "sam", "/api/communication", {"client": cid, "action": "evidence", field: ["invalid"]})[0] == 400
        assert call(server, "sam", "/api/client-wording", {"action": "attorney_review", field: ["invalid"]})[0] == 400
    for ref in ({"kind": [], "sha256": "x", "format": "json"}, {"kind": "consent", "sha256": {}, "format": "json"},
                {"kind": "consent", "sha256": "x", "format": []}, {"kind": "consent", "sha256": "x", "format": "json", "path": "/tmp"}):
        assert call(server, "sam", "/api/communication", {"client": cid, "action": "evidence_read", "evidence": ref})[0] == 400
    from review.server import MAX_BODY_EVIDENCE
    import http.client
    from urllib.parse import urlparse
    address = urlparse(server["base"])
    for route in ("/api/communication", "/api/client-wording"):
        connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
        connection.request("POST", route, headers={"X-Review-App": "1", "Content-Type": "application/json",
            "Cookie": server["sam"], "Content-Length": str(MAX_BODY_EVIDENCE + 1)})
        assert connection.getresponse().status == 413  # Refused before reading any body.
        connection.close()
    over = base64.b64encode(b"x" * (4 * 1024 * 1024 + 1)).decode()
    assert call(server, "sam", "/api/communication", {"client": cid, "action": "evidence", "kind": "consent", "content_base64": over})[0] == 400


def test_protected_contact_and_language_controls_require_exact_fields_and_invalidate_access(server, firm):
    from communication_fixture import accepted_link
    cid = client(server)
    grant(server, cid)
    token = accepted_link(firm["store"], cid)
    firm["store"].save_answers(cid, {"fictional_retained": "Retained original answer"})
    with TestClient(create_app(firm["portal"], base_url="http://testserver", secure_cookies=False)) as phone:
        assert phone.get("/l/" + token, follow_redirects=False).status_code == 303
        assert phone.get("/api/me").status_code == 200
        assert call(server, "jane", "/api/communication", {"client": cid, "action": "contact", "contacts": {"email": "new@example.test", "phone": "", "consent": True}})[0] == 400
        result = ok(server, "jane", "/api/communication", {"client": cid, "action": "language", "language": "pt", "actor_email": "sam@firm.example"})
        assert result["saved"] and result["notified"] is False and result["reconsent_required"]
        assert phone.get("/api/me").status_code == 401
    assert firm["store"].answers(cid)["fictional_retained"] == "Retained original answer"
    assert not ok(server, "jane", "/api/communication?client=" + cid)["can_send"]
    result = ok(server, "jane", "/api/communication", {"client": cid, "action": "contact", "contacts": {"email": "changed-safe@example.test", "phone": "(555) 010-1234"}})
    assert result["saved"] and result["audit_status"] in ("recorded", "unavailable")
    assert ok(server, "jane", "/api/communication?client=" + cid)["phone_access"] == "office_only"


@pytest.mark.parametrize("change", ["disabled", "acl_removed", "revoked", "enrollment_changed"])
def test_authenticated_service_rechecks_session_access_and_consent(server, firm, change):
    import restricted
    cid = client(server)
    token = server['jane'].split('=',1)[1]
    service = server['app'].communication_service()
    if change == 'acl_removed':
        restricted.mark(firm['clients']/cid, True, 'Fictional restriction', 'Sam Attorney', 'attorney')
        restricted.name_person(firm['clients']/cid, 'jane@firm.example', True, 'Sam Attorney', 'attorney')
    wording(server)
    ref = retain(server, '/api/communication', {'client':cid,'kind':'consent'})
    row = service.grant(token, cid, {'channel':'email','evidence':ref,
        'client_approved_at':(_now()-timedelta(minutes=1)).isoformat(),
        'notice_version':'fictional-caller-notice-v1','language':'en','source_kind':'in_person',
        'approval_description':'FICTIONAL documented client approval',
        'actor_email':'sam@firm.example','role':'attorney','firm_id':'foreign-request-value'})
    assert row['grant']['actor'] == 'jane@firm.example'
    assert service.status(token,cid)['can_send'] is (change != 'acl_removed')
    sent = []
    def sink(destination, link):
        sent.append((destination,link))
        return {'status':'sent'}
    if change == 'acl_removed':
        # Canonical protected-case lifecycle holds sends even for named staff.
        assert service.dispatch(token,cid,'email',sink)['status'] == 'held'
        assert not sent
    else:
        assert service.dispatch(token,cid,'email',sink)['status'] == 'sent'
        assert len(sent) == 1
        # A real client credential belongs to the portal principal, never staff.
        client_session = firm['store'].redeem_link(sent[0][1])
        assert client_session
        with pytest.raises(PermissionError):
            service.status(client_session,cid)
    if change == 'disabled':
        server['accounts'].update('jane@firm.example',active=False)
        with pytest.raises(PermissionError):
            service.status(token,cid)
        with pytest.raises(PermissionError):
            service.dispatch(token,cid,'email',sink)
    elif change == 'acl_removed':
        restricted.name_person(firm['clients']/cid,'jane@firm.example',False,'Sam Attorney','attorney')
        with pytest.raises(LookupError,match='unknown client'):
            service.status(token,cid)
        with pytest.raises(LookupError,match='unknown client'):
            service.dispatch(token,cid,'email',sink)
        assert call(server,'jane','/api/communication?client='+cid) == call(server,'jane','/api/communication?client=unknown-fictional')
    else:
        if change == 'revoked':
            service.revoke(token,cid,['email'])
        else:
            import json
            profile=firm['store'].profile(cid)
            # Adverse fixture changes the original enrollment's bound identity;
            # unchanged-profile updates must not be mislabeled reenrollment.
            profile['created_at']=(_now()-timedelta(days=1)).isoformat()
            (firm['store'].client_dir(cid)/'profile.json').write_text(json.dumps(profile),encoding='utf-8')
        assert service.dispatch(token,cid,'email',sink)['status'] == 'held'
        assert not service.status(token,cid)['can_send']
    assert len(sent) == (0 if change == 'acl_removed' else 1)


@pytest.mark.parametrize('state',['absent','unknown','expired','pending','support','disabled','must_change','unavailable','damaged_role'])
def test_direct_consent_service_requires_current_complete_staff_session(server, firm, state):
    import json
    from review.auth import _hash_token
    cid=client(server)
    token=server['jane'].split('=',1)[1]
    path=server['accounts'].path
    data=json.loads(path.read_text(encoding='utf-8'))
    session=data['sessions'][_hash_token(token)]
    if state=='absent':
        token=None
    elif state=='unknown':
        token='fictional-not-a-staff-session'
    elif state=='expired':
        session['expires']=(_now()-timedelta(seconds=1)).isoformat()
    elif state=='pending':
        session['pending']=True
    elif state=='support':
        data['users']['jane@firm.example']['role']='support'
    elif state=='damaged_role':
        data['users']['jane@firm.example']['role']=[]
    elif state=='disabled':
        data['users']['jane@firm.example']['active']=False
    elif state=='must_change':
        session.pop('how',None)
        data['users']['jane@firm.example']['must_change']=True
    path.write_text('{' if state=='unavailable' else json.dumps(data),encoding='utf-8')
    service=server['app'].communication_service()
    with pytest.raises(PermissionError):
        service.status(token,cid)
    with pytest.raises(PermissionError):
        service.revoke(token,cid,['email'])


def test_direct_consent_service_refuses_missing_or_foreign_account_mapping(server, firm, monkeypatch):
    cid=client(server)
    token=server['jane'].split('=',1)[1]
    app=server['app']
    with monkeypatch.context() as change:
        change.setattr(app,'accounts',None)
        assert app.may_open(None,cid)  # legacy visibility is not service authority
        with pytest.raises(PermissionError):
            app.communication_service().status(token,cid)
    with monkeypatch.context() as change:
        change.setattr(app.accounts,'path',firm['root']/'unconfigured'/'review_users.json')
        with pytest.raises(ValueError):
            app.communication_service().status(token,cid)
