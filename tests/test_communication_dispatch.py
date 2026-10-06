"""Real notifier/auth/store paths, with fictional evidence and mocked provider."""
import json
from datetime import timedelta
import re

import pytest
from fastapi.testclient import TestClient

from portal import communication_consent as consent
from portal.notify import Notifier
from portal.store import _hash, _now, LINK_TTL
from test_communication_consent import firm, granted, reviewed, STAFF, ATTORNEY  # noqa: F401 -- pytest fixture registration and helper reexports


def notifier(firm, monkeypatch, *, result="sent"):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    obj = Notifier(scope.portal / "outbox.jsonl", env={"PORTAL_BASE_URL": "https://fictional.example"}, cases_root=scope.cases, store=store)
    calls = []
    def provider(to, subject, body, kind):
        token = re.search(r"/l/([A-Za-z0-9_-]+)", body)
        calls.append({"to": to, "body": body, "kind": kind, "token": token.group(1) if token else None})
        if token:
            # Real redemption must deny the pending token. This consumes it,
            # as an early recipient/attacker attempt must never gain access.
            assert store.redeem_link(token.group(1)) is None
        return result
    monkeypatch.setattr(obj, "_email", provider)
    return obj, calls


def test_actual_notifier_missing_consent_writes_no_outbox_or_token(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    obj, calls = notifier(firm, monkeypatch)
    before = (scope.portal / "auth.json").read_bytes()
    assert store._auth() == {"links": {}, "sessions": {}}
    assert not obj.allowed(store.profile(client))
    assert obj.send(store.profile(client), "invite")[0]["result"] == "skipped"
    assert not calls and not obj.outbox.exists()
    assert (scope.portal / "auth.json").read_bytes() == before
    assert store._auth() == {"links": {}, "sessions": {}}
    with pytest.raises(PermissionError):
        store.new_link_token(client)


def test_early_real_redemption_does_not_get_revived_by_acceptance(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    obj, calls = notifier(firm, monkeypatch)
    assert obj.send(store.profile(client), "invite")[0]["result"] == "sent"
    assert calls[0]["token"] and store.redeem_link(calls[0]["token"]) is None
    assert not store._auth()["sessions"]


def test_actual_accepted_notifier_link_redeem_and_session_revoke(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    obj, calls = notifier(firm, monkeypatch)
    def provider(to, subject, body, kind):
        calls.append(re.search(r"/l/([A-Za-z0-9_-]+)", body).group(1))
        return "sent"
    monkeypatch.setattr(obj, "_email", provider)
    assert obj.send(store.profile(client), "invite", "https://fictional.example/l/OLD-PRECREATED-LINK")[0]["result"] == "sent"
    assert calls[0] != "OLD-PRECREATED-LINK"
    session = store.redeem_link(calls[0])
    assert session and store.session_client(session) == client
    assert store.redeem_link(calls[0]) is None
    consent.revoke(scope, store, client, ["email"], actor_email=STAFF)
    assert store.session_client(session) is None


def test_legacy_tokens_and_sessions_never_authenticate(firm):  # noqa: F811 -- pytest fixture injection
    _, store, client = firm
    store._write(store.root / "auth.json", {"links": {_hash("old-link"): {"client": client, "expires": (_now() + LINK_TTL).isoformat()}},
                                             "sessions": {_hash("old-session"): {"client": client, "expires": (_now() + LINK_TTL).isoformat()}}})
    assert store.redeem_link("old-link") is None
    assert store.session_client("old-session") is None


def test_stale_queued_profile_and_contact_restore_cannot_revive_link(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    obj, calls = notifier(firm, monkeypatch)
    monkeypatch.setattr(obj, "_email", lambda to, subject, body, kind: calls.append(re.search(r"/l/([A-Za-z0-9_-]+)", body).group(1)) or "sent")
    stale = store.profile(client)
    obj.send(stale, "invite")
    token = calls[0]
    store.update_profile(client, email="changed@fictional.example")
    assert store.redeem_link(token) is None
    assert obj.send(stale, "invite")[0]["result"] == "skipped"
    store.update_profile(client, email=stale["email"])
    assert store.redeem_link(token) is None


def test_contact_change_crash_durable_intent_blocks_actual_auth(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    tokens = []
    consent.dispatch(scope, store, client, "email", lambda _, token: tokens.append(token) or {"status": "sent"})
    old = store._auth()["links"][_hash(tokens[0])]
    monkeypatch.setattr(store, "end_sessions", lambda *a: (_ for _ in ()).throw(OSError("fictional cleanup failure")))
    with pytest.raises(OSError):
        store.update_profile(client, email="changed@fictional.example")
    assert consent._record(scope, store, client)["revocation_pending"]
    assert not consent.credential_valid(scope, store, old)
    assert store.redeem_link(tokens[0]) is None


def test_reimport_contact_invalidates_session_and_existing_grant(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    tokens = []
    consent.dispatch(scope, store, client, "email", lambda _, token: tokens.append(token) or {"status": "sent"})
    session = store.redeem_link(tokens[0])
    assert session
    store.add_client(client, "Fictional Client A", email="imported@fictional.example", language="en")
    assert store.session_client(session) is None
    assert not consent.eligibility(scope, store, client, "email")["allowed"]


def test_outbox_link_unusable_and_retry_rechecks_revocation(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    obj = Notifier(scope.portal / "outbox.jsonl", env={"PORTAL_BASE_URL": "https://fictional.example", "PORTAL_OUTBOX_FULL_LINKS": "1"}, cases_root=scope.cases, store=store)
    assert obj.send(store.profile(client), "invite")[0]["result"] == "dry-run (outbox)"
    row = json.loads(obj.outbox.read_text().splitlines()[0])
    token = re.search(r"/l/([A-Za-z0-9_-]+)", row["body"]).group(1)
    assert store.redeem_link(token) is None
    before = obj.outbox.read_bytes()
    consent.revoke(scope, store, client, ["email"], actor_email=STAFF)
    assert obj.send(store.profile(client), "invite")[0]["result"] == "skipped"
    assert obj.outbox.read_bytes() == before


def test_manual_bootstrap_is_consent_only_and_not_email_control(firm):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    reviewed(firm)
    token = consent.manual_bootstrap(scope, store, client, actor_email=STAFF)
    row = store._auth()["links"][_hash(token)]
    assert row["purpose"] == "consent_only" and row["email_control"] is False
    session = store.redeem_link(token)
    assert session and store.session_client(session) is None
    assert store.session_client(session, consent_only=True) == client
    assert not consent.eligibility(scope, store, client, "email")["allowed"]


def test_manual_bootstrap_fresh_staff_contact_and_wording(firm):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    reviewed(firm)
    token = consent.manual_bootstrap(scope, store, client, actor_email=STAFF)
    path = scope.data / "review_users.json"
    rows = json.loads(path.read_text())
    rows["users"][STAFF]["active"] = False
    path.write_text(json.dumps(rows))
    assert store.redeem_link(token) is None
    with pytest.raises(PermissionError):
        consent.manual_bootstrap(scope, store, client, actor_email=STAFF)


def test_main_and_prospect_use_same_gate_but_distinct_notifier_store(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    import prospects
    scope, _, _ = firm
    pscope = consent.Scope(scope.root, scope.data / "portal/prospects", scope.data / "prospects")
    store = prospects.store(scope.portal)
    (pscope.cases / "prospect-a").mkdir(parents=True)
    store.add_client("prospect-a", "Fictional Prospect", email="prospect@fictional.example", language="en")
    pfirm = pscope, store, "prospect-a"
    granted(pfirm)
    obj, calls = notifier(pfirm, monkeypatch)
    assert obj.scope.portal == pscope.portal and obj.scope.kind == "prospect"
    assert obj.send(store.profile("prospect-a"), "invite")[0]["result"] == "sent"
    assert calls[0]["to"] == "prospect@fictional.example"
    assert not (scope.portal / "clients/prospect-a").exists()


def test_admin_send_no_pretoken_and_portal_link_missing_consent(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    from portal import admin, app
    scope, store, client = firm
    obj, calls = notifier(firm, monkeypatch)
    monkeypatch.setattr(store, "new_link_token", lambda *a: pytest.fail("legacy pretoken issued"))
    assert admin.send(store, obj, client, "invite")[0]["result"] == "skipped"
    portal_app = app.create_app(root=scope.portal, notifier=obj, base_url="https://fictional.example")
    with TestClient(portal_app) as http:
        response = http.post("/api/link", json={"contact": "a@fictional.example"}, headers={"X-Portal": "1"})
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert not calls


def test_received_and_appointment_messages_have_no_credential(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    obj, calls = notifier(firm, monkeypatch)
    assert obj.send(store.profile(client), "received")[0]["result"] == "sent"
    assert calls[0]["token"] is None
    assert not store._auth()["links"]


@pytest.mark.parametrize("damage", ["provider_missing", "provider_queued", "created_missing", "recorded_nonstring", "naive", "reversed", "future"])
def test_malformed_accepted_attempt_denies_actual_link_and_session(firm, damage):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    tokens = []
    outcome = consent.dispatch(scope, store, client, "email", lambda _, token: tokens.append(token) or {"status": "sent"})
    session = store.redeem_link(tokens[0])
    assert session and store.session_client(session) == client
    # Retain another real accepted link so both auth entry paths are exercised.
    second = consent.dispatch(scope, store, client, "email", lambda _, token: tokens.append(token) or {"status": "sent"})
    for attempt_id in (outcome["attempt"], second["attempt"]):
        path = store.client_dir(client) / "communication-attempts" / (attempt_id + ".json")
        row = json.loads(path.read_text())
        if damage == "provider_missing":
            row.pop("provider_status")
        elif damage == "provider_queued":
            row["provider_status"] = "queued"
        elif damage == "created_missing":
            row.pop("created_at")
        elif damage == "recorded_nonstring":
            row["recorded_at"] = []
        elif damage == "naive":
            row["recorded_at"] = "2026-01-01T00:00:00"
        elif damage == "reversed":
            row["created_at"] = (_now() - timedelta(minutes=1)).isoformat()
            row["recorded_at"] = (_now() - timedelta(minutes=2)).isoformat()
        else:
            row["recorded_at"] = (_now() + timedelta(days=1)).isoformat()
        path.write_text(json.dumps(row))
    assert store.redeem_link(tokens[1]) is None
    assert store.session_client(session) is None
