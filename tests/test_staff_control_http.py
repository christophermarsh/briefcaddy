"""Protected current contact-control projection; fictional local callbacks only."""
# ruff: noqa: F811 -- canonical imported pytest fixtures are injected by name
from portal import communication_consent as consent
from test_prospects import firm, server, ok  # noqa: F401 -- fixtures
from test_staff_consent_http import client, grant, wording


def test_current_email_control_requires_accepted_redemption_not_manual_or_dispatch(server, firm):
    cid = client(server)
    wording(server)
    manual = ok(server, "sam", "/api/client-link", {"client": cid})
    token = manual["url"].rsplit("/", 1)[1]
    assert firm["store"].redeem_link(token)
    view = ok(server, "sam", "/api/communication?client=" + cid)
    assert not view["email_control"] and not view["contact_control"]["email"]["verified"]
    grant(server, cid)
    tokens = []
    consent.dispatch(firm["store"].communication_scope(), firm["store"], cid, "email", lambda address, token: tokens.append(token) or {"status": "sent"})
    assert not ok(server, "sam", "/api/communication?client=" + cid)["email_control"]
    assert firm["store"].redeem_link(tokens[0])
    view = ok(server, "sam", "/api/communication?client=" + cid)
    assert view["email_control"] and view["contact_control"]["email"]["verified"]
    assert not view["contact_control"]["sms"]["verified"] and not view["contact_control"]["whatsapp"]["verified"]
    ok(server, "jane", "/api/communication", {"client": cid, "action": "revoke", "channels": ["email"]})
    assert not ok(server, "sam", "/api/communication?client=" + cid)["email_control"]
    grant(server, cid)
    assert not ok(server, "sam", "/api/communication?client=" + cid)["email_control"]


import pytest  # noqa: E402
import re  # noqa: E402
from datetime import timedelta  # noqa: E402
from portal.notify import Notifier  # noqa: E402
from portal.store import _now  # noqa: E402
from test_prospects import call  # noqa: E402
from test_staff_consent_http import retain  # noqa: E402


@pytest.mark.parametrize("channel", ["sms", "whatsapp"])
def test_typed_neutral_phone_verification_current_actor_receipt_and_separate_invite(server, firm, monkeypatch, channel):
    cid = client(server)
    wording(server)
    email = firm["store"].profile(cid)["email"]
    ok(server, "jane", "/api/communication", {"client": cid, "action": "contact", "contacts": {"email": email, "phone": "+15550109876"}})
    calls = []
    def send(self, destination, subject, body, kind):
        calls.append((destination, body, kind)); return "sent"
    monkeypatch.setattr(Notifier, "_" + channel, send)
    held = ok(server, "jane", "/api/communication", {"client": cid, "action": "verify_contact", "channel": channel})
    assert held["verification"]["status"] == "held" and not calls
    ref = retain(server, "/api/communication", {"client": cid, "kind": "consent"})
    ok(server, "jane", "/api/communication", {"client": cid, "action": "grant", "channel": channel, "evidence": ref,
        "client_approved_at": (_now() - timedelta(minutes=1)).isoformat(), "notice_version": "fictional-caller-notice-v1", "language": "en",
        "source_kind": "in_person", "approval_description": "FICTIONAL own actual approval for this phone channel"})
    for bad in ("email", "bogus", [channel]):
        assert call(server, "jane", "/api/communication", {"client": cid, "action": "verify_contact", "channel": bad})[0] == 400
    result = ok(server, "jane", "/api/communication", {"client": cid, "action": "verify_contact", "channel": channel, "actor_email": "spoofed@example.test", "role": "attorney"})
    assert result["verification"]["status"] == "sent" and not result["questionnaire_access_granted"]
    assert len(calls) == 1 and calls[0][2] == "verify_contact" and cid not in calls[0][1]
    assert not ok(server, "jane", "/api/communication?client=" + cid)["contact_control"][channel]["verified"]
    token = re.search(r"/l/([A-Za-z0-9_-]+)", calls[0][1]).group(1)
    session = firm["store"].redeem_link(token)
    assert session and firm["store"].session_client(session) is None
    assert firm["store"].session_client(session, consent_only=True) == cid
    view = ok(server, "jane", "/api/communication?client=" + cid)
    assert view["contact_control"][channel]["verified"] and view["phone_access"] == "verified"
    assert view["can_send"] and not view["email_control"]
    ok(server, "jane", "/api/communication", {"client": cid, "action": "revoke", "channels": [channel]})
    assert not ok(server, "jane", "/api/communication?client=" + cid)["contact_control"][channel]["verified"]
    assert ok(server, "jane", "/api/communication", {"client": cid, "action": "verify_contact", "channel": channel})["verification"]["status"] == "held"
    assert len(calls) == 1
