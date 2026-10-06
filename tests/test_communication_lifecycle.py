"""Scoped Q1/Q2/export on fictional consent/STOP state, no provider network."""
import json
from pathlib import Path
import sys

import pytest

import jobs
import purge
from portal import communication_consent as consent, communication_lifecycle as lifecycle, opt_out
from test_communication_consent import firm, granted, artifact, STAFF  # noqa: F401 -- pytest fixture registration and helper reexports
from test_communication_stop import config, signed  # noqa: F401 -- pytest fixture registration and helper reexports


def second(firm, *, prospect=False, same_id=False):  # noqa: F811 -- pytest fixture injection
    import prospects
    scope, store, client = firm
    if prospect:
        scope = consent.Scope(scope.root, scope.data / "portal/prospects", scope.data / "prospects")
        store = prospects.store(firm[0].portal)
    other = client if same_id else "client-b"
    (scope.cases / other).mkdir(parents=True, exist_ok=True)
    store.add_client(other, "Fictional Other", email="other@fictional.example", phone="+16175550101", language="en")
    return scope, store, other


def pending(firm, config, *, associate=False):  # noqa: F811 -- pytest fixture injection
    scope, _, _ = firm
    _, body, signature = signed(config)
    result = opt_out.ingest(scope, body, signature, env=config, apply_now=False)
    key = result["receipt"]
    if associate:
        receipt = opt_out._receipt(scope, key)
        receipt["members"] = [{"store": current.kind, "client": client, "state": "pending"}
                              for current, _, client, profile in opt_out._inventory(scope)
                              if opt_out.phone_hash(profile.get("phone")) == receipt["destination_sha256"]]
        consent._atomic(opt_out.folder(scope) / "receipts" / (key + ".json"), receipt)
    return key, body, signature


def purge_one(firm):  # noqa: F811 -- pytest fixture injection
    scope, _, client = firm
    # Explicit existing selected identity, not an inferred contact match.
    who = purge.Identity(client, [], set(), set(), set())
    with jobs.case_lock(scope.data / "jobs", client):
        return purge.empty_stores(scope.cases, client, scope.portal, who=who)


@pytest.mark.parametrize("prospect,same_id", [(False, False), (True, False), (True, True)])
def test_purge_preserves_other_store_case_and_pending_retry_then_retires_last(firm, config, prospect, same_id):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    other = second(firm, prospect=prospect, same_id=same_id)
    granted(firm, "sms")
    granted(other, "sms")
    key, body, signature = pending(firm, config, associate=True)
    report = purge_one(firm)
    assert not any("Communication STOP" in item for item in report["left"])
    assert not store.client_dir(client).exists() and not (scope.cases / client).exists()
    receipt = opt_out._receipt(scope, key)
    assert receipt["members"] == [{"store": other[0].kind, "client": other[2], "state": "pending"}]
    assert opt_out.pending_for(other[0], other[1].profile(other[2]))
    context = jobs.Context(scope.cases, scope.portal, jobs_root=scope.data / "jobs", use_policies=False)
    assert opt_out.pump(context)["processed"] == 1
    assert consent._record(*other)["channels"]["sms"]["state"] == "revoked"
    assert not store.client_dir(client).exists()
    purge_one(other)
    retired = opt_out._receipt(scope, key)
    assert set(retired) == {"version", "id", "state", "installation", "retired_at"} and retired["state"] == "retired"
    assert opt_out.ingest(scope, body, signature, env=config)["state"] == "retired"
    assert opt_out.process(scope, key)["state"] == "retired"
    assert not other[1].client_dir(other[2]).exists() and not store.client_dir(client).exists()


def test_unassociated_pending_receipt_preserves_current_survivor_and_last_retirement(firm, config):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    other = second(firm)
    key, _, _ = pending(firm, config)
    assert opt_out._receipt(scope, key)["members"] == []
    purge_one(firm)
    assert opt_out._receipt(scope, key)["members"] == [{"store": "client", "client": other[2], "state": "pending"}]
    opt_out.process(scope, key)
    assert not store.client_dir(client).exists() and consent._record(*other)["channels"]["sms"]["state"] == "revoked"


def test_late_ingest_after_scoped_hook_before_profile_removal_never_resurrects(firm, config, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm, "sms")
    # A preexisting STOP folder enables the Q1 hook; later verified ingest does
    # not take the case/gate and may publish after that hook releases ingest.
    opt_out.folder(scope).mkdir(exist_ok=True)
    _, body, signature = signed(config)
    real = purge._empty_stores
    key = []
    def late(*args, **kwargs):
        key.append(opt_out.ingest(scope, body, signature, env=config, apply_now=False)["receipt"])
        return real(*args, **kwargs)
    monkeypatch.setattr(purge, "_empty_stores", late)
    purge_one(firm)
    assert opt_out.process(scope, key[0])["state"] == "retired"
    assert not store.client_dir(client).exists() and not (scope.cases / client).exists()
    assert opt_out._index(scope)["pending"] == []


def test_hook_then_profile_removal_fault_retains_real_channel_denial_and_invalidates_old_credential(firm, config, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope, store, client = firm
    granted(firm, "sms")
    granted(firm, "email")
    tokens = []
    consent.dispatch(scope, store, client, "email", lambda _, token: tokens.append(token) or {"status": "sent"})
    session = store.redeem_link(tokens[0])
    assert store.session_client(session) == client
    key, _, _ = pending(firm, config)  # No case association yet, only durable STOP denial.
    _real = purge._empty_stores
    def fail(*args, **kwargs):
        raise OSError("fictional crash after STOP hook before profile removal")
    with monkeypatch.context() as fault:
        fault.setattr(purge, "_empty_stores", fail)
        with pytest.raises(OSError):
            purge_one(firm)
    assert store.client_dir(client).exists() and (scope.cases / client).exists()
    record = consent._record(scope, store, client)
    assert record["channels"]["sms"]["state"] == "revoked"
    assert record["channels"]["email"]["state"] == "granted"  # unrelated choice retained
    assert store.session_client(session) is None
    assert opt_out._receipt(scope, key)["state"] == "retired"
    assert len([row for row in record["history"] if row.get("operation_id") == key]) == 1
    purge_one(firm)  # restart canonical cleanup, not credential reconstruction
    assert not store.client_dir(client).exists()


@pytest.mark.parametrize("damage", ["profile", "receipt"])
def test_damaged_inventory_remains_unresolved_not_last_destination(firm, config, damage):  # noqa: F811 -- pytest fixture injection
    scope, _, _ = firm
    other = second(firm)
    key, _, _ = pending(firm, config, associate=True)
    path = other[1].client_dir(other[2]) / "profile.json" if damage == "profile" else opt_out.folder(scope) / "receipts" / (key + ".json")
    path.write_text("{fictional damaged inventory")
    with pytest.raises(ValueError):
        lifecycle.purge_members(scope, {("client", firm[2])})
    assert opt_out._index(scope)["pending"] == [key]
    assert other[1].client_dir(other[2]).exists()
    if damage == "profile":
        assert opt_out._receipt(scope, key)["state"] == "pending"


def test_exact_owned_temps_cleanup_with_survivor_proof_keeps_damaged_and_lock(firm, config):  # noqa: F811 -- pytest fixture injection
    scope, _, client = firm
    other = second(firm)
    key, _, _ = pending(firm, config, associate=True)
    root = opt_out.folder(scope)
    receipt = opt_out._receipt(scope, key)
    temporary = root / "receipts" / (key + ".json." + "a" * 16 + ".tmp")
    temporary.write_text(json.dumps(receipt))
    damaged = root / "receipts" / (key + ".json." + "b" * 16 + ".tmp")
    damaged.write_text("{fictional damaged residue")
    queue_tmp = root / ("queue.json." + "c" * 16 + ".tmp")
    queue_tmp.write_text(json.dumps(opt_out._index(scope)))
    report = lifecycle.purge_members(scope, {("client", client)})
    assert not temporary.exists() and not queue_tmp.exists() and damaged.exists()
    assert report["left"] and (root / ".ingest.lock").exists()
    assert opt_out._receipt(scope, key)["members"] == [{"store": "client", "client": other[2], "state": "pending"}]


def test_selected_orphan_receipt_temp_is_preserved_and_reported_unresolved(firm, config):  # noqa: F811 -- pytest fixture injection
    scope, _, client = firm
    key, _, _ = pending(firm, config, associate=True)
    root = opt_out.folder(scope)
    receipt = opt_out._receipt(scope, key)
    temporary = root / "receipts" / (key + ".json." + "d" * 16 + ".tmp")
    temporary.write_text(json.dumps(receipt))
    (root / "receipts" / (key + ".json")).unlink()
    report = purge_one(firm)
    assert temporary.exists()
    assert any("interrupted STOP write" in item and "unresolved" in item for item in report["left"])
    assert (root / ".ingest.lock").exists()


def test_firm_export_retains_main_prospect_and_firm_evidence_excludes_attempts(firm):  # noqa: F811 -- pytest fixture injection
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import export_firm
    scope, store, client = firm
    other = second(firm, prospect=True, same_id=True)
    granted(firm)
    granted(other)
    consent.dispatch(scope, store, client, "email", lambda *_: {"status": "sent"})
    w = export_firm.default_where(scope.cases, scope.portal, scope.data / "review_users.json")
    entries, _, _ = export_firm.everything_entries(w, Path(__file__).resolve().parents[1] / "docs/data_dictionary.md")
    paths = {entry.arcname for entry in entries}
    assert "portal/" + client + "/communication_consent.json" in paths
    assert "firm/prospects/" + client + "/portal/communication_consent.json" in paths
    assert any("wording-evidence/" in path for path in paths)
    assert any("consent-evidence/" in path for path in paths)
    assert not any("communication-attempts/" in path or "communication-stop/" in path for path in paths)


def test_q2_internal_registered_approval_alias_cannot_leave_as_original(firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    import client_file
    import documents
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import export_firm
    scope, store, client = firm
    granted(firm)
    case = scope.cases / client
    own = case / "source"
    own.mkdir()
    original = own / "fictional.pdf"
    original.write_bytes(b"%PDF-fictional retained original")
    names = ["communication_consent.json", "requests.json", "consent-evidence/" + "a" * 64 + ".json", "language-evidence/" + "b" * 64 + ".pdf", "communication-attempts/" + "c" * 32 + ".json"]
    for name in names:
        path = case / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fictional internal review data")
    (case / "meta.json").write_text(json.dumps({"client_id": client, "source_folder": str(own)}))
    documents.save(case, {"documents": [{"files": names + ["fictional.pdf"]}]})
    real = export_firm.gather
    def injected(args):
        entries, skipped, warnings, roots = real(args)
        entries += [export_firm.Entry(f"clients/{client}/{name}", "Malformed registered internal alias", source=case / name) for name in names]
        return entries, skipped, warnings, roots
    monkeypatch.setattr(export_firm, "gather", injected)
    selected, excluded, _ = client_file.gather(case, scope.portal)
    assert not any(entry.source in {case / name for name in names} for entry in selected)
    assert sum("Internal case, authority or security record" in row["why"] for row in excluded) >= len(names)
    assert any(entry.source == original for entry in selected)
