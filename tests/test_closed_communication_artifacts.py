"""Actual accepted credentials and attorney close; fictional stores, no provider."""
from datetime import timedelta
import json

import pytest
from fastapi.testclient import TestClient

import engagement
import firm_world
from portal import communication_consent as consent
from portal.app import COOKIE, create_app
from portal.store import _hash, _now
from rules import approval
from test_communication_consent import firm, granted, STAFF, ATTORNEY  # noqa: F401 -- pytest fixture registration and helper reexports


def accepted(firm):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    firm_world.make_case(scope.cases, client)
    granted(firm)
    approval.approve(engagement.PRACTICE_ID, "Fictional Attorney", "attorney")
    tokens = []
    result = consent.dispatch(scope, store, client, "email", lambda _, token: tokens.append(token) or {"status": "sent"})
    assert result["credential_active"]
    return tokens[0], result


def close(firm, state="closed"):  # noqa: F811 -- pytest fixture injection
    scope, _, client = firm
    return engagement.end(scope.cases / client, state, "Fictional Attorney", "attorney",
                          reason="FICTIONAL TEST: the matter ended.", ended_by="firm" if state == "withdrawn" else "",
                          portal_root=scope.portal)


@pytest.mark.parametrize("state", ["closed", "withdrawn"])
def test_actual_accepted_session_then_end_only_exposes_current_approved_snapshot(firm, state):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    token, _ = accepted(firm)
    with TestClient(create_app(root=scope.portal, base_url="http://testserver", secure_cookies=False)) as http:
        assert http.get("/l/" + token, follow_redirects=False).headers["location"] == "/"
        session = http.cookies.get(COOKIE)
        assert store.session_client(session) == client
        close(firm, state)
        shown = store.engagement(client)["ended"]["letter"]
        before = (scope.cases / client / engagement.FILE).read_bytes()
        projection = http.get("/api/me")
        assert projection.status_code == 200
        value = projection.json()
        assert set(value) == {"first_name", "language", "closed", "progress", "messages"}
        assert value["closed"]["state"] == state and value["closed"]["letter"] == shown
        assert http.get("/api/communication/closed-letter").json() == shown
        assert store.session_client(session) is None
        assert store.session_client(session, closed_artifacts=True) == client
        # Ordinary auth failure must not discard otherwise valid read-only access.
        for method, path, body in [("put", "/api/answers", {}), ("post", "/api/message", {"text": "fictional"}),
                                   ("post", "/api/language", {"language": "pt"}),
                                   ("post", "/api/submit", {})]:
            assert getattr(http, method)(path, json=body, headers={"X-Portal": "1"}).status_code == 401
        assert http.get("/api/communication/consent").status_code == 401
        assert http.get("/api/me").status_code == 200
        assert (scope.cases / client / engagement.FILE).read_bytes() == before
        assert not consent.dispatch(scope, store, client, "email", lambda *args: pytest.fail("closed case sent"))["status"] == "sent"
        with pytest.raises(PermissionError):
            store.new_link_token(client)


@pytest.mark.parametrize("damage", ["missing", "wrong", "unapproved", "end_mismatch", "damaged", "provider", "expired", "revoked", "contact", "purge", "destroyed", "wording"])
def test_closed_access_does_not_bypass_existing_proof_or_current_artifact_guards(firm, damage):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    token, result = accepted(firm)
    session = store.redeem_link(token)
    close(firm)
    assert store.session_client(session, closed_artifacts=True) == client
    path = scope.cases / client / engagement.FILE
    record = json.loads(path.read_text())
    if damage == "missing":
        record["letters"] = []
    elif damage == "wrong":
        record["end"]["letter"] = "L999"
    elif damage == "unapproved":
        record["letters"][-1]["approved"] = False
    elif damage == "end_mismatch":
        record["end"]["on"] = "2020-01-01"
    elif damage == "damaged":
        path.write_text("{fictional damaged record")
    elif damage == "provider":
        proof = store.client_dir(client) / "communication-attempts" / (result["attempt"] + ".json")
        row = json.loads(proof.read_text())
        row.pop("provider_status")
        proof.write_text(json.dumps(row))
    elif damage == "expired":
        auth = store._auth()
        auth["sessions"][_hash(session)]["expires"] = (_now() - timedelta(seconds=1)).isoformat()
        store._write(store.root / "auth.json", auth)
    elif damage == "revoked":
        consent.revoke(scope, store, client, ["email"], actor_email=STAFF)
    elif damage == "contact":
        from case_assignment import Conflict
        before = store.profile(client)
        with pytest.raises(Conflict):
            store.update_profile(client, email="changed@fictional.example")
        assert store.profile(client) == before
        assert store.session_client(session, closed_artifacts=True) == client
        # Deliberately damage fictional persisted input; supported mutation
        # refuses, and closed-only auth must still reject changed contact bytes.
        (store.client_dir(client) / "profile.json").write_text(json.dumps(before | {"email": "changed@fictional.example"}))
    elif damage == "purge":
        (scope.data / "purges.json").write_text(json.dumps({"cases": {client: {"state": "waiting"}}}))
    elif damage == "destroyed":
        (scope.data / "destroyed.json").write_text(json.dumps({"cases": [{"case": client}]}))
    elif damage == "wording":
        (scope.data / "communication_notice.json").write_text("{}")
    if damage in {"missing", "wrong", "unapproved", "end_mismatch"}:
        path.write_text(json.dumps(record))
    assert store.session_client(session, closed_artifacts=True) is None
    assert _hash(session) not in store._auth()["sessions"]
    with TestClient(create_app(root=scope.portal, base_url="http://testserver", secure_cookies=False)) as http:
        http.cookies.set(COOKIE, session)
        assert http.get("/api/me").status_code == 401
        assert http.get("/api/communication/closed-letter").status_code == 401


def test_end_cannot_redeem_preexisting_link_or_manual_principal_as_closed_session(firm):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    token, _ = accepted(firm)
    manual = consent.manual_bootstrap(scope, store, client, actor_email=ATTORNEY)
    close(firm)
    assert store.redeem_link(token) is None
    assert store.redeem_link(manual) is None


def test_decline_revokes_existing_accepted_session(firm):  # noqa: F811 -- pytest fixture injection
    _, store, _ = firm
    token, _ = accepted(firm)
    session = store.redeem_link(token)
    close(firm, "declined")
    assert store.session_client(session, closed_artifacts=True) is None
