"""Fictional protected new-target reservations; no providers or client messages."""
import json
import pytest
from portal import promotion, contact_transitions as access, communication_consent as consent
from test_contact_promotion import pair  # noqa: F401 -- pytest fixture registration and helper reexports
from test_contact_transitions import firm  # noqa: F401 -- pytest fixture registration and helper reexports
from test_communication_consent import STAFF


def body():
    return {"name": "Fictional Reserved Target", "language": "en", "filing": "i485", "conflict": {"decision": "none"}}


def create(pair):  # noqa: F811 -- pytest fixture injection
    target, source = pair
    return promotion.create_target(target[0], target[1], source[2], body(), actor_email=STAFF)


def test_normal_new_target_is_single_reserved_identity_unconsented_and_complete(pair):  # noqa: F811 -- pytest fixture injection
    target, source = pair
    result = create(pair)
    cid = result["id"]
    profile = target[1].profile(cid)
    assert profile["email"] == source[1].profile(source[2])["email"]
    assert profile["consent"] == {"email": False, "sms": False, "whatsapp": False}
    record = promotion._record(target[0], target[1], cid)
    assert record["state"] == "completed"
    assert promotion._identity(target[0], target[1], cid) == record["target"]["identity"]
    first = next(row for row in access._record(target[0], target[1], cid)["history"] if row["action"] == "enrollment_denied")
    assert first["id"] == record["new_target"]["enrollment_id"]
    assert (target[0].cases / cid / promotion.FILE).exists()
    assert not consent.eligibility(target[0], target[1], cid, "email")["allowed"]


def crash_create(pair, monkeypatch, boundary):  # noqa: F811 -- pytest fixture injection
    target, source = pair
    real_save = promotion._save
    real_atomic = promotion._atomic
    from portal.store import PortalStore
    real_write = PortalStore._write
    real_enrollment_save = access._save
    def save(ms, st, cid, rec):
        real_save(ms, st, cid, rec)
        if boundary == "source" and rec["role"] == "source" and rec["state"] == "pending":
            raise OSError("fictional source reservation crash")
    def atomic(path, value):
        if path.parent.parent == target[0].cases and path.name == promotion.FILE:
            if boundary == "mkdir":
                path.parent.mkdir(parents=True, exist_ok=True)
                raise OSError("fictional mkdir-before-marker crash")
            if boundary == "case_temp":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.with_name(path.name + "." + "a" * 16 + ".tmp").write_text(json.dumps(value))
                raise OSError("fictional case-marker temp-only crash")
            real_atomic(path, value)
            if boundary == "case_marker": raise OSError("fictional case-marker crash")
            return
        real_atomic(path, value)
    def enrollment_save(ms, st, cid, rec):
        real_enrollment_save(ms, st, cid, rec)
        if boundary == "enrollment" and (rec.get("transition") or {}).get("enrollment"):
            raise OSError("fictional target enrollment coordinator crash")
    def write(st, path, value):
        if boundary == "profile_before" and path.name == "profile.json" and path.parent.name == "fictional-reserved-target":
            raise OSError("fictional target profile-before crash")
        real_write(st, path, value)
        if boundary == "profile_after" and path.name == "profile.json" and path.parent.name == "fictional-reserved-target":
            raise OSError("fictional target profile-after crash")
    monkeypatch.setattr(promotion, "_save", save)
    monkeypatch.setattr(promotion, "_atomic", atomic)
    monkeypatch.setattr(access, "_save", enrollment_save)
    monkeypatch.setattr(PortalStore, "_write", write)
    with pytest.raises(OSError): create(pair)
    return promotion._record(*source, allow_temps=True)


@pytest.mark.parametrize("boundary", ["source", "mkdir", "case_temp", "case_marker", "enrollment", "profile_before", "profile_after"])
def test_new_target_crashes_hold_both_and_recover_exact_same_enrollment(pair, monkeypatch, boundary):  # noqa: F811 -- pytest fixture injection
    target, source = pair
    with monkeypatch.context() as fault:
        record = crash_create(pair, fault, boundary)
    cid = record["target"]["client"]
    assert not consent.eligibility(*source, "email")["allowed"]
    assert not consent.eligibility(target[0], target[1], cid, "email")["allowed"]
    with pytest.raises(ValueError): create(pair)
    result = promotion.recover(*source, record["operation"], actor_email=STAFF)
    assert result["client"] == cid and result["state"] == "completed"
    assert not target[1].client_dir(cid + "-2").exists()
    assert promotion._identity(target[0], target[1], cid) == record["target"]["identity"]
    assert not (target[0].cases / cid / (promotion.FILE + "." + "a" * 16 + ".tmp")).exists()


@pytest.mark.parametrize("fault", ["foreign_case", "foreign_temp", "target_nonce", "inactive", "source_acl", "target_acl", "snapshot", "direct_enrollment"])
def test_new_target_recovery_requires_exact_owned_case_nonce_snapshot_and_current_acl(pair, monkeypatch, fault):  # noqa: F811 -- pytest fixture injection
    target, source = pair
    boundary = "profile_after" if fault == "target_nonce" else "source"
    with monkeypatch.context() as crash:
        record = crash_create(pair, crash, boundary)
    cid = record["target"]["client"]
    case = target[0].cases / cid
    if fault == "foreign_case":
        case.mkdir(); (case / "foreign.txt").write_text("fictional unrelated case content")
    elif fault == "foreign_temp":
        case.mkdir(); (case / (promotion.FILE + "." + "a" * 16 + ".tmp")).write_text("{")
    elif fault == "target_nonce":
        own = access._record(target[0], target[1], cid)
        next(row for row in own["history"] if row["action"] == "enrollment_denied")["id"] = "f" * 32
        access._save(target[0], target[1], cid, own)
    elif fault == "inactive":
        users = target[0].data / "review_users.json"
        value = json.loads(users.read_text()); value["users"][STAFF]["active"] = False
        users.write_text(json.dumps(value))
    elif fault.endswith("acl"):
        import restricted
        denied = source[0].cases / source[2] if fault == "source_acl" else case
        monkeypatch.setattr(restricted, "visible_to", lambda actor, folder: folder != denied)
    elif fault == "snapshot":
        path = source[1].client_dir(source[2]) / promotion.FILE
        value = json.loads(path.read_text()); value["new_target"]["profile_snapshot"]["name"] = "Changed reservation"
        path.write_text(json.dumps(value))
    else:
        case.mkdir()
        with pytest.raises(ValueError):
            target[1].add_client(cid, "Forged ordinary enrollment", language="en")
        return
    before = (source[1].client_dir(source[2]) / promotion.FILE).read_bytes()
    with pytest.raises((ValueError, PermissionError)):
        promotion.recover(*source, record["operation"], actor_email=STAFF)
    assert (source[1].client_dir(source[2]) / promotion.FILE).read_bytes() == before


def test_source_reservation_q1_refuses_reserved_target_before_folder_or_profile(pair, monkeypatch):  # noqa: F811 -- pytest fixture injection
    import purge
    target, source = pair
    with monkeypatch.context() as crash:
        record = crash_create(pair, crash, "source")
    cid = record["target"]["client"]
    with pytest.raises(ValueError, match="pending promotion"):
        purge.empty_stores(target[0].cases, cid, target[0].portal)
    assert (source[1].client_dir(source[2]) / promotion.FILE).exists()


def test_private_nonce_seam_rejects_a_caller_dict_without_canonical_reservation(pair):  # noqa: F811 -- pytest fixture injection
    target, source = pair
    (target[0].cases / "fake-target").mkdir()
    with pytest.raises((ValueError, KeyError)):
        access.prepare_enrollment(target[0], target[1], "fake-target", {}, _promotion={"source": {"kind": "prospect", "client": source[2]}})


def test_acl_tightened_after_restriction_carry_is_not_overwritten_and_denied_bytes_stay(pair, monkeypatch):  # noqa: F811 -- pytest fixture injection
    import restricted
    target, source = pair
    folder = source[0].cases / source[2]
    restricted.mark(folder, True, "Fictional protected source", "Fictional Attorney", "attorney")
    restricted.name_person(folder, STAFF, True, "Fictional Attorney", "attorney", "Fictional Staff")
    real = restricted.carry_over
    def crash(*args, **kwargs):
        _result = real(*args, **kwargs)
        raise OSError("fictional post-restriction pre-conflict crash")
    with monkeypatch.context() as fault:
        fault.setattr(restricted, "carry_over", crash)
        with pytest.raises(OSError): create(pair)
    record = promotion._record(*source)
    cid = record["target"]["client"]
    case = target[0].cases / cid
    restricted.name_person(case, STAFF, False, "Fictional Attorney", "attorney")
    original = {p.name: p.read_bytes() for p in case.iterdir() if p.is_file()}
    with pytest.raises(PermissionError):
        promotion.recover(*source, record["operation"], actor_email=STAFF)
    assert {p.name: p.read_bytes() for p in case.iterdir() if p.is_file()} == original
    assert not (case / "conflict_check.json").exists()


def test_existing_marker_with_foreign_unenrolled_content_refuses_without_rewrite(pair, monkeypatch):  # noqa: F811 -- pytest fixture injection
    target, source = pair
    with monkeypatch.context() as fault:
        record = crash_create(pair, fault, "case_marker")
    case = target[0].cases / record["target"]["client"]
    (case / "foreign.txt").write_text("fictional unrelated content")
    original = {p.name: p.read_bytes() for p in case.iterdir() if p.is_file()}
    with pytest.raises(ValueError, match="unowned case content"):
        promotion.recover(*source, record["operation"], actor_email=STAFF)
    assert {p.name: p.read_bytes() for p in case.iterdir() if p.is_file()} == original


def test_final_case_marker_failure_holds_both_then_recovers_completed_truthful_receipt(pair, monkeypatch):  # noqa: F811 -- pytest fixture injection
    target, source = pair
    real = promotion._atomic
    def crash(path, value):
        if path.parent.parent == target[0].cases and path.name == promotion.FILE and value["state"] == "completed":
            raise OSError("fictional final case receipt crash")
        real(path, value)
    with monkeypatch.context() as fault:
        fault.setattr(promotion, "_atomic", crash)
        with pytest.raises(OSError): create(pair)
    record = promotion._record(*source)
    cid = record["target"]["client"]
    assert not consent.eligibility(*source, "email")["allowed"]
    assert not consent.eligibility(target[0], target[1], cid, "email")["allowed"]
    assert promotion.recover(*source, record["operation"], actor_email=STAFF)["state"] == "completed"
    own = promotion._record(target[0], target[1], cid, path=target[0].cases / cid / promotion.FILE, allow_temps=True)
    assert own["state"] == "completed" and own["history"][-1]["action"] == "completed"


def test_two_simultaneous_new_target_requests_have_one_reservation_and_one_target(pair):  # noqa: F811 -- pytest fixture injection
    import concurrent.futures
    import threading
    barrier = threading.Barrier(2)
    def worker():
        barrier.wait(timeout=5)
        try: return create(pair)
        except (ValueError, PermissionError): return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), [0, 1]))
    assert sum(row is not None for row in results) == 1
    target, source = pair
    assert not target[1].client_dir("fictional-reserved-target-2").exists()
    assert promotion._record(*source)["state"] == "completed"


def test_new_target_case_marker_is_cataloged_firm_exported_and_q2_excluded(pair):  # noqa: F811 -- pytest fixture injection
    import argparse
    import client_file
    import records
    import export_firm
    target, _ = pair
    cid = create(pair)["id"]
    case = target[0].cases / cid
    assert promotion.FILE in records.patterns("case")
    entries, *_ = export_firm.gather(argparse.Namespace(all=False, client=cid, data=target[0].cases, portal=target[0].portal, firm_files=False))
    assert any(item.source == case / promotion.FILE for item in entries)
    chosen, excluded, _ = client_file.gather(case, target[0].portal)
    assert not any(item.source == case / promotion.FILE for item in chosen)
    assert any(row["path"] == f"clients/{cid}/{promotion.FILE}" for row in excluded)


def test_new_target_cannot_bypass_third_party_cross_store_email_collision(pair):  # noqa: F811 -- pytest fixture injection
    from test_contact_transitions import second
    target, source = pair
    other = second(target)
    other[1].update_profile(other[2], email=source[1].profile(source[2])["email"])
    with pytest.raises(ValueError, match="ambiguous"):
        create(pair)
    assert not (source[1].client_dir(source[2]) / promotion.FILE).exists()
    assert not (target[0].cases / "fictional-reserved-target").exists()


def test_protected_recovery_view_binds_exact_plan_and_current_both_case_acl(pair, monkeypatch):  # noqa: F811 -- pytest fixture injection
    target, source = pair
    with monkeypatch.context() as fault:
        record = crash_create(pair, fault, "case_marker")
    view = promotion.recovery_view(*source, actor_email=STAFF)
    assert view == {"state": "pending", "operation": record["operation"], "source": source[2],
                    "client": record["target"]["client"], "new_target": True, "can_recover": True, "reason": "complete_exact_promotion_recovery"}
    import restricted
    denied = target[0].cases / record["target"]["client"]
    monkeypatch.setattr(restricted, "visible_to", lambda actor, folder: folder != denied)
    with pytest.raises(PermissionError):
        promotion.recovery_view(*source, actor_email=STAFF)


def test_completed_restriction_phase_cannot_recreate_removed_target_acl(pair, monkeypatch):  # noqa: F811 -- pytest fixture injection
    import restricted
    target, source = pair
    folder = source[0].cases / source[2]
    restricted.mark(folder, True, "Fictional source restriction", "Fictional Attorney", "attorney")
    restricted.name_person(folder, STAFF, True, "Fictional Attorney", "attorney", "Fictional Staff")
    with monkeypatch.context() as fault:
        record = crash_create(pair, fault, "enrollment")
    case = target[0].cases / record["target"]["client"]
    (case / "access.json").unlink()  # Fictional externally missing current ACL must hold, not recreate.
    marker = (case / promotion.FILE).read_bytes()
    with pytest.raises(ValueError, match="restriction was removed"):
        promotion.recover(*source, record["operation"], actor_email=STAFF)
    assert not (case / "access.json").exists()
    assert (case / promotion.FILE).read_bytes() == marker


def test_staff_wrapper_requires_current_actor_and_returns_completed_from_call_once(pair):  # noqa: F811 -- pytest fixture injection
    from review.front_desk import add_client
    target, source = pair
    with pytest.raises(PermissionError):
        add_client(target[1], target[0].cases, body(), "Forged Attorney", "attorney", carry_from=source[0].cases / source[2])
    result = add_client(target[1], target[0].cases, body(), "Forged Attorney", "attorney", carry_from=source[0].cases / source[2], actor_email=STAFF)
    assert set(result["from_call"]) == {"answers", "notes", "tasks", "apply_for", "restricted"}
    assert all(isinstance(result["from_call"][key], int) for key in ("answers", "notes", "tasks", "apply_for"))
    assert result["promotion"]["state"] == "completed"
    assert result["promotion"]["client"] == result["id"]
    assert target[1].profile(result["id"])["added_by"] == "Fictional Staff"


def test_source_messages_on_does_not_enable_new_protected_track_defaults(pair):  # noqa: F811 -- pytest fixture injection
    import restricted
    target, source = pair
    folder = source[0].cases / source[2]
    restricted.mark(folder, True, "Fictional source confidentiality", "Fictional Attorney", "attorney")
    restricted.name_person(folder, STAFF, True, "Fictional Attorney", "attorney", "Fictional Staff")
    restricted.set_messages(folder, True, "Fictional source-only reviewed policy", "Fictional Attorney", "attorney")
    details = body() | {"track": "vawa"}
    result = promotion.create_target(target[0], target[1], source[2], details, actor_email=STAFF)
    destination = target[0].cases / result["id"]
    assert restricted.is_restricted(destination)
    assert not restricted.messages_allowed(destination)
    assert restricted.messages_allowed(folder)
