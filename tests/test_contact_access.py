"""Fictional contact inventories only; no credentials, providers or approval."""
import json
from time import perf_counter

import pytest

from communication_fixture import installation
from portal import contact_access as contacts
from portal.communication_consent import Scope
from portal.store import PortalStore
import prospects


@pytest.fixture
def firm(tmp_path, monkeypatch):
    data = installation(tmp_path, monkeypatch)
    scope = Scope(tmp_path, data / "portal", data / "clients")
    return scope, PortalStore(scope.portal)


def add(store, client, *, email="", phone="", **changes):
    path = store.client_dir(client) / "profile.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": client, "name": "Fictional Person", "email": email, "phone": phone, **changes}), encoding="utf-8")


def test_locked_contact_snapshot_is_discarded_before_next_view(firm):
    import read_scope
    from portal.communication_consent import gate
    scope, store = firm
    add(store, "first", email="one@fictional.example")
    add(store, "second", email="two@fictional.example")
    with gate(scope), read_scope.contacts():
        first = contacts.inventory(scope)
        assert contacts.inventory(scope) is first
        assert sum(row.email == "one@fictional.example" for row in first) == 1
    add(store, "second", email="one@fictional.example")
    with gate(scope), read_scope.contacts():
        assert sum(row.email == "one@fictional.example" for row in contacts.inventory(scope)) == 2


@pytest.mark.parametrize("raw,expected", [(" A.User+Case@FICTIONAL.Example ", "a.user+case@fictional.example"),
                                         ("a.user@fictional.example", "a.user@fictional.example")])
def test_exact_email_normalization_without_alias_inference(raw, expected):
    assert contacts.normalize_email(raw) == expected
    assert contacts.normalize_email("a.user+case@fictional.example") != contacts.normalize_email("auser@fictional.example")


@pytest.mark.parametrize("raw", [None, [], "", "a", "a@host", "a..b@fictional.example", "a@host..example", "a@-host.example",
                                  "é@fictional.example", "K@fictional.example", "a@fictional.example\nother@fictional.example"])
def test_unsupported_email_is_not_guessed(raw):
    with pytest.raises(ValueError, match="unsupported_email"):
        contacts.normalize_email(raw)


@pytest.mark.parametrize("raw,expected", [("+1 (617) 555-0101", "+16175550101"), ("+55 11 5555 0101", "+551155550101"),
                                         ("+12345678", "+12345678"), ("+123456789012345", "+123456789012345")])
def test_full_phone_country_identity_supported_pilot_envelope(raw, expected):
    assert contacts.normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", [None, 16175550101, "6175550101", "16175550101", "0016175550101", "+012345678", "+1234567",
                                  "+1234567890123456", "+1 617 555 0101 ext2", "+1+6175550101", "+١٦١٧٥٥٥٠١٠١"])
def test_phone_suffix_extensions_and_country_guessing_refused(raw):
    with pytest.raises(ValueError, match="unsupported_phone"):
        contacts.normalize_phone(raw)


def test_shared_phone_preserves_two_distinct_safe_emails_and_no_other_identity_in_reason(firm):
    scope, store = firm
    add(store, "spouse-a", email="a@fictional.example", phone="+1 (617) 555-0101")
    add(store, "spouse-b", email="b@fictional.example", phone="+16175550101")
    for client in ("spouse-a", "spouse-b"):
        assert contacts.contact_eligibility(scope, store, client, "email")["eligible"]
        result = contacts.contact_eligibility(scope, store, client, "sms")
        assert result == {"eligible": False, "reason": "ambiguous_destination"}
        assert not contacts.contact_eligibility(scope, store, client, "whatsapp")["eligible"]
    assert contacts.lookup_contact(scope, "+16175550101") == {"matched": False, "reason": "ambiguous_destination"}
    assert contacts.lookup_contact(scope, " A@FICTIONAL.Example ")["client"] == "spouse-a"
    assert not (store.root / "auth.json").exists()


def test_same_last_ten_with_different_country_does_not_collide(firm):
    scope, store = firm
    add(store, "client-a", email="a@fictional.example", phone="+16175550101")
    add(store, "client-b", email="b@fictional.example", phone="+556175550101")
    assert contacts.contact_eligibility(scope, store, "client-a", "sms")["eligible"]
    assert contacts.lookup_contact(scope, "+556175550101")["client"] == "client-b"
    assert contacts.lookup_contact(scope, "6175550101")["matched"] is False


def test_cross_store_same_id_duplicate_email_is_ambiguous_even_stopped_or_restricted(firm):
    scope, store = firm
    other = prospects.store(scope.portal)
    add(store, "same-id", email="same@fictional.example", declined_on="2026-10-04")
    add(other, "same-id", email="SAME@fictional.example", track="vawa")
    assert len(contacts.inventory(scope)) == 2
    assert contacts.lookup_contact(scope, "same@fictional.example") == {"matched": False, "reason": "ambiguous_destination"}
    assert contacts.contact_eligibility(scope, store, "same-id", "email")["reason"] == "ambiguous_destination"
    pscope = Scope(scope.root, other.root, scope.data / "prospects")
    assert contacts.contact_eligibility(pscope, other, "same-id", "email")["reason"] == "ambiguous_destination"


@pytest.mark.parametrize("damage", ["json", "list", "id", "email_type", "phone_type", "missing", "oversize"])
def test_malformed_unrelated_profile_denies_complete_inventory(firm, damage):
    scope, store = firm
    add(store, "client-a", email="a@fictional.example")
    add(store, "broken", email="other@fictional.example")
    path = store.client_dir("broken") / "profile.json"
    value = json.loads(path.read_text())
    if damage == "json":
        path.write_text("{unfinished")
    elif damage == "list":
        path.write_text("[]")
    elif damage == "missing":
        path.unlink()
    elif damage == "oversize":
        path.write_bytes(b" " * (contacts.MAX_PROFILE_BYTES + 1))
    else:
        value[{"id": "id", "email_type": "email", "phone_type": "phone"}[damage]] = None
        path.write_text(json.dumps(value))
    assert contacts.contact_eligibility(scope, store, "client-a", "email")["reason"] == "contact_inventory_unavailable"
    assert contacts.lookup_contact(scope, "a@fictional.example")["matched"] is False


def test_empty_optional_store_allowed_but_missing_nonempty_inventory_refused(firm):
    scope, store = firm
    add(store, "client-a", email="a@fictional.example")
    assert contacts.contact_eligibility(scope, store, "client-a", "email")["eligible"]
    optional = scope.portal / "prospects"
    optional.mkdir()
    assert contacts.contact_eligibility(scope, store, "client-a", "email")["eligible"]
    (optional / "auth.json").write_text('{"links":{}}')
    assert contacts.contact_eligibility(scope, store, "client-a", "email")["reason"] == "contact_inventory_unavailable"


def test_local_phone_remains_office_only_and_does_not_block_distinct_email(firm):
    scope, store = firm
    add(store, "local", email="local@fictional.example", phone="(617) 555-0101")
    add(store, "international", email="int@fictional.example", phone="+16175550101")
    assert contacts.contact_eligibility(scope, store, "local", "email")["eligible"]
    assert contacts.contact_eligibility(scope, store, "local", "sms")["reason"] == "unsupported_destination"
    assert contacts.contact_eligibility(scope, store, "international", "sms")["reason"] == "phone_inventory_requires_correction"


def test_destination_group_binding_ignores_unrelated_enrollment(firm):
    scope, store = firm
    add(store, "client-a", email="a@fictional.example")
    before = contacts.contact_eligibility(scope, store, "client-a", "email")
    add(store, "unrelated", email="b@fictional.example")
    assert contacts.contact_eligibility(scope, store, "client-a", "email") == before
    add(store, "collision", email="A@FICTIONAL.EXAMPLE")
    assert not contacts.contact_eligibility(scope, store, "client-a", "email")["eligible"]


def test_full_2000_profile_inventory_late_cross_store_collision_and_visible_bound(firm, monkeypatch, record_property):
    scope, store = firm
    for n in range(2000):
        add(store, f"client-{n}", email=f"client{n}@fictional.example")
    start = perf_counter()
    assert len(contacts.inventory(scope)) == 2000
    record_property("inventory_2000_seconds", round(perf_counter() - start, 6))
    start = perf_counter()
    assert contacts.lookup_contact(scope, "client1999@fictional.example")["client"] == "client-1999"
    record_property("lookup_2000_seconds", round(perf_counter() - start, 6))
    other = prospects.store(scope.portal)
    add(other, "last", email="client1999@fictional.example")
    assert not contacts.contact_eligibility(scope, store, "client-1999", "email")["eligible"]
    monkeypatch.setattr(contacts, "MAX_CONTACTS_PER_STORE", 1999)
    assert contacts.lookup_contact(scope, "client0@fictional.example")["reason"] == "contact_inventory_limit"
    with pytest.raises(contacts.InventoryError, match="contact_inventory_limit"):
        contacts.inventory(scope)


def test_foreign_store_cannot_be_used_with_current_scope(firm, tmp_path):
    scope, store = firm
    add(store, "client-a", email="a@fictional.example")
    foreign = PortalStore(tmp_path / "foreign" / "data" / "portal")
    assert contacts.contact_eligibility(scope, foreign, "client-a", "email")["eligible"] is False


def test_profile_symlink_denies_without_reading_foreign_target(firm):
    scope, store = firm
    add(store, "client-a", email="a@fictional.example")
    original = store.client_dir("client-a") / "profile.json"
    saved = scope.data / "fictional-linked-profile.json"
    saved.write_bytes(original.read_bytes())
    original.unlink()
    try:
        original.symlink_to(saved)
    except OSError:
        pytest.skip("Synthetic symlink creation unavailable on this host.")
    assert contacts.lookup_contact(scope, "a@fictional.example")["reason"] == "contact_inventory_unavailable"


def test_unsupported_purpose_never_becomes_contact_authority(firm):
    scope, store = firm
    add(store, "client-a", email="a@fictional.example")
    assert contacts.contact_eligibility(scope, store, "client-a", "email", "delegate")["eligible"] is False
    assert contacts.contact_eligibility(scope, store, "client-a", "carrier_pigeon")["eligible"] is False
