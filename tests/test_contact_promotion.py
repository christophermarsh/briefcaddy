"""Existing fictional enrollment pairs; no network or real client approval."""
import json
import pytest
import prospects
from portal import promotion, communication_consent as consent, contact_transitions as access, contact_access as contacts
from test_contact_transitions import firm  # noqa: F401 -- pytest fixture registration and helper reexports
from test_communication_consent import STAFF, granted


@pytest.fixture
def pair(firm):  # noqa: F811 -- pytest fixture injection
    scope, target, cid = firm
    target.update_profile(cid, email="", phone="")
    rec = prospects.create(scope.cases, {"name": "Fictional Prospect", "email": "prospect@fictional.example", "language": "en"}, "Fictional Staff", "paralegal", scope.portal)
    ss, source = access._composition(scope, "prospect")
    prospects.ensure_portal(source, rec, "Fictional Staff")
    return firm, (ss, source, rec["id"])


def run(pair):
    target, source = pair
    return promotion.promote(*target, source[2], actor_email=STAFF)


def test_existing_pair_completion_preserves_originals_and_requires_new_signoff(pair):
    target, source = pair
    original = source[0].cases / source[2] / "fictional-original.pdf"
    original.write_bytes(b"%PDF-fictional unchanged evidence")
    import case_notes
    case_notes.add_note(source[0].cases / source[2], "Fictional original note", "Fictional Staff", "paralegal", prospect=True)
    result = run(pair)
    assert result["state"] == "completed" and result["carried"]["notes"] == 1
    assert original.read_bytes() == b"%PDF-fictional unchanged evidence"
    assert source[1].profile(source[2])["closed_on"]
    assert target[1].profile(target[2])["email"] == "prospect@fictional.example"
    assert target[1].profile(target[2])["prospect"] == source[2]
    assert not consent.eligibility(*target, "email")["allowed"]
    assert not consent.eligibility(*source, "email")["allowed"]
    assert {(row.store_kind, row.client) for row in contacts.inventory(target[0])} == {("client", target[2])}
    granted(target)
    assert consent.eligibility(*target, "email")["allowed"]


def pending(pair, monkeypatch, boundary):
    real_save = promotion._save
    from portal.store import PortalStore
    real_write = PortalStore._write
    real_carry = prospects._carry_into_client
    target, source = pair
    def save(ms, st, client, record):
        real_save(ms, st, client, record)
        match = ((boundary == "source_first" and record["role"] == "source" and record["state"] == "pending")
                 or (boundary == "target_marker" and record["role"] == "target" and record["state"] == "pending")
                 or (boundary == "source_completed" and record["role"] == "source" and record["state"] == "completed"))
        if match:
            raise OSError("fictional promotion publication crash")
    def write(st, path, value):
        target_contact = (path == target[1].client_dir(target[2]) / "profile.json" and value.get("email") == "prospect@fictional.example")
        source_closure = (path == source[1].client_dir(source[2]) / "profile.json" and value.get("closed_on"))
        if boundary == "contact_before" and target_contact:
            raise OSError("fictional contact pre-publication crash")
        real_write(st, path, value)
        if (boundary == "contact_after" and target_contact) or (boundary == "closure" and source_closure):
            raise OSError("fictional post-publication crash")
    def carry(*args):
        result = real_carry(*args)
        if boundary == "carry":
            raise OSError("fictional post-carry crash")
        return result
    monkeypatch.setattr(promotion, "_save", save)
    monkeypatch.setattr(PortalStore, "_write", write)
    monkeypatch.setattr(prospects, "_carry_into_client", carry)
    with pytest.raises(OSError):
        run(pair)
    return promotion._record(*source, allow_temps=True)["operation"]


@pytest.mark.parametrize("boundary", ["source_first", "target_marker", "carry", "contact_before", "contact_after", "closure", "source_completed"])
def test_every_crash_denies_both_and_exact_recovery_carries_once(pair, monkeypatch, boundary):
    import case_notes
    from test_contact_transitions import accepted
    target, source = pair
    proof = accepted(source)
    case_notes.add_note(source[0].cases / source[2], "Retained fictional note", "Fictional Staff", "paralegal", prospect=True)
    with monkeypatch.context() as fault:
        operation = pending(pair, fault, boundary)
    for current in pair:
        assert not consent.eligibility(*current, "email")["allowed"]
        with pytest.raises(ValueError):
            access.current_revision(*current)
        with pytest.raises(ValueError):
            current[1].update_profile(current[2], name="Unrelated edit")
    assert not consent.credential_valid(source[0], source[1], proof)
    result = promotion.recover(*source, operation, actor_email=STAFF)
    assert result["state"] == "completed"
    assert len(case_notes._read(target[0].cases / target[2])["notes"]) == 1
    assert not access._record(*target).get("pending")
    assert not access._record(*target).get("transition")
    assert not consent.credential_valid(source[0], source[1], proof)
    assert not consent.eligibility(*target, "email")["allowed"]
    assert promotion.recover(*target, operation, actor_email=STAFF)["state"] == "completed"


@pytest.mark.parametrize("fault", ["inactive", "source_acl", "target_acl", "wrong_operation", "target_contact", "same_day_unrelated_closure", "target_identity", "source_identity"])
def test_recovery_rechecks_both_actual_authorities_and_immutable_identity(pair, monkeypatch, fault):
    target, source = pair
    with monkeypatch.context() as crash:
        operation = pending(pair, crash, "source_first")
    if fault == "inactive":
        path = target[0].data / "review_users.json"
        value = json.loads(path.read_text()); value["users"][STAFF]["active"] = False
        path.write_text(json.dumps(value))
    elif fault.endswith("acl"):
        import restricted
        denied = source[0].cases / source[2] if fault == "source_acl" else target[0].cases / target[2]
        monkeypatch.setattr(restricted, "visible_to", lambda actor, case: case != denied)
    elif fault == "wrong_operation":
        operation = "a" * 32
    elif fault == "target_contact":
        path = target[1].client_dir(target[2]) / "profile.json"
        value = json.loads(path.read_text()); value["email"] = "foreign@fictional.example"
        path.write_text(json.dumps(value))
    elif fault == "same_day_unrelated_closure":
        path = source[1].client_dir(source[2]) / "profile.json"
        value = json.loads(path.read_text()); value["closed_on"] = promotion._record(*source)["closure_on"]
        path.write_text(json.dumps(value))
    else:
        current = target if fault == "target_identity" else source
        record = access._record(*current)
        next(row for row in record["history"] if row.get("action") == "enrollment_denied")["id"] = "f" * 32
        access._save(*current, record)
    before = (source[1].client_dir(source[2]) / promotion.FILE).read_bytes()
    with pytest.raises((ValueError, PermissionError)):
        promotion.recover(*source, operation, actor_email=STAFF)
    assert (source[1].client_dir(source[2]) / promotion.FILE).read_bytes() == before
    assert not consent.eligibility(*target, "email")["allowed"]


@pytest.mark.parametrize("selected_side", [0, 1])
def test_pending_actual_q1_preserves_records_originals_and_waiting_state(pair, monkeypatch, selected_side):
    import purge
    with monkeypatch.context() as fault:
        pending(pair, fault, "source_first")
    scope, store, client = pair[selected_side]
    original = scope.cases / client / "fictional-original.pdf"
    original.write_bytes(b"fictional retained original")
    waiting = scope.data / purge.PURGES_FILE
    waiting.write_text(json.dumps({"version": 1, "cases": {client: {"state": "waiting"}}}))
    snapshot = waiting.read_bytes()
    with pytest.raises(ValueError, match="pending promotion"):
        purge.empty_stores(scope.cases, client, scope.portal, who=purge.Identity(client, [], set(), set(), set()))
    assert waiting.read_bytes() == snapshot
    assert original.read_bytes() == b"fictional retained original"
    assert store.profile(client)


def test_target_normal_contact_edit_keeps_retirement_but_requires_fresh_signoff(pair):
    target, source = pair
    run(pair)
    granted(target)
    from test_contact_transitions import accepted
    proof = accepted(target)
    target[1].update_profile(target[2], email="new-safe@fictional.example")
    assert not consent.credential_valid(target[0], target[1], proof)
    assert not consent.eligibility(*target, "email")["allowed"]
    assert {(row.store_kind, row.client) for row in contacts.inventory(target[0])} == {("client", target[2])}
    granted(target)
    assert consent.eligibility(*target, "email")["allowed"]


def test_completed_source_q1_keeps_target_receipt_new_grant_and_recreated_source_separate(pair):
    import purge
    from test_contact_transitions import accepted
    target, source = pair
    run(pair)
    proof = accepted(target)
    receipt = (target[1].client_dir(target[2]) / promotion.FILE).read_bytes()
    purge.empty_stores(source[0].cases, source[2], source[0].portal, who=purge.Identity(source[2], [], set(), set(), set()))
    assert not source[1].client_dir(source[2]).exists()
    assert (target[1].client_dir(target[2]) / promotion.FILE).read_bytes() == receipt
    assert consent.credential_valid(target[0], target[1], proof)
    assert consent.eligibility(*target, "email")["allowed"]
    # Actual separately enrolled identity at the same ID has a fresh nonce.
    recreated = prospects.create(target[0].cases, {"name": "Fictional Prospect", "email": "recreated@fictional.example", "language": "en"}, "Fictional Staff", "paralegal", target[0].portal)
    assert recreated["id"] == source[2]
    prospects.ensure_portal(source[1], recreated, "Fictional Staff")
    rows = contacts.inventory(target[0])
    assert {(row.store_kind, row.client) for row in rows} == {("client", target[2]), ("prospect", source[2])}
    assert consent.credential_valid(target[0], target[1], proof)
    assert not consent.eligibility(*source, "email")["allowed"]
    fresh_proof = accepted(source)
    assert consent.eligibility(*source, "email")["allowed"]
    assert consent.credential_valid(source[0], source[1], fresh_proof)
    source[1].update_profile(source[2], name="Fictional Independently Enrolled Person")
    assert consent.credential_valid(source[0], source[1], fresh_proof)
    promotion.preflight_purge(target[0], {("client", target[2])})
    assert purge.identity(target[0].cases, target[2], target[0].portal).prospects == []
    assert prospects.first_call(target[0].cases, target[0].portal, target[2]) is None
    purge.empty_stores(target[0].cases, target[2], target[0].portal)
    assert source[1].profile(source[2])["name"] == "Fictional Independently Enrolled Person"
    assert consent.credential_valid(source[0], source[1], fresh_proof)


@pytest.mark.parametrize("fault", ["version_bool", "missing_source", "invalid_role", "bad_identity", "modified_plan", "bad_history", "legacy_source"])
def test_damaged_records_and_legacy_identity_refuse_without_effects(pair, monkeypatch, fault):
    target, source = pair
    if fault == "legacy_source":
        access._save(*source, dict(access._record(*source), revision=0, history=[]))
        with pytest.raises(ValueError, match="legacy profile"):
            run(pair)
        assert not (source[1].client_dir(source[2]) / promotion.FILE).exists()
        return
    with monkeypatch.context() as crash:
        operation = pending(pair, crash, "source_first")
    path = source[1].client_dir(source[2]) / promotion.FILE
    row = json.loads(path.read_text())
    if fault == "version_bool": row["version"] = True
    elif fault == "missing_source": row.pop("source")
    elif fault == "invalid_role": row["role"] = ["source"]
    elif fault == "bad_identity": row["source"]["identity"] = "bad"
    elif fault == "modified_plan": row["proposed"]["email"] = "changed@fictional.example"
    else: row["history"][0]["actor"] = ""
    path.write_text(json.dumps(row))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        promotion.recover(*source, operation, actor_email=STAFF)
    assert path.read_bytes() == before
    assert not consent.eligibility(*target, "email")["allowed"]


@pytest.mark.parametrize("fault", ["intact", "foreign", "corrupt"])
def test_exact_initial_source_temp_denies_target_and_recovers_only_intact_own_plan(pair, monkeypatch, fault):
    target, source = pair
    _real = promotion._save
    staged_path = source[1].client_dir(source[2]) / (promotion.FILE + "." + "a" * 16 + ".tmp")
    captured = {}
    def stage(ms, store, client, record):
        captured.update(record)
        staged_path.write_text(json.dumps(record))
        raise OSError("fictional initial source temp-only crash")
    with monkeypatch.context() as crash:
        crash.setattr(promotion, "_save", stage)
        with pytest.raises(OSError): run(pair)
    assert not (source[1].client_dir(source[2]) / promotion.FILE).exists()
    assert not (target[1].client_dir(target[2]) / promotion.FILE).exists()
    assert not consent.eligibility(*target, "email")["allowed"]
    if fault == "foreign":
        row = dict(captured, operation="b" * 32)
        row["plan_sha256"] = access._hash(promotion._plan(row))
        staged_path.write_text(json.dumps(row))
    elif fault == "corrupt": staged_path.write_text("{")
    before = staged_path.read_bytes()
    if fault == "intact":
        assert promotion.recover(*source, captured["operation"], actor_email=STAFF)["state"] == "completed"
        assert not staged_path.exists()
    else:
        with pytest.raises(ValueError):
            promotion.recover(*source, captured["operation"], actor_email=STAFF)
        assert staged_path.read_bytes() == before


def test_completed_target_q1_cannot_orphan_retired_source(pair):
    run(pair)
    target, _ = pair
    with pytest.raises(ValueError, match="associated retired prospect"):
        promotion.preflight_purge(target[0], {("client", target[2])})


def test_carry_keeps_bank_filter_current_answers_tasks_apply_for_and_subjects(pair):
    import apply_for
    import case_notes
    import deadlines_set
    target, source = pair
    from portal.bank import all_questions, bank_for
    questions = all_questions(bank_for(target[1].profile(target[2])))
    # Use an actual common questionnaire text key from current schema.
    assert "given_name" in questions
    source[1].save_answers(source[2], {"given_name": "Fictional Source", "family_name": "Fictional Family", "unknown_fixture_key": "ignored"})
    target[1].save_answers(target[2], {"given_name": "Keep current target"})
    folder = source[0].cases / source[2]
    task = case_notes.add_task(folder, "Fictional follow-up", "2026-10-05", None, "Fictional task", "Fictional Staff", [], role="paralegal", prospect=True)
    question = next(iter(apply_for.question_ids()))
    apply_for.answer(folder, question, "yes", "Fictional Staff", "paralegal", prospect=True)
    subject = target[0].cases / target[2] / "documents.json"
    subject.write_text(json.dumps({"fictional": "immutable subject guard"}))
    before = subject.read_bytes()
    result = run(pair)
    assert result["carried"]["tasks"] == 1 and result["carried"]["apply_for"] == 1
    assert target[1].answers(target[2])["given_name"] == "Keep current target"
    assert target[1].answers(target[2])["family_name"] == "Fictional Family"
    assert "unknown_fixture_key" not in target[1].answers(target[2])
    copied = deadlines_set.load(target[0].cases / target[2])["deadlines"]
    assert len(copied) == 1 and copied[0]["carried_from"] == f"{source[2]}|{task['id']}"
    assert apply_for.history(target[0].cases / target[2], question) == apply_for.history(folder, question)
    assert subject.read_bytes() == before


def test_catalog_firm_export_and_q2_include_canonical_but_never_internal_release(pair, monkeypatch):
    import argparse
    import client_file
    import documents
    import export_firm
    import records
    target, source = pair
    run(pair)
    case = target[0].cases / target[2]
    names = [promotion.FILE, promotion.FILE + "." + "a" * 16 + ".tmp"]
    for name in names: (case / name).write_text("fictional internal malformed alias")
    originals = case / "source"
    originals.mkdir()
    original = originals / "fictional.pdf"
    original.write_bytes(b"%PDF-fictional original")
    (case / "meta.json").write_text(json.dumps({"client_id": target[2], "source_folder": str(originals)}))
    documents.save(case, {"documents": [{"files": names + [original.name]}]})
    assert promotion.FILE in records.patterns("portal")
    args = argparse.Namespace(data=target[0].cases, portal=target[0].portal, users=target[0].data / "review_users.json", client=target[2], all=False, firm_files=False)
    entries, *_ = export_firm.gather(args)
    assert any(item.source == target[1].client_dir(target[2]) / promotion.FILE for item in entries)
    assert not any(item.source and item.source.name.endswith(".tmp") for item in entries)
    real = export_firm.gather
    def injected(args):
        entries, skipped, warnings, roots = real(args)
        entries += [export_firm.Entry(f"clients/{target[2]}/{name}", "Malformed registered alias", source=case / name) for name in names]
        return entries, skipped, warnings, roots
    monkeypatch.setattr(export_firm, "gather", injected)
    selected, excluded, _ = client_file.gather(case, target[0].portal)
    assert any(item.source == original for item in selected)
    assert not any(item.source and item.source.name.startswith(promotion.FILE) for item in selected)
    assert len([row for row in excluded if "Internal" in row["why"]]) >= 3
