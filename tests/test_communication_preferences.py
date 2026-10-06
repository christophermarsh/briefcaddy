"""Full explicit fictional preference snapshots and concurrent staff edits."""
# ruff: noqa: F811
import json
import pytest
from test_prospects import firm, server, call, ok  # noqa: F401
from test_staff_consent_http import client


def read(server, cid):
    code, body = call(server, "jane", "/api/communication?client=" + cid)
    assert code == 200
    return json.loads(body)["preferences"]


@pytest.mark.parametrize("channel", ["email", "sms", "whatsapp"])
def test_complete_preferences_round_trip_and_revoke_without_handover_side_effect(server, firm, channel):
    cid = client(server)
    firm["store"].update_profile(cid, phone="+15550101234")
    initial = read(server, cid)
    body = {"client": cid, "action": "record_permission", "channels": [channel], "agreed": True,
            "note": "Fictional client instruction in person.", "expected_revision": initial["revision"]}
    saved = ok(server, "jane", "/api/communication", body)
    current = read(server, cid)
    assert current["channels"] == [channel] and current["note"] == body["note"]
    assert current["provenance"][channel]["actor"] == "jane@firm.example"
    assert saved["revision"] == current["revision"]
    auth_before = firm["store"]._auth()
    revoked = ok(server, "jane", "/api/communication", dict(body, channels=[], note="Explicitly withdrawn.", expected_revision=current["revision"]))
    assert revoked["channels"] == [] and read(server, cid)["channels"] == []
    assert firm["store"]._auth() == auth_before
    assert not (firm["portal"] / "outbox.jsonl").exists()
    record = json.loads((firm["store"].client_dir(cid) / "communication_consent.json").read_text())
    assert record["channels"][channel]["state"] == "revoked"
    assert any(event.get("action") == "communication_preference_revoked" for event in record["history"])


def test_stale_staff_edit_and_invalid_channels_cannot_overwrite_permission(server, firm):
    cid = client(server)
    body = {"client": cid, "action": "record_permission", "channels": ["email"], "agreed": True, "note": "First", "expected_revision": 0}
    ok(server, "jane", "/api/communication", body)
    before = (firm["store"].client_dir(cid) / "communication_consent.json").read_bytes()
    for change in ({"note": "Stale overwrite"}, {"channels": [{}]}, {"expected_revision": True}):
        assert call(server, "jane", "/api/communication", dict(body, **change))[0] == 400
        assert (firm["store"].client_dir(cid) / "communication_consent.json").read_bytes() == before
