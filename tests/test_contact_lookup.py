"""Actual public HTTP and trusted intake, entirely fictional/no provider network."""
import json
import re

from fastapi.testclient import TestClient
import pytest

import prospects
from portal.app import create_app
from portal import contact_access as contacts, communication_consent as consent
from portal.notify import Notifier
from portal.store import PortalStore
from review import front_desk
from test_contact_transitions import firm, second  # noqa: F401 -- pytest fixture registration and helper reexports
from test_contact_enrollment import conflict_fixture
from test_communication_consent import granted

H = {"X-Portal": "1"}


def browser(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, _, _ = firm
    calls = []
    for channel in ("email", "sms", "whatsapp"):
        def provider(self, destination, subject, body, kind, channel=channel):
            calls.append({"channel": channel, "destination": destination, "body": body})
            return "sent"
        monkeypatch.setattr(Notifier, "_" + channel, provider)
    app = create_app(root=scope.portal, base_url="https://fictional.example")
    return TestClient(app, base_url="https://fictional.example"), calls


def auth_bytes(scope):
    return {str(path): path.read_bytes() if path.exists() else None
            for path in (scope.portal / "auth.json", scope.portal / "prospects" / "auth.json")}


@pytest.mark.parametrize("prospect", [False, True])
def test_actual_http_dispatches_only_requested_safe_email_and_accepted_own_credential(firm, monkeypatch, prospect):  # noqa: F811 -- pytest fixture injection
    current = second(firm, prospect=True) if prospect else firm
    scope, store, client = current
    if prospect:
        store.update_profile(client, filing="first_contact", prospect=True)
    granted(current)
    http, calls = browser(firm, monkeypatch)
    email = store.profile(client)["email"]
    reply = http.post("/api/link", json={"contact": " " + email.upper() + " "}, headers=H)
    assert reply.status_code == 200 and reply.json() == {"ok": True}
    assert len(calls) == 1 and calls[0]["channel"] == "email" and calls[0]["destination"] == email
    token = re.search(r"/l/([A-Za-z0-9_-]+)", calls[0]["body"]).group(1)
    assert store.redeem_link(token)
    wrong = firm[1] if prospect else prospects.store(firm[0].portal)
    assert wrong.redeem_link(token) is None


@pytest.mark.parametrize("condition", ["absent", "ambiguous", "overlong", "list", "number", "restricted", "stopped", "missing_consent", "phone", "local_phone", "corrupt", "missing_profile"])
def test_public_denials_have_same_response_no_auth_outbox_or_provider(firm, monkeypatch, condition):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm)
    value = store.profile(client)["email"]
    if condition == "absent":
        value = "missing@fictional.example"
    elif condition == "ambiguous":
        other = second(firm, prospect=True, same_id=True)
        other[1].update_profile(other[2], email=value)
        assert not contacts.lookup_contact(scope, value)["matched"]
        assert store.find_by_contact(value) is None and other[1].find_by_contact(value) is None
    elif condition == "overlong":
        value += "x" * 300
    elif condition == "list":
        value = [value]
    elif condition == "number":
        value = 16175550101
    elif condition == "restricted":
        (scope.cases / client / "access.json").write_text(json.dumps({"marked": {"on": True}, "people": [], "history": []}))
    elif condition == "stopped":
        store.update_profile(client, declined_on="2026-10-05")
    elif condition == "missing_consent":
        consent.revoke(scope, store, client, ["email"], actor_email="staff@fictional.example")
    elif condition == "phone":
        granted(firm, "sms")
        value = "+1 (617) 555-0101"
    elif condition == "local_phone":
        value = "6175550101"
    elif condition in {"corrupt", "missing_profile"}:
        other = second(firm, prospect=True)
        path = other[1].client_dir(other[2]) / "profile.json"
        path.write_text("[]") if condition == "corrupt" else path.unlink()
    http, calls = browser(firm, monkeypatch)
    before = auth_bytes(scope)
    reply = http.post("/api/link", json={"contact": value}, headers=H)
    assert reply.status_code == 200 and reply.json() == {"ok": True}
    assert calls == [] and auth_bytes(scope) == before
    assert not (scope.portal / "outbox.jsonl").exists()
    assert not (scope.portal / "prospects" / "outbox.jsonl").exists()


def test_normalized_destination_rate_limit_cannot_be_reset_by_email_formatting(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    granted(firm)
    http, calls = browser(firm, monkeypatch)
    for value in ("a@fictional.example", " A@FICTIONAL.EXAMPLE ", "a@FICTIONAL.example", " a@fictional.example", "A@fictional.example ", "a@fictional.example"):
        assert http.post("/api/link", json={"contact": value}, headers=H).json() == {"ok": True}
    assert len(calls) == 5
    assert contacts.rate_identity("+1 (617) 555-0101") == contacts.rate_identity("+16175550101")
    assert contacts.rate_identity("+556175550101") != contacts.rate_identity("+16175550101")
    assert contacts.rate_identity("a@fictional.example" + "x" * 300) != contacts.rate_identity("a@fictional.example")


def test_existing_address_limiter_still_bounds_many_unknown_contacts(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    http, calls = browser(firm, monkeypatch)
    for index in range(120):
        assert http.post("/api/link", json={"contact": f"unknown-{index}@fictional.example"}, headers=H).status_code == 200
    assert http.post("/api/link", json={"contact": "other@fictional.example"}, headers=H).status_code == 429
    assert not calls


@pytest.mark.parametrize("prospect", [False, True])
def test_actual_intake_shared_phone_keeps_distinct_email_without_contact_authority(firm, monkeypatch, prospect):  # noqa: F811 -- pytest fixture injection
    scope, store, old_client = firm
    conflict_fixture(monkeypatch)
    granted(firm)
    body = {"name": "Fictional Separate Person", "email": "new@fictional.example", "phone": "+1 (617) 555-0101", "language": "en"}
    if prospect:
        result = prospects.create(scope.cases, body, "Fictional Display Staff", "paralegal", scope.portal)
        st = prospects.store(scope.portal)
        prospects.ensure_portal(st, result, "Fictional Display Staff")
        current = consent.Scope(scope.root, st.root, scope.data / "prospects"), st, result["id"]
    else:
        result = front_desk.add_client(store, scope.cases, body, "Fictional Display Staff", "paralegal", may_see=lambda _: False)
        current = scope, store, result["id"]
    assert result["phone_access"] == "office_only" and "office" in result["phone_note"]
    assert current[1].profile(current[2])["phone"] == "+16175550101"
    assert contacts.contact_eligibility(*current, "email")["eligible"]
    assert not contacts.contact_eligibility(*current, "sms")["eligible"]
    assert not consent.eligibility(*current, "email")["allowed"]
    assert consent._record(scope, store, old_client)["channels"]["email"]["state"] == "granted"


@pytest.mark.parametrize("prospect", [False, True])
@pytest.mark.parametrize("existing_prospect", [False, True])
def test_actual_intake_generic_duplicate_does_not_disclose_restricted_hidden_name(firm, monkeypatch, prospect, existing_prospect):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    existing = second(firm, prospect=True) if existing_prospect else firm
    es, st, cid = existing
    st.update_profile(cid, name="Hidden Fictional Name")
    (es.cases / cid / "access.json").write_text(json.dumps({"marked": {"on": True}, "people": [], "history": []}))
    before = set(scope.cases.iterdir())
    conflict_fixture(monkeypatch)
    body = {"name": "Fictional Applicant", "email": " " + st.profile(cid)["email"].upper() + " ", "language": "en"}
    with pytest.raises(ValueError) as error:
        if prospect:
            prospects.create(scope.cases, body, "Fictional Staff", "paralegal", scope.portal)
        else:
            front_desk.add_client(store, scope.cases, body, "Fictional Staff", "paralegal", may_see=lambda _: False)
    assert "Hidden Fictional Name" not in str(error.value) and cid not in str(error.value)
    assert "contact cannot be used" in str(error.value)
    assert set(scope.cases.iterdir()) == before


@pytest.mark.parametrize("phone", ["+12345678", "+55 (11) 5555-0101", "6175550101", "0199"])
@pytest.mark.parametrize("prospect", [False, True])
def test_creation_phone_validation_matches_full_country_or_office_only_context(firm, monkeypatch, phone, prospect):  # noqa: F811 -- pytest fixture injection
    scope, store, _ = firm
    conflict_fixture(monkeypatch)
    body = {"name": "Fictional Contact Context", "phone": phone, "email": "context@fictional.example", "language": "en"}
    result = prospects.create(scope.cases, body, "Fictional Staff", "paralegal", scope.portal) if prospect else front_desk.add_client(store, scope.cases, body, "Fictional Staff", "paralegal")
    assert result["phone_access"] == "office_only"
    assert store.find_by_contact("6175550101") is None


def test_utility_lookup_has_no_suffix_or_first_match_and_no_auth_authority(tmp_path):
    store = PortalStore(tmp_path / "utility")
    store.add_client("one", "Fictional One", email="one@fictional.example", phone="+16175550101")
    assert store.find_by_contact("+1 (617) 555-0101") == "one"
    assert store.find_by_contact("6175550101") is None
    store.add_client("two", "Fictional Two", email="one@fictional.example", phone="+16175550101")
    assert store.find_by_contact("one@fictional.example") is None
    assert store.find_by_contact("+16175550101") is None
    with pytest.raises(ValueError):
        store.communication_scope()


def test_malformed_http_json_is_generic_and_effect_free(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    http, calls = browser(firm, monkeypatch)
    before = auth_bytes(firm[0])
    response = http.post("/api/link", content="{", headers=H | {"Content-Type": "application/json"})
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert not calls and auth_bytes(firm[0]) == before


@pytest.mark.parametrize("prospect", [False, True])
def test_shared_phone_only_requires_generic_assisted_contact_correction(firm, monkeypatch, prospect):  # noqa: F811 -- pytest fixture injection
    scope, store, _ = firm
    conflict_fixture(monkeypatch)
    body = {"name": "Fictional Office Contact", "phone": "+1 (617) 555-0101", "language": "en"}
    with pytest.raises(ValueError, match="distinct safe email or assisted office intake"):
        if prospect:
            prospects.create(scope.cases, body, "Fictional Staff", "paralegal", scope.portal)
        else:
            front_desk.add_client(store, scope.cases, body, "Fictional Staff", "paralegal")


@pytest.mark.parametrize("phone", ["+012345678", "+1234567", "+1234567890123456", "+1+6175550101", "not a phone", "1" * 65])
def test_invalid_supported_full_number_is_not_guessed_into_office_or_country_identity(firm, phone):  # noqa: F811 -- pytest fixture injection
    with pytest.raises(ValueError):
        contacts.enrollment_contacts(firm[0], "unique@fictional.example", phone)
