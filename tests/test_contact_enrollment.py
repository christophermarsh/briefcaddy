"""Fictional trusted enrollment and explicit same-ID recovery, no providers."""
import json

import pytest

import purge
from portal import communication_consent as consent, contact_access as contacts, contact_transitions as access
from test_contact_transitions import firm, accepted  # noqa: F401 -- pytest fixture registration and helper reexports
from test_communication_consent import STAFF


def new_identity(firm, prospect):  # noqa: F811 -- pytest fixture injection
    scope, store, _ = firm
    if prospect:
        scope, store = access._composition(scope, "prospect")
    client = "prospect-fictional-new" if prospect else "fictional-new"
    (scope.cases / client).mkdir(parents=True)
    return scope, store, client


def add(current):
    _, store, client = current
    return store.add_client(client, "Fictional New", email="a@fictional.example", phone="+16175550103", language="en", by="Imported Display Name")


@pytest.mark.parametrize("prospect", [False, True])
@pytest.mark.parametrize("boundary", ["coordinator", "profile", "published", "completed"])
def test_interrupted_enrollment_requires_same_id_and_fresh_recovery(firm, monkeypatch, prospect, boundary):  # noqa: F811 -- pytest fixture injection
    old = accepted(firm)
    current = new_identity(firm, prospect)
    scope, store, client = current
    with monkeypatch.context() as fault:
        if boundary in {"coordinator", "completed"}:
            real = access._save
            def crash(ms, st, cid, record):
                real(ms, st, cid, record)
                plan = record.get("transition") or {}
                if plan.get("enrollment") and (boundary == "coordinator" or plan.get("state") == "completed"):
                    raise OSError("fictional coordinator fault")
            fault.setattr(access, "_save", crash)
        elif boundary == "profile":
            fault.setattr(store, "_write", lambda *_: (_ for _ in ()).throw(OSError("fictional profile fault")))
        else:
            fault.setattr(store, "_finish_contact_change", lambda *_: (_ for _ in ()).throw(OSError("fictional publication fault")))
        with pytest.raises(OSError):
            add(current)
    assert not consent.credential_valid(firm[0], firm[1], old)
    with pytest.raises(ValueError):
        add(current)  # ordinary retry is not authenticated recovery
    from review.front_desk import new_client_id
    import prospects
    with pytest.raises(ValueError):
        new_client_id(firm[1], firm[0].cases, "Fictional New")
    with pytest.raises(ValueError):
        prospects.new_id(firm[0].cases, "Fictional New", firm[0].portal)
    with pytest.raises(ValueError):
        prospects.new_id(firm[0].cases, "Fictional New")
    if not (store.client_dir(client) / "profile.json").exists():
        with pytest.raises(contacts.InventoryError):
            contacts.inventory(scope)  # no missing-profile exception
    with pytest.raises(PermissionError):
        access.recover_enrollment(*current, actor_email="Imported Display Name")
    result = access.recover_enrollment(*current, actor_email=STAFF)
    assert result["client"] == client and result["state"] == "ready"
    assert store.profile(client)["name"] == "Fictional New"
    assert len([identity for identity in contacts.inventory(scope) if identity.client == client and identity.store_kind == scope.kind]) == 1
    assert not (store.client_dir(client + "-2")).exists()
    assert not consent.eligibility(*current, "email")["allowed"]
    assert consent._record(*firm)["channels"]["email"]["state"] == "revoked"
    assert not access._record(*current).get("transition")


def pending(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    current = new_identity(firm, False)
    with monkeypatch.context() as fault:
        real = access._save
        def crash(*args):
            real(*args)
            if (args[-1].get("transition") or {}).get("enrollment"):
                raise OSError("fictional first-write crash")
        fault.setattr(access, "_save", crash)
        with pytest.raises(OSError):
            add(current)
    return current


@pytest.mark.parametrize("fault", ["inactive", "acl", "snapshot", "existing_profile", "missing_case"])
def test_recovery_rechecks_authority_and_exact_immutable_snapshot(firm, monkeypatch, fault):  # noqa: F811 -- pytest fixture injection
    current = pending(firm, monkeypatch)
    scope, store, client = current
    if fault == "inactive":
        path = scope.data / "review_users.json"
        users = json.loads(path.read_text()); users["users"][STAFF]["active"] = False
        path.write_text(json.dumps(users))
    elif fault == "acl":
        import restricted
        monkeypatch.setattr(restricted, "visible_to", lambda *_: False)
    elif fault == "snapshot":
        path = store.client_dir(client) / access.FILE
        record = json.loads(path.read_text()); record["transition"]["profile_snapshot"]["name"] = "Changed proposal"
        path.write_text(json.dumps(record))
    elif fault == "missing_case":
        (scope.cases / client).rmdir()
    else:
        (store.client_dir(client) / "profile.json").write_text(json.dumps({"id": client, "name": "Different stored profile"}))
    with pytest.raises((ValueError, PermissionError)):
        access.recover_enrollment(*current, actor_email=STAFF)
    assert (store.client_dir(client) / access.FILE).exists()
    assert not consent.eligibility(*firm, "email")["allowed"]


def test_missing_profile_recovery_view_is_own_current_authority_only(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    current = pending(firm, monkeypatch)
    scope, store, client = current
    assert not (store.client_dir(client) / "profile.json").exists()
    before = (store.client_dir(client) / access.FILE).read_bytes()
    view = access.enrollment_recovery_view(*current, actor_email=STAFF)
    assert set(view) == {"state", "transition", "client", "revision", "proposed_profile", "reason"}
    assert view["proposed_profile"]["id"] == client and view["proposed_profile"]["name"] == "Fictional New"
    assert (store.client_dir(client) / access.FILE).read_bytes() == before
    import restricted
    monkeypatch.setattr(restricted, "visible_to", lambda *_: False)
    with pytest.raises(PermissionError):
        access.enrollment_recovery_view(*current, actor_email=STAFF)
    assert not (store.client_dir(client) / "profile.json").exists()


@pytest.mark.parametrize("selected_new,operation", [(True, "run"), (False, "run"), (True, "empty"), (False, "empty")])
def test_actual_purge_refuses_missing_profile_coordinator_before_effects(firm, monkeypatch, selected_new, operation):  # noqa: F811 -- pytest fixture injection
    current = pending(firm, monkeypatch)
    scope, store, client = current if selected_new else firm
    state = scope.data / purge.PURGES_FILE
    state.write_text(json.dumps({"version": 1, "cases": {client: {"state": "waiting"}}}))
    before = state.read_bytes()
    with pytest.raises(ValueError):
        if operation == "run":
            purge.run(scope.cases, client, scope.portal)
        else:
            purge.empty_stores(scope.cases, client, scope.portal, who=purge.Identity(client, [], set(), set(), set()))
    assert state.read_bytes() == before
    assert (current[1].client_dir(current[2]) / access.FILE).exists()
    assert (scope.cases / client).is_dir()


@pytest.mark.parametrize("key,value", [("closed_on", "2026-10-05"), ("declined_on", "2026-10-05"), ("destroyed", True), ("status", "declined")])
@pytest.mark.parametrize("already_closed", [False, True])
def test_contact_change_on_current_or_proposed_closed_profile_refuses_before_coordinator(firm, key, value, already_closed):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    if already_closed:
        store.update_profile(client, **{key: value})  # unchanged contacts retain ordinary closure behavior
    path = store.client_dir(client) / access.FILE
    before = path.read_bytes()
    profile = store.profile(client)
    with pytest.raises(ValueError, match="Closed or declined"):
        store.update_profile(client, email="changed@fictional.example", **{key: value})
    assert path.read_bytes() == before and store.profile(client) == profile


def test_enrollment_requires_actual_owned_case_without_fabricated_meta(firm):  # noqa: F811 -- pytest fixture injection
    scope, store, _ = firm
    with pytest.raises(ValueError, match="canonical protected"):
        store.add_client("no-owned-case", "Fictional New", email="new@fictional.example", language="en")
    assert not store.client_dir("no-owned-case").exists()
    assert not (scope.cases / "no-owned-case").exists()


@pytest.mark.parametrize("prospect", [False, True])
def test_at_capacity_refuses_before_pending_directory_or_member_denial(firm, monkeypatch, prospect):  # noqa: F811 -- pytest fixture injection
    from test_contact_transitions import second
    if prospect:
        second(firm, prospect=True)
    current = new_identity(firm, prospect)
    scope, store, client = current
    before = (firm[1].client_dir(firm[2]) / access.FILE).read_bytes()
    auth = (firm[0].portal / "auth.json").read_bytes()
    monkeypatch.setattr(contacts, "MAX_CONTACTS_PER_STORE", 1)
    with pytest.raises(contacts.InventoryError, match="contact_inventory_limit"):
        add(current)
    assert not store.client_dir(client).exists()
    assert (firm[1].client_dir(firm[2]) / access.FILE).read_bytes() == before
    assert (firm[0].portal / "auth.json").read_bytes() == auth
    assert contacts.inventory(scope)


def conflict_fixture(monkeypatch):
    import conflicts
    monkeypatch.setattr(conflicts, "for_new_client", lambda *_args, **_kw: ({"fictional": True}, {"decision": "none"}))
    def record(folder, _search, decision):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / conflicts.FILE).write_text(json.dumps({"decision": decision}))
    monkeypatch.setattr(conflicts, "record_new", record)
    return record


def test_actual_frontdesk_n400_setup_is_in_recoverable_first_snapshot(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    from review import front_desk
    scope, store, _ = firm
    conflict_fixture(monkeypatch)
    with monkeypatch.context() as fault:
        fault.setattr(store, "_finish_contact_change", lambda *_: (_ for _ in ()).throw(OSError("fictional after-profile fault")))
        with pytest.raises(OSError):
            front_desk.add_client(store, scope.cases, {"name": "Fictional Citizenship", "email": "citizenship@fictional.example", "language": "en", "filing": "n400"}, "Display Staff", "paralegal")
    client = "fictional-citizenship"
    assert access.recover_enrollment(scope, store, client, actor_email=STAFF)["state"] == "ready"
    profile = store.profile(client)
    assert profile["filing"] == "n400" and profile["added_by"] == "Display Staff"
    assert not (scope.cases / client / "meta.json").exists()


def test_actual_prospect_first_contact_setup_survives_enrollment_crash(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    import prospects
    scope, _, _ = firm
    rec = prospects.create(scope.cases, {"name": "Fictional First Call", "email": "first@fictional.example", "language": "en"}, "Display Staff", "paralegal", scope.portal)
    ps, st = access._composition(scope, "prospect")
    with monkeypatch.context() as fault:
        fault.setattr(st, "_write", lambda *_: (_ for _ in ()).throw(OSError("fictional before-profile fault")))
        with pytest.raises(OSError):
            prospects.ensure_portal(st, rec, "Display Staff")
    result = access.recover_enrollment(ps, st, rec["id"], actor_email=STAFF)
    assert result["client"] == rec["id"]
    profile = st.profile(rec["id"])
    assert profile["filing"] == "first_contact" and profile["prospect"] is True
    assert prospects.read(ps.cases / rec["id"])["name"] == rec["name"]


def test_independent_frontdesk_stores_serialize_allocation_through_publication(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import conflicts
    from review import front_desk
    from portal.store import PortalStore
    scope, one, _ = firm
    two = PortalStore(scope.portal)
    record = conflict_fixture(monkeypatch)
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def paused(folder, search, decision):
        calls.append(folder.name)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        record(folder, search, decision)
    monkeypatch.setattr(conflicts, "record_new", paused)
    def create(store, email, second=False):
        if second:
            second_started.set()
        return front_desk.add_client(store, scope.cases, {"name": "Fictional Concurrent", "email": email, "language": "en"}, "Display Staff", "paralegal")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(create, one, "one@fictional.example")
        assert entered.wait(5)
        second = pool.submit(create, two, "two@fictional.example", True)
        assert second_started.wait(5)
        assert not second.done()
        release.set()
        results = [first.result(timeout=10), second.result(timeout=10)]
    assert [row["id"] for row in results] == ["fictional-concurrent", "fictional-concurrent-2"]
    assert one.profile(results[0]["id"])["email"] == "one@fictional.example"
    assert two.profile(results[1]["id"])["email"] == "two@fictional.example"
