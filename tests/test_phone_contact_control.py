"""Synthetic neutral phone verification, real SDK STOP, no live providers."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from portal import communication_consent as consent, contact_control as control, contact_transitions as access, opt_out
from portal.app import COOKIE, create_app
from portal.notify import Notifier
from portal.store import _hash
from test_communication_consent import firm as consent_firm, granted, STAFF
from test_communication_stop import config as stop_config, signed


@pytest.fixture
def firm(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_PROSPECTS", str(tmp_path / "fictional-consent-firm/data/prospects"))
    return consent_firm.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def config(firm, monkeypatch):
    return stop_config.__wrapped__(firm, monkeypatch)


def neutral(firm, monkeypatch, channel="sms", status="sent"):
    scope, store, client = firm
    calls = []
    def provider(self, destination, subject, body, kind):
        calls.append({"destination": destination, "subject": subject, "body": body, "kind": kind})
        return status
    monkeypatch.setattr(Notifier, "_" + channel, provider)
    notifier = Notifier(scope.portal / "outbox.jsonl", env={"PORTAL_BASE_URL": "https://fictional.example"}, store=store, cases_root=scope.cases)
    outcome = notifier.verify_contact(store.profile(client), channel, actor_email=STAFF)
    token = re.search(r"/l/([A-Za-z0-9_-]+)", calls[0]["body"]).group(1) if calls else None
    return outcome, token, calls


@pytest.mark.parametrize("channel", ["sms", "whatsapp"])
def test_only_neutral_accepted_consumed_phone_challenge_enables_normal_dispatch(firm, monkeypatch, channel):
    granted(firm, channel)
    calls = []
    assert consent.dispatch(*firm, channel, lambda *args: calls.append(args))["reason"] == "contact_control_required"
    outcome, token, messages = neutral(firm, monkeypatch, channel)
    assert outcome["credential_active"] and messages[0]["kind"] == "verify_contact"
    assert "STOP" in messages[0]["body"]
    assert not any(word in messages[0]["body"] for word in ("Client A", "green card", "questionnaire", "case", "request for evidence"))
    assert not control.status(*firm, channel)["verified"]
    sender = Notifier(firm[0].portal / "outbox.jsonl", env={}, store=firm[1], cases_root=firm[0].cases)
    sender.send(firm[1].profile(firm[2]), "invite")
    assert len(messages) == 1  # The only pre-control send was the neutral challenge.
    verify_session = firm[1].redeem_link(token)
    assert verify_session and firm[1].session_client(verify_session) is None
    assert firm[1].session_client(verify_session, closed_artifacts=True) is None
    assert firm[1].session_client(verify_session, consent_only=True) == firm[2]
    context = consent.client_context(firm[0], firm[1], verify_session)
    assert context["consent_only"] and context["email_control"] is False
    assert next(row for row in context["channels"] if row["channel"] == channel)["dispatch_ready"]
    assert control.status(*firm, channel)["verified"]
    sender.send(firm[1].profile(firm[2]), "invite")
    assert len(messages) == 2 and messages[-1]["kind"] == "invite"
    tokens = []
    result = consent.dispatch(*firm, channel, lambda address, token: tokens.append(token) or {"status": "sent"})
    assert result["credential_active"]
    full = firm[1].redeem_link(tokens[0])
    assert full and firm[1].session_client(full) == firm[2]
    assert firm[1]._auth()["sessions"][_hash(full)]["purpose"] == "sign_in"
    assert calls == []


@pytest.mark.parametrize("phase", ["consume", "receipt-before", "receipt-after", "session"])
def test_phone_verification_crashes_cannot_upgrade_or_replay_after_consumption(firm, monkeypatch, phase):
    scope, store, client = firm
    granted(firm, "sms")
    _, token, _ = neutral(firm, monkeypatch)
    real_write, real_save = store._write, access._save
    writes = 0
    def write(path, value):
        nonlocal writes
        if path == scope.portal / "auth.json":
            writes += 1
            if (phase == "consume" and writes == 1) or (phase == "session" and writes == 2):
                raise OSError("Fictional phone auth crash")
        return real_write(path, value)
    def save(ms, st, cid, value):
        if value.get("control_receipts"):
            assert not st._auth()["sessions"] and _hash(token) not in st._auth()["links"]
            if phase == "receipt-before":
                raise OSError("Fictional before phone receipt crash")
            real_save(ms, st, cid, value)
            if phase == "receipt-after":
                raise OSError("Fictional after phone receipt crash")
            return
        return real_save(ms, st, cid, value)
    monkeypatch.setattr(store, "_write", write)
    monkeypatch.setattr(access, "_save", save)
    assert store.redeem_link(token) is None
    assert not store._auth()["sessions"]
    assert control.status(*firm, "sms")["verified"] is (phase in {"receipt-after", "session"})
    monkeypatch.setattr(store, "_write", real_write)
    monkeypatch.setattr(access, "_save", real_save)
    retry = store.redeem_link(token)
    if phase == "consume":
        assert retry and store.session_client(retry) is None
        assert store.session_client(retry, consent_only=True) == client
    else:
        assert retry is None


def test_later_cross_store_shared_phone_invalidates_receipt_and_regular_phone_session(firm, monkeypatch):
    from test_contact_transitions import second
    granted(firm, "sms")
    _, token, _ = neutral(firm, monkeypatch)
    verification = firm[1].redeem_link(token)
    tokens = []
    assert consent.dispatch(*firm, "sms", lambda address, link: tokens.append(link) or {"status": "sent"})["credential_active"]
    session = firm[1].redeem_link(tokens[0])
    assert session and firm[1].session_client(session) == firm[2]
    other = second(firm, prospect=True)
    other[1].update_profile(other[2], phone=firm[1].profile(firm[2])["phone"])
    assert not control.status(*firm, "sms")["verified"]
    assert firm[1].session_client(session) is None
    assert firm[1].session_client(verification, consent_only=True) is None
    calls = []
    assert consent.dispatch(*firm, "sms", lambda *args: calls.append(args))["status"] == "held" and not calls


def test_full_2000_profile_inventory_rechecks_late_phone_collision_without_names(firm, monkeypatch):
    scope, store, client = firm
    granted(firm, "sms")
    _, token, _ = neutral(firm, monkeypatch)
    session = store.redeem_link(token)
    peer_root = scope.data / "portal/prospects/clients"
    peer_root.mkdir(parents=True)
    late = None
    for number in range(1999):
        folder = peer_root / f"fictional-{number:04d}"
        folder.mkdir()
        late = folder / "profile.json"
        late.write_text(json.dumps({"id": folder.name, "name": "Fictional inventory only", "email": f"inventory-{number}@fictional.example", "phone": f"+1617556{number:04d}"}))
    assert control.status(*firm, "sms")["verified"]
    assert store.session_client(session, consent_only=True) == client
    row = json.loads(late.read_text())
    row["phone"] = store.profile(client)["phone"]
    late.write_text(json.dumps(row))
    result = control.status(*firm, "sms")
    assert not result["verified"] and "Fictional inventory" not in json.dumps(result)
    assert store.session_client(session, consent_only=True) is None


@pytest.mark.parametrize("hint", [None, "email", "foreign", ["sms"], {"channel": "sms"}, 1, True])
def test_public_phone_channel_invalid_hint_has_generic_response_and_no_effect(firm, monkeypatch, hint):
    granted(firm, "sms")
    _, token, _ = neutral(firm, monkeypatch)
    assert firm[1].redeem_link(token)
    calls = []
    for channel in ("email", "sms", "whatsapp"):
        monkeypatch.setattr(Notifier, "_" + channel, lambda *args: calls.append(args) or "sent")
    before = (firm[0].portal / "auth.json").read_bytes()
    with TestClient(create_app(firm[0].portal, base_url="https://fictional.example")) as http:
        response = http.post("/api/link", json={"contact": "+16175550101", "channel": hint}, headers={"X-Portal": "1"})
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert not calls and (firm[0].portal / "auth.json").read_bytes() == before


def test_public_phone_hint_is_bound_to_contact_and_never_falls_back(firm, monkeypatch):
    granted(firm, "sms")
    _, token, _ = neutral(firm, monkeypatch)
    assert firm[1].redeem_link(token)
    calls = []
    for channel in ("email", "sms", "whatsapp"):
        def provider(self, destination, subject, body, kind, channel=channel):
            calls.append(channel)
            return "sent"
        monkeypatch.setattr(Notifier, "_" + channel, provider)
    with TestClient(create_app(firm[0].portal, base_url="https://fictional.example")) as http:
        for body in ({"contact": "a@fictional.example", "channel": "sms"}, {"contact": "+16175550101", "channel": "whatsapp"}, {"contact": "6175550101", "channel": "sms"}):
            assert http.post("/api/link", json=body, headers={"X-Portal": "1"}).json() == {"ok": True}
        assert not calls
        assert http.post("/api/link", json={"contact": "+16175550101"}, headers={"X-Portal": "1"}).json() == {"ok": True}
        assert calls == ["sms"]  # Legacy absent hint still selects only SMS.


def test_verification_cookie_is_limited_on_every_actual_endpoint(firm, monkeypatch):
    granted(firm, "sms")
    _, token, _ = neutral(firm, monkeypatch)
    with TestClient(create_app(firm[0].portal, base_url="https://fictional.example"), base_url="https://fictional.example") as http:
        reply = http.get("/l/" + token, follow_redirects=False)
        assert reply.status_code == 303 and reply.headers["location"] == "/consent"
        assert http.cookies.get(COOKIE)
        assert http.get("/api/communication/consent").json()["consent_only"]
        for path in ("/api/me", "/api/communication/closed-letter", "/api/upload-outcome?attempt=fictional-attempt"):
            assert http.get(path).status_code == 401
        assert http.put("/api/answers", json={"first_name": "Fictional"}, headers={"X-Portal": "1"}).status_code == 401
        for path, body in (("/api/message", {"text": "Fictional"}), ("/api/message-seen", {}), ("/api/submit", {"agree": True, "signature": "Fictional"})):
            assert http.post(path, json=body, headers={"X-Portal": "1"}).status_code == 401
        assert http.post("/api/upload", files={"file": ("fictional.txt", b"fictional", "text/plain")}, data={"doc_id": "passport"}, headers={"X-Portal": "1"}).status_code == 401


@pytest.mark.parametrize("condition", ["no_signoff", "shared_main", "shared_prospect", "local", "restricted", "revoked", "wording", "actor"])
def test_neutral_challenge_refuses_unsafe_contact_or_current_authority_before_provider(firm, monkeypatch, condition):
    scope, store, client = firm
    if condition != "no_signoff":
        granted(firm, "sms")
    if condition in {"shared_main", "shared_prospect"}:
        from test_contact_transitions import second
        other = second(firm, prospect=condition == "shared_prospect")
        other[1].update_profile(other[2], phone=store.profile(client)["phone"])
    elif condition == "local":
        store.update_profile(client, phone="6175550101")
    elif condition == "restricted":
        (scope.cases / client / "access.json").write_text(json.dumps({"marked": {"on": True}, "people": [], "history": []}))
    elif condition == "revoked":
        consent.revoke(*firm, ["sms"], actor_email=STAFF)
    elif condition == "wording":
        path = scope.data / "communication_notice.json"
        row = json.loads(path.read_text())
        row["texts"]["en"] += " FICTIONAL CHANGE"
        path.write_text(json.dumps(row))
    elif condition == "actor":
        path = scope.data / "review_users.json"
        row = json.loads(path.read_text())
        row["users"][STAFF]["active"] = False
        path.write_text(json.dumps(row))
    if condition in {"actor", "restricted"}:
        with pytest.raises(PermissionError):
            neutral(firm, monkeypatch)
    else:
        outcome, token, calls = neutral(firm, monkeypatch)
        assert outcome["status"] == "held" and not token and not calls
    assert not control.status(*firm, "sms")["verified"]


@pytest.mark.parametrize("status", ["dry-run (outbox)", "queued", "failed"])
def test_nonaccepted_phone_challenge_never_verifies(firm, monkeypatch, status):
    granted(firm, "sms")
    result, token, _ = neutral(firm, monkeypatch, status=status)
    assert not result["credential_active"]
    assert firm[1].redeem_link(token) is None
    assert not control.status(*firm, "sms")["verified"]


@pytest.mark.parametrize("channel", ["sms", "whatsapp"])
def test_official_signed_stop_revokes_verified_phone_and_future_dispatch(firm, monkeypatch, config, channel):
    granted(firm, channel)
    _, token, _ = neutral(firm, monkeypatch, channel)
    session = firm[1].redeem_link(token)
    assert session and control.status(*firm, channel)["verified"]
    details = {"From": "whatsapp:+16175550101", "To": "whatsapp:" + config["TWILIO_WHATSAPP_FROM"]} if channel == "whatsapp" else {}
    _, body, signature = signed(config, **details)
    result = opt_out.ingest(firm[0], body, signature, env=config)
    assert result["state"] == "applied"
    assert not control.status(*firm, channel)["verified"]
    assert firm[1].session_client(session, consent_only=True) is None
    calls = []
    assert consent.dispatch(*firm, channel, lambda *args: calls.append(args))["status"] == "held"
    assert not calls


def test_signed_stop_pending_cleanup_denies_challenge_and_existing_receipt(firm, monkeypatch, config):
    granted(firm, "sms")
    _, token, _ = neutral(firm, monkeypatch)
    assert firm[1].redeem_link(token)
    _, body, signature = signed(config)
    receipt = opt_out.ingest(firm[0], body, signature, env=config, apply_now=False)
    assert receipt["state"] == "pending"
    assert not control.status(*firm, "sms")["verified"]
    result, token, calls = neutral(firm, monkeypatch)
    assert result["status"] == "held" and not token and not calls


def test_builtin_provider_does_not_post_without_exact_stop_configuration(firm, monkeypatch, config):
    granted(firm, "sms")
    def forbidden(*args, **kwargs):
        pytest.fail("No live provider call is authorized")
    import httpx
    monkeypatch.setattr(httpx, "post", forbidden)
    for changed in ({"TWILIO_STOP_WEBHOOK_URL": ""}, {"TWILIO_STOP_WEBHOOK_URL": "https://fictional.example/foreign"}, {"TWILIO_AUTH_TOKEN": ""}):
        notifier = Notifier(firm[0].portal / "outbox.jsonl", env=config | changed, store=firm[1], cases_root=firm[0].cases)
        result = notifier.verify_contact(firm[1].profile(firm[2]), "sms", actor_email=STAFF)
        assert not result["credential_active"]
    assert not control.status(*firm, "sms")["verified"]


def test_phone_receipt_failure_consumes_once_without_access_or_replay(firm, monkeypatch):
    granted(firm, "sms")
    _, token, _ = neutral(firm, monkeypatch)
    real_save = access._save
    def fail(scope, store, client, record):
        if record.get("control_receipts"):
            assert _hash(token) not in store._auth()["links"]
            raise OSError("Fictional verification receipt crash")
        return real_save(scope, store, client, record)
    monkeypatch.setattr(access, "_save", fail)
    assert firm[1].redeem_link(token) is None
    monkeypatch.setattr(access, "_save", real_save)
    assert firm[1].redeem_link(token) is None
    assert not control.status(*firm, "sms")["verified"]


def test_channel_proof_and_purpose_cannot_upgrade_other_phone_or_questionnaire(firm, monkeypatch):
    granted(firm, "sms")
    granted(firm, "whatsapp")
    _, token, _ = neutral(firm, monkeypatch)
    session = firm[1].redeem_link(token)
    assert session and control.status(*firm, "sms")["verified"]
    assert not control.status(*firm, "whatsapp")["verified"]
    assert consent.eligibility(*firm, "whatsapp")["reason"] == "contact_control_required"
    auth = firm[1]._auth()
    auth["sessions"][_hash(session)]["purpose"] = "sign_in"
    firm[1]._write(firm[0].portal / "auth.json", auth)
    assert firm[1].session_client(session) is None


@pytest.mark.parametrize("mutation", ["revoke", "contact_back", "shared", "nonce"])
def test_phone_challenge_and_control_recheck_authority_at_redemption_and_send(firm, monkeypatch, mutation):
    granted(firm, "sms")
    _, token, _ = neutral(firm, monkeypatch)
    if mutation == "revoke":
        consent.revoke(*firm, ["sms"], actor_email=STAFF)
    elif mutation == "contact_back":
        old = firm[1].profile(firm[2])["phone"]
        firm[1].update_profile(firm[2], phone="+16175550333")
        firm[1].update_profile(firm[2], phone=old)
        granted(firm, "sms")
    elif mutation == "shared":
        from test_contact_transitions import second
        other = second(firm, prospect=True)
        other[1].update_profile(other[2], phone=firm[1].profile(firm[2])["phone"])
    else:
        record = access._record(*firm)
        next(row for row in record["history"] if row.get("action") == "enrollment_denied")["id"] = "b" * 32
        access._save(*firm, record)
    assert firm[1].redeem_link(token) is None
    assert not control.status(*firm, "sms")["verified"]
    calls = []
    assert consent.dispatch(*firm, "sms", lambda *args: calls.append(args))["status"] == "held"
    assert calls == []
