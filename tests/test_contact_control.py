"""Accepted synthetic email redemption only; no real providers or messages."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from portal import communication_consent as consent, contact_control as control, contact_transitions as access
from portal.store import _hash
from test_communication_consent import firm as consent_firm, granted, reviewed, STAFF


@pytest.fixture
def firm(tmp_path, monkeypatch):
    return consent_firm.__wrapped__(tmp_path, monkeypatch)


def accepted(firm):
    tokens = []
    result = consent.dispatch(*firm, "email", lambda address, token: tokens.append(token) or {"status": "sent"})
    assert result["credential_active"]
    return tokens[0]


def test_accepted_dispatch_is_not_control_but_redemption_is_exact_current_control(firm):
    scope, store, client = firm
    granted(firm)
    token = accepted(firm)
    assert not control.status(*firm, "email")["verified"]
    session = store.redeem_link(token)
    assert session and store.session_client(session) == client
    receipt = access._record(*firm)["control_receipts"][0]
    assert receipt["credential_sha256"] == _hash(token)
    assert receipt["enrollment_sha256"] == control.enrollment(*firm)
    assert receipt["accepted_sha256"] == access._hash(control._accepted(scope, store, receipt))
    assert control.status(*firm, "email")["verified"]
    assert consent.client_context(scope, store, session)["email_control"]
    assert store.redeem_link(token) is None
    assert len(access._record(*firm)["control_receipts"]) == 1
    assert token not in json.dumps(receipt) and session not in json.dumps(receipt)


@pytest.mark.parametrize("status", ["dry-run", "queued", "unknown", "failed"])
def test_unaccepted_attempt_cannot_create_control(firm, status):
    granted(firm)
    tokens = []
    consent.dispatch(*firm, "email", lambda address, token: tokens.append(token) or {"status": status})
    assert firm[1].redeem_link(tokens[0]) is None
    assert not control.status(*firm, "email")["verified"]


def test_manual_link_stays_consent_only_without_control_receipt(firm):
    reviewed(firm)
    token = consent.manual_bootstrap(*firm, actor_email=STAFF)
    session = firm[1].redeem_link(token)
    assert session and firm[1].session_client(session) is None
    assert firm[1].session_client(session, consent_only=True) == firm[2]
    assert not access._record(*firm).get("control_receipts")
    assert consent.client_context(firm[0], firm[1], session)["email_control"] is False


@pytest.mark.parametrize("phase", ["consume", "receipt-before", "receipt-after", "session"])
def test_redemption_crashes_preserve_consume_before_proof_and_session(firm, monkeypatch, phase):
    scope, store, client = firm
    granted(firm)
    token = accepted(firm)
    real_write, real_save = store._write, access._save
    auth_writes = 0
    def write(path, data):
        nonlocal auth_writes
        if path == scope.portal / "auth.json":
            auth_writes += 1
            if (phase == "consume" and auth_writes == 1) or (phase == "session" and auth_writes == 2):
                raise OSError("Fictional auth crash")
        return real_write(path, data)
    def save(ms, st, cid, record):
        if record.get("control_receipts"):
            assert _hash(token) not in store._auth()["links"]
            assert store._auth()["sessions"] == {}
            if phase == "receipt-before":
                raise OSError("Fictional before receipt crash")
            real_save(ms, st, cid, record)
            if phase == "receipt-after":
                raise OSError("Fictional after receipt crash")
            return
        return real_save(ms, st, cid, record)
    monkeypatch.setattr(store, "_write", write)
    monkeypatch.setattr(access, "_save", save)
    assert store.redeem_link(token) is None
    assert not store._auth()["sessions"]
    assert control.status(*firm, "email")["verified"] is (phase in {"receipt-after", "session"})
    monkeypatch.setattr(store, "_write", real_write)
    monkeypatch.setattr(access, "_save", real_save)
    if phase == "consume":
        assert store.redeem_link(token)  # No durable effects preceded failed consumption.
    else:
        assert store.redeem_link(token) is None


def test_exact_one_use_concurrent_redemption_publishes_one_receipt(firm):
    granted(firm)
    token = accepted(firm)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(firm[1].redeem_link, [token, token]))
    assert sum(value is not None for value in results) == 1
    assert len(access._record(*firm)["control_receipts"]) == 1


@pytest.mark.parametrize("mutation", ["revoke", "contact", "duplicate", "nonce", "closure", "attempt", "receipt", "purpose"])
def test_control_and_session_recheck_current_authority(firm, mutation):
    scope, store, client = firm
    granted(firm)
    session = store.redeem_link(accepted(firm))
    assert session and control.status(*firm, "email")["verified"]
    if mutation == "revoke":
        consent.revoke(*firm, ["email"], actor_email=STAFF)
    elif mutation == "contact":
        store.update_profile(client, email="changed@fictional.example")
    elif mutation == "duplicate":
        (scope.cases / "client-b").mkdir()
        store.add_client("client-b", "Fictional Client B", email="a@fictional.example", language="en")
    elif mutation == "nonce":
        record = access._record(*firm)
        next(row for row in record["history"] if row["action"] == "enrollment_denied")["id"] = "c" * 32
        access._save(*firm, record)
    elif mutation == "closure":
        store.update_profile(client, closed_on="2026-10-05")
    elif mutation == "attempt":
        receipt = access._record(*firm)["control_receipts"][0]
        path = store.client_dir(client) / "communication-attempts" / (receipt["communication"] + ".json")
        record = json.loads(path.read_text())
        record["provider_status"] = "queued"
        path.write_text(json.dumps(record))
    elif mutation == "receipt":
        record = access._record(*firm)
        record["control_receipts"][0]["accepted_sha256"] = "d" * 64
        access._save(*firm, record)
    else:
        auth = store._auth()
        auth["sessions"][_hash(session)]["purpose"] = "notification"
        store._write(scope.portal / "auth.json", auth)
    if mutation != "purpose":
        assert not control.status(*firm, "email")["verified"]
    assert store.session_client(session) is None


def test_consumed_accepted_credential_without_receipt_cannot_authenticate(firm):
    granted(firm)
    token = accepted(firm)
    auth = firm[1]._auth()
    row = auth["links"].pop(_hash(token))
    auth["sessions"][_hash("fictional-session")] = row
    firm[1]._write(firm[0].portal / "auth.json", auth)
    assert firm[1].session_client("fictional-session") is None


def test_session_cannot_substitute_another_accepted_attempts_control_receipt(firm):
    granted(firm)
    first = firm[1].redeem_link(accepted(firm))
    second = firm[1].redeem_link(accepted(firm))
    auth = firm[1]._auth()
    auth["sessions"][_hash(second)]["control_receipt"] = auth["sessions"][_hash(first)]["control_receipt"]
    firm[1]._write(firm[0].portal / "auth.json", auth)
    assert firm[1].session_client(second) is None
    assert firm[1].session_client(first) == firm[2]


def test_staff_control_view_requires_current_own_case_authority(firm):
    granted(firm)
    firm[1].redeem_link(accepted(firm))
    assert control.staff_view(*firm, actor_email=STAFF)["email"]["verified"]
    with pytest.raises(PermissionError):
        control.staff_view(*firm, actor_email="absent@fictional.example")


def test_notification_has_no_credential_and_cannot_be_repurposed_to_login(firm):
    scope, store, client = firm
    granted(firm)
    tokens = []
    outcome = consent.dispatch(*firm, "email", lambda address, token: tokens.append(token) or {"status": "sent"}, credential=False)
    assert tokens == [None] and not outcome["credential_active"]
    assert not control.status(*firm, "email")["verified"]
    token = accepted(firm)
    row = store._auth()["links"][_hash(token)]
    path = store.client_dir(client) / "communication-attempts" / (row["communication"] + ".json")
    attempt = json.loads(path.read_text()) | {"purpose": "notification"}
    path.write_text(json.dumps(attempt))
    assert store.redeem_link(token) is None
    assert not control.status(*firm, "email")["verified"]


def test_contact_change_then_change_back_does_not_revive_receipt_or_grant(firm):
    granted(firm)
    session = firm[1].redeem_link(accepted(firm))
    original = firm[1].profile(firm[2])["email"]
    firm[1].update_profile(firm[2], email="changed@fictional.example")
    firm[1].update_profile(firm[2], email=original)
    assert not control.status(*firm, "email")["verified"]
    assert firm[1].session_client(session) is None
    assert not consent.eligibility(*firm, "email")["allowed"]
    granted(firm)
    assert not control.status(*firm, "email")["verified"]
    assert firm[1].redeem_link(accepted(firm))


def test_cross_store_duplicate_and_copied_receipt_never_grant_other_identity(firm):
    from test_contact_transitions import second
    granted(firm)
    session = firm[1].redeem_link(accepted(firm))
    original = access._record(*firm)["control_receipts"][0]
    other = second(firm, prospect=True, same_id=True)
    granted(other)
    record = access._record(*other)
    record["control_receipts"] = [original]
    access._save(*other, record)
    assert not control.status(*other, "email")["verified"]
    other[1].update_profile(other[2], email=firm[1].profile(firm[2])["email"])
    assert not control.status(*firm, "email")["verified"]
    assert firm[1].session_client(session) is None


def test_recreated_same_id_and_timestamp_cannot_reuse_copied_receipt_or_session(firm):
    import shutil
    scope, store, client = firm
    granted(firm)
    session = store.redeem_link(accepted(firm))
    old_profile = store.profile(client)
    receipt = access._record(*firm)["control_receipts"][0]
    attempt = control._accepted(scope, store, receipt)
    session_row = store._auth()["sessions"][_hash(session)]
    shutil.rmtree(store.client_dir(client))  # Fictional hard deletion and separately enrolled same-ID person.
    store.add_client(client, "Fictional Separately Enrolled", email=old_profile["email"], phone=old_profile["phone"], language="en")
    store.update_profile(client, created_at=old_profile["created_at"])
    granted(firm)
    assert control.enrollment(*firm) != receipt["enrollment_sha256"]
    record = access._record(*firm)
    record["control_receipts"] = [receipt]
    access._save(*firm, record)
    path = store.client_dir(client) / "communication-attempts" / (receipt["communication"] + ".json")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(attempt))
    auth = store._auth()
    auth["sessions"][_hash(session)] = session_row
    store._write(scope.portal / "auth.json", auth)
    assert not control.status(*firm, "email")["verified"]
    assert store.session_client(session) is None


def test_actual_http_shared_device_logout_then_separate_person_sign_in(firm, monkeypatch):
    from fastapi.testclient import TestClient
    from portal.app import COOKIE, create_app
    from test_contact_transitions import second
    monkeypatch.setenv("I485_PROSPECTS", str(firm[0].data / "prospects"))
    granted(firm)
    first_token = accepted(firm)
    other = second(firm)
    granted(other)
    next_token = accepted(other)
    with TestClient(create_app(firm[0].portal, base_url="https://fictional.example"), base_url="https://fictional.example") as http:
        assert http.get("/l/" + first_token, follow_redirects=False).status_code == 303
        first_cookie = http.cookies.get(COOKIE)
        assert firm[1].session_client(first_cookie) == firm[2]
        assert http.get("/api/me").status_code == 200
        assert http.post("/api/logout", headers={"X-Portal": "1"}).status_code == 200
        assert firm[1].session_client(first_cookie) is None
        assert http.get("/api/me").status_code == 401
        assert http.get("/l/" + first_token, follow_redirects=False).headers["location"] == "/?expired=1"
        assert http.get("/l/" + next_token, follow_redirects=False).status_code == 303
        assert firm[1].session_client(http.cookies.get(COOKIE)) == other[2]
        assert http.cookies.get(COOKIE) != first_cookie
        assert http.get("/api/me").status_code == 200
