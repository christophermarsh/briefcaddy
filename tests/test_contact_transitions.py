"""Fictional existing profiles; accepted providers are callbacks, never network."""
import json

import pytest

import purge
from portal import communication_consent as consent, contact_transitions as access
from test_communication_consent import firm as consent_firm, granted, STAFF, entry


@pytest.fixture
def firm(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_PROSPECTS", str(tmp_path / "fictional-consent-firm/data/prospects"))
    return consent_firm.__wrapped__(tmp_path, monkeypatch)


def second(firm, *, prospect=False, same_id=False):
    scope, store, _ = firm
    if prospect:
        scope, store = access._composition(scope, "prospect")
    client = "client-a" if same_id else "client-b"
    (scope.cases / client).mkdir(parents=True, exist_ok=True)
    store.add_client(client, "Fictional B", email="b@fictional.example", phone="+16175550102", language="en")
    return scope, store, client


def accepted(firm):
    granted(firm)
    scope, store, client = firm
    captured = []
    result = consent.dispatch(scope, store, client, "email", lambda _, token: captured.append(token) or {"status": "sent"})
    assert result["credential_active"]
    return entry(store, captured[0])


def leave_pending(firm, other, monkeypatch, boundary):
    scope, store, client = other
    if boundary == "coordinator":
        real = access._save
        def stop(ms, st, cid, record):
            real(ms, st, cid, record)
            if record.get("transition"):
                raise OSError("fictional first-write crash")
        monkeypatch.setattr(access, "_save", stop)
    elif boundary == "denied":
        monkeypatch.setattr(store, "_write", lambda *_: (_ for _ in ()).throw(OSError("fictional pre-profile crash")))
    elif boundary == "profile":
        monkeypatch.setattr(store, "_finish_contact_change", lambda *_: (_ for _ in ()).throw(OSError("fictional post-profile crash")))
    elif boundary == "auth":
        real = access.finish_transition
        def stop(ms, st, cid, identity):
            from portal.store import PortalStore
            old = PortalStore.end_sessions
            PortalStore.end_sessions = lambda *_: (_ for _ in ()).throw(OSError("fictional auth cleanup crash"))
            try:
                return real(ms, st, cid, identity)
            finally:
                PortalStore.end_sessions = old
        monkeypatch.setattr(access, "finish_transition", stop)
    elif boundary == "member_ready":
        real = access._save
        def stop(ms, st, cid, record):
            real(ms, st, cid, record)
            if record["state"] == "ready" and record.get("pending") and not record.get("transition"):
                raise OSError("fictional member-ready crash")
        monkeypatch.setattr(access, "_save", stop)
    elif boundary == "completed":
        real = access._save
        def stop(ms, st, cid, record):
            real(ms, st, cid, record)
            if (record.get("transition") or {}).get("state") == "completed":
                raise OSError("fictional coordinator-ready crash")
        monkeypatch.setattr(access, "_save", stop)
    with pytest.raises(OSError):
        store.update_profile(client, email="a@fictional.example")


@pytest.mark.parametrize("boundary", ["coordinator", "denied", "profile", "auth", "member_ready", "completed"])
def test_crash_fences_both_previously_accepted_identities_and_authorized_exact_recovery(firm, monkeypatch, boundary):
    other = second(firm)
    old = [accepted(firm), accepted(other)]
    with monkeypatch.context() as fault:
        leave_pending(firm, other, fault, boundary)
    for current, proof in zip((firm, other), old):
        assert not consent.credential_valid(current[0], current[1], proof)
        assert not consent.eligibility(*current, "email")["allowed"]
    # The first-write failure specifically precedes the other member fence.
    if boundary == "coordinator":
        unchanged = access._record(*firm)
        assert unchanged["state"] == "ready" and not unchanged.get("pending")
        assert unchanged["revision"] == 1  # accepted enrollment, before new member denial
    result = access.recover_transition(*other, {"email": "a@fictional.example"}, actor_email=STAFF)
    assert result["state"] == "ready"
    for current, proof in zip((firm, other), old):
        record = access._record(*current)
        assert record["state"] == "ready" and not record.get("pending") and not record.get("transition")
        assert not consent.credential_valid(current[0], current[1], proof)
        assert all("owner_client" not in row and "affected" not in row for row in record["history"])


def test_partial_completed_cleanup_blocks_new_transition_and_preserves_its_fence(firm, monkeypatch):
    other = second(firm)
    real = access._save
    def interrupted(ms, st, cid, record):
        real(ms, st, cid, record)
        if cid == firm[2] and record["revision"] and record["state"] == "ready" and not record.get("pending"):
            raise OSError("fictional member-cleanup crash")
    with monkeypatch.context() as fault:
        fault.setattr(access, "_save", interrupted)
        with pytest.raises(OSError):
            other[1].update_profile(other[2], email="a@fictional.example")
    assert not access._record(*firm).get("pending")
    with pytest.raises(ValueError, match="coordinator cleanup"):
        firm[1].update_profile(firm[2], email="new@fictional.example")
    # Even externally damaged/newer association cannot be erased by old cleanup.
    newer = access._record(*firm)
    newer["revision"] += 1
    newer["history"].append({"revision": newer["revision"], "action": "fictional_newer_denial"})
    real(*firm, newer)
    retained = (firm[1].client_dir(firm[2]) / access.FILE).read_bytes()
    with pytest.raises(ValueError, match="member revision"):
        access.recover_transition(*other, {"email": "a@fictional.example"}, actor_email=STAFF)
    assert (firm[1].client_dir(firm[2]) / access.FILE).read_bytes() == retained


@pytest.mark.parametrize("prospect,same_id", [(False, False), (True, False), (True, True)])
def test_collision_then_removal_never_restores_previous_email_grants(firm, prospect, same_id):
    other = second(firm, prospect=prospect, same_id=same_id)
    old = [accepted(firm), accepted(other)]
    other[1].update_profile(other[2], email="a@fictional.example")
    for current in (firm, other):
        assert consent._record(*current)["channels"]["email"]["state"] == "revoked"
    other[1].update_profile(other[2], email="b@fictional.example")
    for current, proof in zip((firm, other), old):
        assert not consent.credential_valid(current[0], current[1], proof)
        assert not consent.eligibility(*current, "email")["allowed"]
        granted(current)
        assert consent.eligibility(*current, "email")["allowed"]


def test_shared_phone_revokes_only_phone_grants_and_keeps_distinct_email_history(firm):
    other = second(firm)
    for current in (firm, other):
        granted(current, "sms")
        granted(current, "whatsapp")
        granted(current)
    before = [consent._record(*current)["channels"]["email"] for current in (firm, other)]
    other[1].update_profile(other[2], phone="+1 (617) 555-0101")
    for current, email in zip((firm, other), before):
        record = consent._record(*current)
        assert record["channels"]["email"] == email
        assert all(record["channels"][ch]["state"] == "revoked" for ch in ("sms", "whatsapp"))
        assert consent.eligibility(*current, "email")["allowed"]
        revokes = [row for row in record["history"] if row.get("action") == "revoke"]
        assert revokes[-1]["source"] == "contact_transition"
        assert revokes[-1]["channels"] == ["sms", "whatsapp"]


def test_unrelated_timestamps_and_equivalent_contact_format_do_not_churn_revision(firm):
    proof = accepted(firm)
    scope, store, client = firm
    before = consent._record(*firm)
    revision = access.current_revision(*firm)
    store.update_profile(client, last_invited="fictional", email=" A@FICTIONAL.Example ", phone="+1 (617) 555-0101")
    assert access.current_revision(*firm) == revision
    assert consent._record(*firm) == before
    assert consent.credential_valid(scope, store, proof)


def test_lost_original_request_recovers_exact_private_snapshot_with_fresh_authority(firm, monkeypatch):
    other = second(firm)
    with monkeypatch.context() as fault:
        leave_pending(firm, other, fault, "coordinator")
    view = access.recovery_view(*other, actor_email=STAFF)
    assert view["proposed_contacts"] == {"email": "a@fictional.example", "phone": "+16175550102"}
    assert set(view) == {"state", "transition", "revision", "proposed_contacts", "reason"}
    assert access.recover_transition(*other, actor_email=STAFF)["state"] == "ready"
    assert other[1].profile(other[2])["email"] == "a@fictional.example"


@pytest.mark.parametrize("change", ["wrong_payload", "inactive", "acl", "damaged"])
def test_recovery_requires_current_authority_exact_payload_and_intact_plan(firm, monkeypatch, change):
    other = second(firm)
    with monkeypatch.context() as fault:
        leave_pending(firm, other, fault, "coordinator")
    scope, store, client = other
    contacts = {"email": "a@fictional.example"}
    error = (ValueError, PermissionError)
    if change == "wrong_payload":
        contacts["email"] = "not-the-plan@fictional.example"
    elif change == "inactive":
        path = scope.data / "review_users.json"
        value = json.loads(path.read_text()); value["users"][STAFF]["active"] = False
        path.write_text(json.dumps(value))
    elif change == "acl":
        import restricted
        monkeypatch.setattr(restricted, "visible_to", lambda *_: False)
    else:
        (store.client_dir(client) / access.FILE).write_text("{")
    with pytest.raises(error):
        access.recover_transition(*other, contacts, actor_email=STAFF)
    assert not consent.eligibility(*firm, "email")["allowed"]


@pytest.mark.parametrize("owner_selected,method", [(False, "empty"), (True, "empty"), (False, "run"), (True, "run")])
def test_actual_purge_preflight_preserves_pending_coordinator_and_waiting_state(firm, monkeypatch, owner_selected, method):
    other = second(firm)
    with monkeypatch.context() as fault:
        leave_pending(firm, other, fault, "coordinator")
    selected = other if owner_selected else firm
    scope, store, client = selected
    path = scope.data / purge.PURGES_FILE
    path.write_text(json.dumps({"version": 1, "cases": {client: {"state": "waiting", "fictional": True}}}))
    snapshot = path.read_bytes()
    original = scope.cases / client / "fictional-original.pdf"
    original.write_bytes(b"fictional retained bytes")
    with pytest.raises(ValueError, match="contact recovery"):
        if method == "run":
            purge.run(scope.cases, client, scope.portal)
        else:
            purge.empty_stores(scope.cases, client, scope.portal, who=purge.Identity(client, [], set(), set(), set()))
    assert path.read_bytes() == snapshot and original.read_bytes() == b"fictional retained bytes"
    assert store.profile(client) and (other[1].client_dir(other[2]) / access.FILE).exists()


def test_malformed_coordinator_and_history_limit_refuse_without_discarding(firm):
    scope, store, client = firm
    record = access._record(*firm)
    record["transition"] = {"id": "a" * 32}
    access._save(*firm, record)
    assert not consent.eligibility(*firm, "email")["allowed"]
    path = store.client_dir(client) / access.FILE
    previous = path.read_bytes()
    with pytest.raises(ValueError, match="nothing was discarded"):
        access._save(*firm, dict(record, history=[{"fictional": "a" * access.MAX_BYTES}]))
    assert path.read_bytes() == previous


def test_unrelated_enrollment_keeps_current_accepted_email_binding(firm):
    proof = accepted(firm)
    second(firm)
    assert consent.credential_valid(firm[0], firm[1], proof)


def test_internal_revoke_source_enum_and_default_stop_attribution(firm):
    granted(firm, "sms")
    with pytest.raises(ValueError, match="operation source"):
        consent._revoke(*firm, ["sms"], operation_id="fictional", operation_source="public_claim")
    consent._revoke(*firm, ["sms"], operation_id="fictional")
    before = consent._record(*firm)
    assert before["history"][-1]["source"] == "authenticated_provider_stop"
    consent._revoke(*firm, ["sms"], operation_id="fictional")
    assert consent._record(*firm)["history"] == before["history"]
    with pytest.raises(ValueError, match="identity was reused"):
        consent._revoke(*firm, ["sms"], operation_id="fictional", operation_source="contact_transition")


def test_q2_registered_contact_record_and_exact_temp_stay_internal_original_retained(firm, monkeypatch):
    import sys
    from pathlib import Path
    import client_file
    import documents
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import export_firm
    scope, store, client = firm
    case = scope.cases / client
    source = case / "source"
    source.mkdir()
    original = source / "fictional.pdf"
    original.write_bytes(b"%PDF-fictional original")
    names = [access.FILE, access.FILE + "." + "a" * 16 + ".tmp"]
    for name in names:
        (case / name).write_text("fictional internal access data")
    (case / "meta.json").write_text(json.dumps({"client_id": client, "source_folder": str(source)}))
    documents.save(case, {"documents": [{"files": names + [original.name]}]})
    real = export_firm.gather
    def injected(args):
        entries, skipped, warnings, roots = real(args)
        entries += [export_firm.Entry(f"clients/{client}/{name}", "Malformed registered alias", source=case / name) for name in names]
        return entries, skipped, warnings, roots
    monkeypatch.setattr(export_firm, "gather", injected)
    selected, excluded, _ = client_file.gather(case, scope.portal)
    assert any(item.source == original for item in selected)
    assert not any(item.source in {case / name for name in names} for item in selected)
    assert len([row for row in excluded if "Internal case, authority or security record" in row["why"]]) >= 2
