"""Invitation history uses accepted synthetic delivery, never imported flags."""
# ruff: noqa: F811 -- imported pytest fixture is intentionally injected by name
import json
import re

import pytest

from portal import communication_consent as consent
from portal.notify import Notifier
from review import front_desk
from test_communication_consent import firm, granted, STAFF  # noqa: F401 -- shared fixture


def _successful_events(store, client):
    return [row for row in map(json.loads, (store.client_dir(client) / "events.jsonl").read_text().splitlines())
            if row["event"] == "invited"]


@pytest.mark.parametrize("outcome", ["dry-run (outbox)", "failed: synthetic provider", "uncertain"])
def test_unsuccessful_invitation_has_attempt_but_no_success(firm, monkeypatch, outcome):
    scope, store, client = firm
    granted(firm)
    tokens = []
    def provider(self, destination, subject, body, kind):
        tokens.append(re.search(r"/l/([A-Za-z0-9_-]+)", body).group(1))
        return outcome
    monkeypatch.setattr(Notifier, "_email", provider)
    result = front_desk.invite(store, client, "Fictional Staff", cases_root=scope.cases)
    profile = store.profile(client)
    assert profile["last_invite_attempt"]["result"] == result["delivery"]
    assert not any(profile.get(key) for key in ("invited_at", "last_invite_at", "last_invite_by", "last_invite"))
    assert not _successful_events(store, client)
    assert tokens and all(store.redeem_link(token) is None for token in tokens)


def test_later_revocation_preserves_success_and_records_held_attempt(firm, monkeypatch):
    scope, store, client = firm
    granted(firm)
    monkeypatch.setattr(Notifier, "_email", lambda *args: "sent")
    result = front_desk.invite(store, client, "Fictional Staff", cases_root=scope.cases)
    assert result["delivery"]["status"] == "sent"
    before = store.profile(client)
    assert before["invited_at"] and before["last_invite_at"]
    assert len(_successful_events(store, client)) == 1
    consent.revoke(scope, store, client, ["email"], actor_email=STAFF)
    monkeypatch.setattr(Notifier, "_email", lambda *args: pytest.fail("revoked invitation reached a provider"))
    result = front_desk.invite(store, client, "Fictional Other Staff", again=True, cases_root=scope.cases)
    after = store.profile(client)
    assert result["delivery"]["status"] == "none"
    for key in ("invited_at", "last_invite_at", "last_invite_by", "last_invite"):
        assert after[key] == before[key]
    assert after["last_invite_attempt"]["by"] == "Fictional Other Staff"
    assert after["last_invite_attempt"]["result"] == result["delivery"]
    assert len(_successful_events(store, client)) == 1
